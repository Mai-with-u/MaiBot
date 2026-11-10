// 设置向导API调用函数

import i18n from '@/i18n'
import { authApi, backendApi } from '@/lib/http'
import { testModelCapability } from '@/lib/config-api'
import {
  findTemplateByBaseUrl,
  PROVIDER_TEMPLATES,
  resolveModelFetcherTemplate,
  resolveThinkingFormatForModel,
} from '@/routes/config/providerTemplates'
import type { ProviderTemplate } from '@/routes/config/providerTemplates'
import { setThinkingEnabled } from '@/routes/config/model/thinkingFormats'
import { getDeepSeekReasoningEffort, setDeepSeekReasoningEffort, setDeepSeekThinkingEnabled } from '@/routes/config/model/deepSeekExtraParams'
import type { ModelListItem, ModelTestResult } from '@/lib/config-api'
import type { ModelPricePeriod } from '@/routes/config/model/types'

import type {
  ApiProviderSetupConfig,
  BasicSetupConfig,
  BotBasicConfig,
  ModelSetupConfig,
  PersonalityConfig,
  SetupModelRole,
} from './types'

interface ModelInfo {
  model_type: 'llm' | 'embedding' | 'image_embedding'
  model_identifier: string
  name: string
  api_provider: string
  price_in?: number
  cache_price_in?: number
  price_periods?: ModelPricePeriod[]
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
  thinkingEnabled: boolean,
  modelIdentifier: string,
  template: ProviderTemplate | null,
  clientType: string,
  effort: 'low' | 'high' | 'max' = 'high'
): Record<string, unknown> {
  const extraParams = { ...(existingParams || {}) }
  delete extraParams.thinking
  delete extraParams.enable_thinking
  delete extraParams.reasoning_effort
  if (template?.id === 'deepseek' && (clientType === 'openai' || clientType === 'openai_responses')) {
    const params = setDeepSeekThinkingEnabled(extraParams, clientType, thinkingEnabled)
    return thinkingEnabled ? setDeepSeekReasoningEffort(params, clientType, effort) : params
  }
  const format = resolveThinkingFormatForModel(template, modelIdentifier)
  if (!format) return extraParams
  // 不可关闭思考的模型遵循模板的启用值，不使用向导中隐藏的开关状态。
  const canDisable = format.kind === 'enable_thinking' ||
    (format.kind === 'thinking_type' && format.canDisable === true)
  return setThinkingEnabled(extraParams, format, canDisable ? thinkingEnabled : true)
}

// ===== 读取配置 =====

export async function loadSetupStatus(): Promise<SetupStatus> {
  return authApi.get<SetupStatus>('/api/webui/setup/status', {
    errorMessage: i18n.t('setupPage.errors.loadStatus'),
  })
}

type ReplyTimingSetupConfig = Pick<BasicSetupConfig, 'inevitable_at_reply' | 'private_reply_paused'>

export async function loadBasicSetupConfig(): Promise<BasicSetupConfig> {
  const { config } = await backendApi.get<{ config: {
    chat: { reply_timing: ReplyTimingSetupConfig }
    desktop_pet: { enabled: boolean }
  } }>(
    '/api/webui/config/bot',
    { errorMessage: i18n.t('setupPage.errors.loadBot') }
  )
  const { inevitable_at_reply, private_reply_paused } = config.chat.reply_timing
  return { inevitable_at_reply, private_reply_paused, desktop_pet_enabled: config.desktop_pet.enabled }
}

export async function saveBasicSetupConfig(config: Partial<ReplyTimingSetupConfig>) {
  return backendApi.post('/api/webui/config/bot/section/chat', {
    body: { reply_timing: config },
    errorMessage: i18n.t('setupPage.toast.saveFailedTitle'),
  })
}

export async function saveDesktopPetEnabled(enabled: boolean) {
  return backendApi.post('/api/webui/config/bot/section/desktop_pet', {
    body: { enabled },
    errorMessage: i18n.t('setupPage.toast.saveFailedTitle'),
  })
}

// 读取Bot基础配置
export async function loadBotBasicConfig(): Promise<BotBasicConfig> {
  const data = await backendApi.get<{ config: { bot?: BotBasicConfig } }>('/api/webui/config/bot', {
    errorMessage: i18n.t('setupPage.errors.loadBot'),
  })
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
    { errorMessage: i18n.t('setupPage.errors.loadPersonality') }
  )
  const personalityConfig = (data.config.personality || {}) as Partial<PersonalityConfig>

  return {
    personality: personalityConfig.personality || '',
    behavior_style: personalityConfig.behavior_style ?? '',
    reply_style: personalityConfig.reply_style || '',
    multiple_reply_style: personalityConfig.multiple_reply_style || [],
    multiple_probability: personalityConfig.multiple_probability ?? 0.2,
  }
}

async function loadModelConfig(): Promise<ModelConfig> {
  const data = await backendApi.get<{ config: ModelConfig }>('/api/webui/config/model', {
    errorMessage: i18n.t('setupPage.errors.loadModel'),
  })
  return data.config || {}
}

// API 提供商在向导中始终从内置预设开始，避免回显历史自定义服务商配置。
export async function loadApiProviderSetupConfig(): Promise<ApiProviderSetupConfig> {
  return {
    provider_name: DEFAULT_API_PROVIDER_TEMPLATE?.name || '',
    base_url: DEFAULT_API_PROVIDER_TEMPLATE?.base_url || '',
    api_key: '',
  }
}

// 使用尚未保存的提供商配置获取模型，避免输入 Key 时写入配置文件。
export async function fetchSetupModels(config: ApiProviderSetupConfig): Promise<ModelListItem[]> {
  const template = resolveModelFetcherTemplate(config.base_url)
  const response = await backendApi.get<{ models: ModelListItem[] }>('/api/webui/models/list-by-url', {
    query: {
      base_url: config.base_url.trim(),
      api_key: config.api_key.trim(),
      client_type: template?.client_type ?? 'openai',
      parser: template?.modelFetcher?.parser ?? 'openai',
      endpoint: template?.modelFetcher?.endpoint ?? '/models',
    },
    errorMessage: i18n.t('setupPage.errors.fetchModels'),
  })
  if (response.models.length === 0) throw new Error(i18n.t('setupPage.errors.emptyModelList'))
  return response.models
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
    planner_reasoning_effort: getDeepSeekReasoningEffort(plannerModel?.extra_params ?? {}, 'openai'),
    planner_thinking: readThinkingEnabled(plannerModel, plannerName),
    replyer_model_name: replyerName,
    replyer_model_identifier: replyerModel?.model_identifier || replyerName,
    replyer_visual: Boolean(replyerModel?.visual),
    replyer_reasoning_effort: getDeepSeekReasoningEffort(replyerModel?.extra_params ?? {}, 'openai'),
    replyer_thinking: readThinkingEnabled(replyerModel, replyerName),
  }
}

// ===== 保存配置 =====

// 保存Bot基础配置
export async function saveBotBasicConfig(config: BotBasicConfig) {
  return backendApi.post('/api/webui/config/bot/section/bot', {
    body: config,
    errorMessage: i18n.t('setupPage.errors.saveBot'),
  })
}

// 保存人格配置
export async function savePersonalityConfig(config: PersonalityConfig) {
  return backendApi.post('/api/webui/config/bot/section/personality', {
    body: config,
    errorMessage: i18n.t('setupPage.errors.savePersonality'),
  })
}

function createBasicModel(
  modelName: string,
  modelIdentifier: string,
  providerName: string,
  visual: boolean,
  thinking: boolean,
  template: ProviderTemplate | null,
  clientType: string,
  existing?: ModelInfo,
  effort: 'low' | 'high' | 'max' = 'high'
): ModelInfo {
  return {
    price_in: 0,
    cache_price_in: 0,
    price_out: 0,
    force_stream_mode: false,
    ...existing,
    extra_params: buildThinkingExtraParams(existing?.extra_params, thinking, modelIdentifier, template, clientType, effort),
    visual,
    model_identifier: modelIdentifier,
    model_type: 'llm',
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
    client_type: findTemplateByBaseUrl(config.base_url)?.client_type ?? 'openai',
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
    errorMessage: i18n.t('setupPage.errors.saveProvider'),
  })
}

// 向导写入 model_config.toml 时使用的模型名称。
// 同一模型用于两个角色时分别保存，避免思考开关和强度互相覆盖。
export function resolveSetupModelNames(config: ModelSetupConfig): { planner: string; replyer: string } {
  const plannerIdentifier = config.planner_model_identifier.trim()
  const replyerIdentifier = config.replyer_model_identifier.trim()
  return {
    planner: plannerIdentifier === replyerIdentifier ? `${plannerIdentifier}-planner` : plannerIdentifier,
    replyer: replyerIdentifier,
  }
}

// 复用模型配置页的单模型测试；模型须已写入配置文件。
export async function testSetupModel(modelName: string): Promise<ModelTestResult> {
  return testModelCapability(modelName)
}

// 保存基础模型配置
export async function saveModelSetupConfig(config: ModelSetupConfig, providerName: string, testRole?: SetupModelRole) {
  const modelConfig = await loadModelConfig()
  const trimmedProviderName = providerName.trim()
  const provider = modelConfig.api_providers?.find((item) => item.name === trimmedProviderName)
  const template = findTemplateByBaseUrl(provider?.base_url ?? '')
  const clientType = provider?.client_type ?? 'openai'
  const names = resolveSetupModelNames(config)
  const modelTaskConfig = modelConfig.model_task_config || {}
  const otherRoles = testRole === 'replyer' ? ['planner', 'utils'] : ['replyer']
  if (testRole) {
    const reserved = new Set(otherRoles.flatMap((role) => modelTaskConfig[role]?.model_list ?? []))
    const name = names[testRole]
    for (let suffix = 1; reserved.has(names[testRole]); suffix += 1) {
      names[testRole] = `${name}-${testRole}${suffix > 1 ? `-${suffix}` : ''}`
    }
  }

  // 测试只写入当前模型，正式保存时才更新任务分配。
  let models = modelConfig.models || []
  for (const role of testRole ? [testRole] : (['planner', 'replyer'] as const)) {
    const name = names[role]
    models = upsertModel(models, createBasicModel(
      name,
      config[`${role}_model_identifier`].trim(),
      trimmedProviderName,
      config[`${role}_visual`],
      config[`${role}_thinking`],
      template,
      clientType,
      models.find((model) => model.name === name),
      config[`${role}_reasoning_effort`]
    ))
  }

  const updatedTaskConfig = testRole ? modelConfig.model_task_config : {
    ...modelTaskConfig,
    planner: {
      ...(modelTaskConfig.planner || {}),
      model_list: [names.planner],
    },
    replyer: {
      ...(modelTaskConfig.replyer || {}),
      model_list: [names.replyer],
    },
    utils: {
      ...(modelTaskConfig.utils || {}),
      model_list: [names.planner],
    },
  }

  // vlm/voice/embedding 等其他任务配置保持原样。
  const updatedConfig = {
    ...modelConfig,
    models,
    model_task_config: updatedTaskConfig,
  }

  await backendApi.post('/api/webui/config/model', {
    body: updatedConfig,
    errorMessage: i18n.t('setupPage.errors.saveModel'),
  })
  return names
}

// 标记设置完成
export async function completeSetup() {
  return authApi.post('/api/webui/setup/complete', {
    errorMessage: i18n.t('setupPage.errors.complete'),
  })
}

export async function updateAccessToken(newToken: string): Promise<TokenUpdateResult> {
  return authApi.post<TokenUpdateResult>('/api/webui/auth/update', {
    body: { new_token: newToken },
    errorMessage: i18n.t('setupPage.errors.updateToken'),
  })
}
