import { useEffect } from 'react'
import { unifiedWsClient } from '@/lib/unified-ws'
import { VersionCompatibilityDialog } from '@/components/version-compatibility-dialog'

import { listHostBots } from './api'
import { useBotAvailable, useHostRuntime } from './state'

export function HostVersionCompatibility() {
  return useBotAvailable() ? <VersionCompatibilityDialog /> : null
}

/** 状态监测属于宿主，不依赖任一 Bot 的 WebSocket 或配置接口。 */
export function HostRuntimeObserver() {
  const { host, authenticated, selected_bot_running } = useHostRuntime()
  useEffect(() => {
    if (host && !selected_bot_running) unifiedWsClient.disconnect()
  }, [host, selected_bot_running])
  useEffect(() => {
    if (!host || !authenticated) return
    const refresh = () => {
      void listHostBots().catch(() => {
        /* 管理页显示读取错误；401 由客户端返回登录页。 */
      })
    }
    const timer = window.setInterval(refresh, 3_000)
    window.addEventListener('focus', refresh)
    return () => {
      window.clearInterval(timer)
      window.removeEventListener('focus', refresh)
    }
  }, [host, authenticated])
  return null
}
