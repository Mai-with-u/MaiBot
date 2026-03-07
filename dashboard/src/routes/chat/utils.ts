import { VIRTUAL_TABS_STORAGE_KEY } from './types'
import type { SavedVirtualTab } from './types'

// 生成唯一用户 ID
export function generateUserId(): string {
  return 'webui_' + Math.random().toString(36).substr(2, 9) + '_' + Date.now().toString(36)
}

// 从 localStorage 获取或生成用户 ID
export function getOrCreateUserId(): string {
  const storageKey = 'maibot_webui_user_id'
  let userId = localStorage.getItem(storageKey)
  if (!userId) {
    userId = generateUserId()
    localStorage.setItem(storageKey, userId)
  }
  return userId
}

// 从 localStorage 获取用户昵称
export function getStoredUserName(): string {
  return localStorage.getItem('maibot_webui_user_name') || 'WebUI用户'
}

// 保存用户昵称到 localStorage
export function saveUserName(name: string): void {
  localStorage.setItem('maibot_webui_user_name', name)
}

// 从 localStorage 获取保存的虚拟标签页
export function getSavedVirtualTabs(): SavedVirtualTab[] {
  try {
    const saved = localStorage.getItem(VIRTUAL_TABS_STORAGE_KEY)
    if (saved) {
      return JSON.parse(saved)
    }
  } catch (e) {
    console.error('[Chat] 加载虚拟标签页失败:', e)
  }
  return []
}

// 保存虚拟标签页到 localStorage
export function saveVirtualTabs(tabs: SavedVirtualTab[]): void {
  try {
    localStorage.setItem(VIRTUAL_TABS_STORAGE_KEY, JSON.stringify(tabs))
  } catch (e) {
    console.error('[Chat] 保存虚拟标签页失败:', e)
  }
}
