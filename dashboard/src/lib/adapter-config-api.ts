/**
 * 适配器配置 API
 */

import { fetchWithAuth } from './fetch-with-auth'

export interface AdapterConfig {
  id: string
  name: string
  type: string
  enabled: boolean
  config: Record<string, unknown>
}

export async function getAdapterList(): Promise<{ success: boolean; data: AdapterConfig[] }> {
  const response = await fetchWithAuth('/api/webui/adapters')
  return response.json()
}

export async function getAdapterConfig(id: string): Promise<{ success: boolean; data: AdapterConfig }> {
  const response = await fetchWithAuth(`/api/webui/adapters/${id}`)
  return response.json()
}

export async function updateAdapterConfig(id: string, config: Partial<AdapterConfig>): Promise<{ success: boolean; message: string }> {
  const response = await fetchWithAuth(`/api/webui/adapters/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(config),
  })
  return response.json()
}

export async function enableAdapter(id: string): Promise<{ success: boolean; message: string }> {
  const response = await fetchWithAuth(`/api/webui/adapters/${id}/enable`, {
    method: 'POST',
  })
  return response.json()
}

export async function disableAdapter(id: string): Promise<{ success: boolean; message: string }> {
  const response = await fetchWithAuth(`/api/webui/adapters/${id}/disable`, {
    method: 'POST',
  })
  return response.json()
}

export async function testAdapterConnection(id: string): Promise<{ success: boolean; message: string }> {
  const response = await fetchWithAuth(`/api/webui/adapters/${id}/test`, {
    method: 'POST',
  })
  return response.json()
}
