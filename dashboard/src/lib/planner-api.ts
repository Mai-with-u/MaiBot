/**
 * 计划器 API
 */

import { fetchWithAuth } from './fetch-with-auth'

export interface PlanItem {
  id: string
  content: string
  status: 'pending' | 'completed' | 'failed'
  created_at: string
}

export interface PlannerStatus {
  enabled: boolean
  currentPlan: PlanItem[]
  completedCount: number
  failedCount: number
}

export async function getPlannerStatus(): Promise<{ success: boolean; data: PlannerStatus }> {
  const response = await fetchWithAuth('/api/webui/planner/status')
  return response.json()
}

export async function getPlannerHistory(params?: { limit?: number }): Promise<{ success: boolean; data: PlanItem[] }> {
  const searchParams = new URLSearchParams()
  if (params?.limit) searchParams.set('limit', String(params.limit))

  const response = await fetchWithAuth(`/api/webui/planner/history?${searchParams}`)
  return response.json()
}

export async function enablePlanner(): Promise<{ success: boolean; message: string }> {
  const response = await fetchWithAuth('/api/webui/planner/enable', {
    method: 'POST',
  })
  return response.json()
}

export async function disablePlanner(): Promise<{ success: boolean; message: string }> {
  const response = await fetchWithAuth('/api/webui/planner/disable', {
    method: 'POST',
  })
  return response.json()
}

export async function clearPlannerHistory(): Promise<{ success: boolean; message: string }> {
  const response = await fetchWithAuth('/api/webui/planner/history', {
    method: 'DELETE',
  })
  return response.json()
}
