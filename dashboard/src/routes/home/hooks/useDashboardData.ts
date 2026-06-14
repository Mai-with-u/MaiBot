/**
 * useDashboardData —— 仪表盘统计数据领域 hook（页面逻辑下沉）。
 *
 * 收编 index.tsx 的统计数据状态机：dashboardData / loading / loadingProgress / timeRange，
 * 以及 fetchDashboardData（按 hours 维度的模块级缓存 + stale-while-revalidate）与伪加载进度条。
 *
 * 设计判断：
 * - 不引入 useQuery —— 页面保留按 hours 维度的手写 TTL 缓存（dashboardDataCache）与
 *   stale-while-revalidate，命中缓存直接回填、过期仍先展示旧数据再后台刷新。
 * - fetchDashboardData 依赖 [timeRange]，时间范围切换时函数引用变化，触发主 effect 以新 hours 重拉。
 */
import { useCallback, useEffect, useRef, useState } from 'react'

import { backendApi } from '@/lib/http'

import { DEFAULT_TIME_RANGE, type DashboardData } from '../types'

const DASHBOARD_DATA_CACHE_TTL = 5 * 60_000

// 按 hours 维度的模块级缓存（跨组件实例存活，支撑 stale-while-revalidate）
const dashboardDataCache = new Map<number, { timestamp: number; data: DashboardData }>()

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

export function useDashboardData() {
  const initialDashboardData = getCachedDashboardData(DEFAULT_TIME_RANGE) ?? getStaleDashboardData(DEFAULT_TIME_RANGE)
  const [dashboardData, setDashboardData] = useState<DashboardData | null>(initialDashboardData)
  const [loading, setLoading] = useState(!initialDashboardData)
  const [loadingProgress, setLoadingProgress] = useState(0)
  const [timeRange, setTimeRange] = useState(DEFAULT_TIME_RANGE) // 默认24小时

  // 使用 ref 跟踪组件是否已卸载，防止内存泄漏
  const isMountedRef = useRef(true)
  useEffect(() => {
    isMountedRef.current = true
    return () => {
      isMountedRef.current = false
    }
  }, [])

  const fetchDashboardData = useCallback(async (force = false) => {
    try {
      const cachedData = force ? null : getCachedDashboardData(timeRange)
      if (cachedData) {
        setDashboardData(cachedData)
        setLoading(false)
        setLoadingProgress(100)
        return
      }

      const staleData = getStaleDashboardData(timeRange)
      if (staleData) {
        setDashboardData(staleData)
        setLoading(false)
        setLoadingProgress(100)
      } else {
        setLoading(true)
      }
      const data = await backendApi.get<DashboardData>('/api/webui/statistics/dashboard', {
        query: { hours: timeRange },
      })
      if (!isMountedRef.current) return
      dashboardDataCache.set(timeRange, { timestamp: Date.now(), data })
      setDashboardData(data)
      setLoading(false)
      setLoadingProgress(100)
    } catch (error) {
      console.error('Failed to fetch dashboard data:', error)
      if (isMountedRef.current) {
        setLoading(false)
        setLoadingProgress(100)
      }
    }
  }, [timeRange])

  // 伪加载进度条效果
  useEffect(() => {
    if (!loading) return

    // 先归零，再逐级递增（用 0ms 定时器避免在 effect 同步体内 setState）
    const timer0 = setTimeout(() => setLoadingProgress(0), 0)

    // 快速到15%
    const timer1 = setTimeout(() => setLoadingProgress(15), 200)
    // 到30%
    const timer2 = setTimeout(() => setLoadingProgress(30), 800)
    // 到45%
    const timer3 = setTimeout(() => setLoadingProgress(45), 2000)
    // 到60%
    const timer4 = setTimeout(() => setLoadingProgress(60), 4000)
    // 到75%
    const timer5 = setTimeout(() => setLoadingProgress(75), 6500)
    // 到85%
    const timer6 = setTimeout(() => setLoadingProgress(85), 9000)
    // 到92%
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
    loading,
    loadingProgress,
    timeRange,
    setTimeRange,
    fetchDashboardData,
  }
}
