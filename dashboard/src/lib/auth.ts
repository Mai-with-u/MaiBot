/**
 * 认证流程工具：登出与认证状态探测。
 *
 * 走 authApi 实例（携带 Cookie 但 401 不跳转）——
 * 在这两个场景里 401 / 未认证是正常业务结果，不应触发整页跳转。
 */
import { authApi } from '@/lib/http'

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
export async function checkAuthStatus(): Promise<boolean> {
  return (await getAuthStatus()).authenticated
}

/**
 * 获取当前认证状态和 Token 来源
 */
export async function getAuthStatus(): Promise<AuthStatus> {
  try {
    const data = await authApi.get<AuthStatus>('/api/webui/auth/check')
    return {
      authenticated: data.authenticated === true,
      token_source: data.token_source,
      requires_custom_token: data.requires_custom_token === true,
    }
  } catch {
    return { authenticated: false }
  }
}

/**
 * 获取首次配置状态和 Token 来源
 */
export async function getSetupStatus(): Promise<SetupStatus | null> {
  try {
    return await authApi.get<SetupStatus>('/api/webui/setup/status')
  } catch {
    return null
  }
}
