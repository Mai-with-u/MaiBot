/**
 * MaiSaka 实时监控 WebSocket 客户端
 *
 * 订阅 maisaka_monitor 主题，接收推理引擎各阶段的实时事件。
 */
import type { WsEventEnvelope } from './unified-ws'

import { unifiedWsClient } from './unified-ws'

// ─── 事件数据类型 ───────────────────────────────────────────────

export interface MaisakaMessage {
  role: string
  content: string | null
  tool_call_id?: string
  tool_calls?: MaisakaToolCall[]
}

export interface MaisakaToolCall {
  id: string
  name: string
  arguments?: Record<string, unknown>
  arguments_raw?: string
  source?: 'reasoning' | 'response' | string
  source_label?: string
}

export interface SessionStartEvent {
  session_id: string
  session_name: string
  is_group_chat?: boolean
  group_id?: string | null
  user_id?: string | null
  platform?: string
  timestamp: number
}

export interface StageStatusEvent {
  session_id: string
  session_name?: string
  platform?: string
  user_id?: string | null
  group_id?: string | null
  stage: string
  detail: string
  round_text: string
  agent_state: string
  stage_started_at: number
  updated_at: number
  timestamp: number
}

export interface StageRemovedEvent {
  session_id: string
  session_name?: string
  platform?: string
  user_id?: string | null
  group_id?: string | null
  timestamp: number
}

export interface StageSnapshotEvent {
  entries: StageStatusEvent[]
  timestamp: number
}

export interface LlmRetryEvent {
  session_id: string
  platform?: string
  user_id?: string | null
  group_id?: string | null
  task_name: string
  request_type: string
  model_name: string
  attempt: number
  max_attempts: number
  reason: string
  retry_interval: number
  timestamp: number
}

export interface LlmErrorEvent {
  session_id: string
  platform?: string
  user_id?: string | null
  group_id?: string | null
  task_name: string
  request_type: string
  model_name: string
  message: string
  timestamp: number
}

export interface MessageIngestedEvent {
  session_id: string
  speaker_name: string
  content: string
  message_id: string
  reply_to?: MaisakaReplyPreview | null
  media?: MaisakaMessageMedia[]
  platform?: string
  user_id?: string
  group_id?: string
  timestamp: number
}

export interface MessageSentEvent {
  session_id: string
  speaker_name: string
  content: string
  message_id: string
  reply_to?: MaisakaReplyPreview | null
  media?: MaisakaMessageMedia[]
  source_kind?: string
  platform?: string
  user_id?: string
  group_id?: string
  timestamp: number
}

export interface MessageUpdatedEvent {
  session_id: string
  speaker_name: string
  content: string
  message_id: string
  reply_to?: MaisakaReplyPreview | null
  media?: MaisakaMessageMedia[]
  source_kind?: string
  platform?: string
  user_id?: string
  group_id?: string
  timestamp: number
}

export interface MaisakaReplyPreview {
  message_id: string
  sender_name: string
  content: string
}

export interface MaisakaMessageMedia {
  kind: 'image' | 'emoji'
  hash: string
  text: string
  url: string
  data_url?: string
  default_original?: boolean
  index?: number
}

export interface TimingGateResultEvent {
  session_id: string
  cycle_id: number
  action: 'continue' | 'wait' | 'no_action'
  content: string | null
  tool_calls: MaisakaToolCall[]
  messages: MaisakaMessage[]
  prompt_tokens: number
  selected_history_count: number
  duration_ms: number
  timestamp: number
}

export interface PlannerRequestEvent {
  session_id: string
  cycle_id: number
  messages: MaisakaMessage[]
  tool_count: number
  selected_history_count: number
  timestamp: number
}

export interface PlannerResponseEvent {
  session_id: string
  cycle_id: number
  content: string | null
  tool_calls: MaisakaToolCall[]
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  duration_ms: number
  timestamp: number
}

export interface ToolExecutionEvent {
  session_id: string
  cycle_id: number
  tool_name: string
  tool_args: Record<string, unknown>
  result_summary: string
  success: boolean
  duration_ms: number
  timestamp: number
}

export interface MaisakaRequestBlock {
  messages: MaisakaMessage[]
  selected_history_count: number
  tool_count: number
}

export interface MaisakaPlannerBlock {
  content: string | null
  tool_calls: MaisakaToolCall[]
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  duration_ms: number
  prompt_html_uri?: string
}

export interface MaisakaTimingGateBlock {
  request: MaisakaRequestBlock | null
  result: {
    action: 'continue' | 'wait' | 'no_action' | null
    content: string | null
    tool_calls: MaisakaToolCall[]
    tool_results: unknown[]
    prompt_tokens: number
    completion_tokens: number
    total_tokens: number
    duration_ms: number
  }
}

export interface MaisakaFinalizedToolResult {
  tool_call_id: string
  tool_name: string
  tool_args: Record<string, unknown>
  tool_call_source?: string
  tool_call_source_label?: string
  success: boolean
  duration_ms: number
  summary: string
  prompt_html_uri?: string
  detail?: unknown
}

export interface PlannerFinalizedEvent {
  session_id: string
  cycle_id: number
  timestamp: number
  timing_gate: MaisakaTimingGateBlock | null
  request: MaisakaRequestBlock | null
  planner: MaisakaPlannerBlock | null
  tools: MaisakaFinalizedToolResult[]
  interrupted?: boolean
  final_state: {
    time_records: Record<string, number>
    agent_state: string
    end_reason?: string
    end_detail?: string
  }
}

export interface ReplierRequestEvent {
  session_id: string
  messages: MaisakaMessage[]
  model_name: string
  timestamp: number
}

export interface ReplierResponseEvent {
  session_id: string
  content: string | null
  reasoning: string
  model_name: string
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  duration_ms: number
  success: boolean
  timestamp: number
}

// ─── 统一事件联合类型 ─────────────────────────────────────────

export type MaisakaMonitorEvent =
  | { type: 'session.start'; data: SessionStartEvent }
  | { type: 'stage.status'; data: StageStatusEvent }
  | { type: 'stage.removed'; data: StageRemovedEvent }
  | { type: 'stage.snapshot'; data: StageSnapshotEvent }
  | { type: 'llm.retry'; data: LlmRetryEvent }
  | { type: 'llm.error'; data: LlmErrorEvent }
  | { type: 'message.ingested'; data: MessageIngestedEvent }
  | { type: 'message.sent'; data: MessageSentEvent }
  | { type: 'message.updated'; data: MessageUpdatedEvent }
  | { type: 'timing_gate.result'; data: TimingGateResultEvent }
  | { type: 'planner.request'; data: PlannerRequestEvent }
  | { type: 'planner.response'; data: PlannerResponseEvent }
  | { type: 'planner.finalized'; data: PlannerFinalizedEvent }
  | { type: 'tool.execution'; data: ToolExecutionEvent }
  | { type: 'replier.request'; data: ReplierRequestEvent }
  | { type: 'replier.response'; data: ReplierResponseEvent }

export type MaisakaEventListener = (event: MaisakaMonitorEvent) => void

// ─── 客户端 ───────────────────────────────────────────────────

class MaisakaMonitorClient {
  private initialized = false
  private readonly initialReplayLimit = 1000
  private listenerIdCounter = 0
  private listeners: Map<number, MaisakaEventListener> = new Map()
  private replayCursor = 0
  private readonly replayLimit = 10000
  private subscriptionActive = false
  private subscriptionPromise: Promise<void> | null = null
  private deferredUnsubTimer: ReturnType<typeof setTimeout> | null = null

  private initialize(): void {
    if (this.initialized) {
      return
    }

    unifiedWsClient.addEventListener((message: WsEventEnvelope) => {
      if (message.domain !== 'maisaka_monitor') {
        return
      }

      const event: MaisakaMonitorEvent = {
        type: message.event as MaisakaMonitorEvent['type'],
        data: message.data as never,
      }

      this.listeners.forEach((listener) => {
        try {
          listener(event)
        } catch (error) {
          console.error('MaiSaka 监控事件监听器执行失败:', error)
        }
      })
    })

    this.initialized = true
  }

  private getReplaySubscribeData(): Record<string, unknown> {
    return {
      since_event_id: this.replayCursor,
      replay_limit: this.replayCursor > 0 ? this.replayLimit : this.initialReplayLimit,
    }
  }

  private async ensureSubscribed(): Promise<boolean> {
    if (this.subscriptionActive) {
      return false
    }

    if (this.subscriptionPromise === null) {
      this.subscriptionPromise = unifiedWsClient
        .subscribe('maisaka_monitor', 'main', this.getReplaySubscribeData())
        .then(() => {
          this.subscriptionActive = true
        })
        .finally(() => {
          this.subscriptionPromise = null
        })
    }

    await this.subscriptionPromise
    return true
  }

  private async replayFromCursor(): Promise<void> {
    await unifiedWsClient.subscribe('maisaka_monitor', 'main', this.getReplaySubscribeData())
  }

  updateReplayCursor(eventId: number): void {
    if (!Number.isFinite(eventId) || eventId <= this.replayCursor) {
      return
    }
    this.replayCursor = Math.floor(eventId)
    unifiedWsClient.updateSubscriptionData('maisaka_monitor', 'main', this.getReplaySubscribeData())
  }

  setInitialReplayCursor(eventId: number): void {
    if (!Number.isFinite(eventId) || eventId < 0) {
      return
    }
    this.replayCursor = Math.max(this.replayCursor, Math.floor(eventId))
  }

  async subscribe(listener: MaisakaEventListener): Promise<() => Promise<void>> {
    this.initialize()
    const listenerId = ++this.listenerIdCounter
    this.listeners.set(listenerId, listener)

    // 如果有待执行的延迟退订，取消它（React StrictMode 快速卸载/重新挂载）
    if (this.deferredUnsubTimer !== null) {
      clearTimeout(this.deferredUnsubTimer)
      this.deferredUnsubTimer = null
    }

    const createdSubscription = await this.ensureSubscribed()
    if (!createdSubscription) {
      await this.replayFromCursor()
    }

    return async () => {
      this.listeners.delete(listenerId)
      if (this.listeners.size === 0 && this.subscriptionActive) {
        // 延迟退订：等待短暂时间再真正退订，避免 StrictMode 导致的竞态
        this.deferredUnsubTimer = setTimeout(() => {
          this.deferredUnsubTimer = null
          if (this.listeners.size === 0 && this.subscriptionActive) {
            this.subscriptionActive = false
            void unifiedWsClient.unsubscribe('maisaka_monitor', 'main')
          }
        }, 200)
      }
    }
  }
}

export const maisakaMonitorClient = new MaisakaMonitorClient()
