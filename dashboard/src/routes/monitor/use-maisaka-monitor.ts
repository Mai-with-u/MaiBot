/**
 * MaiSaka 聊天流实时监控 - React Hook
 *
 * 管理 WebSocket 订阅与事件流的状态。
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
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
    stageStartedAt: typeof raw.stage_started_at === 'number' ? raw.stage_started_at : Date.now() / 1000,
    updatedAt: typeof raw.updated_at === 'number' ? raw.updated_at : Date.now() / 1000,
  }
}

function notifyStoreListeners() {
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
    const [timelineRecords, sessionRecords, selectedSessionMeta, entryCounterMeta, lastEventIdMeta] = await Promise.all([
      db.getAllFromIndex('timeline', 'by-timestamp'),
      db.getAll('sessions'),
      db.get('meta', 'selectedSession'),
      db.get('meta', 'entryCounter'),
      db.get('meta', 'lastEventId'),
    ])

    cachedTimeline = timelineRecords
      .slice(-MAX_TIMELINE_ENTRIES)
      .map(toTimelineEntry)
    cachedSeenEventIds = new Set(
      cachedTimeline
        .map((entry) => entry.eventId)
        .filter((eventId): eventId is number => typeof eventId === 'number' && eventId > 0),
    )
    cachedSessions = new Map(sessionRecords.map((session) => [session.sessionId, session]))
    cachedSelectedSession = typeof selectedSessionMeta?.value === 'string' ? selectedSessionMeta.value : null
    entryCounter = typeof entryCounterMeta?.value === 'number' ? entryCounterMeta.value : cachedTimeline.length
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

    if (entries.length === 0 && updatedEntryIds.length === 0 && sessionIds.length === 0 && !shouldPersistMeta) {
      return
    }

    const db = await dbPromise
    const tx = db.transaction(['timeline', 'sessions', 'meta'], 'readwrite')
    const persistedAt = Date.now()
    for (const entry of entries) {
      await tx.objectStore('timeline').put({ ...entry, persistedAt })
    }
    for (const entryId of updatedEntryIds) {
      const entry = cachedTimeline.find((item) => item.id === entryId)
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
  const next = [...cachedTimeline, entry].sort(compareTimelineEntries)
  cachedTimeline = next.length > MAX_TIMELINE_ENTRIES
    ? next.slice(next.length - MAX_TIMELINE_ENTRIES)
    : next
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
  const isGroupChat = typeof dataRecord.is_group_chat === 'boolean'
    ? dataRecord.is_group_chat
    : undefined
  const groupId = typeof dataRecord.group_id === 'string' ? dataRecord.group_id : null
  const userId = typeof dataRecord.user_id === 'string' ? dataRecord.user_id : null
  const platform = typeof dataRecord.platform === 'string' ? dataRecord.platform : undefined
  const sessionName = typeof dataRecord.session_name === 'string'
    ? dataRecord.session_name
    : undefined

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
      lastActivity: timestamp,
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
      lastActivity: timestamp,
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

  let updatedEntryId = ''
  const nextTimeline = cachedTimeline.map((entry) => {
    if (
      entry.sessionId !== sessionId
      || (entry.type !== 'message.ingested' && entry.type !== 'message.sent')
    ) {
      return entry
    }

    const entryData = entry.data as unknown as Record<string, unknown>
    if (entryData.message_id !== messageId) {
      return entry
    }

    updatedEntryId = entry.id
    return {
      ...entry,
      data: {
        ...entryData,
        content,
        reply_to: replyTo,
        media,
      } as TimelineEntry['data'],
    }
  })

  if (!updatedEntryId) {
    return false
  }

  cachedTimeline = nextTimeline
  schedulePersistUpdatedTimelineEntry(updatedEntryId, sessionId)
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
    const updated = updateTimelineMessageContent(event, sessionId)
    updateSessionInfo(event, sessionId, timestamp)
    if (updated) {
      notifyStoreListeners()
    }
    return
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

function ensureMonitorSubscription() {
  if (monitorSubscriptionStarted || monitorSubscriptionPromise !== null) {
    return
  }

  monitorSubscriptionPromise = maisakaMonitorClient
    .subscribe(handleMonitorEvent)
    .then((unsub) => {
      monitorUnsubscribe = unsub
      if (!shouldKeepMonitorActive()) {
        monitorUnsubscribe = null
        void unsub()
        cachedConnected = false
        notifyStoreListeners()
        return
      }
      monitorSubscriptionStarted = true
      cachedConnected = true
      notifyStoreListeners()
    })
    .catch((error) => {
      console.error('MaiSaka 监控订阅失败:', error)
      cachedConnected = false
      notifyStoreListeners()
    })
    .finally(() => {
      monitorSubscriptionPromise = null
    })
}

function stopMonitorSubscriptionIfIdle() {
  if (shouldKeepMonitorActive()) {
    return
  }

  if (monitorUnsubscribe) {
    const unsub = monitorUnsubscribe
    monitorUnsubscribe = null
    monitorSubscriptionStarted = false
    cachedConnected = false
    notifyStoreListeners()
    void unsub()
  }
}

export function useMaisakaMonitor() {
  const [timeline, setTimeline] = useState<TimelineEntry[]>(cachedTimeline)
  const [sessions, setSessions] = useState<Map<string, SessionInfo>>(new Map(cachedSessions))
  const [stageStatuses, setStageStatuses] = useState<Map<string, StageStatusInfo>>(new Map(cachedStageStatuses))
  const [selectedSession, setSelectedSessionState] = useState<string | null>(cachedSelectedSession)
  const [connected, setConnected] = useState(cachedConnected)

  useEffect(() => {
    activeConsumerCount += 1
    ensureMonitorSubscription()
    const syncFromStore = () => {
      setTimeline(cachedTimeline)
      setSessions(new Map(cachedSessions))
      setStageStatuses(new Map(cachedStageStatuses))
      setSelectedSessionState(cachedSelectedSession)
      setConnected(cachedConnected)
    }

    storeListeners.add(syncFromStore)
    syncFromStore()
    return () => {
      storeListeners.delete(syncFromStore)
      activeConsumerCount = Math.max(0, activeConsumerCount - 1)
      stopMonitorSubscriptionIfIdle()
    }
  }, [])

  const clearTimeline = useCallback(() => {
    cachedTimeline = []
    cachedSessions = new Map()
    cachedStageStatuses = new Map()
    cachedSelectedSession = null
    setTimeline([])
    setSessions(new Map())
    setStageStatuses(new Map())
    setSelectedSessionState(null)
    pendingPersistEntries = []
    pendingPersistUpdatedEntryIds = new Set()
    pendingPersistSessionIds = new Set()
    pendingPersistMeta = false
    void clearPersistedMonitorSnapshot()
    notifyStoreListeners()
  }, [])

  const setSelectedSession = useCallback((sessionId: string | null) => {
    cachedSelectedSession = sessionId
    setSelectedSessionState(sessionId)
    schedulePersistMonitorSnapshot()
    notifyStoreListeners()
  }, [])

  /** 当前选中会话的时间线 */
  const timelineBySession = useMemo(() => {
    const groupedTimeline = new Map<string, TimelineEntry[]>()
    for (const entry of timeline) {
      const sessionTimeline = groupedTimeline.get(entry.sessionId)
      if (sessionTimeline) {
        sessionTimeline.push(entry)
      } else {
        groupedTimeline.set(entry.sessionId, [entry])
      }
    }
    return groupedTimeline
  }, [timeline])

  const filteredTimeline = useMemo(
    () => selectedSession ? timelineBySession.get(selectedSession) ?? [] : timeline,
    [selectedSession, timeline, timelineBySession],
  )

  return {
    timeline: filteredTimeline,
    allTimeline: timeline,
    sessions,
    stageStatuses,
    selectedSession,
    setSelectedSession,
    connected,
    clearTimeline,
  }
}
