import { act, renderHook } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { backendApi } from '@/lib/http'

import type { DashboardData } from '../types'
import { useDashboardData } from './useDashboardData'

vi.mock('@/lib/http', () => ({
  backendApi: {
    get: vi.fn(),
  },
}))

const backendGetMock = vi.mocked(backendApi.get)

function createDashboardData(totalRequests: number): DashboardData {
  return {
    summary: {
      total_requests: totalRequests,
      total_cost: 0,
      total_tokens: 0,
      input_tokens: 0,
      output_tokens: 0,
      cache_hit_tokens: 0,
      cache_miss_tokens: 0,
      cache_hit_rate: null,
      online_time: 0,
      total_messages: 0,
      total_replies: 0,
      avg_response_time: 0,
      cost_per_hour: 0,
      tokens_per_hour: 0,
    },
    model_stats: [],
    hourly_data: [],
    daily_data: [],
    recent_activity: [],
  }
}

function createDeferred<T>() {
  let resolve: (value: T | PromiseLike<T>) => void = () => {}
  const promise = new Promise<T>((resolvePromise) => {
    resolve = resolvePromise
  })
  return { promise, resolve }
}

afterEach(() => {
  vi.clearAllMocks()
})

describe('useDashboardData', () => {
  it('较早时间范围的响应不会覆盖当前范围', async () => {
    const olderRequest = createDeferred<DashboardData>()
    const currentRequest = createDeferred<DashboardData>()
    backendGetMock
      .mockImplementationOnce(() => olderRequest.promise)
      .mockImplementationOnce(() => currentRequest.promise)
    const { result } = renderHook(() => useDashboardData())

    act(() => result.current.setTimeRange(101))
    let olderPromise: Promise<void> = Promise.resolve()
    act(() => {
      olderPromise = result.current.fetchDashboardData(true)
    })

    act(() => result.current.setTimeRange(202))
    let currentPromise: Promise<void> = Promise.resolve()
    act(() => {
      currentPromise = result.current.fetchDashboardData(true)
    })

    await act(async () => {
      currentRequest.resolve(createDashboardData(202))
      await currentPromise
    })
    expect(result.current.dashboardData?.summary.total_requests).toBe(202)

    await act(async () => {
      olderRequest.resolve(createDashboardData(101))
      await olderPromise
    })
    expect(result.current.dashboardData?.summary.total_requests).toBe(202)
  })

  it('请求失败时暴露错误，并允许强制重试', async () => {
    backendGetMock
      .mockRejectedValueOnce(new Error('统计接口暂不可用'))
      .mockResolvedValueOnce(createDashboardData(303))
    const { result } = renderHook(() => useDashboardData())

    act(() => result.current.setTimeRange(303))
    await act(async () => {
      await result.current.fetchDashboardData(true)
    })
    expect(result.current.loading).toBe(false)
    expect(result.current.error).toBe('统计接口暂不可用')
    expect(result.current.dashboardData).toBeNull()

    await act(async () => {
      await result.current.fetchDashboardData(true)
    })
    expect(result.current.error).toBeNull()
    expect(result.current.dashboardData?.summary.total_requests).toBe(303)
  })
})
