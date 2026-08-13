import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { memoryProgressClient as MemoryProgressClientType } from './memory-progress-client'
import type { WsEventEnvelope } from './unified-ws'

interface TestWsEvent {
  data?: Record<string, unknown>
  domain: string
  event: string
  op: 'event'
  topic?: string
}

const wsMocks = vi.hoisted(() => ({
  addEventListener: vi.fn(),
  subscribe: vi.fn(),
  unsubscribe: vi.fn(),
}))

vi.mock('./unified-ws', () => ({
  unifiedWsClient: wsMocks,
}))

async function loadClient(): Promise<typeof MemoryProgressClientType> {
  const module = await import('./memory-progress-client')
  return module.memoryProgressClient
}

describe('memoryProgressClient', () => {
  let eventListener: ((message: TestWsEvent) => void) | null

  beforeEach(() => {
    vi.resetModules()
    eventListener = null
    wsMocks.addEventListener.mockImplementation((listener: (message: TestWsEvent) => void) => {
      eventListener = listener
      return vi.fn()
    })
    wsMocks.subscribe.mockResolvedValue({})
    wsMocks.unsubscribe.mockResolvedValue({})
  })

  afterEach(() => {
    vi.clearAllMocks()
  })

  it('首个订阅者注册事件监听并订阅全部已知 topic，后续订阅者跳过已激活 topic', async () => {
    const client = await loadClient()

    await client.subscribe(vi.fn())
    await client.subscribe(vi.fn(), ['import_progress', 'delete_progress'])

    expect(wsMocks.addEventListener).toHaveBeenCalledTimes(1)
    expect(wsMocks.subscribe).toHaveBeenCalledTimes(3)
    expect(wsMocks.subscribe).toHaveBeenCalledWith('memory', 'import_progress')
    expect(wsMocks.subscribe).toHaveBeenCalledWith('memory', 'delete_progress')
    expect(wsMocks.subscribe).toHaveBeenCalledWith('memory', 'feedback_progress')
  })

  it('只分发 memory 域且 topic 已知的事件，缺失 data 时回落为空对象', async () => {
    const client = await loadClient()
    const listener = vi.fn()
    await client.subscribe(listener)
    expect(eventListener).not.toBeNull()

    eventListener?.({
      op: 'event',
      domain: 'logs',
      event: 'progress',
      topic: 'import_progress',
      data: { stage: 'other' },
    })
    eventListener?.({
      op: 'event',
      domain: 'memory',
      event: 'progress',
      topic: 'unknown_topic',
      data: { stage: 'ignored' },
    })
    expect(listener).not.toHaveBeenCalled()

    eventListener?.({
      op: 'event',
      domain: 'memory',
      event: 'tick',
      topic: 'import_progress',
    } as WsEventEnvelope)

    expect(listener).toHaveBeenCalledTimes(1)
    expect(listener).toHaveBeenCalledWith({
      topic: 'import_progress',
      event: 'tick',
      data: {},
    })
  })

  it('某个监听器抛错时其余监听器仍能收到进度', async () => {
    const consoleErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    const client = await loadClient()
    const throwing = vi.fn(() => {
      throw new Error('监听器内部错误')
    })
    const normal = vi.fn()
    await client.subscribe(throwing)
    await client.subscribe(normal)

    eventListener?.({
      op: 'event',
      domain: 'memory',
      event: 'progress',
      topic: 'delete_progress',
      data: { deleted: 1 },
    })

    expect(throwing).toHaveBeenCalledTimes(1)
    expect(normal).toHaveBeenCalledTimes(1)
    expect(normal).toHaveBeenCalledWith({
      topic: 'delete_progress',
      event: 'progress',
      data: { deleted: 1 },
    })
    expect(consoleErrorSpy).toHaveBeenCalledWith('长期记忆进度监听器执行失败:', expect.any(Error))
  })

  it('订阅单个 topic 失败时只告警不抛错，成功的 topic 仍会激活', async () => {
    const consoleWarnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})
    wsMocks.subscribe
      .mockRejectedValueOnce(new Error('topic 未实现'))
      .mockResolvedValue({})
    const client = await loadClient()

    const unsubscribe = await client.subscribe(vi.fn(), ['import_progress', 'delete_progress'])

    expect(consoleWarnSpy).toHaveBeenCalledWith(
      '订阅长期记忆 topic 失败（将退化到轮询兜底）: import_progress',
      expect.any(Error)
    )
    expect(wsMocks.subscribe).toHaveBeenCalledTimes(2)

    await unsubscribe()
    // 失败的 topic 不会进入 activeTopics，退订时只清理成功订阅的那一个
    expect(wsMocks.unsubscribe).toHaveBeenCalledTimes(1)
    expect(wsMocks.unsubscribe).toHaveBeenCalledWith('memory', 'delete_progress')
  })

  it('最后一个订阅者退订时撤销底层订阅，退订失败只告警；再次订阅会重建', async () => {
    const consoleWarnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})
    wsMocks.unsubscribe.mockRejectedValueOnce(new Error('退订失败')).mockResolvedValue({})
    const client = await loadClient()
    const unsubscribeFirst = await client.subscribe(vi.fn())
    const unsubscribeSecond = await client.subscribe(vi.fn())

    await unsubscribeFirst()
    expect(wsMocks.unsubscribe).not.toHaveBeenCalled()

    await unsubscribeSecond()
    expect(wsMocks.unsubscribe).toHaveBeenCalledTimes(3)
    expect(consoleWarnSpy).toHaveBeenCalledWith(
      '取消订阅长期记忆 topic 失败: import_progress',
      expect.any(Error)
    )

    await client.subscribe(vi.fn())
    expect(wsMocks.subscribe).toHaveBeenCalledTimes(6)
  })

  it('退订后的监听器不再收到事件，其余监听器仍可接收', async () => {
    const client = await loadClient()
    const removed = vi.fn()
    const kept = vi.fn()
    const unsubscribeRemoved = await client.subscribe(removed)
    await client.subscribe(kept)

    await unsubscribeRemoved()
    eventListener?.({
      op: 'event',
      domain: 'memory',
      event: 'progress',
      topic: 'feedback_progress',
      data: { ok: true },
    })

    expect(removed).not.toHaveBeenCalled()
    expect(kept).toHaveBeenCalledTimes(1)
  })
})
