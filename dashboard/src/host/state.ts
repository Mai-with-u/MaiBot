import { useSyncExternalStore } from 'react'

import type { HostBot } from './api'

export interface HostMode {
  host: boolean
  authenticated: boolean
  selected_bot_id: string | null
  selected_bot_running: boolean
}

export interface HostRuntime extends HostMode {
  bots: HostBot[]
}

let state: HostRuntime = {
  host: false,
  authenticated: false,
  selected_bot_id: null,
  selected_bot_running: false,
  bots: [],
}
const listeners = new Set<() => void>()

export function getHostRuntime(): HostRuntime {
  return state
}

/** 用于 img/audio/link 等同步资源地址，外部 URL 和已固定实例的地址保持原样。 */
export function scopeBotResourcePath(path: string): string {
  if (
    !state.host ||
    path.startsWith('/api/host/') ||
    (!path.startsWith('/api/') && path !== '/maibot_statistics.html')
  )
    return path
  if (!state.selected_bot_id) throw new Error('请先选择 Bot')
  return `/api/host/bots/${encodeURIComponent(state.selected_bot_id)}/proxy${path}`
}

export function setHostRuntime(next: HostRuntime): void {
  state = next
  for (const listener of listeners) listener()
}

export function updateHostBots(bots: HostBot[]): void {
  if (!state.host) return
  const selected = bots.find((bot) => bot.bot_id === state.selected_bot_id)
  setHostRuntime({ ...state, bots, selected_bot_running: selected?.status === 'running' })
}

function subscribe(listener: () => void) {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

export function useHostRuntime(): HostRuntime {
  return useSyncExternalStore(subscribe, getHostRuntime, getHostRuntime)
}

export function useBotAvailable(): boolean {
  const runtime = useHostRuntime()
  return !runtime.host || runtime.selected_bot_running
}
