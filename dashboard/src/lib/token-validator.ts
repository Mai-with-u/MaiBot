/**
 * Token 验证器
 */

import { fetchWithAuth } from './fetch-with-auth'

/**
 * 验证 token 是否有效
 */
export async function validateToken(token: string): Promise<{ valid: boolean; message: string }> {
  try {
    const response = await fetchWithAuth('/api/webui/auth/verify', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token }),
    })

    if (response.ok) {
      const data = await response.json()
      return {
        valid: data.valid ?? false,
        message: data.message ?? '',
      }
    }

    return { valid: false, message: '验证失败' }
  } catch (error) {
    console.error('Token 验证失败:', error)
    return { valid: false, message: '验证请求失败' }
  }
}
