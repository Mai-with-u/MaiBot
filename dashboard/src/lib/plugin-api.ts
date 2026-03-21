/**
 * 插件 API
 */

import { fetchWithAuth } from './fetch-with-auth'

export interface Plugin {
  id: string
  name: string
  description: string
  version: string
  author: string
  enabled: boolean
  installed: boolean
  config?: Record<string, unknown>
}

export interface PluginListResponse {
  success: boolean
  data: Plugin[]
  total: number
}

export async function getPluginList(): Promise<PluginListResponse> {
  const response = await fetchWithAuth('/api/webui/plugins')
  return response.json()
}

export async function getPluginDetail(id: string): Promise<{ success: boolean; data: Plugin }> {
  const response = await fetchWithAuth(`/api/webui/plugins/${id}`)
  return response.json()
}

export async function installPlugin(id: string): Promise<{ success: boolean; message: string }> {
  const response = await fetchWithAuth(`/api/webui/plugins/${id}/install`, {
    method: 'POST',
  })
  return response.json()
}

export async function uninstallPlugin(id: string): Promise<{ success: boolean; message: string }> {
  const response = await fetchWithAuth(`/api/webui/plugins/${id}/uninstall`, {
    method: 'POST',
  })
  return response.json()
}

export async function enablePlugin(id: string): Promise<{ success: boolean; message: string }> {
  const response = await fetchWithAuth(`/api/webui/plugins/${id}/enable`, {
    method: 'POST',
  })
  return response.json()
}

export async function disablePlugin(id: string): Promise<{ success: boolean; message: string }> {
  const response = await fetchWithAuth(`/api/webui/plugins/${id}/disable`, {
    method: 'POST',
  })
  return response.json()
}

export async function updatePluginConfig(id: string, config: Record<string, unknown>): Promise<{ success: boolean; message: string }> {
  const response = await fetchWithAuth(`/api/webui/plugins/${id}/config`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(config),
  })
  return response.json()
}

export async function reloadPlugins(): Promise<{ success: boolean; message: string }> {
  const response = await fetchWithAuth('/api/webui/plugins/reload', {
    method: 'POST',
  })
  return response.json()
}
