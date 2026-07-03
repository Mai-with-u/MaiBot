import { unifiedWsClient, type ConnectionStatus } from './unified-ws'

interface ChatSessionOpenPayload {
  client?: {
    type?: 'webui' | 'floating' | 'launcher'
    name?: string
    version?: string
  }
  group_id?: string
  group_name?: string
  person_id?: string
  platform?: string
  user_id?: string
  user_name?: string
}

export interface ChatImagePayload {
  name: string
  mime_type: string
  base64: string
}

interface ChatSendOptions {
  images?: ChatImagePayload[]
}

type ChatSessionListener = (message: Record<string, unknown>) => void

/** 浅层比较两个 session.open 负载是否完全一致。 */
function arePayloadsEqual(left: ChatSessionOpenPayload, right: ChatSessionOpenPayload): boolean {
  const keys = new Set<keyof ChatSessionOpenPayload>([
    ...(Object.keys(left) as Array<keyof ChatSessionOpenPayload>),
    ...(Object.keys(right) as Array<keyof ChatSessionOpenPayload>),
  ])
  for (const key of keys) {
    if (key === 'client') {
      if (JSON.stringify(left.client ?? {}) !== JSON.stringify(right.client ?? {})) {
        return false
      }
      continue
    }
    if (left[key] !== right[key]) {
      return false
    }
  }
  return true
}

class ChatWsClient {
  private initialized = false
  private listeners: Map<string, Set<ChatSessionListener>> = new Map()
  private sessionPayloads: Map<string, ChatSessionOpenPayload> = new Map()
  // 记录当前 WS 连接上已打开的会话，避免 React StrictMode 双挂载重复发送 ``session.open``。
  private openedSessions: Set<string> = new Set()
  // 记录正在进行中的打开请求，使同一会话的重复调用复用同一个 Promise。
  private pendingOpens: Map<string, Promise<void>> = new Map()

  private initialize(): void {
    if (this.initialized) {
      return
    }

    unifiedWsClient.addEventListener((message) => {
      if (message.domain !== 'chat' || !message.session) {
        return
      }

      const sessionListeners = this.listeners.get(message.session)
      if (!sessionListeners) {
        return
      }

      sessionListeners.forEach((listener) => {
        try {
          listener(message.data)
        } catch (error) {
          console.error('聊天会话监听器执行失败:', error)
        }
      })
    })

    unifiedWsClient.onReconnect(() => {
      // 重连后需要重新订阅，清空本地“已打开”标记。
      this.openedSessions.clear()
      this.pendingOpens.clear()
      void this.reopenSessions()
    })

    unifiedWsClient.onConnectionChange((connected) => {
      if (!connected) {
        // 连接断开后，下次重新连上需要重新发送 session.open。
        this.openedSessions.clear()
        this.pendingOpens.clear()
      }
    })

    this.initialized = true
  }

  private async reopenSessions(): Promise<void> {
    const reopenTargets = Array.from(this.sessionPayloads.entries())
    for (const [sessionId, payload] of reopenTargets) {
      try {
        await unifiedWsClient.call({
          domain: 'chat',
          method: 'session.open',
          session: sessionId,
          data: {
            ...payload,
            restore: true,
          } as Record<string, unknown>,
        })
        this.openedSessions.add(sessionId)
      } catch (error) {
        console.error(`恢复聊天会话失败 (${sessionId}):`, error)
      }
    }
  }

  async openSession(sessionId: string, payload: ChatSessionOpenPayload): Promise<void> {
    this.initialize()

    const previousPayload = this.sessionPayloads.get(sessionId)
    this.sessionPayloads.set(sessionId, payload)

    // 同一会话上一次打开请求还未完成 → 复用该 Promise，避免重复发送。
    const inflight = this.pendingOpens.get(sessionId)
    if (inflight) {
      await inflight
      return
    }

    // 如果该会话在当前 WS 连接上已经打开，且负载未变化，则跳过，避免服务端重复断开/重连。
    if (
      this.openedSessions.has(sessionId) &&
      previousPayload !== undefined &&
      arePayloadsEqual(previousPayload, payload)
    ) {
      return
    }

    const openPromise = unifiedWsClient
      .call({
        domain: 'chat',
        method: 'session.open',
        session: sessionId,
        data: payload as Record<string, unknown>,
      })
      .then(() => {
        this.openedSessions.add(sessionId)
      })

    this.pendingOpens.set(sessionId, openPromise)
    try {
      await openPromise
    } finally {
      this.pendingOpens.delete(sessionId)
    }
  }

  async closeSession(sessionId: string): Promise<void> {
    this.sessionPayloads.delete(sessionId)
    this.openedSessions.delete(sessionId)
    this.pendingOpens.delete(sessionId)
    if (unifiedWsClient.getStatus() !== 'connected') {
      return
    }

    try {
      await unifiedWsClient.call({
        domain: 'chat',
        method: 'session.close',
        session: sessionId,
        data: {},
      })
    } catch (error) {
      console.warn(`关闭聊天会话失败 (${sessionId}):`, error)
    }
  }

  async sendMessage(
    sessionId: string,
    content: string,
    userName: string,
    options: ChatSendOptions = {}
  ): Promise<void> {
    await unifiedWsClient.call({
      domain: 'chat',
      method: 'message.send',
      session: sessionId,
      data: {
        content,
        images: options.images ?? [],
        user_name: userName,
      },
    })
  }

  async updateNickname(sessionId: string, userName: string): Promise<void> {
    const currentPayload = this.sessionPayloads.get(sessionId)
    if (currentPayload) {
      this.sessionPayloads.set(sessionId, {
        ...currentPayload,
        user_name: userName,
      })
    }

    await unifiedWsClient.call({
      domain: 'chat',
      method: 'session.update_nickname',
      session: sessionId,
      data: {
        user_name: userName,
      },
    })
  }

  onSessionMessage(sessionId: string, listener: ChatSessionListener): () => void {
    this.initialize()
    const sessionListeners = this.listeners.get(sessionId) ?? new Set<ChatSessionListener>()
    sessionListeners.add(listener)
    this.listeners.set(sessionId, sessionListeners)

    return () => {
      const currentListeners = this.listeners.get(sessionId)
      if (!currentListeners) {
        return
      }

      currentListeners.delete(listener)
      if (currentListeners.size === 0) {
        this.listeners.delete(sessionId)
      }
    }
  }

  onConnectionChange(listener: (connected: boolean) => void): () => void {
    return unifiedWsClient.onConnectionChange(listener)
  }

  onStatusChange(listener: (status: ConnectionStatus) => void): () => void {
    return unifiedWsClient.onStatusChange(listener)
  }

  async restart(): Promise<void> {
    await unifiedWsClient.restart()
  }
}

export const chatWsClient = new ChatWsClient()
