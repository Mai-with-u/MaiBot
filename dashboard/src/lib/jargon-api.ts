/**
 * 黑话/术语 API
 */

import { fetchWithAuth } from './fetch-with-auth'

export interface Jargon {
  id: string
  term: string
  definition: string
  examples: string[]
  status: 'active' | 'inactive'
  created_at: string
  updated_at: string
}

export async function getJargonList(): Promise<{ success: boolean; data: Jargon[] }> {
  const response = await fetchWithAuth('/api/webui/jargons')
  return response.json()
}

export async function getJargonDetail(id: string): Promise<{ success: boolean; data: Jargon }> {
  const response = await fetchWithAuth(`/api/webui/jargons/${id}`)
  return response.json()
}

export async function createJargon(data: Partial<Jargon>): Promise<{ success: boolean; data?: Jargon; message?: string }> {
  const response = await fetchWithAuth('/api/webui/jargons', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  return response.json()
}

export async function updateJargon(id: string, data: Partial<Jargon>): Promise<{ success: boolean; message: string }> {
  const response = await fetchWithAuth(`/api/webui/jargons/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  return response.json()
}

export async function deleteJargon(id: string): Promise<{ success: boolean; message: string }> {
  const response = await fetchWithAuth(`/api/webui/jargons/${id}`, {
    method: 'DELETE',
  })
  return response.json()
}

export async function batchDeleteJargons(ids: string[]): Promise<{ success: boolean; message: string }> {
  const response = await fetchWithAuth('/api/webui/jargons/batch-delete', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ids }),
  })
  return response.json()
}

export async function getJargonStats(): Promise<{ success: boolean; data: { total: number; active: number } }> {
  const response = await fetchWithAuth('/api/webui/jargons/stats')
  return response.json()
}

export async function getJargonChatList(jargonId: string): Promise<{ success: boolean; data: unknown[] }> {
  const response = await fetchWithAuth(`/api/webui/jargons/${jargonId}/chats`)
  return response.json()
}

export async function batchSetJargonStatus(ids: string[], status: 'active' | 'inactive'): Promise<{ success: boolean; message: string }> {
  const response = await fetchWithAuth('/api/webui/jargons/batch-status', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ids, status }),
  })
  return response.json()
}
