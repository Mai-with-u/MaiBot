/**
 * API 提供商接口定义
 */
export interface APIProvider {
  name: string
  base_url: string
  api_key: string
  client_type: string
  max_retry: number | null
  timeout: number | null
  retry_interval: number | null
}

/**
 * 删除确认对话框状态
 */
export interface DeleteConfirmState {
  isOpen: boolean
  providersToDelete: string[]
  affectedModels: any[]
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
