import { getApiBaseUrl } from './api-base'
import { isElectron } from './runtime'

// 带自动认证处理的 fetch 封装

/**
 * 将相对路径在 Electron 端转换为绝对路径
 * 浏览器端直接返回原始 input，行为不变
 */
async function resolveUrl(input: RequestInfo | URL): Promise<RequestInfo | URL> {
  if (isElectron() && typeof input === 'string' && input.startsWith('/')) {
    const base = await getApiBaseUrl()
    return base ? `${base}${input}` : input
  }
  return input
}

/**
 * 增强的 fetch 函数，自动处理 401 错误并跳转到登录页
 * 使用 HttpOnly Cookie 进行认证，自动携带 credentials
 * 
 * 对于 FormData 请求，不自动设置 Content-Type，让浏览器自动设置 multipart/form-data
 */
export async function fetchWithAuth(
  input: RequestInfo | URL,
  init?: RequestInit
): Promise<Response> {
  // 检查是否是 FormData 请求
  const isFormData = init?.body instanceof FormData
  
  // 构建 headers，对于 FormData 不设置 Content-Type
  const headers: HeadersInit = isFormData
    ? { ...init?.headers }
    : { 'Content-Type': 'application/json', ...init?.headers }
  
  // 合并默认配置，确保携带 Cookie
  const config: RequestInit = {
    ...init,
    credentials: 'include', // 确保携带 Cookie
    headers,
  }
  
  const response = await fetch(await resolveUrl(input), config)

  // 检测 401 未授权错误
  if (response.status === 401) {
    // 跳转到登录页
    window.location.href = '/auth'

    // 抛出错误以便调用者可以处理
    throw new Error('认证失败，请重新登录')
  }

  return response
}

/**
 * 获取带认证的请求配置
 * 现在使用 Cookie 认证，不再需要手动设置 Authorization header
 */
export function getAuthHeaders(): HeadersInit {
  return {
    'Content-Type': 'application/json',
  }
}

/**
 * 调用登出接口并跳转到登录页
 */
export async function logout(): Promise<void> {
  try {
    await fetch(await resolveUrl('/api/webui/auth/logout'), {
      method: 'POST',
      credentials: 'include',
    })
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
    const response = await fetch(await resolveUrl('/api/webui/auth/check'), {
      method: 'GET',
      credentials: 'include',
    })
    const data = await response.json()
    return data.authenticated === true
  } catch {
    return false
  }
}
