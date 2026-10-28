/**
 * MaiSaka 实时监控 WebSocket 客户端
 *
 * 订阅 maisaka_monitor 主题，接收推理引擎各阶段的实时事件。
 */
import type { WsEventEnvelope } from './unified-ws'

import { PlannerDeltaDecoder } from './maisaka-planner-delta'
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
  wait_until?: number | null
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

export interface FlowStepEvent {
  session_id: string
  run_id: string
  cycle_id: number | null
  step: string
  title: string
  detail: string
  timestamp: number
  trim?: {
    removed_count: number
    remaining_count: number
    learning: {
      status: 'pending' | 'running' | 'waiting' | 'completed' | 'no_results' | 'skipped' | 'cancelled' | 'failed'
      detail: string
      learners: {
        name: string
        status: 'running' | 'waiting' | 'completed' | 'no_results' | 'skipped' | 'disabled' | 'failed' | 'cancelled'
        detail: string
        contents: string[]
      }[]
    }
  }
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
  /** 完整请求仅旧版监控载荷携带；新版通过推理详情页读取。 */
  messages?: MaisakaMessage[]
  selected_history_count: number
  tool_count: number
  context_sections?: MaisakaContextSection[]
}

/** 提示词分段用量：字符数与条目数，token 由前端按占比换算 */
export interface MaisakaContextSection {
  key: string
  chars: number
  count: number
}

export interface MaisakaNativeToolCall {
  tool_type: string
  call_id: string
  status: string
  action_type: string
  details: string[]
  source_count: number
}

export interface MaisakaPlannerBlock {
  content: string | null
  reasoning?: string | null
  model_name?: string | null
  tool_calls: MaisakaToolCall[]
  native_tool_calls?: MaisakaNativeToolCall[]
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  prompt_cache_hit_tokens?: number
  prompt_cache_miss_tokens?: number
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
  session_id?: string
  session_name?: string
  started_at?: number
  ended_at?: number
  tool_call_id: string
  tool_name: string
  model_name?: string
  images?: { thumbnail_url: string; label: string; error?: string }[]
  tool_args: Record<string, unknown>
  tool_call_source?: string
  tool_call_source_label?: string
  success: boolean
  stop_after_execution?: boolean
  duration_ms: number
  summary: string
  prompt_html_uri?: string
  detail?: unknown
  matched_tool_names?: string[]
  newly_discovered_tool_names?: string[]
}

export interface PlannerFinalizedEvent {
  focus_scope_id?: string
  focus_session_ids?: string[]
  planner_started_at?: number | null
  planner_ended_at?: number | null
  session_id: string
  cycle_id: number
  run_id?: string
  timestamp: number
  timing_gate: MaisakaTimingGateBlock | null
  request: MaisakaRequestBlock | null
  planner: MaisakaPlannerBlock | null
  tools: MaisakaFinalizedToolResult[]
  active_tool_call_id?: string
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

/** 子代理任务开始；call_id 是 Planner 发起这次派发的工具调用 id */
export interface SubagentStartedEvent {
  session_id: string
  task_id: string
  call_id: string
  description: string
  granted_tools: string[]
  /** 是否在后台运行（开启高级子代理） */
  background: boolean
  max_rounds: number
  timestamp: number
}

/** 子代理一轮模型输出 */
export interface SubagentRoundEvent {
  session_id: string
  task_id: string
  round_index: number
  content: string
  tool_calls: MaisakaToolCall[]
  timestamp: number
}

/** 子代理内部一次工具调用的结果摘要 */
export interface SubagentToolResultEvent {
  session_id: string
  task_id: string
  round_index: number
  call_id: string
  tool_name: string
  success: boolean
  summary: string
  duration_ms: number
  timestamp: number
}

export type SubagentTaskStatus = 'running' | 'completed' | 'failed' | 'stopped'

/** 子代理任务结束：完成、失败或被终止 */
export interface SubagentFinishedEvent {
  session_id: string
  task_id: string
  status: SubagentTaskStatus
  conclusion: string
  error_message: string
  tool_usage: Record<string, number>
  duration_ms: number
  timestamp: number
}

/** 订阅后下发的单个聊天流概要；时间线本身按页另行加载 */
export interface SessionSummary {
  session_id: string
  event_count: number
  last_event_id: number
  last_activity: number
  session_start: SessionStartEvent | null
  latest_message: {
    event: 'message.ingested' | 'message.sent'
    data: MessageIngestedEvent | MessageSentEvent
  } | null
}

export interface SessionsSnapshotEvent {
  sessions: SessionSummary[]
  timestamp: number
}

/** 随首页历史补发的子代理事件，只用于重建子代理面板，不进入时间线 */
export interface SubagentHistoryEvent {
  session_id: string
  events: { event: string; data: Record<string, unknown> }[]
}

/** 一页历史加载结果；事件本身经事件通道先于该结果到达 */
export interface MonitorHistoryPage {
  count: number
  oldestEventId: number
  hasMore: boolean
}

export type MaisakaMonitorEvent =
  | { type: 'replay.started'; data: Record<string, never> }
  | { type: 'sessions.snapshot'; data: SessionsSnapshotEvent }
  | { type: 'subagent.history'; data: SubagentHistoryEvent }
  | { type: 'session.start'; data: SessionStartEvent }
  | { type: 'stage.status'; data: StageStatusEvent }
  | { type: 'stage.removed'; data: StageRemovedEvent }
  | { type: 'stage.snapshot'; data: StageSnapshotEvent }
  | { type: 'flow.step'; data: FlowStepEvent }
  | { type: 'llm.retry'; data: LlmRetryEvent }
  | { type: 'llm.error'; data: LlmErrorEvent }
  | { type: 'message.ingested'; data: MessageIngestedEvent }
  | { type: 'message.sent'; data: MessageSentEvent }
  | { type: 'message.updated'; data: MessageUpdatedEvent }
  | { type: 'timing_gate.result'; data: TimingGateResultEvent }
  | { type: 'planner.request'; data: PlannerRequestEvent }
  | { type: 'planner.response'; data: PlannerResponseEvent }
  | { type: 'planner.finalized'; data: PlannerFinalizedEvent }
  | { type: 'planner.progress'; data: PlannerFinalizedEvent }
  | { type: 'tool.execution'; data: ToolExecutionEvent }
  | { type: 'replier.request'; data: ReplierRequestEvent }
  | { type: 'replier.response'; data: ReplierResponseEvent }
  | { type: 'subagent.started'; data: SubagentStartedEvent }
  | { type: 'subagent.round'; data: SubagentRoundEvent }
  | { type: 'subagent.tool_result'; data: SubagentToolResultEvent }
  | { type: 'subagent.finished'; data: SubagentFinishedEvent }

export type MaisakaEventListener = (event: MaisakaMonitorEvent) => void

// ─── 客户端 ───────────────────────────────────────────────────

/** 每页拉取的历史事件数 */
const HISTORY_PAGE_SIZE = 100
/** 历史页经事件通道逐条下发，慢链路上比普通请求耗时更长 */
const HISTORY_REQUEST_TIMEOUT_MS = 30000

class MaisakaMonitorClient {
  private readonly plannerDecoder = new PlannerDeltaDecoder()
  private initialized = false
  private listenerIdCounter = 0
  private listeners: Map<number, MaisakaEventListener> = new Map()
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

      // 先重建快照再去重；即使基准事件已入账，也必须更新解码基准。
      let decoded: WsEventEnvelope | null
      try {
        decoded = this.plannerDecoder.decode(message)
      } catch (error) {
        // 基准损坏后继续收增量会一直丢失内容，重连从账本恢复完整快照。
        console.error('MaiSaka 监控增量解码失败，重新连接:', error)
        void unifiedWsClient.restart().catch((restartError) => {
          console.error('MaiSaka 监控重新连接失败:', restartError)
        })
        return
      }
      if (!decoded && message.event !== 'planner.reset') return
      const event: MaisakaMonitorEvent = decoded
        ? { type: decoded.event as MaisakaMonitorEvent['type'], data: decoded.data as never }
        : { type: 'replay.started', data: {} }

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
      // 订阅只同步各聊天流概要与当前阶段，时间线由 loadHistory 按聊天流分页拉取。
      mode: 'paged',
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

  private async replayHistory(): Promise<void> {
    await unifiedWsClient.subscribe('maisaka_monitor', 'main', this.getReplaySubscribeData())
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

    try {
      const createdSubscription = await this.ensureSubscribed()
      if (!createdSubscription) {
        await this.replayHistory()
      }
    } catch (error) {
      // 订阅失败时调用方拿不到退订函数，必须在这里回收监听器
      this.listeners.delete(listenerId)
      throw error
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

  /** 拉取某个聊天流早于 beforeEventId 的一页历史；不传时拉取最新一页 */
  async loadHistory(
    sessionId: string,
    beforeEventId?: number,
    limit = HISTORY_PAGE_SIZE
  ): Promise<MonitorHistoryPage> {
    const result = await unifiedWsClient.call({
      domain: 'maisaka_monitor',
      method: 'history.load',
      data: { session_id: sessionId, before_event_id: beforeEventId ?? 0, limit },
      timeoutMs: HISTORY_REQUEST_TIMEOUT_MS,
    })
    return {
      count: Number(result.count) || 0,
      oldestEventId: Number(result.oldest_event_id) || 0,
      hasMore: result.has_more === true,
    }
  }

  onConnectionChange(listener: (connected: boolean) => void): () => void {
    return unifiedWsClient.onConnectionChange(listener)
  }
}

export const maisakaMonitorClient = new MaisakaMonitorClient()
