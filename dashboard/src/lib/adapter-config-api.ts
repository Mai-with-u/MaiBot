/**
 * 适配器配置API客户端
 */

import { backendApi, requireSuccess } from '@/lib/http'

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
  const data = await backendApi.get<ConfigPathResponse>(`${API_BASE}/adapter-config/path`, {
    errorMessage: '获取适配器配置路径失败',
  })

  // 未保存过路径属于正常情况，返回 null 而不是抛错
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
  const data = await backendApi.post<ConfigMessageResponse>(`${API_BASE}/adapter-config/path`, {
    body: { path },
    errorMessage: '保存路径失败',
  })
  requireSuccess(data, '保存路径失败')
}

/**
 * 从指定路径读取适配器配置文件
 */
export async function loadConfigFromPath(path: string): Promise<string> {
  const data = await backendApi.get<ConfigContentResponse>(`${API_BASE}/adapter-config`, {
    query: { path },
    errorMessage: '读取配置文件失败',
  })
  return requireSuccess(data, '读取配置文件失败').content
}

/**
 * 保存适配器配置到指定路径
 */
export async function saveConfigToPath(path: string, content: string): Promise<void> {
  const data = await backendApi.post<ConfigMessageResponse>(`${API_BASE}/adapter-config`, {
    body: { path, content },
    errorMessage: '保存配置失败',
  })
  requireSuccess(data, '保存配置失败')
}
