import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { PluginLoadProgress } from '@/lib/plugin-api/types'
import type { pluginProgressClient as PluginProgressClientType } from '../plugin-progress-client'

interface TestWsEvent {
  op: 'event'
  domain: string
  event: string
  data: Record<string, unknown>
}

const wsMocks = vi.hoisted(() => ({
  addEventListener: vi.fn(),
  subscribe: vi.fn(),
  unsubscribe: vi.fn(),
}))

vi.mock('../unified-ws', () => ({
  unifiedWsClient: wsMocks,
}))

function createProgress(pluginId: string): PluginLoadProgress {
  return {
    operation: 'install',
    stage: 'loading',
    progress: 50,
    message: `正在安装 ${pluginId}`,
    plugin_id: pluginId,
    total_plugins: 1,
    loaded_plugins: 0,
  }
}

async function loadClient(): Promise<typeof PluginProgressClientType> {
  const module = await import('../plugin-progress-client')
  return module.pluginProgressClient
}

describe('pluginProgressClient', () => {
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

  it('首个订阅者建立底层订阅，后续订阅者复用同一订阅与事件监听', async () => {
    const client = await loadClient()

    await client.subscribe(vi.fn())
    await client.subscribe(vi.fn())

    // 底层事件监听只注册一次，订阅也只发起一次
    expect(wsMocks.addEventListener).toHaveBeenCalledTimes(1)
    expect(wsMocks.subscribe).toHaveBeenCalledTimes(1)
    expect(wsMocks.subscribe).toHaveBeenCalledWith('plugin_progress', 'main')
  })

  it('只分发 plugin_progress 域且携带 progress 的事件', async () => {
    const client = await loadClient()
    const listener = vi.fn()
    await client.subscribe(listener)
    expect(eventListener).not.toBeNull()

    // 其他域的事件被忽略
    eventListener?.({
      op: 'event',
      domain: 'logs',
      event: 'progress',
      data: { progress: createProgress('other') },
    })
    // 缺少 progress 字段的事件被忽略
    eventListener?.({
      op: 'event',
      domain: 'plugin_progress',
      event: 'progress',
      data: {},
    })
    expect(listener).not.toHaveBeenCalled()

    const progress = createProgress('plugin-a')
    eventListener?.({
      op: 'event',
      domain: 'plugin_progress',
      event: 'progress',
      data: { progress },
    })
    expect(listener).toHaveBeenCalledTimes(1)
    expect(listener).toHaveBeenCalledWith(progress)
  })

  it('最后一个订阅者退订时才撤销底层订阅，再次订阅会重新建立', async () => {
    const client = await loadClient()
    const unsubscribeFirst = await client.subscribe(vi.fn())
    const unsubscribeSecond = await client.subscribe(vi.fn())

    // 还有订阅者存在时不撤销底层订阅
    await unsubscribeFirst()
    expect(wsMocks.unsubscribe).not.toHaveBeenCalled()

    // 最后一个订阅者退订后撤销底层订阅
    await unsubscribeSecond()
    expect(wsMocks.unsubscribe).toHaveBeenCalledTimes(1)
    expect(wsMocks.unsubscribe).toHaveBeenCalledWith('plugin_progress', 'main')

    // 再次订阅重新建立底层订阅
    await client.subscribe(vi.fn())
    expect(wsMocks.subscribe).toHaveBeenCalledTimes(2)
  })

  it('退订后的监听器不再收到进度事件', async () => {
    const client = await loadClient()
    const removed = vi.fn()
    const kept = vi.fn()
    const unsubscribeRemoved = await client.subscribe(removed)
    await client.subscribe(kept)

    await unsubscribeRemoved()
    eventListener?.({
      op: 'event',
      domain: 'plugin_progress',
      event: 'progress',
      data: { progress: createProgress('plugin-b') },
    })

    expect(removed).not.toHaveBeenCalled()
    expect(kept).toHaveBeenCalledTimes(1)
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
      domain: 'plugin_progress',
      event: 'progress',
      data: { progress: createProgress('plugin-c') },
    })

    expect(throwing).toHaveBeenCalledTimes(1)
    expect(normal).toHaveBeenCalledTimes(1)
    expect(consoleErrorSpy).toHaveBeenCalledWith('插件进度监听器执行失败:', expect.any(Error))
  })
})
