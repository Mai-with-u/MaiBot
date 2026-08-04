import { useCallback, useEffect, useRef, useState } from 'react'

import { backendApi } from '@/lib/http'

import type { DetailedStatisticsData } from './types'

let detailedStatisticsCache: DetailedStatisticsData | null = null

function getErrorMessage(error: unknown): string | null {
  if (error instanceof Error && error.message.trim()) {
    return error.message
  }
  return null
}

export function useDetailedStatistics() {
  const [data, setData] = useState<DetailedStatisticsData | null>(detailedStatisticsCache)
  const [loading, setLoading] = useState(detailedStatisticsCache === null)
  const [error, setError] = useState<string | null>(null)
  const requestIdRef = useRef(0)
  const mountedRef = useRef(true)

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      requestIdRef.current += 1
    }
  }, [])

  const refresh = useCallback(async () => {
    const requestId = requestIdRef.current + 1
    requestIdRef.current = requestId
    setLoading(true)
    setError(null)

    try {
      const nextData = await backendApi.get<DetailedStatisticsData>(
        '/api/webui/statistics/detailed'
      )
      if (!mountedRef.current || requestId !== requestIdRef.current) {
        return
      }
      detailedStatisticsCache = nextData
      setData(nextData)
    } catch (requestError) {
      if (!mountedRef.current || requestId !== requestIdRef.current) {
        return
      }
      setError(getErrorMessage(requestError))
    } finally {
      if (mountedRef.current && requestId === requestIdRef.current) {
        setLoading(false)
      }
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  return { data, error, loading, refresh }
}
