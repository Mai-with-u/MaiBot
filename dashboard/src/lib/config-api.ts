/**
 * 配置API客户端
 *
 * 请求样板（认证、解析、错误格式化）由 @/lib/http 的请求客户端承担；
 * 本文件只声明 endpoint、业务错误文案、响应解包规则与配置数据缓存。
 * 公开函数遵循 throw 契约：成功返回数据，失败抛 ApiError。
 */

import { ApiError, backendApi } from '@/lib/http'
import type { ConfigSchema } from '@/types/config-schema'

const API_BASE = '/api/webui/config'
export const BOT_CONFIG_UPDATED_EVENT = 'maibot:bot-config-updated'
const schemaRequestCache = new Map<string, Promise<ConfigSchema>>()
const configDataCache = new Map<string, { timestamp: number; request: Promise<Record<string, unknown>> }>()
const CONFIG_DATA_CACHE_TTL = 30_000

function unwrapConfigResponse(data: unknown): Record<string, unknown> {
  if (
    data &&
    typeof data === 'object' &&
    'config' in data &&
    (data as Record<string, unknown>).config &&
    typeof (data as Record<string, unknown>).config === 'object'
  ) {
    return (data as { config: Record<string, unknown> }).config
  }

  if (data && typeof data === 'object') {
    return data as Record<string, unknown>
  }

  return {}
}

function getCachedSchema(key: string, url: string): Promise<ConfigSchema> {
  const cachedRequest = schemaRequestCache.get(key)
  if (cachedRequest) {
    return cachedRequest
  }

  const request = backendApi
    .get<ConfigSchema>(url, { cache: 'no-store', errorMessage: '获取配置架构失败' })
    .catch((error: unknown) => {
      // HTTP 层失败（带 status 的 ApiError）保留被拒绝的 Promise 在缓存中，
      // 避免对失败的配置接口反复发起请求；请求未到达服务器等瞬时异常则剔除缓存后重抛
      if (!(error instanceof ApiError) || error.status === undefined) {
        schemaRequestCache.delete(key)
      }
      throw error
    })

  schemaRequestCache.set(key, request)
  return request
}

function getCachedConfigData(key: string, url: string): Promise<Record<string, unknown>> {
  const cachedRequest = configDataCache.get(key)
  if (cachedRequest && Date.now() - cachedRequest.timestamp < CONFIG_DATA_CACHE_TTL) {
    return cachedRequest.request
  }

  const request = backendApi
    .get<unknown>(url, { cache: 'no-store', errorMessage: '获取配置失败' })
    .then((data) => unwrapConfigResponse(data))
    .catch((error: unknown) => {
      // HTTP 层失败保留被拒绝的 Promise 在缓存中，避免反复请求失败接口；瞬时异常则剔除缓存后重抛
      if (!(error instanceof ApiError) || error.status === undefined) {
        configDataCache.delete(key)
      }
      throw error
    })

  configDataCache.set(key, { timestamp: Date.now(), request })
  return request
}

function invalidateConfigDataCache(key?: string): void {
  if (key) {
    configDataCache.delete(key)
    return
  }
  configDataCache.clear()
}

function notifyBotConfigUpdated(): void {
  if (typeof window !== 'undefined') {
    window.dispatchEvent(new Event(BOT_CONFIG_UPDATED_EVENT))
  }
}

/**
 * 获取麦麦主程序配置架构
 */
export async function getBotConfigSchema(): Promise<ConfigSchema> {
  return getCachedSchema('bot', `${API_BASE}/schema/bot`)
}

/**
 * 获取模型配置架构
 */
export async function getModelConfigSchema(): Promise<ConfigSchema> {
  return getCachedSchema('model', `${API_BASE}/schema/model`)
}

/**
 * 获取指定配置节的架构
 */
export async function getConfigSectionSchema(sectionName: string): Promise<ConfigSchema> {
  return getCachedSchema(`section:${sectionName}`, `${API_BASE}/schema/section/${sectionName}`)
}

/**
 * 获取麦麦主程序配置数据
 */
export async function getBotConfig(): Promise<Record<string, unknown>> {
  const data = await backendApi.get<unknown>(`${API_BASE}/bot`, {
    cache: 'no-store',
    errorMessage: '获取配置失败',
  })
  return unwrapConfigResponse(data)
}

/** Cached config data for lightweight status summaries. */
export async function getBotConfigCached(): Promise<Record<string, unknown>> {
  return getCachedConfigData('bot', `${API_BASE}/bot`)
}

/**
 * 获取模型配置数据
 */
export async function getModelConfig(): Promise<Record<string, unknown>> {
  const data = await backendApi.get<unknown>(`${API_BASE}/model`, {
    cache: 'no-store',
    errorMessage: '获取配置失败',
  })
  return unwrapConfigResponse(data)
}

/** Cached model config data for lightweight status summaries. */
export async function getModelConfigCached(): Promise<Record<string, unknown>> {
  return getCachedConfigData('model', `${API_BASE}/model`)
}

/**
 * 更新麦麦主程序配置
 */
export async function updateBotConfig(
  config: Record<string, unknown>
): Promise<Record<string, unknown>> {
  const result = await backendApi.post<Record<string, unknown>>(`${API_BASE}/bot`, {
    body: config,
    errorMessage: '更新配置失败',
  })
  invalidateConfigDataCache('bot')
  notifyBotConfigUpdated()
  return result
}

/**
 * 获取麦麦主程序配置的原始 TOML 内容
 */
export async function getBotConfigRaw(): Promise<string> {
  return backendApi.get<string>(`${API_BASE}/bot/raw`, {
    cache: 'no-store',
    errorMessage: '获取原始配置失败',
  })
}

/**
 * 更新麦麦主程序配置（原始 TOML 内容）
 */
export async function updateBotConfigRaw(rawContent: string): Promise<Record<string, unknown>> {
  const result = await backendApi.post<Record<string, unknown>>(`${API_BASE}/bot/raw`, {
    body: { raw_content: rawContent },
    errorMessage: '更新配置失败',
  })
  invalidateConfigDataCache('bot')
  notifyBotConfigUpdated()
  return result
}

/**
 * 更新模型配置
 */
export async function updateModelConfig(
  config: Record<string, unknown>
): Promise<Record<string, unknown>> {
  const result = await backendApi.post<Record<string, unknown>>(`${API_BASE}/model`, {
    body: config,
    errorMessage: '更新配置失败',
  })
  invalidateConfigDataCache('model')
  return result
}

/**
 * 更新麦麦主程序配置的指定节
 */
export async function updateBotConfigSection(
  sectionName: string,
  sectionData: unknown
): Promise<Record<string, unknown>> {
  const result = await backendApi.post<Record<string, unknown>>(`${API_BASE}/bot/section/${sectionName}`, {
    body: sectionData,
    errorMessage: '更新配置失败',
  })
  invalidateConfigDataCache('bot')
  notifyBotConfigUpdated()
  return result
}

/**
 * 更新模型配置的指定节
 */
export async function updateModelConfigSection(
  sectionName: string,
  sectionData: unknown
): Promise<Record<string, unknown>> {
  const result = await backendApi.post<Record<string, unknown>>(`${API_BASE}/model/section/${sectionName}`, {
    body: sectionData,
    errorMessage: '更新配置失败',
  })
  invalidateConfigDataCache('model')
  return result
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
 * 已注册的模型客户端类型
 */
export interface ModelClientType {
  client_type: string
  owner_plugin_id: string | null
  version: string
  description: string
  builtin: boolean
}

/**
 * 获取当前主程序与插件已注册的模型客户端类型
 */
export async function fetchModelClientTypes(): Promise<ModelClientType[]> {
  const body = await backendApi.get<{ client_types?: ModelClientType[] } | ModelClientType[]>(
    '/api/webui/models/client-types',
    {
      errorMessage: '获取模型客户端类型失败',
    }
  )
  return Array.isArray(body) ? body : Array.isArray(body?.client_types) ? body.client_types : []
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
): Promise<ModelListItem[]> {
  // 后端返回 { success, models, provider, count }，需要展开取出 models 数组
  const body = await backendApi.get<{ models?: ModelListItem[] } | ModelListItem[]>(
    '/api/webui/models/list',
    {
      query: {
        provider_name: providerName,
        parser,
        endpoint,
      },
      errorMessage: '获取模型列表失败',
    }
  )
  return Array.isArray(body) ? body : Array.isArray(body?.models) ? body.models : []
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
): Promise<TestConnectionResult> {
  return backendApi.post<TestConnectionResult>('/api/webui/models/test-connection-by-name', {
    query: { provider_name: providerName },
    errorMessage: '测试提供商连接失败',
  })
}
