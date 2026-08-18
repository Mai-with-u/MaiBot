import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { chatWsClient as ChatWsClientType } from '../chat-ws-client'
import type { ConnectionStatus } from '../unified-ws'

interface TestWsEvent {
  data?: Record<string, unknown>
  domain: string
  event?: string
  session?: string
}

const wsMocks = vi.hoisted(() => ({
  addEventListener: vi.fn(),
  call: vi.fn(),
  getStatus: vi.fn(() => 'connected' as ConnectionStatus),
  onConnectionChange: vi.fn(),
  onReconnect: vi.fn(),
  onStatusChange: vi.fn(),
  restart: vi.fn(),
}))

vi.mock('../unified-ws', () => ({
  unifiedWsClient: wsMocks,
}))

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
  for (let index = 0; index < 12; index += 1) {
    await Promise.resolve()
  }
}

async function loadClient(): Promise<typeof ChatWsClientType> {
  const module = await import('../chat-ws-client')
  return module.chatWsClient
}

const defaultPayload = {
  client: { type: 'webui' as const, name: 'MaiBot WebUI' },
  user_id: 'user-1',
  user_name: 'Alice',
}

describe('chatWsClient', () => {
  let eventListener: ((message: TestWsEvent) => void) | null
  let reconnectListener: (() => void) | null
  let connectionListener: ((connected: boolean) => void) | null

  beforeEach(() => {
    vi.resetModules()
    eventListener = null
    reconnectListener = null
    connectionListener = null
    wsMocks.addEventListener.mockImplementation((listener: (message: TestWsEvent) => void) => {
      eventListener = listener
      return vi.fn()
    })
    wsMocks.onReconnect.mockImplementation((listener: () => void) => {
      reconnectListener = listener
      return vi.fn()
    })
    wsMocks.onConnectionChange.mockImplementation((listener: (connected: boolean) => void) => {
      // 只保留 initialize 注册的第一条，避免业务包装器覆盖重连清理逻辑
      if (!connectionListener) {
        connectionListener = listener
      }
      return vi.fn()
    })
    wsMocks.onStatusChange.mockImplementation(() => vi.fn())
    wsMocks.call.mockResolvedValue({})
    wsMocks.getStatus.mockReturnValue('connected')
    wsMocks.restart.mockResolvedValue(undefined)
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('sends image payloads through message.send', async () => {
    const client = await loadClient()

    await client.sendMessage('tab-1', '看看这张图', 'Alice', {
      images: [
        {
          name: 'cat.png',
          mime_type: 'image/png',
          base64: 'iVBORw0KGgo=',
        },
      ],
    })

    expect(wsMocks.call).toHaveBeenCalledWith({
      domain: 'chat',
      method: 'message.send',
      session: 'tab-1',
      data: {
        content: '看看这张图',
        emojis: [],
        images: [
          {
            name: 'cat.png',
            mime_type: 'image/png',
            base64: 'iVBORw0KGgo=',
          },
        ],
        user_name: 'Alice',
      },
    })
  })

  it('未传附件时 message.send 使用空图片与空表情列表', async () => {
    const client = await loadClient()

    await client.sendMessage('tab-2', '你好', 'Bob')

    expect(wsMocks.call).toHaveBeenCalledWith({
      domain: 'chat',
      method: 'message.send',
      session: 'tab-2',
      data: {
        content: '你好',
        emojis: [],
        images: [],
        user_name: 'Bob',
      },
    })
  })

  it('retains a released session for five minutes and restores it when reopened', async () => {
    vi.useFakeTimers()
    const client = await loadClient()
    const payload = {
      client: { type: 'webui' as const, name: 'MaiBot WebUI' },
      user_id: 'retained-user',
      user_name: 'Alice',
    }

    await client.openSession('retained-tab', payload)
    client.releaseSession('retained-tab')
    await vi.advanceTimersByTimeAsync(4 * 60 * 1000)

    expect(wsMocks.call).toHaveBeenCalledTimes(1)

    await client.openSession('retained-tab', payload)
    await vi.advanceTimersByTimeAsync(60 * 1000)

    expect(wsMocks.call).toHaveBeenCalledTimes(2)
    expect(wsMocks.call).toHaveBeenLastCalledWith({
      domain: 'chat',
      method: 'session.open',
      session: 'retained-tab',
      data: {
        ...payload,
        restore: true,
      },
    })
  })

  it('closes a released session after five minutes', async () => {
    vi.useFakeTimers()
    const client = await loadClient()
    const payload = {
      client: { type: 'webui' as const, name: 'MaiBot WebUI' },
      user_id: 'expired-user',
      user_name: 'Bob',
    }

    await client.openSession('expired-tab', payload)
    client.releaseSession('expired-tab')
    await vi.advanceTimersByTimeAsync(5 * 60 * 1000)

    expect(wsMocks.call).toHaveBeenLastCalledWith({
      domain: 'chat',
      method: 'session.close',
      session: 'expired-tab',
      data: {},
    })
  })

  it('同一会话进行中的打开请求会复用 inflight Promise', async () => {
    const client = await loadClient()
    const deferred = createDeferred<Record<string, unknown>>()
    wsMocks.call.mockReturnValueOnce(deferred.promise)

    const firstOpen = client.openSession('shared-tab', defaultPayload)
    const secondOpen = client.openSession('shared-tab', {
      ...defaultPayload,
      user_name: 'Changed',
    })
    await flushMicrotasks()
    expect(wsMocks.call).toHaveBeenCalledTimes(1)

    deferred.resolve({})
    await Promise.all([firstOpen, secondOpen])
    expect(wsMocks.call).toHaveBeenCalledTimes(1)
  })

  it('已打开且负载完全一致时跳过重复 session.open', async () => {
    const client = await loadClient()

    await client.openSession('same-tab', defaultPayload)
    wsMocks.call.mockClear()
    await client.openSession('same-tab', defaultPayload)

    expect(wsMocks.call).not.toHaveBeenCalled()
  })

  it('client 字段不一致或非 client 字段不一致时重新打开会话', async () => {
    const client = await loadClient()

    await client.openSession('diff-tab', defaultPayload)
    wsMocks.call.mockClear()
    await client.openSession('diff-tab', {
      ...defaultPayload,
      client: { type: 'floating', name: 'MaiBot WebUI' },
    })
    expect(wsMocks.call).toHaveBeenCalledTimes(1)

    wsMocks.call.mockClear()
    await client.openSession('diff-tab', {
      client: { type: 'floating', name: 'MaiBot WebUI' },
      user_id: 'user-2',
      user_name: 'Alice',
    })
    expect(wsMocks.call).toHaveBeenCalledTimes(1)
  })

  it('缺失 client 与空对象 client 视为相同；字段顺序不同则视为不同', async () => {
    const client = await loadClient()

    await client.openSession('client-tab', { user_id: 'user-1' })
    wsMocks.call.mockClear()
    await client.openSession('client-tab', { user_id: 'user-1', client: {} })
    expect(wsMocks.call).not.toHaveBeenCalled()

    await client.openSession('order-tab', {
      client: { type: 'webui', name: 'MaiBot WebUI' },
    })
    wsMocks.call.mockClear()
    await client.openSession('order-tab', {
      client: { name: 'MaiBot WebUI', type: 'webui' },
    })
    expect(wsMocks.call).toHaveBeenCalledTimes(1)
  })

  it('未连接时 closeSession 不发送 session.close；call 失败只告警', async () => {
    const consoleWarnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const client = await loadClient()

    wsMocks.getStatus.mockReturnValue('idle')
    await client.closeSession('offline-tab')
    expect(wsMocks.call).not.toHaveBeenCalled()

    wsMocks.getStatus.mockReturnValue('connected')
    wsMocks.call.mockRejectedValueOnce(new Error('关闭失败'))
    await client.closeSession('online-tab')
    expect(consoleWarnSpy).toHaveBeenCalledWith('关闭聊天会话失败 (online-tab):', expect.any(Error))
  })

  it('重复释放同一会话只安排一次关闭；未知会话直接忽略', async () => {
    vi.useFakeTimers()
    const client = await loadClient()

    await client.openSession('once-tab', defaultPayload)
    client.releaseSession('once-tab')
    client.releaseSession('once-tab')
    client.releaseSession('unknown-tab')
    await vi.advanceTimersByTimeAsync(5 * 60 * 1000)

    const closeCalls = wsMocks.call.mock.calls.filter(
      ([payload]) => (payload as { method?: string }).method === 'session.close'
    )
    expect(closeCalls).toHaveLength(1)
    expect(closeCalls[0]?.[0]).toMatchObject({ session: 'once-tab' })
  })

  it('updateNickname 会补丁本地负载并发送 session.update_nickname', async () => {
    const client = await loadClient()

    await client.openSession('nick-tab', defaultPayload)
    await client.updateNickname('nick-tab', 'Carol')
    expect(wsMocks.call).toHaveBeenLastCalledWith({
      domain: 'chat',
      method: 'session.update_nickname',
      session: 'nick-tab',
      data: { user_name: 'Carol' },
    })

    wsMocks.call.mockClear()
    await client.openSession('nick-tab', { ...defaultPayload, user_name: 'Carol' })
    expect(wsMocks.call).not.toHaveBeenCalled()

    await client.updateNickname('missing-tab', 'Dave')
    expect(wsMocks.call).toHaveBeenCalledWith({
      domain: 'chat',
      method: 'session.update_nickname',
      session: 'missing-tab',
      data: { user_name: 'Dave' },
    })
  })

  it('只分发 chat 域且已注册会话的 data，监听器抛错不影响其它监听器', async () => {
    const consoleErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    const client = await loadClient()
    const throwing = vi.fn(() => {
      throw new Error('监听器内部错误')
    })
    const healthy = vi.fn()
    const unsubscribe = client.onSessionMessage('chat-1', throwing)
    client.onSessionMessage('chat-1', healthy)

    eventListener?.({ domain: 'logs', session: 'chat-1', data: { text: 'ignore' } })
    eventListener?.({ domain: 'chat', data: { text: 'no-session' } })
    eventListener?.({ domain: 'chat', session: 'unknown', data: { text: 'skip' } })
    expect(healthy).not.toHaveBeenCalled()

    eventListener?.({ domain: 'chat', session: 'chat-1', data: { text: 'hello' } })
    expect(throwing).toHaveBeenCalledWith({ text: 'hello' })
    expect(healthy).toHaveBeenCalledWith({ text: 'hello' })
    expect(consoleErrorSpy).toHaveBeenCalledWith('聊天会话监听器执行失败:', expect.any(Error))

    unsubscribe()
    unsubscribe()
    eventListener?.({ domain: 'chat', session: 'chat-1', data: { text: 'again' } })
    expect(throwing).toHaveBeenCalledTimes(1)
    expect(healthy).toHaveBeenCalledTimes(2)
  })

  it('最后一个会话监听器退订后删除该会话的监听集合', async () => {
    const client = await loadClient()
    const listener = vi.fn()
    const unsubscribe = client.onSessionMessage('solo-tab', listener)

    unsubscribe()
    unsubscribe()
    eventListener?.({ domain: 'chat', session: 'solo-tab', data: { text: 'gone' } })
    expect(listener).not.toHaveBeenCalled()
  })

  it('重连后清空已打开标记并用 restore 重新打开会话，失败只记日志', async () => {
    const consoleErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    const client = await loadClient()

    await client.openSession('restore-ok', defaultPayload)
    await client.openSession('restore-fail', { user_id: 'fail-user' })
    await client.updateNickname('restore-ok', 'Erin')
    wsMocks.call.mockClear()
    wsMocks.call.mockImplementation(async (payload: { session?: string }) => {
      if (payload.session === 'restore-fail') {
        throw new Error('恢复失败')
      }
      return {}
    })

    reconnectListener?.()
    await vi.waitFor(() => {
      expect(wsMocks.call).toHaveBeenCalledTimes(2)
    })
    expect(wsMocks.call).toHaveBeenCalledWith({
      domain: 'chat',
      method: 'session.open',
      session: 'restore-fail',
      data: { user_id: 'fail-user', restore: true },
    })
    expect(wsMocks.call).toHaveBeenCalledWith({
      domain: 'chat',
      method: 'session.open',
      session: 'restore-ok',
      data: {
        ...defaultPayload,
        user_name: 'Erin',
        restore: true,
      },
    })
    expect(consoleErrorSpy).toHaveBeenCalledWith('恢复聊天会话失败 (restore-fail):', expect.any(Error))

    wsMocks.call.mockClear()
    await client.openSession('restore-ok', { ...defaultPayload, user_name: 'Erin' })
    expect(wsMocks.call).not.toHaveBeenCalled()
  })

  it('连接断开时清空已打开与进行中的打开请求，连上时不清理', async () => {
    const client = await loadClient()
    const deferred = createDeferred<Record<string, unknown>>()
    wsMocks.call.mockReturnValueOnce(deferred.promise)

    const pendingOpen = client.openSession('drop-tab', defaultPayload)
    await flushMicrotasks()
    expect(wsMocks.call).toHaveBeenCalledTimes(1)

    // 断开时清掉 pendingOpens，后续同会话打开会重新发请求
    connectionListener?.(false)
    const secondOpen = client.openSession('drop-tab', defaultPayload)
    await flushMicrotasks()
    expect(wsMocks.call).toHaveBeenCalledTimes(2)

    deferred.resolve({})
    await Promise.all([pendingOpen, secondOpen])

    wsMocks.call.mockClear()
    connectionListener?.(false)
    await client.openSession('drop-tab', defaultPayload)
    expect(wsMocks.call).toHaveBeenCalledTimes(1)

    wsMocks.call.mockClear()
    connectionListener?.(true)
    await client.openSession('drop-tab', defaultPayload)
    expect(wsMocks.call).not.toHaveBeenCalled()
  })

  it('onConnectionChange / onStatusChange / restart 直接转发给统一连接层', async () => {
    const client = await loadClient()
    const connectionCb = vi.fn()
    const statusCb = vi.fn()

    client.onConnectionChange(connectionCb)
    client.onStatusChange(statusCb)
    await client.restart()

    expect(wsMocks.onConnectionChange).toHaveBeenCalledWith(connectionCb)
    expect(wsMocks.onStatusChange).toHaveBeenCalledWith(statusCb)
    expect(wsMocks.restart).toHaveBeenCalledTimes(1)
  })
})
