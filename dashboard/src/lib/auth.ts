/**
 * 认证流程工具：登出与认证状态探测。
 *
 * 走 authApi 实例（携带 Cookie 但 401 不跳转）——
 * 在这两个场景里 401 / 未认证是正常业务结果，不应触发整页跳转。
 */
import { ApiError, authApi } from '@/lib/http'

export interface AuthStatus {
  authenticated: boolean
  token_source?: string
  requires_custom_token?: boolean
}

export interface SetupStatus {
  is_first_setup: boolean
  token_source: string
  requires_custom_token: boolean
  message?: string
}

/**
 * 使用 AbortController 同时处理超时和外部取消，兼容缺少 AbortSignal.any 的浏览器。
 */
async function withAuthTimeout<T>(
  request: (signal: AbortSignal) => Promise<T>,
  signal?: AbortSignal,
): Promise<T> {
  const controller = new AbortController()
  const abort = () => controller.abort(signal?.reason)
  if (signal?.aborted) {
    abort()
  } else {
    signal?.addEventListener('abort', abort, { once: true })
  }
  const timeout = setTimeout(() => {
    controller.abort(new DOMException('认证请求超时', 'TimeoutError'))
  }, 10_000)
  try {
    return await request(controller.signal)
  } finally {
    clearTimeout(timeout)
    signal?.removeEventListener('abort', abort)
  }
}

/**
 * 调用登出接口并跳转到登录页
 */
export async function logout(): Promise<void> {
  try {
    await authApi.post('/api/webui/auth/logout', { parse: 'response' })
  } catch (error) {
    console.error('登出请求失败:', error)
  }
  // 无论成功与否都跳转到登录页
  window.location.href = '/auth'
}

/**
 * 检查当前认证状态
 */
export async function checkAuthStatus(signal?: AbortSignal): Promise<boolean> {
  return (await getAuthStatus(signal)).authenticated
}

/**
 * 获取当前认证状态和 Token 来源
 */
export async function getAuthStatus(signal?: AbortSignal): Promise<AuthStatus> {
  try {
    const data = await withAuthTimeout(
      (requestSignal) => authApi.get<AuthStatus>('/api/webui/auth/check', {
        cache: 'no-store',
        signal: requestSignal,
      }),
      signal,
    )
    if (typeof data?.authenticated !== 'boolean') {
      throw new Error('认证接口返回了无效的登录状态')
    }
    return {
      authenticated: data.authenticated === true,
      token_source: data.token_source,
      requires_custom_token: data.requires_custom_token === true,
    }
  } catch (error) {
    // 只有明确的未授权响应才视为未登录，连接失败交给页面显示并重试。
    if (error instanceof ApiError && error.status === 401) {
      return { authenticated: false }
    }
    throw error
  }
}

/**
 * 获取首次配置状态和 Token 来源
 */
export async function getSetupStatus(signal?: AbortSignal): Promise<SetupStatus | null> {
  try {
    return await withAuthTimeout(
      (requestSignal) => authApi.get<SetupStatus>('/api/webui/setup/status', {
        signal: requestSignal,
      }),
      signal,
    )
  } catch {
    return null
  }
}
