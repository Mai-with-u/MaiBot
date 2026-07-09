// 设置向导API调用函数

import { authApi, backendApi } from '@/lib/http'
import { PROVIDER_TEMPLATES } from '@/routes/config/providerTemplates'

import type {
  ApiProviderSetupConfig,
  BotBasicConfig,
  ModelSetupConfig,
  PersonalityConfig,
} from './types'

interface ModelInfo {
  model_identifier: string
  name: string
  api_provider: string
  price_in?: number
  cache?: boolean
  cache_price_in?: number
  price_out?: number
  force_stream_mode?: boolean
  visual?: boolean
  extra_params?: Record<string, unknown>
}

interface ApiProviderConfig {
  name: string
  base_url: string
  api_key: string
  client_type?: string
  max_retry?: number
  timeout?: number
  retry_interval?: number
}

interface TaskConfig {
  model_list?: string[]
  max_tokens?: number
  temperature?: number
  slow_threshold?: number
  selection_strategy?: string
}

interface ModelConfig {
  models?: ModelInfo[]
  api_providers?: ApiProviderConfig[]
  model_task_config?: Record<string, TaskConfig>
}

export interface SetupStatus {
  is_first_setup: boolean
  token_source: string
  requires_custom_token: boolean
  message?: string
}

export interface TokenUpdateResult {
  success: boolean
  message: string
}

const DEFAULT_API_PROVIDER_TEMPLATE = PROVIDER_TEMPLATES.find(
  (template) => template.id === 'deepseek'
)

function inferThinkingEnabled(modelIdentifier: string): boolean {
  return modelIdentifier.trim().toLowerCase().includes('deepseek-v4-pro')
}

function readThinkingEnabled(model?: ModelInfo, fallbackIdentifier = ''): boolean {
  const thinking = model?.extra_params?.thinking
  if (typeof thinking === 'object' && thinking !== null && !Array.isArray(thinking)) {
    return (thinking as Record<string, unknown>).type === 'enabled'
  }

  const legacyThinking = model?.extra_params?.enable_thinking
  if (typeof legacyThinking === 'boolean') return legacyThinking
  if (typeof legacyThinking === 'string') return legacyThinking.toLowerCase() === 'true'

  return inferThinkingEnabled(model?.model_identifier || fallbackIdentifier)
}

function buildThinkingExtraParams(
  existingParams: Record<string, unknown> | undefined,
  thinkingEnabled: boolean
): Record<string, unknown> {
  const extraParams = { ...(existingParams || {}) }
  delete extraParams.enable_thinking
  extraParams.thinking = { type: thinkingEnabled ? 'enabled' : 'disabled' }

  if (thinkingEnabled) {
    extraParams.reasoning_effort = 'high'
  } else {
    delete extraParams.reasoning_effort
  }

  return extraParams
}

// ===== 读取配置 =====

export async function loadSetupStatus(): Promise<SetupStatus> {
  return authApi.get<SetupStatus>('/api/webui/setup/status', {
    errorMessage: '读取设置状态失败',
  })
}

// 读取Bot基础配置
export async function loadBotBasicConfig(): Promise<BotBasicConfig> {
  const data = await backendApi.get<{ config: { bot?: BotBasicConfig } }>(
    '/api/webui/config/bot',
    { errorMessage: '读取 Bot 配置失败' }
  )
  const botConfig = (data.config.bot || {}) as Partial<BotBasicConfig>
  const qqAccount = String(botConfig.qq_account ?? '').trim()

  return {
    platform: botConfig.platform || (qqAccount ? 'qq' : ''),
    qq_account: qqAccount,
    platforms: botConfig.platforms || [],
    nickname: botConfig.nickname || '',
    alias_names: botConfig.alias_names || [],
  }
}

// 读取人格配置
export async function loadPersonalityConfig(): Promise<PersonalityConfig> {
  const data = await backendApi.get<{ config: { personality?: PersonalityConfig } }>(
    '/api/webui/config/bot',
    { errorMessage: '读取人格配置失败' }
  )
  const personalityConfig = (data.config.personality || {}) as Partial<PersonalityConfig>

  return {
    personality: personalityConfig.personality || '',
    reply_style: personalityConfig.reply_style || '',
    multiple_reply_style: personalityConfig.multiple_reply_style || [],
    multiple_probability: personalityConfig.multiple_probability ?? 0.2,
  }
}

async function loadModelConfig(): Promise<ModelConfig> {
  const data = await backendApi.get<{ config: ModelConfig }>('/api/webui/config/model', {
    errorMessage: '读取模型配置失败',
  })
  return data.config || {}
}

// 读取 API 提供商配置
export async function loadApiProviderSetupConfig(): Promise<ApiProviderSetupConfig> {
  const modelConfig = await loadModelConfig()
  const models = modelConfig.models || []
  const taskConfig = modelConfig.model_task_config || {}
  const plannerName = taskConfig.planner?.model_list?.[0] || ''
  const replyerName = taskConfig.replyer?.model_list?.[0] || ''
  const plannerModel = models.find((model) => model.name === plannerName)
  const replyerModel = models.find((model) => model.name === replyerName)
  const providerName =
    plannerModel?.api_provider ||
    replyerModel?.api_provider ||
    modelConfig.api_providers?.[0]?.name ||
    ''
  const provider = modelConfig.api_providers?.find((item) => item.name === providerName)

  return {
    provider_name: providerName || DEFAULT_API_PROVIDER_TEMPLATE?.name || '',
    base_url: provider?.base_url || DEFAULT_API_PROVIDER_TEMPLATE?.base_url || '',
    api_key: '',
  }
}

// 读取基础模型配置
export async function loadModelSetupConfig(): Promise<ModelSetupConfig> {
  const modelConfig = await loadModelConfig()
  const models = modelConfig.models || []
  const taskConfig = modelConfig.model_task_config || {}
  const plannerName = taskConfig.planner?.model_list?.[0] || ''
  const replyerName = taskConfig.replyer?.model_list?.[0] || ''
  const plannerModel = models.find((model) => model.name === plannerName)
  const replyerModel = models.find((model) => model.name === replyerName)

  return {
    planner_model_name: plannerName,
    planner_model_identifier: plannerModel?.model_identifier || plannerName,
    planner_visual: Boolean(plannerModel?.visual),
    planner_thinking: readThinkingEnabled(plannerModel, plannerName),
    replyer_model_name: replyerName,
    replyer_model_identifier: replyerModel?.model_identifier || replyerName,
    replyer_visual: Boolean(replyerModel?.visual),
    replyer_thinking: readThinkingEnabled(replyerModel, replyerName),
  }
}

// ===== 保存配置 =====

// 保存Bot基础配置
export async function saveBotBasicConfig(config: BotBasicConfig) {
  return backendApi.post('/api/webui/config/bot/section/bot', {
    body: config,
    errorMessage: '保存 Bot 配置失败',
  })
}

// 保存人格配置
export async function savePersonalityConfig(config: PersonalityConfig) {
  return backendApi.post('/api/webui/config/bot/section/personality', {
    body: config,
    errorMessage: '保存人格配置失败',
  })
}

function createBasicModel(
  modelName: string,
  modelIdentifier: string,
  providerName: string,
  visual: boolean,
  thinking: boolean,
  existing?: ModelInfo
): ModelInfo {
  return {
    price_in: 0,
    cache: false,
    cache_price_in: 0,
    price_out: 0,
    force_stream_mode: false,
    ...existing,
    extra_params: buildThinkingExtraParams(existing?.extra_params, thinking),
    visual,
    model_identifier: modelIdentifier,
    name: modelName,
    api_provider: providerName,
  }
}

function upsertModel(models: ModelInfo[], model: ModelInfo): ModelInfo[] {
  const index = models.findIndex((item) => item.name === model.name)
  if (index >= 0) {
    return models.map((item, itemIndex) => (itemIndex === index ? model : item))
  }
  return [...models, model]
}

// 保存 API 提供商配置
export async function saveApiProviderSetupConfig(config: ApiProviderSetupConfig) {
  const modelConfig = await loadModelConfig()
  const providerName = config.provider_name.trim()

  const apiProviders = modelConfig.api_providers || []
  const providerIndex = apiProviders.findIndex((provider) => provider.name === providerName)
  const providerConfig: ApiProviderConfig = {
    name: providerName,
    base_url: config.base_url.trim(),
    api_key: config.api_key.trim(),
    client_type: 'openai',
    max_retry: 3,
    timeout: 120,
    retry_interval: 5,
  }

  if (providerIndex >= 0) {
    apiProviders[providerIndex] = {
      ...apiProviders[providerIndex],
      ...providerConfig,
    }
  } else {
    apiProviders.push(providerConfig)
  }

  const updatedConfig = {
    ...modelConfig,
    api_providers: apiProviders,
  }

  return backendApi.post('/api/webui/config/model', {
    body: updatedConfig,
    errorMessage: '保存 API 提供商配置失败',
  })
}

// 保存基础模型配置
export async function saveModelSetupConfig(
  config: ModelSetupConfig,
  providerName: string
) {
  const modelConfig = await loadModelConfig()
  const trimmedProviderName = providerName.trim()
  const plannerModelIdentifier = config.planner_model_identifier.trim()
  const plannerModelName = plannerModelIdentifier
  const replyerModelIdentifier = config.replyer_model_identifier.trim()
  const replyerModelName = replyerModelIdentifier

  // 新增或更新 planner/replyer 模型，并仅同步 utils 到 planner。
  let models = modelConfig.models || []
  const existingPlannerModel = models.find((model) => model.name === plannerModelName)
  const existingReplyerModel = models.find((model) => model.name === replyerModelName)
  models = upsertModel(
    models,
    createBasicModel(
      plannerModelName,
      plannerModelIdentifier,
      trimmedProviderName,
      config.planner_visual,
      config.planner_thinking,
      existingPlannerModel
    )
  )
  models = upsertModel(
    models,
    createBasicModel(
      replyerModelName,
      replyerModelIdentifier,
      trimmedProviderName,
      config.replyer_visual,
      config.replyer_thinking,
      existingReplyerModel
    )
  )

  const modelTaskConfig = modelConfig.model_task_config || {}
  const updatedTaskConfig = {
    ...modelTaskConfig,
    planner: {
      ...(modelTaskConfig.planner || {}),
      model_list: [plannerModelName],
    },
    replyer: {
      ...(modelTaskConfig.replyer || {}),
      model_list: [replyerModelName],
    },
    utils: {
      ...(modelTaskConfig.utils || {}),
      model_list: [plannerModelName],
    },
  }

  // vlm/voice/embedding 等其他任务配置保持原样。
  const updatedConfig = {
    ...modelConfig,
    models,
    model_task_config: updatedTaskConfig,
  }

  return backendApi.post('/api/webui/config/model', {
    body: updatedConfig,
    errorMessage: '保存模型配置失败',
  })
}

// 标记设置完成
export async function completeSetup() {
  return backendApi.post('/api/webui/setup/complete', {
    errorMessage: '标记设置完成失败',
  })
}

export async function updateAccessToken(newToken: string): Promise<TokenUpdateResult> {
  return authApi.post<TokenUpdateResult>('/api/webui/auth/update', {
    body: { new_token: newToken },
    errorMessage: '更新 Token 失败',
  })
}
