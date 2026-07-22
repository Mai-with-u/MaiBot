// 设置向导相关类型定义

export interface SetupStep {
  id: string
  title: string
  description: string
  icon: React.ComponentType<{ className?: string }>
}

// 步骤1：Bot基础信息
export interface BotBasicConfig {
  platform: string        // Primary platform name (normalized, lowercase)
  qq_account: string      // QQ account
  platforms: string[]     // Other platform accounts "platform:account"
  nickname: string
  alias_names: string[]
}

// 步骤2：人格配置
export interface PersonalityConfig {
  personality: string
  reply_style: string
  multiple_reply_style: string[]
  multiple_probability: number
}

// 步骤3：API 提供商配置
export interface ApiProviderSetupConfig {
  provider_name: string
  base_url: string
  api_key: string
}

// 步骤4：基础模型配置
export interface ModelSetupConfig {
  planner_model_name: string
  planner_model_identifier: string
  planner_visual: boolean
  planner_thinking: boolean
  replyer_model_name: string
  replyer_model_identifier: string
  replyer_visual: boolean
  replyer_thinking: boolean
}
