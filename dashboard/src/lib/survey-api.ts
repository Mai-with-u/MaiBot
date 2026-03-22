/**
 * 问卷 API
 */

import { fetchWithAuth } from './fetch-with-auth'

export interface SurveySubmission {
  id: string
  surveyId: string
  answers: Record<string, unknown>
  submittedAt: string
}

export interface SurveyStats {
  total: number
  completed: number
}

export async function getSurveyStats(surveyId: string): Promise<{ success: boolean; data: SurveyStats }> {
  const response = await fetchWithAuth(`/api/webui/surveys/${surveyId}/stats`)
  return response.json()
}

export async function getUserSubmissions(surveyId: string): Promise<{ success: boolean; data: SurveySubmission[] }> {
  const response = await fetchWithAuth(`/api/webui/surveys/${surveyId}/submissions`)
  return response.json()
}

export async function submitSurvey(surveyId: string, answers: Record<string, unknown>): Promise<{ success: boolean; message: string }> {
  const response = await fetchWithAuth(`/api/webui/surveys/${surveyId}/submit`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ answers }),
  })
  return response.json()
}

export async function checkUserSubmission(surveyId: string): Promise<{ success: boolean; submitted: boolean }> {
  const response = await fetchWithAuth(`/api/webui/surveys/${surveyId}/check`)
  return response.json()
}
