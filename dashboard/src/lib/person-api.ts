/**
 * 人物 API
 */

import { fetchWithAuth } from './fetch-with-auth'

export interface Person {
  id: string
  name: string
  description: string
  avatar?: string
  created_at: string
  updated_at: string
}

export async function getPersonList(): Promise<{ success: boolean; data: Person[] }> {
  const response = await fetchWithAuth('/api/webui/persons')
  return response.json()
}

export async function getPersonDetail(id: string): Promise<{ success: boolean; data: Person }> {
  const response = await fetchWithAuth(`/api/webui/persons/${id}`)
  return response.json()
}

export async function updatePerson(id: string, data: Partial<Person>): Promise<{ success: boolean; message: string }> {
  const response = await fetchWithAuth(`/api/webui/persons/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  return response.json()
}

export async function deletePerson(id: string): Promise<{ success: boolean; message: string }> {
  const response = await fetchWithAuth(`/api/webui/persons/${id}`, {
    method: 'DELETE',
  })
  return response.json()
}

export async function getPersonStats(): Promise<{ success: boolean; data: { total: number } }> {
  const response = await fetchWithAuth('/api/webui/persons/stats')
  return response.json()
}

export async function batchDeletePersons(ids: string[]): Promise<{ success: boolean; message: string }> {
  const response = await fetchWithAuth('/api/webui/persons/batch-delete', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ids }),
  })
  return response.json()
}
