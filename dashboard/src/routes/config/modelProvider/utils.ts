import type { APIProvider } from './types'

/**
 * 清理后的 provider：可空数值字段已归一为 undefined，缺省字段不会向下发
 * （由后端 schema 默认值填充，避免前后端各写一套默认值导致分叉）
 */
export type CleanedProvider = Omit<APIProvider, 'max_retry' | 'timeout' | 'retry_interval'> & {
  max_retry?: number
  timeout?: number
  retry_interval?: number
}

/**
 * 清理 provider 数据：数值字段缺省时不向下发，由后端 schema 默认值填充
 * 避免前后端各写一套默认值导致分叉；null 转为 undefined 后会在序列化时丢弃，防止后端校验报错
 */
export const cleanProviderData = (provider: APIProvider): CleanedProvider => ({
  ...provider,
  default_headers: provider.default_headers ?? {},
  max_retry: provider.max_retry ?? undefined,
  timeout: provider.timeout ?? undefined,
  retry_interval: provider.retry_interval ?? undefined,
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
