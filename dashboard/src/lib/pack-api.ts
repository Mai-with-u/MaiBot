/**
 * 模型配置 Pack API
 *
 * 与 Cloudflare Workers Pack 服务（statsApi 实例，与统计服务同源）
 * 和主后端模型配置接口（backendApi 实例）交互。
 * 请求样板（认证、解析、错误格式化）由 @/lib/http 的请求客户端承担；
 * 本文件只声明 endpoint、业务错误文案与 Pack 服务包络的解包规则。
 */
import { ApiError, backendApi, statsApi } from '@/lib/http'

// ============ 类型定义 ============

/**
 * 提供商配置（分享时不含 api_key）
 */
export interface PackProvider {
  name: string
  base_url: string
  client_type: 'openai' | 'gemini'
  max_retry?: number
  timeout?: number
  retry_interval?: number
}

/**
 * 模型配置
 */
export interface PackModel {
  model_identifier: string
  name: string
  api_provider: string
  price_in: number
  price_out: number
  temperature?: number
  max_tokens?: number
  force_stream_mode?: boolean
  extra_params?: Record<string, unknown>
}

/**
 * 单个任务配置
 */
export interface PackTaskConfig {
  model_list: string[]
  temperature?: number
  max_tokens?: number
  slow_threshold?: number
}

/**
 * 所有任务配置
 */
export interface PackTaskConfigs {
  utils?: PackTaskConfig
  utils_small?: PackTaskConfig
  tool_use?: PackTaskConfig
  replyer?: PackTaskConfig
  learner?: PackTaskConfig
  planner?: PackTaskConfig
  vlm?: PackTaskConfig
  voice?: PackTaskConfig
  embedding?: PackTaskConfig
  lpmm_entity_extract?: PackTaskConfig
  lpmm_rdf_build?: PackTaskConfig
}

/**
 * Pack 列表项
 */
export interface PackListItem {
  id: string
  name: string
  description: string
  author: string
  version: string
  created_at: string
  updated_at: string
  status: 'pending' | 'approved' | 'rejected'
  reject_reason?: string
  downloads: number
  likes: number
  tags?: string[]
  provider_count: number
  model_count: number
  task_count: number
}

/**
 * 完整的 Pack 数据
 */
export interface ModelPack extends Omit<PackListItem, 'provider_count' | 'model_count' | 'task_count'> {
  providers: PackProvider[]
  models: PackModel[]
  task_config: PackTaskConfigs
}

/**
 * Pack 列表响应
 */
export interface ListPacksResponse {
  packs: PackListItem[]
  total: number
  page: number
  page_size: number
  total_pages: number
}

/**
 * 应用 Pack 时的选项
 */
export interface ApplyPackOptions {
  apply_providers: boolean
  apply_models: boolean
  apply_task_config: boolean
  task_mode: 'replace' | 'append'
  selected_providers?: string[]
  selected_models?: string[]
  selected_tasks?: string[]
}

/**
 * 应用 Pack 时的冲突检测结果
 */
export interface ApplyPackConflicts {
  existing_providers: Array<{
    pack_provider: PackProvider
    local_providers: Array<{  // 改为数组，支持多个匹配
      name: string
      base_url: string
    }>
  }>
  new_providers: PackProvider[]
  conflicting_models: Array<{
    pack_model: string
    local_model: string
  }>
}

/**
 * Pack 服务的业务包络：失败时以 error 字段（而非 message）给出原因，
 * 因此不使用 requireSuccess，逐函数手动校验。
 */
interface PackEnvelope {
  success: boolean
  error?: string
}

/** 本地模型配置中的提供商条目（本地配置含 api_key，分享 Pack 时会被剥离） */
type LocalProviderConfig = PackProvider & { api_key?: string }

/** /api/webui/config/model 返回的本地模型配置（仅声明本文件用到的字段） */
interface LocalModelConfig {
  api_providers: LocalProviderConfig[]
  models: PackModel[]
  model_task_config: Record<string, PackTaskConfig>
}

/** 模型配置接口响应体：{ success, config } 包络 */
interface ModelConfigResponse {
  success?: boolean
  config?: LocalModelConfig
}

// ============ API 函数 ============

/**
 * 获取 Pack 列表
 */
export async function listPacks(params?: {
  status?: 'pending' | 'approved' | 'rejected' | 'all'
  page?: number
  page_size?: number
  search?: string
  sort_by?: 'created_at' | 'downloads' | 'likes'
  sort_order?: 'asc' | 'desc'
}): Promise<ListPacksResponse> {
  return statsApi.get<ListPacksResponse>('/pack', {
    query: {
      status: params?.status,
      page: params?.page || undefined,
      page_size: params?.page_size || undefined,
      search: params?.search || undefined,
      sort_by: params?.sort_by,
      sort_order: params?.sort_order,
    },
    errorMessage: '获取 Pack 列表失败',
  })
}

/**
 * 获取单个 Pack 详情
 */
export async function getPack(packId: string): Promise<ModelPack> {
  const data = await statsApi.get<PackEnvelope & { pack: ModelPack }>(`/pack/${packId}`, {
    errorMessage: '获取 Pack 失败',
  })
  if (!data.success) {
    throw new ApiError(data.error || '获取 Pack 失败', { detail: data })
  }
  return data.pack
}

/**
 * 记录 Pack 下载
 */
export async function recordPackDownload(packId: string, userId?: string): Promise<void> {
  // 下载计数为尽力而为的统计：原实现不检查 response.ok 也不读取响应体，
  // HTTP 层失败不影响调用方；网络层失败（status 为 undefined）保持抛出。
  try {
    await statsApi.post('/pack/download', {
      body: { pack_id: packId, user_id: userId },
      parse: 'response',
      errorMessage: '记录 Pack 下载失败',
    })
  } catch (error) {
    if (!(error instanceof ApiError) || error.status === undefined) {
      throw error
    }
  }
}

/**
 * 点赞/取消点赞 Pack
 */
export async function togglePackLike(packId: string, userId: string): Promise<{ likes: number; liked: boolean }> {
  const data = await statsApi.post<PackEnvelope & { likes: number; liked: boolean }>('/pack/like', {
    body: { pack_id: packId, user_id: userId },
    errorMessage: '点赞失败',
  })
  if (!data.success) {
    throw new ApiError(data.error || '点赞失败', { detail: data })
  }
  return { likes: data.likes, liked: data.liked }
}

/**
 * 检查是否已点赞
 */
export async function checkPackLike(packId: string, userId: string): Promise<boolean> {
  const data = await statsApi.get<{ liked?: boolean }>('/pack/like/check', {
    query: { pack_id: packId, user_id: userId },
    errorMessage: '查询 Pack 点赞状态失败',
  })
  return data.liked || false
}

// ============ 本地应用 Pack 相关 ============

/**
 * 检测应用 Pack 时的冲突
 */
export async function detectPackConflicts(
  pack: ModelPack
): Promise<ApplyPackConflicts> {
  // 获取当前配置
  const responseData = await backendApi.get<ModelConfigResponse>('/api/webui/config/model', {
    errorMessage: '获取当前模型配置失败',
  })
  const currentConfig = (responseData.config || responseData) as LocalModelConfig

  const conflicts: ApplyPackConflicts = {
    existing_providers: [],
    new_providers: [],
    conflicting_models: [],
  }

  // 检测提供商冲突
  const localProviders = currentConfig.api_providers || []
  for (const packProvider of pack.providers) {
    // 按 URL 匹配 - 找出所有匹配的本地提供商
    const matchedProviders = localProviders.filter(
      (p: { base_url: string; name: string }) => {
        const localNormalized = normalizeUrl(p.base_url)
        const packNormalized = normalizeUrl(packProvider.base_url)
        return localNormalized === packNormalized
      }
    )

    if (matchedProviders.length > 0) {
      conflicts.existing_providers.push({
        pack_provider: packProvider,
        local_providers: matchedProviders.map((p: { name: string; base_url: string }) => ({
          name: p.name,
          base_url: p.base_url,
        })),
      })
    } else {
      conflicts.new_providers.push(packProvider)
    }
  }

  // 检测模型名称冲突
  const localModels = currentConfig.models || []
  for (const packModel of pack.models) {
    const conflictModel = localModels.find(
      (m: { name: string }) => m.name === packModel.name
    )
    if (conflictModel) {
      conflicts.conflicting_models.push({
        pack_model: packModel.name,
        local_model: conflictModel.name,
      })
    }
  }

  return conflicts
}

/**
 * 应用 Pack 到本地配置
 */
export async function applyPack(
  pack: ModelPack,
  options: ApplyPackOptions,
  providerMapping: Record<string, string>,  // pack_provider_name -> local_provider_name
  newProviderApiKeys: Record<string, string>,  // provider_name -> api_key
): Promise<void> {
  // 获取当前配置
  const responseData = await backendApi.get<ModelConfigResponse>('/api/webui/config/model', {
    errorMessage: '获取当前模型配置失败',
  })
  const currentConfig = (responseData.config || responseData) as LocalModelConfig

  // 1. 处理提供商
  if (options.apply_providers) {
    const providersToApply = options.selected_providers
      ? pack.providers.filter(p => options.selected_providers!.includes(p.name))
      : pack.providers

    for (const packProvider of providersToApply) {
      // 检查是否映射到已有提供商
      if (providerMapping[packProvider.name]) {
        // 使用已有提供商，不需要添加
        continue
      }

      // 添加新提供商
      const apiKey = newProviderApiKeys[packProvider.name]
      if (!apiKey) {
        throw new Error(`提供商 "${packProvider.name}" 缺少 API Key`)
      }

      const newProvider = {
        ...packProvider,
        api_key: apiKey,
      }

      // 检查是否已存在同名提供商
      const existingIndex = currentConfig.api_providers.findIndex(
        (p: { name: string }) => p.name === packProvider.name
      )

      if (existingIndex >= 0) {
        // 覆盖
        currentConfig.api_providers[existingIndex] = newProvider
      } else {
        // 添加
        currentConfig.api_providers.push(newProvider)
      }
    }
  }

  // 2. 处理模型
  if (options.apply_models) {
    const modelsToApply = options.selected_models
      ? pack.models.filter(m => options.selected_models!.includes(m.name))
      : pack.models

    for (const packModel of modelsToApply) {
      // 映射提供商名称
      const actualProvider = providerMapping[packModel.api_provider] || packModel.api_provider

      const newModel = {
        ...packModel,
        api_provider: actualProvider,
      }

      // 检查是否已存在同名模型
      const existingIndex = currentConfig.models.findIndex(
        (m: { name: string }) => m.name === packModel.name
      )

      if (existingIndex >= 0) {
        // 覆盖
        currentConfig.models[existingIndex] = newModel
      } else {
        // 添加
        currentConfig.models.push(newModel)
      }
    }
  }

  // 3. 处理任务配置
  if (options.apply_task_config) {
    const taskKeys = options.selected_tasks || Object.keys(pack.task_config)

    for (const taskKey of taskKeys) {
      const packTaskConfig = pack.task_config[taskKey as keyof PackTaskConfigs]
      if (!packTaskConfig) continue

      // 映射模型名称（如果模型名称被跳过，则从任务列表中移除）
      const appliedModelNames = new Set(
        options.selected_models || pack.models.map(m => m.name)
      )
      const filteredModelList = packTaskConfig.model_list.filter(
        name => appliedModelNames.has(name)
      )

      if (filteredModelList.length === 0) continue

      const newTaskConfig = {
        ...packTaskConfig,
        model_list: filteredModelList,
      }

      if (options.task_mode === 'replace') {
        // 替换模式
        currentConfig.model_task_config[taskKey] = newTaskConfig
      } else {
        // 追加模式
        const existingConfig = currentConfig.model_task_config[taskKey]
        if (existingConfig) {
          // 合并模型列表（去重）
          const mergedList = [...new Set([
            ...existingConfig.model_list,
            ...filteredModelList,
          ])]
          currentConfig.model_task_config[taskKey] = {
            ...existingConfig,
            model_list: mergedList,
          }
        } else {
          currentConfig.model_task_config[taskKey] = newTaskConfig
        }
      }
    }
  }

  // 保存配置（原实现不读取保存接口的响应体，保持 parse: 'response'）
  await backendApi.post('/api/webui/config/model', {
    body: currentConfig,
    parse: 'response',
    errorMessage: '保存配置失败',
  })
}

// ============ 辅助函数 ============

/**
 * 标准化 URL 用于比较
 */
function normalizeUrl(url: string): string {
  try {
    const parsed = new URL(url)
    // 移除末尾斜杠，统一小写
    return `${parsed.protocol}//${parsed.host}${parsed.pathname}`.replace(/\/$/, '').toLowerCase()
  } catch {
    return url.toLowerCase().replace(/\/$/, '')
  }
}

/**
 * 生成用户 ID（用于统计）
 */
export function getPackUserId(): string {
  const storageKey = 'maibot_pack_user_id'
  let userId = localStorage.getItem(storageKey)
  if (!userId) {
    userId = 'pack_user_' + Math.random().toString(36).substring(2, 15)
    localStorage.setItem(storageKey, userId)
  }
  return userId
}
