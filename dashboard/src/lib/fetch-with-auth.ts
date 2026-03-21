/**
 * 带认证的 fetch 工具
 * 自动处理 session token 和认证错误
 */

const SESSION_COOKIE_NAME = 'maibot_session'

/**
 * 获取 cookie 中的 session token
 */
function getSessionToken(): string | null {
  const cookies = document.cookie.split(';')
  for (const cookie of cookies) {
    const [name, value] = cookie.trim().split('=')
    if (name === SESSION_COOKIE_NAME) {
      return value
    }
  }
  return null
}

/**
 * 获取认证请求头
 */
export function getAuthHeaders(): Record<string, string> {
  const token = getSessionToken()
  if (token) {
    return { 'Authorization': `Bearer ${token}` }
  }
  return {}
}

/**
 * 带认证的 fetch
 * 自动添加认证头，处理 401 错误
 */
export async function fetchWithAuth(
  url: string,
  options: RequestInit = {}
): Promise<Response> {
  const headers = {
    ...options.headers,
    ...getAuthHeaders(),
  }

  const response = await fetch(url, {
    ...options,
    headers,
    credentials: 'include', // 包含 cookies
  })

  // 如果返回 401，可能需要重新登录
  if (response.status === 401) {
    // 可以在这里触发登录流程或重定向
    console.warn('[fetchWithAuth] 认证失败，可能需要重新登录')
  }

  return response
}

/**
 * 检查认证状态
 */
export async function checkAuthStatus(): Promise<{ authenticated: boolean; username?: string }> {
  try {
    const response = await fetchWithAuth('/api/webui/auth/status')
    if (response.ok) {
      const data = await response.json()
      return {
        authenticated: data.authenticated ?? false,
        username: data.username,
      }
    }
    return { authenticated: false }
  } catch (error) {
    console.error('[checkAuthStatus] 检查认证状态失败:', error)
    return { authenticated: false }
  }
}

/**
 * 登出
 */
export async function logout(): Promise<void> {
  try {
    await fetchWithAuth('/api/webui/auth/logout', { method: 'POST' })
  } catch (error) {
    console.error('[logout] 登出失败:', error)
  }
  // 清除本地 session
  document.cookie = `${SESSION_COOKIE_NAME}=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/`
  // 刷新页面
  window.location.reload()
}
