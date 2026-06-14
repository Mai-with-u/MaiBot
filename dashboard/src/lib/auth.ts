/**
 * 认证流程工具：登出与认证状态探测。
 *
 * 走 authApi 实例（携带 Cookie 但 401 不跳转）——
 * 在这两个场景里 401 / 未认证是正常业务结果，不应触发整页跳转。
 */
import { authApi } from '@/lib/http'

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
  try {
    const data = await authApi.get<{ authenticated?: boolean }>('/api/webui/auth/check')
    return data.authenticated === true
  } catch {
    return false
  }
}
