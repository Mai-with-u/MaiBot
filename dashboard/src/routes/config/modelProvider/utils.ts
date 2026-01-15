import type { APIProvider } from './types'

/**
 * 清理 provider 数据，填充默认值
 * 用于确保所有数值字段都有有效值，避免 null 导致的后端验证错误
 */
export const cleanProviderData = (provider: APIProvider): APIProvider => ({
  ...provider,
  max_retry: provider.max_retry ?? 2,
  timeout: provider.timeout ?? 30,
  retry_interval: provider.retry_interval ?? 10,
})

/**
 * 验证提供商表单数据
 * @param provider 当前编辑的提供商
 * @param existingProviders 现有提供商列表
 * @param editingIndex 当前编辑的索引（新增时为 null）
 */
export const validateProvider = (
  provider: APIProvider | null,
  existingProviders: APIProvider[] = [],
  editingIndex: number | null = null
): {
  isValid: boolean
  errors: { name?: string; base_url?: string; api_key?: string }
} => {
  const errors: { name?: string; base_url?: string; api_key?: string } = {}
  
  if (!provider) {
    return { isValid: false, errors: { name: '提供商数据为空' } }
  }

  if (!provider.name?.trim()) {
    errors.name = '请输入提供商名称'
  } else {
    // 检查名称是否与现有提供商重复
    const isDuplicate = existingProviders.some((p, index) => {
      // 编辑时排除自身
      if (editingIndex !== null && index === editingIndex) {
        return false
      }
      return p.name.trim().toLowerCase() === provider.name.trim().toLowerCase()
    })
    if (isDuplicate) {
      errors.name = '提供商名称已存在，请使用其他名称'
    }
  }
  
  if (!provider.base_url?.trim()) {
    errors.base_url = '请输入基础 URL'
  }
  if (!provider.api_key?.trim()) {
    errors.api_key = '请输入 API Key'
  }

  return {
    isValid: Object.keys(errors).length === 0,
    errors,
  }
}
