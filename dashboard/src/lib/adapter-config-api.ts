/**
 * 适配器配置API客户端
 */

import { fetchWithAuth, getAuthHeaders } from '@/lib/fetch-with-auth'

const API_BASE = '/api/webui/config'

export interface AdapterConfigPath {
  path: string
  lastModified?: string
}

interface ConfigPathResponse {
  success: boolean
  path?: string
  lastModified?: string
}

interface ConfigContentResponse {
  success: boolean
  content: string
}

interface ConfigMessageResponse {
  success: boolean
  message: string
}

/**
 * 获取保存的适配器配置文件路径
 */
export async function getSavedConfigPath(): Promise<AdapterConfigPath | null> {
  const response = await fetchWithAuth(`${API_BASE}/adapter-config/path`)
  const data: ConfigPathResponse = await response.json()
  
  if (!data.success || !data.path) {
    return null
  }
  
  return {
    path: data.path,
    lastModified: data.lastModified,
  }
}

/**
 * 保存适配器配置文件路径偏好设置
 */
export async function saveConfigPath(path: string): Promise<void> {
  const response = await fetchWithAuth(`${API_BASE}/adapter-config/path`, {
    method: 'POST',
    headers: getAuthHeaders(),
    body: JSON.stringify({ path }),
  })
  
  const data: ConfigMessageResponse = await response.json()
  
  if (!data.success) {
    throw new Error(data.message || '保存路径失败')
  }
}

/**
 * 从指定路径读取适配器配置文件
 */
export async function loadConfigFromPath(path: string): Promise<string> {
  const response = await fetchWithAuth(
    `${API_BASE}/adapter-config?path=${encodeURIComponent(path)}`
  )
  const data: ConfigContentResponse = await response.json()
  
  if (!data.success) {
    throw new Error('读取配置文件失败')
  }
  
  return data.content
}

/**
 * 保存适配器配置到指定路径
 */
export async function saveConfigToPath(path: string, content: string): Promise<void> {
  const response = await fetchWithAuth(`${API_BASE}/adapter-config`, {
    method: 'POST',
    headers: getAuthHeaders(),
    body: JSON.stringify({ path, content }),
  })
  
  const data: ConfigMessageResponse = await response.json()
  
  if (!data.success) {
    throw new Error(data.message || '保存配置失败')
  }
}
