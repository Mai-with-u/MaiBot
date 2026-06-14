/**
 * 问卷调查 API 客户端
 * 用于与 Cloudflare Workers 问卷服务交互
 *
 * 请求样板（解析、错误格式化）由 @/lib/http 的 statsApi 实例承担（外部统计服务，不携带凭据）；
 * 本文件只声明 endpoint、业务错误文案与按状态码（429 / 409）区分的提示语。
 */
import { ApiError, statsApi } from '@/lib/http'
import type {
  QuestionAnswer,
  StoredSubmission,
  SurveyStats,
  SurveyStatsResponse,
  SurveySubmission,
  SurveySubmitResponse,
  UserSubmissionsResponse,
} from '@/types/survey'

/**
 * 生成或获取用户ID
 */
export function getUserId(): string {
  const storageKey = 'maibot_user_id'
  let userId = localStorage.getItem(storageKey)

  if (!userId) {
    // 生成新的用户ID: fp_{fingerprint}_{timestamp}_{random}
    const fingerprint = Math.random().toString(36).substring(2, 10)
    const timestamp = Date.now().toString(36)
    const random = Math.random().toString(36).substring(2, 10)
    userId = `fp_${fingerprint}_${timestamp}_${random}`
    localStorage.setItem(storageKey, userId)
  }

  return userId
}

/** 从 ApiError 携带的后端原始错误体中提取 error 字段 */
function getDetailError(error: ApiError): string | undefined {
  const detailError = (error.detail as { error?: unknown } | null | undefined)?.error
  return typeof detailError === 'string' ? detailError : undefined
}

/**
 * 提交问卷
 */
export async function submitSurvey(
  surveyId: string,
  surveyVersion: string,
  answers: QuestionAnswer[],
  options?: {
    allowMultiple?: boolean
    userId?: string
  }
): Promise<SurveySubmitResponse> {
  try {
    const userId = options?.userId || getUserId()

    const submission: SurveySubmission & { allowMultiple?: boolean } = {
      surveyId,
      surveyVersion,
      userId,
      answers,
      submittedAt: new Date().toISOString(),
      allowMultiple: options?.allowMultiple,
      metadata: {
        userAgent: navigator.userAgent,
        language: navigator.language
      }
    }

    const data = await statsApi.post<{ submissionId?: string; message?: string }>(
      '/survey/submit',
      {
        body: submission,
        errorMessage: '提交失败',
      }
    )

    return {
      success: true,
      submissionId: data.submissionId,
      message: data.message
    }
  } catch (error) {
    if (error instanceof ApiError) {
      if (error.status === 429) {
        return { success: false, error: '提交过于频繁，请稍后再试' }
      }
      if (error.status === 409) {
        return { success: false, error: getDetailError(error) || '你已经提交过这份问卷了' }
      }
      if (error.status !== undefined) {
        return { success: false, error: error.message }
      }
    }
    console.error('Error submitting survey:', error)
    return { success: false, error: '网络错误' }
  }
}

/**
 * 获取问卷统计数据
 */
export async function getSurveyStats(surveyId: string): Promise<SurveyStatsResponse> {
  try {
    const data = await statsApi.get<{ stats: SurveyStats }>(`/survey/stats/${surveyId}`, {
      errorMessage: '获取统计数据失败',
    })
    return { success: true, stats: data.stats }
  } catch (error) {
    if (error instanceof ApiError && error.status !== undefined) {
      return { success: false, error: error.message }
    }
    console.error('Error fetching survey stats:', error)
    return { success: false, error: '网络错误' }
  }
}

/**
 * 获取用户提交记录
 */
export async function getUserSubmissions(
  surveyId?: string,
  userId?: string
): Promise<UserSubmissionsResponse> {
  try {
    const finalUserId = userId || getUserId()

    const data = await statsApi.get<{ submissions: StoredSubmission[] }>('/survey/submissions', {
      query: {
        user_id: finalUserId,
        survey_id: surveyId || undefined,
      },
      errorMessage: '获取提交记录失败',
    })
    return { success: true, submissions: data.submissions }
  } catch (error) {
    if (error instanceof ApiError && error.status !== undefined) {
      return { success: false, error: error.message }
    }
    console.error('Error fetching user submissions:', error)
    return { success: false, error: '网络错误' }
  }
}

/**
 * 检查用户是否已提交问卷
 */
export async function checkUserSubmission(
  surveyId: string,
  userId?: string
): Promise<{ success: boolean; hasSubmitted?: boolean; error?: string }> {
  try {
    const finalUserId = userId || getUserId()

    const data = await statsApi.get<{ hasSubmitted?: boolean }>('/survey/check', {
      query: {
        user_id: finalUserId,
        survey_id: surveyId,
      },
      errorMessage: '检查失败',
    })
    return { success: true, hasSubmitted: data.hasSubmitted }
  } catch (error) {
    if (error instanceof ApiError && error.status !== undefined) {
      return { success: false, error: error.message }
    }
    console.error('Error checking submission:', error)
    return { success: false, error: '网络错误' }
  }
}
