/**
 * 模型配置 Pack API
 * 
 * 与 Cloudflare Workers Pack 服务交互
 */

import { fetchWithAuth } from './fetch-with-auth'

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
  planner?: PackTaskConfig
  vlm?: PackTaskConfig
  voice?: PackTaskConfig
  embedding?: PackTaskConfig
  lpmm_entity_extract?: PackTaskConfig
  lpmm_rdf_build?: PackTaskConfig
  lpmm_qa?: PackTaskConfig
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

// ============ API 配置 ============

// Pack 服务基础 URL（Cloudflare Workers）
const PACK_SERVICE_URL = 'https://maibot-plugin-stats.maibot-webui.workers.dev'

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
  const searchParams = new URLSearchParams()
  if (params?.status) searchParams.set('status', params.status)
  if (params?.page) searchParams.set('page', params.page.toString())
  if (params?.page_size) searchParams.set('page_size', params.page_size.toString())
  if (params?.search) searchParams.set('search', params.search)
  if (params?.sort_by) searchParams.set('sort_by', params.sort_by)
  if (params?.sort_order) searchParams.set('sort_order', params.sort_order)
  
  const response = await fetch(`${PACK_SERVICE_URL}/pack?${searchParams.toString()}`)
  if (!response.ok) {
    throw new Error(`获取 Pack 列表失败: ${response.status}`)
  }
  return response.json()
}

/**
 * 获取单个 Pack 详情
 */
export async function getPack(packId: string): Promise<ModelPack> {
  const response = await fetch(`${PACK_SERVICE_URL}/pack/${packId}`)
  if (!response.ok) {
    throw new Error(`获取 Pack 失败: ${response.status}`)
  }
  const data = await response.json()
  if (!data.success) {
    throw new Error(data.error || '获取 Pack 失败')
  }
  return data.pack
}

/**
 * 创建新 Pack
 */
export async function createPack(pack: {
  name: string
  description: string
  author: string
  tags?: string[]
  providers: PackProvider[]
  models: PackModel[]
  task_config: PackTaskConfigs
}): Promise<{ pack_id: string; message: string }> {
  const response = await fetch(`${PACK_SERVICE_URL}/pack`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(pack),
  })
  
  const data = await response.json()
  if (!data.success) {
    throw new Error(data.error || '创建 Pack 失败')
  }
  return data
}

/**
 * 记录 Pack 下载
 */
export async function recordPackDownload(packId: string, userId?: string): Promise<void> {
  await fetch(`${PACK_SERVICE_URL}/pack/download`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ pack_id: packId, user_id: userId }),
  })
}

/**
 * 点赞/取消点赞 Pack
 */
export async function togglePackLike(packId: string, userId: string): Promise<{ likes: number; liked: boolean }> {
  const response = await fetch(`${PACK_SERVICE_URL}/pack/like`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ pack_id: packId, user_id: userId }),
  })
  
  const data = await response.json()
  if (!data.success) {
    throw new Error(data.error || '点赞失败')
  }
  return { likes: data.likes, liked: data.liked }
}

/**
 * 检查是否已点赞
 */
export async function checkPackLike(packId: string, userId: string): Promise<boolean> {
  const response = await fetch(
    `${PACK_SERVICE_URL}/pack/like/check?pack_id=${packId}&user_id=${userId}`
  )
  const data = await response.json()
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
  const response = await fetchWithAuth('/api/webui/config/model')
  if (!response.ok) {
    throw new Error('获取当前模型配置失败')
  }
  const responseData = await response.json()
  const currentConfig = responseData.config || responseData
  
  console.log('=== Pack Conflict Detection ===')
  console.log('Pack providers:', pack.providers)
  console.log('Local providers:', currentConfig.api_providers)
  
  const conflicts: ApplyPackConflicts = {
    existing_providers: [],
    new_providers: [],
    conflicting_models: [],
  }
  
  // 检测提供商冲突
  const localProviders = currentConfig.api_providers || []
  for (const packProvider of pack.providers) {
    console.log(`\nChecking pack provider: ${packProvider.name}`)
    console.log(`  Pack URL: ${packProvider.base_url}`)
    console.log(`  Normalized: ${normalizeUrl(packProvider.base_url)}`)
    
    // 按 URL 匹配 - 找出所有匹配的本地提供商
    const matchedProviders = localProviders.filter(
      (p: { base_url: string; name: string }) => {
        const localNormalized = normalizeUrl(p.base_url)
        const packNormalized = normalizeUrl(packProvider.base_url)
        console.log(`  Comparing with local "${p.name}": ${p.base_url}`)
        console.log(`    Local normalized: ${localNormalized}`)
        console.log(`    Match: ${localNormalized === packNormalized}`)
        return localNormalized === packNormalized
      }
    )
    
    if (matchedProviders.length > 0) {
      console.log(`  ✓ Matched with ${matchedProviders.length} local provider(s):`, matchedProviders.map((p: {name: string}) => p.name).join(', '))
      conflicts.existing_providers.push({
        pack_provider: packProvider,
        local_providers: matchedProviders.map((p: { name: string; base_url: string }) => ({
          name: p.name,
          base_url: p.base_url,
        })),
      })
    } else {
      console.log(`  ✗ No match found - will need API key`)
      conflicts.new_providers.push(packProvider)
    }
  }
  
  // 检测模型名称冲突
  const localModels = currentConfig.models || []
  console.log('\n=== Model Conflict Detection ===')
  for (const packModel of pack.models) {
    const conflictModel = localModels.find(
      (m: { name: string }) => m.name === packModel.name
    )
    if (conflictModel) {
      console.log(`Model conflict: ${packModel.name}`)
      conflicts.conflicting_models.push({
        pack_model: packModel.name,
        local_model: conflictModel.name,
      })
    }
  }
  
  console.log('\n=== Detection Summary ===')
  console.log(`Existing providers: ${conflicts.existing_providers.length}`)
  console.log(`New providers: ${conflicts.new_providers.length}`)
  console.log(`Conflicting models: ${conflicts.conflicting_models.length}`)
  console.log('===========================\n')
  
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
  const response = await fetchWithAuth('/api/webui/config/model')
  if (!response.ok) {
    throw new Error('获取当前模型配置失败')
  }
  const responseData = await response.json()
  const currentConfig = responseData.config || responseData
  
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
  
  // 保存配置
  const saveResponse = await fetchWithAuth('/api/webui/config/model', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(currentConfig),
  })
  
  if (!saveResponse.ok) {
    throw new Error('保存配置失败')
  }
}

/**
 * 从当前配置导出 Pack
 */
export async function exportCurrentConfigAsPack(params: {
  name: string
  description: string
  author: string
  tags?: string[]
  selectedProviders?: string[]
  selectedModels?: string[]
  selectedTasks?: string[]
}): Promise<{
  providers: PackProvider[]
  models: PackModel[]
  task_config: PackTaskConfigs
}> {
  // 获取当前配置
  const response = await fetchWithAuth('/api/webui/config/model')
  if (!response.ok) {
    throw new Error('获取当前模型配置失败')
  }
  const responseData = await response.json()
  
  // API 返回的格式是 { success: true, config: {...} }
  if (!responseData.success || !responseData.config) {
    throw new Error('获取配置失败')
  }
  
  const currentConfig = responseData.config
  
  // 过滤提供商（移除 api_key）
  let providers: PackProvider[] = (currentConfig.api_providers || []).map(
    (p: { name: string; base_url: string; client_type: string; max_retry?: number; timeout?: number; retry_interval?: number }) => ({
      name: p.name,
      base_url: p.base_url,
      client_type: p.client_type,
      max_retry: p.max_retry,
      timeout: p.timeout,
      retry_interval: p.retry_interval,
    })
  )
  
  if (params.selectedProviders) {
    providers = providers.filter(p => params.selectedProviders!.includes(p.name))
  }
  
  // 过滤模型
  let models: PackModel[] = currentConfig.models || []
  if (params.selectedModels) {
    models = models.filter(m => params.selectedModels!.includes(m.name))
  }
  
  // 过滤任务配置
  const task_config: PackTaskConfigs = {}
  const allTasks = currentConfig.model_task_config || {}
  const taskKeys = params.selectedTasks || Object.keys(allTasks)
  
  for (const key of taskKeys) {
    if (allTasks[key]) {
      task_config[key as keyof PackTaskConfigs] = allTasks[key]
    }
  }
  
  return { providers, models, task_config }
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
