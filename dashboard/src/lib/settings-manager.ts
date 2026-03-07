/**
 * 前端设置管理器
 * 统一管理所有前端 localStorage 设置
 */

// 所有设置的 key 定义
export const STORAGE_KEYS = {
  // 外观设置
  /** @deprecated 使用新的主题系统 — 见 @/lib/theme/storage.ts 的 THEME_STORAGE_KEYS.MODE */
  THEME: 'maibot-ui-theme',
  /** @deprecated 使用新的主题系统 — 见 @/lib/theme/storage.ts 的 THEME_STORAGE_KEYS.ACCENT */
  ACCENT_COLOR: 'accent-color',
  ENABLE_ANIMATIONS: 'maibot-animations',
  ENABLE_WAVES_BACKGROUND: 'maibot-waves-background',
  
  // 性能与存储设置
  LOG_CACHE_SIZE: 'maibot-log-cache-size',
  LOG_AUTO_SCROLL: 'maibot-log-auto-scroll',
  LOG_FONT_SIZE: 'maibot-log-font-size',
  LOG_LINE_SPACING: 'maibot-log-line-spacing',
  DATA_SYNC_INTERVAL: 'maibot-data-sync-interval',
  WS_RECONNECT_INTERVAL: 'maibot-ws-reconnect-interval',
  WS_MAX_RECONNECT_ATTEMPTS: 'maibot-ws-max-reconnect-attempts',
  
  // 用户数据
  // 注意：ACCESS_TOKEN 已弃用，现在使用 HttpOnly Cookie 存储认证信息
  // 保留此常量仅用于向后兼容和清理旧数据
  ACCESS_TOKEN: 'access-token',
  COMPLETED_TOURS: 'maibot-completed-tours',
  CHAT_USER_ID: 'maibot_webui_user_id',
  CHAT_USER_NAME: 'maibot_webui_user_name',
} as const

// 默认设置值
export const DEFAULT_SETTINGS = {
  // 外观
  theme: 'system' as 'light' | 'dark' | 'system',
  accentColor: 'blue',
  enableAnimations: true,
  enableWavesBackground: true,
  
  // 性能与存储
  logCacheSize: 1000,
  logAutoScroll: true,
  logFontSize: 'xs' as 'xs' | 'sm' | 'base',
  logLineSpacing: 4,
  dataSyncInterval: 30, // 秒
  wsReconnectInterval: 3000, // 毫秒
  wsMaxReconnectAttempts: 10,
}

// 设置类型定义
export type Settings = typeof DEFAULT_SETTINGS

// 可导出的设置（不包含敏感信息）
export type ExportableSettings = Omit<Settings, never> & {
  completedTours?: string[]
}

/**
 * 获取单个设置值
 */
export function getSetting<K extends keyof Settings>(key: K): Settings[K] {
  const storageKey = getStorageKey(key)
  const stored = localStorage.getItem(storageKey)
  
  if (stored === null) {
    return DEFAULT_SETTINGS[key]
  }
  
  // 根据默认值类型进行转换
  const defaultValue = DEFAULT_SETTINGS[key]
  
  if (typeof defaultValue === 'boolean') {
    return (stored === 'true') as Settings[K]
  }
  
  if (typeof defaultValue === 'number') {
    const num = parseFloat(stored)
    return (isNaN(num) ? defaultValue : num) as Settings[K]
  }
  
  return stored as Settings[K]
}

/**
 * 设置单个值
 */
export function setSetting<K extends keyof Settings>(key: K, value: Settings[K]): void {
  const storageKey = getStorageKey(key)
  localStorage.setItem(storageKey, String(value))
  
  // 触发自定义事件，通知其他组件设置已更新
  window.dispatchEvent(new CustomEvent('maibot-settings-change', {
    detail: { key, value }
  }))
}

/**
 * 获取所有设置
 */
export function getAllSettings(): Settings {
  return {
    theme: getSetting('theme'),
    accentColor: getSetting('accentColor'),
    enableAnimations: getSetting('enableAnimations'),
    enableWavesBackground: getSetting('enableWavesBackground'),
    logCacheSize: getSetting('logCacheSize'),
    logAutoScroll: getSetting('logAutoScroll'),
    logFontSize: getSetting('logFontSize'),
    logLineSpacing: getSetting('logLineSpacing'),
    dataSyncInterval: getSetting('dataSyncInterval'),
    wsReconnectInterval: getSetting('wsReconnectInterval'),
    wsMaxReconnectAttempts: getSetting('wsMaxReconnectAttempts'),
  }
}

/**
 * 导出设置（用于备份）
 */
export function exportSettings(): ExportableSettings {
  const settings = getAllSettings()
  
  // 添加已完成的引导
  const completedToursStr = localStorage.getItem(STORAGE_KEYS.COMPLETED_TOURS)
  const completedTours = completedToursStr ? JSON.parse(completedToursStr) : []
  
  return {
    ...settings,
    completedTours,
  }
}

/**
 * 导入设置
 */
export function importSettings(settings: Partial<ExportableSettings>): { success: boolean; imported: string[]; skipped: string[] } {
  const imported: string[] = []
  const skipped: string[] = []
  
  // 验证并导入每个设置
  for (const [key, value] of Object.entries(settings)) {
    if (key === 'completedTours') {
      // 特殊处理已完成的引导
      if (Array.isArray(value)) {
        localStorage.setItem(STORAGE_KEYS.COMPLETED_TOURS, JSON.stringify(value))
        imported.push('completedTours')
      } else {
        skipped.push('completedTours')
      }
      continue
    }
    
    if (key in DEFAULT_SETTINGS) {
      const settingKey = key as keyof Settings
      const defaultValue = DEFAULT_SETTINGS[settingKey]
      
      // 类型验证
      if (typeof value === typeof defaultValue) {
        // 额外验证
        if (settingKey === 'theme' && !['light', 'dark', 'system'].includes(value as string)) {
          skipped.push(key)
          continue
        }
        if (settingKey === 'logFontSize' && !['xs', 'sm', 'base'].includes(value as string)) {
          skipped.push(key)
          continue
        }
        
        setSetting(settingKey, value as Settings[typeof settingKey])
        imported.push(key)
      } else {
        skipped.push(key)
      }
    } else {
      skipped.push(key)
    }
  }
  
  return {
    success: imported.length > 0,
    imported,
    skipped,
  }
}

/**
 * 重置所有设置为默认值
 */
export function resetAllSettings(): void {
  for (const key of Object.keys(DEFAULT_SETTINGS) as (keyof Settings)[]) {
    setSetting(key, DEFAULT_SETTINGS[key])
  }
  
  // 清除已完成的引导
  localStorage.removeItem(STORAGE_KEYS.COMPLETED_TOURS)
  
  // 触发全局事件
  window.dispatchEvent(new CustomEvent('maibot-settings-reset'))
}

/**
 * 清除所有本地缓存
 * 注意：认证信息现在存储在 HttpOnly Cookie 中，不受此函数影响
 */
export function clearLocalCache(): { clearedKeys: string[]; preservedKeys: string[] } {
  const clearedKeys: string[] = []
  const preservedKeys: string[] = []
  
  // 遍历所有 localStorage 项
  const keysToRemove: string[] = []
  for (let i = 0; i < localStorage.length; i++) {
    const key = localStorage.key(i)
    if (key) {
      if (key.startsWith('maibot') || key.startsWith('accent-color') || key === 'access-token') {
        keysToRemove.push(key)
      }
    }
  }
  
  // 删除需要清除的 key
  for (const key of keysToRemove) {
    localStorage.removeItem(key)
    clearedKeys.push(key)
  }
  
  return { clearedKeys, preservedKeys }
}

/**
 * 获取本地存储使用情况
 */
export function getStorageUsage(): { used: number; items: number; details: { key: string; size: number }[] } {
  let totalSize = 0
  const details: { key: string; size: number }[] = []
  
  for (let i = 0; i < localStorage.length; i++) {
    const key = localStorage.key(i)
    if (key) {
      const value = localStorage.getItem(key) || ''
      const size = (key.length + value.length) * 2 // UTF-16 编码，每个字符 2 字节
      totalSize += size
      details.push({ key, size })
    }
  }
  
  // 按大小排序
  details.sort((a, b) => b.size - a.size)
  
  return {
    used: totalSize,
    items: localStorage.length,
    details,
  }
}

/**
 * 格式化字节大小
 */
export function formatBytes(bytes: number): string {
  if (bytes === 0) return '0 B'
  const k = 1024
  const sizes = ['B', 'KB', 'MB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i]
}

// 内部辅助函数：获取 localStorage key
function getStorageKey(settingKey: keyof Settings): string {
  const keyMap: Record<keyof Settings, string> = {
    theme: STORAGE_KEYS.THEME,
    accentColor: STORAGE_KEYS.ACCENT_COLOR,
    enableAnimations: STORAGE_KEYS.ENABLE_ANIMATIONS,
    enableWavesBackground: STORAGE_KEYS.ENABLE_WAVES_BACKGROUND,
    logCacheSize: STORAGE_KEYS.LOG_CACHE_SIZE,
    logAutoScroll: STORAGE_KEYS.LOG_AUTO_SCROLL,
    logFontSize: STORAGE_KEYS.LOG_FONT_SIZE,
    logLineSpacing: STORAGE_KEYS.LOG_LINE_SPACING,
    dataSyncInterval: STORAGE_KEYS.DATA_SYNC_INTERVAL,
    wsReconnectInterval: STORAGE_KEYS.WS_RECONNECT_INTERVAL,
    wsMaxReconnectAttempts: STORAGE_KEYS.WS_MAX_RECONNECT_ATTEMPTS,
  }
  return keyMap[settingKey]
}
