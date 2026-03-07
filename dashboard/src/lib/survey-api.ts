/**
 * 问卷调查 API 客户端
 * 用于与 Cloudflare Workers 问卷服务交互
 */

import type { 
  SurveySubmission, 
  StoredSubmission, 
  SurveyStats,
  SurveySubmitResponse,
  SurveyStatsResponse,
  UserSubmissionsResponse,
  QuestionAnswer
} from '@/types/survey'

// 配置统计服务 API 地址
const STATS_API_BASE_URL = 'https://maibot-plugin-stats.maibot-webui.workers.dev'

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
    
    const response = await fetch(`${STATS_API_BASE_URL}/survey/submit`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(submission),
    })
    
    const data = await response.json()
    
    if (response.status === 429) {
      return { success: false, error: '提交过于频繁，请稍后再试' }
    }
    
    if (response.status === 409) {
      return { success: false, error: data.error || '你已经提交过这份问卷了' }
    }
    
    if (!response.ok) {
      return { success: false, error: data.error || '提交失败' }
    }
    
    return { 
      success: true, 
      submissionId: data.submissionId,
      message: data.message 
    }
  } catch (error) {
    console.error('Error submitting survey:', error)
    return { success: false, error: '网络错误' }
  }
}

/**
 * 获取问卷统计数据
 */
export async function getSurveyStats(surveyId: string): Promise<SurveyStatsResponse> {
  try {
    const response = await fetch(`${STATS_API_BASE_URL}/survey/stats/${surveyId}`)
    
    if (!response.ok) {
      const data = await response.json()
      return { success: false, error: data.error || '获取统计数据失败' }
    }
    
    const data = await response.json()
    return { success: true, stats: data.stats as SurveyStats }
  } catch (error) {
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
    const params = new URLSearchParams({ user_id: finalUserId })
    
    if (surveyId) {
      params.append('survey_id', surveyId)
    }
    
    const response = await fetch(`${STATS_API_BASE_URL}/survey/submissions?${params}`)
    
    if (!response.ok) {
      const data = await response.json()
      return { success: false, error: data.error || '获取提交记录失败' }
    }
    
    const data = await response.json()
    return { success: true, submissions: data.submissions as StoredSubmission[] }
  } catch (error) {
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
    const params = new URLSearchParams({
      user_id: finalUserId,
      survey_id: surveyId
    })
    
    const response = await fetch(`${STATS_API_BASE_URL}/survey/check?${params}`)
    
    if (!response.ok) {
      const data = await response.json()
      return { success: false, error: data.error || '检查失败' }
    }
    
    const data = await response.json()
    return { success: true, hasSubmitted: data.hasSubmitted }
  } catch (error) {
    console.error('Error checking submission:', error)
    return { success: false, error: '网络错误' }
  }
}
