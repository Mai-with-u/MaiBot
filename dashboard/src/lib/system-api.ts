import { fetchWithAuth, getAuthHeaders } from './fetch-with-auth'

/**
 * 系统控制 API
 */

/**
 * 重启麦麦主程序
 */
export async function restartMaiBot(): Promise<{ success: boolean; message: string }> {
  const response = await fetchWithAuth('/api/webui/system/restart', {
    method: 'POST',
    headers: getAuthHeaders(),
  })
  
  if (!response.ok) {
    const error = await response.json()
    throw new Error(error.detail || '重启失败')
  }
  
  return await response.json()
}

/**
 * 检查麦麦运行状态
 */
export async function getMaiBotStatus(): Promise<{
  running: boolean
  uptime: number
  version: string
  start_time: string
}> {
  const response = await fetchWithAuth('/api/webui/system/status', {
    method: 'GET',
    headers: getAuthHeaders(),
  })
  
  if (!response.ok) {
    const error = await response.json()
    throw new Error(error.detail || '获取状态失败')
  }
  
  return await response.json()
}
