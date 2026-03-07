/**
 * 配置API客户端
 */

import { parseResponse } from '@/lib/api-helpers'
import { fetchWithAuth } from '@/lib/fetch-with-auth'
import type { ApiResponse } from '@/types/api'
import type { ConfigSchema } from '@/types/config-schema'

const API_BASE = '/api/webui/config'

/**
 * 获取麦麦主程序配置架构
 */
export async function getBotConfigSchema(): Promise<ApiResponse<ConfigSchema>> {
  const response = await fetchWithAuth(`${API_BASE}/schema/bot`)
  return parseResponse<ConfigSchema>(response)
}

/**
 * 获取模型配置架构
 */
export async function getModelConfigSchema(): Promise<ApiResponse<ConfigSchema>> {
  const response = await fetchWithAuth(`${API_BASE}/schema/model`)
  return parseResponse<ConfigSchema>(response)
}

/**
 * 获取指定配置节的架构
 */
export async function getConfigSectionSchema(sectionName: string): Promise<ApiResponse<ConfigSchema>> {
  const response = await fetchWithAuth(`${API_BASE}/schema/section/${sectionName}`)
  return parseResponse<ConfigSchema>(response)
}

/**
 * 获取麦麦主程序配置数据
 */
export async function getBotConfig(): Promise<ApiResponse<Record<string, unknown>>> {
  const response = await fetchWithAuth(`${API_BASE}/bot`)
  return parseResponse<Record<string, unknown>>(response)
}

/**
 * 获取模型配置数据
 */
export async function getModelConfig(): Promise<ApiResponse<Record<string, unknown>>> {
  const response = await fetchWithAuth(`${API_BASE}/model`)
  return parseResponse<Record<string, unknown>>(response)
}

/**
 * 更新麦麦主程序配置
 */
export async function updateBotConfig(
  config: Record<string, unknown>
): Promise<ApiResponse<Record<string, unknown>>> {
  const response = await fetchWithAuth(`${API_BASE}/bot`, {
    method: 'POST',
    body: JSON.stringify(config),
  })
  return parseResponse<Record<string, unknown>>(response)
}

/**
 * 获取麦麦主程序配置的原始 TOML 内容
 */
export async function getBotConfigRaw(): Promise<ApiResponse<string>> {
  const response = await fetchWithAuth(`${API_BASE}/bot/raw`)
  return parseResponse<string>(response)
}

/**
 * 更新麦麦主程序配置（原始 TOML 内容）
 */
export async function updateBotConfigRaw(rawContent: string): Promise<ApiResponse<Record<string, unknown>>> {
  const response = await fetchWithAuth(`${API_BASE}/bot/raw`, {
    method: 'POST',
    body: JSON.stringify({ raw_content: rawContent }),
  })
  return parseResponse<Record<string, unknown>>(response)
}

/**
 * 更新模型配置
 */
export async function updateModelConfig(
  config: Record<string, unknown>
): Promise<ApiResponse<Record<string, unknown>>> {
  const response = await fetchWithAuth(`${API_BASE}/model`, {
    method: 'POST',
    body: JSON.stringify(config),
  })
  return parseResponse<Record<string, unknown>>(response)
}

/**
 * 更新麦麦主程序配置的指定节
 */
export async function updateBotConfigSection(
  sectionName: string,
  sectionData: unknown
): Promise<ApiResponse<Record<string, unknown>>> {
  const response = await fetchWithAuth(`${API_BASE}/bot/section/${sectionName}`, {
    method: 'POST',
    body: JSON.stringify(sectionData),
  })
  return parseResponse<Record<string, unknown>>(response)
}

/**
 * 更新模型配置的指定节
 */
export async function updateModelConfigSection(
  sectionName: string,
  sectionData: unknown
): Promise<ApiResponse<Record<string, unknown>>> {
  const response = await fetchWithAuth(`${API_BASE}/model/section/${sectionName}`, {
    method: 'POST',
    body: JSON.stringify(sectionData),
  })
  return parseResponse<Record<string, unknown>>(response)
}

/**
 * 模型信息
 */
export interface ModelListItem {
  id: string
  name: string
  owned_by?: string
}

/**
 * 获取模型列表响应
 */
export interface FetchModelsResponse {
  success: boolean
  models: ModelListItem[]
  provider?: string
  count: number
}

/**
 * 获取指定提供商的可用模型列表
 * @param providerName 提供商名称（在 model_config.toml 中配置的名称）
 * @param parser 响应解析器类型 ('openai' | 'gemini')
 * @param endpoint 获取模型列表的端点（默认 '/models'）
 */
export async function fetchProviderModels(
  providerName: string,
  parser: 'openai' | 'gemini' = 'openai',
  endpoint: string = '/models'
): Promise<ApiResponse<ModelListItem[]>> {
  const params = new URLSearchParams({
    provider_name: providerName,
    parser,
    endpoint,
  })
  const response = await fetchWithAuth(`/api/webui/models/list?${params}`)
  return parseResponse<ModelListItem[]>(response)
}

/**
 * 测试提供商连接结果
 */
export interface TestConnectionResult {
  network_ok: boolean
  api_key_valid: boolean | null
  latency_ms: number | null
  error: string | null
  http_status: number | null
}

/**
 * 测试提供商连接状态（通过提供商名称）
 * @param providerName 提供商名称
 */
export async function testProviderConnection(
  providerName: string
): Promise<ApiResponse<TestConnectionResult>> {
  const params = new URLSearchParams({
    provider_name: providerName,
  })
  const response = await fetchWithAuth(`/api/webui/models/test-connection-by-name?${params}`, {
    method: 'POST',
  })
  return parseResponse<TestConnectionResult>(response)
}
