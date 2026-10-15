/**
 * MaiSaka 聊天流实时监控 - React Hook
 *
 * 管理 WebSocket 订阅与事件流的状态。
 */
import { useSyncExternalStore } from 'react'

import type {
  MaisakaMonitorEvent,
  MaisakaToolCall,
  MonitorHistoryPage,
  SessionsSnapshotEvent,
  SubagentHistoryEvent,
  SubagentFinishedEvent,
  SubagentRoundEvent,
  SubagentStartedEvent,
  SubagentTaskStatus,
  SubagentToolResultEvent,
} from '@/lib/maisaka-monitor-client'
import { maisakaMonitorClient } from '@/lib/maisaka-monitor-client'

/** 单条时间线事件（前端视图模型） */
export interface TimelineEntry {
  /** 唯一 ID */
  id: string
  /** 后端事件账本 ID */
  eventId?: number
  /** 事件类型 */
  type: MaisakaMonitorEvent['type']
  /** 原始事件数据 */
  data: MaisakaMonitorEvent['data']
  /** 事件时间戳 */
  timestamp: number
  /** 所属会话 ID */
  sessionId: string
}

/** 会话概要信息 */
export interface SessionInfo {
  sessionId: string
  sessionName: string
  isGroupChat?: boolean
  groupId?: string | null
  userId?: string | null
  platform?: string
  lastActivity: number
  eventCount: number
}

export interface StageStatusInfo {
  sessionId: string
  sessionName?: string
  stage: string
  detail: string
  roundText: string
  agentState: string
  waitUntil?: number
  stageStartedAt: number
  updatedAt: number
}

/** 子代理一轮里的一次工具调用及其结果 */
export interface SubagentToolCallInfo {
  call: MaisakaToolCall
  result?: { success: boolean; summary: string; durationMs: number }
}

/** 子代理的一轮模型输出 */
export interface SubagentRoundInfo {
  roundIndex: number
  content: string
  timestamp: number
  toolCalls: SubagentToolCallInfo[]
}

/** 一个子代理任务的完整运行情况（由 subagent.* 事件累积而成） */
export interface SubagentTaskInfo {
  /** 前端唯一键：后端任务 id 在重启后会从头编号，所以带上开始时间 */
  key: string
  taskId: string
  sessionId: string
  /** Planner 发起这次派发的工具调用 id，用于从时间线卡片定位到任务 */
  callId: string
  description: string
  grantedTools: string[]
  background: boolean
  maxRounds: number
  status: SubagentTaskStatus
  startedAt: number
  finishedAt?: number
  durationMs?: number
  rounds: SubagentRoundInfo[]
  toolUsage: Record<string, number>
  conclusion: string
  errorMessage: string
}

/** 每个会话保留的子代理任务数量上限 */
const MAX_SUBAGENT_TASKS_PER_SESSION = 50

/** 前端内存中最多恢复/展示的时间线条目数，避免一次渲染过多节点。 */
const MAX_TIMELINE_ENTRIES = 3000

function resolveSessionDisplayName({
  fallbackName,
  groupId,
  isGroupChat,
  sessionId,
  userId,
}: {
  fallbackName?: string
  groupId?: string | null
  isGroupChat?: boolean
  sessionId: string
  userId?: string | null
}) {
  const targetId = isGroupChat ? groupId : userId
  const normalizedName = fallbackName?.trim()

  if (targetId && normalizedName?.endsWith(`(${targetId})`)) {
    return normalizedName
  }
  if (normalizedName && targetId && normalizedName !== targetId && normalizedName !== sessionId) {
    return `${normalizedName}(${targetId})`
  }
  if (isGroupChat && groupId) {
    return groupId
  }
  if (!isGroupChat && userId) {
    return userId
  }
  return fallbackName || sessionId.slice(0, 8)
}

let entryCounter = 0
let cachedTimeline: TimelineEntry[] = []
const emptyTimeline: TimelineEntry[] = []
const timelinesBySession = new Map<string, TimelineEntry[]>()
const entriesById = new Map<string, TimelineEntry>()
const messageEntryIds = new Map<string, string>()
const mutableEntryIds = new Map<string, string>()
let cachedLatestMessages = new Map<string, TimelineEntry>()
let cachedSessions: Map<string, SessionInfo> = new Map()
let cachedStageStatuses: Map<string, StageStatusInfo> = new Map()
const emptySubagentTasks: SubagentTaskInfo[] = []
let cachedSubagentTasks: Map<string, SubagentTaskInfo[]> = new Map()
let cachedSelectedSession: string | null = null
/** 各聊天流已计入 eventCount 的最大事件号；翻页加载的旧事件不重复计数 */
const countedEventIds = new Map<string, number>()
/** 目标消息尚未加载时暂存的 message.updated，等该消息随历史页到达后再应用 */
const orphanMessageUpdates = new Map<string, MaisakaMonitorEvent>()
const MAX_ORPHAN_MESSAGE_UPDATES = 500
/** 各聊天流按事件号升序保存的子代理相关事件，乱序到达时据此重建任务状态 */
const subagentLogs = new Map<string, MaisakaMonitorEvent[]>()
const MAX_SUBAGENT_LOG_EVENTS = 2000

/** 单个聊天流的历史分页状态 */
interface SessionHistoryState {
  /** 本次订阅期间是否已同步过最新一页 */
  loaded: boolean
  loading: boolean
  hasMore: boolean
}
const defaultHistoryState: SessionHistoryState = { loaded: false, loading: false, hasMore: true }
const historyStates = new Map<string, SessionHistoryState>()
/** 重新订阅时各聊天流已有条目的最大事件号；其后可能漏掉事件，下次同步最新一页时据此判断是否断档 */
const staleBoundaries = new Map<string, number>()
/** 每次重新订阅或清空后递增，用于丢弃过期的历史加载结果 */
let historyGeneration = 0
let maxObservedEventId = 0
/** 用户清空时间线时的最大事件号，此前的事件不再通过历史加载恢复 */
let clearedThroughEventId = 0
let cachedConnected = false
let activeConsumerCount = 0
let monitorSubscriptionStarted = false
let monitorSubscriptionPromise: Promise<void> | null = null
let monitorUnsubscribe: (() => Promise<void>) | null = null
let monitorConnectionUnsubscribe: (() => void) | null = null
let monitorInitialSyncPending = false
const storeListeners = new Set<() => void>()
let notificationPending = false
let pendingReplayEvents: MaisakaMonitorEvent[] = []

function toStageStatusInfo(raw: Record<string, unknown>): StageStatusInfo | null {
  const sessionId = typeof raw.session_id === 'string' ? raw.session_id : ''
  if (!sessionId) {
    return null
  }
  return {
    sessionId,
    sessionName: typeof raw.session_name === 'string' ? raw.session_name : undefined,
    stage: typeof raw.stage === 'string' ? raw.stage : '',
    detail: typeof raw.detail === 'string' ? raw.detail : '',
    roundText: typeof raw.round_text === 'string' ? raw.round_text : '',
    agentState: typeof raw.agent_state === 'string' ? raw.agent_state : '',
    waitUntil: typeof raw.wait_until === 'number' ? raw.wait_until : undefined,
    stageStartedAt:
      typeof raw.stage_started_at === 'number' ? raw.stage_started_at : Date.now() / 1000,
    updatedAt: typeof raw.updated_at === 'number' ? raw.updated_at : Date.now() / 1000,
  }
}

function notifyStoreListeners() {
  // 历史补发结束后一次性展示；实时事件按帧合并，避免连续重排和滚动。
  if (monitorInitialSyncPending || notificationPending || storeListeners.size === 0) return
  notificationPending = true
  requestAnimationFrame(() => {
    notificationPending = false
    if (monitorInitialSyncPending) return
    publishSnapshots()
    storeListeners.forEach((listener) => listener())
  })
}

function shouldKeepMonitorActive() {
  return activeConsumerCount > 0
}

function appendTimelineEntry(entry: TimelineEntry) {
  cachedTimeline = insertTimelineEntry(cachedTimeline, entry)
  timelinesBySession.set(
    entry.sessionId,
    insertTimelineEntry(timelinesBySession.get(entry.sessionId) ?? emptyTimeline, entry)
  )
  indexTimelineEntry(entry)
  // 只在追加实时事件时裁剪；向上翻页插入的旧条目不能刚加载就被挤掉。
  if (cachedTimeline.length > MAX_TIMELINE_ENTRIES && cachedTimeline.at(-1) === entry) {
    const removed = cachedTimeline[0]
    removeTimelineEntries(removed.sessionId, (item) => item === removed)
    // 被裁掉的条目仍在后端账本里，可以再次向上翻页取回。
    const state = historyStates.get(removed.sessionId)
    if (state && !state.hasMore) historyStates.set(removed.sessionId, { ...state, hasMore: true })
  }
}

/** 从时间线和各索引中移除某个聊天流里满足条件的条目。 */
function removeTimelineEntries(sessionId: string, shouldRemove: (entry: TimelineEntry) => boolean) {
  const sessionTimeline = timelinesBySession.get(sessionId)
  if (!sessionTimeline) return
  const removed = new Set(sessionTimeline.filter(shouldRemove))
  if (removed.size === 0) return
  cachedTimeline = cachedTimeline.filter((item) => !removed.has(item))
  timelinesBySession.set(
    sessionId,
    sessionTimeline.filter((item) => !removed.has(item))
  )
  for (const entry of removed) {
    entriesById.delete(entry.id)
    const key = getEntryLookupKey(entry)
    if (key) {
      const index = isMessageEntry(entry) ? messageEntryIds : mutableEntryIds
      if (index.get(key) === entry.id) index.delete(key)
    }
  }
}

/** 正常实时事件直接追加；回放乱序时二分插入，不为每条事件重排全部历史。 */
function insertTimelineEntry(timeline: TimelineEntry[], entry: TimelineEntry) {
  const last = timeline.at(-1)
  if (!last || compareTimelineEntries(last, entry) <= 0) return [...timeline, entry]
  let low = 0
  let high = timeline.length
  while (low < high) {
    const mid = (low + high) >>> 1
    if (compareTimelineEntries(timeline[mid], entry) <= 0) low = mid + 1
    else high = mid
  }
  return [...timeline.slice(0, low), entry, ...timeline.slice(low)]
}

function isMessageEntry(entry: TimelineEntry) {
  return entry.type === 'message.ingested' || entry.type === 'message.sent'
}

function getEntryLookupKey(entry: TimelineEntry): string | null {
  const data = entry.data as unknown as Record<string, unknown>
  if (isMessageEntry(entry)) return JSON.stringify([entry.sessionId, data.message_id])
  if (entry.type === 'flow.step' && data.step === 'context.trimmed') {
    return JSON.stringify([entry.sessionId, data.run_id, data.cycle_id, 'context.trimmed'])
  }
  if (
    (entry.type === 'planner.progress' || entry.type === 'planner.finalized') &&
    typeof data.run_id === 'string' &&
    data.run_id.length > 0
  ) {
    return JSON.stringify([entry.sessionId, data.run_id, data.cycle_id])
  }
  return null
}

function indexTimelineEntry(entry: TimelineEntry) {
  entriesById.set(entry.id, entry)
  const key = getEntryLookupKey(entry)
  if (key) (isMessageEntry(entry) ? messageEntryIds : mutableEntryIds).set(key, entry.id)
  if (isMessageEntry(entry)) {
    const latest = cachedLatestMessages.get(entry.sessionId)
    if (!latest || latest.id === entry.id || compareTimelineEntries(latest, entry) < 0) {
      cachedLatestMessages = new Map(cachedLatestMessages).set(entry.sessionId, entry)
    }
  }
}

/** 只替换所属会话的数组，其他会话保留引用，详情订阅不会被无关更新唤醒。 */
function replaceTimelineEntry(existing: TimelineEntry, updated: TimelineEntry) {
  const next = cachedTimeline.slice()
  next[next.indexOf(existing)] = updated
  cachedTimeline = next
  const sessionTimeline = timelinesBySession.get(existing.sessionId)!.slice()
  sessionTimeline[sessionTimeline.indexOf(existing)] = updated
  timelinesBySession.set(existing.sessionId, sessionTimeline)
  indexTimelineEntry(updated)
}

function getTimelineEntrySequence(entry: TimelineEntry) {
  const match = /^evt_(\d+)_/.exec(entry.id)
  return match ? Number(match[1]) : 0
}

function compareTimelineEntries(a: TimelineEntry, b: TimelineEntry) {
  // 事件号代表实际广播/写入顺序，优先于消息自身时间戳。
  // message.sent 的 timestamp 是原始消息时间，可能早于 Planner 和工具事件。
  if (a.eventId !== undefined && b.eventId !== undefined && a.eventId !== b.eventId) {
    return a.eventId - b.eventId
  }
  if (a.eventId !== undefined && b.eventId === undefined) {
    return -1
  }
  if (a.eventId === undefined && b.eventId !== undefined) {
    return 1
  }
  if (a.timestamp !== b.timestamp) {
    return a.timestamp - b.timestamp
  }
  return getTimelineEntrySequence(a) - getTimelineEntrySequence(b)
}

function getMonitorEventId(dataRecord: Record<string, unknown>) {
  const eventId = dataRecord.event_id
  if (typeof eventId === 'number' && Number.isFinite(eventId) && eventId > 0) {
    return Math.floor(eventId)
  }
  return null
}

/** 事件是否应计入聊天流的 eventCount：重复事件和翻页加载的旧事件不计。 */
function shouldCountEvent(sessionId: string, eventId: number | null) {
  if (eventId === null) {
    return true
  }
  if (eventId <= (countedEventIds.get(sessionId) ?? 0)) {
    return false
  }
  countedEventIds.set(sessionId, eventId)
  return true
}

function updateSessionInfo(
  event: { type: MaisakaMonitorEvent['type']; data: unknown },
  sessionId: string,
  timestamp: number,
  countEvent: boolean
) {
  const increment = countEvent ? 1 : 0
  const dataRecord = event.data as unknown as Record<string, unknown>
  const isGroupChat =
    typeof dataRecord.is_group_chat === 'boolean' ? dataRecord.is_group_chat : undefined
  const groupId = typeof dataRecord.group_id === 'string' ? dataRecord.group_id : null
  const userId = typeof dataRecord.user_id === 'string' ? dataRecord.user_id : null
  const platform = typeof dataRecord.platform === 'string' ? dataRecord.platform : undefined
  const sessionName =
    typeof dataRecord.session_name === 'string' ? dataRecord.session_name : undefined

  const next = new Map(cachedSessions)
  const existing = next.get(sessionId)

  if (event.type === 'session.start' || !existing) {
    next.set(sessionId, {
      sessionId,
      sessionName: resolveSessionDisplayName({
        fallbackName: sessionName,
        groupId,
        isGroupChat,
        sessionId,
        userId,
      }),
      isGroupChat,
      groupId,
      userId,
      platform,
      lastActivity: Math.max(existing?.lastActivity ?? 0, timestamp),
      eventCount: (existing?.eventCount ?? 0) + increment,
    })
  } else {
    next.set(sessionId, {
      ...existing,
      sessionName: resolveSessionDisplayName({
        fallbackName: sessionName ?? existing.sessionName,
        groupId: groupId ?? existing.groupId,
        isGroupChat: isGroupChat ?? existing.isGroupChat,
        sessionId,
        userId: userId ?? existing.userId,
      }),
      isGroupChat: isGroupChat ?? existing.isGroupChat,
      groupId: groupId ?? existing.groupId,
      userId: userId ?? existing.userId,
      platform: platform ?? existing.platform,
      lastActivity: Math.max(existing.lastActivity, timestamp),
      eventCount: existing.eventCount + increment,
    })
  }

  cachedSessions = next
}

function updateStageStatus(event: MaisakaMonitorEvent) {
  const applyStatusIfFresh = (next: Map<string, StageStatusInfo>, status: StageStatusInfo) => {
    const existing = next.get(status.sessionId)
    if (existing && status.updatedAt < existing.updatedAt) {
      return
    }
    next.set(status.sessionId, status)
  }

  if (event.type === 'stage.snapshot') {
    const rawEntries = (event.data as unknown as Record<string, unknown>).entries
    if (!Array.isArray(rawEntries)) {
      return
    }
    const next = new Map(cachedStageStatuses)
    for (const rawEntry of rawEntries) {
      if (!rawEntry || typeof rawEntry !== 'object') {
        continue
      }
      const status = toStageStatusInfo(rawEntry as Record<string, unknown>)
      if (status) {
        applyStatusIfFresh(next, status)
      }
    }
    cachedStageStatuses = next
    return
  }

  if (event.type === 'stage.status') {
    const status = toStageStatusInfo(event.data as unknown as Record<string, unknown>)
    if (!status) {
      return
    }
    const next = new Map(cachedStageStatuses)
    applyStatusIfFresh(next, status)
    cachedStageStatuses = next
    return
  }

  if (event.type === 'stage.removed') {
    const dataRecord = event.data as unknown as Record<string, unknown>
    const sessionId = typeof dataRecord.session_id === 'string' ? dataRecord.session_id : ''
    if (!sessionId) {
      return
    }
    const next = new Map(cachedStageStatuses)
    next.delete(sessionId)
    cachedStageStatuses = next
  }
}

function updateTimelineMessageContent(event: MaisakaMonitorEvent, sessionId: string) {
  if (event.type !== 'message.updated') {
    return false
  }

  const dataRecord = event.data as unknown as Record<string, unknown>
  const messageId = typeof dataRecord.message_id === 'string' ? dataRecord.message_id : ''
  const content = typeof dataRecord.content === 'string' ? dataRecord.content : ''
  const replyTo = dataRecord.reply_to
  const media = Array.isArray(dataRecord.media) ? dataRecord.media : []
  if (!messageId) {
    return false
  }

  const messageKey = JSON.stringify([sessionId, messageId])
  const entryId = messageEntryIds.get(messageKey)
  const entry = entryId ? entriesById.get(entryId) : undefined
  if (!entry) {
    // 原消息可能在尚未加载的更早一页里，暂存最新的更新，等它到达时再应用。
    const stashed = orphanMessageUpdates.get(messageKey)
    const stashedEventId = stashed
      ? getMonitorEventId(stashed.data as unknown as Record<string, unknown>)
      : null
    if ((getMonitorEventId(dataRecord) ?? 0) >= (stashedEventId ?? 0)) {
      orphanMessageUpdates.delete(messageKey)
      orphanMessageUpdates.set(messageKey, event)
      if (orphanMessageUpdates.size > MAX_ORPHAN_MESSAGE_UPDATES) {
        orphanMessageUpdates.delete(orphanMessageUpdates.keys().next().value!)
      }
    }
    // 历史裁剪不清除侧栏摘要；迟到的识图结果仍须更新该群保留的最新消息。
    const latest = cachedLatestMessages.get(sessionId)
    if (latest && (latest.data as unknown as Record<string, unknown>).message_id === messageId) {
      cachedLatestMessages = new Map(cachedLatestMessages).set(sessionId, {
        ...latest,
        data: { ...latest.data, content, reply_to: replyTo, media } as TimelineEntry['data'],
      })
    }
    return false
  }

  replaceTimelineEntry(entry, {
    ...entry,
    data: { ...entry.data, content, reply_to: replyTo, media } as TimelineEntry['data'],
  })

  return true
}

function isSubagentEvent(event: MaisakaMonitorEvent) {
  return event.type.startsWith('subagent.')
}

/** 替换某个会话里最近一个同 id 任务；找不到说明开始事件已被裁掉，直接忽略。 */
function updateLatestSubagentTask(
  sessionId: string,
  taskId: string,
  update: (task: SubagentTaskInfo) => SubagentTaskInfo
) {
  const tasks = cachedSubagentTasks.get(sessionId)
  if (!tasks) return
  let index = -1
  for (let cursor = tasks.length - 1; cursor >= 0; cursor -= 1) {
    if (tasks[cursor].taskId === taskId) {
      index = cursor
      break
    }
  }
  if (index < 0) return
  const nextTasks = tasks.slice()
  nextTasks[index] = update(tasks[index])
  cachedSubagentTasks = new Map(cachedSubagentTasks).set(sessionId, nextTasks)
}

/** 子代理事件只累积成任务状态，供右侧栏展示，不进入主时间线。 */
function applySubagentEvent(event: MaisakaMonitorEvent, sessionId: string) {
  if (event.type === 'subagent.started') {
    const data = event.data as SubagentStartedEvent
    const task: SubagentTaskInfo = {
      key: data.task_id + '@' + data.timestamp,
      taskId: data.task_id,
      sessionId,
      callId: data.call_id,
      description: data.description,
      grantedTools: data.granted_tools ?? [],
      background: data.background,
      maxRounds: data.max_rounds,
      status: 'running',
      startedAt: data.timestamp,
      rounds: [],
      toolUsage: {},
      conclusion: '',
      errorMessage: '',
    }
    const tasks = [...(cachedSubagentTasks.get(sessionId) ?? emptySubagentTasks), task]
    cachedSubagentTasks = new Map(cachedSubagentTasks).set(
      sessionId,
      tasks.slice(-MAX_SUBAGENT_TASKS_PER_SESSION)
    )
    return
  }

  if (event.type === 'subagent.round') {
    const data = event.data as SubagentRoundEvent
    updateLatestSubagentTask(sessionId, data.task_id, (task) => ({
      ...task,
      rounds: [
        ...task.rounds,
        {
          roundIndex: data.round_index,
          content: data.content,
          timestamp: data.timestamp,
          toolCalls: (data.tool_calls ?? []).map((call) => ({ call })),
        },
      ],
    }))
    return
  }

  if (event.type === 'subagent.tool_result') {
    const data = event.data as SubagentToolResultEvent
    updateLatestSubagentTask(sessionId, data.task_id, (task) => ({
      ...task,
      toolUsage: { ...task.toolUsage, [data.tool_name]: (task.toolUsage[data.tool_name] ?? 0) + 1 },
      rounds: task.rounds.map((round) =>
        round.roundIndex !== data.round_index
          ? round
          : {
              ...round,
              toolCalls: round.toolCalls.map((toolCall) =>
                toolCall.call.id !== data.call_id
                  ? toolCall
                  : {
                      ...toolCall,
                      result: {
                        success: data.success,
                        summary: data.summary,
                        durationMs: data.duration_ms,
                      },
                    }
              ),
            }
      ),
    }))
    return
  }

  if (event.type === 'subagent.finished') {
    const data = event.data as SubagentFinishedEvent
    updateLatestSubagentTask(sessionId, data.task_id, (task) => ({
      ...task,
      status: data.status,
      finishedAt: data.timestamp,
      durationMs: data.duration_ms,
      toolUsage: data.tool_usage ?? task.toolUsage,
      conclusion: data.conclusion,
      errorMessage: data.error_message,
    }))
  }
}

/** 按事件号顺序应用子代理相关事件；session.start 只用来终止遗留任务。 */
function applySubagentLogEvent(event: MaisakaMonitorEvent, sessionId: string) {
  if (event.type === 'session.start') {
    stopStaleSubagentTasks(sessionId, event.data.timestamp)
    return
  }
  applySubagentEvent(event, sessionId)
}

/**
 * 记录一条子代理相关事件并更新任务状态。
 * 实时事件按序追加；翻页或补发带来的更早事件插入日志后整体重建，保证任务与轮次顺序正确。
 */
function recordSubagentEvent(event: MaisakaMonitorEvent, sessionId: string) {
  const eventId = getMonitorEventId(event.data as unknown as Record<string, unknown>)
  if (eventId === null) {
    applySubagentLogEvent(event, sessionId)
    return
  }
  const idOf = (item: MaisakaMonitorEvent) =>
    getMonitorEventId(item.data as unknown as Record<string, unknown>) ?? 0
  const log = subagentLogs.get(sessionId) ?? []
  if (log.length === 0 || eventId > idOf(log[log.length - 1])) {
    log.push(event)
    if (log.length > MAX_SUBAGENT_LOG_EVENTS) log.shift()
    subagentLogs.set(sessionId, log)
    applySubagentLogEvent(event, sessionId)
    return
  }
  const insertAt = log.findIndex((item) => idOf(item) >= eventId)
  if (idOf(log[insertAt]) === eventId) return
  log.splice(insertAt, 0, event)
  const next = new Map(cachedSubagentTasks)
  next.delete(sessionId)
  cachedSubagentTasks = next
  for (const item of log) applySubagentLogEvent(item, sessionId)
}

/** 会话重新启动说明上一个进程里的子代理已经不在了，把还显示为运行中的任务标记为已终止。 */
function stopStaleSubagentTasks(sessionId: string, timestamp: number) {
  const tasks = cachedSubagentTasks.get(sessionId)
  if (!tasks?.some((task) => task.status === 'running')) return
  cachedSubagentTasks = new Map(cachedSubagentTasks).set(
    sessionId,
    tasks.map((task) =>
      task.status === 'running' ? { ...task, status: 'stopped', finishedAt: timestamp } : task
    )
  )
}

function handleMonitorEvent(event: MaisakaMonitorEvent) {
  if (event.type === 'replay.started') {
    monitorInitialSyncPending = true
    pendingReplayEvents = []
    markHistoryStale()
    return
  }
  if (event.type === 'sessions.snapshot') {
    applySessionsSnapshot(event.data)
    return
  }
  if (event.type === 'subagent.history') {
    applySubagentHistory(event.data)
    return
  }
  if (event.type === 'stage.snapshot') {
    // 订阅后实时广播可能与历史交错；先按账本顺序应用，避免识图更新早于原消息。
    const events = pendingReplayEvents
    pendingReplayEvents = []
    events.sort(
      (a, b) =>
        (getMonitorEventId(a.data as unknown as Record<string, unknown>) ??
          Number.MAX_SAFE_INTEGER) -
        (getMonitorEventId(b.data as unknown as Record<string, unknown>) ?? Number.MAX_SAFE_INTEGER)
    )
    for (const replayEvent of events) applyMonitorEvent(replayEvent)
    updateStageStatus(event)
    monitorInitialSyncPending = false
    if (cachedSelectedSession !== null) loadSessionHistory(cachedSelectedSession, 'latest')
    notifyStoreListeners()
    return
  }
  if (monitorInitialSyncPending) {
    pendingReplayEvents.push(event)
    return
  }
  applyMonitorEvent(event)
}

function applyMonitorEvent(event: MaisakaMonitorEvent) {
  const dataRecord = event.data as unknown as Record<string, unknown>
  const eventId = getMonitorEventId(dataRecord)
  if (eventId !== null && eventId <= clearedThroughEventId) {
    return
  }
  const sessionId = dataRecord.session_id as string
  const timestamp = dataRecord.timestamp as number

  if (!sessionId || typeof timestamp !== 'number') {
    return
  }
  if (eventId !== null && eventId > maxObservedEventId) maxObservedEventId = eventId

  if (event.type === 'stage.status' || event.type === 'stage.removed') {
    updateStageStatus(event)
    updateSessionInfo(event, sessionId, timestamp, shouldCountEvent(sessionId, eventId))

    notifyStoreListeners()
    return
  }

  if (isSubagentEvent(event)) {
    recordSubagentEvent(event, sessionId)
    updateSessionInfo(event, sessionId, timestamp, shouldCountEvent(sessionId, eventId))

    notifyStoreListeners()
    return
  }

  if (event.type === 'message.updated') {
    updateTimelineMessageContent(event, sessionId)
    updateSessionInfo(event, sessionId, timestamp, shouldCountEvent(sessionId, eventId))

    notifyStoreListeners()
    return
  }

  if (
    event.type === 'planner.progress' || event.type === 'planner.finalized' ||
    (event.type === 'flow.step' && dataRecord.step === 'context.trimmed')
  ) {
    const cycleId = dataRecord.cycle_id
    const runId = dataRecord.run_id
    const key = event.type === 'flow.step'
      ? [sessionId, runId, cycleId, 'context.trimmed']
      : [sessionId, runId, cycleId]
    const entryId = mutableEntryIds.get(JSON.stringify(key))
    const existing = entryId ? entriesById.get(entryId) : undefined
    if (existing) {
      // 回放与实时推送可能交错；旧版本不能把正在执行/已完成的轮次回滚。
      const previousEventId = getMonitorEventId(existing.data as unknown as Record<string, unknown>)
      if (eventId !== null && previousEventId !== null && eventId <= previousEventId) {
        // 向上翻页带来同一轮更早的事件：内容保持最新版本，卡片移到这一轮真正开始的位置。
        if (existing.eventId !== undefined && eventId < existing.eventId) {
          removeTimelineEntries(sessionId, (item) => item === existing)
          appendTimelineEntry({ ...existing, id: `evt_${eventId}`, eventId })
          notifyStoreListeners()
        }
        return
      }
      // 保留首次出现的位置，让工具和裁切学习进度在原卡片内更新。
      const updated: TimelineEntry = { ...existing, type: event.type, data: event.data }
      replaceTimelineEntry(existing, updated)
      updateSessionInfo(event, sessionId, timestamp, shouldCountEvent(sessionId, eventId))

      notifyStoreListeners()
      return
    }
  }

  // 同一事件可能经实时推送和历史页各到达一次，只入账一次。
  if (eventId !== null && entriesById.has(`evt_${eventId}`)) {
    return
  }

  if (event.type === 'session.start') {
    recordSubagentEvent(event, sessionId)
  }

  let data: TimelineEntry['data'] = event.data
  if (event.type === 'message.ingested' || event.type === 'message.sent') {
    // 识图等更新先于原消息到达（原消息在更早一页）时，在入账时补上。
    const messageKey = JSON.stringify([sessionId, dataRecord.message_id])
    const update = orphanMessageUpdates.get(messageKey)
    if (update) {
      orphanMessageUpdates.delete(messageKey)
      const updateData = update.data as unknown as Record<string, unknown>
      if ((getMonitorEventId(updateData) ?? 0) > (eventId ?? 0)) {
        data = {
          ...event.data,
          content: typeof updateData.content === 'string' ? updateData.content : '',
          reply_to: updateData.reply_to,
          media: Array.isArray(updateData.media) ? updateData.media : [],
        } as TimelineEntry['data']
      }
    }
  }

  const entry: TimelineEntry = {
    id: eventId ? `evt_${eventId}` : `evt_${++entryCounter}_${Date.now()}`,
    eventId: eventId ?? undefined,
    type: event.type,
    data,
    timestamp,
    sessionId,
  }
  appendTimelineEntry(entry)

  updateSessionInfo(event, sessionId, timestamp, shouldCountEvent(sessionId, eventId))

  if (cachedSelectedSession === null) {
    cachedSelectedSession = sessionId
    loadSessionHistory(sessionId, 'latest')
  }

  notifyStoreListeners()
}

/** 用后端概要建立侧栏：聊天流信息、最新消息摘要和事件数，不产生时间线条目。 */
function applySessionsSnapshot(snapshot: SessionsSnapshotEvent) {
  if (!Array.isArray(snapshot.sessions)) return
  for (const summary of snapshot.sessions) {
    const sessionId = summary.session_id
    if (!sessionId || summary.last_event_id <= clearedThroughEventId) continue
    if (summary.last_event_id > maxObservedEventId) maxObservedEventId = summary.last_event_id

    // session.start 会重建聊天流信息，先应用它，再用最新消息补全群号、用户等字段。
    if (summary.session_start) {
      updateSessionInfo(
        { type: 'session.start', data: summary.session_start },
        sessionId,
        summary.last_activity,
        false
      )
    }
    const latestMessage = summary.latest_message
    if (latestMessage || !summary.session_start) {
      updateSessionInfo(
        latestMessage
          ? { type: latestMessage.event, data: latestMessage.data }
          : { type: 'flow.step', data: { session_id: sessionId } },
        sessionId,
        summary.last_activity,
        false
      )
    }
    const info = cachedSessions.get(sessionId)!
    cachedSessions = new Map(cachedSessions).set(sessionId, {
      ...info,
      eventCount: summary.event_count,
      lastActivity: Math.max(info.lastActivity, summary.last_activity),
    })
    countedEventIds.set(
      sessionId,
      Math.max(countedEventIds.get(sessionId) ?? 0, summary.last_event_id)
    )

    const messageEventId = latestMessage
      ? getMonitorEventId(latestMessage.data as unknown as Record<string, unknown>)
      : null
    if (latestMessage && messageEventId !== null) {
      const entry: TimelineEntry = {
        id: `evt_${messageEventId}`,
        eventId: messageEventId,
        type: latestMessage.event,
        data: latestMessage.data,
        timestamp: latestMessage.data.timestamp,
        sessionId,
      }
      // 已有同一条或更新的摘要时保留，它可能带着后续的识图结果。
      const latest = cachedLatestMessages.get(sessionId)
      if (!latest || compareTimelineEntries(latest, entry) < 0) {
        cachedLatestMessages = new Map(cachedLatestMessages).set(sessionId, entry)
      }
    }
  }

  if (cachedSelectedSession === null) {
    // 默认打开最近活跃的聊天流；历史在同步结束（stage.snapshot）时加载。
    let latest: SessionInfo | undefined
    for (const info of cachedSessions.values()) {
      if (!latest || info.lastActivity > latest.lastActivity) latest = info
    }
    if (latest) cachedSelectedSession = latest.sessionId
  }
}

/** 首页历史之前的子代理事件只用于重建子代理面板，不进入时间线。 */
function applySubagentHistory(history: SubagentHistoryEvent) {
  if (!history.session_id || !Array.isArray(history.events)) return
  for (const item of history.events) {
    const eventId = getMonitorEventId(item.data)
    if (eventId !== null && eventId <= clearedThroughEventId) continue
    recordSubagentEvent(
      { type: item.event, data: item.data } as unknown as MaisakaMonitorEvent,
      history.session_id
    )
  }
  notifyStoreListeners()
}

function getHistoryState(sessionId: string) {
  return historyStates.get(sessionId) ?? defaultHistoryState
}

/** 聊天流里带事件号的最新条目；没有事件号的条目排在末尾，需要跳过。 */
function getNewestEventId(timeline: TimelineEntry[]) {
  for (let index = timeline.length - 1; index >= 0; index -= 1) {
    const eventId = timeline[index].eventId
    if (eventId !== undefined) return eventId
  }
  return undefined
}

/**
 * 重新订阅（重连、页面重新挂载）期间可能漏掉事件：所有聊天流都要重新同步最新一页。
 * 已有条目先保留，记录其最大事件号，等最新一页到达后再判断中间是否断档。
 */
function markHistoryStale() {
  historyGeneration += 1
  historyStates.clear()
  for (const sessionId of Array.from(timelinesBySession.keys())) {
    // 上一次断档还没来得及同步又再次断开：更早的那段已无法确认连续，直接丢弃。
    const previousBoundary = staleBoundaries.get(sessionId)
    if (previousBoundary !== undefined) {
      removeTimelineEntries(
        sessionId,
        (entry) => entry.eventId !== undefined && entry.eventId <= previousBoundary
      )
    }
    const newestEventId = getNewestEventId(timelinesBySession.get(sessionId) ?? emptyTimeline)
    if (newestEventId !== undefined) staleBoundaries.set(sessionId, newestEventId)
    else staleBoundaries.delete(sessionId)
  }
}

/** 最新一页到达后收尾：若它够不到断线前的条目，说明中间有缺口，丢弃缺口之前的旧条目。 */
function resolveStaleBoundary(sessionId: string, page: MonitorHistoryPage) {
  const boundary = staleBoundaries.get(sessionId)
  if (boundary === undefined) return
  staleBoundaries.delete(sessionId)
  if (page.count > 0 && page.oldestEventId > boundary) {
    removeTimelineEntries(
      sessionId,
      (entry) => entry.eventId !== undefined && entry.eventId < page.oldestEventId
    )
  }
}

/**
 * 按聊天流分页加载时间线：latest 同步最新一页，older 拉取当前最早条目之前的一页。
 * 历史事件经事件通道到达并按事件号插入，这里只维护分页状态。
 */
function loadSessionHistory(sessionId: string, mode: 'latest' | 'older') {
  const state = getHistoryState(sessionId)
  if (state.loading || monitorInitialSyncPending) return
  if (mode === 'latest' ? state.loaded : !state.loaded || !state.hasMore) return

  const beforeEventId =
    mode === 'older' ? timelinesBySession.get(sessionId)?.[0]?.eventId : undefined
  if (mode === 'older' && beforeEventId === undefined) {
    historyStates.set(sessionId, { ...state, hasMore: false })
    notifyStoreListeners()
    return
  }

  const generation = historyGeneration
  historyStates.set(sessionId, { ...state, loading: true })
  notifyStoreListeners()
  maisakaMonitorClient
    .loadHistory(sessionId, beforeEventId)
    .then((page) => {
      if (generation !== historyGeneration) return
      if (mode === 'latest') resolveStaleBoundary(sessionId, page)
      historyStates.set(sessionId, {
        loaded: true,
        loading: false,
        hasMore: page.hasMore && page.oldestEventId > clearedThroughEventId + 1,
      })
      notifyStoreListeners()
    })
    .catch((error) => {
      if (generation !== historyGeneration) return
      console.error('MaiSaka 监控历史加载失败:', error)
      // 失败后不自动重试，避免触顶加载反复请求；重新订阅时会重置。
      historyStates.set(sessionId, { loaded: mode === 'older', loading: false, hasMore: false })
      notifyStoreListeners()
    })
}

function loadOlderHistory() {
  if (cachedSelectedSession !== null) loadSessionHistory(cachedSelectedSession, 'older')
}

// connected 跟随底层连接实时变化；订阅失败（如后端尚未就绪）后，连接恢复时自动补订阅
function handleMonitorConnectionChange(connected: boolean) {
  if (connected && !monitorSubscriptionStarted && shouldKeepMonitorActive()) {
    ensureMonitorSubscription()
  }

  const nextConnected = connected && monitorSubscriptionStarted
  if (cachedConnected !== nextConnected) {
    cachedConnected = nextConnected
    notifyStoreListeners()
  }
}

function ensureMonitorSubscription() {
  if (monitorSubscriptionStarted || monitorSubscriptionPromise !== null) {
    return
  }

  monitorInitialSyncPending = true
  monitorSubscriptionPromise = maisakaMonitorClient
    .subscribe(handleMonitorEvent)
    .then((unsub) => {
      monitorUnsubscribe = unsub
      if (!shouldKeepMonitorActive()) {
        monitorInitialSyncPending = false
        pendingReplayEvents = []
        monitorUnsubscribe = null
        void unsub()
        cachedConnected = false
        return
      }
      monitorSubscriptionStarted = true
      cachedConnected = true
      notifyStoreListeners()
    })
    .catch((error) => {
      console.error('MaiSaka 监控订阅失败:', error)
      cachedConnected = false
      monitorInitialSyncPending = false
      notifyStoreListeners()
    })
    .finally(() => {
      monitorSubscriptionPromise = null
    })

  // 须在 monitorSubscriptionPromise 赋值后注册：注册时会立即回调一次当前状态
  monitorConnectionUnsubscribe ??= maisakaMonitorClient.onConnectionChange(
    handleMonitorConnectionChange
  )
}

function stopMonitorSubscriptionIfIdle() {
  if (shouldKeepMonitorActive()) {
    return
  }

  monitorConnectionUnsubscribe?.()
  monitorConnectionUnsubscribe = null

  if (monitorUnsubscribe) {
    const unsub = monitorUnsubscribe
    monitorUnsubscribe = null
    monitorSubscriptionStarted = false
    cachedConnected = false
    notifyStoreListeners()
    void unsub()
  }
}

function clearTimeline() {
  cachedTimeline = []
  timelinesBySession.clear()
  entriesById.clear()
  messageEntryIds.clear()
  mutableEntryIds.clear()
  cachedLatestMessages = new Map()
  cachedSessions = new Map()
  cachedStageStatuses = new Map()
  cachedSubagentTasks = new Map()
  cachedSelectedSession = null
  pendingReplayEvents = []
  countedEventIds.clear()
  orphanMessageUpdates.clear()
  subagentLogs.clear()
  historyStates.clear()
  staleBoundaries.clear()
  historyGeneration += 1
  // 清空之前的事件不再随翻页或概要恢复。
  clearedThroughEventId = maxObservedEventId
  notifyStoreListeners()
}

function setSelectedSession(sessionId: string | null) {
  if (cachedSelectedSession === sessionId) return
  cachedSelectedSession = sessionId
  if (sessionId !== null) loadSessionHistory(sessionId, 'latest')
  notifyStoreListeners()
}

function getSessionSnapshot() {
  const historyState = cachedSelectedSession ? getHistoryState(cachedSelectedSession) : undefined
  return {
    historyLoading: historyState?.loading ?? false,
    // 尚未同步最新一页时还不能向上翻页。
    hasMoreHistory: Boolean(historyState?.loaded && historyState.hasMore),
    loadOlderHistory,
    timeline: cachedSelectedSession
      ? (timelinesBySession.get(cachedSelectedSession) ?? emptyTimeline)
      : cachedTimeline,
    selectedSession: cachedSelectedSession,
    selectedStageStatus: cachedSelectedSession
      ? cachedStageStatuses.get(cachedSelectedSession)
      : undefined,
    subagentTasks: cachedSelectedSession
      ? (cachedSubagentTasks.get(cachedSelectedSession) ?? emptySubagentTasks)
      : emptySubagentTasks,
    clearTimeline,
  }
}

function getOverviewSnapshot() {
  return {
    sessions: cachedSessions,
    stageStatuses: cachedStageStatuses,
    latestMessages: cachedLatestMessages,
    selectedSession: cachedSelectedSession,
    connected: cachedConnected,
    setSelectedSession,
  }
}

let sessionSnapshot = getSessionSnapshot()
let overviewSnapshot = getOverviewSnapshot()
let fullSnapshot = { ...overviewSnapshot, ...sessionSnapshot, allTimeline: cachedTimeline }

/** 摘要可以全局更新；当前会话的时间线/阶段未变时，保持详情快照引用不变。 */
function publishSnapshots() {
  const nextSession = getSessionSnapshot()
  if (
    nextSession.timeline !== sessionSnapshot.timeline ||
    nextSession.selectedSession !== sessionSnapshot.selectedSession ||
    nextSession.selectedStageStatus !== sessionSnapshot.selectedStageStatus ||
    nextSession.historyLoading !== sessionSnapshot.historyLoading ||
    nextSession.hasMoreHistory !== sessionSnapshot.hasMoreHistory ||
    nextSession.subagentTasks !== sessionSnapshot.subagentTasks
  ) {
    sessionSnapshot = nextSession
  }

  const nextOverview = getOverviewSnapshot()
  if (
    nextOverview.sessions !== overviewSnapshot.sessions ||
    nextOverview.stageStatuses !== overviewSnapshot.stageStatuses ||
    nextOverview.latestMessages !== overviewSnapshot.latestMessages ||
    nextOverview.selectedSession !== overviewSnapshot.selectedSession ||
    nextOverview.connected !== overviewSnapshot.connected
  ) {
    overviewSnapshot = nextOverview
  }

  fullSnapshot = { ...overviewSnapshot, ...sessionSnapshot, allTimeline: cachedTimeline }
}

function subscribeMonitorStore(listener: () => void) {
  activeConsumerCount += 1
  storeListeners.add(listener)
  publishSnapshots()
  ensureMonitorSubscription()
  return () => {
    storeListeners.delete(listener)
    activeConsumerCount = Math.max(0, activeConsumerCount - 1)
    stopMonitorSubscriptionIfIdle()
  }
}

const readSessionSnapshot = () => sessionSnapshot
const readOverviewSnapshot = () => overviewSnapshot
const readFullSnapshot = () => fullSnapshot

/** 侧边栏只订阅各会话摘要，不再遍历整个事件历史寻找最新消息。 */
export function useMaisakaMonitorOverview() {
  return useSyncExternalStore(subscribeMonitorStore, readOverviewSnapshot)
}

/** 其他群的事件继续入库和更新摘要，但不会触发当前详情重渲染。 */
export function useMaisakaMonitorSession() {
  return useSyncExternalStore(subscribeMonitorStore, readSessionSnapshot)
}

export function useMaisakaMonitor() {
  return useSyncExternalStore(subscribeMonitorStore, readFullSnapshot)
}
