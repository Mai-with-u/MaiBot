import { VIRTUAL_TABS_STORAGE_KEY } from './types'
import type { ChatTab, SavedVirtualTab } from './types'

const USER_AVATAR_VERSION_STORAGE_KEY = 'maibot_webui_user_avatar_version'

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
  return localStorage.getItem('maibot_webui_user_name') || '人类'
}

// 保存用户昵称到 localStorage
export function saveUserName(name: string): void {
  localStorage.setItem('maibot_webui_user_name', name)
}

export function getStoredUserAvatarVersion(): number | undefined {
  const value = Number(localStorage.getItem(USER_AVATAR_VERSION_STORAGE_KEY))
  return Number.isFinite(value) && value > 0 ? value : undefined
}

export function saveUserAvatarVersion(version: number): void {
  localStorage.setItem(USER_AVATAR_VERSION_STORAGE_KEY, String(version))
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

// 本地聊天会话对用户来说就是和 bot 对话，不展示内部 WebUI 占位名。
export function getChatTabDisplayName(tab: ChatTab, botNameFallback: string): string {
  if (tab.type === 'virtual') {
    return tab.label
  }
  return tab.sessionInfo.bot_name?.trim() || botNameFallback
}
