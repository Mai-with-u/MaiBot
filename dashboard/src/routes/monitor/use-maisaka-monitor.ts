/**
 * MaiSaka 聊天流实时监控 - React Hook
 *
 * 管理 WebSocket 订阅与事件流的状态。
 */
import { useSyncExternalStore } from 'react'
import { openDB, type DBSchema, type IDBPDatabase } from 'idb'

import type { MaisakaMonitorEvent } from '@/lib/maisaka-monitor-client'
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

/** 前端内存中最多恢复/展示的时间线条目数，避免一次渲染过多节点。 */
const MAX_TIMELINE_ENTRIES = 3000
/** IndexedDB 中最多持久化的时间线条目数。 */
const MAX_PERSISTED_TIMELINE_ENTRIES = 10000
const PERSIST_PRUNE_INTERVAL = 200
const LAST_EVENT_ID_STORAGE_KEY = 'maisaka-monitor-last-event-id'
const MONITOR_DB_NAME = 'maisaka-monitor-db'
const MONITOR_DB_VERSION = 2

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
const plannerEntryIds = new Map<string, string>()
let cachedLatestMessages = new Map<string, TimelineEntry>()
let cachedSessions: Map<string, SessionInfo> = new Map()
let cachedStageStatuses: Map<string, StageStatusInfo> = new Map()
let cachedSelectedSession: string | null = null
let cachedLastEventId = loadLastEventIdFromStorage()
let cachedSeenEventIds = new Set<number>()
let cachedConnected = false
let activeConsumerCount = 0
let monitorSubscriptionStarted = false
let monitorSubscriptionPromise: Promise<void> | null = null
let monitorUnsubscribe: (() => Promise<void>) | null = null
let monitorConnectionUnsubscribe: (() => void) | null = null
let monitorInitialSyncPending = false
const storeListeners = new Set<() => void>()
let persistSnapshotTimer: ReturnType<typeof setTimeout> | null = null
let monitorDbPromise: Promise<IDBPDatabase<MaisakaMonitorDb>> | null = null
let persistedEntryCountSincePrune = 0
let pendingPersistEntries: TimelineEntry[] = []
let pendingPersistUpdatedEntryIds = new Set<string>()
let pendingPersistSessionIds = new Set<string>()
let pendingPersistMeta = false

interface PersistedTimelineEntry extends TimelineEntry {
  persistedAt: number
}

interface MonitorMetaRecord {
  key: string
  value: unknown
}

interface MaisakaMonitorDb extends DBSchema {
  timeline: {
    key: string
    value: PersistedTimelineEntry
    indexes: {
      'by-timestamp': number
    }
  }
  sessions: {
    key: string
    value: SessionInfo
  }
  meta: {
    key: string
    value: MonitorMetaRecord
  }
}

maisakaMonitorClient.setInitialReplayCursor(cachedLastEventId)

function loadLastEventIdFromStorage() {
  if (typeof window === 'undefined') {
    return 0
  }
  const rawValue = window.localStorage.getItem(LAST_EVENT_ID_STORAGE_KEY)
  if (!rawValue) {
    return 0
  }
  const parsedValue = Number(rawValue)
  return Number.isFinite(parsedValue) && parsedValue > 0 ? Math.floor(parsedValue) : 0
}

function persistLastEventIdToStorage() {
  if (typeof window === 'undefined') {
    return
  }
  window.localStorage.setItem(LAST_EVENT_ID_STORAGE_KEY, String(cachedLastEventId))
}

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
  // 首次订阅会连续补发积压事件。此时只更新模块缓存，待订阅完成后一次性刷新界面，
  // 避免会话列表反复重排、时间线逐条自动滚动。
  if (monitorInitialSyncPending) {
    return
  }
  publishSnapshots()
  storeListeners.forEach((listener) => listener())
}

function getMonitorDb() {
  if (typeof window === 'undefined' || !window.indexedDB) {
    return null
  }

  monitorDbPromise ??= openDB<MaisakaMonitorDb>(MONITOR_DB_NAME, MONITOR_DB_VERSION, {
    upgrade(db, oldVersion, _newVersion, transaction) {
      if (!db.objectStoreNames.contains('timeline')) {
        const timelineStore = db.createObjectStore('timeline', { keyPath: 'id' })
        timelineStore.createIndex('by-timestamp', 'timestamp')
      }
      if (!db.objectStoreNames.contains('sessions')) {
        db.createObjectStore('sessions', { keyPath: 'sessionId' })
      }
      if (!db.objectStoreNames.contains('meta')) {
        db.createObjectStore('meta', { keyPath: 'key' })
      }

      if (oldVersion > 0 && oldVersion < 2) {
        transaction.objectStore('timeline').clear()
        transaction.objectStore('sessions').clear()
        transaction.objectStore('meta').clear()
      }
    },
  })

  return monitorDbPromise
}

function toTimelineEntry(entry: PersistedTimelineEntry): TimelineEntry {
  return {
    id: entry.id,
    eventId: entry.eventId,
    type: entry.type,
    data: entry.data,
    timestamp: entry.timestamp,
    sessionId: entry.sessionId,
  }
}

async function loadMonitorSnapshot() {
  if (typeof window === 'undefined') {
    return
  }

  try {
    const dbPromise = getMonitorDb()
    if (!dbPromise) {
      return
    }

    const db = await dbPromise
    const [
      timelineRecords,
      sessionRecords,
      selectedSessionMeta,
      entryCounterMeta,
      lastEventIdMeta,
    ] = await Promise.all([
      db.getAllFromIndex('timeline', 'by-timestamp'),
      db.getAll('sessions'),
      db.get('meta', 'selectedSession'),
      db.get('meta', 'entryCounter'),
      db.get('meta', 'lastEventId'),
    ])

    cachedTimeline = timelineRecords
      .map(toTimelineEntry)
      .sort(compareTimelineEntries)
      .slice(-MAX_TIMELINE_ENTRIES)
    rebuildTimelineIndexes()
    cachedSeenEventIds = new Set(
      cachedTimeline
        .map((entry) => entry.eventId)
        .filter((eventId): eventId is number => typeof eventId === 'number' && eventId > 0)
    )
    cachedSessions = new Map(sessionRecords.map((session) => [session.sessionId, session]))
    cachedSelectedSession =
      typeof selectedSessionMeta?.value === 'string' ? selectedSessionMeta.value : null
    entryCounter =
      typeof entryCounterMeta?.value === 'number' ? entryCounterMeta.value : cachedTimeline.length
    if (typeof lastEventIdMeta?.value === 'number') {
      cachedLastEventId = Math.max(cachedLastEventId, lastEventIdMeta.value)
      persistLastEventIdToStorage()
      maisakaMonitorClient.setInitialReplayCursor(cachedLastEventId)
    }
    notifyStoreListeners()
  } catch (error) {
    console.warn('读取 MaiSaka 观察 IndexedDB 缓存失败，已忽略:', error)
  }
}

async function prunePersistedTimeline(db: IDBPDatabase<MaisakaMonitorDb>) {
  const keys = await db.getAllKeysFromIndex('timeline', 'by-timestamp')
  const overflowCount = keys.length - MAX_PERSISTED_TIMELINE_ENTRIES
  if (overflowCount <= 0) {
    return
  }

  const tx = db.transaction('timeline', 'readwrite')
  for (const key of keys.slice(0, overflowCount)) {
    await tx.store.delete(key)
  }
  await tx.done
}

async function flushMonitorSnapshot() {
  try {
    const dbPromise = getMonitorDb()
    if (!dbPromise) {
      return
    }

    const entries = pendingPersistEntries
    const updatedEntryIds = Array.from(pendingPersistUpdatedEntryIds)
    const sessionIds = Array.from(pendingPersistSessionIds)
    const shouldPersistMeta = pendingPersistMeta
    pendingPersistEntries = []
    pendingPersistUpdatedEntryIds = new Set()
    pendingPersistSessionIds = new Set()
    pendingPersistMeta = false

    if (
      entries.length === 0 &&
      updatedEntryIds.length === 0 &&
      sessionIds.length === 0 &&
      !shouldPersistMeta
    ) {
      return
    }

    const db = await dbPromise
    const tx = db.transaction(['timeline', 'sessions', 'meta'], 'readwrite')
    const persistedAt = Date.now()
    for (const entry of entries) {
      await tx.objectStore('timeline').put({ ...entry, persistedAt })
    }
    for (const entryId of updatedEntryIds) {
      const entry = entriesById.get(entryId)
      if (entry) {
        await tx.objectStore('timeline').put({ ...entry, persistedAt })
      }
    }
    for (const sessionId of sessionIds) {
      const session = cachedSessions.get(sessionId)
      if (session) {
        await tx.objectStore('sessions').put(session)
      }
    }
    await tx.objectStore('meta').put({ key: 'selectedSession', value: cachedSelectedSession })
    await tx.objectStore('meta').put({ key: 'entryCounter', value: entryCounter })
    await tx.objectStore('meta').put({ key: 'lastEventId', value: cachedLastEventId })
    await tx.done

    persistedEntryCountSincePrune += entries.length
    if (persistedEntryCountSincePrune >= PERSIST_PRUNE_INTERVAL) {
      persistedEntryCountSincePrune = 0
      await prunePersistedTimeline(db)
    }
  } catch (error) {
    console.warn('保存 MaiSaka 观察 IndexedDB 缓存失败，已忽略:', error)
  }
}

async function clearPersistedMonitorSnapshot() {
  try {
    const dbPromise = getMonitorDb()
    if (!dbPromise) {
      return
    }
    const db = await dbPromise
    const tx = db.transaction(['timeline', 'sessions', 'meta'], 'readwrite')
    await Promise.all([
      tx.objectStore('timeline').clear(),
      tx.objectStore('sessions').clear(),
      tx.objectStore('meta').clear(),
    ])
    await tx.done
  } catch (error) {
    console.warn('清空 MaiSaka 观察 IndexedDB 缓存失败，已忽略:', error)
  }
}

function schedulePersistMonitorSnapshot(entry?: TimelineEntry, sessionId?: string) {
  if (typeof window === 'undefined') {
    return
  }
  if (entry) {
    pendingPersistEntries.push(entry)
  }
  if (sessionId) {
    pendingPersistSessionIds.add(sessionId)
  }
  pendingPersistMeta = true
  if (persistSnapshotTimer !== null) {
    window.clearTimeout(persistSnapshotTimer)
  }
  persistSnapshotTimer = window.setTimeout(() => {
    persistSnapshotTimer = null
    void flushMonitorSnapshot()
  }, 300)
}

void loadMonitorSnapshot()

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
  if (cachedTimeline.length > MAX_TIMELINE_ENTRIES) {
    const removed = cachedTimeline[0]
    cachedTimeline = cachedTimeline.slice(1)
    const sessionTimeline = timelinesBySession.get(removed.sessionId)!
    timelinesBySession.set(removed.sessionId, sessionTimeline.filter((item) => item !== removed))
    entriesById.delete(removed.id)
    const key = getEntryLookupKey(removed)
    if (key) {
      const index = isMessageEntry(removed) ? messageEntryIds : plannerEntryIds
      if (index.get(key) === removed.id) index.delete(key)
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
  if (key) (isMessageEntry(entry) ? messageEntryIds : plannerEntryIds).set(key, entry.id)
  if (isMessageEntry(entry)) {
    const latest = cachedLatestMessages.get(entry.sessionId)
    if (!latest || latest.id === entry.id || compareTimelineEntries(latest, entry) < 0) {
      cachedLatestMessages = new Map(cachedLatestMessages).set(entry.sessionId, entry)
    }
  }
}

function rebuildTimelineIndexes() {
  timelinesBySession.clear()
  entriesById.clear()
  messageEntryIds.clear()
  plannerEntryIds.clear()
  cachedLatestMessages = new Map()
  for (const entry of cachedTimeline) {
    const sessionTimeline = timelinesBySession.get(entry.sessionId) ?? []
    sessionTimeline.push(entry)
    timelinesBySession.set(entry.sessionId, sessionTimeline)
    indexTimelineEntry(entry)
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

function schedulePersistUpdatedTimelineEntry(entryId: string, sessionId?: string) {
  if (typeof window === 'undefined') {
    return
  }
  pendingPersistUpdatedEntryIds.add(entryId)
  if (sessionId) {
    pendingPersistSessionIds.add(sessionId)
  }
  pendingPersistMeta = true
  if (persistSnapshotTimer !== null) {
    window.clearTimeout(persistSnapshotTimer)
  }
  persistSnapshotTimer = window.setTimeout(() => {
    persistSnapshotTimer = null
    void flushMonitorSnapshot()
  }, 300)
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

function markMonitorEventSeen(eventId: number | null) {
  if (eventId === null) {
    return true
  }
  if (cachedSeenEventIds.has(eventId)) {
    return false
  }
  cachedSeenEventIds.add(eventId)
  cachedLastEventId = Math.max(cachedLastEventId, eventId)
  persistLastEventIdToStorage()
  maisakaMonitorClient.updateReplayCursor(cachedLastEventId)
  return true
}

function updateSessionInfo(event: MaisakaMonitorEvent, sessionId: string, timestamp: number) {
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
      eventCount: (existing?.eventCount ?? 0) + 1,
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
      eventCount: existing.eventCount + 1,
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

  const entryId = messageEntryIds.get(JSON.stringify([sessionId, messageId]))
  const entry = entryId ? entriesById.get(entryId) : undefined
  if (!entry) {
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
  schedulePersistUpdatedTimelineEntry(entry.id, sessionId)
  return true
}

function handleMonitorEvent(event: MaisakaMonitorEvent) {
  const dataRecord = event.data as unknown as Record<string, unknown>
  const eventId = getMonitorEventId(dataRecord)
  if (!markMonitorEventSeen(eventId)) {
    return
  }
  const sessionId = dataRecord.session_id as string
  const timestamp = dataRecord.timestamp as number

  if (event.type === 'stage.snapshot') {
    updateStageStatus(event)
    monitorInitialSyncPending = false
    notifyStoreListeners()
    return
  }

  if (!sessionId || typeof timestamp !== 'number') {
    return
  }

  if (event.type === 'stage.status' || event.type === 'stage.removed') {
    updateStageStatus(event)
    updateSessionInfo(event, sessionId, timestamp)
    schedulePersistMonitorSnapshot(undefined, sessionId)
    notifyStoreListeners()
    return
  }

  if (event.type === 'message.updated') {
    updateTimelineMessageContent(event, sessionId)
    updateSessionInfo(event, sessionId, timestamp)
    schedulePersistMonitorSnapshot(undefined, sessionId)
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
      if (eventId !== null && previousEventId !== null && eventId <= previousEventId) return
      // 保留首次出现的位置，让正在执行的工具在原卡片内更新。
      const updated: TimelineEntry = { ...existing, type: event.type, data: event.data }
      replaceTimelineEntry(existing, updated)
      updateSessionInfo(event, sessionId, timestamp)
      schedulePersistUpdatedTimelineEntry(existing.id, sessionId)
      notifyStoreListeners()
      return
    }
  }

  const entry: TimelineEntry = {
    id: eventId ? `evt_${eventId}` : `evt_${++entryCounter}_${Date.now()}`,
    eventId: eventId ?? undefined,
    type: event.type,
    data: event.data,
    timestamp,
    sessionId,
  }
  appendTimelineEntry(entry)

  updateSessionInfo(event, sessionId, timestamp)

  if (cachedSelectedSession === null) {
    cachedSelectedSession = sessionId
  }

  schedulePersistMonitorSnapshot(entry, sessionId)
  notifyStoreListeners()
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
        monitorUnsubscribe = null
        void unsub()
        cachedConnected = false
        return
      }
      monitorSubscriptionStarted = true
      cachedConnected = true
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
  rebuildTimelineIndexes()
  cachedSessions = new Map()
  cachedStageStatuses = new Map()
  cachedSelectedSession = null
  pendingPersistEntries = []
  pendingPersistUpdatedEntryIds = new Set()
  pendingPersistSessionIds = new Set()
  pendingPersistMeta = false
  void clearPersistedMonitorSnapshot()
  notifyStoreListeners()
}

function setSelectedSession(sessionId: string | null) {
  if (cachedSelectedSession === sessionId) return
  cachedSelectedSession = sessionId
  schedulePersistMonitorSnapshot()
  notifyStoreListeners()
}

function getSessionSnapshot() {
  return {
    timeline: cachedSelectedSession
      ? (timelinesBySession.get(cachedSelectedSession) ?? emptyTimeline)
      : cachedTimeline,
    selectedSession: cachedSelectedSession,
    selectedStageStatus: cachedSelectedSession
      ? cachedStageStatuses.get(cachedSelectedSession)
      : undefined,
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
    nextSession.selectedStageStatus !== sessionSnapshot.selectedStageStatus
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
