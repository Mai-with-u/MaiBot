/**
 * 插件统计 API
 */

import { fetchWithAuth } from './fetch-with-auth'

export interface PluginStatsData {
  total: number
  enabled: number
  installed: number
  downloads: number
}

export async function getPluginStats(): Promise<{ success: boolean; data: PluginStatsData }> {
  const response = await fetchWithAuth('/api/webui/plugins/stats')
  return response.json()
}

export async function recordPluginDownload(pluginId: string): Promise<{ success: boolean }> {
  const response = await fetchWithAuth(`/api/webui/plugins/${pluginId}/download`, {
    method: 'POST',
  })
  return response.json()
}
