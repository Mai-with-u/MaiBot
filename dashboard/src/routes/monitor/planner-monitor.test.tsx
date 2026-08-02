/**
 * 规划器监控组件测试
 *
 * 覆盖：总览统计与聊天卡片渲染、空态、进入聊天日志视图、搜索、
 * 页码跳转、详情弹窗（动作列表/性能统计/失败路径）、返回总览、
 * refreshKey 失效刷新与 10 秒轮询。
 */
import type { ReactNode } from 'react'

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { PlannerMonitor } from './planner-monitor'
import type { PaginatedChatLogs, PlanLogDetail, PlannerOverview } from '@/lib/planner-api'
import type { ChatInfo } from '@/types/expression'
import * as expressionApi from '@/lib/expression-api'
import * as plannerApi from '@/lib/planner-api'

// 拦截规划器 API，避免真实网络请求
vi.mock('@/lib/planner-api', () => ({
  getPlannerOverview: vi.fn(),
  getChatLogs: vi.fn(),
  getLogDetail: vi.fn(),
}))

// useChatNameMap 内部通过表达方式 API 拉取聊天列表，同样打桩
vi.mock('@/lib/expression-api', () => ({
  getChatList: vi.fn(),
}))

afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

/** 构造一个最小可用的聊天信息对象（用于聊天名称映射） */
function makeChat(overrides: Partial<ChatInfo> = {}): ChatInfo {
  return {
    chat_id: 'chat-1',
    chat_name: '测试群',
    platform: 'qq',
    is_group: true,
    use_expression: true,
    enable_learning: true,
    ...overrides,
  }
}

/** 构造规划器总览数据 */
function makeOverview(overrides: Partial<PlannerOverview> = {}): PlannerOverview {
  const nowSec = Date.now() / 1000
  return {
    total_chats: 3,
    total_plans: 51,
    chats: [
      { chat_id: 'chat-1', plan_count: 7, latest_timestamp: nowSec - 30, latest_filename: 'p1.json' },
      { chat_id: 'chat-raw', plan_count: 9, latest_timestamp: nowSec - 7200, latest_filename: 'p2.json' },
    ],
    ...overrides,
  }
}

/** 构造分页计划日志列表（total=45、page_size=20，共 3 页） */
function makeLogsPage(): PaginatedChatLogs {
  return {
    data: [
      {
        chat_id: 'chat-1',
        timestamp: 1700000000,
        filename: 'plan-a.json',
        action_count: 2,
        action_types: ['reply', 'no_action'],
        total_plan_ms: 300.6,
        llm_duration_ms: 200,
        reasoning_preview: '预览：用户在提问',
      },
      {
        chat_id: 'chat-1',
        timestamp: 1700000100,
        filename: 'plan-b.json',
        action_count: 0,
        action_types: [],
        total_plan_ms: 100.2,
        llm_duration_ms: 50,
        reasoning_preview: '',
      },
    ],
    total: 45,
    page: 1,
    page_size: 20,
    chat_id: 'chat-1',
  }
}

/** 构造计划日志详情（一个动作 + 完整 timing，覆盖详情弹窗主要分支） */
function makeDetail(): PlanLogDetail {
  return {
    type: 'normal_planner',
    chat_id: 'chat-1',
    timestamp: 1700000000,
    prompt: '完整提示词内容',
    reasoning: '用户在提问，需要认真回复',
    raw_output: '{"action":"reply"}',
    actions: [
      {
        action_type: 'emoji',
        reasoning: '需要回应用户',
        action_message: '这是动作消息',
        action_data: { text: '你好' },
        action_reasoning: '选择表情动作',
      },
    ],
    timing: {
      prompt_build_ms: 10.5,
      llm_duration_ms: 200.25,
      total_plan_ms: 300.75,
      loop_start_time: 0,
    },
    extra: null,
  }
}

/** 每个测试用独立的 QueryClient，关闭重试避免错误路径超时 */
function makeWrapper() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
}

beforeEach(() => {
  vi.mocked(expressionApi.getChatList).mockResolvedValue([makeChat()])
  vi.mocked(plannerApi.getPlannerOverview).mockResolvedValue(makeOverview())
  vi.mocked(plannerApi.getChatLogs).mockResolvedValue(makeLogsPage())
  vi.mocked(plannerApi.getLogDetail).mockResolvedValue(makeDetail())
})

/** 渲染组件并等待总览加载完成 */
async function renderOverview(props: { autoRefresh?: boolean; refreshKey?: number } = {}) {
  const result = render(
    <PlannerMonitor autoRefresh={props.autoRefresh ?? false} refreshKey={props.refreshKey ?? 0} />,
    { wrapper: makeWrapper() }
  )
  await screen.findByText('51')
  return result
}

/** 从总览点进 chat-1 的聊天日志视图并等待列表加载 */
async function enterChatLogs(user: ReturnType<typeof userEvent.setup>) {
  await renderOverview()
  await user.click(screen.getByRole('button', { name: /测试群/ }))
  await screen.findByText('预览：用户在提问')
}

describe('PlannerMonitor 总览视图', () => {
  it('加载后展示统计数字、聊天卡片与名称映射', async () => {
    await renderOverview()

    expect(plannerApi.getPlannerOverview).toHaveBeenCalledTimes(1)
    // 统计卡片数值
    expect(screen.getByText('聊天数量')).toBeInTheDocument()
    expect(screen.getByText('3')).toBeInTheDocument()
    expect(screen.getByText('计划总数')).toBeInTheDocument()
    expect(screen.getByText('51')).toBeInTheDocument()
    // 命中映射显示聊天名称，未命中回退为 chat_id
    expect(screen.getByText('测试群')).toBeInTheDocument()
    expect(screen.getByText('chat-raw')).toBeInTheDocument()
    // 每个聊天卡片的计划计数与相对时间
    expect(screen.getByText('7')).toBeInTheDocument()
    expect(screen.getByText('9')).toBeInTheDocument()
    expect(screen.getByText(/最后活动: 刚刚/)).toBeInTheDocument()
    expect(screen.getByText(/最后活动: 2 小时前/)).toBeInTheDocument()
  })

  it('聊天列表为空时显示空态文案', async () => {
    vi.mocked(plannerApi.getPlannerOverview).mockResolvedValue(
      makeOverview({ total_chats: 0, total_plans: 0, chats: [] })
    )
    render(<PlannerMonitor autoRefresh={false} refreshKey={0} />, { wrapper: makeWrapper() })

    expect(await screen.findByText('暂无聊天记录')).toBeInTheDocument()
  })

  it('refreshKey 变化时使查询失效并重新拉取总览', async () => {
    const wrapper = makeWrapper()
    const { rerender } = render(<PlannerMonitor autoRefresh={false} refreshKey={0} />, { wrapper })
    await waitFor(() => expect(plannerApi.getPlannerOverview).toHaveBeenCalledTimes(1))

    rerender(<PlannerMonitor autoRefresh={false} refreshKey={1} />)
    await waitFor(() => expect(plannerApi.getPlannerOverview).toHaveBeenCalledTimes(2))
  })

  it('autoRefresh 开启时总览每 10 秒轮询一次，关闭时不轮询', async () => {
    vi.useFakeTimers()
    const { unmount } = render(<PlannerMonitor autoRefresh refreshKey={0} />, {
      wrapper: makeWrapper(),
    })

    // 先刷掉初始请求的微任务
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })
    expect(plannerApi.getPlannerOverview).toHaveBeenCalledTimes(1)

    // 推进 10 秒触发一轮轮询
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000)
    })
    expect(plannerApi.getPlannerOverview).toHaveBeenCalledTimes(2)
    unmount()

    // 关闭 autoRefresh 后重新挂载，推进 30 秒也不应再拉取
    vi.mocked(plannerApi.getPlannerOverview).mockClear()
    render(<PlannerMonitor autoRefresh={false} refreshKey={0} />, { wrapper: makeWrapper() })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })
    expect(plannerApi.getPlannerOverview).toHaveBeenCalledTimes(1)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000)
    })
    expect(plannerApi.getPlannerOverview).toHaveBeenCalledTimes(1)
  })
})

describe('PlannerMonitor 聊天日志视图', () => {
  it('点击聊天卡片进入日志列表并按默认分页拉取', async () => {
    const user = userEvent.setup()
    await enterChatLogs(user)

    expect(plannerApi.getChatLogs).toHaveBeenCalledWith('chat-1', 1, 20, undefined)
    // 顶部聊天信息与总数
    expect(screen.getByText(/共 45 条计划记录/)).toBeInTheDocument()
    // 日志条目：动作数量、动作类型徽章、耗时与推理预览兜底
    expect(screen.getByText('2 个动作')).toBeInTheDocument()
    expect(screen.getByText('0 个动作')).toBeInTheDocument()
    expect(screen.getByText('reply')).toBeInTheDocument()
    expect(screen.getByText('no_action')).toBeInTheDocument()
    expect(screen.getByText('301ms')).toBeInTheDocument()
    expect(screen.getByText('预览：用户在提问')).toBeInTheDocument()
    expect(screen.getByText('无推理内容')).toBeInTheDocument()
    // 分页信息：45 条 / 每页 20 条 = 3 页
    expect(screen.getByText('共 45 条记录，第 1 / 3 页')).toBeInTheDocument()
  })

  it('搜索提示词触发带关键词的请求', async () => {
    const user = userEvent.setup()
    await enterChatLogs(user)

    const searchInput = screen.getByPlaceholderText('搜索提示词内容...')
    await user.type(searchInput, '提问{Enter}')

    await waitFor(() =>
      expect(plannerApi.getChatLogs).toHaveBeenCalledWith('chat-1', 1, 20, '提问')
    )
    expect(screen.getByText(/搜索关键词/)).toBeInTheDocument()
  })

  it('页码跳转触发对应页请求，越界跳转被忽略', async () => {
    const user = userEvent.setup()
    await enterChatLogs(user)

    const jumpInput = screen.getByPlaceholderText('跳转')
    await user.type(jumpInput, '3')
    await user.click(screen.getByRole('button', { name: '跳转' }))

    await waitFor(() =>
      expect(plannerApi.getChatLogs).toHaveBeenCalledWith('chat-1', 3, 20, undefined)
    )
    expect(await screen.findByText('共 45 条记录，第 3 / 3 页')).toBeInTheDocument()

    // 超过总页数（3 页）的跳转不生效
    await user.type(jumpInput, '99')
    await user.click(screen.getByRole('button', { name: '跳转' }))
    expect(plannerApi.getChatLogs).not.toHaveBeenCalledWith('chat-1', 99, 20, undefined)
  })

  it('日志为空时显示空态文案', async () => {
    vi.mocked(plannerApi.getChatLogs).mockResolvedValue({
      data: [],
      total: 0,
      page: 1,
      page_size: 20,
      chat_id: 'chat-1',
    })
    const user = userEvent.setup()
    await renderOverview()
    await user.click(screen.getByRole('button', { name: /测试群/ }))

    expect(await screen.findByText('暂无计划记录')).toBeInTheDocument()
  })

  it('点击返回按钮回到总览视图', async () => {
    const user = userEvent.setup()
    await enterChatLogs(user)

    await user.click(screen.getByRole('button', { name: /返回聊天列表/ }))

    expect(screen.getByText('点击查看该聊天的所有计划记录')).toBeInTheDocument()
    expect(screen.queryByText(/返回聊天列表/)).not.toBeInTheDocument()
  })
})

describe('PlannerMonitor 详情弹窗', () => {
  it('点击日志条目加载并展示计划详情', async () => {
    const user = userEvent.setup()
    await enterChatLogs(user)

    await user.click(screen.getByText('预览：用户在提问'))

    expect(plannerApi.getLogDetail).toHaveBeenCalledWith('chat-1', 'plan-a.json')
    expect(await screen.findByText('计划执行详情')).toBeInTheDocument()
    // 基本信息：类型与动作数量
    expect(await screen.findByText('normal_planner')).toBeInTheDocument()
    expect(screen.getByText('1 个动作')).toBeInTheDocument()
    // 性能统计（toFixed(2) 格式）
    expect(screen.getByText('10.50ms')).toBeInTheDocument()
    expect(screen.getByText('200.25ms')).toBeInTheDocument()
    expect(screen.getByText('300.75ms')).toBeInTheDocument()
    // 推理过程
    expect(screen.getByText('用户在提问，需要认真回复')).toBeInTheDocument()
    // 执行动作卡片：编号、类型、推理依据、动作消息、动作数据 JSON、动作推理
    expect(screen.getByText('执行动作 (1)')).toBeInTheDocument()
    expect(screen.getByText('动作 1')).toBeInTheDocument()
    expect(screen.getByText('emoji')).toBeInTheDocument()
    expect(screen.getByText('需要回应用户')).toBeInTheDocument()
    expect(screen.getByText('这是动作消息')).toBeInTheDocument()
    expect(screen.getByText(/"text": "你好"/)).toBeInTheDocument()
    expect(screen.getByText('选择表情动作')).toBeInTheDocument()
    // 原始输出与完整提示词折叠区
    expect(screen.getByText('{"action":"reply"}')).toBeInTheDocument()
    expect(screen.getByText('完整提示词内容')).toBeInTheDocument()
  })

  it('详情加载失败时记录错误日志并展示无数据', async () => {
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    const loadError = new Error('网络错误')
    vi.mocked(plannerApi.getLogDetail).mockRejectedValue(loadError)

    const user = userEvent.setup()
    await enterChatLogs(user)
    await user.click(screen.getByText('预览：用户在提问'))

    await waitFor(() => expect(errorSpy).toHaveBeenCalledWith('加载计划详情失败:', loadError))
    expect(await screen.findByText('无数据')).toBeInTheDocument()
  })
})
