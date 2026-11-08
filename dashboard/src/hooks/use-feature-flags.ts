import { useSyncExternalStore } from 'react'
import { useBotAvailable } from '@/host/state'

import { BOT_CONFIG_UPDATED_EVENT, getBotConfigCached } from '@/lib/config-api'

export interface FeatureFlags {
  agent: boolean
  files: boolean
}

function resolveFeatureFlags(config: Record<string, unknown> | null): FeatureFlags {
  const agent = config?.agent
  const capabilities = config?.capabilities

  return {
    agent: agent !== null && typeof agent === 'object'
      && (agent as Record<string, unknown>).enabled === true,
    files: capabilities !== null && typeof capabilities === 'object'
      && (capabilities as Record<string, unknown>).files === true,
  }
}

interface FeatureFlagState {
  flags: FeatureFlags | null
  error: string
}

const initialState: FeatureFlagState = { flags: null, error: '' }
let state = initialState
let revision = 0
const listeners = new Set<() => void>()

function publish(next: FeatureFlagState) {
  state = next
  for (const listener of listeners) listener()
}

function refresh() {
  const requestRevision = ++revision
  getBotConfigCached().then(config => {
    if (listeners.size > 0 && requestRevision === revision) {
      publish({ flags: resolveFeatureFlags(config), error: '' })
    }
  }).catch((reason: unknown) => {
    if (listeners.size > 0 && requestRevision === revision) {
      publish({ flags: null, error: String(reason) })
    }
  })
}

function subscribe(listener: () => void) {
  listeners.add(listener)
  if (listeners.size === 1) {
    // 导航、页面和附件卡片共享一次订阅，避免每张卡片都监听配置更新。
    refresh()
    window.addEventListener(BOT_CONFIG_UPDATED_EVENT, refresh)
    window.addEventListener('focus', refresh)
  }
  return () => {
    listeners.delete(listener)
    if (listeners.size === 0) {
      revision++
      state = initialState
      window.removeEventListener(BOT_CONFIG_UPDATED_EVENT, refresh)
      window.removeEventListener('focus', refresh)
    }
  }
}

const getSnapshot = () => state
const getServerSnapshot = () => initialState
const subscribeDisabled = () => () => {}

export function useFeatureFlags() {
  const available = useBotAvailable()
  return useSyncExternalStore(available ? subscribe : subscribeDisabled, available ? getSnapshot : getServerSnapshot, getServerSnapshot)
}
