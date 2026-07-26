import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { WsEventEnvelope } from '../unified-ws'

// maisakaMonitorClient 是模块级单例（initialized/replayCursor/subscriptionActive 等内部状态），
// 因此每个用例都通过 vi.resetModules + 动态 import 获取全新实例，避免跨用例状态污染。
type MonitorModule = typeof import('../maisaka-monitor-client')

const wsMocks = vi.hoisted(() => ({
  addEventListener: vi.fn(),
  subscribe: vi.fn(),
  unsubscribe: vi.fn(),
  updateSubscriptionData: vi.fn(),
}))

vi.mock('../unified-ws', () => ({
  unifiedWsClient: wsMocks,
}))

/** 创建一个可手动控制 resolve/reject 的 Promise */
function createDeferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((nextResolve, nextReject) => {
    resolve = nextResolve
    reject = nextReject
  })
  return { promise, reject, resolve }
}

/** 连续排空若干轮微任务，让链式 then/finally 全部执行完 */
async function flushMicrotasks(): Promise<void> {
  for (let index = 0; index < 6; index += 1) {
    await Promise.resolve()
  }
}

describe('maisakaMonitorClient', () => {
  let client: MonitorModule['maisakaMonitorClient']
  let capturedWsListener: ((message: WsEventEnvelope) => void) | null

  beforeEach(async () => {
    vi.resetModules()
    capturedWsListener = null
    wsMocks.addEventListener.mockImplementation((listener: (message: WsEventEnvelope) => void) => {
      capturedWsListener = listener
      return vi.fn()
    })
    wsMocks.subscribe.mockResolvedValue({})
    wsMocks.unsubscribe.mockResolvedValue({})

    const monitorModule: MonitorModule = await import('../maisaka-monitor-client')
    client = monitorModule.maisakaMonitorClient
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('首次订阅时注册底层事件监听并携带初始补发参数订阅 maisaka_monitor 主题', async () => {
    await client.subscribe(vi.fn())

    expect(wsMocks.addEventListener).toHaveBeenCalledTimes(1)
    expect(wsMocks.subscribe).toHaveBeenCalledTimes(1)
    expect(wsMocks.subscribe).toHaveBeenCalledWith('maisaka_monitor', 'main', {
      since_event_id: 0,
      replay_limit: 1000,
    })
  })

  it('只转发 maisaka_monitor 域的事件，并把 event/data 映射为监控事件', async () => {
    const listener = vi.fn()
    await client.subscribe(listener)
    expect(capturedWsListener).not.toBeNull()

    // 其它域的事件应被忽略
    capturedWsListener?.({
      op: 'event',
      domain: 'logs',
      event: 'entry',
      data: { message: '无关日志' },
    })
    expect(listener).not.toHaveBeenCalled()

    const sessionStartData = {
      session_id: 'session-1',
      session_name: '测试群聊',
      timestamp: 1_753_500_000,
    }
    capturedWsListener?.({
      op: 'event',
      domain: 'maisaka_monitor',
      event: 'session.start',
      data: sessionStartData,
    })

    expect(listener).toHaveBeenCalledTimes(1)
    expect(listener).toHaveBeenCalledWith({
      type: 'session.start',
      data: sessionStartData,
    })
  })

  it('某个监听器抛错时记录错误且不影响其余监听器', async () => {
    const consoleErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    const throwingListener = vi.fn(() => {
      throw new Error('监听器内部错误')
    })
    const healthyListener = vi.fn()
    await client.subscribe(throwingListener)
    await client.subscribe(healthyListener)

    const stageData = { session_id: 'session-2', stage: '规划中', timestamp: 1 }
    capturedWsListener?.({
      op: 'event',
      domain: 'maisaka_monitor',
      event: 'stage.status',
      data: stageData,
    })

    expect(throwingListener).toHaveBeenCalledTimes(1)
    expect(healthyListener).toHaveBeenCalledTimes(1)
    expect(healthyListener).toHaveBeenCalledWith({ type: 'stage.status', data: stageData })
    expect(consoleErrorSpy).toHaveBeenCalledWith(
      'MaiSaka 监控事件监听器执行失败:',
      expect.any(Error),
    )
  })

  it('并发首次订阅共享同一个订阅 Promise，底层 subscribe 只调用一次', async () => {
    const pendingSubscribe = createDeferred<Record<string, unknown>>()
    wsMocks.subscribe.mockImplementation(() => pendingSubscribe.promise)

    const firstSubscribe = client.subscribe(vi.fn())
    const secondSubscribe = client.subscribe(vi.fn())
    await flushMicrotasks()
    expect(wsMocks.subscribe).toHaveBeenCalledTimes(1)

    pendingSubscribe.resolve({})
    await Promise.all([firstSubscribe, secondSubscribe])

    // 两个订阅者都通过 subscriptionPromise 拿到成功结果，不会重复订阅或触发补发
    expect(wsMocks.subscribe).toHaveBeenCalledTimes(1)
    expect(wsMocks.addEventListener).toHaveBeenCalledTimes(1)
  })

  it('订阅已激活后新增订阅者会按当前游标触发补发订阅', async () => {
    await client.subscribe(vi.fn())
    expect(wsMocks.subscribe).toHaveBeenLastCalledWith('maisaka_monitor', 'main', {
      since_event_id: 0,
      replay_limit: 1000,
    })

    client.updateReplayCursor(42)
    await client.subscribe(vi.fn())

    // 游标推进后 replay_limit 切换为增量补发上限 10000
    expect(wsMocks.subscribe).toHaveBeenCalledTimes(2)
    expect(wsMocks.subscribe).toHaveBeenLastCalledWith('maisaka_monitor', 'main', {
      since_event_id: 42,
      replay_limit: 10000,
    })
  })

  it('updateReplayCursor 只在游标前进时生效并同步底层订阅数据', () => {
    client.updateReplayCursor(7.9)
    expect(wsMocks.updateSubscriptionData).toHaveBeenCalledTimes(1)
    expect(wsMocks.updateSubscriptionData).toHaveBeenCalledWith('maisaka_monitor', 'main', {
      since_event_id: 7,
      replay_limit: 10000,
    })

    // 等于或小于当前游标、非有限数值均不应触发更新
    client.updateReplayCursor(7)
    client.updateReplayCursor(3)
    client.updateReplayCursor(Number.NaN)
    client.updateReplayCursor(Number.POSITIVE_INFINITY)
    expect(wsMocks.updateSubscriptionData).toHaveBeenCalledTimes(1)
  })

  it('setInitialReplayCursor 忽略非法值且不回退游标，并影响首次订阅参数', async () => {
    client.setInitialReplayCursor(-1)
    client.setInitialReplayCursor(Number.NaN)
    client.setInitialReplayCursor(8.7)
    // 已推进到 8，较小的值不会回退游标
    client.setInitialReplayCursor(3)

    await client.subscribe(vi.fn())
    expect(wsMocks.subscribe).toHaveBeenCalledWith('maisaka_monitor', 'main', {
      since_event_id: 8,
      replay_limit: 10000,
    })
  })

  it('最后一个监听器移除后延迟 200ms 才真正退订，随后订阅会重建', async () => {
    vi.useFakeTimers()
    const stopListening = await client.subscribe(vi.fn())

    await stopListening()
    await vi.advanceTimersByTimeAsync(199)
    expect(wsMocks.unsubscribe).not.toHaveBeenCalled()

    await vi.advanceTimersByTimeAsync(1)
    expect(wsMocks.unsubscribe).toHaveBeenCalledTimes(1)
    expect(wsMocks.unsubscribe).toHaveBeenCalledWith('maisaka_monitor', 'main')

    // 真正退订后再次订阅会重新建立订阅（初始游标参数）
    await client.subscribe(vi.fn())
    expect(wsMocks.subscribe).toHaveBeenCalledTimes(2)
    expect(wsMocks.subscribe).toHaveBeenLastCalledWith('maisaka_monitor', 'main', {
      since_event_id: 0,
      replay_limit: 1000,
    })
  })

  it('延迟退订窗口内重新订阅会取消退订（StrictMode 快速卸载重挂载场景）', async () => {
    vi.useFakeTimers()
    const stopListening = await client.subscribe(vi.fn())

    await stopListening()
    // 200ms 窗口内新订阅者出现：取消延迟退订，仅触发一次补发
    await client.subscribe(vi.fn())
    await vi.advanceTimersByTimeAsync(300)

    expect(wsMocks.unsubscribe).not.toHaveBeenCalled()
    expect(wsMocks.subscribe).toHaveBeenCalledTimes(2)
  })

  it('仍有其他监听器时移除某个监听器不会退订，且被移除者不再收到事件', async () => {
    vi.useFakeTimers()
    const removedListener = vi.fn()
    const remainingListener = vi.fn()
    const stopRemovedListener = await client.subscribe(removedListener)
    await client.subscribe(remainingListener)

    await stopRemovedListener()
    await vi.advanceTimersByTimeAsync(300)
    expect(wsMocks.unsubscribe).not.toHaveBeenCalled()

    const messageData = { session_id: 'session-3', speaker_name: '麦麦', timestamp: 2 }
    capturedWsListener?.({
      op: 'event',
      domain: 'maisaka_monitor',
      event: 'message.sent',
      data: messageData,
    })

    expect(removedListener).not.toHaveBeenCalled()
    expect(remainingListener).toHaveBeenCalledTimes(1)
    expect(remainingListener).toHaveBeenCalledWith({ type: 'message.sent', data: messageData })
  })
})
