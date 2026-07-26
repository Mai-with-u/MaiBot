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
      return 3
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
})
