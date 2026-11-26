/**
 * 配置API客户端
 *
 * 请求样板（认证、解析、错误格式化）由 @/lib/http 的请求客户端承担；
 * 本文件只声明 endpoint、业务错误文案、响应解包规则与配置数据缓存。
 * 公开函数遵循 throw 契约：成功返回数据，失败抛 ApiError。
 */

import { getHostRuntime } from '@/host/state'
import { readBotNicknameVersion } from '@/lib/bot-nickname'
import { backendApi } from '@/lib/http'
import type { ConfigSchema } from '@/types/config-schema'

const API_BASE = '/api/webui/config'
export const BOT_CONFIG_UPDATED_EVENT = 'maibot:bot-config-updated'
export const BOT_CONFIG_QUERY_KEY = ['bot-config'] as const
const schemaRequestCache = new Map<string, Promise<ConfigSchema>>()
const configDataCache = new Map<
  string,
  { timestamp: number; request: Promise<unknown> }
>()
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
      // 只复用进行中或成功的请求；失败后释放缓存，让后续读取可以重新请求。
      if (schemaRequestCache.get(key) === request) {
        schemaRequestCache.delete(key)
      }
      throw error
    })

  schemaRequestCache.set(key, request)
  return request
}

function getCachedConfigResponse(key: string, url: string): Promise<unknown> {
  const cachedRequest = configDataCache.get(key)
  if (cachedRequest && Date.now() - cachedRequest.timestamp < CONFIG_DATA_CACHE_TTL) {
    return cachedRequest.request
  }

  const request = backendApi
    .get<unknown>(url, { cache: 'no-store', errorMessage: '获取配置失败' })
    .catch((error: unknown) => {
      // 失败后释放缓存，但不能清除过期或失效后已经启动的新请求。
      if (configDataCache.get(key)?.request === request) {
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

function getBotConfigUpdateStorageKey(): string | null {
  const { host, selected_bot_id: botId } = getHostRuntime()
  if (!host) return `${BOT_CONFIG_UPDATED_EVENT}:standalone`
  return botId ? `${BOT_CONFIG_UPDATED_EVENT}:bot:${botId}` : null
}

function dispatchBotConfigUpdated(): void {
  invalidateConfigDataCache('bot')
  if (typeof window !== 'undefined') {
    window.dispatchEvent(new Event(BOT_CONFIG_UPDATED_EVENT))
  }
}

function notifyBotConfigUpdated(): void {
  dispatchBotConfigUpdated()
  if (typeof window === 'undefined') return

  const key = getBotConfigUpdateStorageKey()
  if (!key) return

  try {
    // storage 事件只在其他标签页触发，值只用于标记本次保存。
    window.localStorage.setItem(key, Math.random().toString(36))
  } catch {
    // 浏览器禁用存储时，当前页面仍通过本地事件刷新。
  }
}

export function subscribeToBotConfigUpdatesFromOtherTabs(): () => void {
  const onStorage = (event: StorageEvent) => {
    if (
      event.key !== null &&
      event.key === getBotConfigUpdateStorageKey() &&
      event.newValue !== null &&
      event.storageArea === window.localStorage
    ) {
      dispatchBotConfigUpdated()
    }
  }
  window.addEventListener('storage', onStorage)
  return () => window.removeEventListener('storage', onStorage)
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
  return unwrapConfigResponse(await getCachedConfigResponse('bot', `${API_BASE}/bot`))
}

export async function getBotConfigSnapshotCached() {
  const data = await getCachedConfigResponse('bot', `${API_BASE}/bot`)
  const nicknameVersion = data && typeof data === 'object' && 'bot_nickname_version' in data
    ? readBotNicknameVersion(data.bot_nickname_version)
    : undefined
  return { config: unwrapConfigResponse(data), nicknameVersion }
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
  return unwrapConfigResponse(await getCachedConfigResponse('model', `${API_BASE}/model`))
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
  notifyBotConfigUpdated()
  return result
}

/**
 * 更新模型配置
 */
export async function updateModelConfig(
  config: Record<string, unknown>,
  catalogRevision?: string
): Promise<Record<string, unknown>> {
  const result = await backendApi.post<Record<string, unknown>>(`${API_BASE}/model${catalogRevision ? `?catalog_revision=${encodeURIComponent(catalogRevision)}` : ""}`, {
    body: config,
    errorMessage: '更新配置失败',
  })
  invalidateConfigDataCache('model')
  return result
}

/**
 * 分工方案信息
 */
export interface ModelConfigVersionInfo {
  id: string
  label: string
  created_at: number
  modified_at: number
  size: number
  active: boolean
  inner_config_version: string | null
  valid: boolean
  error: string | null
}

/**
 * 分工方案列表
 */
export interface ModelConfigVersionListResponse {
  success: boolean
  active_version: ModelConfigVersionInfo
  versions: ModelConfigVersionInfo[]
}

/**
 * 获取分工方案列表
 */
export async function getModelConfigVersions(): Promise<ModelConfigVersionListResponse> {
  return backendApi.get<ModelConfigVersionListResponse>(`${API_BASE}/model/versions`, {
    cache: 'no-store',
    errorMessage: '获取分工方案失败',
  })
}

/**
 * 将当前模型配置保存为副本
 */
export async function createModelConfigVersion(label: string): Promise<ModelConfigVersionInfo> {
  const result = await backendApi.post<{ version: ModelConfigVersionInfo }>(
    `${API_BASE}/model/versions`,
    {
      body: { label },
      errorMessage: '创建分工方案失败',
    }
  )
  return result.version
}

/**
 * 切换当前启用的分工方案
 */
export async function switchModelConfigVersion(versionId: string): Promise<ModelConfigVersionInfo> {
  const result = await backendApi.post<{ version: ModelConfigVersionInfo }>(
    `${API_BASE}/model/versions/${encodeURIComponent(versionId)}/activate`,
    {
      body: { archive_current: true },
      errorMessage: '切换分工方案失败',
    }
  )
  invalidateConfigDataCache('model')
  return result.version
}

/**
 * 更新分工方案名称
 */
export async function updateModelConfigVersionLabel(
  versionId: string,
  label: string
): Promise<ModelConfigVersionInfo> {
  const result = await backendApi.patch<{ version: ModelConfigVersionInfo }>(
    `${API_BASE}/model/versions/${encodeURIComponent(versionId)}`,
    {
      body: { label },
      errorMessage: '更新分工方案失败',
    }
  )
  return result.version
}

/**
 * 删除分工方案
 */
export async function deleteModelConfigVersion(versionId: string): Promise<void> {
  await backendApi.delete(`${API_BASE}/model/versions/${encodeURIComponent(versionId)}`, {
    errorMessage: '删除分工方案失败',
  })
}

/**
 * 更新麦麦主程序配置的指定节
 */
export async function updateBotConfigSection(
  sectionName: string,
  sectionData: unknown,
  errorMessage = '更新配置失败'
): Promise<Record<string, unknown>> {
  const result = await backendApi.post<Record<string, unknown>>(
    `${API_BASE}/bot/section/${sectionName}`,
    {
      body: sectionData,
      errorMessage,
    }
  )
  notifyBotConfigUpdated()
  return result
}

/**
 * 更新模型配置的指定节
 */
export async function updateModelConfigSection(
  sectionName: string,
  sectionData: unknown,
  catalogRevision?: string
): Promise<Record<string, unknown>> {
  const result = await backendApi.post<Record<string, unknown>>(
    `${API_BASE}/model/section/${sectionName}${catalogRevision ? `?catalog_revision=${encodeURIComponent(catalogRevision)}` : ""}`,
    {
      body: sectionData,
      errorMessage: '更新配置失败',
    }
  )
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
  formats?: {
    asr: Record<string, { label: string; voices: Record<string, string> }>
    tts: Record<string, { label: string; voices: Record<string, string> }>
    web: ('web_search' | 'web_fetch')[]
  }
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
 * 单个模型测试返回的工具调用摘要
 */
export interface ModelTestToolCall {
  id: string
  name: string
  arguments: Record<string, unknown>
}

/**
 * 嵌入对比测试的单组相似度结果
 */
export interface ModelTestEmbeddingPair {
  /** 对比说明，例如「相近文本」「不同图片」 */
  label: string
  /** 两组向量的余弦相似度 */
  similarity: number
}

/**
 * 单个模型测试结果
 */
export interface ModelTestResult {
  success: boolean
  model_name: string
  visual_tested: boolean
  tool_call_ok: boolean
  response: string
  reasoning: string
  tool_calls: ModelTestToolCall[]
  latency_ms: number | null
  error: string | null
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  /** 测试类型：chat / text_embedding / image_embedding */
  test_kind?: string
  /** 嵌入向量维度；仅嵌入测试返回 */
  embedding_dimension?: number | null
  /** 嵌入对比相似度结果；仅嵌入测试返回 */
  embedding_pairs?: ModelTestEmbeddingPair[]
}

/**
 * 测试提供商连接状态（通过提供商名称）
 * @param providerName 提供商名称
 */
export async function testProviderConnection(providerName: string): Promise<TestConnectionResult> {
  return backendApi.post<TestConnectionResult>('/api/webui/models/test-connection-by-name', {
    query: { provider_name: providerName },
    errorMessage: '测试提供商连接失败',
  })
}

/**
 * 测试单个模型的文本、tool call 与可选视觉能力
 * @param modelName 模型名称
 */
export async function testModelCapability(modelName: string): Promise<ModelTestResult> {
  return backendApi.post<ModelTestResult>('/api/webui/models/test-model', {
    body: { model_name: modelName },
    errorMessage: '测试模型能力失败',
  })
}

export function previewSpeech(
  model: string, voice: string, connection: { api_provider: string } | { draft_api_key: string }, signal: AbortSignal,
) {
  return backendApi.post<Blob>('/api/webui/models/preview-speech', {
    body: { model_identifier: model, voice_id: voice, ...connection },
    parse: 'blob', signal, errorMessage: '语音试听失败',
  })
}

export type ThinkingProbeMode = 'thinking_type' | 'enable_thinking' | 'reasoning_effort_nested' | 'reasoning_effort'

export interface ThinkingProbeResult {
  status: 'matched' | 'inconclusive'
  matches: ThinkingProbeMode[]
  attempts: {
    mode: ThinkingProbeMode
    status: 'matched' | 'inconclusive' | 'rejected'
    message: string
  }[]
  message: string
}

/** 使用当前模型草稿测试，凭据由后端从已保存的服务商配置读取。 */
export async function probeModelThinking(
  draft: {
    api_provider: string
    model_identifier: string
    extra_params: Record<string, unknown>
    force_stream_mode: boolean
  },
  signal?: AbortSignal,
): Promise<ThinkingProbeResult> {
  return backendApi.post<ThinkingProbeResult>('/api/webui/models/probe-thinking', {
    body: draft,
    signal,
    errorMessage: '测试思考参数失败',
  })
}
