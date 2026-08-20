/**
 * Model 配置页面类型定义
 */

/**
 * 模型信息
 */
export interface ModelInfo {
  model_identifier: string
  name: string
  api_provider: string
  price_in: number | null
  price_out: number | null
  cache?: boolean
  cache_price_in?: number | null
  temperature?: number | null // 模型级别温度，覆盖任务配置中的温度
  max_tokens?: number | null // 模型级别最大token数，覆盖任务配置中的max_tokens
  visual?: boolean
  force_stream_mode?: boolean
  extra_params?: Record<string, unknown>
}

/**
 * 提供商完整配置接口
 */
export interface ProviderConfig {
  name: string
  base_url: string
  api_key: string
  client_type: string
  max_retry?: number
  timeout?: number
  retry_interval?: number
}

/**
 * 单个任务配置
 */
export interface TaskConfig {
  model_list: string[]
  temperature?: number
  max_tokens?: number
  slow_threshold?: number
  selection_strategy?: string
}

/**
 * 所有模型任务配置（动态，由后端 schema 决定字段）
 */
export type ModelTaskConfig = Record<string, TaskConfig>

/**
 * 表单验证错误
 */
export interface FormErrors {
  name?: string
  api_provider?: string
  model_identifier?: string
}
