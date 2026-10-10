/**
 * useMaisakaMonitor Hook 单元测试
 *
 * 该模块持有大量模块级缓存状态（时间线、会话、阶段状态、订阅计数、历史补发等），
 * 因此所有用例都通过 vi.resetModules + 动态 import 获取全新模块实例，避免跨用例污染。
 * WebSocket 订阅链路整体 mock @/lib/maisaka-monitor-client，历史通过事件回放恢复。
 */
import { act, renderHook } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { Mock } from 'vitest'

import type { MaisakaEventListener, MaisakaMonitorEvent } from '@/lib/maisaka-monitor-client'
import type { TimelineEntry } from './use-maisaka-monitor'

type MonitorHookModule = typeof import('./use-maisaka-monitor')

// 监控客户端单例 mock：捕获 subscribe 传入的事件处理器以便测试中手动派发事件
const clientMocks = vi.hoisted(() => ({
  onConnectionChange: vi.fn(),
  subscribe: vi.fn(),
}))

vi.mock('@/lib/maisaka-monitor-client', () => ({
  maisakaMonitorClient: clientMocks,
}))

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
let connectionListener: ((connected: boolean) => void) | null = null
let connectionUnsubscribeMock: Mock<() => void>

async function importHookModule(): Promise<MonitorHookModule> {
  return import('./use-maisaka-monitor')
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

/** 在 act 内模拟底层 WebSocket 连接状态变化 */
function emitConnectionChange(connected: boolean) {
  expect(connectionListener).not.toBeNull()
  act(() => {
    connectionListener?.(connected)
  })
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
  vi.spyOn(window, 'requestAnimationFrame').mockImplementation((callback) => {
    callback(0)
    return 1
  })

  capturedHandler = null
  unsubscribeMock = vi.fn(async () => {})
  clientMocks.subscribe.mockImplementation(async (listener: MaisakaEventListener) => {
    capturedHandler = listener
    return unsubscribeMock
  })

  connectionListener = null
  connectionUnsubscribeMock = vi.fn()
  clientMocks.onConnectionChange.mockImplementation((listener: (connected: boolean) => void) => {
    connectionListener = listener
    // 与真实客户端一致：注册时立即回调当前连接状态
    listener(true)
    return connectionUnsubscribeMock
  })
})

describe('账本恢复与批量刷新', () => {
  it('裁切学习进度在原位置更新，旧事件不能回滚，轮次间互不覆盖', async () => {
    const view = await mountMonitor(await importHookModule())
    const data = {
      session_id: 'session-a', run_id: 'run-1', cycle_id: 1,
      step: 'context.trimmed', title: '上下文裁切', detail: '', timestamp: 100,
    }
    emitMonitorEvent('flow.step', { ...data, event_id: 101, trim: { learning: { status: 'running' } } })
    emitMonitorEvent('message.ingested', makeMessageData({ event_id: 102 }))
    emitMonitorEvent('flow.step', { ...data, event_id: 104, timestamp: 105, trim: { learning: { status: 'completed' } } })
    emitMonitorEvent('flow.step', { ...data, event_id: 103, trim: { learning: { status: 'running' } } })
    emitMonitorEvent('flow.step', { ...data, cycle_id: 2, event_id: 105 })
    const timeline = view.result.current.allTimeline
    expect(timeline).toHaveLength(3)
    expect(timeline[0]).toMatchObject({ id: 'evt_101', timestamp: 100, data: { trim: { learning: { status: 'completed' } } } })
    expect(timeline[1].type).toBe('message.ingested')
    expect(timeline[2].data).toMatchObject({ cycle_id: 2 })
  })

  it('旧浏览器游标不影响刷新后的历史恢复', async () => {
    window.localStorage.setItem('maisaka-monitor-last-event-id', '9999')
    const view = await mountMonitor(await importHookModule(), false)
    emitMonitorEvent('replay.started', {})
    emitMonitorEvent('message.ingested', makeMessageData({ event_id: 101 }))
    emitMonitorEvent('stage.snapshot', { entries: [], timestamp: 100 })
    expect(view.result.current.allTimeline.map((entry) => entry.eventId)).toEqual([101])
    expect(window.localStorage.getItem('maisaka-monitor-last-event-id')).toBe('9999')
  })

  it('实时更新与历史交错时，先恢复原消息再应用识图结果', async () => {
    const view = await mountMonitor(await importHookModule())
    emitMonitorEvent('replay.started', {})
    emitMonitorEvent('message.updated', makeMessageData({ event_id: 102, content: '识图完成' }))
    emitMonitorEvent('message.ingested', makeMessageData({ event_id: 101, content: '识图中' }))
    expect(view.result.current.allTimeline).toHaveLength(0)
    emitMonitorEvent('stage.snapshot', { entries: [], timestamp: 100 })
    expect(view.result.current.allTimeline).toHaveLength(1)
    expect(view.result.current.allTimeline[0].data).toMatchObject({ content: '识图完成' })
  })

  it('回放中断后重新恢复，不漏消息也不重复计数', async () => {
    const view = await mountMonitor(await importHookModule())
    emitMonitorEvent('message.ingested', makeMessageData({ event_id: 101 }))
    emitMonitorEvent('replay.started', {})
    emitMonitorEvent('message.ingested', makeMessageData({ event_id: 102, message_id: 'msg-2' }))
    emitConnectionChange(false)
    emitConnectionChange(true)
    emitMonitorEvent('replay.started', {})
    emitMonitorEvent('message.ingested', makeMessageData({ event_id: 101 }))
    emitMonitorEvent('message.ingested', makeMessageData({ event_id: 102, message_id: 'msg-2' }))
    emitMonitorEvent('message.ingested', makeMessageData({ event_id: 103, message_id: 'msg-3' }))
    emitMonitorEvent('stage.snapshot', { entries: [], timestamp: 100 })
    expect(view.result.current.allTimeline.map((entry) => entry.eventId)).toEqual([101, 102, 103])
    expect(view.result.current.sessions.get('session-a')?.eventCount).toBe(3)
  })

  it('同一帧的实时事件只刷新一次，并完整展示全部消息', async () => {
    const hookModule = await importHookModule()
    const view = await mountMonitor(hookModule)
    let frame: FrameRequestCallback | undefined
    const schedule = vi
      .spyOn(window, 'requestAnimationFrame')
      .mockImplementation((callback) => {
        frame = callback
        return 1
      })
      .mockClear()
    emitMonitorEvents(
      Array.from({ length: 100 }, (_, index) => ({
        type: 'message.ingested',
        data: makeMessageData({ event_id: index + 1, message_id: `burst-${index}` }),
      }))
    )
    expect(schedule).toHaveBeenCalledTimes(1)
    expect(view.result.current.allTimeline).toHaveLength(0)
    act(() => frame?.(0))
    expect(view.result.current.allTimeline).toHaveLength(100)
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

    // 补发期间暂存事件，完成后统一入账，避免逐条触发侧栏重排和自动滚动。
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

  it('connected 跟随底层连接断开与恢复', async () => {
    const hookModule = await importHookModule()
    const view = await mountMonitor(hookModule)
    expect(view.result.current.connected).toBe(true)

    emitConnectionChange(false)
    expect(view.result.current.connected).toBe(false)

    emitConnectionChange(true)
    expect(view.result.current.connected).toBe(true)
    // 已有订阅由底层客户端在重连时自行恢复，不重复订阅
    expect(clientMocks.subscribe).toHaveBeenCalledTimes(1)
  })

  it('订阅失败后，连接恢复时自动补订阅', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    clientMocks.subscribe.mockRejectedValueOnce(new Error('后端尚未就绪'))
    const hookModule = await importHookModule()
    const view = await mountMonitor(hookModule, false)
    expect(view.result.current.connected).toBe(false)

    emitConnectionChange(true)
    await act(async () => {})
    emitMonitorEvent('stage.snapshot', { entries: [], timestamp: 100 })

    expect(clientMocks.subscribe).toHaveBeenCalledTimes(2)
    expect(view.result.current.connected).toBe(true)
  })

  it('最后一个消费者卸载时停止监听连接状态，之后的连接恢复不再订阅', async () => {
    const hookModule = await importHookModule()
    const view = await mountMonitor(hookModule)
    view.unmount()

    expect(connectionUnsubscribeMock).toHaveBeenCalledTimes(1)
    connectionListener?.(true)
    expect(clientMocks.subscribe).toHaveBeenCalledTimes(1)
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
  })

  it('相同 event_id 的事件只入账一次', async () => {
    const hookModule = await importHookModule()
    const view = await mountMonitor(hookModule)

    const data = makeMessageData({ event_id: 701 })
    emitMonitorEvent('message.ingested', data)
    emitMonitorEvent('message.ingested', data)

    expect(view.result.current.allTimeline).toHaveLength(1)
  })

  it('缺少 event_id 的事件使用自增序号生成条目 ID', async () => {
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
  })

  it('时间线按事件账本顺序排列（消息时间早于推理完成时间时仍保持事件顺序）', async () => {
    const hookModule = await importHookModule()
    const view = await mountMonitor(hookModule)

    emitMonitorEvent('message.sent', makeMessageData({ event_id: 202, timestamp: 100 }))
    emitMonitorEvent('message.ingested', makeMessageData({ event_id: 201, timestamp: 200 }))

    expect(view.result.current.allTimeline.map((entry) => entry.id)).toEqual(['evt_201', 'evt_202'])
    expect(view.result.current.allTimeline.map((entry) => entry.timestamp)).toEqual([200, 100])
  })

  it('同一轮 Planner 的过程和完成事件更新原卡片，并保留原时间线位置', async () => {
    const hookModule = await importHookModule()
    const view = await mountMonitor(hookModule)
    const plannerData = {
      session_id: 'session-a',
      cycle_id: 7,
      run_id: 'run-a',
      timestamp: 100,
      planner: { content: '先调用工具' },
      tools: [],
    }

    emitMonitorEvent('planner.progress', { ...plannerData, event_id: 301 })
    emitMonitorEvent('message.sent', makeMessageData({ event_id: 302, timestamp: 101 }))
    emitMonitorEvent('planner.progress', {
      ...plannerData,
      event_id: 303,
      timestamp: 102,
      tools: [{ tool_call_id: 'tool-1', summary: '完成' }],
    })
    emitMonitorEvent('planner.finalized', {
      ...plannerData,
      event_id: 304,
      timestamp: 103,
      tools: [{ tool_call_id: 'tool-1', summary: '完成' }],
    })

    expect(view.result.current.allTimeline.map((entry) => entry.id)).toEqual(['evt_301', 'evt_302'])
    expect(view.result.current.allTimeline[0]).toMatchObject({
      type: 'planner.finalized',
      data: { event_id: 304, cycle_id: 7 },
    })
  })

  it('重启后复用轮次编号时保留新旧两张 Planner 卡片', async () => {
    const hookModule = await importHookModule()
    const view = await mountMonitor(hookModule)
    const plannerData = { session_id: 'session-a', cycle_id: 1, timestamp: 100, tools: [] }

    emitMonitorEvent('planner.finalized', { ...plannerData, run_id: 'run-old', event_id: 401 })
    emitMonitorEvent('planner.progress', {
      ...plannerData,
      run_id: 'run-new',
      event_id: 402,
      timestamp: 200,
    })

    expect(view.result.current.allTimeline.map((entry) => entry.id)).toEqual(['evt_401', 'evt_402'])
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

describe('会话详情与全局摘要隔离', () => {
  it('交错到达的旧 Planner 回放不会覆盖较新的完成状态', async () => {
    const hooks = await importHookModule()
    const view = await mountMonitor(hooks)
    const data = { session_id: 'session-a', run_id: 'r', cycle_id: 1, timestamp: 100 }
    emitMonitorEvent('planner.progress', { ...data, event_id: 301 })
    emitMonitorEvent('planner.finalized', {
      ...data,
      event_id: 303,
      tools: [{ summary: '已完成' }],
    })
    const before = view.result.current.timeline
    emitMonitorEvent('planner.progress', { ...data, event_id: 302, tools: [] })
    expect(view.result.current.timeline).toBe(before)
    expect(view.result.current.timeline[0].type).toBe('planner.finalized')
    expect(view.result.current.timeline[0].data).toMatchObject({ event_id: 303 })
  })

  it('其他群的消息、识图和阶段更新只刷新摘要，切过去直接显示已更新的记录', async () => {
    const hooks = await importHookModule()
    const overview = renderHook(() => hooks.useMaisakaMonitorOverview())
    let detailRenders = 0
    const detail = renderHook(() => {
      detailRenders += 1
      return hooks.useMaisakaMonitorSession()
    })
    await act(async () => {})
    emitMonitorEvent('stage.snapshot', { entries: [], timestamp: 100 })
    emitMonitorEvent('message.ingested', makeMessageData())
    const before = detail.result.current
    const rendersBefore = detailRenders

    emitMonitorEvent(
      'message.ingested',
      makeMessageData({
        event_id: 102,
        session_id: 'session-b',
        content: 'B 群消息',
        timestamp: 200,
      })
    )
    emitMonitorEvent(
      'message.updated',
      makeMessageData({
        event_id: 103,
        session_id: 'session-b',
        content: 'B 群识图完成',
        timestamp: 201,
      })
    )
    emitMonitorEvent(
      'stage.status',
      makeStageData({
        event_id: undefined,
        session_id: 'session-b',
        stage: '执行工具',
        timestamp: 202,
      })
    )

    expect(detail.result.current).toBe(before)
    expect(detailRenders).toBe(rendersBefore)
    expect(overview.result.current.latestMessages.get('session-b')?.data).toMatchObject({
      content: 'B 群识图完成',
    })
    expect(overview.result.current.sessions.get('session-b')?.lastActivity).toBe(202)
    expect(overview.result.current.stageStatuses.get('session-b')?.stage).toBe('执行工具')
    // 两个群即使使用相同的消息 ID，也不会更新错会话。
    expect(detail.result.current.timeline[0].data).toMatchObject({ content: '你好' })

    act(() => overview.result.current.setSelectedSession('session-b'))
    expect(detail.result.current.timeline).toHaveLength(1)
    expect(detail.result.current.timeline[0].data).toMatchObject({ content: 'B 群识图完成' })
    expect(detail.result.current.selectedStageStatus?.stage).toBe('执行工具')
    expect(clientMocks.subscribe).toHaveBeenCalledTimes(1)

    act(() => detail.result.current.clearTimeline())
    expect(overview.result.current.latestMessages.size).toBe(0)
    expect(detail.result.current.timeline).toHaveLength(0)
  })

  it('后台群的 Planner 进度合并不刷新当前群，阶段更新不重建最新消息摘要', async () => {
    const hooks = await importHookModule()
    const overview = renderHook(() => hooks.useMaisakaMonitorOverview())
    const detail = renderHook(() => hooks.useMaisakaMonitorSession())
    await act(async () => {})
    emitMonitorEvent('stage.snapshot', { entries: [], timestamp: 100 })
    emitMonitorEvent('message.ingested', makeMessageData())
    const before = detail.result.current
    const previews = overview.result.current.latestMessages
    const planner = { session_id: 'session-b', run_id: 'run-b', cycle_id: 1, timestamp: 200 }
    emitMonitorEvent('planner.progress', { ...planner, event_id: 102 })
    emitMonitorEvent('planner.finalized', { ...planner, event_id: 103, tools: [] })
    expect(detail.result.current).toBe(before)
    expect(overview.result.current.latestMessages).toBe(previews)

    emitMonitorEvent('stage.status', makeStageData({ event_id: undefined }))
    expect(detail.result.current.selectedStageStatus?.stage).toBe('规划中')
    expect(detail.result.current.timeline).toBe(before.timeline)
    expect(overview.result.current.latestMessages).toBe(previews)
    act(() => overview.result.current.setSelectedSession('session-b'))
    expect(detail.result.current.timeline).toHaveLength(1)
    expect(detail.result.current.timeline[0].type).toBe('planner.finalized')
  })

  it('乱序补发和旧消息识图不覆盖最新消息，也不会让活动时间倒退', async () => {
    const hooks = await importHookModule()
    const view = await mountMonitor(hooks)
    emitMonitorEvent(
      'message.ingested',
      makeMessageData({ event_id: 103, message_id: 'new', timestamp: 300 })
    )
    emitMonitorEvent(
      'message.ingested',
      makeMessageData({ event_id: 101, message_id: 'old', timestamp: 100 })
    )
    emitMonitorEvent(
      'message.updated',
      makeMessageData({ event_id: 104, message_id: 'old', content: '旧图', timestamp: 100 })
    )
    expect(view.result.current.timeline.map((entry) => entry.eventId)).toEqual([101, 103])
    expect(view.result.current.latestMessages.get('session-a')?.data).toMatchObject({
      message_id: 'new',
    })
    expect(view.result.current.sessions.get('session-a')?.lastActivity).toBe(300)
  })

  it('其他群挤出旧历史后仍保留该群摘要，并能接收迟到的识图结果', async () => {
    const restoredEntries = Array.from({ length: 3000 }, (_, index) => ({
      id: `evt_${index + 1}`,
      eventId: index + 1,
      type: 'message.ingested' as const,
      data: makeMessageData({
        session_id: index === 0 ? 'session-a' : 'session-b',
        message_id: `msg-${index + 1}`,
      }) as unknown as TimelineEntry['data'],
      timestamp: index + 1,
      sessionId: index === 0 ? 'session-a' : 'session-b',
      persistedAt: 1,
    }))
    const hooks = await importHookModule()
    const view = await mountMonitor(hooks)
    act(() => {
      for (const entry of restoredEntries) {
        capturedHandler?.({
          type: entry.type,
          data: { ...entry.data, event_id: entry.eventId },
        } as unknown as MaisakaMonitorEvent)
      }
    })
    emitMonitorEvent(
      'message.ingested',
      makeMessageData({
        event_id: 3001,
        session_id: 'session-b',
        message_id: 'new-b',
        timestamp: 3001,
      })
    )
    expect(view.result.current.allTimeline.some((entry) => entry.sessionId === 'session-a')).toBe(
      false
    )
    expect(view.result.current.latestMessages.has('session-a')).toBe(true)
    emitMonitorEvent(
      'message.updated',
      makeMessageData({
        event_id: 3002,
        message_id: 'msg-1',
        content: '识图完成',
        timestamp: 3002,
      })
    )
    expect(view.result.current.latestMessages.get('session-a')?.data).toMatchObject({
      content: '识图完成',
    })
    expect(view.result.current.allTimeline).toHaveLength(3000)
  })
})

describe('会话选择与清空', () => {
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

  it('clearTimeline 清空内存状态', async () => {
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
  })
})

describe('事件边界与空态', () => {
  it('stage.snapshot 非数组、stage.removed 缺 session_id 时保持原状态', async () => {
    const hookModule = await importHookModule()
    const view = await mountMonitor(hookModule)

    emitMonitorEvent('stage.status', makeStageData({ event_id: 1501 }))
    emitMonitorEvent('stage.snapshot', { event_id: 1502, timestamp: 110, entries: null })
    emitMonitorEvent('stage.removed', { event_id: 1503, timestamp: 111 })
    emitMonitorEvent('stage.status', makeStageData({ event_id: 1504, session_id: undefined }))

    expect(view.result.current.stageStatuses.get('session-a')?.stage).toBe('规划中')
    expect(view.result.current.stageStatuses.size).toBe(1)
  })

  it('message.updated 缺少 message_id 时不改写内容', async () => {
    const hookModule = await importHookModule()
    const view = await mountMonitor(hookModule)

    emitMonitorEvent('message.ingested', makeMessageData({ event_id: 1601, content: '原始内容' }))
    emitMonitorEvent('message.updated', {
      event_id: 1602,
      session_id: 'session-a',
      content: '不该生效',
      timestamp: 101,
    })

    expect(view.result.current.allTimeline[0].data).toMatchObject({ content: '原始内容' })
  })

  it('event_id 为 0 或负数时仍生成独立时间线条目', async () => {
    const hookModule = await importHookModule()
    const view = await mountMonitor(hookModule)

    emitMonitorEvent('message.ingested', makeMessageData({ event_id: 0, message_id: 'msg-0' }))
    emitMonitorEvent('message.ingested', makeMessageData({ event_id: -3, message_id: 'msg-neg' }))

    expect(view.result.current.allTimeline).toHaveLength(2)
  })

  it('群聊名称已含群号时不重复拼接，缺标识时用 session_id 前八位', async () => {
    const hookModule = await importHookModule()
    const view = await mountMonitor(hookModule)

    emitMonitorEvent('session.start', {
      event_id: 1701,
      session_id: 'session-g',
      session_name: '测试群(group-1)',
      is_group_chat: true,
      group_id: 'group-1',
      timestamp: 100,
    })
    emitMonitorEvent('session.start', {
      event_id: 1702,
      session_id: 'anonabcd1234',
      is_group_chat: true,
      timestamp: 110,
    })

    expect(view.result.current.sessions.get('session-g')?.sessionName).toBe('测试群(group-1)')
    expect(view.result.current.sessions.get('anonabcd1234')?.sessionName).toBe('anonabcd')
  })

  it('选中不存在的会话时过滤结果为空', async () => {
    const hookModule = await importHookModule()
    const view = await mountMonitor(hookModule)

    emitMonitorEvent('message.ingested', makeMessageData({ event_id: 1801 }))
    act(() => view.result.current.setSelectedSession('missing'))

    expect(view.result.current.selectedSession).toBe('missing')
    expect(view.result.current.timeline).toHaveLength(0)
    expect(view.result.current.allTimeline).toHaveLength(1)
  })
})

/** 批量派发监控事件，避免每条都包一层 act 带来的额外开销 */
function emitMonitorEvents(
  events: Array<{ type: MaisakaMonitorEvent['type']; data: Record<string, unknown> }>
) {
  expect(capturedHandler).not.toBeNull()
  act(() => {
    for (const event of events) {
      capturedHandler?.({ type: event.type, data: event.data } as unknown as MaisakaMonitorEvent)
    }
  })
}

describe('会话展示名补充', () => {
  it('群聊缺名称或名称等于群号/会话 ID 时直接使用 groupId', async () => {
    const hookModule = await importHookModule()
    const view = await mountMonitor(hookModule)

    emitMonitorEvent('session.start', {
      event_id: 2401,
      session_id: 'session-only-group',
      is_group_chat: true,
      group_id: 'group-only',
      timestamp: 100,
    })
    emitMonitorEvent('session.start', {
      event_id: 2402,
      session_id: 'session-same-group',
      session_name: 'group-same',
      is_group_chat: true,
      group_id: 'group-same',
      timestamp: 110,
    })
    emitMonitorEvent('session.start', {
      event_id: 2403,
      session_id: 'named-as-session',
      session_name: 'named-as-session',
      is_group_chat: true,
      group_id: 'group-from-id',
      timestamp: 120,
    })
    emitMonitorEvent('session.start', {
      event_id: 2404,
      session_id: 'session-blank-name',
      session_name: '   ',
      is_group_chat: true,
      group_id: 'group-blank',
      timestamp: 130,
    })

    expect(view.result.current.sessions.get('session-only-group')?.sessionName).toBe('group-only')
    expect(view.result.current.sessions.get('session-same-group')?.sessionName).toBe('group-same')
    expect(view.result.current.sessions.get('named-as-session')?.sessionName).toBe('group-from-id')
    expect(view.result.current.sessions.get('session-blank-name')?.sessionName).toBe('group-blank')
  })

  it('session.start 会重建已有会话并累加 eventCount', async () => {
    const hookModule = await importHookModule()
    const view = await mountMonitor(hookModule)

    emitMonitorEvent('message.ingested', makeMessageData({ event_id: 2411, timestamp: 100 }))
    emitMonitorEvent('session.start', {
      event_id: 2412,
      session_id: 'session-a',
      session_name: '新群名',
      is_group_chat: true,
      group_id: 'group-1',
      platform: 'qq',
      timestamp: 150,
    })

    expect(view.result.current.sessions.get('session-a')).toMatchObject({
      sessionName: '新群名(group-1)',
      lastActivity: 150,
      eventCount: 2,
    })
  })
})

describe('阶段状态与消息更新边界', () => {
  it('stage.status / stage.removed 的 session_id 非字符串时不改阶段状态', async () => {
    const hookModule = await importHookModule()
    const view = await mountMonitor(hookModule)

    emitMonitorEvent('stage.status', makeStageData({ event_id: 2701 }))
    emitMonitorEvent(
      'stage.status',
      makeStageData({ event_id: 2702, session_id: 123, stage: '不该写入', timestamp: 101 })
    )
    emitMonitorEvent('stage.removed', { event_id: 2703, session_id: 123, timestamp: 102 })

    expect(view.result.current.stageStatuses.size).toBe(1)
    expect(view.result.current.stageStatuses.get('session-a')?.stage).toBe('规划中')
  })

  it('阶段状态缺省字段回退为空串并用当前时间填充时间戳', async () => {
    const hookModule = await importHookModule()
    const view = await mountMonitor(hookModule)

    emitMonitorEvent('stage.status', {
      event_id: 2711,
      session_id: 'session-a',
      timestamp: 100,
    })

    const status = view.result.current.stageStatuses.get('session-a')
    expect(status).toMatchObject({
      sessionId: 'session-a',
      sessionName: undefined,
      stage: '',
      detail: '',
      roundText: '',
      agentState: '',
    })
    expect(status?.stageStartedAt).toEqual(expect.any(Number))
    expect(status?.updatedAt).toEqual(expect.any(Number))
  })

  it('message.updated 跳过其他会话和非消息条目，并就地更新 message.sent', async () => {
    const hookModule = await importHookModule()
    const view = await mountMonitor(hookModule)

    emitMonitorEvent('session.start', {
      event_id: 2801,
      session_id: 'session-a',
      session_name: '测试群',
      is_group_chat: true,
      group_id: 'group-1',
      timestamp: 90,
    })
    emitMonitorEvent(
      'message.ingested',
      makeMessageData({ event_id: 2802, content: '原始入站', timestamp: 100 })
    )
    emitMonitorEvent(
      'message.sent',
      makeMessageData({
        event_id: 2803,
        message_id: 'msg-sent',
        content: '原始回复',
        timestamp: 110,
      })
    )
    emitMonitorEvent(
      'message.ingested',
      makeMessageData({
        event_id: 2804,
        session_id: 'session-b',
        session_name: '另一个群',
        group_id: 'group-2',
        message_id: 'msg-b',
        content: '另一会话',
        timestamp: 120,
      })
    )

    emitMonitorEvent('message.updated', {
      event_id: 2805,
      session_id: 'session-a',
      message_id: 'msg-1',
      content: '修正入站',
      timestamp: 130,
    })
    emitMonitorEvent('message.updated', {
      event_id: 2806,
      session_id: 'session-a',
      message_id: 'msg-sent',
      content: 12,
      media: 'not-array',
      timestamp: 140,
    })

    const timeline = view.result.current.allTimeline
    expect(timeline).toHaveLength(4)
    expect(timeline[0]).toMatchObject({ type: 'session.start', sessionId: 'session-a' })
    expect(timeline[1].data).toMatchObject({ message_id: 'msg-1', content: '修正入站' })
    expect(timeline[2].data).toMatchObject({ message_id: 'msg-sent', content: '', media: [] })
    expect(timeline[3].data).toMatchObject({ message_id: 'msg-b', content: '另一会话' })
  })
})

describe('内存裁剪', () => {
  it('内存时间线超过上限时只保留最新的 3000 条', async () => {
    const restoredEntries = Array.from({ length: 3000 }, (_, index) => ({
      id: `evt_${index + 1}`,
      eventId: index + 1,
      type: 'message.ingested' as const,
      data: {
        session_id: 'session-a',
        message_id: `msg-cap-${index + 1}`,
        timestamp: index + 1,
      },
      timestamp: index + 1,
      sessionId: 'session-a',
      persistedAt: 1,
    }))
    const hookModule = await importHookModule()
    const view = await mountMonitor(hookModule)
    act(() => {
      for (const entry of restoredEntries) {
        capturedHandler?.({
          type: entry.type,
          data: { ...entry.data, event_id: entry.eventId },
        } as unknown as MaisakaMonitorEvent)
      }
    })
    expect(view.result.current.allTimeline).toHaveLength(3000)

    emitMonitorEvent(
      'message.ingested',
      makeMessageData({ event_id: 9000, message_id: 'msg-cap-new', timestamp: 4000 })
    )

    expect(view.result.current.allTimeline).toHaveLength(3000)
    expect(view.result.current.allTimeline[0].id).toBe('evt_2')
    expect(view.result.current.allTimeline[2999].id).toBe('evt_9000')
  })

  it('相同时间戳时按 evt_序号_ 的自增序号排序', async () => {
    const hookModule = await importHookModule()
    const view = await mountMonitor(hookModule)

    emitMonitorEvent(
      'message.ingested',
      makeMessageData({ event_id: undefined, message_id: 'msg-late', timestamp: 100 })
    )
    emitMonitorEvent(
      'message.ingested',
      makeMessageData({ event_id: undefined, message_id: 'msg-later', timestamp: 100 })
    )

    expect(view.result.current.allTimeline.map((entry) => entry.data)).toEqual([
      expect.objectContaining({ message_id: 'msg-late' }),
      expect.objectContaining({ message_id: 'msg-later' }),
    ])
    expect(view.result.current.allTimeline[0].id < view.result.current.allTimeline[1].id).toBe(true)
  })
})

describe('订阅进行中的共享与 SSR 短路', () => {
  it('订阅 Promise 未完成时第二个消费者不重复发起订阅', async () => {
    const deferred = createDeferred<() => Promise<void>>()
    clientMocks.subscribe.mockImplementation((listener: MaisakaEventListener) => {
      capturedHandler = listener
      return deferred.promise
    })
    const hookModule = await importHookModule()

    const first = renderHook(() => hookModule.useMaisakaMonitor())
    const second = renderHook(() => hookModule.useMaisakaMonitor())
    expect(clientMocks.subscribe).toHaveBeenCalledTimes(1)

    deferred.resolve(unsubscribeMock)
    await act(async () => {})
    emitMonitorEvent('stage.snapshot', { entries: [], timestamp: 100 })
    expect(first.result.current.connected).toBe(true)
    expect(second.result.current.connected).toBe(true)
  })
})
