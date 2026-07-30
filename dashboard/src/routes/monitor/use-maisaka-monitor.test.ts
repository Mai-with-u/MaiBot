/**
 * useMaisakaMonitor Hook 单元测试
 *
 * 该模块持有大量模块级缓存状态（时间线、会话、阶段状态、订阅计数、补发游标等），
 * 因此所有用例都通过 vi.resetModules + 动态 import 获取全新模块实例，避免跨用例污染。
 * IndexedDB 持久化链路通过 mock idb.openDB 并桩掉 window.indexedDB 来驱动，
 * WebSocket 订阅链路则整体 mock @/lib/maisaka-monitor-client。
 */
import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { Mock } from 'vitest'

import type { MaisakaEventListener, MaisakaMonitorEvent } from '@/lib/maisaka-monitor-client'
import type { SessionInfo, TimelineEntry } from './use-maisaka-monitor'

type MonitorHookModule = typeof import('./use-maisaka-monitor')

/** IndexedDB 中持久化的时间线记录（比内存条目多一个 persistedAt 字段） */
type PersistedTimelineRecord = TimelineEntry & { persistedAt: number }

/** meta 表记录 */
interface MetaRecord {
  key: string
  value: unknown
}

const LAST_EVENT_ID_STORAGE_KEY = 'maisaka-monitor-last-event-id'

// 监控客户端单例 mock：捕获 subscribe 传入的事件处理器以便测试中手动派发事件
const clientMocks = vi.hoisted(() => ({
  setInitialReplayCursor: vi.fn(),
  subscribe: vi.fn(),
  updateReplayCursor: vi.fn(),
}))

vi.mock('@/lib/maisaka-monitor-client', () => ({
  maisakaMonitorClient: clientMocks,
}))

const idbMocks = vi.hoisted(() => ({
  openDB: vi.fn(),
}))

vi.mock('idb', () => ({
  openDB: idbMocks.openDB,
}))

/** 每个对象仓库暴露 put/clear/delete 三个可断言的桩方法 */
function createFakeStores() {
  return {
    timeline: {
      put: vi.fn(async () => {}),
      clear: vi.fn(async () => {}),
      delete: vi.fn(async () => {}),
    },
    sessions: {
      put: vi.fn(async () => {}),
      clear: vi.fn(async () => {}),
      delete: vi.fn(async () => {}),
    },
    meta: {
      put: vi.fn(async () => {}),
      clear: vi.fn(async () => {}),
      delete: vi.fn(async () => {}),
    },
  }
}

type FakeStores = ReturnType<typeof createFakeStores>

/** 构造一个满足 hook 使用面的假 IndexedDB 数据库对象 */
function createFakeDb(stores: FakeStores) {
  const fakeTransaction = {
    objectStore(name: keyof FakeStores) {
      return stores[name]
    },
    store: stores.timeline,
    done: Promise.resolve(),
  }
  return {
    get: vi.fn<(store: string, key: string) => Promise<MetaRecord | undefined>>(
      async () => undefined
    ),
    getAll: vi.fn<(store: string) => Promise<SessionInfo[]>>(async () => []),
    getAllFromIndex: vi.fn<(store: string, index: string) => Promise<PersistedTimelineRecord[]>>(
      async () => []
    ),
    getAllKeysFromIndex: vi.fn<(store: string, index: string) => Promise<string[]>>(async () => []),
    transaction: vi.fn(() => fakeTransaction),
  }
}

type FakeDb = ReturnType<typeof createFakeDb>

/** 创建一个可手动控制 resolve 的 Promise */
function createDeferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void
  const promise = new Promise<T>((nextResolve) => {
    resolve = nextResolve
  })
  return { promise, resolve }
}

/** 构造一条消息事件数据（含账本 event_id 与会话归属字段） */
function makeMessageData(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    event_id: 101,
    session_id: 'session-a',
    session_name: '测试群',
    is_group_chat: true,
    group_id: 'group-1',
    platform: 'qq',
    speaker_name: '张三',
    content: '你好',
    message_id: 'msg-1',
    timestamp: 100,
    ...overrides,
  }
}

/** 构造一条阶段状态事件数据 */
function makeStageData(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    event_id: 201,
    session_id: 'session-a',
    session_name: '测试群',
    stage: '规划中',
    detail: '正在调用工具',
    round_text: '第 1 轮',
    agent_state: 'running',
    stage_started_at: 90,
    updated_at: 100,
    timestamp: 100,
    ...overrides,
  }
}

let capturedHandler: MaisakaEventListener | null = null
let unsubscribeMock: Mock<() => Promise<void>>
let fakeStores: FakeStores
let fakeDb: FakeDb

/**
 * 动态导入被测模块，并等待一个宏任务：
 * 模块加载时会触发 loadMonitorSnapshot 的异步链，若不先排空，
 * 快照回填可能覆盖用例中派发的实时事件导致偶发失败。
 */
async function importHookModule(): Promise<MonitorHookModule> {
  const hookModule = await import('./use-maisaka-monitor')
  await new Promise((resolve) => {
    setTimeout(resolve, 0)
  })
  return hookModule
}

/** 挂载 hook，并模拟后端在历史补发末尾发送阶段快照。 */
async function mountMonitor(hookModule: MonitorHookModule, completeInitialSync = true) {
  const view = renderHook(() => hookModule.useMaisakaMonitor())
  await act(async () => {})
  if (completeInitialSync) {
    emitMonitorEvent('stage.snapshot', { entries: [], timestamp: 100 })
  }
  return view
}

/** 在 act 内把事件派发给被捕获的监控事件处理器 */
function emitMonitorEvent(type: MaisakaMonitorEvent['type'], data: Record<string, unknown>) {
  expect(capturedHandler).not.toBeNull()
  act(() => {
    capturedHandler?.({ type, data } as unknown as MaisakaMonitorEvent)
  })
}

beforeEach(() => {
  vi.resetModules()
  window.localStorage.clear()
  // jsdom 没有 IndexedDB，桩一个真值让 getMonitorDb 走 mock 的 openDB
  Object.defineProperty(window, 'indexedDB', { configurable: true, value: {} as IDBFactory })

  capturedHandler = null
  unsubscribeMock = vi.fn(async () => {})
  clientMocks.subscribe.mockImplementation(async (listener: MaisakaEventListener) => {
    capturedHandler = listener
    return unsubscribeMock
  })

  fakeStores = createFakeStores()
  fakeDb = createFakeDb(fakeStores)
  idbMocks.openDB.mockImplementation(() => Promise.resolve(fakeDb))
})

describe('模块初始化与快照恢复', () => {
  it('用 localStorage 保存的 last-event-id 初始化补发游标（向下取整）', async () => {
    window.localStorage.setItem(LAST_EVENT_ID_STORAGE_KEY, '57.9')

    await importHookModule()

    expect(clientMocks.setInitialReplayCursor).toHaveBeenCalledTimes(1)
    expect(clientMocks.setInitialReplayCursor).toHaveBeenCalledWith(57)
  })

  it('localStorage 中的非法游标值按 0 处理', async () => {
    window.localStorage.setItem(LAST_EVENT_ID_STORAGE_KEY, '不是数字')

    await importHookModule()

    expect(clientMocks.setInitialReplayCursor).toHaveBeenCalledWith(0)
  })

  it('IndexedDB 快照中的 lastEventId 更大时推进游标并回写 localStorage', async () => {
    window.localStorage.setItem(LAST_EVENT_ID_STORAGE_KEY, '10')
    fakeDb.get.mockImplementation(async (_store, key) => {
      if (key === 'lastEventId') {
        return { key, value: 88 }
      }
      return undefined
    })

    await importHookModule()

    // 第一次调用来自 localStorage，第二次来自快照恢复后的更大游标
    expect(clientMocks.setInitialReplayCursor).toHaveBeenNthCalledWith(1, 10)
    expect(clientMocks.setInitialReplayCursor).toHaveBeenLastCalledWith(88)
    expect(window.localStorage.getItem(LAST_EVENT_ID_STORAGE_KEY)).toBe('88')
  })

  it('从 IndexedDB 快照恢复时间线、会话与选中会话，重复 event_id 不再入账', async () => {
    const restoredEntries: PersistedTimelineRecord[] = [
      {
        id: 'evt_1',
        eventId: 1,
        type: 'message.ingested',
        data: {
          session_id: 'session-a',
          speaker_name: '张三',
          content: '历史消息一',
          message_id: 'msg-h1',
          timestamp: 100,
        },
        timestamp: 100,
        sessionId: 'session-a',
        persistedAt: 1000,
      },
      {
        id: 'evt_2',
        eventId: 2,
        type: 'message.sent',
        data: {
          session_id: 'session-a',
          speaker_name: '麦麦',
          content: '历史回复',
          message_id: 'msg-h2',
          timestamp: 200,
        },
        timestamp: 200,
        sessionId: 'session-a',
        persistedAt: 1000,
      },
    ]
    fakeDb.getAllFromIndex.mockResolvedValue(restoredEntries)
    fakeDb.getAll.mockResolvedValue([
      {
        sessionId: 'session-a',
        sessionName: '测试群(group-1)',
        isGroupChat: true,
        groupId: 'group-1',
        userId: null,
        platform: 'qq',
        lastActivity: 200,
        eventCount: 2,
      },
    ])
    fakeDb.get.mockImplementation(async (_store, key) => {
      if (key === 'selectedSession') {
        return { key, value: 'session-a' }
      }
      if (key === 'entryCounter') {
        return { key, value: 7 }
      }
      return undefined
    })

    const hookModule = await importHookModule()
    const view = await mountMonitor(hookModule)

    expect(view.result.current.allTimeline).toHaveLength(2)
    // 恢复时应剥掉 persistedAt 字段，还原为纯前端视图模型
    expect(view.result.current.allTimeline[0]).toEqual({
      id: 'evt_1',
      eventId: 1,
      type: 'message.ingested',
      data: restoredEntries[0].data,
      timestamp: 100,
      sessionId: 'session-a',
    })
    expect(view.result.current.allTimeline[0]).not.toHaveProperty('persistedAt')
    expect(view.result.current.selectedSession).toBe('session-a')
    expect(view.result.current.sessions.get('session-a')?.sessionName).toBe('测试群(group-1)')

    // 恢复时已登记过的 event_id 再次到达不会重复入账
    emitMonitorEvent('message.ingested', makeMessageData({ event_id: 1, message_id: 'msg-h1' }))
    expect(view.result.current.allTimeline).toHaveLength(2)
  })
})

describe('订阅生命周期', () => {
  it('挂载时发起订阅，订阅成功后 connected 为 true', async () => {
    const { useMaisakaMonitor } = await importHookModule()

    const view = renderHook(() => useMaisakaMonitor())
    expect(clientMocks.subscribe).toHaveBeenCalledTimes(1)
    expect(clientMocks.subscribe).toHaveBeenCalledWith(expect.any(Function))
    // 订阅 Promise 落定前尚未连接
    expect(view.result.current.connected).toBe(false)

    await act(async () => {})
    emitMonitorEvent('stage.snapshot', { entries: [], timestamp: 100 })
    expect(view.result.current.connected).toBe(true)
  })

  it('首次订阅积压事件完成前不逐条刷新，完成后一次性展示最新状态', async () => {
    const deferred = createDeferred<() => Promise<void>>()
    clientMocks.subscribe.mockImplementation((listener: MaisakaEventListener) => {
      capturedHandler = listener
      return deferred.promise
    })
    const hookModule = await importHookModule()
    const view = renderHook(() => hookModule.useMaisakaMonitor())

    emitMonitorEvent(
      'message.ingested',
      makeMessageData({ event_id: 101, session_id: 'session-a', timestamp: 100 })
    )
    emitMonitorEvent(
      'message.ingested',
      makeMessageData({ event_id: 102, session_id: 'session-b', timestamp: 200 })
    )
    emitMonitorEvent(
      'message.ingested',
      makeMessageData({ event_id: 103, session_id: 'session-a', timestamp: 300 })
    )

    // 补发期间缓存照常入账，但界面保持稳定，不逐条触发侧栏重排和自动滚动。
    expect(view.result.current.allTimeline).toHaveLength(0)
    expect(view.result.current.sessions.size).toBe(0)

    deferred.resolve(unsubscribeMock)
    await act(async () => {})
    expect(view.result.current.allTimeline).toHaveLength(0)

    emitMonitorEvent('stage.snapshot', { entries: [], timestamp: 300 })

    expect(view.result.current.allTimeline).toHaveLength(3)
    expect(view.result.current.sessions.size).toBe(2)
    expect(view.result.current.sessions.get('session-a')?.lastActivity).toBe(300)
    expect(view.result.current.connected).toBe(true)
  })

  it('订阅失败时记录错误且 connected 保持 false', async () => {
    const consoleErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    clientMocks.subscribe.mockRejectedValue(new Error('订阅通道不可用'))
    const hookModule = await importHookModule()

    const view = await mountMonitor(hookModule, false)

    expect(view.result.current.connected).toBe(false)
    expect(consoleErrorSpy).toHaveBeenCalledWith('MaiSaka 监控订阅失败:', expect.any(Error))
  })

  it('多个消费者共享同一订阅，最后一个卸载时才退订', async () => {
    const hookModule = await importHookModule()

    const first = await mountMonitor(hookModule)
    const second = await mountMonitor(hookModule)
    // 两个消费者只建立一次订阅
    expect(clientMocks.subscribe).toHaveBeenCalledTimes(1)

    first.unmount()
    expect(unsubscribeMock).not.toHaveBeenCalled()

    // 剩余消费者仍能收到事件
    emitMonitorEvent('message.ingested', makeMessageData({ event_id: 11 }))
    expect(second.result.current.allTimeline).toHaveLength(1)

    second.unmount()
    expect(unsubscribeMock).toHaveBeenCalledTimes(1)
  })

  it('订阅完成前所有消费者已卸载则立即退订', async () => {
    const deferred = createDeferred<() => Promise<void>>()
    clientMocks.subscribe.mockImplementation(() => deferred.promise)
    const hookModule = await importHookModule()

    const view = renderHook(() => hookModule.useMaisakaMonitor())
    view.unmount()

    // 卸载后订阅才成功：应立即用返回的退订函数断开
    deferred.resolve(unsubscribeMock)
    await act(async () => {})

    expect(unsubscribeMock).toHaveBeenCalledTimes(1)
    expect(view.result.current.connected).toBe(false)
  })
})

describe('事件入账', () => {
  it('消息事件追加时间线、更新会话并自动选中首个会话', async () => {
    const hookModule = await importHookModule()
    const view = await mountMonitor(hookModule)

    const data = makeMessageData({ event_id: 101 })
    emitMonitorEvent('message.ingested', data)

    expect(view.result.current.allTimeline).toEqual([
      {
        id: 'evt_101',
        eventId: 101,
        type: 'message.ingested',
        data,
        timestamp: 100,
        sessionId: 'session-a',
      },
    ])
    // 首个事件自动选中所属会话
    expect(view.result.current.selectedSession).toBe('session-a')
    // 群聊展示名 = 群名称(群号)
    expect(view.result.current.sessions.get('session-a')).toMatchObject({
      sessionId: 'session-a',
      sessionName: '测试群(group-1)',
      isGroupChat: true,
      groupId: 'group-1',
      platform: 'qq',
      lastActivity: 100,
      eventCount: 1,
    })
    // 账本游标同步推进并写入 localStorage
    expect(clientMocks.updateReplayCursor).toHaveBeenCalledWith(101)
    expect(window.localStorage.getItem(LAST_EVENT_ID_STORAGE_KEY)).toBe('101')
  })

  it('相同 event_id 的事件只入账一次', async () => {
    const hookModule = await importHookModule()
    const view = await mountMonitor(hookModule)

    const data = makeMessageData({ event_id: 701 })
    emitMonitorEvent('message.ingested', data)
    emitMonitorEvent('message.ingested', data)

    expect(view.result.current.allTimeline).toHaveLength(1)
    expect(clientMocks.updateReplayCursor).toHaveBeenCalledTimes(1)
    expect(clientMocks.updateReplayCursor).toHaveBeenCalledWith(701)
  })

  it('缺少 event_id 的事件使用自增序号生成条目 ID 且不推进游标', async () => {
    const hookModule = await importHookModule()
    const view = await mountMonitor(hookModule)

    emitMonitorEvent(
      'message.ingested',
      makeMessageData({ event_id: undefined, message_id: 'msg-x1' })
    )
    emitMonitorEvent(
      'message.ingested',
      makeMessageData({ event_id: undefined, message_id: 'msg-x2' })
    )

    expect(view.result.current.allTimeline).toHaveLength(2)
    expect(view.result.current.allTimeline[0].id).toMatch(/^evt_1_\d+$/)
    expect(view.result.current.allTimeline[1].id).toMatch(/^evt_2_\d+$/)
    expect(view.result.current.allTimeline[0].eventId).toBeUndefined()
    expect(clientMocks.updateReplayCursor).not.toHaveBeenCalled()
    expect(window.localStorage.getItem(LAST_EVENT_ID_STORAGE_KEY)).toBeNull()
  })

  it('时间线按时间戳升序排列（乱序到达自动重排）', async () => {
    const hookModule = await importHookModule()
    const view = await mountMonitor(hookModule)

    emitMonitorEvent('message.ingested', makeMessageData({ event_id: 202, timestamp: 200 }))
    emitMonitorEvent('message.ingested', makeMessageData({ event_id: 201, timestamp: 100 }))

    expect(view.result.current.allTimeline.map((entry) => entry.id)).toEqual(['evt_201', 'evt_202'])
    expect(view.result.current.allTimeline.map((entry) => entry.timestamp)).toEqual([100, 200])
  })

  it('缺少 session_id 或 timestamp 非数字的事件被丢弃', async () => {
    const hookModule = await importHookModule()
    const view = await mountMonitor(hookModule)

    emitMonitorEvent('message.ingested', makeMessageData({ event_id: 301, session_id: undefined }))
    emitMonitorEvent('message.ingested', makeMessageData({ event_id: 302, timestamp: '100' }))

    expect(view.result.current.allTimeline).toHaveLength(0)
    expect(view.result.current.sessions.size).toBe(0)
  })

  it('后续事件合并进已有会话：eventCount 递增、lastActivity 更新、名称保留', async () => {
    const hookModule = await importHookModule()
    const view = await mountMonitor(hookModule)

    emitMonitorEvent('message.ingested', makeMessageData({ event_id: 601, timestamp: 100 }))
    // 第二条事件不携带会话名称等归属字段，应继承已有信息
    emitMonitorEvent('message.sent', {
      event_id: 602,
      session_id: 'session-a',
      speaker_name: '麦麦',
      content: '回复内容',
      message_id: 'msg-2',
      timestamp: 200,
    })

    expect(view.result.current.sessions.get('session-a')).toMatchObject({
      sessionName: '测试群(group-1)',
      isGroupChat: true,
      groupId: 'group-1',
      platform: 'qq',
      lastActivity: 200,
      eventCount: 2,
    })
  })

  it('私聊会话无名称时以 user_id 作为展示名，无任何标识时取 session_id 前八位', async () => {
    const hookModule = await importHookModule()
    const view = await mountMonitor(hookModule)

    emitMonitorEvent('session.start', {
      event_id: 611,
      session_id: 'session-p',
      is_group_chat: false,
      user_id: 'user-9',
      timestamp: 100,
    })
    emitMonitorEvent('session.start', {
      event_id: 612,
      session_id: 'anon12345678',
      is_group_chat: false,
      timestamp: 110,
    })

    expect(view.result.current.sessions.get('session-p')?.sessionName).toBe('user-9')
    expect(view.result.current.sessions.get('anon12345678')?.sessionName).toBe('anon1234')
  })
})

describe('阶段状态', () => {
  it('stage.status 更新阶段状态且不追加时间线，较旧的更新不覆盖', async () => {
    const hookModule = await importHookModule()
    const view = await mountMonitor(hookModule)

    emitMonitorEvent('stage.status', makeStageData({ event_id: 201 }))

    expect(view.result.current.allTimeline).toHaveLength(0)
    expect(view.result.current.stageStatuses.get('session-a')).toEqual({
      sessionId: 'session-a',
      sessionName: '测试群',
      stage: '规划中',
      detail: '正在调用工具',
      roundText: '第 1 轮',
      agentState: 'running',
      stageStartedAt: 90,
      updatedAt: 100,
    })
    // 阶段事件同样计入会话活跃信息
    expect(view.result.current.sessions.get('session-a')?.eventCount).toBe(1)

    // updated_at 更旧的状态不应覆盖已有状态
    emitMonitorEvent(
      'stage.status',
      makeStageData({ event_id: 202, stage: '过期阶段', updated_at: 50, timestamp: 101 })
    )
    expect(view.result.current.stageStatuses.get('session-a')?.stage).toBe('规划中')
  })

  it('stage.snapshot 批量写入阶段状态并跳过非法条目', async () => {
    const hookModule = await importHookModule()
    const view = await mountMonitor(hookModule)

    emitMonitorEvent('stage.snapshot', {
      event_id: 801,
      timestamp: 300,
      entries: [
        makeStageData({ session_id: 'session-a', stage: '回复中', updated_at: 10 }),
        { session_id: 'session-b', stage: '思考中' },
        '非法条目',
        { stage: '缺少会话 ID' },
      ],
    })

    expect(view.result.current.stageStatuses.size).toBe(2)
    expect(view.result.current.stageStatuses.get('session-a')?.stage).toBe('回复中')
    expect(view.result.current.stageStatuses.get('session-b')?.stage).toBe('思考中')
    // 快照事件不进时间线也不更新会话
    expect(view.result.current.allTimeline).toHaveLength(0)
    expect(view.result.current.sessions.size).toBe(0)
  })

  it('stage.removed 移除对应会话的阶段状态', async () => {
    const hookModule = await importHookModule()
    const view = await mountMonitor(hookModule)

    emitMonitorEvent('stage.status', makeStageData({ event_id: 811 }))
    expect(view.result.current.stageStatuses.has('session-a')).toBe(true)

    emitMonitorEvent('stage.removed', { event_id: 812, session_id: 'session-a', timestamp: 101 })
    expect(view.result.current.stageStatuses.has('session-a')).toBe(false)
    expect(view.result.current.stageStatuses.size).toBe(0)
  })
})

describe('message.updated 就地更新', () => {
  it('更新已入账消息的内容、回复引用与媒体，不新增时间线条目', async () => {
    const hookModule = await importHookModule()
    const view = await mountMonitor(hookModule)

    emitMonitorEvent('message.ingested', makeMessageData({ event_id: 401, content: '原始内容' }))
    emitMonitorEvent('message.updated', {
      event_id: 402,
      session_id: 'session-a',
      message_id: 'msg-1',
      content: '修正后的内容',
      reply_to: { message_id: 'msg-0', sender_name: '李四', content: '被引用的消息' },
      media: [{ kind: 'image', hash: 'h1', text: '[图片]', url: 'http://example/1.png' }],
      timestamp: 105,
    })

    expect(view.result.current.allTimeline).toHaveLength(1)
    expect(view.result.current.allTimeline[0]).toMatchObject({
      id: 'evt_401',
      type: 'message.ingested',
      data: {
        message_id: 'msg-1',
        content: '修正后的内容',
        reply_to: { message_id: 'msg-0', sender_name: '李四', content: '被引用的消息' },
        media: [{ kind: 'image', hash: 'h1', text: '[图片]', url: 'http://example/1.png' }],
      },
    })
  })

  it('未匹配到 message_id 时时间线保持不变', async () => {
    const hookModule = await importHookModule()
    const view = await mountMonitor(hookModule)

    emitMonitorEvent('message.ingested', makeMessageData({ event_id: 411, content: '原始内容' }))
    emitMonitorEvent('message.updated', {
      event_id: 412,
      session_id: 'session-a',
      message_id: 'msg-404',
      content: '不该生效的内容',
      timestamp: 106,
    })

    expect(view.result.current.allTimeline).toHaveLength(1)
    expect(view.result.current.allTimeline[0].data).toMatchObject({ content: '原始内容' })
  })
})

describe('会话选择、清空与持久化', () => {
  it('setSelectedSession 切换过滤会话，传 null 时显示全部时间线', async () => {
    const hookModule = await importHookModule()
    const view = await mountMonitor(hookModule)

    emitMonitorEvent('message.ingested', makeMessageData({ event_id: 901, timestamp: 100 }))
    emitMonitorEvent(
      'message.ingested',
      makeMessageData({
        event_id: 902,
        session_id: 'session-b',
        session_name: '另一个群',
        group_id: 'group-2',
        message_id: 'msg-b1',
        timestamp: 200,
      })
    )

    // 自动选中首个会话，timeline 只含该会话的条目
    expect(view.result.current.selectedSession).toBe('session-a')
    expect(view.result.current.allTimeline).toHaveLength(2)
    expect(view.result.current.timeline.map((entry) => entry.sessionId)).toEqual(['session-a'])

    act(() => view.result.current.setSelectedSession('session-b'))
    expect(view.result.current.selectedSession).toBe('session-b')
    expect(view.result.current.timeline.map((entry) => entry.sessionId)).toEqual(['session-b'])

    act(() => view.result.current.setSelectedSession(null))
    expect(view.result.current.timeline).toHaveLength(2)
  })

  it('clearTimeline 清空内存状态并清空持久化存储', async () => {
    const hookModule = await importHookModule()
    const view = await mountMonitor(hookModule)

    emitMonitorEvent('message.ingested', makeMessageData({ event_id: 911 }))
    emitMonitorEvent('stage.status', makeStageData({ event_id: 912, timestamp: 101 }))
    expect(view.result.current.allTimeline).toHaveLength(1)

    act(() => view.result.current.clearTimeline())

    expect(view.result.current.allTimeline).toHaveLength(0)
    expect(view.result.current.timeline).toHaveLength(0)
    expect(view.result.current.sessions.size).toBe(0)
    expect(view.result.current.stageStatuses.size).toBe(0)
    expect(view.result.current.selectedSession).toBeNull()

    // 三个对象仓库全部被清空
    await waitFor(() => expect(fakeStores.timeline.clear).toHaveBeenCalledTimes(1))
    expect(fakeStores.sessions.clear).toHaveBeenCalledTimes(1)
    expect(fakeStores.meta.clear).toHaveBeenCalledTimes(1)
  })

  it('事件入账后经防抖把条目、会话与元数据写入 IndexedDB', async () => {
    const hookModule = await importHookModule()
    const view = await mountMonitor(hookModule)

    const data = makeMessageData({ event_id: 501 })
    emitMonitorEvent('message.ingested', data)
    expect(view.result.current.allTimeline).toHaveLength(1)

    // 300ms 防抖后触发一次快照落盘
    await waitFor(() => expect(fakeStores.timeline.put).toHaveBeenCalledTimes(1))
    expect(fakeStores.timeline.put).toHaveBeenCalledWith({
      id: 'evt_501',
      eventId: 501,
      type: 'message.ingested',
      data,
      timestamp: 100,
      sessionId: 'session-a',
      persistedAt: expect.any(Number),
    })
    expect(fakeStores.sessions.put).toHaveBeenCalledWith(
      expect.objectContaining({ sessionId: 'session-a', eventCount: 1 })
    )
    expect(fakeStores.meta.put).toHaveBeenCalledWith({ key: 'selectedSession', value: 'session-a' })
    expect(fakeStores.meta.put).toHaveBeenCalledWith({ key: 'entryCounter', value: 0 })
    expect(fakeStores.meta.put).toHaveBeenCalledWith({ key: 'lastEventId', value: 501 })
  })
})
