// 设置向导API调用函数

import { fetchWithAuth, getAuthHeaders } from '@/lib/fetch-with-auth'
import type {
  BotBasicConfig,
  PersonalityConfig,
  EmojiConfig,
  OtherBasicConfig,
  SiliconFlowConfig,
} from './types'

// ===== 读取配置 =====

// 读取Bot基础配置
export async function loadBotBasicConfig(): Promise<BotBasicConfig> {
  const response = await fetchWithAuth('/api/webui/config/bot', {
    method: 'GET',
    headers: getAuthHeaders(),
  })

  if (!response.ok) {
    throw new Error('读取Bot配置失败')
  }

  const data = await response.json()
  const botConfig = data.config.bot || {}

  return {
    qq_account: botConfig.qq_account || 0,
    nickname: botConfig.nickname || '',
    alias_names: botConfig.alias_names || [],
  }
}

// 读取人格配置
export async function loadPersonalityConfig(): Promise<PersonalityConfig> {
  const response = await fetchWithAuth('/api/webui/config/bot', {
    method: 'GET',
    headers: getAuthHeaders(),
  })

  if (!response.ok) {
    throw new Error('读取人格配置失败')
  }

  const data = await response.json()
  const personalityConfig = data.config.personality || {}

  return {
    personality: personalityConfig.personality || '',
    reply_style: personalityConfig.reply_style || '',
    interest: personalityConfig.interest || '',
    plan_style: personalityConfig.plan_style || '',
    private_plan_style: personalityConfig.private_plan_style || '',
  }
}

// 读取表情包配置
export async function loadEmojiConfig(): Promise<EmojiConfig> {
  const response = await fetchWithAuth('/api/webui/config/bot', {
    method: 'GET',
    headers: getAuthHeaders(),
  })

  if (!response.ok) {
    throw new Error('读取表情包配置失败')
  }

  const data = await response.json()
  const emojiConfig = data.config.emoji || {}

  return {
    emoji_chance: emojiConfig.emoji_chance ?? 0.4,
    max_reg_num: emojiConfig.max_reg_num ?? 40,
    do_replace: emojiConfig.do_replace ?? true,
    check_interval: emojiConfig.check_interval ?? 10,
    steal_emoji: emojiConfig.steal_emoji ?? true,
    content_filtration: emojiConfig.content_filtration ?? false,
    filtration_prompt: emojiConfig.filtration_prompt || '',
  }
}

// 读取其他基础配置
export async function loadOtherBasicConfig(): Promise<OtherBasicConfig> {
  const response = await fetchWithAuth('/api/webui/config/bot', {
    method: 'GET',
    headers: getAuthHeaders(),
  })

  if (!response.ok) {
    throw new Error('读取其他配置失败')
  }

  const data = await response.json()
  const config = data.config

  const toolConfig = config.tool || {}
  const expressionConfig = config.expression || {}

  return {
    enable_tool: toolConfig.enable_tool ?? true,
    all_global: expressionConfig.all_global_jargon ?? true,
  }
}

// 读取硅基流动API配置
export async function loadSiliconFlowConfig(): Promise<SiliconFlowConfig> {
  const response = await fetchWithAuth('/api/webui/config/model', {
    method: 'GET',
    headers: getAuthHeaders(),
  })

  if (!response.ok) {
    throw new Error('读取模型配置失败')
  }

  const data = await response.json()
  const modelConfig = data.config

  // 获取SiliconFlow提供商的API Key
  const apiProviders = modelConfig.api_providers || []
  const siliconFlowProvider = apiProviders.find(
    (p: Record<string, unknown>) => p.name === 'SiliconFlow'
  )

  return {
    api_key: siliconFlowProvider?.api_key || '',
  }
}

// ===== 保存配置 =====

// 保存Bot基础配置
export async function saveBotBasicConfig(config: BotBasicConfig) {
  const response = await fetchWithAuth('/api/webui/config/bot/section/bot', {
    method: 'POST',
    headers: getAuthHeaders(),
    body: JSON.stringify(config),
  })

  if (!response.ok) {
    const error = await response.json()
    throw new Error(error.detail || '保存Bot基础配置失败')
  }

  return await response.json()
}

// 保存人格配置
export async function savePersonalityConfig(config: PersonalityConfig) {
  const response = await fetchWithAuth('/api/webui/config/bot/section/personality', {
    method: 'POST',
    headers: getAuthHeaders(),
    body: JSON.stringify(config),
  })

  if (!response.ok) {
    const error = await response.json()
    throw new Error(error.detail || '保存人格配置失败')
  }

  return await response.json()
}

// 保存表情包配置
export async function saveEmojiConfig(config: EmojiConfig) {
  const response = await fetchWithAuth('/api/webui/config/bot/section/emoji', {
    method: 'POST',
    headers: getAuthHeaders(),
    body: JSON.stringify(config),
  })

  if (!response.ok) {
    const error = await response.json()
    throw new Error(error.detail || '保存表情包配置失败')
  }

  return await response.json()
}

// 保存其他基础配置（工具、情绪、黑话）
export async function saveOtherBasicConfig(config: OtherBasicConfig) {
  // 需要分别保存到不同的section
  const promises = []

  // 保存tool配置
  promises.push(
    fetchWithAuth('/api/webui/config/bot/section/tool', {
      method: 'POST',
      headers: getAuthHeaders(),
      body: JSON.stringify({ enable_tool: config.enable_tool }),
    })
  )

  // 保存expression配置中的all_global_jargon
  promises.push(
    fetchWithAuth('/api/webui/config/bot/section/expression', {
      method: 'POST',
      headers: getAuthHeaders(),
      body: JSON.stringify({ all_global_jargon: config.all_global }),
    })
  )

  const results = await Promise.all(promises)

  // 检查所有请求是否成功
  for (const response of results) {
    if (!response.ok) {
      const error = await response.json()
      throw new Error(error.detail || '保存其他配置失败')
    }
  }

  return { success: true }
}

// 保存硅基流动API配置
export async function saveSiliconFlowConfig(config: SiliconFlowConfig) {
  // 1. 读取现有配置
  const response = await fetchWithAuth('/api/webui/config/model', {
    method: 'GET',
    headers: getAuthHeaders(),
  })

  if (!response.ok) {
    throw new Error('读取模型配置失败')
  }

  const currentModelConfig = await response.json()
  const modelConfig = currentModelConfig.config

  // 2. 更新SiliconFlow提供商的API Key
  const apiProviders = modelConfig.api_providers || []
  const siliconFlowIndex = apiProviders.findIndex(
    (p: Record<string, unknown>) => p.name === 'SiliconFlow'
  )

  if (siliconFlowIndex >= 0) {
    // 更新现有提供商的API Key
    apiProviders[siliconFlowIndex] = {
      ...apiProviders[siliconFlowIndex],
      api_key: config.api_key,
    }
  } else {
    // 如果不存在,创建新的SiliconFlow提供商
    apiProviders.push({
      name: 'SiliconFlow',
      base_url: 'https://api.siliconflow.cn/v1',
      api_key: config.api_key,
      client_type: 'openai',
      max_retry: 3,
      timeout: 120,
      retry_interval: 5,
    })
  }

  // 3. 保存更新后的配置
  const updatedConfig = {
    ...modelConfig,
    api_providers: apiProviders,
  }

  const saveResponse = await fetchWithAuth('/api/webui/config/model', {
    method: 'POST',
    headers: getAuthHeaders(),
    body: JSON.stringify(updatedConfig),
  })

  if (!saveResponse.ok) {
    const error = await saveResponse.json()
    throw new Error(error.detail || '保存模型配置失败')
  }

  return await saveResponse.json()
}

// 标记设置完成
export async function completeSetup() {
  const response = await fetchWithAuth('/api/webui/setup/complete', {
    method: 'POST',
  })

  if (!response.ok) {
    const error = await response.json()
    throw new Error(error.message || '标记配置完成失败')
  }

  return await response.json()
}
