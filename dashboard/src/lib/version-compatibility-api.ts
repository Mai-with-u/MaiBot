import { getApiBaseUrl } from '@/lib/api-base'
import { APP_VERSION } from '@/lib/version'

export type VersionCompatibilityStatus =
  | 'compatible'
  | 'webui_outdated'
  | 'main_program_outdated'

export interface VersionCompatibilityResult {
  status: VersionCompatibilityStatus
  main_program_version: string
  webui_version: string
  required_webui_version: string
}

function isVersionCompatibilityResult(value: unknown): value is VersionCompatibilityResult {
  if (!value || typeof value !== 'object') {
    return false
  }

  const result = value as Record<string, unknown>
  return (
    ['compatible', 'webui_outdated', 'main_program_outdated'].includes(
      String(result.status)
    ) &&
    typeof result.main_program_version === 'string' &&
    typeof result.webui_version === 'string' &&
    typeof result.required_webui_version === 'string'
  )
}

/**
 * 该接口必须保持免登录：版本不匹配提示需要在认证页和首次配置页之前显示。
 * 接口不可用时只记录警告，不能在缺少版本数据的情况下推断任一端版本过低。
 */
export async function getVersionCompatibility(
  signal?: AbortSignal
): Promise<VersionCompatibilityResult> {
  const baseUrl = await getApiBaseUrl()
  const query = new URLSearchParams({ webui_version: APP_VERSION })
  const response = await fetch(
    `${baseUrl}/api/webui/version-compatibility?${query.toString()}`,
    {
      credentials: 'include',
      signal,
      cache: 'no-store',
    }
  )
  const contentType = response.headers.get('content-type')?.toLowerCase() ?? ''

  if (
    response.status === 404 ||
    response.status === 405 ||
    (response.ok && contentType.includes('text/html'))
  ) {
    throw new Error('当前主程序未提供版本兼容性检查接口')
  }
  if (!response.ok) {
    throw new Error(`版本兼容性检查失败（HTTP ${response.status}）`)
  }

  const result: unknown = await response.json()
  if (!isVersionCompatibilityResult(result)) {
    throw new Error('版本兼容性接口返回了无效数据')
  }

  return result
}
