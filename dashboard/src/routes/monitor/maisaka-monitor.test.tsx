/**
 * MaiSaka 聊天流实时监控组件测试
 *
 * 覆盖：空态与侧边栏折叠、会话列表排序/选择/状态圆点、阶段状态栏与统计浮层、
 * 各类时间线事件卡片（消息/媒体/反应门/规划器/工具/回复器）、
 * no_action 循环过滤、可折叠文本、推理记录跳转与滚动行为。
 *
 * useMaisakaMonitor hook 已有独立单测（同目录），此处整体打桩以便精确控制视图状态；
 * 虚拟滚动与路由跳转按仓库既有样板打桩。
 */
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { MaisakaMonitor } from './maisaka-monitor'
import type {
  MaisakaFinalizedToolResult,
  MaisakaMessageMedia,
  MaisakaPlannerBlock,
  MessageIngestedEvent,
  MessageSentEvent,
  PlannerFinalizedEvent,
  PlannerResponseEvent,
  ReplierResponseEvent,
  SessionStartEvent,
  TimingGateResultEvent,
  ToolExecutionEvent,
} from '@/lib/maisaka-monitor-client'
import type { SessionInfo, StageStatusInfo, TimelineEntry } from './use-maisaka-monitor'

const SIDEBAR_COLLAPSED_KEY = 'maisaka-monitor-sidebar-collapsed'

// jsdom 未实现 Element.prototype.scrollTo，空时间线的兜底滚动会在 rAF 回调中触发并抛出
// 未捕获异常，这里补一个空实现；需要断言滚动参数的用例会在视口实例上另行打桩。
Element.prototype.scrollTo = (() => {}) as unknown as Element['scrollTo']

// 监控 hook 整体打桩：组件只消费其返回的状态与两个动作回调
const monitorHookMocks = vi.hoisted(() => ({
  useMaisakaMonitor: vi.fn(),
}))

// 路由跳转桩：捕获推理记录跳转参数
const routerMocks = vi.hoisted(() => ({
  navigate: vi.fn(),
}))

// 虚拟滚动桩：直接渲染全部行，并暴露 scrollToIndex 供滚动断言
const virtualizerMocks = vi.hoisted(() => ({
  measureElement: vi.fn(),
  scrollToIndex: vi.fn(),
}))

vi.mock('./use-maisaka-monitor', () => ({
  useMaisakaMonitor: monitorHookMocks.useMaisakaMonitor,
}))

vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => routerMocks.navigate,
}))

vi.mock('@tanstack/react-virtual', () => ({
  useVirtualizer: ({ count, estimateSize }: { count: number; estimateSize: () => number }) => ({
    getTotalSize: () => count * estimateSize(),
    getVirtualItems: () =>
      Array.from({ length: count }, (_, index) => ({
        index,
        key: index,
        start: index * estimateSize(),
      })),
    measureElement: virtualizerMocks.measureElement,
    scrollToIndex: virtualizerMocks.scrollToIndex,
  }),
}))

// 头像解析依赖设置项与后端地址解析，桩为无头像（仅渲染回退字符/图标）。
// 使用普通函数而非 vi.fn，避免 mockReset 清空实现。
vi.mock('@/lib/avatar-url', () => ({
  useResolvedAvatarUrl: () => undefined,
}))

// hook 返回的两个动作回调（clearMocks 每个用例前会自动清空调用记录）
const hookActions = {
  clearTimeline: vi.fn(),
  setSelectedSession: vi.fn(),
}

type MonitorHookModule = typeof import('./use-maisaka-monitor')
type MonitorHookResult = ReturnType<MonitorHookModule['useMaisakaMonitor']>

interface MonitorStateOverrides {
  connected?: boolean
  selectedSession?: string | null
  sessions?: Map<string, SessionInfo>
  stageStatuses?: Map<string, StageStatusInfo>
  timeline?: TimelineEntry[]
}

/** 配置 useMaisakaMonitor 桩的返回值（mockReset 会清空实现，每个用例都需重新配置） */
function setupMonitorState(overrides: MonitorStateOverrides = {}) {
  const timeline = overrides.timeline ?? []
  const state: MonitorHookResult = {
    timeline,
    allTimeline: timeline,
    sessions: overrides.sessions ?? new Map(),
    stageStatuses: overrides.stageStatuses ?? new Map(),
    selectedSession: overrides.selectedSession ?? null,
    setSelectedSession: hookActions.setSelectedSession,
    connected: overrides.connected ?? true,
    clearTimeline: hookActions.clearTimeline,
  }
  monitorHookMocks.useMaisakaMonitor.mockReturnValue(state)
}

function nowSec() {
  return Date.now() / 1000
}

let entrySeq = 0

/** 构造一条时间线条目，id 全局自增保证渲染 key 唯一 */
function makeEntry(
  type: TimelineEntry['type'],
  data: TimelineEntry['data'],
  sessionId = 's1'
): TimelineEntry {
  entrySeq += 1
  return { id: `entry-${entrySeq}`, type, data, timestamp: nowSec(), sessionId }
}

function makeSession(overrides: Partial<SessionInfo> = {}): SessionInfo {
  return {
    sessionId: 's1',
    sessionName: '测试群(10086)',
    isGroupChat: true,
    groupId: '10086',
    userId: null,
    platform: 'qq',
    lastActivity: nowSec() - 5,
    eventCount: 3,
    ...overrides,
  }
}

function makeStatus(overrides: Partial<StageStatusInfo> = {}): StageStatusInfo {
  return {
    sessionId: 's1',
    stage: '规划中',
    detail: '',
    roundText: '',
    agentState: '',
    stageStartedAt: nowSec() - 5,
    updatedAt: nowSec() - 5,
    ...overrides,
  }
}

function makeIngested(overrides: Partial<MessageIngestedEvent> = {}): MessageIngestedEvent {
  return {
    session_id: 's1',
    speaker_name: '张三',
    content: '你好呀',
    message_id: 'msg-1',
    timestamp: 1753500000,
    ...overrides,
  }
}

function makeSent(overrides: Partial<MessageSentEvent> = {}): MessageSentEvent {
  return {
    session_id: 's1',
    speaker_name: '麦麦',
    content: '我来啦',
    message_id: 'sent-1',
    timestamp: 1753500001,
    ...overrides,
  }
}

function makeTimingGate(overrides: Partial<TimingGateResultEvent> = {}): TimingGateResultEvent {
  return {
    session_id: 's1',
    cycle_id: 1,
    action: 'continue',
    content: null,
    tool_calls: [],
    messages: [],
    prompt_tokens: 0,
    selected_history_count: 0,
    duration_ms: 500,
    timestamp: 1753500002,
    ...overrides,
  }
}

function makePlannerResponse(overrides: Partial<PlannerResponseEvent> = {}): PlannerResponseEvent {
  return {
    session_id: 's1',
    cycle_id: 1,
    content: '思考内容',
    tool_calls: [],
    prompt_tokens: 100,
    completion_tokens: 28,
    total_tokens: 128,
    duration_ms: 2000,
    timestamp: 1753500003,
    ...overrides,
  }
}

function makePlannerBlock(overrides: Partial<MaisakaPlannerBlock> = {}): MaisakaPlannerBlock {
  return {
    content: '决定回复用户',
    tool_calls: [],
    prompt_tokens: 10,
    completion_tokens: 5,
    total_tokens: 15,
    duration_ms: 1500,
    ...overrides,
  }
}

function makeFinalized(overrides: Partial<PlannerFinalizedEvent> = {}): PlannerFinalizedEvent {
  return {
    session_id: 's1',
    cycle_id: 1,
    timestamp: 1753500004,
    timing_gate: null,
    request: null,
    planner: makePlannerBlock(),
    tools: [],
    final_state: { time_records: {}, agent_state: 'idle' },
    ...overrides,
  }
}

function makeToolResult(
  overrides: Partial<MaisakaFinalizedToolResult> = {}
): MaisakaFinalizedToolResult {
  return {
    tool_call_id: 'tc-1',
    tool_name: 'send_message',
    tool_args: {},
    success: true,
    duration_ms: 300,
    summary: '已发送消息',
    ...overrides,
  }
}

function makeToolExecution(overrides: Partial<ToolExecutionEvent> = {}): ToolExecutionEvent {
  return {
    session_id: 's1',
    cycle_id: 1,
    tool_name: 'web_search',
    tool_args: {},
    result_summary: '查询完成',
    success: true,
    duration_ms: 42,
    timestamp: 1753500005,
    ...overrides,
  }
}

function makeReplier(overrides: Partial<ReplierResponseEvent> = {}): ReplierResponseEvent {
  return {
    session_id: 's1',
    content: '回复内容',
    reasoning: '',
    model_name: '',
    prompt_tokens: 0,
    completion_tokens: 0,
    total_tokens: 0,
    duration_ms: 800,
    success: true,
    timestamp: 1753500006,
    ...overrides,
  }
}

/** 等待挂载阶段 requestAnimationFrame 触发的自动滚动全部完成 */
async function flushAutoScroll() {
  await new Promise((resolve) => setTimeout(resolve, 80))
}

/** 在多个 ScrollArea 视口中定位包含指定文案的时间线视口 */
function findTimelineViewport(container: HTMLElement, markerText: string): HTMLDivElement {
  const viewport = Array.from(
    container.querySelectorAll<HTMLDivElement>('[data-radix-scroll-area-viewport]')
  ).find((el) => el.textContent?.includes(markerText))
  if (!viewport) throw new Error('未找到时间线滚动视口')
  return viewport
}

/** 定位“回到底部”按钮内的箭头图标（autoScroll 开启时带 text-primary 高亮） */
function getBackToBottomIcon(): SVGElement {
  const icon = screen.getByRole('button', { name: '回到底部' }).querySelector('svg')
  if (!icon) throw new Error('未找到回到底部按钮图标')
  return icon
}

/** 会话侧边栏按钮以会话名作为 title（内部文本 span 也带同名 title，需过滤出按钮） */
function getSessionButton(name: string): HTMLElement {
  const button = screen.getAllByTitle(name).find((el) => el.tagName === 'BUTTON')
  if (!button) throw new Error(`未找到会话按钮：${name}`)
  return button
}

beforeEach(() => {
  window.localStorage.clear()
  // 默认展开侧边栏，便于断言会话列表；折叠行为在专门用例内覆盖
  window.localStorage.setItem(SIDEBAR_COLLAPSED_KEY, 'false')
})

afterEach(() => {
  cleanup()
  window.localStorage.clear()
})

describe('MaisakaMonitor 空态与侧边栏', () => {
  it('无会话且无事件时渲染各区域空态与连接圆点', () => {
    setupMonitorState()
    const { container } = render(<MaisakaMonitor />)

    expect(screen.getByText('聊天流')).toBeInTheDocument()
    expect(screen.getByText('等待 MaiSaka 会话…')).toBeInTheDocument()
    expect(screen.getByText('等待 MaiSaka 推理事件…')).toBeInTheDocument()
    expect(screen.getByText('当 MaiSaka 处理新消息时，推理过程会实时展示在这里')).toBeInTheDocument()
    expect(screen.getByText('当前聊天流暂无阶段状态')).toBeInTheDocument()
    // connected=true 时侧边栏标题旁渲染绿色连接圆点
    expect(container.querySelector('.bg-emerald-500')).not.toBeNull()
  })

  it('connected 为 false 时不渲染连接圆点', () => {
    setupMonitorState({ connected: false })
    const { container } = render(<MaisakaMonitor />)

    expect(container.querySelector('.bg-emerald-500')).toBeNull()
  })

  it('无存档时默认折叠侧边栏，点击按钮展开并持久化状态', async () => {
    const user = userEvent.setup()
    window.localStorage.removeItem(SIDEBAR_COLLAPSED_KEY)
    setupMonitorState()
    render(<MaisakaMonitor />)

    // 默认折叠：空会话提示不渲染，折叠状态被写入 localStorage
    expect(screen.queryByText('等待 MaiSaka 会话…')).not.toBeInTheDocument()
    expect(window.localStorage.getItem(SIDEBAR_COLLAPSED_KEY)).toBe('true')

    await user.click(screen.getByRole('button', { name: '展开侧边栏' }))

    expect(screen.getByText('等待 MaiSaka 会话…')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '折叠侧边栏' })).toBeInTheDocument()
    expect(window.localStorage.getItem(SIDEBAR_COLLAPSED_KEY)).toBe('false')
  })
})

describe('会话侧边栏', () => {
  it('会话按最近活跃排序，展示事件数、相对时间、阶段与等待圆点', () => {
    const sessions = new Map<string, SessionInfo>([
      [
        's-old',
        makeSession({
          sessionId: 's-old',
          sessionName: '老群(1)',
          lastActivity: nowSec() - 120,
          eventCount: 9,
        }),
      ],
      [
        's-new',
        makeSession({
          sessionId: 's-new',
          sessionName: '新群(2)',
          lastActivity: nowSec() - 5,
          eventCount: 2,
        }),
      ],
    ])
    const stageStatuses = new Map<string, StageStatusInfo>([
      ['s-new', makeStatus({ sessionId: 's-new', stage: '回复中' })],
      ['s-old', makeStatus({ sessionId: 's-old', stage: '等待消息', agentState: 'wait' })],
    ])
    setupMonitorState({ sessions, stageStatuses })
    const { container } = render(<MaisakaMonitor />)

    // 插入顺序为 old→new，但渲染按 lastActivity 倒序：new 在前
    const buttons = screen
      .getAllByRole('button')
      .filter((el) => el.title === '老群(1)' || el.title === '新群(2)')
    expect(buttons.map((el) => el.title)).toEqual(['新群(2)', '老群(1)'])

    expect(screen.getByText('9')).toBeInTheDocument()
    expect(screen.getByText('2')).toBeInTheDocument()
    expect(screen.getByText('刚刚')).toBeInTheDocument()
    expect(screen.getByText('2分钟前')).toBeInTheDocument()
    expect(screen.getByText('回复中')).toBeInTheDocument()
    expect(screen.getByText('等待消息')).toBeInTheDocument()
    // 等待消息状态的会话头像角标为蓝色圆点
    expect(container.querySelector('.bg-blue-500')).not.toBeNull()
  })

  it('点击会话按钮回调 setSelectedSession', async () => {
    const user = userEvent.setup()
    const sessions = new Map<string, SessionInfo>([
      ['s-new', makeSession({ sessionId: 's-new', sessionName: '新群(2)' })],
    ])
    setupMonitorState({ sessions })
    render(<MaisakaMonitor />)

    await user.click(getSessionButton('新群(2)'))

    expect(hookActions.setSelectedSession).toHaveBeenCalledWith('s-new')
  })
})

describe('阶段状态栏与工具条', () => {
  it('展示选中会话的阶段徽章、轮次、agent 状态、详情与更新时间', () => {
    const stageStatuses = new Map<string, StageStatusInfo>([
      [
        's1',
        makeStatus({
          stage: '回复中',
          roundText: '第 2 轮',
          agentState: 'running',
          detail: '正在生成回复',
          updatedAt: nowSec() - 3,
        }),
      ],
    ])
    setupMonitorState({ selectedSession: 's1', stageStatuses })
    render(<MaisakaMonitor />)

    expect(screen.getByText('回复中')).toBeInTheDocument()
    expect(screen.getByText('第 2 轮')).toBeInTheDocument()
    expect(screen.getByText('running')).toBeInTheDocument()
    expect(screen.getByText('正在生成回复')).toBeInTheDocument()
    expect(screen.getByText('更新于 刚刚')).toBeInTheDocument()
    expect(screen.queryByText('当前聊天流暂无阶段状态')).not.toBeInTheDocument()
  })

  it('统计浮层汇总消息、循环与工具调用数量', async () => {
    const user = userEvent.setup()
    const timeline = [
      makeEntry('message.ingested', makeIngested()),
      makeEntry('message.sent', makeSent()),
      makeEntry(
        'planner.finalized',
        makeFinalized({ tools: [makeToolResult(), makeToolResult({ tool_call_id: 'tc-2' })] })
      ),
      makeEntry('tool.execution', makeToolExecution()),
    ]
    setupMonitorState({ timeline })
    render(<MaisakaMonitor />)

    await user.hover(screen.getByText('统计'))

    // 消息 2（接收+发送）、循环 1（finalized）、工具调用 3（finalized 2 个 + 执行事件 1 个）
    const messageStat = await screen.findAllByText('消息：2')
    expect(messageStat.length).toBeGreaterThan(0)
    expect(screen.getAllByText('循环：1').length).toBeGreaterThan(0)
    expect(screen.getAllByText('工具调用：3').length).toBeGreaterThan(0)
  })

  it('回到底部按钮触发平滑滚动，清空按钮调用 clearTimeline', async () => {
    const user = userEvent.setup()
    const timeline = [
      makeEntry('message.ingested', makeIngested({ content: '第一条' })),
      makeEntry('message.ingested', makeIngested({ content: '第二条' })),
      makeEntry('message.ingested', makeIngested({ content: '第三条' })),
    ]
    setupMonitorState({ timeline })
    render(<MaisakaMonitor />)

    // 等挂载期的自动滚动完成后再断言按钮触发的平滑滚动
    await flushAutoScroll()
    virtualizerMocks.scrollToIndex.mockClear()

    await user.click(screen.getByRole('button', { name: '回到底部' }))
    expect(virtualizerMocks.scrollToIndex).toHaveBeenCalledWith(2, {
      align: 'end',
      behavior: 'smooth',
    })

    await user.click(screen.getByRole('button', { name: '清空' }))
    expect(hookActions.clearTimeline).toHaveBeenCalledTimes(1)
  })

  it('远离底部滚动后关闭自动滚动，点击回到底部恢复', async () => {
    const user = userEvent.setup()
    const timeline = [
      makeEntry('message.ingested', makeIngested({ content: '第一条' })),
      makeEntry('message.ingested', makeIngested({ content: '第二条' })),
    ]
    setupMonitorState({ timeline })
    const { container } = render(<MaisakaMonitor />)

    await flushAutoScroll()
    expect(getBackToBottomIcon()).toHaveClass('text-primary')

    // 模拟一个距底部 700px 的滚动位置（阈值 80px），应关闭自动滚动
    const viewport = findTimelineViewport(container, '第一条')
    Object.defineProperty(viewport, 'scrollHeight', { configurable: true, value: 1000 })
    Object.defineProperty(viewport, 'clientHeight', { configurable: true, value: 200 })
    Object.defineProperty(viewport, 'scrollTop', { configurable: true, value: 100 })
    fireEvent.scroll(viewport)

    await waitFor(() => expect(getBackToBottomIcon()).not.toHaveClass('text-primary'))

    virtualizerMocks.scrollToIndex.mockClear()
    await user.click(screen.getByRole('button', { name: '回到底部' }))
    expect(virtualizerMocks.scrollToIndex).toHaveBeenCalledWith(1, {
      align: 'end',
      behavior: 'smooth',
    })
    await waitFor(() => expect(getBackToBottomIcon()).toHaveClass('text-primary'))
  })

  it('时间线为空时回到底部退化为视口滚动', async () => {
    const user = userEvent.setup()
    setupMonitorState()
    const { container } = render(<MaisakaMonitor />)

    const viewport = findTimelineViewport(container, '等待 MaiSaka 推理事件…')
    const scrollToSpy = vi.fn()
    viewport.scrollTo = scrollToSpy as unknown as HTMLElement['scrollTo']

    await flushAutoScroll()
    scrollToSpy.mockClear()
    await user.click(screen.getByRole('button', { name: '回到底部' }))

    expect(virtualizerMocks.scrollToIndex).not.toHaveBeenCalled()
    expect(scrollToSpy).toHaveBeenCalledWith({ top: 0, behavior: 'smooth' })
  })
})

describe('时间线事件卡片', () => {
  it('渲染接收消息与已发送消息卡片及回复预览', () => {
    const timeline = [
      makeEntry(
        'message.ingested',
        makeIngested({
          speaker_name: '张三',
          content: '你好呀',
          reply_to: { message_id: 'm-9', sender_name: '李四', content: '原始消息' },
        })
      ),
      makeEntry(
        'message.sent',
        makeSent({
          speaker_name: '',
          content: '收到！',
          reply_to: { message_id: '', sender_name: '', content: '' },
        })
      ),
      makeEntry('message.ingested', makeIngested({ speaker_name: '王五', content: '' })),
    ]
    setupMonitorState({ timeline })
    render(<MaisakaMonitor />)

    expect(screen.getByText('张三')).toBeInTheDocument()
    expect(screen.getByText('你好呀')).toBeInTheDocument()
    expect(screen.getByText('回复 李四')).toBeInTheDocument()
    expect(screen.getByText('#m-9')).toBeInTheDocument()
    expect(screen.getByText('原始消息')).toBeInTheDocument()
    // 发送方为空时回退显示“麦麦”，并带“已发送”徽章
    expect(screen.getByText('麦麦')).toBeInTheDocument()
    expect(screen.getByText('已发送')).toBeInTheDocument()
    expect(screen.getByText('收到！')).toBeInTheDocument()
    // 空回复预览显示回退文案，且不渲染消息 ID
    expect(screen.getByText('回复 未知用户')).toBeInTheDocument()
    expect(screen.getByText('原消息已无法访问')).toBeInTheDocument()
    // 无内容无媒体时显示空消息占位
    expect(screen.getByText('[空消息]')).toBeInTheDocument()
  })

  it('媒体消息可在识别文本与原文件之间切换，无原文件的媒体不可切换', async () => {
    const user = userEvent.setup()
    const media: MaisakaMessageMedia[] = [
      { kind: 'emoji', hash: 'h1', text: '滑稽表情', url: '', data_url: 'data:image/png;base64,abc' },
      { kind: 'image', hash: 'h2', text: '', url: '' },
    ]
    setupMonitorState({
      timeline: [makeEntry('message.ingested', makeIngested({ content: '', media }))],
    })
    render(<MaisakaMonitor />)

    // 有媒体时不显示空消息占位；无识别文本的图片显示类型占位
    expect(screen.queryByText('[空消息]')).not.toBeInTheDocument()
    expect(screen.getByText('滑稽表情')).toBeInTheDocument()
    expect(screen.getByText('[图片]')).toBeInTheDocument()

    // 点击表情包切换为原文件图片
    const emojiButton = screen.getByText('滑稽表情').closest('button')
    if (!emojiButton) throw new Error('未找到表情包媒体按钮')
    await user.click(emojiButton)
    const image = screen.getByAltText('表情包原文件')
    expect(image).toHaveAttribute('src', 'data:image/png;base64,abc')

    // 再次点击切回识别文本
    await user.click(image)
    expect(screen.queryByAltText('表情包原文件')).not.toBeInTheDocument()
    expect(screen.getByText('滑稽表情')).toBeInTheDocument()

    // 没有 url/data_url 的媒体点击后保持文本态
    const placeholderButton = screen.getByText('[图片]').closest('button')
    if (!placeholderButton) throw new Error('未找到图片媒体按钮')
    await user.click(placeholderButton)
    expect(screen.queryByAltText('图片原文件')).not.toBeInTheDocument()
  })

  it('反应门卡片按动作渲染徽章与耗时', () => {
    const timeline = [
      makeEntry('timing_gate.result', makeTimingGate({ cycle_id: 1, action: 'continue', duration_ms: 500 })),
      makeEntry('timing_gate.result', makeTimingGate({ cycle_id: 2, action: 'wait', duration_ms: 1234 })),
      makeEntry(
        'timing_gate.result',
        makeTimingGate({ cycle_id: 3, action: 'no_action', content: '现在不适合回复', duration_ms: 80 })
      ),
    ]
    setupMonitorState({ timeline })
    render(<MaisakaMonitor />)

    expect(screen.getAllByText('反应')).toHaveLength(3)
    expect(screen.getAllByText('react')).toHaveLength(3)
    expect(screen.getByText('继续执行')).toBeInTheDocument()
    expect(screen.getByText('等待')).toBeInTheDocument()
    expect(screen.getByText('不回复')).toBeInTheDocument()
    // 耗时格式化：小于 1s 用毫秒，超过 1s 保留两位小数
    expect(screen.getByText('500ms')).toBeInTheDocument()
    expect(screen.getByText('1.23s')).toBeInTheDocument()
    expect(screen.getByText('现在不适合回复')).toBeInTheDocument()
  })

  it('规划器思考卡片展示耗时、tokens 与工具调用来源徽章', () => {
    setupMonitorState({
      timeline: [
        makeEntry(
          'planner.response',
          makePlannerResponse({
            content: '需要先查一下资料',
            tool_calls: [
              { id: 't1', name: 'send_message', source: 'reasoning' },
              { id: 't2', name: 'web_search', source: 'response' },
              { id: 't3', name: 'noop', source: '', source_label: '自定义来源' },
            ],
          })
        ),
      ],
    })
    render(<MaisakaMonitor />)

    expect(screen.getByText('规划器思考')).toBeInTheDocument()
    expect(screen.getByText('2.00s')).toBeInTheDocument()
    expect(screen.getByText('100+28 tokens')).toBeInTheDocument()
    expect(screen.getByText('需要先查一下资料')).toBeInTheDocument()
    expect(screen.getByText('send_message')).toBeInTheDocument()
    // 来源标签：reasoning/response 映射为固定文案，其余回退 source_label
    expect(screen.getByText('推理中调用')).toBeInTheDocument()
    expect(screen.getByText('正文调用')).toBeInTheDocument()
    expect(screen.getByText('自定义来源')).toBeInTheDocument()
  })

  it('planner.finalized 渲染 Planner 卡片、请求徽章与工具执行结果', () => {
    setupMonitorState({
      timeline: [
        makeEntry(
          'planner.finalized',
          makeFinalized({
            planner: makePlannerBlock({
              content: '决定回复用户',
              duration_ms: 1500,
              prompt_tokens: 10,
              completion_tokens: 5,
            }),
            request: { messages: [], selected_history_count: 3, tool_count: 7 },
            tools: [
              makeToolResult({
                tool_name: 'send_message',
                tool_args: { text: 'hi' },
                summary: '已发送消息',
                duration_ms: 300,
              }),
              makeToolResult({
                tool_call_id: 'tc-2',
                tool_name: 'web_search',
                success: false,
                duration_ms: 0,
                summary: '',
              }),
            ],
          })
        ),
      ],
    })
    render(<MaisakaMonitor />)

    expect(screen.getByText('Planner')).toBeInTheDocument()
    expect(screen.getByText('决定回复用户')).toBeInTheDocument()
    expect(screen.getByText('1.50s')).toBeInTheDocument()
    expect(screen.getByText('上下文 3 条 / 可用工具 7')).toBeInTheDocument()
    expect(screen.getByText('10+5 tokens')).toBeInTheDocument()

    // 工具执行结果卡片
    expect(screen.getByText('使用工具')).toBeInTheDocument()
    expect(screen.getByText('2 个')).toBeInTheDocument()
    expect(screen.getByText('send_message')).toBeInTheDocument()
    expect(screen.getByText('web_search')).toBeInTheDocument()
    expect(screen.getByText('执行成功')).toBeInTheDocument()
    expect(screen.getByText('执行失败')).toBeInTheDocument()
    expect(screen.getByText('300ms')).toBeInTheDocument()
    // 参数内联块与完整 JSON 折叠入口
    expect(screen.getByText('text')).toBeInTheDocument()
    expect(screen.getByText('hi')).toBeInTheDocument()
    expect(screen.getByText('JSON')).toBeInTheDocument()
    // 结果摘要：有摘要显示原文，空摘要显示占位
    expect(screen.getByText('已发送消息')).toBeInTheDocument()
    expect(screen.getByText('未返回结果摘要。')).toBeInTheDocument()
    expect(screen.getByText('#1')).toBeInTheDocument()
    expect(screen.getByText('#2')).toBeInTheDocument()
  })

  it('planner.finalized 仅 finish 工具时提示回合结束，混合工具时内联提示', () => {
    const timeline = [
      makeEntry(
        'planner.finalized',
        makeFinalized({ cycle_id: 1, tools: [makeToolResult({ tool_name: 'finish', summary: '' })] })
      ),
      makeEntry(
        'planner.finalized',
        makeFinalized({
          cycle_id: 2,
          tools: [
            makeToolResult({ tool_call_id: 'tc-f', tool_name: 'Finish' }),
            makeToolResult({ tool_call_id: 'tc-r', tool_name: 'web_search', summary: '找到了结果' }),
          ],
        })
      ),
    ]
    setupMonitorState({ timeline })
    render(<MaisakaMonitor />)

    // 两张卡都提示回合结束（finish 工具名大小写不敏感）
    expect(screen.getAllByText('本轮思考暂时结束')).toHaveLength(2)
    expect(screen.getAllByText('等待新的消息。')).toHaveLength(2)
    // 只有混合工具的卡片渲染“使用工具”，且计数只统计非 finish 工具
    expect(screen.getByText('使用工具')).toBeInTheDocument()
    expect(screen.getByText('1 个')).toBeInTheDocument()
    expect(screen.getByText('web_search')).toBeInTheDocument()
    expect(screen.getByText('找到了结果')).toBeInTheDocument()
  })

  it('planner.finalized 无执行结果时回退展示 tool_calls，空文本给出占位', () => {
    setupMonitorState({
      timeline: [
        makeEntry(
          'planner.finalized',
          makeFinalized({
            planner: makePlannerBlock({
              content: '',
              tool_calls: [{ id: 'a', name: 'search_web', source: 'response' }],
            }),
            tools: [],
          })
        ),
      ],
    })
    render(<MaisakaMonitor />)

    expect(screen.getByText('planner 本轮没有文本内容')).toBeInTheDocument()
    expect(screen.getByText('使用工具')).toBeInTheDocument()
    expect(screen.getByText('search_web')).toBeInTheDocument()
    // 回退条目默认视为执行成功且无耗时
    expect(screen.getByText('执行成功')).toBeInTheDocument()
    expect(screen.getByText('正文调用')).toBeInTheDocument()
    expect(screen.getByText('未返回结果摘要。')).toBeInTheDocument()
  })

  it('被打断的 planner 渲染打断卡片（显式标记与启发式识别）', () => {
    const timeline = [
      makeEntry(
        'planner.finalized',
        makeFinalized({
          cycle_id: 7,
          interrupted: true,
          planner: makePlannerBlock({
            content: '',
            prompt_tokens: 0,
            completion_tokens: 0,
            duration_ms: 800,
          }),
        })
      ),
      // 未带 interrupted 标记，但内容以 "Planner " 开头且 tokens/工具全空 → 启发式判定为被打断
      makeEntry(
        'planner.finalized',
        makeFinalized({
          cycle_id: 8,
          planner: makePlannerBlock({
            content: 'Planner 已被打断',
            prompt_tokens: 0,
            completion_tokens: 0,
            duration_ms: 0,
          }),
        })
      ),
    ]
    setupMonitorState({ timeline })
    render(<MaisakaMonitor />)

    expect(screen.getAllByText('Planner 被新消息打断')).toHaveLength(2)
    expect(screen.getByText('#7')).toBeInTheDocument()
    expect(screen.getByText('#8')).toBeInTheDocument()
    // 空内容回退默认文案；有内容显示原文
    expect(screen.getByText('收到新消息，已停止当前思考并准备重新决策。')).toBeInTheDocument()
    expect(screen.getByText('Planner 已被打断')).toBeInTheDocument()
    // duration > 0 时展示耗时
    expect(screen.getByText('800ms')).toBeInTheDocument()
  })

  it('no_action 循环隐藏同循环 planner 事件，被打断与其他循环不受影响', () => {
    const timeline = [
      makeEntry(
        'timing_gate.result',
        makeTimingGate({ cycle_id: 5, action: 'no_action', content: '现在不适合回复' })
      ),
      makeEntry('planner.response', makePlannerResponse({ cycle_id: 5, content: '不应显示的思考' })),
      makeEntry(
        'planner.finalized',
        makeFinalized({ cycle_id: 5, planner: makePlannerBlock({ content: '不应显示的决策' }) })
      ),
      makeEntry('planner.finalized', makeFinalized({ cycle_id: 5, interrupted: true })),
      // 事件自带 no_action 门禁结果 → 保留条目但渲染为空
      makeEntry(
        'planner.finalized',
        makeFinalized({
          cycle_id: 7,
          planner: makePlannerBlock({ content: '门禁判定不回复的决策' }),
          timing_gate: {
            request: null,
            result: {
              action: 'no_action',
              content: null,
              tool_calls: [],
              tool_results: [],
              prompt_tokens: 0,
              completion_tokens: 0,
              total_tokens: 0,
              duration_ms: 0,
            },
          },
        })
      ),
      makeEntry('planner.response', makePlannerResponse({ cycle_id: 6, content: '正常展示的思考' })),
      // 不支持展示的事件类型直接从可见时间线剔除
      makeEntry('session.start', {
        session_id: 's1',
        session_name: '某群',
        timestamp: nowSec(),
      } satisfies SessionStartEvent),
    ]
    setupMonitorState({ timeline })
    const { container } = render(<MaisakaMonitor />)

    expect(screen.getByText('不回复')).toBeInTheDocument()
    expect(screen.getByText('正常展示的思考')).toBeInTheDocument()
    expect(screen.getByText('Planner 被新消息打断')).toBeInTheDocument()
    expect(screen.queryByText('不应显示的思考')).not.toBeInTheDocument()
    expect(screen.queryByText('不应显示的决策')).not.toBeInTheDocument()
    expect(screen.queryByText('门禁判定不回复的决策')).not.toBeInTheDocument()
    // 可见条目 = 门禁结果 + 被打断卡片 + 空渲染的 no_action finalized + 循环 6 的思考
    expect(container.querySelectorAll('[data-index]')).toHaveLength(4)
  })

  it('工具执行卡片展示参数、结果摘要与折叠展开', async () => {
    const user = userEvent.setup()
    const longSummary = ['结果1', '结果2', '结果3', '结果4', '结果5'].join('\n')
    const timeline = [
      makeEntry(
        'tool.execution',
        makeToolExecution({
          tool_name: 'web_search',
          tool_args: { query: '天气' },
          result_summary: longSummary,
          duration_ms: 42,
        })
      ),
      makeEntry(
        'tool.execution',
        makeToolExecution({
          tool_name: 'bad_tool',
          tool_args: {},
          result_summary: '',
          success: false,
          duration_ms: 1000,
        })
      ),
    ]
    setupMonitorState({ timeline })
    render(<MaisakaMonitor />)

    expect(screen.getByText('web_search')).toBeInTheDocument()
    expect(screen.getByText('bad_tool')).toBeInTheDocument()
    expect(screen.getByText('42ms')).toBeInTheDocument()
    expect(screen.getByText('1.00s')).toBeInTheDocument()
    // 参数以缩进 JSON 展示（断言按空白归一化后的文本）
    expect(screen.getByText('{ "query": "天气" }')).toBeInTheDocument()

    // 摘要超过 3 行折叠，仅显示前 3 行
    expect(screen.getByText('结果1 结果2 结果3')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '展开全部 (5 行)' }))
    expect(screen.getByText('结果1 结果2 结果3 结果4 结果5')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '收起' }))
    expect(screen.getByText('结果1 结果2 结果3')).toBeInTheDocument()
  })

  it('回复器响应卡片展示状态、思考过程与 token 统计', () => {
    const timeline = [
      makeEntry(
        'replier.response',
        makeReplier({
          content: '好的，我来帮你',
          reasoning: '用户需要帮助',
          model_name: 'gpt-x',
          prompt_tokens: 12,
          completion_tokens: 34,
          total_tokens: 46,
          duration_ms: 800,
        })
      ),
      makeEntry(
        'replier.response',
        makeReplier({ content: '', reasoning: '', success: false, duration_ms: 100 })
      ),
    ]
    setupMonitorState({ timeline })
    render(<MaisakaMonitor />)

    expect(screen.getAllByText('回复器响应')).toHaveLength(2)
    expect(screen.getByText('成功')).toBeInTheDocument()
    expect(screen.getByText('失败')).toBeInTheDocument()
    expect(screen.getByText('好的，我来帮你')).toBeInTheDocument()
    expect(screen.getByText('思考过程')).toBeInTheDocument()
    expect(screen.getByText('用户需要帮助')).toBeInTheDocument()
    expect(screen.getByText('800ms')).toBeInTheDocument()
    // token 统计仅在有 token 时展示
    expect(screen.getByText('模型: gpt-x')).toBeInTheDocument()
    expect(screen.getByText('输入: 12')).toBeInTheDocument()
    expect(screen.getByText('输出: 34')).toBeInTheDocument()
    expect(screen.getByText('总计: 46')).toBeInTheDocument()
  })
})

describe('推理记录跳转', () => {
  it('Planner 卡片推理按钮解析 prompt_html_uri 并携带返回地址跳转', async () => {
    const user = userEvent.setup()
    setupMonitorState({
      timeline: [
        makeEntry(
          'planner.finalized',
          makeFinalized({
            planner: makePlannerBlock({
              prompt_html_uri: '/api/webui/config/maisaka-prompt-preview?path=planner/sess-1/rec-01.html',
            }),
          })
        ),
      ],
    })
    render(<MaisakaMonitor />)

    await user.click(screen.getByRole('button', { name: '推理' }))

    const expectedParams = new URLSearchParams({
      stage: 'planner',
      session: 'sess-1',
      stem: 'rec-01',
      returnTo: `${window.location.pathname}${window.location.search}${window.location.hash}`,
    })
    expect(routerMocks.navigate).toHaveBeenCalledTimes(1)
    expect(routerMocks.navigate).toHaveBeenCalledWith({
      to: `/reasoning-process?${expectedParams.toString()}`,
    })
  })

  it('工具结果的推理按钮使用工具自身的 JSON 记录地址', async () => {
    const user = userEvent.setup()
    setupMonitorState({
      timeline: [
        makeEntry(
          'planner.finalized',
          makeFinalized({
            tools: [
              makeToolResult({
                prompt_html_uri: '/api/webui/config/maisaka-prompt-preview?path=tool/sess-2/rec-02.json',
              }),
            ],
          })
        ),
      ],
    })
    render(<MaisakaMonitor />)

    // planner 块没有记录地址，唯一的推理按钮来自工具结果行
    await user.click(screen.getByRole('button', { name: '推理' }))

    const expectedParams = new URLSearchParams({
      stage: 'tool',
      session: 'sess-2',
      stem: 'rec-02',
      returnTo: `${window.location.pathname}${window.location.search}${window.location.hash}`,
    })
    expect(routerMocks.navigate).toHaveBeenCalledWith({
      to: `/reasoning-process?${expectedParams.toString()}`,
    })
  })

  it('无法解析的 prompt_html_uri 不渲染推理按钮', () => {
    const timeline = [
      // 跨域地址
      makeEntry(
        'planner.finalized',
        makeFinalized({
          cycle_id: 1,
          planner: makePlannerBlock({
            prompt_html_uri:
              'https://evil.example.com/api/webui/config/maisaka-prompt-preview?path=planner/s/x.html',
          }),
        })
      ),
      // 路径段不足（缺少会话层级）
      makeEntry(
        'planner.finalized',
        makeFinalized({
          cycle_id: 2,
          planner: makePlannerBlock({
            prompt_html_uri: '/api/webui/config/maisaka-prompt-preview?path=planner/x.html',
          }),
        })
      ),
      // 不支持的文件后缀
      makeEntry(
        'planner.finalized',
        makeFinalized({
          cycle_id: 3,
          tools: [
            makeToolResult({
              prompt_html_uri: '/api/webui/config/maisaka-prompt-preview?path=tool/s/x.txt',
            }),
          ],
        })
      ),
    ]
    setupMonitorState({ timeline })
    render(<MaisakaMonitor />)

    expect(screen.queryAllByRole('button', { name: '推理' })).toHaveLength(0)
  })
})
