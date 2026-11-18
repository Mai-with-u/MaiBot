import { useSyncExternalStore } from 'react'

import { useResolvedAvatarUrl } from '@/lib/avatar-url'
import { getHostRuntime, useHostRuntime } from '@/host/state'

import type { ChatTab } from './types'

const STORAGE_KEY = 'maibot_local_bot_avatar'
const CHANGE_EVENT = 'maibot-local-bot-avatar-change'

function storageKey() {
  return `${STORAGE_KEY}:${getHostRuntime().selected_bot_id ?? 'standalone'}`
}

export function getLocalBotUploadId() {
  const key = `${storageKey()}:upload-id`
  const existing = localStorage.getItem(key)
  if (existing) return existing
  const id = `webui_bot_${crypto.randomUUID()}`
  localStorage.setItem(key, id)
  return id
}

export interface LocalBotAvatar {
  platform: string
  userId: string
  accountId?: string
  version?: number
}

function subscribe(listener: () => void) {
  window.addEventListener(CHANGE_EVENT, listener)
  window.addEventListener('storage', listener)
  return () => {
    window.removeEventListener(CHANGE_EVENT, listener)
    window.removeEventListener('storage', listener)
  }
}

export function setLocalBotAvatar(avatar: LocalBotAvatar | null) {
  if (avatar) localStorage.setItem(storageKey(), JSON.stringify(avatar))
  else localStorage.removeItem(storageKey())
  window.dispatchEvent(new Event(CHANGE_EVENT))
}

/** 仅本地聊天室读取此偏好，平台聊天流继续按自己的账号解析头像。 */
export function useLocalBotAvatarUrl(botQq?: string, enabled = true) {
  useHostRuntime()
  const stored = useSyncExternalStore(
    subscribe,
    () => localStorage.getItem(storageKey()),
    () => null
  )
  const avatar: LocalBotAvatar | null = enabled && stored ? JSON.parse(stored) : null
  return useResolvedAvatarUrl(
    avatar?.platform ?? 'qq',
    avatar?.userId ?? botQq,
    'user',
    avatar?.version,
    { accountId: avatar?.accountId }
  )
}

export function useChatTabAvatarUrl(tab: ChatTab) {
  const localAvatar = useLocalBotAvatarUrl(tab.sessionInfo.bot_qq, tab.type === 'webui')
  const virtualAvatar = useResolvedAvatarUrl(tab.virtualConfig?.platform, tab.virtualConfig?.userId)
  return tab.type === 'webui' ? localAvatar : virtualAvatar
}
