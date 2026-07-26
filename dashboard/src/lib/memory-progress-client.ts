import { unifiedWsClient, type WsEventEnvelope } from './unified-ws'

export type MemoryProgressTopic = 'import_progress' | 'delete_progress' | 'feedback_progress'

export interface MemoryProgressEvent {
  topic: MemoryProgressTopic
  event: string
  data: Record<string, unknown>
}

type ProgressListener = (event: MemoryProgressEvent) => void

const DOMAIN = 'memory'
const KNOWN_TOPICS: MemoryProgressTopic[] = [
  'import_progress',
  'delete_progress',
  'feedback_progress',
]

/**
 * 长期记忆控制台的统一 WebSocket 桥接客户端。
 *
 * 负责：
 *  1. 订阅 `memory` 域下的若干 topic（导入/删除/反馈进度）。
 *  2. 把后端推送的事件分发给所有已注册的监听器。
 *  3. 即使后端尚未广播也保持安全：监听器为空时不抛错，订阅幂等。
 *
 * 与 `pluginProgressClient` 保持一致的形状，便于复用。
 */
class MemoryProgressClient {
  private initialized = false
  private listeners: Set<ProgressListener> = new Set()
  private activeTopics: Set<MemoryProgressTopic> = new Set()

  private initialize(): void {
    if (this.initialized) {
      return
    }

    unifiedWsClient.addEventListener((message: WsEventEnvelope) => {
      if (message.domain !== DOMAIN) {
        return
      }
      const topic = (message.topic ?? '') as MemoryProgressTopic
      if (!KNOWN_TOPICS.includes(topic)) {
        return
      }
      const payload: MemoryProgressEvent = {
        topic,
        event: message.event,
        data: message.data ?? {},
      }
      this.listeners.forEach((listener) => {
        try {
          listener(payload)
        } catch (error) {
          console.error('长期记忆进度监听器执行失败:', error)
        }
      })
    })

    this.initialized = true
  }

  async subscribe(
    listener: ProgressListener,
    topics: MemoryProgressTopic[] = KNOWN_TOPICS
  ): Promise<() => Promise<void>> {
    this.initialize()
    this.listeners.add(listener)

    // 仅订阅尚未激活的 topic，避免重复 subscribe
    for (const topic of topics) {
      if (this.activeTopics.has(topic)) {
        continue
      }
      try {
        await unifiedWsClient.subscribe(DOMAIN, topic)
        this.activeTopics.add(topic)
      } catch (error) {
        // 后端可能尚未实现该 topic，订阅失败时只记录，不抛出，确保 polling 仍可作为兜底
        console.warn(`订阅长期记忆 topic 失败（将退化到轮询兜底）: ${topic}`, error)
      }
    }

    return async () => {
      this.listeners.delete(listener)
      if (this.listeners.size === 0) {
        const topicsToRelease = Array.from(this.activeTopics)
        this.activeTopics.clear()
        for (const topic of topicsToRelease) {
          try {
            await unifiedWsClient.unsubscribe(DOMAIN, topic)
          } catch (error) {
            console.warn(`取消订阅长期记忆 topic 失败: ${topic}`, error)
          }
        }
      }
    }
  }
}

export const memoryProgressClient = new MemoryProgressClient()
