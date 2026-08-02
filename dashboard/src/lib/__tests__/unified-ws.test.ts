import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { unifiedWsClient as UnifiedWsClientType } from '../unified-ws'

// 可变的 mock 状态：token 响应、ws 基地址与前端设置，均在各用例中按需调整
const apiState = vi.hoisted(() => ({
  settings: { wsMaxReconnectAttempts: 2, wsReconnectInterval: 1000 } as Record<string, number>,
  tokenError: null as Error | null,
  tokenResponse: { success: true, token: 'token/1' } as { success?: boolean; token?: string },
  wsBaseUrl: 'ws://test-host',
}))

vi.mock('@/lib/api-base', () => ({
  getWsBaseUrl: async () => apiState.wsBaseUrl,
}))

vi.mock('@/lib/http', () => ({
  backendApi: {
    get: async () => {
      if (apiState.tokenError) {
        throw apiState.tokenError
      }
      return apiState.tokenResponse
    },
  },
}))

vi.mock('../settings-manager', () => ({
  getSetting: (key: string) => {
    const value = apiState.settings[key]
    if (value === undefined) {
      throw new Error(`未处理的设置项: ${key}`)
    }
    return value
  },
}))

/** 发送到服务端的信封结构（测试用宽松类型） */
interface SentEnvelope {
  op: string
  id?: string
  domain?: string
  topic?: string
  method?: string
  session?: string
  data?: Record<string, unknown>
}

/** 假 WebSocket：记录实例与发送内容，并提供服务端事件触发辅助 */
class FakeWebSocket {
  static readonly CONNECTING = 0
  static readonly OPEN = 1
  static readonly CLOSING = 2
  static readonly CLOSED = 3
  static instances: FakeWebSocket[] = []

  readonly url: string
  readyState = FakeWebSocket.CONNECTING
  sent: string[] = []
  onopen: (() => void) | null = null
  onmessage: ((event: MessageEvent) => void) | null = null
  onerror: (() => void) | null = null
  onclose: ((event: CloseEvent) => void) | null = null

  constructor(url: string) {
    this.url = url
    FakeWebSocket.instances.push(this)
  }

  send(data: string): void {
    this.sent.push(data)
  }

  close(): void {
    // 模拟浏览器行为：主动 close 也会触发 close 事件
    this.readyState = FakeWebSocket.CLOSED
    this.onclose?.({ code: 1000 } as CloseEvent)
  }

  /** 服务端确认连接 */
  serverOpen(): void {
    this.readyState = FakeWebSocket.OPEN
    this.onopen?.()
  }

  /** 服务端下发一条 JSON 消息 */
  serverMessage(payload: unknown): void {
    this.onmessage?.({ data: JSON.stringify(payload) } as MessageEvent)
  }

  /** 服务端下发原始文本（用于测坏 JSON） */
  serverRaw(data: string): void {
    this.onmessage?.({ data } as MessageEvent)
  }

  /** 服务端异常断开 */
  serverClose(code: number): void {
    this.readyState = FakeWebSocket.CLOSED
    this.onclose?.({ code } as CloseEvent)
  }

  lastSent(): SentEnvelope {
    const raw = this.sent[this.sent.length - 1]
    return JSON.parse(raw) as SentEnvelope
  }
}

async function flushMicrotasks(): Promise<void> {
  for (let index = 0; index < 10; index += 1) {
    await Promise.resolve()
  }
}

async function loadClient(): Promise<typeof UnifiedWsClientType> {
  const module = await import('../unified-ws')
  return module.unifiedWsClient
}

/** 建立连接并打开第一个 socket，返回客户端与 socket */
async function connectAndOpen() {
  const client = await loadClient()
  const connectPromise = client.connect()
  await flushMicrotasks()
  const socket = FakeWebSocket.instances[0]
  socket.serverOpen()
  await connectPromise
  return { client, socket }
}

describe('unifiedWsClient', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.resetModules()
    FakeWebSocket.instances = []
    apiState.settings = { wsMaxReconnectAttempts: 2, wsReconnectInterval: 1000 }
    apiState.tokenError = null
    apiState.tokenResponse = { success: true, token: 'token/1' }
    apiState.wsBaseUrl = 'ws://test-host'
    vi.stubGlobal('WebSocket', FakeWebSocket)
    // 静音重连/心跳路径上的预期错误日志
    vi.spyOn(console, 'error').mockImplementation(() => {})
    vi.spyOn(console, 'warn').mockImplementation(() => {})
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.useRealTimers()
  })

  it('连接成功：URL 携带编码后的 token，状态依次经过 connecting 与 connected', async () => {
    const client = await loadClient()
    const statuses: string[] = []
    const connections: boolean[] = []
    client.onStatusChange((status) => statuses.push(status))
    client.onConnectionChange((connected) => connections.push(connected))

    const connectPromise = client.connect()
    await flushMicrotasks()

    const socket = FakeWebSocket.instances[0]
    // token/1 应被 encodeURIComponent 编码为 token%2F1
    expect(socket.url).toBe('ws://test-host/api/webui/ws?token=token%2F1')

    socket.serverOpen()
    await connectPromise

    expect(client.getStatus()).toBe('connected')
    // 注册时立即回调当前状态 idle，随后 connecting、connected
    expect(statuses).toEqual(['idle', 'connecting', 'connected'])
    expect(connections).toEqual([false, false, true])
  })

  it('token 获取失败时 connect 抛错并回到 idle 状态', async () => {
    apiState.tokenResponse = { success: false }
    const client = await loadClient()
    const statuses: string[] = []
    client.onStatusChange((status) => statuses.push(status))

    await expect(client.connect()).rejects.toThrow('无法建立统一 WebSocket 连接')
    expect(statuses).toEqual(['idle', 'connecting', 'idle'])
    expect(FakeWebSocket.instances).toHaveLength(0)
  })

  it('call 发送 call 信封并用响应 data 解析，未知请求 ID 的响应被忽略', async () => {
    const { client, socket } = await connectAndOpen()

    const callPromise = client.call({
      domain: 'chat',
      method: 'message.send',
      session: 's1',
      data: { text: 'hi' },
    })
    await flushMicrotasks()

    const payload = socket.lastSent()
    expect(payload).toMatchObject({
      op: 'call',
      domain: 'chat',
      method: 'message.send',
      session: 's1',
      data: { text: 'hi' },
    })
    expect(typeof payload.id).toBe('string')

    // 未知请求 ID 的响应不影响挂起请求
    socket.serverMessage({ op: 'response', id: 'unknown-id', ok: true, data: {} })
    socket.serverMessage({ op: 'response', id: payload.id, ok: true, data: { echoed: true } })

    await expect(callPromise).resolves.toEqual({ echoed: true })
  })

  it('响应 ok=false 时用后端错误信息拒绝请求', async () => {
    const { client, socket } = await connectAndOpen()

    const callPromise = client.call({ domain: 'chat', method: 'session.open' })
    await flushMicrotasks()
    const payload = socket.lastSent()

    const expectation = expect(callPromise).rejects.toThrow('后端拒绝了请求')
    socket.serverMessage({
      op: 'response',
      id: payload.id,
      ok: false,
      error: { message: '后端拒绝了请求' },
    })
    await expectation
  })

  it('请求 10 秒无响应时按超时拒绝', async () => {
    const { client } = await connectAndOpen()

    const callPromise = client.call({ domain: 'chat', method: 'slow.call' })
    await flushMicrotasks()

    const expectation = expect(callPromise).rejects.toThrow('统一 WebSocket 请求超时')
    await vi.advanceTimersByTimeAsync(10000)
    await expectation
  })

  it('心跳每 30 秒发送 ping，超过 90 秒无 pong 时重启连接', async () => {
    const { socket } = await connectAndOpen()

    await vi.advanceTimersByTimeAsync(30000)
    expect(socket.lastSent()).toEqual({ op: 'ping' })

    // 服务端回 pong，刷新 lastPongAt
    socket.serverMessage({ op: 'pong', ts: Date.now() })

    // 此后 120 秒无 pong：在 150 秒的心跳 tick 上判定超时并重启（旧 socket 被关闭）
    await vi.advanceTimersByTimeAsync(120000)
    expect(socket.readyState).toBe(FakeWebSocket.CLOSED)

    // 重启走重连路径：按 1000ms 退避后建立新 socket
    await vi.advanceTimersByTimeAsync(1000)
    await flushMicrotasks()
    expect(FakeWebSocket.instances).toHaveLength(2)
  })

  it('异常断开后按退避重连，并用最新订阅数据恢复订阅、通知重连监听器', async () => {
    const { client, socket } = await connectAndOpen()

    // 建立订阅并等服务端 ACK
    const subscribePromise = client.subscribe('logs', 'main', { level: 'INFO' })
    await flushMicrotasks()
    const subscribePayload = socket.lastSent()
    expect(subscribePayload).toMatchObject({
      op: 'subscribe',
      domain: 'logs',
      topic: 'main',
      data: { level: 'INFO' },
    })
    socket.serverMessage({ op: 'response', id: subscribePayload.id, ok: true, data: {} })
    await subscribePromise

    const onReconnect = vi.fn()
    client.onReconnect(onReconnect)
    client.updateSubscriptionData('logs', 'main', { level: 'ERROR' })

    // 服务端异常断开：状态回到 idle 并调度重连
    socket.serverClose(1006)
    expect(client.getStatus()).toBe('idle')

    await vi.advanceTimersByTimeAsync(1000)
    await flushMicrotasks()
    const nextSocket = FakeWebSocket.instances[1]
    nextSocket.serverOpen()
    await flushMicrotasks()

    // 恢复订阅使用 updateSubscriptionData 更新后的数据
    const restorePayload = nextSocket.lastSent()
    expect(restorePayload).toMatchObject({
      op: 'subscribe',
      domain: 'logs',
      topic: 'main',
      data: { level: 'ERROR' },
    })

    // 订阅 ACK 返回前不通知重连监听器
    expect(onReconnect).not.toHaveBeenCalled()
    nextSocket.serverMessage({ op: 'response', id: restorePayload.id, ok: true, data: {} })
    await flushMicrotasks()
    expect(onReconnect).toHaveBeenCalledTimes(1)
  })

  it('达到最大重连次数后停止重连', async () => {
    const { socket } = await connectAndOpen()

    // 第一次断开：调度第 1 次重连（1000ms）
    socket.serverClose(1006)
    await vi.advanceTimersByTimeAsync(1000)
    await flushMicrotasks()
    expect(FakeWebSocket.instances).toHaveLength(2)

    // 第 1 次重连失败：调度第 2 次重连（2000ms 退避）
    FakeWebSocket.instances[1].serverClose(1006)
    await vi.advanceTimersByTimeAsync(1999)
    await flushMicrotasks()
    expect(FakeWebSocket.instances).toHaveLength(2)
    await vi.advanceTimersByTimeAsync(1)
    await flushMicrotasks()
    expect(FakeWebSocket.instances).toHaveLength(3)

    // 第 2 次重连失败：已达最大次数（2），不再重连
    FakeWebSocket.instances[2].serverClose(1006)
    await vi.advanceTimersByTimeAsync(600000)
    await flushMicrotasks()
    expect(FakeWebSocket.instances).toHaveLength(3)
  })

  it('手动断开会拒绝挂起请求且不再自动重连', async () => {
    const { client } = await connectAndOpen()

    const callPromise = client.call({ domain: 'chat', method: 'pending.call' })
    await flushMicrotasks()

    const expectation = expect(callPromise).rejects.toThrow('统一 WebSocket 已手动断开')
    client.disconnect()
    await expectation

    expect(client.getStatus()).toBe('idle')
    await vi.advanceTimersByTimeAsync(600000)
    await flushMicrotasks()
    expect(FakeWebSocket.instances).toHaveLength(1)
  })

  it('未连接时 unsubscribe 直接返回 null 而不发请求', async () => {
    const client = await loadClient()
    await expect(client.unsubscribe('logs', 'main')).resolves.toBeNull()
    expect(FakeWebSocket.instances).toHaveLength(0)
  })

  it('事件信封分发给事件监听器，坏 JSON 消息被安全忽略', async () => {
    const { client, socket } = await connectAndOpen()

    const received: unknown[] = []
    const removeListener = client.addEventListener((message) => received.push(message))

    socket.serverRaw('not-json')
    socket.serverMessage({
      op: 'event',
      domain: 'logs',
      event: 'entry',
      data: { entry: { id: 'log-1' } },
    })

    expect(received).toEqual([
      {
        op: 'event',
        domain: 'logs',
        event: 'entry',
        data: { entry: { id: 'log-1' } },
      },
    ])

    // 取消监听后不再接收事件
    removeListener()
    socket.serverMessage({ op: 'event', domain: 'logs', event: 'entry', data: {} })
    expect(received).toHaveLength(1)
  })
})
