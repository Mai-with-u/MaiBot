import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { LogWebSocketManager, type LogEntry } from '../log-websocket'

interface TestWsEvent {
  data: Record<string, unknown>
  domain: string
  event: string
}

const authMocks = vi.hoisted(() => ({
  checkAuthStatus: vi.fn(async () => true),
}))
const settingsState = vi.hoisted(() => ({
  logCacheSize: 3,
}))
const wsMocks = vi.hoisted(() => ({
  addEventListener: vi.fn(),
  onConnectionChange: vi.fn(),
  onReconnect: vi.fn(),
  subscribe: vi.fn(),
  unsubscribe: vi.fn(),
}))

vi.mock('../auth', () => authMocks)
vi.mock('../settings-manager', () => ({
  getSetting: (key: string) => {
    if (key === 'logCacheSize') {
      return settingsState.logCacheSize
    }
    throw new Error(`未处理的设置项: ${key}`)
  },
}))
vi.mock('../unified-ws', () => ({
  unifiedWsClient: wsMocks,
}))

function createLog(id: string): LogEntry {
  return {
    id,
    level: 'INFO',
    message: `message-${id}`,
    module: 'test',
    timestamp: '2026-07-24 12:00:00',
  }
}

function createDeferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((nextResolve, nextReject) => {
    resolve = nextResolve
    reject = nextReject
  })
  return { promise, reject, resolve }
}

async function flushMicrotasks(): Promise<void> {
  for (let index = 0; index < 6; index += 1) {
    await Promise.resolve()
  }
}

describe('LogWebSocketManager', () => {
  let connectionListener: ((connected: boolean) => void) | null
  let eventListener: ((message: TestWsEvent) => void) | null
  let reconnectListener: (() => void) | null

  beforeEach(() => {
    vi.useFakeTimers()
    vi.clearAllMocks()
    connectionListener = null
    eventListener = null
    reconnectListener = null
    settingsState.logCacheSize = 3
    authMocks.checkAuthStatus.mockResolvedValue(true)
    wsMocks.addEventListener.mockImplementation((listener: (message: TestWsEvent) => void) => {
      eventListener = listener
      return vi.fn()
    })
    wsMocks.onConnectionChange.mockImplementation((listener: (connected: boolean) => void) => {
      connectionListener = listener
      listener(true)
      return vi.fn()
    })
    wsMocks.onReconnect.mockImplementation((listener: () => void) => {
      reconnectListener = listener
      return vi.fn()
    })
    wsMocks.subscribe.mockResolvedValue({})
    wsMocks.unsubscribe.mockResolvedValue({})
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('按批次通知消费者，并通过 ID 索引去重和淘汰旧日志', async () => {
    const manager = new LogWebSocketManager()
    const callback = vi.fn()
    const stopListening = manager.onLog(callback)
    await manager.connect()

    expect(connectionListener).not.toBeNull()
    expect(eventListener).not.toBeNull()
    for (const id of ['a', 'a', 'b', 'c', 'd']) {
      eventListener?.({
        data: { entry: createLog(id) },
        domain: 'logs',
        event: 'entry',
      })
    }

    expect(callback).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(49)
    expect(callback).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(1)

    expect(callback).toHaveBeenCalledTimes(1)
    expect(manager.getAllLogs().map((log) => log.id)).toEqual(['b', 'c', 'd'])
    expect(callback).toHaveBeenLastCalledWith(manager.getAllLogs())

    stopListening()
    expect(wsMocks.unsubscribe).toHaveBeenCalledWith('logs', 'main')
  })

  it('订阅收尾期间重新出现消费者时会重新订阅', async () => {
    const firstSubscribe = createDeferred<Record<string, unknown>>()
    const firstUnsubscribe = createDeferred<Record<string, unknown>>()
    wsMocks.subscribe.mockImplementationOnce(() => firstSubscribe.promise).mockResolvedValue({})
    wsMocks.unsubscribe.mockImplementationOnce(() => firstUnsubscribe.promise).mockResolvedValue({})

    const manager = new LogWebSocketManager()
    const stopFirstListener = manager.onLog(vi.fn())
    const firstConnect = manager.connect()
    await flushMicrotasks()
    expect(wsMocks.subscribe).toHaveBeenCalledTimes(1)

    stopFirstListener()
    firstSubscribe.resolve({})
    await flushMicrotasks()
    // disconnect 先撤销底层期望订阅；迟到的 subscribe ACK 到达后再确认撤销一次。
    expect(wsMocks.unsubscribe).toHaveBeenCalledTimes(2)

    const stopSecondListener = manager.onLog(vi.fn())
    firstUnsubscribe.resolve({})
    await firstConnect
    await flushMicrotasks()

    expect(wsMocks.subscribe).toHaveBeenCalledTimes(2)
    stopSecondListener()
  })

  it('订阅 ACK 失败时清理底层登记并重试', async () => {
    const failedSubscribe = createDeferred<Record<string, unknown>>()
    wsMocks.subscribe.mockImplementationOnce(() => failedSubscribe.promise).mockResolvedValue({})

    const manager = new LogWebSocketManager()
    const stopListening = manager.onLog(vi.fn())
    await flushMicrotasks()
    failedSubscribe.reject(new Error('连接中断'))
    await flushMicrotasks()

    expect(wsMocks.unsubscribe).toHaveBeenCalledWith('logs', 'main')
    expect(wsMocks.subscribe).toHaveBeenCalledTimes(1)

    await vi.advanceTimersByTimeAsync(3000)
    await flushMicrotasks()
    expect(wsMocks.subscribe).toHaveBeenCalledTimes(2)

    stopListening()
  })

  it('底层重连后显式确认日志订阅', async () => {
    const manager = new LogWebSocketManager()
    const stopListening = manager.onLog(vi.fn())
    await manager.connect()
    expect(wsMocks.subscribe).toHaveBeenCalledTimes(1)

    reconnectListener?.()
    await flushMicrotasks()
    expect(wsMocks.subscribe).toHaveBeenCalledTimes(2)

    stopListening()
  })

  it('snapshot 用数组条目替换缓存，非数组条目清空；忽略非 logs 域和缺少 entry 的事件', async () => {
    const manager = new LogWebSocketManager()
    const callback = vi.fn()
    const stopListening = manager.onLog(callback)
    await manager.connect()

    eventListener?.({
      data: {
        entries: [
          createLog('old-1'),
          createLog('keep-1'),
          { ...createLog('dup'), message: 'first' },
          createLog('keep-2'),
          { ...createLog('dup'), message: 'last' },
        ],
      },
      domain: 'logs',
      event: 'snapshot',
    })
    await vi.advanceTimersByTimeAsync(50)
    // maxCacheSize=3 只保留快照末尾 3 条，重复 ID 以最后一次为准并挪到末尾
    expect(manager.getAllLogs().map((log) => log.id)).toEqual(['keep-2', 'dup'])
    expect(manager.getAllLogs()[1]?.message).toBe('last')

    eventListener?.({
      data: { entry: createLog('ignored-domain') },
      domain: 'chat',
      event: 'entry',
    })
    eventListener?.({
      data: {},
      domain: 'logs',
      event: 'entry',
    })
    expect(manager.getAllLogs().map((log) => log.id)).toEqual(['keep-2', 'dup'])

    eventListener?.({
      data: { entries: { not: 'array' } },
      domain: 'logs',
      event: 'snapshot',
    })
    await vi.advanceTimersByTimeAsync(50)
    expect(manager.getAllLogs()).toEqual([])

    stopListening()
  })

  it('缓存上限小于等于 0 时 snapshot 清空缓存且不再追加新日志', async () => {
    const manager = new LogWebSocketManager()
    const stopListening = manager.onLog(vi.fn())
    await manager.connect()

    eventListener?.({
      data: { entry: createLog('old') },
      domain: 'logs',
      event: 'entry',
    })
    await vi.advanceTimersByTimeAsync(50)
    expect(manager.getAllLogs()).toHaveLength(1)

    settingsState.logCacheSize = 0
    eventListener?.({
      data: { entry: createLog('new') },
      domain: 'logs',
      event: 'entry',
    })
    expect(manager.getAllLogs().map((log) => log.id)).toEqual(['old'])

    eventListener?.({
      data: { entries: [createLog('snap')] },
      domain: 'logs',
      event: 'snapshot',
    })
    expect(manager.getAllLogs()).toEqual([])

    stopListening()
  })

  it('未登录、/auth 页面或没有消费者时 connect 不会订阅', async () => {
    const previousPath = `${window.location.pathname}${window.location.search}`
    const manager = new LogWebSocketManager()

    await manager.connect()
    expect(wsMocks.subscribe).not.toHaveBeenCalled()

    authMocks.checkAuthStatus.mockResolvedValue(false)
    const stopUnauthed = manager.onLog(vi.fn())
    await manager.connect()
    expect(wsMocks.subscribe).not.toHaveBeenCalled()
    stopUnauthed()
    authMocks.checkAuthStatus.mockResolvedValue(true)

    window.history.pushState({}, '', '/auth')
    const stopOnAuth = manager.onLog(vi.fn())
    await manager.connect()
    expect(wsMocks.subscribe).not.toHaveBeenCalled()
    stopOnAuth()
    window.history.pushState({}, '', previousPath || '/')
  })

  it('订阅已激活时再次 connect 直接返回', async () => {
    const manager = new LogWebSocketManager()
    const stopListening = manager.onLog(vi.fn())
    await manager.connect()
    wsMocks.subscribe.mockClear()

    await manager.connect()
    expect(wsMocks.subscribe).not.toHaveBeenCalled()

    stopListening()
  })

  it('传输层恢复且本地仍有消费者时自动重新订阅', async () => {
    const manager = new LogWebSocketManager()
    const stopListening = manager.onLog(vi.fn())
    await manager.connect()
    expect(wsMocks.subscribe).toHaveBeenCalledTimes(1)

    manager.disconnect()
    wsMocks.subscribe.mockClear()
    connectionListener?.(true)
    await flushMicrotasks()
    expect(wsMocks.subscribe).toHaveBeenCalledTimes(1)

    stopListening()
  })

  it('没有日志消费者时重连回调直接返回', async () => {
    const manager = new LogWebSocketManager()
    const stopListening = manager.onLog(vi.fn())
    await manager.connect()
    stopListening()
    wsMocks.subscribe.mockClear()

    reconnectListener?.()
    await flushMicrotasks()
    expect(wsMocks.subscribe).not.toHaveBeenCalled()
  })

  it('onConnectionChange 立即回传当前状态，退订后不再接收后续变化', async () => {
    const manager = new LogWebSocketManager()
    const callback = vi.fn()
    const stopStatus = manager.onConnectionChange(callback)
    expect(callback).toHaveBeenCalledWith(false)
    expect(manager.getConnectionStatus()).toBe(false)

    const stopListening = manager.onLog(vi.fn())
    await manager.connect()
    expect(callback).toHaveBeenCalledWith(true)
    expect(manager.getConnectionStatus()).toBe(true)

    stopStatus()
    manager.disconnect()
    expect(callback).toHaveBeenCalledTimes(2)
    expect(manager.getConnectionStatus()).toBe(false)

    stopListening()
  })

  it('clearLogs 立即通知空列表并取消尚未触发的批量通知', async () => {
    const manager = new LogWebSocketManager()
    const callback = vi.fn()
    const stopListening = manager.onLog(callback)
    await manager.connect()

    eventListener?.({
      data: { entry: createLog('pending') },
      domain: 'logs',
      event: 'entry',
    })
    manager.clearLogs()
    expect(manager.getAllLogs()).toEqual([])
    expect(callback).toHaveBeenCalledWith([])

    await vi.advanceTimersByTimeAsync(50)
    expect(callback).toHaveBeenCalledTimes(1)

    stopListening()
  })

  it('日志回调或连接回调抛错时不影响其它回调', async () => {
    const consoleErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    const manager = new LogWebSocketManager()
    const throwingLog = vi.fn(() => {
      throw new Error('日志回调失败')
    })
    const healthyLog = vi.fn()
    const throwingConn = vi.fn()
    throwingConn.mockImplementationOnce(() => undefined)
    throwingConn.mockImplementation(() => {
      throw new Error('连接回调失败')
    })
    const healthyConn = vi.fn()

    manager.onConnectionChange(throwingConn)
    manager.onConnectionChange(healthyConn)
    manager.onLog(throwingLog)
    manager.onLog(healthyLog)
    await manager.connect()

    eventListener?.({
      data: { entry: createLog('cb-1') },
      domain: 'logs',
      event: 'entry',
    })
    await vi.advanceTimersByTimeAsync(50)

    expect(healthyLog).toHaveBeenCalledTimes(1)
    expect(healthyConn).toHaveBeenCalledWith(true)
    expect(consoleErrorSpy).toHaveBeenCalledWith('日志回调执行失败:', expect.any(Error))
    expect(consoleErrorSpy).toHaveBeenCalledWith('连接状态回调执行失败:', expect.any(Error))
  })

  it('取消订阅失败时记录错误；重复退订同一日志监听器是空操作', async () => {
    const consoleErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    wsMocks.unsubscribe.mockRejectedValue(new Error('取消订阅失败'))
    const manager = new LogWebSocketManager()
    const stopListening = manager.onLog(vi.fn())
    await manager.connect()

    stopListening()
    stopListening()
    await flushMicrotasks()

    expect(consoleErrorSpy).toHaveBeenCalledWith('取消日志流订阅失败:', expect.any(Error))
  })

  it('并发 connect 共享进行中的订阅 Promise', async () => {
    const pendingSubscribe = createDeferred<Record<string, unknown>>()
    wsMocks.subscribe.mockImplementationOnce(() => pendingSubscribe.promise).mockResolvedValue({})
    const manager = new LogWebSocketManager()
    const stopListening = manager.onLog(vi.fn())

    const firstConnect = manager.connect()
    await flushMicrotasks()
    const secondConnect = manager.connect()
    expect(wsMocks.subscribe).toHaveBeenCalledTimes(1)

    pendingSubscribe.resolve({})
    await Promise.all([firstConnect, secondConnect])
    expect(wsMocks.subscribe).toHaveBeenCalledTimes(1)

    stopListening()
  })
})
