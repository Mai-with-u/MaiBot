import { act, cleanup, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { BotStatus } from '../types'

const mocks = vi.hoisted(() => ({
  backendGet: vi.fn(),
}))

vi.mock('@/lib/http', () => ({
  backendApi: {
    get: mocks.backendGet,
  },
}))

const backendGetMock = mocks.backendGet

let visibilityState: DocumentVisibilityState = 'visible'

function setVisibilityState(state: DocumentVisibilityState) {
  visibilityState = state
}

function createBotStatus(overrides: Partial<BotStatus> = {}): BotStatus {
  return {
    running: true,
    uptime: 3600,
    version: '1.0.0',
    start_time: '2025-01-01T00:00:00Z',
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

async function loadUseBotStatus() {
  vi.resetModules()
  const mod = await import('./useBotStatus')
  return mod.useBotStatus
}

beforeEach(() => {
  visibilityState = 'visible'
  Object.defineProperty(document, 'visibilityState', {
    configurable: true,
    get: () => visibilityState,
  })
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.clearAllMocks()
})

describe('useBotStatus', () => {
  it('无缓存时初始为加载中，成功拉取在线状态', async () => {
    const online = createBotStatus({ running: true, uptime: 42 })
    backendGetMock.mockResolvedValue(online)
    const useBotStatus = await loadUseBotStatus()
    const { result } = renderHook(() => useBotStatus())

    expect(result.current.botStatus).toBeNull()
    expect(result.current.isBotStatusLoading).toBe(true)
    expect(backendGetMock).not.toHaveBeenCalled()

    await act(async () => {
      await result.current.fetchBotStatus()
    })

    expect(backendGetMock).toHaveBeenCalledWith('/api/webui/system/status')
    expect(result.current.botStatus).toEqual(online)
    expect(result.current.isBotStatusLoading).toBe(false)
  })

  it('成功拉取离线状态', async () => {
    const offline = createBotStatus({ running: false, uptime: 0 })
    backendGetMock.mockResolvedValue(offline)
    const useBotStatus = await loadUseBotStatus()
    const { result } = renderHook(() => useBotStatus())

    await act(async () => {
      await result.current.fetchBotStatus()
    })

    expect(result.current.botStatus).toEqual(offline)
    expect(result.current.botStatus?.running).toBe(false)
    expect(result.current.isBotStatusLoading).toBe(false)
  })

  it('请求进行中保持加载态，完成后写入状态', async () => {
    const deferred = createDeferred<BotStatus>()
    backendGetMock.mockReturnValue(deferred.promise)
    const useBotStatus = await loadUseBotStatus()
    const { result } = renderHook(() => useBotStatus())

    let pending: Promise<void> = Promise.resolve()
    act(() => {
      pending = result.current.fetchBotStatus()
    })
    expect(result.current.isBotStatusLoading).toBe(true)
    expect(result.current.botStatus).toBeNull()

    const online = createBotStatus({ running: true })
    await act(async () => {
      deferred.resolve(online)
      await pending
    })

    expect(result.current.botStatus).toEqual(online)
    expect(result.current.isBotStatusLoading).toBe(false)
  })

  it('请求失败且无缓存时清空状态并结束加载', async () => {
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    backendGetMock.mockRejectedValue(new Error('状态接口不可用'))
    const useBotStatus = await loadUseBotStatus()
    const { result } = renderHook(() => useBotStatus())

    await act(async () => {
      await result.current.fetchBotStatus()
    })

    expect(result.current.botStatus).toBeNull()
    expect(result.current.isBotStatusLoading).toBe(false)
    expect(errorSpy).toHaveBeenCalledWith('获取机器人状态失败:', expect.any(Error))
  })

  it('失败后允许强制重试并恢复在线状态', async () => {
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    const online = createBotStatus({ running: true, version: '2.0.0' })
    backendGetMock
      .mockRejectedValueOnce(new Error('状态接口暂不可用'))
      .mockResolvedValueOnce(online)
    const useBotStatus = await loadUseBotStatus()
    const { result } = renderHook(() => useBotStatus())

    await act(async () => {
      await result.current.fetchBotStatus(true)
    })
    expect(result.current.botStatus).toBeNull()
    expect(result.current.isBotStatusLoading).toBe(false)

    await act(async () => {
      await result.current.fetchBotStatus(true)
    })
    expect(result.current.botStatus).toEqual(online)
    expect(result.current.isBotStatusLoading).toBe(false)
    expect(backendGetMock).toHaveBeenCalledTimes(2)
    expect(errorSpy).toHaveBeenCalledTimes(1)
  })

  it('TTL 内命中缓存时不发请求，新实例直接水合缓存', async () => {
    const online = createBotStatus({ running: true, version: 'cached' })
    backendGetMock.mockResolvedValue(online)
    const useBotStatus = await loadUseBotStatus()
    const first = renderHook(() => useBotStatus())

    await act(async () => {
      await first.result.current.fetchBotStatus()
    })
    expect(backendGetMock).toHaveBeenCalledTimes(1)
    first.unmount()

    const second = renderHook(() => useBotStatus())
    expect(second.result.current.botStatus).toEqual(online)
    expect(second.result.current.isBotStatusLoading).toBe(false)

    await act(async () => {
      await second.result.current.fetchBotStatus()
    })
    expect(backendGetMock).toHaveBeenCalledTimes(1)
  })

  it('force 会绕过缓存并刷新为离线状态', async () => {
    const online = createBotStatus({ running: true })
    const offline = createBotStatus({ running: false, uptime: 0 })
    backendGetMock.mockResolvedValueOnce(online).mockResolvedValueOnce(offline)
    const useBotStatus = await loadUseBotStatus()
    const { result } = renderHook(() => useBotStatus())

    await act(async () => {
      await result.current.fetchBotStatus()
    })
    expect(result.current.botStatus?.running).toBe(true)

    await act(async () => {
      await result.current.fetchBotStatus(true)
    })
    expect(backendGetMock).toHaveBeenCalledTimes(2)
    expect(result.current.botStatus).toEqual(offline)
  })

  it('缓存过期后非强制刷新会重新请求', async () => {
    const nowSpy = vi.spyOn(Date, 'now').mockReturnValue(1_000)
    const firstStatus = createBotStatus({ running: true, version: 'old' })
    const freshStatus = createBotStatus({ running: true, version: 'new' })
    backendGetMock.mockResolvedValueOnce(firstStatus).mockResolvedValueOnce(freshStatus)
    const useBotStatus = await loadUseBotStatus()
    const { result } = renderHook(() => useBotStatus())

    await act(async () => {
      await result.current.fetchBotStatus()
    })
    expect(result.current.botStatus?.version).toBe('old')

    nowSpy.mockReturnValue(1_000 + 30_001)
    await act(async () => {
      await result.current.fetchBotStatus()
    })
    expect(backendGetMock).toHaveBeenCalledTimes(2)
    expect(result.current.botStatus).toEqual(freshStatus)
  })

  it('已有缓存时后续失败保留旧状态', async () => {
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    const online = createBotStatus({ running: true, version: 'keep' })
    backendGetMock.mockResolvedValueOnce(online).mockRejectedValueOnce(new Error('刷新失败'))
    const useBotStatus = await loadUseBotStatus()
    const { result } = renderHook(() => useBotStatus())

    await act(async () => {
      await result.current.fetchBotStatus()
    })
    await act(async () => {
      await result.current.fetchBotStatus(true)
    })

    expect(result.current.botStatus).toEqual(online)
    expect(result.current.isBotStatusLoading).toBe(false)
    expect(errorSpy).toHaveBeenCalledWith('获取机器人状态失败:', expect.any(Error))
  })

  it('进行中的请求会合并，即使 force 也不会重复打接口', async () => {
    const deferred = createDeferred<BotStatus>()
    backendGetMock.mockReturnValue(deferred.promise)
    const useBotStatus = await loadUseBotStatus()
    const { result } = renderHook(() => useBotStatus())

    let first: Promise<void> = Promise.resolve()
    let second: Promise<void> = Promise.resolve()
    act(() => {
      first = result.current.fetchBotStatus()
      second = result.current.fetchBotStatus(true)
    })

    expect(first).toBe(second)
    expect(backendGetMock).toHaveBeenCalledTimes(1)

    const online = createBotStatus({ running: true })
    await act(async () => {
      deferred.resolve(online)
      await first
    })
    expect(result.current.botStatus).toEqual(online)
  })

  it('卸载后完成的请求不会写入状态，也不会污染缓存', async () => {
    const deferred = createDeferred<BotStatus>()
    backendGetMock.mockReturnValueOnce(deferred.promise)
    const useBotStatus = await loadUseBotStatus()
    const first = renderHook(() => useBotStatus())

    let pending: Promise<void> = Promise.resolve()
    act(() => {
      pending = first.result.current.fetchBotStatus()
    })
    first.unmount()

    await act(async () => {
      deferred.resolve(createBotStatus({ running: true, version: 'late' }))
      await pending
    })

    const second = renderHook(() => useBotStatus())
    expect(second.result.current.botStatus).toBeNull()
    expect(second.result.current.isBotStatusLoading).toBe(true)
  })

  it('页面可见时 30 秒后自动强制刷新', async () => {
    vi.useFakeTimers()
    setVisibilityState('visible')
    backendGetMock.mockResolvedValue(createBotStatus({ running: true }))
    const useBotStatus = await loadUseBotStatus()
    renderHook(() => useBotStatus())
    expect(backendGetMock).not.toHaveBeenCalled()

    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000)
    })
    expect(backendGetMock).toHaveBeenCalledTimes(1)
    expect(backendGetMock).toHaveBeenCalledWith('/api/webui/system/status')
  })

  it('页面隐藏时暂停轮询，恢复可见后立即刷新', async () => {
    vi.useFakeTimers()
    setVisibilityState('visible')
    backendGetMock.mockResolvedValue(createBotStatus({ running: true }))
    const useBotStatus = await loadUseBotStatus()
    renderHook(() => useBotStatus())

    setVisibilityState('hidden')
    act(() => {
      document.dispatchEvent(new Event('visibilitychange'))
    })

    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000)
    })
    expect(backendGetMock).not.toHaveBeenCalled()

    setVisibilityState('visible')
    await act(async () => {
      document.dispatchEvent(new Event('visibilitychange'))
      await Promise.resolve()
    })
    expect(backendGetMock).toHaveBeenCalledTimes(1)
  })

  it('隐藏状态下超时回调不会发请求', async () => {
    vi.useFakeTimers()
    setVisibilityState('visible')
    backendGetMock.mockResolvedValue(createBotStatus({ running: false }))
    const useBotStatus = await loadUseBotStatus()
    renderHook(() => useBotStatus())

    setVisibilityState('hidden')
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000)
    })
    expect(backendGetMock).not.toHaveBeenCalled()
  })

  it('窗口聚焦且页面可见时强制刷新；隐藏时忽略聚焦', async () => {
    setVisibilityState('hidden')
    backendGetMock.mockResolvedValue(createBotStatus({ running: true }))
    const useBotStatus = await loadUseBotStatus()
    renderHook(() => useBotStatus())

    await act(async () => {
      window.dispatchEvent(new Event('focus'))
      await Promise.resolve()
    })
    expect(backendGetMock).not.toHaveBeenCalled()

    setVisibilityState('visible')
    await act(async () => {
      window.dispatchEvent(new Event('focus'))
      await Promise.resolve()
    })
    expect(backendGetMock).toHaveBeenCalledTimes(1)
  })

  it('卸载后取消已安排的轮询', async () => {
    vi.useFakeTimers()
    setVisibilityState('visible')
    backendGetMock.mockResolvedValue(createBotStatus({ running: true }))
    const useBotStatus = await loadUseBotStatus()
    const { unmount } = renderHook(() => useBotStatus())
    unmount()

    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000)
    })
    expect(backendGetMock).not.toHaveBeenCalled()
  })

  it('初始即为隐藏时不会安排轮询', async () => {
    vi.useFakeTimers()
    setVisibilityState('hidden')
    backendGetMock.mockResolvedValue(createBotStatus({ running: true }))
    const useBotStatus = await loadUseBotStatus()
    renderHook(() => useBotStatus())

    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000)
    })
    expect(backendGetMock).not.toHaveBeenCalled()
  })
})
