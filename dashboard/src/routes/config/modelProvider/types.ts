import type { ModelInfo } from '@/routes/config/model/types'

/**
 * OpenAI 兼容接口的鉴权方式，与后端 OpenAICompatibleAuthType 枚举对齐
 * （src/config/model_configs.py）。`none` 表示端点无需鉴权，api_key 可留空。
 */
export type ProviderAuthType = 'bearer' | 'header' | 'query' | 'none'

/** 后端 APIProvider.auth_type 的默认值 */
export const DEFAULT_AUTH_TYPE: ProviderAuthType = 'bearer'

/**
 * API 提供商接口定义
 */
export interface APIProvider {
  name: string
  base_url: string
  api_key: string
  client_type: string
  auth_type?: string
  default_headers?: Record<string, string>
  max_retry?: number | null
  timeout?: number | null
  retry_interval?: number | null
}

/**
 * 删除确认对话框状态
 */
export interface DeleteConfirmState {
  isOpen: boolean
  providersToDelete: string[]
  affectedModels: ModelInfo[]
  pendingProviders: APIProvider[]
  context: 'auto' | 'manual' | 'restart'
  oldProviders: APIProvider[]
}

/**
 * 表单验证错误
 */
export interface FormErrors {
  name?: string
  base_url?: string
  api_key?: string
}
