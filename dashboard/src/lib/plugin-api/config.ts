import type { ApiResponse } from '@/types/api'

import { fetchWithAuth, getAuthHeaders } from '@/lib/fetch-with-auth'
import { parseResponse } from '@/lib/api-helpers'

import type { PluginConfigSchema } from './types'

/**
 * 获取插件配置 Schema
 */
export async function getPluginConfigSchema(pluginId: string): Promise<ApiResponse<PluginConfigSchema>> {
  const response = await fetchWithAuth(`/api/webui/plugins/config/${pluginId}/schema`, {
    headers: getAuthHeaders()
  })
  
  const apiResult = await parseResponse<{ success: boolean; schema?: PluginConfigSchema; message?: string }>(response)
  
  if (!apiResult.success) {
    return apiResult
  }
  
  const result = apiResult.data
  if (!result.success || !result.schema) {
    return {
      success: false,
      error: result.message || '获取配置 Schema 失败'
    }
  }
  
  return {
    success: true,
    data: result.schema
  }
}

/**
 * 获取插件当前配置值
 */
export async function getPluginConfig(pluginId: string): Promise<ApiResponse<Record<string, unknown>>> {
  const response = await fetchWithAuth(`/api/webui/plugins/config/${pluginId}`, {
    headers: getAuthHeaders()
  })
  
  const apiResult = await parseResponse<{ success: boolean; config?: Record<string, unknown>; message?: string }>(response)
  
  if (!apiResult.success) {
    return apiResult
  }
  
  const result = apiResult.data
  if (!result.success || !result.config) {
    return {
      success: false,
      error: result.message || '获取配置失败'
    }
  }
  
  return {
    success: true,
    data: result.config
  }
}

/**
 * 获取插件原始 TOML 配置
 */
export async function getPluginConfigRaw(pluginId: string): Promise<ApiResponse<string>> {
  const response = await fetchWithAuth(`/api/webui/plugins/config/${pluginId}/raw`, {
    headers: getAuthHeaders()
  })
  
  const apiResult = await parseResponse<{ success: boolean; config?: string; message?: string }>(response)
  
  if (!apiResult.success) {
    return apiResult
  }
  
  const result = apiResult.data
  if (!result.success || !result.config) {
    return {
      success: false,
      error: result.message || '获取配置失败'
    }
  }
  
  return {
    success: true,
    data: result.config
  }
}

/**
 * 更新插件配置
 */
export async function updatePluginConfig(
  pluginId: string,
  config: Record<string, unknown>
): Promise<ApiResponse<{ success: boolean; message: string; note?: string }>> {
  const response = await fetchWithAuth(`/api/webui/plugins/config/${pluginId}`, {
    method: 'PUT',
    headers: getAuthHeaders(),
    body: JSON.stringify({ config })
  })
  
  return await parseResponse<{ success: boolean; message: string; note?: string }>(response)
}

/**
 * 更新插件原始 TOML 配置
 */
export async function updatePluginConfigRaw(
  pluginId: string,
  configToml: string
): Promise<ApiResponse<{ success: boolean; message: string; note?: string }>> {
  const response = await fetchWithAuth(`/api/webui/plugins/config/${pluginId}/raw`, {
    method: 'PUT',
    headers: getAuthHeaders(),
    body: JSON.stringify({ config: configToml })
  })
  
  return await parseResponse<{ success: boolean; message: string; note?: string }>(response)
}

/**
 * 重置插件配置为默认值
 */
export async function resetPluginConfig(
  pluginId: string
): Promise<ApiResponse<{ success: boolean; message: string; backup?: string }>> {
  const response = await fetchWithAuth(`/api/webui/plugins/config/${pluginId}/reset`, {
    method: 'POST',
    headers: getAuthHeaders()
  })
  
  return await parseResponse<{ success: boolean; message: string; backup?: string }>(response)
}

/**
 * 切换插件启用状态
 */
export async function togglePlugin(
  pluginId: string
): Promise<ApiResponse<{ success: boolean; enabled: boolean; message: string; note?: string }>> {
  const response = await fetchWithAuth(`/api/webui/plugins/config/${pluginId}/toggle`, {
    method: 'POST',
    headers: getAuthHeaders()
  })
  
  return await parseResponse<{ success: boolean; enabled: boolean; message: string; note?: string }>(response)
}
