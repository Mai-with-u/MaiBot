import { act, cleanup, renderHook } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { getReviewStats } from '@/lib/expression-api'
import type { ReviewStats } from '@/types/expression'

import { useReviewStats } from './useReviewStats'

vi.mock('@/lib/expression-api', () => ({
  getReviewStats: vi.fn(),
}))

const getReviewStatsMock = vi.mocked(getReviewStats)

function createReviewStats(overrides: Partial<ReviewStats> = {}): ReviewStats {
  return {
    total: 10,
    unchecked: 3,
    passed: 7,
    ai_checked: 4,
    user_checked: 3,
    ...overrides,
  }
}

function createDeferred<T>() {
  let resolve: (value: T | PromiseLike<T>) => void = () => {}
  let reject: (reason?: unknown) => void = () => {}
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, resolve, reject }
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('useReviewStats', () => {
  it('初始未审核数为 0，成功后写入 unchecked', async () => {
    getReviewStatsMock.mockResolvedValue(createReviewStats({ unchecked: 12 }))
    const { result } = renderHook(() => useReviewStats())

    expect(result.current.uncheckedCount).toBe(0)

    await act(async () => {
      await result.current.fetchReviewStats()
    })

    expect(getReviewStatsMock).toHaveBeenCalledTimes(1)
    expect(result.current.uncheckedCount).toBe(12)
  })

  it('未审核数为 0 的空态也会写入', async () => {
    getReviewStatsMock.mockResolvedValue(createReviewStats({ unchecked: 0, total: 0, passed: 0 }))
    const { result } = renderHook(() => useReviewStats())

    await act(async () => {
      await result.current.fetchReviewStats()
    })

    expect(result.current.uncheckedCount).toBe(0)
  })

  it('请求失败时记录错误并保留原值', async () => {
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    getReviewStatsMock
      .mockResolvedValueOnce(createReviewStats({ unchecked: 8 }))
      .mockRejectedValueOnce(new Error('审核统计失败'))
    const { result } = renderHook(() => useReviewStats())

    await act(async () => {
      await result.current.fetchReviewStats()
    })
    expect(result.current.uncheckedCount).toBe(8)

    await act(async () => {
      await result.current.fetchReviewStats()
    })
    expect(result.current.uncheckedCount).toBe(8)
    expect(errorSpy).toHaveBeenCalledWith('获取审核统计失败:', expect.any(Error))
  })

  it('首次失败时保持 0', async () => {
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    getReviewStatsMock.mockRejectedValue(new Error('审核统计失败'))
    const { result } = renderHook(() => useReviewStats())

    await act(async () => {
      await result.current.fetchReviewStats()
    })

    expect(result.current.uncheckedCount).toBe(0)
    expect(errorSpy).toHaveBeenCalledTimes(1)
  })

  it('卸载后完成的请求不会回写', async () => {
    const deferred = createDeferred<ReviewStats>()
    getReviewStatsMock.mockReturnValue(deferred.promise)
    const { result, unmount } = renderHook(() => useReviewStats())

    let pending: Promise<void> = Promise.resolve()
    act(() => {
      pending = result.current.fetchReviewStats()
    })
    unmount()

    await act(async () => {
      deferred.resolve(createReviewStats({ unchecked: 99 }))
      await pending
    })
  })
})
