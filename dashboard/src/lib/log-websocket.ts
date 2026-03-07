/**
 * 全局日志 WebSocket 管理器
 * 确保整个应用只有一个 WebSocket 连接
 */

import { checkAuthStatus } from './fetch-with-auth'
import { getSetting } from './settings-manager'
import { createReconnectingWebSocket } from './ws-utils'

import { getWsBaseUrl } from '@/lib/api-base'

export interface LogEntry {
  id: string
  timestamp: string
  level: 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR' | 'CRITICAL'
  module: string
  message: string
}

type LogCallback = (log: LogEntry) => void
type ConnectionCallback = (connected: boolean) => void

class LogWebSocketManager {
  private wsControl: ReturnType<typeof createReconnectingWebSocket> | null = null
  
  // 订阅者
  private logCallbacks: Set<LogCallback> = new Set()
  private connectionCallbacks: Set<ConnectionCallback> = new Set()
  
  private isConnected = false
  
  // 日志缓存 - 保存所有接收到的日志
  private logCache: LogEntry[] = []

  /**
   * 获取最大缓存大小（从设置读取）
   */
  private getMaxCacheSize(): number {
    return getSetting('logCacheSize')
  }

  /**
   * 获取最大重连次数（从设置读取）
   */
  private getMaxReconnectAttempts(): number {
    return getSetting('wsMaxReconnectAttempts')
  }

  /**
   * 获取重连间隔（从设置读取）
   */
  private getReconnectInterval(): number {
    return getSetting('wsReconnectInterval')
  }

  /**
   * 获取 WebSocket URL（不含 token 参数）
   */
  private async getWebSocketUrl(): Promise<string> {
    const wsBase = await getWsBaseUrl()
    return `${wsBase}/ws/logs`
  }

  /**
   * 连接 WebSocket（会先检查登录状态）
   */
  async connect() {
    // 检查是否在登录页面
    if (window.location.pathname === '/auth') {
      console.log('📡 在登录页面，跳过 WebSocket 连接')
      return
    }

    // 检查登录状态，避免未登录时尝试连接
    const isAuthenticated = await checkAuthStatus()
    if (!isAuthenticated) {
      console.log('📡 未登录，跳过 WebSocket 连接')
      return
    }

    const wsUrl = await this.getWebSocketUrl()

    // 使用 ws-utils 创建 WebSocket
    this.wsControl = createReconnectingWebSocket(wsUrl, {
      onMessage: (data: string) => {
        try {
          const log: LogEntry = JSON.parse(data)
          this.notifyLog(log)
        } catch (error) {
          console.error('解析日志消息失败:', error)
        }
      },
      onOpen: () => {
        this.isConnected = true
        this.notifyConnection(true)
      },
      onClose: () => {
        this.isConnected = false
        this.notifyConnection(false)
      },
      onError: (error) => {
        console.error('❌ WebSocket 错误:', error)
        this.isConnected = false
        this.notifyConnection(false)
      },
      heartbeatInterval: 30000,
      maxRetries: this.getMaxReconnectAttempts(),
      backoffBase: this.getReconnectInterval(),
      maxBackoff: 30000,
    })

    // 启动连接
    await this.wsControl.connect()
  }

  /**
   * 断开连接
   */
  disconnect() {
    if (this.wsControl) {
      this.wsControl.disconnect()
      this.wsControl = null
    }

    this.isConnected = false
  }

  /**
   * 订阅日志消息
   */
  onLog(callback: LogCallback) {
    this.logCallbacks.add(callback)
    return () => this.logCallbacks.delete(callback)
  }

  /**
   * 订阅连接状态
   */
  onConnectionChange(callback: ConnectionCallback) {
    this.connectionCallbacks.add(callback)
    // 立即通知当前状态
    callback(this.isConnected)
    return () => this.connectionCallbacks.delete(callback)
  }

  /**
   * 通知所有订阅者新日志
   */
  private notifyLog(log: LogEntry) {
    // 检查是否已存在（通过 id 去重）
    const exists = this.logCache.some(existingLog => existingLog.id === log.id)
    
    if (!exists) {
      // 添加到缓存
      this.logCache.push(log)
      
      // 限制缓存大小（动态读取配置）
      const maxCacheSize = this.getMaxCacheSize()
      if (this.logCache.length > maxCacheSize) {
        this.logCache = this.logCache.slice(-maxCacheSize)
      }
      
      // 只有新日志才通知订阅者
      this.logCallbacks.forEach(callback => {
        try {
          callback(log)
        } catch (error) {
          console.error('日志回调执行失败:', error)
        }
      })
    }
  }

  /**
   * 通知所有订阅者连接状态变化
   */
  private notifyConnection(connected: boolean) {
    this.connectionCallbacks.forEach(callback => {
      try {
        callback(connected)
      } catch (error) {
        console.error('连接状态回调执行失败:', error)
      }
    })
  }

  /**
   * 获取缓存的所有日志
   */
  getAllLogs(): LogEntry[] {
    return [...this.logCache]
  }

  /**
   * 清空日志缓存
   */
  clearLogs() {
    this.logCache = []
  }

  /**
   * 获取当前连接状态
   */
  getConnectionStatus(): boolean {
    return this.isConnected
  }
}

// 导出单例
export const logWebSocket = new LogWebSocketManager()

// 自动连接（应用启动时）
if (typeof window !== 'undefined') {
  // 延迟一下确保页面加载完成
  setTimeout(() => {
    logWebSocket.connect()
  }, 100)
}
