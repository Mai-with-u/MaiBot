/**
 * 全局日志 WebSocket 管理器
 * 确保整个应用只通过统一连接层订阅日志流
 */

import { checkAuthStatus } from './auth'
import { getSetting } from './settings-manager'
import { unifiedWsClient } from './unified-ws'

export interface LogEntry {
  id: string
  timestamp: string
  level: 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR' | 'CRITICAL'
  module: string
  moduleBackgroundColor?: string
  moduleBold?: boolean
  moduleColor?: string
  message: string
}

type LogCallback = (logs: LogEntry[]) => void
type ConnectionCallback = (connected: boolean) => void

const LOG_NOTIFICATION_BATCH_MS = 50
const LOG_SUBSCRIPTION_RETRY_MS = 3000

export class LogWebSocketManager {
  private connectionCallbacks: Set<ConnectionCallback> = new Set()
  private initialized = false
  private isConnected = false
  private logCache: Map<string, LogEntry> = new Map()
  private logCallbacks: Set<LogCallback> = new Set()
  private logNotificationTimer: ReturnType<typeof setTimeout> | null = null
  private resubscribeAfterPending = false
  private subscriptionRetryTimer: ReturnType<typeof setTimeout> | null = null
  private subscribePromise: Promise<void> | null = null
  private subscriptionActive = false
  private transportConnected = false

  private getMaxCacheSize(): number {
    return getSetting('logCacheSize')
  }

  private replaceLogs(entries: LogEntry[]): void {
    const maxCacheSize = this.getMaxCacheSize()
    const nextCache = new Map<string, LogEntry>()
    if (maxCacheSize <= 0) {
      this.logCache = nextCache
      return
    }

    for (const entry of entries.slice(-maxCacheSize)) {
      // 快照中若出现重复 ID，以最后一条为准并保持最后出现的顺序。
      nextCache.delete(entry.id)
      nextCache.set(entry.id, entry)
    }
    this.logCache = nextCache
  }

  private initialize(): void {
    if (this.initialized) {
      return
    }

    unifiedWsClient.addEventListener((message) => {
      if (message.domain !== 'logs') {
        return
      }

      if (message.event === 'snapshot') {
        const entries = Array.isArray(message.data.entries)
          ? (message.data.entries as LogEntry[])
          : []
        this.replaceLogs(entries)
        this.scheduleLogChange()
        return
      }

      if (message.event === 'entry' && message.data.entry) {
        this.appendLog(message.data.entry as LogEntry)
      }
    })

    unifiedWsClient.onConnectionChange((connected) => {
      this.transportConnected = connected
      this.updateConnectionState()
      if (
        connected &&
        this.logCallbacks.size > 0 &&
        !this.subscriptionActive &&
        !this.subscribePromise
      ) {
        void this.connect()
      }
    })

    unifiedWsClient.onReconnect(() => {
      if (this.logCallbacks.size === 0) {
        return
      }

      // 底层会尝试恢复订阅，但恢复失败只记录日志。这里再做一次带 ACK 的
      // 显式确认，让失败进入本管理器的清理与重试流程。
      this.subscriptionActive = false
      this.updateConnectionState()
      void this.connect()
    })

    this.initialized = true
  }

  private appendLog(log: LogEntry): void {
    if (this.logCache.has(log.id)) {
      return
    }

    const maxCacheSize = this.getMaxCacheSize()
    if (maxCacheSize <= 0) {
      return
    }

    this.logCache.set(log.id, log)
    while (this.logCache.size > maxCacheSize) {
      const oldestId = this.logCache.keys().next().value
      if (oldestId === undefined) {
        break
      }
      this.logCache.delete(oldestId)
    }
    this.scheduleLogChange()
  }

  private cancelLogNotification(): void {
    if (this.logNotificationTimer === null) {
      return
    }
    clearTimeout(this.logNotificationTimer)
    this.logNotificationTimer = null
  }

  private cancelSubscriptionRetry(): void {
    if (this.subscriptionRetryTimer === null) {
      return
    }
    clearTimeout(this.subscriptionRetryTimer)
    this.subscriptionRetryTimer = null
  }

  private scheduleSubscriptionRetry(): void {
    if (
      this.logCallbacks.size === 0 ||
      this.subscriptionActive ||
      this.subscriptionRetryTimer !== null
    ) {
      return
    }

    this.subscriptionRetryTimer = setTimeout(() => {
      this.subscriptionRetryTimer = null
      void this.connect()
    }, LOG_SUBSCRIPTION_RETRY_MS)
  }

  private scheduleLogChange(): void {
    if (this.logCallbacks.size === 0 || this.logNotificationTimer !== null) {
      return
    }

    this.logNotificationTimer = setTimeout(() => {
      this.logNotificationTimer = null
      this.notifyLogChange()
    }, LOG_NOTIFICATION_BATCH_MS)
  }

  private notifyLogChange(): void {
    const logs = this.getAllLogs()
    this.logCallbacks.forEach((callback) => {
      try {
        callback(logs)
      } catch (error) {
        console.error('日志回调执行失败:', error)
      }
    })
  }

  private notifyConnection(connected: boolean): void {
    this.connectionCallbacks.forEach((callback) => {
      try {
        callback(connected)
      } catch (error) {
        console.error('连接状态回调执行失败:', error)
      }
    })
  }

  private updateConnectionState(): void {
    const connected = this.subscriptionActive && this.transportConnected
    if (connected === this.isConnected) {
      return
    }
    this.isConnected = connected
    this.notifyConnection(connected)
  }

  private async startSubscription(): Promise<void> {
    try {
      const isAuthenticated = await checkAuthStatus()
      if (!isAuthenticated || this.logCallbacks.size === 0) {
        return
      }

      this.initialize()
      if (this.subscriptionActive) {
        return
      }

      await unifiedWsClient.subscribe('logs', 'main', { replay: 100 })
      if (this.logCallbacks.size === 0) {
        await unifiedWsClient.unsubscribe('logs', 'main')
        return
      }

      this.cancelSubscriptionRetry()
      this.subscriptionActive = true
      this.updateConnectionState()
    } catch (error) {
      // subscribe 会在等待服务端 ACK 前登记期望订阅。失败时必须主动删除，
      // 否则底层重连可能恢复一个上层并不知情的幽灵订阅。
      void unifiedWsClient.unsubscribe('logs', 'main').catch((unsubscribeError) => {
        console.error('清理失败的日志流订阅时出错:', unsubscribeError)
      })
      this.scheduleSubscriptionRetry()
      console.error('订阅日志流失败:', error)
    }
  }

  async connect(): Promise<void> {
    if (
      window.location.pathname === '/auth' ||
      this.logCallbacks.size === 0 ||
      this.subscriptionActive
    ) {
      return
    }

    if (this.subscribePromise) {
      this.resubscribeAfterPending = true
      return await this.subscribePromise
    }

    const subscribePromise = this.startSubscription()
    this.subscribePromise = subscribePromise
    try {
      await subscribePromise
    } finally {
      if (this.subscribePromise === subscribePromise) {
        this.subscribePromise = null
      }
      const shouldResubscribe =
        this.resubscribeAfterPending && this.logCallbacks.size > 0 && !this.subscriptionActive
      this.resubscribeAfterPending = false
      if (shouldResubscribe) {
        void this.connect()
      }
    }
  }

  disconnect(): void {
    this.cancelLogNotification()
    this.cancelSubscriptionRetry()
    this.resubscribeAfterPending = false
    this.subscriptionActive = false
    this.updateConnectionState()
    // 即使 ACK 尚未返回，底层也可能已经登记了期望订阅，因此始终清理。
    void unifiedWsClient.unsubscribe('logs', 'main').catch((error) => {
      console.error('取消日志流订阅失败:', error)
    })
  }

  onLog(callback: LogCallback): () => void {
    const shouldConnect = this.logCallbacks.size === 0
    this.logCallbacks.add(callback)
    if (shouldConnect) {
      void this.connect()
    }

    let subscribed = true
    return () => {
      if (!subscribed) {
        return
      }
      subscribed = false
      this.logCallbacks.delete(callback)
      if (this.logCallbacks.size === 0) {
        this.disconnect()
      }
    }
  }

  onConnectionChange(callback: ConnectionCallback): () => void {
    this.connectionCallbacks.add(callback)
    callback(this.isConnected)
    return () => this.connectionCallbacks.delete(callback)
  }

  getAllLogs(): LogEntry[] {
    return Array.from(this.logCache.values())
  }

  clearLogs(): void {
    this.logCache.clear()
    this.cancelLogNotification()
    this.notifyLogChange()
  }

  getConnectionStatus(): boolean {
    return this.isConnected
  }
}

export const logWebSocket = new LogWebSocketManager()
