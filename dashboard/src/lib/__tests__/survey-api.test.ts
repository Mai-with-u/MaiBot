import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError, statsApi } from '@/lib/http'
import type { SurveyStats } from '@/types/survey'

import {
  checkUserSubmission,
  getSurveyStats,
  getUserId,
  getUserSubmissions,
  submitSurvey,
} from '../survey-api'

vi.mock('@/lib/http', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/http')>()
  return {
    ...actual,
    statsApi: {
      request: vi.fn(),
      get: vi.fn(),
      post: vi.fn(),
      put: vi.fn(),
      patch: vi.fn(),
      delete: vi.fn(),
    },
  }
})

const getMock = vi.mocked(statsApi.get)
const postMock = vi.mocked(statsApi.post)

/** getUserId 使用的 localStorage 键 */
const USER_ID_STORAGE_KEY = 'maibot_user_id'

beforeEach(() => {
  getMock.mockReset()
  postMock.mockReset()
  // getUserId 依赖 localStorage 持久化，用例间必须清空避免互相污染
  localStorage.removeItem(USER_ID_STORAGE_KEY)
})

describe('getUserId', () => {
  it('首次调用生成 fp_ 前缀的用户 ID 并写入 localStorage', () => {
    const userId = getUserId()

    expect(userId).toMatch(/^fp_[a-z0-9]+_[a-z0-9]+_[a-z0-9]+$/)
    expect(localStorage.getItem(USER_ID_STORAGE_KEY)).toBe(userId)
  })

  it('已有用户 ID 时直接复用，不重新生成', () => {
    localStorage.setItem(USER_ID_STORAGE_KEY, 'fp_existing_id_value')

    expect(getUserId()).toBe('fp_existing_id_value')
    expect(getUserId()).toBe('fp_existing_id_value')
  })
})

describe('submitSurvey', () => {
  it('提交成功：请求体包含问卷信息、答案与浏览器元数据，返回 submissionId', async () => {
    postMock.mockResolvedValue({ submissionId: 'sub-1', message: '感谢参与' })
    const answers = [{ questionId: 'q1', value: 'a' }]

    const result = await submitSurvey('survey-2026', 'v1', answers, {
      allowMultiple: true,
      userId: 'fp_custom_user',
    })

    expect(result).toEqual({ success: true, submissionId: 'sub-1', message: '感谢参与' })
    expect(postMock).toHaveBeenCalledTimes(1)
    const [path, options] = postMock.mock.calls[0]
    expect(path).toBe('/survey/submit')
    expect(options).toMatchObject({ errorMessage: '提交失败' })
    expect(options?.body).toMatchObject({
      surveyId: 'survey-2026',
      surveyVersion: 'v1',
      userId: 'fp_custom_user',
      answers,
      allowMultiple: true,
      metadata: {
        userAgent: navigator.userAgent,
        language: navigator.language,
      },
    })
    // submittedAt 应为合法的 ISO 时间字符串
    const body = options?.body as { submittedAt: string }
    expect(new Date(body.submittedAt).toISOString()).toBe(body.submittedAt)
  })

  it('未指定 userId 时使用 localStorage 中的用户 ID', async () => {
    localStorage.setItem(USER_ID_STORAGE_KEY, 'fp_stored_user')
    postMock.mockResolvedValue({})

    await submitSurvey('survey-2026', 'v1', [])

    expect(postMock.mock.calls[0][1]?.body).toMatchObject({ userId: 'fp_stored_user' })
  })

  it('429 限流时返回频率提示', async () => {
    postMock.mockRejectedValue(new ApiError('提交失败', { status: 429 }))

    await expect(submitSurvey('s', 'v1', [])).resolves.toEqual({
      success: false,
      error: '提交过于频繁，请稍后再试',
    })
  })

  it('409 且错误体携带 error 字段时透传后端文案', async () => {
    postMock.mockRejectedValue(
      new ApiError('提交失败', { status: 409, detail: { error: '该问卷限一人一份' } })
    )

    await expect(submitSurvey('s', 'v1', [])).resolves.toEqual({
      success: false,
      error: '该问卷限一人一份',
    })
  })

  it('409 且错误体无可用 error 字段时使用默认重复提交文案', async () => {
    postMock.mockRejectedValue(
      new ApiError('提交失败', { status: 409, detail: { error: 42 } })
    )

    await expect(submitSurvey('s', 'v1', [])).resolves.toEqual({
      success: false,
      error: '你已经提交过这份问卷了',
    })
  })

  it('其他 HTTP 错误时返回 ApiError 的 message', async () => {
    postMock.mockRejectedValue(new ApiError('服务暂不可用', { status: 503 }))

    await expect(submitSurvey('s', 'v1', [])).resolves.toEqual({
      success: false,
      error: '服务暂不可用',
    })
  })

  it('网络层失败（无 status）时返回网络错误文案', async () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {})
    postMock.mockRejectedValue(new ApiError('网络请求失败'))

    await expect(submitSurvey('s', 'v1', [])).resolves.toEqual({
      success: false,
      error: '网络错误',
    })
    expect(consoleError).toHaveBeenCalled()
  })
})

describe('getSurveyStats', () => {
  it('按问卷 ID 请求统计接口并返回 stats', async () => {
    const stats: SurveyStats = {
      surveyId: 'survey-2026',
      totalSubmissions: 10,
      uniqueUsers: 8,
      questionStats: { q1: { answered: 10 } },
    }
    getMock.mockResolvedValue({ stats })

    await expect(getSurveyStats('survey-2026')).resolves.toEqual({ success: true, stats })
    expect(getMock).toHaveBeenCalledWith('/survey/stats/survey-2026', {
      errorMessage: '获取统计数据失败',
    })
  })

  it('HTTP 错误时返回 ApiError 的 message', async () => {
    getMock.mockRejectedValue(new ApiError('获取统计数据失败', { status: 404 }))

    await expect(getSurveyStats('missing')).resolves.toEqual({
      success: false,
      error: '获取统计数据失败',
    })
  })

  it('非 ApiError 异常时返回网络错误文案', async () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {})
    getMock.mockRejectedValue(new TypeError('Failed to fetch'))

    await expect(getSurveyStats('s')).resolves.toEqual({ success: false, error: '网络错误' })
    expect(consoleError).toHaveBeenCalled()
  })
})

describe('getUserSubmissions', () => {
  it('携带 user_id 与 survey_id 查询提交记录', async () => {
    const submissions = [
      {
        id: 'sub-1',
        surveyId: 'survey-2026',
        surveyVersion: 'v1',
        answers: [],
        submittedAt: '2026-07-26T00:00:00.000Z',
      },
    ]
    getMock.mockResolvedValue({ submissions })

    await expect(getUserSubmissions('survey-2026', 'fp_user_a')).resolves.toEqual({
      success: true,
      submissions,
    })
    expect(getMock).toHaveBeenCalledWith('/survey/submissions', {
      query: { user_id: 'fp_user_a', survey_id: 'survey-2026' },
      errorMessage: '获取提交记录失败',
    })
  })

  it('省略参数时回退到 localStorage 用户 ID，survey_id 为 undefined', async () => {
    localStorage.setItem(USER_ID_STORAGE_KEY, 'fp_stored_user')
    getMock.mockResolvedValue({ submissions: [] })

    await getUserSubmissions()

    expect(getMock).toHaveBeenCalledWith('/survey/submissions', {
      query: { user_id: 'fp_stored_user', survey_id: undefined },
      errorMessage: '获取提交记录失败',
    })
  })

  it('HTTP 错误时返回 ApiError 的 message', async () => {
    getMock.mockRejectedValue(new ApiError('获取提交记录失败', { status: 500 }))

    await expect(getUserSubmissions('s', 'u')).resolves.toEqual({
      success: false,
      error: '获取提交记录失败',
    })
  })
})

describe('checkUserSubmission', () => {
  it('携带 user_id 与 survey_id 检查提交状态', async () => {
    getMock.mockResolvedValue({ hasSubmitted: true })

    await expect(checkUserSubmission('survey-2026', 'fp_user_a')).resolves.toEqual({
      success: true,
      hasSubmitted: true,
    })
    expect(getMock).toHaveBeenCalledWith('/survey/check', {
      query: { user_id: 'fp_user_a', survey_id: 'survey-2026' },
      errorMessage: '检查失败',
    })
  })

  it('HTTP 错误时返回 ApiError 的 message', async () => {
    getMock.mockRejectedValue(new ApiError('检查失败', { status: 500 }))

    await expect(checkUserSubmission('s', 'u')).resolves.toEqual({
      success: false,
      error: '检查失败',
    })
  })

  it('非 ApiError 异常时返回网络错误文案', async () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {})
    getMock.mockRejectedValue(new Error('boom'))

    await expect(checkUserSubmission('s', 'u')).resolves.toEqual({
      success: false,
      error: '网络错误',
    })
    expect(consoleError).toHaveBeenCalled()
  })
})
