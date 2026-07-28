/**
 * SurveyRenderer 问卷渲染器测试
 * 覆盖：提交状态检查、已提交/有效期外分支、进度统计、必填与 minLength 校验、
 * 提交成功/失败/异常路径、initialAnswers 预填、分页模式导航
 */
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { SurveyRenderer } from '../survey-renderer'
import * as surveyApi from '@/lib/survey-api'
import type { SurveyConfig } from '@/types/survey'

// 打桩问卷 API，避免真实网络请求
vi.mock('@/lib/survey-api', () => ({
  submitSurvey: vi.fn(),
  checkUserSubmission: vi.fn(),
}))

afterEach(() => {
  cleanup()
})

/** 构造两题（必填文本 + 必填单选）的问卷配置 */
function makeConfig(overrides?: Partial<SurveyConfig>): SurveyConfig {
  return {
    id: 'test-survey',
    version: '1.0.0',
    title: '测试问卷',
    description: '这是一份测试问卷',
    questions: [
      { id: 'q1', type: 'text', title: '你的昵称', required: true },
      {
        id: 'q2',
        type: 'single',
        title: '满意度',
        required: true,
        options: [
          { id: 'good', label: '满意', value: 'good' },
          { id: 'bad', label: '不满意', value: 'bad' },
        ],
      },
    ],
    settings: { allowMultiple: false, thankYouMessage: '感谢参与测试问卷' },
    ...overrides,
  }
}

beforeEach(() => {
  vi.mocked(surveyApi.checkUserSubmission).mockResolvedValue({ success: true, hasSubmitted: false })
  vi.mocked(surveyApi.submitSurvey).mockResolvedValue({ success: true, submissionId: 'sub-001' })
})

describe('SurveyRenderer 展示前置分支', () => {
  it('检查提交状态期间只显示加载指示', () => {
    vi.mocked(surveyApi.checkUserSubmission).mockReturnValue(
      new Promise<{ success: boolean; hasSubmitted?: boolean; error?: string }>(() => {})
    )
    const { container } = render(<SurveyRenderer config={makeConfig()} />)
    expect(container.querySelector('.animate-spin')).toBeInTheDocument()
    expect(screen.queryByText('你的昵称')).not.toBeInTheDocument()
  })

  it('已提交过且不允许多次提交时显示提示', async () => {
    vi.mocked(surveyApi.checkUserSubmission).mockResolvedValue({ success: true, hasSubmitted: true })
    render(<SurveyRenderer config={makeConfig()} />)
    expect(await screen.findByText('你已经提交过这份问卷了，感谢参与！')).toBeInTheDocument()
    expect(surveyApi.checkUserSubmission).toHaveBeenCalledWith('test-survey')
    // 已提交分支下不再渲染题目
    expect(screen.queryByText('你的昵称')).not.toBeInTheDocument()
  })

  it('允许多次提交时跳过历史提交检查直接渲染问卷', async () => {
    render(<SurveyRenderer config={makeConfig({ settings: { allowMultiple: true } })} />)
    expect(await screen.findByText('你的昵称')).toBeInTheDocument()
    expect(surveyApi.checkUserSubmission).not.toHaveBeenCalled()
  })

  it('超过结束时间时显示不在有效期内', async () => {
    render(
      <SurveyRenderer
        config={makeConfig({ settings: { allowMultiple: false, endTime: '2020-01-01T00:00:00.000Z' } })}
      />
    )
    expect(await screen.findByText('问卷不在有效期内')).toBeInTheDocument()
  })

  it('未到开始时间时显示不在有效期内', async () => {
    render(
      <SurveyRenderer
        config={makeConfig({ settings: { allowMultiple: false, startTime: '2999-01-01T00:00:00.000Z' } })}
      />
    )
    expect(await screen.findByText('问卷不在有效期内')).toBeInTheDocument()
  })
})

describe('SurveyRenderer 正常答题流程', () => {
  it('渲染标题、描述并随作答更新进度', async () => {
    render(<SurveyRenderer config={makeConfig()} />)
    expect(await screen.findByText('测试问卷')).toBeInTheDocument()
    expect(screen.getByText('这是一份测试问卷')).toBeInTheDocument()
    expect(screen.getByText('进度')).toBeInTheDocument()
    expect(screen.getByText('0 / 2')).toBeInTheDocument()
    // 填写一题后进度更新为 1 / 2
    fireEvent.change(screen.getByPlaceholderText('请输入...'), { target: { value: '麦麦' } })
    expect(screen.getByText('1 / 2')).toBeInTheDocument()
  })

  it('showProgress 为 false 时不渲染进度条', async () => {
    render(<SurveyRenderer config={makeConfig()} showProgress={false} />)
    await screen.findByText('测试问卷')
    expect(screen.queryByText('进度')).not.toBeInTheDocument()
  })

  it('必填未答时提交给出校验错误且不调用接口', async () => {
    const user = userEvent.setup()
    render(<SurveyRenderer config={makeConfig()} />)
    await screen.findByText('你的昵称')
    await user.click(screen.getByRole('button', { name: '提交问卷' }))
    expect(await screen.findAllByText('此题为必填项')).toHaveLength(2)
    expect(screen.getByText('还有 2 个必填项未完成')).toBeInTheDocument()
    expect(surveyApi.submitSurvey).not.toHaveBeenCalled()
  })

  it('文本长度不足 minLength 时给出字符数提示', async () => {
    const user = userEvent.setup()
    const config = makeConfig({
      questions: [{ id: 'q1', type: 'text', title: '详细描述', required: true, minLength: 10 }],
    })
    render(<SurveyRenderer config={config} />)
    await screen.findByText('详细描述')
    fireEvent.change(screen.getByPlaceholderText('请输入...'), { target: { value: '太短' } })
    await user.click(screen.getByRole('button', { name: '提交问卷' }))
    expect(await screen.findByText('至少需要 10 个字符')).toBeInTheDocument()
    expect(surveyApi.submitSurvey).not.toHaveBeenCalled()
  })

  it('填写完整提交成功后显示感谢信息并回调', async () => {
    const user = userEvent.setup()
    const onSubmitSuccess = vi.fn()
    render(<SurveyRenderer config={makeConfig()} onSubmitSuccess={onSubmitSuccess} />)
    await screen.findByText('你的昵称')
    fireEvent.change(screen.getByPlaceholderText('请输入...'), { target: { value: '麦麦' } })
    await user.click(screen.getByRole('radio', { name: '满意' }))
    await user.click(screen.getByRole('button', { name: '提交问卷' }))

    expect(await screen.findByText('提交成功')).toBeInTheDocument()
    expect(screen.getByText('感谢参与测试问卷')).toBeInTheDocument()
    expect(screen.getByText('提交编号：sub-001')).toBeInTheDocument()
    expect(onSubmitSuccess).toHaveBeenCalledWith('sub-001')
    expect(surveyApi.submitSurvey).toHaveBeenCalledWith(
      'test-survey',
      '1.0.0',
      [
        { questionId: 'q1', value: '麦麦' },
        { questionId: 'q2', value: 'good' },
      ],
      { allowMultiple: false }
    )
  })

  it('提交返回失败时显示错误并回调 onSubmitError', async () => {
    vi.mocked(surveyApi.submitSurvey).mockResolvedValue({ success: false, error: '服务器繁忙' })
    const user = userEvent.setup()
    const onSubmitError = vi.fn()
    render(<SurveyRenderer config={makeConfig()} onSubmitError={onSubmitError} />)
    await screen.findByText('你的昵称')
    fireEvent.change(screen.getByPlaceholderText('请输入...'), { target: { value: '麦麦' } })
    await user.click(screen.getByRole('radio', { name: '满意' }))
    await user.click(screen.getByRole('button', { name: '提交问卷' }))

    expect(await screen.findByText('服务器繁忙')).toBeInTheDocument()
    expect(onSubmitError).toHaveBeenCalledWith('服务器繁忙')
    // 失败后仍停留在答题界面，可以重新提交
    expect(screen.getByRole('button', { name: '提交问卷' })).toBeInTheDocument()
  })

  it('提交抛出异常时展示异常消息', async () => {
    vi.mocked(surveyApi.submitSurvey).mockRejectedValue(new Error('网络中断'))
    const user = userEvent.setup()
    const onSubmitError = vi.fn()
    render(<SurveyRenderer config={makeConfig()} onSubmitError={onSubmitError} />)
    await screen.findByText('你的昵称')
    fireEvent.change(screen.getByPlaceholderText('请输入...'), { target: { value: '麦麦' } })
    await user.click(screen.getByRole('radio', { name: '满意' }))
    await user.click(screen.getByRole('button', { name: '提交问卷' }))

    expect(await screen.findByText('网络中断')).toBeInTheDocument()
    expect(onSubmitError).toHaveBeenCalledWith('网络中断')
  })

  it('initialAnswers 预填答案并计入进度', async () => {
    render(
      <SurveyRenderer
        config={makeConfig()}
        initialAnswers={[{ questionId: 'q1', value: '预填昵称' }]}
      />
    )
    expect(await screen.findByDisplayValue('预填昵称')).toBeInTheDocument()
    expect(screen.getByText('1 / 2')).toBeInTheDocument()
  })
})

describe('SurveyRenderer 分页模式', () => {
  it('逐题展示、上一题在首页禁用、末页提交问卷', async () => {
    const user = userEvent.setup()
    render(<SurveyRenderer config={makeConfig()} paginateQuestions />)
    // 第一页只展示第一题
    expect(await screen.findByText('问题 1 / 2')).toBeInTheDocument()
    expect(screen.getByText('你的昵称')).toBeInTheDocument()
    expect(screen.queryByText('满意度')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '上一题' })).toBeDisabled()

    // 填写第一题后进入第二页
    fireEvent.change(screen.getByPlaceholderText('请输入...'), { target: { value: '麦麦' } })
    await user.click(screen.getByRole('button', { name: '下一题' }))
    expect(await screen.findByText('问题 2 / 2')).toBeInTheDocument()
    expect(screen.getByText('满意度')).toBeInTheDocument()

    // 末页出现提交按钮，作答后可成功提交
    await user.click(screen.getByRole('radio', { name: '满意' }))
    await user.click(screen.getByRole('button', { name: '提交问卷' }))
    await waitFor(() => expect(surveyApi.submitSurvey).toHaveBeenCalled())
    expect(await screen.findByText('提交成功')).toBeInTheDocument()
  })

  it('点击上一题返回前一页', async () => {
    const user = userEvent.setup()
    render(<SurveyRenderer config={makeConfig()} paginateQuestions />)
    await screen.findByText('问题 1 / 2')
    await user.click(screen.getByRole('button', { name: '下一题' }))
    await screen.findByText('问题 2 / 2')
    await user.click(screen.getByRole('button', { name: '上一题' }))
    expect(await screen.findByText('问题 1 / 2')).toBeInTheDocument()
    expect(screen.getByText('你的昵称')).toBeInTheDocument()
  })
})
