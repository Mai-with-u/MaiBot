import { fetchWithAuth } from './fetch-with-auth'

/**
 * WebSocket 配置选项
 */
export interface WebSocketOptions {
  onMessage?: (data: string) => void
  onOpen?: () => void
  onClose?: () => void
  onError?: (error: Event) => void
  heartbeatInterval?: number  // 心跳间隔（毫秒）
  maxRetries?: number  // 最大重连次数
  backoffBase?: number  // 重连基础间隔（毫秒）
  maxBackoff?: number  // 最大重连间隔（毫秒）
}

/**
 * 获取 WebSocket 临时认证 token
 */
export async function getWsToken(): Promise<string | null> {
  try {
    // 使用相对路径，让前端代理处理请求，避免 CORS 问题
    const response = await fetchWithAuth('/api/webui/ws-token', {
      method: 'GET',
      credentials: 'include', // 携带 Cookie
    })
    
    if (!response.ok) {
      console.error('获取 WebSocket token 失败:', response.status)
      return null
    }
    
    const data = await response.json()
    if (data.success && data.token) {
      return data.token
    }
    return null
  } catch (error) {
    console.error('获取 WebSocket token 失败:', error)
    return null
  }
}

/**
 * 创建带重连、心跳的 WebSocket 封装
 * 
 * @param url WebSocket URL（不含 token 参数）
 * @param options 配置选项
 * @returns WebSocket 控制对象，包含 connect、disconnect、send 方法
 */
export function createReconnectingWebSocket(
  url: string,
  options: WebSocketOptions = {}
) {
  const {
    onMessage,
    onOpen,
    onClose,
    onError,
    heartbeatInterval = 30000,
    maxRetries = 10,
    backoffBase = 1000,
    maxBackoff = 30000,
  } = options

  let ws: WebSocket | null = null
  let reconnectTimeout: number | null = null
  let reconnectAttempts = 0
  let heartbeatIntervalId: number | null = null
  let isManualDisconnect = false

  /**
   * 启动心跳
   */
  function startHeartbeat() {
    stopHeartbeat()
    heartbeatIntervalId = window.setInterval(() => {
      if (ws?.readyState === WebSocket.OPEN) {
        ws.send('ping')
      }
    }, heartbeatInterval)
  }

  /**
   * 停止心跳
   */
  function stopHeartbeat() {
    if (heartbeatIntervalId !== null) {
      clearInterval(heartbeatIntervalId)
      heartbeatIntervalId = null
    }
  }

  /**
   * 尝试重连
   */
  function attemptReconnect() {
    if (isManualDisconnect) {
      return
    }

    if (reconnectAttempts >= maxRetries) {
      console.warn(`WebSocket 达到最大重连次数 (${maxRetries})，停止重连`)
      return
    }

    reconnectAttempts += 1
    const delay = Math.min(backoffBase * reconnectAttempts, maxBackoff)

    console.log(`WebSocket 将在 ${delay}ms 后重连（第 ${reconnectAttempts} 次）`)
    reconnectTimeout = window.setTimeout(() => {
      connect()
    }, delay)
  }

  /**
   * 连接 WebSocket
   */
  async function connect() {
    if (ws?.readyState === WebSocket.OPEN || ws?.readyState === WebSocket.CONNECTING) {
      return
    }

    // 先获取临时认证 token
    const wsToken = await getWsToken()
    if (!wsToken) {
      console.warn('无法获取 WebSocket token，跳过连接')
      return
    }

    const wsUrl = `${url}?token=${encodeURIComponent(wsToken)}`

    try {
      ws = new WebSocket(wsUrl)

      ws.onopen = () => {
        reconnectAttempts = 0
        startHeartbeat()
        onOpen?.()
      }

      ws.onmessage = (event) => {
        // 忽略心跳响应
        if (event.data === 'pong') {
          return
        }
        onMessage?.(event.data)
      }

      ws.onerror = (error) => {
        console.error('WebSocket 错误:', error)
        onError?.(error)
      }

      ws.onclose = () => {
        stopHeartbeat()
        onClose?.()
        attemptReconnect()
      }
    } catch (error) {
      console.error('创建 WebSocket 连接失败:', error)
      attemptReconnect()
    }
  }

  /**
   * 断开连接
   */
  function disconnect() {
    isManualDisconnect = true

    if (reconnectTimeout !== null) {
      clearTimeout(reconnectTimeout)
      reconnectTimeout = null
    }

    stopHeartbeat()

    if (ws) {
      ws.close()
      ws = null
    }

    reconnectAttempts = 0
  }

  /**
   * 发送消息
   */
  function send(data: string) {
    if (ws?.readyState === WebSocket.OPEN) {
      ws.send(data)
    } else {
      console.warn('WebSocket 未连接，无法发送消息')
    }
  }

  /**
   * 获取当前 WebSocket 实例
   */
  function getWebSocket(): WebSocket | null {
    return ws
  }

  return {
    connect,
    disconnect,
    send,
    getWebSocket,
  }
}
