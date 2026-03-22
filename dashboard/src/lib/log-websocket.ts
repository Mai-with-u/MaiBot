/**
 * 日志 WebSocket 模块
 * 提供全局单例 WebSocket 连接，用于实时接收日志消息
 */

import { fetchWithAuth } from './fetch-with-auth'

// 日志条目类型
export interface LogEntry {
  id: string
  timestamp: string
  level: 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR' | 'CRITICAL'
  module: string
  message: string
}

// 日志缓存的最大数量
const MAX_LOGS = 1000

// 回调函数类型
type LogCallback = () => void
type ConnectionCallback = (connected: boolean) => void

class LogWebSocketManager {
  private ws: WebSocket | null = null
  private logs: LogEntry[] = []
  private logCallbacks: Set<LogCallback> = new Set()
  private connectionCallbacks: Set<ConnectionCallback> = new Set()
  private reconnectTimeout: number | null = null
  private isConnected: boolean = false
  private shouldReconnect: boolean = true

  constructor() {
    // 初始化时尝试连接
    this.connect()
  }

  /**
   * 获取 WebSocket token 并连接
   */
  private async connect(): Promise<void> {
    if (this.ws?.readyState === WebSocket.OPEN || this.ws?.readyState === WebSocket.CONNECTING) {
      return
    }

    try {
      // 获取临时 WebSocket token
      const response = await fetchWithAuth('/api/webui/ws-token')
      if (!response.ok) {
        console.warn('[LogWebSocket] 获取 WebSocket token 失败')
        this.scheduleReconnect()
        return
      }

      const data = await response.json()
      if (!data.success || !data.token) {
        console.warn('[LogWebSocket] 获取 WebSocket token 失败:', data.message)
        this.scheduleReconnect()
        return
      }

      const token = data.token
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
      const wsUrl = `${protocol}//${window.location.host}/ws/logs?token=${token}`

      this.createConnection(wsUrl)
    } catch (error) {
      console.error('[LogWebSocket] 连接失败:', error)
      this.scheduleReconnect()
    }
  }

  /**
   * 创建 WebSocket 连接
   */
  private createConnection(url: string): void {
    this.ws = new WebSocket(url)

    this.ws.onopen = () => {
      console.log('[LogWebSocket] 已连接')
      this.isConnected = true
      this.notifyConnectionChange(true)
    }

    this.ws.onmessage = (event) => {
      try {
        const log: LogEntry = JSON.parse(event.data)
        this.addLog(log)
      } catch (error) {
        console.error('[LogWebSocket] 解析日志失败:', error)
      }
    }

    this.ws.onclose = (event) => {
      console.log('[LogWebSocket] 连接关闭:', event.code, event.reason)
      this.isConnected = false
      this.notifyConnectionChange(false)
      this.ws = null

      // 如果不是正常关闭，尝试重连
      if (this.shouldReconnect && event.code !== 1000) {
        this.scheduleReconnect()
      }
    }

    this.ws.onerror = (error) => {
      console.error('[LogWebSocket] 连接错误:', error)
    }
  }

  /**
   * 安排重连
   */
  private scheduleReconnect(): void {
    if (this.reconnectTimeout) {
      clearTimeout(this.reconnectTimeout)
    }

    this.reconnectTimeout = window.setTimeout(() => {
      if (this.shouldReconnect) {
        this.connect()
      }
    }, 3000)
  }

  /**
   * 添加日志到缓存
   */
  private addLog(log: LogEntry): void {
    // 添加到缓存开头
    this.logs.push(log)

    // 如果超过最大数量，移除最旧的日志
    if (this.logs.length > MAX_LOGS) {
      this.logs = this.logs.slice(-MAX_LOGS)
    }

    // 通知所有订阅者
    this.notifyLogCallbacks()
  }

  /**
   * 通知所有日志订阅者
   */
  private notifyLogCallbacks(): void {
    this.logCallbacks.forEach(callback => {
      try {
        callback()
      } catch (error) {
        console.error('[LogWebSocket] 回调执行失败:', error)
      }
    })
  }

  /**
   * 通知所有连接状态订阅者
   */
  private notifyConnectionChange(connected: boolean): void {
    this.connectionCallbacks.forEach(callback => {
      try {
        callback(connected)
      } catch (error) {
        console.error('[LogWebSocket] 连接状态回调执行失败:', error)
      }
    })
  }

  /**
   * 订阅日志更新
   * @returns 取消订阅的函数
   */
  onLog(callback: LogCallback): () => void {
    this.logCallbacks.add(callback)
    return () => {
      this.logCallbacks.delete(callback)
    }
  }

  /**
   * 订阅连接状态变化
   * @returns 取消订阅的函数
   */
  onConnectionChange(callback: ConnectionCallback): () => void {
    this.connectionCallbacks.add(callback)
    // 立即通知当前状态
    callback(this.isConnected)
    return () => {
      this.connectionCallbacks.delete(callback)
    }
  }

  /**
   * 获取所有缓存的日志
   */
  getAllLogs(): LogEntry[] {
    return [...this.logs]
  }

  /**
   * 清空日志缓存
   */
  clearLogs(): void {
    this.logs = []
    this.notifyLogCallbacks()
  }

  /**
   * 手动重连
   */
  reconnect(): void {
    this.shouldReconnect = true
    if (this.ws) {
      this.ws.close()
    }
    this.connect()
  }

  /**
   * 断开连接
   */
  disconnect(): void {
    this.shouldReconnect = false
    if (this.reconnectTimeout) {
      clearTimeout(this.reconnectTimeout)
      this.reconnectTimeout = null
    }
    if (this.ws) {
      this.ws.close(1000, '用户断开')
      this.ws = null
    }
    this.isConnected = false
    this.notifyConnectionChange(false)
  }
}

// 导出全局单例
export const logWebSocket = new LogWebSocketManager()
