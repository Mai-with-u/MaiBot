/**
 * 回复器监控组件测试
 *
 * 覆盖：总览统计与聊天卡片渲染、空态、进入聊天日志视图、搜索与清除、
 * 页码跳转、详情弹窗（成功/失败）、返回总览、refreshKey 失效刷新与 10 秒轮询。
 */
import type { ReactNode } from 'react'

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ReplierMonitor } from './replier-monitor'
import type { PaginatedReplyLogs, ReplierOverview, ReplyLogDetail } from '@/lib/planner-api'
import type { ChatInfo } from '@/types/expression'
import * as expressionApi from '@/lib/expression-api'
import * as plannerApi from '@/lib/planner-api'

// 拦截回复器 API，避免真实网络请求
vi.mock('@/lib/planner-api', () => ({
  getReplierOverview: vi.fn(),
  getReplyChatLogs: vi.fn(),
  getReplyLogDetail: vi.fn(),
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

/** 构造回复器总览数据 */
function makeOverview(overrides: Partial<ReplierOverview> = {}): ReplierOverview {
  const nowSec = Date.now() / 1000
  return {
    total_chats: 3,
    total_replies: 34,
    chats: [
      { chat_id: 'chat-1', reply_count: 12, latest_timestamp: nowSec - 30, latest_filename: 'r1.json' },
      { chat_id: 'chat-raw', reply_count: 22, latest_timestamp: nowSec - 7200, latest_filename: 'r2.json' },
    ],
    ...overrides,
  }
}

/** 构造分页回复日志列表（total=45、page_size=20，共 3 页） */
function makeLogsPage(): PaginatedReplyLogs {
  return {
    data: [
      {
        chat_id: 'chat-1',
        timestamp: 1700000000,
        filename: 'log-a.json',
        model: 'gpt-4o',
        success: true,
        llm_ms: 300,
        overall_ms: 456.7,
        output_preview: '预览：你好呀',
      },
      {
        chat_id: 'chat-1',
        timestamp: 1700000100,
        filename: 'log-b.json',
        model: 'deepseek',
        success: false,
        llm_ms: 100,
        overall_ms: 200.4,
        output_preview: '',
      },
    ],
    total: 45,
    page: 1,
    page_size: 20,
    chat_id: 'chat-1',
  }
}

/** 构造回复日志详情（带错误信息的失败样本，可覆盖详情弹窗大部分分支） */
function makeDetail(): ReplyLogDetail {
  return {
    type: 'reply',
    chat_id: 'chat-1',
    timestamp: 1700000000,
    prompt: '完整提示词内容',
    output: '你好呀，我是麦麦',
    processed_output: ['你好呀（处理后）'],
    model: 'gpt-4o-mini-detail',
    reasoning: '用户在打招呼，需要友好回应',
    think_level: 2,
    timing: {
      prompt_ms: 12.3,
      overall_ms: 456.7,
      timing_logs: ['构建提示词耗时 12ms'],
      llm_ms: 300.1,
      almost_zero: 'memory_query',
    },
    error: '模型偶发超时',
    success: false,
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
  vi.mocked(plannerApi.getReplierOverview).mockResolvedValue(makeOverview())
  vi.mocked(plannerApi.getReplyChatLogs).mockResolvedValue(makeLogsPage())
  vi.mocked(plannerApi.getReplyLogDetail).mockResolvedValue(makeDetail())
})

/** 渲染组件并等待总览加载完成 */
async function renderOverview(props: { autoRefresh?: boolean; refreshKey?: number } = {}) {
  const result = render(
    <ReplierMonitor autoRefresh={props.autoRefresh ?? false} refreshKey={props.refreshKey ?? 0} />,
    { wrapper: makeWrapper() }
  )
  await screen.findByText('34')
  return result
}

/** 从总览点进 chat-1 的聊天日志视图并等待列表加载 */
async function enterChatLogs(user: ReturnType<typeof userEvent.setup>) {
  await renderOverview()
  await user.click(screen.getByRole('button', { name: /测试群/ }))
  await screen.findByText('预览：你好呀')
}

describe('ReplierMonitor 总览视图', () => {
  it('加载后展示统计数字、聊天卡片与名称映射', async () => {
    await renderOverview()

    expect(plannerApi.getReplierOverview).toHaveBeenCalledTimes(1)
    // 统计卡片数值
    expect(screen.getByText('聊天数量')).toBeInTheDocument()
    expect(screen.getByText('3')).toBeInTheDocument()
    expect(screen.getByText('回复总数')).toBeInTheDocument()
    expect(screen.getByText('34')).toBeInTheDocument()
    // 命中映射显示聊天名称，未命中回退为 chat_id
    expect(screen.getByText('测试群')).toBeInTheDocument()
    expect(screen.getByText('chat-raw')).toBeInTheDocument()
    // 每个聊天卡片的回复计数与相对时间
    expect(screen.getByText('12')).toBeInTheDocument()
    expect(screen.getByText('22')).toBeInTheDocument()
    expect(screen.getByText(/最后活动: 刚刚/)).toBeInTheDocument()
    expect(screen.getByText(/最后活动: 2 小时前/)).toBeInTheDocument()
  })

  it('聊天列表为空时显示空态文案', async () => {
    vi.mocked(plannerApi.getReplierOverview).mockResolvedValue(
      makeOverview({ total_chats: 0, total_replies: 0, chats: [] })
    )
    render(<ReplierMonitor autoRefresh={false} refreshKey={0} />, { wrapper: makeWrapper() })

    expect(await screen.findByText('暂无聊天记录')).toBeInTheDocument()
  })

  it('refreshKey 变化时使查询失效并重新拉取总览', async () => {
    const wrapper = makeWrapper()
    const { rerender } = render(<ReplierMonitor autoRefresh={false} refreshKey={0} />, { wrapper })
    await waitFor(() => expect(plannerApi.getReplierOverview).toHaveBeenCalledTimes(1))

    rerender(<ReplierMonitor autoRefresh={false} refreshKey={1} />)
    await waitFor(() => expect(plannerApi.getReplierOverview).toHaveBeenCalledTimes(2))
  })

  it('autoRefresh 开启时总览每 10 秒轮询一次', async () => {
    vi.useFakeTimers()
    render(<ReplierMonitor autoRefresh refreshKey={0} />, { wrapper: makeWrapper() })

    // 先刷掉初始请求的微任务
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })
    expect(plannerApi.getReplierOverview).toHaveBeenCalledTimes(1)

    // 推进 10 秒触发一轮轮询
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000)
    })
    expect(plannerApi.getReplierOverview).toHaveBeenCalledTimes(2)
  })

  it('autoRefresh 关闭时不轮询', async () => {
    vi.useFakeTimers()
    render(<ReplierMonitor autoRefresh={false} refreshKey={0} />, { wrapper: makeWrapper() })

    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })
    expect(plannerApi.getReplierOverview).toHaveBeenCalledTimes(1)

    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000)
    })
    expect(plannerApi.getReplierOverview).toHaveBeenCalledTimes(1)
  })
})

describe('ReplierMonitor 聊天日志视图', () => {
  it('点击聊天卡片进入日志列表并按默认分页拉取', async () => {
    const user = userEvent.setup()
    await enterChatLogs(user)

    expect(plannerApi.getReplyChatLogs).toHaveBeenCalledWith('chat-1', 1, 20, undefined)
    // 顶部聊天信息与总数
    expect(screen.getByText(/共 45 条回复记录/)).toBeInTheDocument()
    // 日志条目：预览、空输出兜底、模型/耗时/状态徽章
    expect(screen.getByText('预览：你好呀')).toBeInTheDocument()
    expect(screen.getByText('无输出内容')).toBeInTheDocument()
    expect(screen.getByText('gpt-4o')).toBeInTheDocument()
    expect(screen.getByText('deepseek')).toBeInTheDocument()
    expect(screen.getByText('成功')).toBeInTheDocument()
    expect(screen.getByText('失败')).toBeInTheDocument()
    expect(screen.getByText('457ms')).toBeInTheDocument()
    // 分页信息：45 条 / 每页 20 条 = 3 页
    expect(screen.getByText('共 45 条记录，第 1 / 3 页')).toBeInTheDocument()
  })

  it('搜索提示词触发带关键词的请求，清除后恢复', async () => {
    const user = userEvent.setup()
    await enterChatLogs(user)

    const searchInput = screen.getByPlaceholderText('搜索提示词内容...')
    await user.type(searchInput, '你好{Enter}')

    await waitFor(() =>
      expect(plannerApi.getReplyChatLogs).toHaveBeenCalledWith('chat-1', 1, 20, '你好')
    )
    expect(screen.getByText(/搜索关键词/)).toBeInTheDocument()

    // 点击「清除」回到无搜索状态并重新拉取
    await user.click(screen.getByRole('button', { name: '清除' }))
    await waitFor(() => expect(screen.queryByText(/搜索关键词/)).not.toBeInTheDocument())
    expect(searchInput).toHaveValue('')
    await waitFor(() => expect(plannerApi.getReplyChatLogs).toHaveBeenCalledTimes(3))
    expect(plannerApi.getReplyChatLogs).toHaveBeenLastCalledWith('chat-1', 1, 20, undefined)
  })

  it('页码跳转触发对应页请求，越界跳转被忽略', async () => {
    const user = userEvent.setup()
    await enterChatLogs(user)

    const jumpInput = screen.getByPlaceholderText('跳转')
    await user.type(jumpInput, '2')
    await user.click(screen.getByRole('button', { name: '跳转' }))

    await waitFor(() =>
      expect(plannerApi.getReplyChatLogs).toHaveBeenCalledWith('chat-1', 2, 20, undefined)
    )
    expect(await screen.findByText('共 45 条记录，第 2 / 3 页')).toBeInTheDocument()

    // 超过总页数（3 页）的跳转不生效
    await user.type(jumpInput, '99')
    await user.click(screen.getByRole('button', { name: '跳转' }))
    expect(plannerApi.getReplyChatLogs).not.toHaveBeenCalledWith('chat-1', 99, 20, undefined)
  })

  it('日志为空时显示空态文案', async () => {
    vi.mocked(plannerApi.getReplyChatLogs).mockResolvedValue({
      data: [],
      total: 0,
      page: 1,
      page_size: 20,
      chat_id: 'chat-1',
    })
    const user = userEvent.setup()
    await renderOverview()
    await user.click(screen.getByRole('button', { name: /测试群/ }))

    expect(await screen.findByText('暂无回复记录')).toBeInTheDocument()
  })

  it('点击返回按钮回到总览视图', async () => {
    const user = userEvent.setup()
    await enterChatLogs(user)

    await user.click(screen.getByRole('button', { name: /返回聊天列表/ }))

    expect(screen.getByText('点击查看该聊天的所有回复记录')).toBeInTheDocument()
    expect(screen.queryByText(/返回聊天列表/)).not.toBeInTheDocument()
  })
})

describe('ReplierMonitor 详情弹窗', () => {
  it('点击日志条目加载并展示回复详情', async () => {
    const user = userEvent.setup()
    await enterChatLogs(user)

    await user.click(screen.getByText('预览：你好呀'))

    expect(plannerApi.getReplyLogDetail).toHaveBeenCalledWith('chat-1', 'log-a.json')
    expect(await screen.findByText('回复生成详情')).toBeInTheDocument()
    // 基本信息与模型
    expect(await screen.findByText('Level 2')).toBeInTheDocument()
    expect(screen.getByText('gpt-4o-mini-detail')).toBeInTheDocument()
    // 性能统计（toFixed(2) 格式）与耗时日志
    expect(screen.getByText('12.30ms')).toBeInTheDocument()
    expect(screen.getByText('300.10ms')).toBeInTheDocument()
    expect(screen.getByText('456.70ms')).toBeInTheDocument()
    expect(screen.getByText('构建提示词耗时 12ms')).toBeInTheDocument()
    expect(screen.getByText(/近乎零耗时/)).toBeInTheDocument()
    // 回复输出、处理后输出与推理过程
    expect(screen.getByText('你好呀，我是麦麦')).toBeInTheDocument()
    expect(screen.getByText('你好呀（处理后）')).toBeInTheDocument()
    expect(screen.getByText('用户在打招呼，需要友好回应')).toBeInTheDocument()
    // 错误信息与完整提示词折叠区
    expect(screen.getByText('错误信息')).toBeInTheDocument()
    expect(screen.getByText('模型偶发超时')).toBeInTheDocument()
    expect(screen.getByText('完整提示词内容')).toBeInTheDocument()
  })

  it('详情加载失败时记录错误日志并展示无数据', async () => {
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    const loadError = new Error('网络错误')
    vi.mocked(plannerApi.getReplyLogDetail).mockRejectedValue(loadError)

    const user = userEvent.setup()
    await enterChatLogs(user)
    await user.click(screen.getByText('预览：你好呀'))

    await waitFor(() => expect(errorSpy).toHaveBeenCalledWith('加载回复详情失败:', loadError))
    expect(await screen.findByText('无数据')).toBeInTheDocument()
  })
})
