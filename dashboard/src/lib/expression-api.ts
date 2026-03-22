/**
 * 表达方式 API
 */

import { fetchWithAuth } from './fetch-with-auth'

export interface Expression {
  id: string
  name: string
  description: string
  content: string
  created_at: string
  updated_at: string
}

export interface ExpressionListResponse {
  success: boolean
  data: Expression[]
  total: number
}

export async function getExpressionList(params?: {
  page?: number
  pageSize?: number
  search?: string
}): Promise<ExpressionListResponse> {
  const searchParams = new URLSearchParams()
  if (params?.page) searchParams.set('page', String(params.page))
  if (params?.pageSize) searchParams.set('pageSize', String(params.pageSize))
  if (params?.search) searchParams.set('search', params.search)

  const response = await fetchWithAuth(`/api/webui/expressions?${searchParams}`)
  return response.json()
}

export async function getExpressionDetail(id: string): Promise<{ success: boolean; data: Expression }> {
  const response = await fetchWithAuth(`/api/webui/expressions/${id}`)
  return response.json()
}

export async function createExpression(data: Partial<Expression>): Promise<{ success: boolean; data?: Expression; message?: string }> {
  const response = await fetchWithAuth('/api/webui/expressions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  return response.json()
}

export async function updateExpression(id: string, data: Partial<Expression>): Promise<{ success: boolean; message?: string }> {
  const response = await fetchWithAuth(`/api/webui/expressions/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  return response.json()
}

export async function deleteExpression(id: string): Promise<{ success: boolean; message?: string }> {
  const response = await fetchWithAuth(`/api/webui/expressions/${id}`, {
    method: 'DELETE',
  })
  return response.json()
}

export async function batchDeleteExpressions(ids: string[]): Promise<{ success: boolean; message?: string }> {
  const response = await fetchWithAuth('/api/webui/expressions/batch-delete', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ids }),
  })
  return response.json()
}

export async function getExpressionStats(): Promise<{ success: boolean; data: { total: number; active: number } }> {
  const response = await fetchWithAuth('/api/webui/expressions/stats')
  return response.json()
}

export async function getChatList(params?: { limit?: number }): Promise<{ success: boolean; data: unknown[] }> {
  const searchParams = new URLSearchParams()
  if (params?.limit) searchParams.set('limit', String(params.limit))

  const response = await fetchWithAuth(`/api/webui/chats?${searchParams}`)
  return response.json()
}

export async function getReviewStats(): Promise<{ success: boolean; data: { total: number; pending: number } }> {
  const response = await fetchWithAuth('/api/webui/reviews/stats')
  return response.json()
}
