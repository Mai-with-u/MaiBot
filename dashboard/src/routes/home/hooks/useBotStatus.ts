/**
 * useBotStatus —— 机器人运行状态领域 hook（页面逻辑下沉）。
 *
 * 收编 index.tsx 的机器人状态状态机：botStatus / isBotStatusLoading，
 * 以及 fetchBotStatus（30s TTL 模块级缓存）、30s 轮询、visibilitychange / focus 刷新。
 *
 * 设计判断：
 * - 不引入 useQuery —— 保留手写 30s TTL 缓存（botStatusCache）跨实例存活。
 * - fetchBotStatus 依赖为空数组，引用稳定，轮询/可见性 effect 不会反复重建。
 */
import { useCallback, useEffect, useRef, useState } from 'react'

import { backendApi } from '@/lib/http'

import type { BotStatus } from '../types'

const BOT_STATUS_CACHE_TTL = 30_000

// 模块级缓存（跨组件实例存活）
let botStatusCache: { timestamp: number; data: BotStatus } | null = null

function getCachedBotStatus(): BotStatus | null {
  if (!botStatusCache || Date.now() - botStatusCache.timestamp > BOT_STATUS_CACHE_TTL) {
    return null
  }
  return botStatusCache.data
}

export function useBotStatus() {
  const [botStatus, setBotStatus] = useState<BotStatus | null>(botStatusCache?.data ?? null)
  const [isBotStatusLoading, setIsBotStatusLoading] = useState(!botStatusCache)

  // 使用 ref 跟踪组件是否已卸载，防止内存泄漏
  const isMountedRef = useRef(true)
  useEffect(() => {
    isMountedRef.current = true
    return () => {
      isMountedRef.current = false
    }
  }, [])

  // 获取机器人状态
  const fetchBotStatus = useCallback(async (force = false) => {
    const cachedStatus = force ? null : getCachedBotStatus()
    if (cachedStatus) {
      setBotStatus(cachedStatus)
      setIsBotStatusLoading(false)
      return
    }

    setIsBotStatusLoading(true)
    try {
      const data = await backendApi.get<BotStatus>('/api/webui/system/status')
      if (!isMountedRef.current) return
      botStatusCache = { timestamp: Date.now(), data }
      setBotStatus(data)
    } catch (error) {
      console.error('获取机器人状态失败:', error)
      if (isMountedRef.current && !botStatusCache) {
        setBotStatus(null)
      }
    } finally {
      if (isMountedRef.current) {
        setIsBotStatusLoading(false)
      }
    }
  }, [])

  // 30s 轮询 + 可见性 / 焦点恢复时刷新
  useEffect(() => {
    const refreshBotStatus = () => {
      if (isMountedRef.current) {
        fetchBotStatus(true)
      }
    }

    const handleVisibilityChange = () => {
      if (document.visibilityState === 'visible') {
        refreshBotStatus()
      }
    }

    const intervalId = setInterval(refreshBotStatus, 30000)
    document.addEventListener('visibilitychange', handleVisibilityChange)
    window.addEventListener('focus', refreshBotStatus)

    return () => {
      clearInterval(intervalId)
      document.removeEventListener('visibilitychange', handleVisibilityChange)
      window.removeEventListener('focus', refreshBotStatus)
    }
  }, [fetchBotStatus])

  return {
    botStatus,
    isBotStatusLoading,
    fetchBotStatus,
  }
}
