/**
 * 插件配置 API
 *
 * 请求样板（认证、解析、错误格式化）由 @/lib/http 的请求客户端承担；
 * 本文件只声明 endpoint、业务错误文案与响应体 success 标记的解包规则。
 * 公开函数遵循 throw 契约：成功返回数据，失败抛 ApiError。
 */
import { ApiError, backendApi, requireSuccess } from '@/lib/http'

import type { PluginConfigSchema } from './types'

const API_BASE = '/api/webui/plugins/config'

/**
 * 获取插件配置 Schema
 */
export async function getPluginConfigSchema(pluginId: string): Promise<PluginConfigSchema> {
  const data = await backendApi.get<{ success: boolean; schema?: PluginConfigSchema; message?: string }>(
    `${API_BASE}/${pluginId}/schema`,
    { errorMessage: '获取配置 Schema 失败' }
  )
  const checked = requireSuccess(data, '获取配置 Schema 失败')
  if (!checked.schema) {
    throw new ApiError(checked.message || '获取配置 Schema 失败', { detail: checked })
  }
  return checked.schema
}

/**
 * 获取插件当前配置值
 */
export async function getPluginConfig(pluginId: string): Promise<Record<string, unknown>> {
  const data = await backendApi.get<{ success: boolean; config?: Record<string, unknown>; message?: string }>(
    `${API_BASE}/${pluginId}`,
    { errorMessage: '获取配置失败' }
  )
  const checked = requireSuccess(data, '获取配置失败')
  if (!checked.config) {
    throw new ApiError(checked.message || '获取配置失败', { detail: checked })
  }
  return checked.config
}

/**
 * 获取插件原始 TOML 配置
 */
export async function getPluginConfigRaw(pluginId: string): Promise<string> {
  const data = await backendApi.get<{ success: boolean; config?: string; message?: string }>(
    `${API_BASE}/${pluginId}/raw`,
    { errorMessage: '获取配置失败' }
  )
  const checked = requireSuccess(data, '获取配置失败')
  if (!checked.config) {
    throw new ApiError(checked.message || '获取配置失败', { detail: checked })
  }
  return checked.config
}

/**
 * 更新插件配置
 */
export async function updatePluginConfig(
  pluginId: string,
  config: Record<string, unknown>
): Promise<{ success: boolean; message: string; note?: string }> {
  return backendApi.put<{ success: boolean; message: string; note?: string }>(`${API_BASE}/${pluginId}`, {
    body: { config },
    errorMessage: '更新插件配置失败',
  })
}

/**
 * 更新插件原始 TOML 配置
 */
export async function updatePluginConfigRaw(
  pluginId: string,
  configToml: string
): Promise<{ success: boolean; message: string; note?: string }> {
  return backendApi.put<{ success: boolean; message: string; note?: string }>(`${API_BASE}/${pluginId}/raw`, {
    body: { config: configToml },
    errorMessage: '更新插件配置失败',
  })
}

/**
 * 重置插件配置为默认值
 */
export async function resetPluginConfig(
  pluginId: string
): Promise<{ success: boolean; message: string; backup?: string }> {
  return backendApi.post<{ success: boolean; message: string; backup?: string }>(`${API_BASE}/${pluginId}/reset`, {
    errorMessage: '重置插件配置失败',
  })
}

/**
 * 切换插件启用状态
 */
export async function togglePlugin(
  pluginId: string
): Promise<{ success: boolean; enabled: boolean; message: string; note?: string }> {
  return backendApi.post<{ success: boolean; enabled: boolean; message: string; note?: string }>(
    `${API_BASE}/${pluginId}/toggle`,
    { errorMessage: '切换插件状态失败' }
  )
}
