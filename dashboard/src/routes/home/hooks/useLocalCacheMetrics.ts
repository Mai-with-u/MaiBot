/**
 * useLocalCacheMetrics —— 本地存储占用领域 hook（页面逻辑下沉）。
 *
 * 收编 index.tsx 的 localCacheStats 状态机与 fetchLocalCacheStats（15min TTL 模块级缓存）。
 */
import { useCallback, useEffect, useRef, useState } from 'react'

import { getLocalCacheStats, type LocalCacheStats } from '@/lib/system-api'

const LOCAL_CACHE_STATS_CACHE_TTL = 15 * 60_000

// 模块级缓存（跨组件实例存活）
let localCacheStatsCache: { timestamp: number; data: LocalCacheStats } | null = null

function getCachedLocalCacheStats(): LocalCacheStats | null {
  if (!localCacheStatsCache || Date.now() - localCacheStatsCache.timestamp > LOCAL_CACHE_STATS_CACHE_TTL) {
    return null
  }
  return localCacheStatsCache.data
}

export function useLocalCacheMetrics() {
  const [localCacheStats, setLocalCacheStats] = useState<LocalCacheStats | null>(localCacheStatsCache?.data ?? null)
  const [isLocalCacheStatsLoading, setIsLocalCacheStatsLoading] = useState(!localCacheStatsCache)

  // 使用 ref 跟踪组件是否已卸载，防止内存泄漏
  const isMountedRef = useRef(true)
  useEffect(() => {
    isMountedRef.current = true
    return () => {
      isMountedRef.current = false
    }
  }, [])

  const fetchLocalCacheStats = useCallback(async () => {
    const cachedStats = getCachedLocalCacheStats()
    if (cachedStats) {
      setLocalCacheStats(cachedStats)
      setIsLocalCacheStatsLoading(false)
      return
    }

    setIsLocalCacheStatsLoading(true)
    try {
      const stats = await getLocalCacheStats()
      if (isMountedRef.current) {
        localCacheStatsCache = { timestamp: Date.now(), data: stats }
        setLocalCacheStats(stats)
      }
    } catch (error) {
      console.error('获取本地存储占用失败:', error)
      if (isMountedRef.current && !localCacheStatsCache) {
        setLocalCacheStats(null)
      }
    } finally {
      if (isMountedRef.current) {
        setIsLocalCacheStatsLoading(false)
      }
    }
  }, [])

  return {
    localCacheStats,
    isLocalCacheStatsLoading,
    fetchLocalCacheStats,
  }
}
