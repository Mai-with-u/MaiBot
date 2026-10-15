import { getWsBaseUrl } from '@/lib/api-base'
import { backendApi } from '@/lib/http'

import { getSetting } from './settings-manager'

export type ConnectionStatus = 'idle' | 'connecting' | 'connected'

export interface WsErrorPayload {
  code?: string
  message: string
}

export interface WsEventEnvelope {
  op: 'event'
  domain: string
  event: string
  session?: string
  topic?: string
  data: Record<string, unknown>
}

interface WsResponseEnvelope {
  op: 'response'
  id?: string
  ok: boolean
  data?: Record<string, unknown>
  error?: WsErrorPayload
}

interface WsPongEnvelope {
  op: 'pong'
  ts: number
}

type WsServerEnvelope = WsEventEnvelope | WsPongEnvelope | WsResponseEnvelope

interface PendingRequest {
  reject: (error: Error) => void
  resolve: (data: Record<string, unknown>) => void
  timeoutId: number
}

interface SubscriptionDefinition {
  data?: Record<string, unknown>
  domain: string
  topic: string
}

type EventListener = (message: WsEventEnvelope) => void
type ConnectionListener = (connected: boolean) => void
type StatusListener = (status: ConnectionStatus) => void
type ReconnectListener = () => void

function isResponseEnvelope(message: WsServerEnvelope): message is WsResponseEnvelope {
  return message.op === 'response'
}

function isEventEnvelope(message: WsServerEnvelope): message is WsEventEnvelope {
  return message.op === 'event'
}

async function getWsToken(): Promise<string | null> {
  try {
    const data = await backendApi.get<{ success?: boolean; token?: string }>('/api/webui/ws-token')
    if (data.success && data.token) {
      return data.token
    }
    return null
  } catch (error) {
    console.error('获取统一 WebSocket token 失败:', error)
    return null
  }
}

class UnifiedWebSocketClient {
  private readonly heartbeatIntervalMs = 30000
  private readonly heartbeatTimeoutMs = 90000
  private connectPromise: Promise<void> | null = null
  private connectionListeners: Set<ConnectionListener> = new Set()
  private eventListeners: Set<EventListener> = new Set()
  private hasConnectedOnce = false
  private heartbeatIntervalId: number | null = null
  private lastInboundAt = 0
  private manualDisconnect = false
  private pendingRequests: Map<string, PendingRequest> = new Map()
  private reconnectAttempts = 0
  private reconnectListeners: Set<ReconnectListener> = new Set()
  private reconnectTimeout: number | null = null
  private recoveryWatchEnabled = false
  private requestCounter = 0
  private status: ConnectionStatus = 'idle'
  private statusListeners: Set<StatusListener> = new Set()
  private subscriptions: Map<string, SubscriptionDefinition> = new Map()
  private ws: WebSocket | null = null

  private getReconnectDelay(): number {
    const baseDelay = getSetting('wsReconnectInterval')
    return Math.min(baseDelay * Math.max(this.reconnectAttempts, 1), 30000)
  }

  private getMaxReconnectAttempts(): number {
    return getSetting('wsMaxReconnectAttempts')
  }

  private getSubscriptionKey(domain: string, topic: string): string {
    return `${domain}:${topic}`
  }

  private nextRequestId(): string {
    this.requestCounter += 1
    return `ws-${Date.now()}-${this.requestCounter}`
  }

  private setStatus(status: ConnectionStatus): void {
    if (this.status === status) {
      return
    }

    this.status = status
    this.statusListeners.forEach((listener) => {
      try {
        listener(status)
      } catch (error) {
        console.error('WebSocket 状态监听器执行失败:', error)
      }
    })

    const connected = status === 'connected'
    this.connectionListeners.forEach((listener) => {
      try {
        listener(connected)
      } catch (error) {
        console.error('WebSocket 连接监听器执行失败:', error)
      }
    })
  }

  private stopHeartbeat(): void {
    if (this.heartbeatIntervalId !== null) {
      clearInterval(this.heartbeatIntervalId)
      this.heartbeatIntervalId = null
    }
  }

  private startHeartbeat(): void {
    this.stopHeartbeat()
    this.heartbeatIntervalId = window.setInterval(() => {
      if (this.ws?.readyState !== WebSocket.OPEN) {
        return
      }

      const now = Date.now()
      if (this.lastInboundAt > 0 && now - this.lastInboundAt > this.heartbeatTimeoutMs) {
        console.warn('统一 WebSocket 心跳超时，准备重连')
        void this.restart().catch((error) => {
          console.error('统一 WebSocket 心跳重连失败:', error)
        })
        return
      }

      this.ws.send(JSON.stringify({ op: 'ping' }))
    }, this.heartbeatIntervalMs)
  }

  private clearReconnectTimer(): void {
    if (this.reconnectTimeout !== null) {
      clearTimeout(this.reconnectTimeout)
      this.reconnectTimeout = null
    }
  }

  // 网络恢复或页面重新可见时立即重连，不必等退避计时器，也能救回已放弃重连的连接
  private readonly handleRecoverySignal = (): void => {
    if (document.visibilityState === 'hidden') {
      return
    }
    if (this.manualDisconnect || this.status !== 'idle' || this.reconnectAttempts === 0) {
      return
    }

    this.clearReconnectTimer()
    this.reconnectAttempts = 0
    void this.connect().catch((error) => {
      console.error('统一 WebSocket 恢复连接失败:', error)
    })
  }

  private setRecoveryWatch(enabled: boolean): void {
    if (typeof window === 'undefined' || this.recoveryWatchEnabled === enabled) {
      return
    }

    this.recoveryWatchEnabled = enabled
    const method = enabled ? 'addEventListener' : 'removeEventListener'
    window[method]('online', this.handleRecoverySignal)
    document[method]('visibilitychange', this.handleRecoverySignal)
  }

  private rejectPendingRequests(error: Error): void {
    this.pendingRequests.forEach((pendingRequest, requestId) => {
      clearTimeout(pendingRequest.timeoutId)
      pendingRequest.reject(error)
      this.pendingRequests.delete(requestId)
    })
  }

  private scheduleReconnect(): void {
    if (this.manualDisconnect || this.reconnectTimeout !== null) {
      return
    }

    this.setRecoveryWatch(true)
    if (this.reconnectAttempts >= this.getMaxReconnectAttempts()) {
      console.warn(`统一 WebSocket 达到最大重连次数 (${this.getMaxReconnectAttempts()})，停止重连`)
      return
    }

    this.reconnectAttempts += 1
    const delay = this.getReconnectDelay()
    this.reconnectTimeout = window.setTimeout(() => {
      this.reconnectTimeout = null
      void this.connect().catch((error) => {
        console.error('统一 WebSocket 重连失败:', error)
      })
    }, delay)
  }

  private async createWebSocketUrl(): Promise<string | null> {
    try {
      const wsBaseUrl = await getWsBaseUrl()
      const wsToken = await getWsToken()
      if (!wsBaseUrl || !wsToken) {
        return null
      }
      return `${wsBaseUrl}/api/webui/ws?token=${encodeURIComponent(wsToken)}`
    } catch (error) {
      console.error('构建统一 WebSocket 地址失败:', error)
      return null
    }
  }

  private async sendRequest(
    payload: Record<string, unknown>,
    timeoutMs = 10000
  ): Promise<Record<string, unknown>> {
    if (this.ws?.readyState !== WebSocket.OPEN) {
      throw new Error('统一 WebSocket 尚未连接')
    }

    const requestId = payload.id as string
    return await new Promise<Record<string, unknown>>((resolve, reject) => {
      const timeoutId = window.setTimeout(() => {
        this.pendingRequests.delete(requestId)
        reject(new Error(`统一 WebSocket 请求超时: ${requestId}`))
      }, timeoutMs)

      this.pendingRequests.set(requestId, {
        resolve,
        reject,
        timeoutId,
      })
      this.ws?.send(JSON.stringify(payload))
    })
  }

  private async restoreState(shouldNotifyReconnect: boolean): Promise<void> {
    const subscriptions = Array.from(this.subscriptions.values())
    for (const subscription of subscriptions) {
      try {
        await this.sendRequest({
          op: 'subscribe',
          id: this.nextRequestId(),
          domain: subscription.domain,
          topic: subscription.topic,
          data: subscription.data ?? {},
        })
      } catch (error) {
        console.error('恢复统一 WebSocket 订阅失败:', error)
      }
    }

    if (shouldNotifyReconnect) {
      this.reconnectListeners.forEach((listener) => {
        try {
          listener()
        } catch (error) {
          console.error('统一 WebSocket 重连监听器执行失败:', error)
        }
      })
    }
  }

  private handleServerMessage(socket: WebSocket, rawData: string): void {
    if (this.ws !== socket) {
      return
    }

    // 任何下行数据都说明连接存活；pong 可能排在大批量事件之后，不能只靠它判活
    this.lastInboundAt = Date.now()

    let message: WsServerEnvelope
    try {
      message = JSON.parse(rawData) as WsServerEnvelope
    } catch (error) {
      console.error('解析统一 WebSocket 消息失败:', error)
      return
    }

    if (message.op === 'pong') {
      return
    }

    if (isResponseEnvelope(message)) {
      const requestId = message.id
      if (!requestId) {
        return
      }

      const pendingRequest = this.pendingRequests.get(requestId)
      if (!pendingRequest) {
        return
      }

      clearTimeout(pendingRequest.timeoutId)
      this.pendingRequests.delete(requestId)
      if (message.ok) {
        pendingRequest.resolve(message.data ?? {})
      } else {
        pendingRequest.reject(new Error(message.error?.message ?? '统一 WebSocket 请求失败'))
      }
      return
    }

    if (isEventEnvelope(message)) {
      this.eventListeners.forEach((listener) => {
        try {
          listener(message)
        } catch (error) {
          console.error('统一 WebSocket 事件监听器执行失败:', error)
        }
      })
    }
  }

  /** 丢弃当前 socket 的全部运行态；之后该 socket 的回调都会被视为过期而忽略 */
  private releaseSocket(reason: Error): void {
    this.stopHeartbeat()
    this.lastInboundAt = 0
    this.ws = null
    this.connectPromise = null
    this.setStatus('idle')
    this.rejectPendingRequests(reason)
  }

  private handleClose(socket: WebSocket, event: CloseEvent): void {
    if (this.ws !== socket) {
      return
    }

    this.releaseSocket(new Error(`统一 WebSocket 已关闭 (${event.code})`))

    if (event.code === 4001) {
      this.manualDisconnect = true
      this.setRecoveryWatch(false)
      if (window.location.pathname !== '/auth') {
        window.location.href = '/auth'
      }
      return
    }

    this.scheduleReconnect()
  }

  async connect(): Promise<void> {
    if (this.ws?.readyState === WebSocket.OPEN) {
      return
    }

    if (this.connectPromise) {
      return await this.connectPromise
    }

    this.manualDisconnect = false
    this.setStatus('connecting')

    const connectPromise = (async () => {
      const wsUrl = await this.createWebSocketUrl()
      if (!wsUrl) {
        this.setStatus('idle')
        // 后端重启期间 token 接口也不可用；此时尚未创建 socket，不会有 close 事件驱动重连
        this.scheduleReconnect()
        throw new Error('无法建立统一 WebSocket 连接')
      }

      await new Promise<void>((resolve, reject) => {
        let settled = false
        const socket = new WebSocket(wsUrl)
        this.ws = socket

        socket.onopen = () => {
          if (this.ws !== socket) {
            socket.close()
            return
          }

          settled = true
          const shouldNotifyReconnect = this.hasConnectedOnce
          this.hasConnectedOnce = true
          this.reconnectAttempts = 0
          this.clearReconnectTimer()
          this.setRecoveryWatch(false)
          this.lastInboundAt = Date.now()
          this.startHeartbeat()
          this.setStatus('connected')
          resolve()
          void this.restoreState(shouldNotifyReconnect)
        }

        socket.onmessage = (event) => {
          this.handleServerMessage(socket, event.data)
        }

        socket.onerror = () => {
          if (this.ws !== socket) {
            return
          }

          if (!settled) {
            settled = true
            reject(new Error('统一 WebSocket 连接失败'))
          }
        }

        socket.onclose = (event) => {
          if (!settled) {
            settled = true
            reject(new Error(`统一 WebSocket 已关闭 (${event.code})`))
          }
          this.handleClose(socket, event)
        }
      })
    })()
    this.connectPromise = connectPromise

    try {
      await connectPromise
    } finally {
      // restart 可能已换上新的握手 Promise，只清理属于本次握手的引用
      if (this.connectPromise === connectPromise && this.status !== 'connected') {
        this.connectPromise = null
      }
    }
  }

  disconnect(): void {
    this.manualDisconnect = true
    this.clearReconnectTimer()
    this.setRecoveryWatch(false)
    const socket = this.ws
    this.releaseSocket(new Error('统一 WebSocket 已手动断开'))
    socket?.close()
  }

  async restart(): Promise<void> {
    this.manualDisconnect = false
    this.clearReconnectTimer()
    const socket = this.ws
    if (socket) {
      // 半开连接上 close() 可能迟迟等不到 close 事件，先丢弃旧 socket 再立即重建
      this.releaseSocket(new Error('统一 WebSocket 正在重启'))
      socket.close()
    }
    await this.connect()
  }

  async call(params: {
    data?: Record<string, unknown>
    domain: string
    method: string
    session?: string
    timeoutMs?: number
  }): Promise<Record<string, unknown>> {
    await this.connect()
    const requestId = this.nextRequestId()
    return await this.sendRequest(
      {
        op: 'call',
        id: requestId,
        domain: params.domain,
        method: params.method,
        session: params.session,
        data: params.data ?? {},
      },
      params.timeoutMs
    )
  }

  async subscribe(
    domain: string,
    topic: string,
    data?: Record<string, unknown>
  ): Promise<Record<string, unknown>> {
    await this.connect()
    this.subscriptions.set(this.getSubscriptionKey(domain, topic), {
      domain,
      topic,
      data,
    })

    return await this.sendRequest({
      op: 'subscribe',
      id: this.nextRequestId(),
      domain,
      topic,
      data: data ?? {},
    })
  }

  async unsubscribe(domain: string, topic: string): Promise<Record<string, unknown> | null> {
    this.subscriptions.delete(this.getSubscriptionKey(domain, topic))
    if (this.ws?.readyState !== WebSocket.OPEN) {
      return null
    }

    return await this.sendRequest({
      op: 'unsubscribe',
      id: this.nextRequestId(),
      domain,
      topic,
      data: {},
    })
  }

  updateSubscriptionData(domain: string, topic: string, data: Record<string, unknown>): void {
    const subscriptionKey = this.getSubscriptionKey(domain, topic)
    const subscription = this.subscriptions.get(subscriptionKey)
    if (!subscription) {
      return
    }
    this.subscriptions.set(subscriptionKey, {
      ...subscription,
      data,
    })
  }

  addEventListener(listener: EventListener): () => void {
    this.eventListeners.add(listener)
    return () => {
      this.eventListeners.delete(listener)
    }
  }

  onConnectionChange(listener: ConnectionListener): () => void {
    this.connectionListeners.add(listener)
    listener(this.status === 'connected')
    return () => {
      this.connectionListeners.delete(listener)
    }
  }

  onStatusChange(listener: StatusListener): () => void {
    this.statusListeners.add(listener)
    listener(this.status)
    return () => {
      this.statusListeners.delete(listener)
    }
  }

  onReconnect(listener: ReconnectListener): () => void {
    this.reconnectListeners.add(listener)
    return () => {
      this.reconnectListeners.delete(listener)
    }
  }

  getStatus(): ConnectionStatus {
    return this.status
  }
}

export const unifiedWsClient = new UnifiedWebSocketClient()
