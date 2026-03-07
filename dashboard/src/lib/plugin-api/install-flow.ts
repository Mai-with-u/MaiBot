import type { ApiResponse } from '@/types/api'

import { fetchWithAuth } from '@/lib/fetch-with-auth'
import { parseResponse } from '@/lib/api-helpers'

/**
 * 安装插件
 */
export async function installPlugin(pluginId: string, repositoryUrl: string, branch: string = 'main'): Promise<ApiResponse<{ success: boolean; message: string }>> {
  const response = await fetchWithAuth('/api/webui/plugins/install', {
    method: 'POST',
    body: JSON.stringify({
      plugin_id: pluginId,
      repository_url: repositoryUrl,
      branch: branch
    })
  })
  
  return await parseResponse<{ success: boolean; message: string }>(response)
}

/**
 * 卸载插件
 */
export async function uninstallPlugin(pluginId: string): Promise<ApiResponse<{ success: boolean; message: string }>> {
  const response = await fetchWithAuth('/api/webui/plugins/uninstall', {
    method: 'POST',
    body: JSON.stringify({
      plugin_id: pluginId
    })
  })
  
  return await parseResponse<{ success: boolean; message: string }>(response)
}

/**
 * 更新插件
 */
export async function updatePlugin(pluginId: string, repositoryUrl: string, branch: string = 'main'): Promise<ApiResponse<{ success: boolean; message: string; old_version: string; new_version: string }>> {
  const response = await fetchWithAuth('/api/webui/plugins/update', {
    method: 'POST',
    body: JSON.stringify({
      plugin_id: pluginId,
      repository_url: repositoryUrl,
      branch: branch
    })
  })
  
  return await parseResponse<{ success: boolean; message: string; old_version: string; new_version: string }>(response)
}
