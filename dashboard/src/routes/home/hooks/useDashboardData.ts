/**
 * useDashboardData —— 仪表盘统计数据领域 hook（页面逻辑下沉）。
 *
 * 保留按 hours 维度的模块级缓存与 stale-while-revalidate，同时显式暴露
 * 加载错误。每次请求都有递增编号，较早请求即使晚返回也不能覆盖当前范围。
 */
import { useCallback, useEffect, useRef, useState } from 'react'

import { backendApi } from '@/lib/http'

import { DEFAULT_TIME_RANGE, type DashboardData } from '../types'

const DASHBOARD_DATA_CACHE_TTL = 5 * 60_000

// 按 hours 维度的模块级缓存（跨组件实例存活，支持 stale-while-revalidate）。
const dashboardDataCache = new Map<
  number,
  { timestamp: number; data: DashboardData }
>()

function getCachedDashboardData(hours: number): DashboardData | null {
  const cached = dashboardDataCache.get(hours)
  if (!cached || Date.now() - cached.timestamp > DASHBOARD_DATA_CACHE_TTL) {
    return null
  }
  return cached.data
}

function getStaleDashboardData(hours: number): DashboardData | null {
  return dashboardDataCache.get(hours)?.data ?? null
}

function getDashboardErrorMessage(error: unknown): string {
  if (error instanceof Error && error.message.trim()) {
    return error.message
  }
  return '仪表盘数据加载失败，请稍后重试'
}

export function useDashboardData() {
  const initialDashboardData =
    getCachedDashboardData(DEFAULT_TIME_RANGE) ??
    getStaleDashboardData(DEFAULT_TIME_RANGE)
  const [dashboardData, setDashboardData] = useState<DashboardData | null>(
    initialDashboardData
  )
  const [loading, setLoading] = useState(!initialDashboardData)
  const [loadingProgress, setLoadingProgress] = useState(0)
  const [error, setError] = useState<string | null>(null)
  const [timeRangeState, setTimeRangeState] = useState(DEFAULT_TIME_RANGE)

  const isMountedRef = useRef(true)
  const latestRequestIdRef = useRef(0)

  useEffect(() => {
    isMountedRef.current = true
    return () => {
      isMountedRef.current = false
      latestRequestIdRef.current += 1
    }
  }, [])

  // 切换范围时立刻使旧请求失效，不必等下一轮 effect 开始请求。
  const setTimeRange = useCallback((nextTimeRange: number) => {
    latestRequestIdRef.current += 1
    setTimeRangeState(nextTimeRange)
  }, [])

  const fetchDashboardData = useCallback(
    async (force = false) => {
      const requestedTimeRange = timeRangeState
      const requestId = latestRequestIdRef.current + 1
      latestRequestIdRef.current = requestId
      setError(null)

      try {
        const cachedData = force
          ? null
          : getCachedDashboardData(requestedTimeRange)
        if (cachedData) {
          setDashboardData(cachedData)
          setLoading(false)
          setLoadingProgress(100)
          return
        }

        const staleData = getStaleDashboardData(requestedTimeRange)
        if (staleData) {
          setDashboardData(staleData)
          setLoading(false)
          setLoadingProgress(100)
        } else {
          // 不保留其他时间范围的数据，避免失败后显示范围与内容不一致。
          setDashboardData(null)
          setLoading(true)
        }

        const data = await backendApi.get<DashboardData>(
          '/api/webui/statistics/dashboard',
          { query: { hours: requestedTimeRange } }
        )
        if (
          !isMountedRef.current ||
          requestId !== latestRequestIdRef.current
        ) {
          return
        }
        dashboardDataCache.set(requestedTimeRange, {
          timestamp: Date.now(),
          data,
        })
        setDashboardData(data)
        setLoading(false)
        setLoadingProgress(100)
      } catch (requestError) {
        if (
          !isMountedRef.current ||
          requestId !== latestRequestIdRef.current
        ) {
          return
        }
        console.error('Failed to fetch dashboard data:', requestError)
        setError(getDashboardErrorMessage(requestError))
        setLoading(false)
        setLoadingProgress(100)
      }
    },
    [timeRangeState]
  )

  // 伪加载进度条效果。
  useEffect(() => {
    if (!loading) return

    const timer0 = setTimeout(() => setLoadingProgress(0), 0)
    const timer1 = setTimeout(() => setLoadingProgress(15), 200)
    const timer2 = setTimeout(() => setLoadingProgress(30), 800)
    const timer3 = setTimeout(() => setLoadingProgress(45), 2000)
    const timer4 = setTimeout(() => setLoadingProgress(60), 4000)
    const timer5 = setTimeout(() => setLoadingProgress(75), 6500)
    const timer6 = setTimeout(() => setLoadingProgress(85), 9000)
    const timer7 = setTimeout(() => setLoadingProgress(92), 11000)

    return () => {
      clearTimeout(timer0)
      clearTimeout(timer1)
      clearTimeout(timer2)
      clearTimeout(timer3)
      clearTimeout(timer4)
      clearTimeout(timer5)
      clearTimeout(timer6)
      clearTimeout(timer7)
    }
  }, [loading])

  return {
    dashboardData,
    error,
    loading,
    loadingProgress,
    timeRange: timeRangeState,
    setTimeRange,
    fetchDashboardData,
  }
}
