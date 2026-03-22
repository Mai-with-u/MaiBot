/**
 * 设置管理器
 */

import { fetchWithAuth } from './fetch-with-auth'

// 默认设置
export const DEFAULT_SETTINGS = {
  theme: 'system' as const,
  reducedMotion: false,
  fontSize: 'medium' as const,
}

type Settings = typeof DEFAULT_SETTINGS

const STORAGE_KEY = 'maibot_settings'

/**
 * 获取设置项
 */
export function getSetting<K extends keyof Settings>(key: K): Settings[K] {
  try {
    const stored = localStorage.getItem(STORAGE_KEY)
    if (stored) {
      const settings = JSON.parse(stored) as Settings
      return settings[key] ?? DEFAULT_SETTINGS[key]
    }
  } catch {
    // ignore
  }
  return DEFAULT_SETTINGS[key]
}

/**
 * 设置设置项
 */
export function setSetting<K extends keyof Settings>(key: K, value: Settings[K]): void {
  try {
    const stored = localStorage.getItem(STORAGE_KEY)
    const settings: Settings = stored ? JSON.parse(stored) : { ...DEFAULT_SETTINGS }
    settings[key] = value
    localStorage.setItem(STORAGE_KEY, JSON.stringify(settings))
  } catch {
    // ignore
  }
}

/**
 * 导出设置
 */
export async function exportSettings(): Promise<void> {
  try {
    const response = await fetchWithAuth('/api/webui/settings/export')
    if (response.ok) {
      const data = await response.json()
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = 'maibot-settings.json'
      link.click()
      URL.revokeObjectURL(url)
    }
  } catch (error) {
    console.error('导出设置失败:', error)
  }
}

/**
 * 导入设置
 */
export async function importSettings(file: File): Promise<boolean> {
  try {
    const content = await file.text()
    const data = JSON.parse(content)

    const response = await fetchWithAuth('/api/webui/settings/import', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    })

    return response.ok
  } catch (error) {
    console.error('导入设置失败:', error)
    return false
  }
}

/**
 * 重置所有设置
 */
export async function resetAllSettings(): Promise<boolean> {
  try {
    localStorage.removeItem(STORAGE_KEY)
    const response = await fetchWithAuth('/api/webui/settings/reset', { method: 'POST' })
    return response.ok
  } catch (error) {
    console.error('重置设置失败:', error)
    return false
  }
}

/**
 * 清除本地缓存
 */
export function clearLocalCache(): void {
  localStorage.clear()
  sessionStorage.clear()
}

/**
 * 获取存储使用量
 */
export async function getStorageUsage(): Promise<{ used: number; available: number }> {
  try {
    const response = await fetchWithAuth('/api/webui/storage/usage')
    if (response.ok) {
      return await response.json()
    }
  } catch {
    // ignore
  }
  return { used: 0, available: 0 }
}

/**
 * 格式化字节数
 */
export function formatBytes(bytes: number): string {
  if (bytes === 0) return '0 B'

  const k = 1024
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))

  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(2))} ${sizes[i]}`
}
