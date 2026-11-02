import { DEFAULT_AUTH_TYPE, type APIProvider } from './types'

/** 鉴权方式选项，值与后端 OpenAICompatibleAuthType 枚举一一对应 */
export const AUTH_TYPE_OPTIONS: Array<{ value: string; label: string }> = [
  { value: 'bearer', label: 'Bearer Token' },
  { value: 'header', label: '自定义请求头' },
  { value: 'query', label: '查询参数' },
  { value: 'none', label: '无鉴权' },
]

const AUTH_TYPE_VALUES = new Set(AUTH_TYPE_OPTIONS.map((option) => option.value))

/**
 * 归一提供商的鉴权方式：缺失或不在后端枚举内的值按后端默认 `bearer` 处理
 * （存量配置没有 auth_type 字段，语义上等同于 bearer）
 */
export const resolveAuthType = (provider: Pick<APIProvider, 'auth_type'>): string =>
  provider.auth_type && AUTH_TYPE_VALUES.has(provider.auth_type)
    ? provider.auth_type
    : DEFAULT_AUTH_TYPE

/** 该提供商是否无需鉴权（auth_type=none 时 api_key 可留空） */
export const isNoAuthProvider = (provider: Pick<APIProvider, 'auth_type'>): boolean =>
  resolveAuthType(provider) === 'none'

/**
 * 清理后的 provider：可空数值字段已归一为 undefined，缺省字段不会向下发
 * （由后端 schema 默认值填充，避免前后端各写一套默认值导致分叉）
 */
export type CleanedProvider = Omit<APIProvider, 'max_retry' | 'timeout' | 'retry_interval'> & {
  auth_type: string
  max_retry?: number
  timeout?: number
  retry_interval?: number
}

/**
 * 清理 provider 数据：数值字段缺省时不向下发，由后端 schema 默认值填充
 * 避免前后端各写一套默认值导致分叉；null 转为 undefined 后会在序列化时丢弃，防止后端校验报错
 * auth_type 例外：必须显式下发归一后的值，否则后端会按默认 bearer 校验 api_key，
 * 无鉴权端点（auth_type=none）将无法保存
 */
export const cleanProviderData = (provider: APIProvider): CleanedProvider => ({
  ...provider,
  auth_type: resolveAuthType(provider),
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
  // 与后端 APIProvider.model_post_init 对齐：仅 auth_type=none 时 api_key 可留空
  if (!isNoAuthProvider(provider) && !provider.api_key?.trim()) {
    errors.api_key = '请输入 API Key'
  }

  return {
    isValid: Object.keys(errors).length === 0,
    errors,
  }
}
