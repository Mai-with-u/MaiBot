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

type LogCallback = () => void
type ConnectionCallback = (connected: boolean) => void

class LogWebSocketManager {
  private connectionCallbacks: Set<ConnectionCallback> = new Set()
  private initialized = false
  private isConnected = false
  private logCache: LogEntry[] = []
  private logCallbacks: Set<LogCallback> = new Set()
  private subscriptionActive = false

  private getMaxCacheSize(): number {
    return getSetting('logCacheSize')
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
        this.logCache = entries.slice(-this.getMaxCacheSize())
        this.notifyLogChange()
        return
      }

      if (message.event === 'entry' && message.data.entry) {
        this.appendLog(message.data.entry as LogEntry)
      }
    })

    unifiedWsClient.onConnectionChange((connected) => {
      this.isConnected = connected
      this.notifyConnection(connected)
    })

    this.initialized = true
  }

  private appendLog(log: LogEntry): void {
    const exists = this.logCache.some(existingLog => existingLog.id === log.id)
    if (exists) {
      return
    }

    this.logCache.push(log)
    const maxCacheSize = this.getMaxCacheSize()
    if (this.logCache.length > maxCacheSize) {
      this.logCache = this.logCache.slice(-maxCacheSize)
    }
    this.notifyLogChange()
  }

  private notifyLogChange(): void {
    this.logCallbacks.forEach((callback) => {
      try {
        callback()
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

  async connect(): Promise<void> {
    if (window.location.pathname === '/auth') {
      return
    }

    const isAuthenticated = await checkAuthStatus()
    if (!isAuthenticated) {
      return
    }

    this.initialize()
    if (this.subscriptionActive) {
      return
    }

    try {
      await unifiedWsClient.subscribe('logs', 'main', { replay: 100 })
      this.subscriptionActive = true
    } catch (error) {
      console.error('订阅日志流失败:', error)
    }
  }

  disconnect(): void {
    this.subscriptionActive = false
    void unifiedWsClient.unsubscribe('logs', 'main')
    this.isConnected = false
    this.notifyConnection(false)
  }

  onLog(callback: LogCallback): () => void {
    this.logCallbacks.add(callback)
    return () => this.logCallbacks.delete(callback)
  }

  onConnectionChange(callback: ConnectionCallback): () => void {
    this.connectionCallbacks.add(callback)
    callback(this.isConnected)
    return () => this.connectionCallbacks.delete(callback)
  }

  getAllLogs(): LogEntry[] {
    return [...this.logCache]
  }

  clearLogs(): void {
    this.logCache = []
    this.notifyLogChange()
  }

  getConnectionStatus(): boolean {
    return this.isConnected
  }
}

export const logWebSocket = new LogWebSocketManager()

if (typeof window !== 'undefined') {
  setTimeout(() => {
    void logWebSocket.connect()
  }, 100)
}
