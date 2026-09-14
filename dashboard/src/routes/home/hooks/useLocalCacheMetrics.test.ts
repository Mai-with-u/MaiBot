import { act, cleanup, renderHook } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { getLocalCacheStats, type LocalCacheStats } from '@/lib/system-api'

const mocks = vi.hoisted(() => ({
  getLocalCacheStats: vi.fn(),
}))

vi.mock('@/lib/system-api', () => ({
  getLocalCacheStats: mocks.getLocalCacheStats,
}))

const getLocalCacheStatsMock = vi.mocked(getLocalCacheStats)

function createLocalCacheStats(overrides: Partial<LocalCacheStats> = {}): LocalCacheStats {
  return {
    directories: [
      {
        key: 'images',
        label: '图片',
        path: 'data/images',
        exists: true,
        file_count: 2,
        total_size: 1024,
        db_records: 1,
      },
    ],
    database: {
      files: [],
      tables: [],
      total_size: 2048,
      page_size: 4096,
      page_count: 1,
      freelist_count: 0,
      free_size: 0,
    },
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

async function loadUseLocalCacheMetrics() {
  vi.resetModules()
  const mod = await import('./useLocalCacheMetrics')
  return mod.useLocalCacheMetrics
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('useLocalCacheMetrics', () => {
  it('无缓存时初始为加载中，成功后写入统计', async () => {
    const stats = createLocalCacheStats()
    getLocalCacheStatsMock.mockResolvedValue(stats)
    const useLocalCacheMetrics = await loadUseLocalCacheMetrics()
    const { result } = renderHook(() => useLocalCacheMetrics())

    expect(result.current.localCacheStats).toBeNull()
    expect(result.current.isLocalCacheStatsLoading).toBe(true)

    await act(async () => {
      await result.current.fetchLocalCacheStats()
    })

    expect(getLocalCacheStatsMock).toHaveBeenCalledTimes(1)
    expect(result.current.localCacheStats).toEqual(stats)
    expect(result.current.isLocalCacheStatsLoading).toBe(false)
  })

  it('请求进行中保持加载态', async () => {
    const deferred = createDeferred<LocalCacheStats>()
    getLocalCacheStatsMock.mockReturnValue(deferred.promise)
    const useLocalCacheMetrics = await loadUseLocalCacheMetrics()
    const { result } = renderHook(() => useLocalCacheMetrics())

    let pending: Promise<void> = Promise.resolve()
    act(() => {
      pending = result.current.fetchLocalCacheStats()
    })
    expect(result.current.isLocalCacheStatsLoading).toBe(true)

    const stats = createLocalCacheStats()
    await act(async () => {
      deferred.resolve(stats)
      await pending
    })
    expect(result.current.localCacheStats).toEqual(stats)
    expect(result.current.isLocalCacheStatsLoading).toBe(false)
  })

  it('TTL 内命中缓存时不发请求，新实例直接水合', async () => {
    const stats = createLocalCacheStats({
      database: {
        files: [],
        tables: [],
        total_size: 4096,
        page_size: 4096,
        page_count: 1,
        freelist_count: 0,
        free_size: 0,
      },
    })
    getLocalCacheStatsMock.mockResolvedValue(stats)
    const useLocalCacheMetrics = await loadUseLocalCacheMetrics()
    const first = renderHook(() => useLocalCacheMetrics())

    await act(async () => {
      await first.result.current.fetchLocalCacheStats()
    })
    expect(getLocalCacheStatsMock).toHaveBeenCalledTimes(1)
    first.unmount()

    const second = renderHook(() => useLocalCacheMetrics())
    expect(second.result.current.localCacheStats).toEqual(stats)
    expect(second.result.current.isLocalCacheStatsLoading).toBe(false)

    await act(async () => {
      await second.result.current.fetchLocalCacheStats()
    })
    expect(getLocalCacheStatsMock).toHaveBeenCalledTimes(1)
  })

  it('缓存过期后重新请求', async () => {
    const nowSpy = vi.spyOn(Date, 'now').mockReturnValue(1_000)
    const stale = createLocalCacheStats({
      database: {
        files: [],
        tables: [],
        total_size: 1,
        page_size: 4096,
        page_count: 1,
        freelist_count: 0,
        free_size: 0,
      },
    })
    const fresh = createLocalCacheStats({
      database: {
        files: [],
        tables: [],
        total_size: 99,
        page_size: 4096,
        page_count: 1,
        freelist_count: 0,
        free_size: 0,
      },
    })
    getLocalCacheStatsMock.mockResolvedValueOnce(stale).mockResolvedValueOnce(fresh)
    const useLocalCacheMetrics = await loadUseLocalCacheMetrics()
    const { result } = renderHook(() => useLocalCacheMetrics())

    await act(async () => {
      await result.current.fetchLocalCacheStats()
    })
    expect(result.current.localCacheStats?.database.total_size).toBe(1)

    nowSpy.mockReturnValue(1_000 + 15 * 60_000 + 1)
    await act(async () => {
      await result.current.fetchLocalCacheStats()
    })
    expect(getLocalCacheStatsMock).toHaveBeenCalledTimes(2)
    expect(result.current.localCacheStats?.database.total_size).toBe(99)
  })

  it('失败且无缓存时保持空态', async () => {
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    getLocalCacheStatsMock.mockRejectedValue(new Error('缓存统计失败'))
    const useLocalCacheMetrics = await loadUseLocalCacheMetrics()
    const { result } = renderHook(() => useLocalCacheMetrics())

    await act(async () => {
      await result.current.fetchLocalCacheStats()
    })

    expect(result.current.localCacheStats).toBeNull()
    expect(result.current.isLocalCacheStatsLoading).toBe(false)
    expect(errorSpy).toHaveBeenCalledWith('获取本地存储占用失败:', expect.any(Error))
  })

  it('已有缓存时后续失败保留旧统计', async () => {
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    const nowSpy = vi.spyOn(Date, 'now').mockReturnValue(5_000_000)
    const stats = createLocalCacheStats()
    getLocalCacheStatsMock.mockResolvedValueOnce(stats).mockRejectedValueOnce(new Error('刷新失败'))
    const useLocalCacheMetrics = await loadUseLocalCacheMetrics()
    const { result } = renderHook(() => useLocalCacheMetrics())

    await act(async () => {
      await result.current.fetchLocalCacheStats()
    })

    nowSpy.mockReturnValue(5_000_000 + 15 * 60_000 + 1)
    await act(async () => {
      await result.current.fetchLocalCacheStats()
    })

    expect(result.current.localCacheStats).toEqual(stats)
    expect(result.current.isLocalCacheStatsLoading).toBe(false)
    expect(errorSpy).toHaveBeenCalledWith('获取本地存储占用失败:', expect.any(Error))
  })

  it('失败后允许再次拉取成功', async () => {
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    const stats = createLocalCacheStats()
    getLocalCacheStatsMock
      .mockRejectedValueOnce(new Error('缓存统计失败'))
      .mockResolvedValueOnce(stats)
    const useLocalCacheMetrics = await loadUseLocalCacheMetrics()
    const { result } = renderHook(() => useLocalCacheMetrics())

    await act(async () => {
      await result.current.fetchLocalCacheStats()
    })
    expect(result.current.localCacheStats).toBeNull()

    await act(async () => {
      await result.current.fetchLocalCacheStats()
    })
    expect(result.current.localCacheStats).toEqual(stats)
    expect(errorSpy).toHaveBeenCalledTimes(1)
  })

  it('卸载后完成的请求不会写入缓存', async () => {
    const deferred = createDeferred<LocalCacheStats>()
    getLocalCacheStatsMock.mockReturnValueOnce(deferred.promise)
    const useLocalCacheMetrics = await loadUseLocalCacheMetrics()
    const first = renderHook(() => useLocalCacheMetrics())

    let pending: Promise<void> = Promise.resolve()
    act(() => {
      pending = first.result.current.fetchLocalCacheStats()
    })
    first.unmount()

    await act(async () => {
      deferred.resolve(createLocalCacheStats())
      await pending
    })

    const second = renderHook(() => useLocalCacheMetrics())
    expect(second.result.current.localCacheStats).toBeNull()
    expect(second.result.current.isLocalCacheStatsLoading).toBe(true)
  })
})
