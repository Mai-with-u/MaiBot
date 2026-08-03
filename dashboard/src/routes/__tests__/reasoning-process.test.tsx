/**
 * ReasoningProcessPage 页面集成测试（特征化）。
 *
 * 近 3000 行巨页，按「类型总览 → 进入类型浏览 → 选中记录查看 → 重放」主链路
 * 做特征化覆盖：mock reasoning-process-api / config-api / 路由与 toast，
 * 验证分类分组、清空确认、过滤请求形状、内容加载与错误分支、returnTo 安全校验、
 * 重放面板的请求编排与结果展示。
 */
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ReasoningProcessPage } from '../reasoning-process'
import { getModelConfig } from '@/lib/config-api'
import {
  clearReasoningPromptStage,
  getReasoningPromptFile,
  getReasoningPromptHtmlUrl,
  listReasoningPromptFiles,
  listReasoningPromptStages,
  replayReasoningPrompt,
  type ReasoningPromptContentResponse,
  type ReasoningPromptFile,
  type ReasoningPromptListParams,
  type ReasoningPromptListResponse,
  type ReasoningPromptSessionInfo,
  type ReasoningPromptStageInfo,
  type ReasoningReplayResponse,
} from '@/lib/reasoning-process-api'

// 路由与 toast 都在工厂闭包里延迟取值，vi.hoisted 保证提升后可引用
const { navigateMock, toastMock } = vi.hoisted(() => ({
  navigateMock: vi.fn(),
  toastMock: vi.fn(),
}))

vi.mock('@tanstack/react-router', () => ({ useNavigate: () => navigateMock }))
vi.mock('@/hooks/use-toast', () => ({ useToast: () => ({ toast: toastMock }) }))
// 头像开关固定关闭，避免触发 resolveApiPath 的头像地址解析链路
vi.mock('@/lib/avatar-url', () => ({ useAvatarFetchEnabled: () => false }))
vi.mock('@/lib/config-api', () => ({ getModelConfig: vi.fn() }))
vi.mock('@/lib/reasoning-process-api', () => ({
  clearReasoningPromptStage: vi.fn(),
  getReasoningPromptFile: vi.fn(),
  getReasoningPromptHtmlUrl: vi.fn(),
  listReasoningPromptFiles: vi.fn(),
  listReasoningPromptStages: vi.fn(),
  replayReasoningPrompt: vi.fn(),
}))

// 覆盖五个分类：主流程、学习器、其余、LLM 请求、不再使用
const STAGE_INFOS: ReasoningPromptStageInfo[] = [
  { name: 'planner', session_count: 3, latest_modified_at: 1753500000 },
  { name: 'replyer', session_count: 2, latest_modified_at: 1753500100 },
  { name: 'expression_learner', session_count: 1, latest_modified_at: 1753500200 },
  { name: 'emotion', session_count: 4, latest_modified_at: 0 },
  { name: 'llm_error', session_count: 1, latest_modified_at: 1753500250 },
  { name: 'timing_gate', session_count: 1, latest_modified_at: 1753500300 },
]

const SESSION_INFO: ReasoningPromptSessionInfo = {
  name: 'sess-1',
  platform: 'qq',
  chat_type: 'group',
  target_id: '123456',
  resolved_session_id: 'resolved-1234567',
  display_name: '测试群聊',
  account_id: null,
  matched_current_account: false,
}

function makeFile(overrides: Partial<ReasoningPromptFile> = {}): ReasoningPromptFile {
  return {
    stage: 'planner',
    session_id: 'sess-1',
    resolved_session_id: 'resolved-1234567',
    session_display_name: null,
    platform: 'qq',
    chat_type: 'group',
    target_id: '123456',
    stem: 'rec-1',
    timestamp: 1753500000000,
    text_path: '/prompts/rec-1.txt',
    html_path: null,
    json_path: '/prompts/rec-1.json',
    output_preview: null,
    action_preview: '动作：回复',
    display_title: '打招呼回应',
    related_json_paths: ['/prompts/rec-1.json'],
    has_behavior_choice_insert: false,
    model_name: 'gpt-test',
    duration_ms: 1500,
    size: 2048,
    modified_at: 1753500000,
    ...overrides,
  }
}

// 主记录：同时有 txt 与结构化 json
const PLANNER_FILE = makeFile()
// 纯文本记录：display_title 为空时回退到动作预览（前缀「动作：」应被剥掉）
const TEXT_ONLY_FILE = makeFile({
  stem: 'rec-2',
  display_title: null,
  action_preview: '动作：忽略',
  model_name: null,
  duration_ms: null,
  size: 100,
  text_path: '/prompts/rec-2.txt',
  json_path: null,
  related_json_paths: [],
})

// 结构化 prompt：头部元信息（会话/调用 ID）+ 两条消息 + 输出结果
const STRUCTURED_JSON = JSON.stringify({
  schema_version: 3,
  request: {
    kind: 'planner',
    selection_reason: '会话ID: sess-full-1\n调用ID: call-9\n根据近期消息决定回复',
  },
  metadata: { model_name: 'gpt-test', duration_ms: 1500 },
  messages: [
    { index: 1, role: 'system', content: '系统提示词' },
    { index: 2, role: 'user', content: '用户发言内容' },
  ],
  output: {
    title: '决策结果',
    content: '回复用户',
    tool_calls: [
      {
        id: 'ws-test',
        name: 'web_search',
        arguments: {
          action_type: 'search',
          status: 'completed',
          details: ['查询：最新消息'],
          source_count: 3,
        },
        source: 'provider',
        source_label: 'Provider 原生调用',
      },
      {
        id: 'reply-test',
        name: 'reply',
        arguments: { msg_id: 'message-test' },
        source: 'response',
        source_label: '正文调用',
      },
    ],
  },
  provider_response: {
    id: 'resp-structured',
    model: 'deepseek-v4-flash',
    status: 'completed',
    parallel_tool_calls: true,
    output: [
      {
        type: 'reasoning',
        id: 'rs-1',
        summary: [{ type: 'summary_text', text: '先判断是否需要实时资料' }],
      },
      {
        type: 'web_search_call',
        id: 'ws-1',
        status: 'completed',
        action: { type: 'search', queries: ['最新消息'], sources: [{ url: 'https://example.com' }] },
      },
      {
        type: 'reasoning',
        id: 'rs-2',
        content: [{ type: 'reasoning_text', text: '搜索完成，开始整理结果' }],
      },
      {
        type: 'function_call',
        id: 'fc-1',
        call_id: 'reply-test',
        name: 'reply',
        arguments: '{"msg_id":"message-test"}',
        status: 'completed',
      },
      {
        type: 'message',
        id: 'msg-1',
        role: 'assistant',
        status: 'completed',
        content: [{ type: 'output_text', text: '原生最终回答' }],
      },
    ],
    usage: { input_tokens: 100, output_tokens: 50, total_tokens: 150 },
  },
  tool_definitions: [],
})

const LLM_ERROR_JSON = JSON.stringify({
  schema_version: 4,
  request: {
    kind: 'response',
    operation: 'create_response',
    request_type: 'chat',
    task_name: 'replyer',
  },
  metadata: {
    client_type: 'openai',
    created_at: '2026-07-28T12:00:00',
    updated_at: '2026-07-28T12:00:04',
    model_name: 'gpt-test',
    provider_name: 'test-provider',
    request_id: 'request-123',
    status: 'final_failed',
  },
  messages: [{ index: 1, role: 'user', content: '失败请求内容' }],
  attempts: [
    {
      attempt: 1,
      model_attempt: 1,
      model_name: 'gpt-test',
      provider_name: 'test-provider',
      client_type: 'openai',
      operation: 'create_response',
      status: 'retrying',
      retry_interval: 2,
      timestamp: '2026-07-28T12:00:01',
      error: { type: 'RateLimitError', status_code: 429, message: '请求频率过高' },
    },
    {
      attempt: 2,
      model_attempt: 2,
      model_name: 'gpt-test',
      provider_name: 'test-provider',
      status: 'final_failed',
      timestamp: '2026-07-28T12:00:04',
      error: {
        type: 'APIError',
        status_code: 503,
        message: '上游服务不可用',
        response_body: { error: 'unavailable' },
      },
    },
  ],
})

function makeFilesResponse(
  params: ReasoningPromptListParams,
  items: ReasoningPromptFile[],
  total = items.length
): ReasoningPromptListResponse {
  // 回显请求参数，避免组件因 page / selected_session 不一致而反复重查
  return {
    items,
    total,
    page: params.page ?? 1,
    page_size: params.pageSize ?? 50,
    stages: STAGE_INFOS.map((info) => info.name),
    stage_infos: STAGE_INFOS,
    sessions: ['sess-1'],
    session_infos: [SESSION_INFO],
    selected_session: params.session ?? 'auto',
  }
}

function makeContentResponse(path: string, content: string): ReasoningPromptContentResponse {
  return {
    path,
    content,
    size: content.length,
    modified_at: 1753500000,
    model_name: 'gpt-test',
    duration_ms: 1500,
    message_avatars: {},
  }
}

function makeReplayResponse(
  overrides: Partial<ReasoningReplayResponse> = {}
): ReasoningReplayResponse {
  return {
    success: true,
    response: '这是重放回复',
    reasoning: '',
    model_name: 'gpt-test',
    tool_calls: null,
    prompt_tokens: 100,
    completion_tokens: 20,
    total_tokens: 120,
    prompt_cache_hit_tokens: 0,
    prompt_cache_miss_tokens: 0,
    duration_ms: 800,
    error: null,
    ...overrides,
  }
}

const listStagesMock = vi.mocked(listReasoningPromptStages)
const listFilesMock = vi.mocked(listReasoningPromptFiles)
const getFileMock = vi.mocked(getReasoningPromptFile)
const getHtmlUrlMock = vi.mocked(getReasoningPromptHtmlUrl)
const clearStageMock = vi.mocked(clearReasoningPromptStage)
const replayMock = vi.mocked(replayReasoningPrompt)
const getModelConfigMock = vi.mocked(getModelConfig)

beforeEach(() => {
  listStagesMock.mockResolvedValue({
    stages: STAGE_INFOS.map((info) => info.name),
    stage_infos: STAGE_INFOS,
  })
  listFilesMock.mockImplementation(async (params) =>
    makeFilesResponse(params, [PLANNER_FILE, TEXT_ONLY_FILE])
  )
  getFileMock.mockImplementation(async (path) =>
    path.endsWith('.json')
      ? makeContentResponse(path, STRUCTURED_JSON)
      : makeContentResponse(path, '原始文本内容')
  )
  getHtmlUrlMock.mockResolvedValue('/resolved/html-preview')
  clearStageMock.mockResolvedValue({ stage: 'planner', deleted_files: 12 })
  replayMock.mockResolvedValue(makeReplayResponse())
  getModelConfigMock.mockResolvedValue({
    config: { models: [{ name: 'gpt-test' }, { name: 'another-model' }] },
  })
})

afterEach(() => {
  cleanup()
  // 复位 URL，避免 stage/returnTo 查询参数在用例间泄漏
  window.history.replaceState({}, '', '/')
})

/** 渲染页面并点击 planner 卡片进入类型浏览模式 */
async function renderAndEnterStage(expectedCountText = '2 条记录') {
  const user = userEvent.setup()
  render(<ReasoningProcessPage />)
  await user.click(await screen.findByRole('button', { name: /planner/ }))
  await screen.findByText(expectedCountText)
  return user
}

/** 进入浏览模式并选中带结构化内容的主记录 */
async function renderAndSelectRecord() {
  const user = await renderAndEnterStage()
  await user.click(screen.getByRole('button', { name: /打招呼回应/ }))
  await screen.findByText('系统提示词')
  return user
}

/** 选中记录后打开重放面板并等待模型列表加载完成 */
async function openReplayPanel() {
  const user = await renderAndSelectRecord()
  await user.click(screen.getByRole('button', { name: '重放' }))
  await screen.findByText('编辑重放消息')
  await waitFor(() => {
    expect(screen.getByRole('button', { name: /执行重放/ })).toBeEnabled()
  })
  return user
}

describe('类型总览', () => {
  it('按类别分组展示类型卡片，底部两个分组默认折叠可展开', async () => {
    const user = userEvent.setup()
    render(<ReasoningProcessPage />)

    // 四个分类行标题
    expect(await screen.findByText('主流程')).toBeInTheDocument()
    expect(screen.getByText('学习器')).toBeInTheDocument()
    expect(screen.getByText('其余')).toBeInTheDocument()
    // 各分类下的中文标签
    expect(screen.getByText('规划器')).toBeInTheDocument()
    expect(screen.getByText('回复器')).toBeInTheDocument()
    expect(screen.getByText('表达学习')).toBeInTheDocument()
    expect(screen.getByText('表情包发送')).toBeInTheDocument()
    // planner 卡片带「最新」后缀时文案在同一节点内拼接，用正则局部匹配
    expect(screen.getByText(/3 个会话/)).toBeInTheDocument()
    // 两个底部折叠分组内容默认不渲染
    expect(screen.queryByText('LLM 请求异常')).not.toBeInTheDocument()
    expect(screen.queryByText('时机判断')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /LLM 请求/ }))
    expect(screen.getByText('LLM 请求异常')).toBeInTheDocument()
    expect(screen.getByText('llm_error')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /不再使用/ }))
    expect(screen.getByText('时机判断')).toBeInTheDocument()
    expect(screen.getByText('timing_gate')).toBeInTheDocument()
  })

  it('LLM 请求异常详情展示最终状态和逐次重试信息', async () => {
    const llmErrorFile = makeFile({
      stage: 'llm_error',
      stem: 'request-123',
      text_path: null,
      json_path: '/prompts/request-123.json',
      related_json_paths: ['/prompts/request-123.json'],
      display_title: '最终失败 · 上游服务不可用',
      action_preview: null,
    })
    listFilesMock.mockImplementation(async (params) =>
      makeFilesResponse(params, params.stage === 'llm_error' ? [llmErrorFile] : [PLANNER_FILE])
    )
    getFileMock.mockImplementation(async (path) =>
      makeContentResponse(path, path.includes('request-123') ? LLM_ERROR_JSON : STRUCTURED_JSON)
    )

    const user = userEvent.setup()
    render(<ReasoningProcessPage />)
    await user.click(await screen.findByRole('button', { name: /LLM 请求/ }))
    await user.click(screen.getByRole('button', { name: /llm_error/ }))
    await user.click(await screen.findByRole('button', { name: /最终失败/ }))

    expect(await screen.findByRole('heading', { name: '请求结果' })).toBeInTheDocument()
    expect(screen.getAllByText('最终失败').length).toBeGreaterThan(0)
    expect(screen.getByText('2 次尝试')).toBeInTheDocument()
    expect(screen.getByText('请求频率过高')).toBeInTheDocument()
    expect(screen.getByText('上游服务不可用')).toBeInTheDocument()
    expect(screen.getByText('HTTP 503')).toBeInTheDocument()

    await user.click(screen.getByText('查看上游响应'))
    expect(screen.getByText(/unavailable/)).toBeInTheDocument()
  })

  it('类型列表加载失败时展示错误提示', async () => {
    listStagesMock.mockRejectedValue(new Error('后端连接失败'))
    render(<ReasoningProcessPage />)

    expect(await screen.findByText('后端连接失败')).toBeInTheDocument()
  })

  it('类型为空时展示空态文案', async () => {
    listStagesMock.mockResolvedValue({ stages: [], stage_infos: [] })
    render(<ReasoningProcessPage />)

    expect(await screen.findByText('没有找到推理过程类型')).toBeInTheDocument()
  })

  it('清空类型先弹确认框，取消时不发请求', async () => {
    const user = userEvent.setup()
    render(<ReasoningProcessPage />)

    await user.click(await screen.findByRole('button', { name: '清空规划器' }))
    expect(await screen.findByText('清空推理过程记录')).toBeInTheDocument()
    // 描述里带类型中文名与会话数量
    expect(screen.getByText(/将清空「规划器」/)).toBeInTheDocument()
    expect(screen.getByText(/当前包含 3 个会话/)).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '取消' }))
    await waitFor(() => {
      expect(screen.queryByText('清空推理过程记录')).not.toBeInTheDocument()
    })
    expect(clearStageMock).not.toHaveBeenCalled()
  })

  it('确认清空调用接口并 toast 提示删除数量', async () => {
    const user = userEvent.setup()
    render(<ReasoningProcessPage />)

    await user.click(await screen.findByRole('button', { name: '清空规划器' }))
    await user.click(await screen.findByRole('button', { name: '确认清空' }))

    await waitFor(() => {
      expect(clearStageMock).toHaveBeenCalledWith('planner')
    })
    expect(toastMock).toHaveBeenCalledWith({
      title: '已清空推理过程',
      description: '规划器：删除 12 个文件',
    })
  })

  it('清空失败时 toast 展示错误信息', async () => {
    clearStageMock.mockRejectedValue(new Error('磁盘只读'))
    const user = userEvent.setup()
    render(<ReasoningProcessPage />)

    await user.click(await screen.findByRole('button', { name: '清空规划器' }))
    await user.click(await screen.findByRole('button', { name: '确认清空' }))

    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith({
        title: '清空失败',
        description: '磁盘只读',
        variant: 'destructive',
      })
    })
  })
})

describe('类型浏览与文件列表', () => {
  it('进入类型后按默认参数请求文件列表并渲染记录卡片', async () => {
    await renderAndEnterStage()

    expect(listFilesMock).toHaveBeenCalledWith({
      stage: 'planner',
      session: 'auto',
      action: '',
      search: '',
      targetStem: '',
      page: 1,
      pageSize: 50,
    })
    // 记录预览：display_title 优先；无标题时剥掉「动作：」前缀
    expect(screen.getByText('打招呼回应')).toBeInTheDocument()
    expect(screen.getByText('忽略')).toBeInTheDocument()
    // 模型 / 耗时 / 大小格式化
    expect(screen.getByText('gpt-test')).toBeInTheDocument()
    expect(screen.getByText('1.50 s')).toBeInTheDocument()
    expect(screen.getByText('2.0 KB')).toBeInTheDocument()
    expect(screen.getByText('第 1 / 1 页')).toBeInTheDocument()
  })

  it('搜索与动作过滤会重置页码并携带查询参数', async () => {
    await renderAndEnterStage()

    fireEvent.change(
      screen.getByPlaceholderText('搜索会话、文件名、模型或记录摘要'),
      { target: { value: '关键词' } }
    )
    await waitFor(() => {
      expect(listFilesMock).toHaveBeenLastCalledWith(
        expect.objectContaining({ search: '关键词', page: 1 })
      )
    })

    fireEvent.change(screen.getByPlaceholderText('动作过滤'), { target: { value: '回复' } })
    await waitFor(() => {
      expect(listFilesMock).toHaveBeenLastCalledWith(
        expect.objectContaining({ action: '回复', search: '关键词', page: 1 })
      )
    })
  })

  it('分页：首页禁用上一页，下一页按 page=2 重新请求', async () => {
    listFilesMock.mockImplementation(async (params) =>
      makeFilesResponse(params, [PLANNER_FILE, TEXT_ONLY_FILE], 120)
    )
    const user = await renderAndEnterStage('120 条记录')

    expect(screen.getByText('第 1 / 3 页')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '上一页' })).toBeDisabled()

    await user.click(screen.getByRole('button', { name: '下一页' }))
    await waitFor(() => {
      expect(listFilesMock).toHaveBeenLastCalledWith(expect.objectContaining({ page: 2 }))
    })
    expect(await screen.findByText('第 2 / 3 页')).toBeInTheDocument()
  })

  it('文件列表加载失败展示错误横幅', async () => {
    listFilesMock.mockRejectedValue(new Error('文件列表读取失败'))
    const user = userEvent.setup()
    render(<ReasoningProcessPage />)

    await user.click(await screen.findByRole('button', { name: /planner/ }))
    expect(await screen.findByText('文件列表读取失败')).toBeInTheDocument()
  })

  it('文件列表为空时展示空态文案', async () => {
    listFilesMock.mockImplementation(async (params) => makeFilesResponse(params, []))
    const user = userEvent.setup()
    render(<ReasoningProcessPage />)

    await user.click(await screen.findByRole('button', { name: /planner/ }))
    expect(await screen.findByText('没有找到推理过程记录')).toBeInTheDocument()
  })

  it('「类型」按钮返回类型总览', async () => {
    const user = await renderAndEnterStage()

    await user.click(screen.getByRole('button', { name: '类型' }))
    expect(await screen.findByText('主流程')).toBeInTheDocument()
    expect(screen.queryByText('2 条记录')).not.toBeInTheDocument()
  })
})

describe('URL 参数与返回入口', () => {
  it('带 stage 参数直达浏览模式，合法 returnTo 显示返回按钮并跳转', async () => {
    window.history.replaceState({}, '', '/?stage=replyer&returnTo=/maisaka')
    const user = userEvent.setup()
    render(<ReasoningProcessPage />)

    await waitFor(() => {
      expect(listFilesMock).toHaveBeenCalledWith(expect.objectContaining({ stage: 'replyer' }))
    })

    await user.click(screen.getByRole('button', { name: '返回观察' }))
    expect(navigateMock).toHaveBeenCalledWith({ to: '/maisaka' })
  })

  it('以 // 开头的 returnTo 被拒绝，不显示返回按钮', async () => {
    window.history.replaceState({}, '', '/?returnTo=//evil.com')
    render(<ReasoningProcessPage />)

    expect(await screen.findByText('主流程')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '返回观察' })).not.toBeInTheDocument()
  })
})

describe('记录详情', () => {
  it('选中记录后加载文本与结构化内容，展示标题与头部元信息', async () => {
    await renderAndSelectRecord()

    // 文本与 JSON 双路加载
    expect(getFileMock).toHaveBeenCalledWith('/prompts/rec-1.txt')
    expect(getFileMock).toHaveBeenCalledWith('/prompts/rec-1.json')
    // 标题：类型中文名/会话显示名/记录标题/平台/群聊/目标 ID
    // （详情头部与常驻隐藏的重放面板头部各出现一次）
    expect(screen.getAllByText('规划器/测试群聊/打招呼回应/qq/群聊/123456').length).toBeGreaterThan(0)
    // selection_reason 中的会话/调用 ID 被抽取到头部
    expect(screen.getByText('会话ID: sess-full-1')).toBeInTheDocument()
    expect(screen.getByText('调用ID: call-9')).toBeInTheDocument()
    expect(screen.getByText('根据近期消息决定回复')).toBeInTheDocument()
    // 结构化消息与输出
    expect(screen.getByText('system')).toBeInTheDocument()
    expect(screen.getByText('用户发言内容')).toBeInTheDocument()
    expect(screen.getByText('决策结果')).toBeInTheDocument()
    expect(screen.getByText('回复用户')).toBeInTheDocument()
    // 有 json_path 的记录默认展示结构化页签
    expect(screen.getByRole('tab', { name: '结构化' })).toBeInTheDocument()
  })

  it('详细推理记录合并展示 Provider 原生工具和 function 工具', async () => {
    const user = await renderAndSelectRecord()

    await user.click(screen.getByRole('button', { name: '工具调用 · 2 个' }))

    expect(screen.getByText('web_search')).toBeInTheDocument()
    expect(screen.getByText('Provider 原生调用')).toBeInTheDocument()
    expect(screen.getByText('reply')).toBeInTheDocument()
    expect(screen.getByText('正文调用')).toBeInTheDocument()
    expect(screen.getByText(/查询：最新消息/)).toBeInTheDocument()
  })

  it('按 Responses output 顺序完整展示多段推理、原生工具和多段输出', async () => {
    const user = await renderAndSelectRecord()

    expect(screen.getByText('Responses 原生输出')).toBeInTheDocument()
    expect(screen.getByText('5 Items')).toBeInTheDocument()
    expect(screen.getByText('先判断是否需要实时资料')).toBeInTheDocument()
    expect(screen.getByText('搜索完成，开始整理结果')).toBeInTheDocument()
    expect(screen.getByText('原生最终回答')).toBeInTheDocument()
    expect(screen.getByText('联网搜索')).toBeInTheDocument()
    expect(screen.getByText('Function 调用')).toBeInTheDocument()
    expect(screen.getByText(/"queries": \[/)).toBeInTheDocument()
    expect(screen.getByText('输入 100')).toBeInTheDocument()
    expect(screen.getByText('输出 50')).toBeInTheDocument()
    expect(screen.getByText('总计 150')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '完整 Responses JSON' }))
    expect(screen.getByText(/"parallel_tool_calls": true/)).toBeInTheDocument()
  })

  it('复制按钮把文本内容写入剪贴板并提示成功', async () => {
    const user = await renderAndSelectRecord()
    const writeTextSpy = vi.spyOn(navigator.clipboard, 'writeText')

    await user.click(screen.getByRole('button', { name: '复制' }))

    expect(writeTextSpy).toHaveBeenCalledWith('原始文本内容')
    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({ title: '已复制完整 Prompt' })
      )
    })
  })

  it('纯文本记录读取失败时在文本页展示错误信息', async () => {
    listFilesMock.mockImplementation(async (params) => makeFilesResponse(params, [TEXT_ONLY_FILE]))
    getFileMock.mockRejectedValue(new Error('读取文本失败啦'))
    const user = userEvent.setup()
    render(<ReasoningProcessPage />)

    await user.click(await screen.findByRole('button', { name: /planner/ }))
    await user.click(await screen.findByRole('button', { name: /忽略/ }))

    expect(await screen.findByText('读取文本失败啦')).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: '文本' })).toBeInTheDocument()
  })
})

describe('重放', () => {
  it('打开重放面板载入模型列表与可编辑消息，执行重放提交完整请求形状', async () => {
    const user = await openReplayPanel()

    // 编辑列展示两条可编辑消息
    expect(screen.getByText('2 条')).toBeInTheDocument()
    expect(screen.getByDisplayValue('系统提示词')).toBeInTheDocument()
    expect(screen.getByDisplayValue('用户发言内容')).toBeInTheDocument()
    expect(getModelConfigMock).toHaveBeenCalledTimes(1)

    await user.click(screen.getByRole('button', { name: /执行重放/ }))

    await waitFor(() => {
      expect(replayMock).toHaveBeenCalledWith({
        source_path: '/prompts/rec-1.json',
        stage: 'planner',
        model_name: 'gpt-test',
        messages: [
          { role: 'system', content: '系统提示词' },
          { role: 'user', content: '用户发言内容' },
        ],
        tool_definitions: [],
        temperature: null,
        max_tokens: null,
      })
    })
    // 结果卡片：完成徽标、正文与 token 汇总
    expect(await screen.findByText('#1 完成')).toBeInTheDocument()
    expect(screen.getByText('这是重放回复')).toBeInTheDocument()
    expect(screen.getByText('输入 100 · 输出 20 · 总计 120 · 耗时 800 ms')).toBeInTheDocument()
    expect(toastMock).toHaveBeenCalledWith({
      title: '批量重放完成',
      description: '成功 1/1 次。',
      variant: 'default',
    })
  })

  it('重放次数超出范围时直接提示，不发请求', async () => {
    const user = await openReplayPanel()

    fireEvent.change(screen.getByLabelText('次数'), { target: { value: '99' } })
    await user.click(screen.getByRole('button', { name: /执行重放/ }))

    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith({
        title: '重放次数无效',
        description: '请输入 1-20 之间的整数。',
        variant: 'destructive',
      })
    })
    expect(replayMock).not.toHaveBeenCalled()
  })

  it('重放接口失败时展示失败结果与 destructive 汇总', async () => {
    replayMock.mockRejectedValue(new Error('模型超时'))
    const user = await openReplayPanel()

    await user.click(screen.getByRole('button', { name: /执行重放/ }))

    expect(await screen.findByText('#1 失败')).toBeInTheDocument()
    expect(screen.getByText('模型超时')).toBeInTheDocument()
    expect(toastMock).toHaveBeenCalledWith({
      title: '批量重放完成',
      description: '成功 0/1 次。',
      variant: 'destructive',
    })
  })

  it('重放编辑器支持添加与删除消息', async () => {
    const user = await openReplayPanel()

    await user.click(screen.getByRole('button', { name: '添加消息' }))
    expect(screen.getByText('3 条')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '删除第 1 条消息' }))
    expect(screen.getByText('2 条')).toBeInTheDocument()
    // 原第一条 system 消息被删除
    expect(screen.queryByDisplayValue('系统提示词')).not.toBeInTheDocument()
  })
})

describe('embedded 模式', () => {
  it('把工具栏与顶栏动作 Portal 到指定容器，并回调工具栏内容可见性', async () => {
    window.history.replaceState({}, '', '/?returnTo=/logs')
    const toolbarRoot = document.createElement('div')
    toolbarRoot.id = 'reasoning-toolbar-root'
    const topbarRoot = document.createElement('div')
    topbarRoot.id = 'reasoning-topbar-root'
    document.body.append(toolbarRoot, topbarRoot)
    const onToolbarContentVisibleChange = vi.fn()

    try {
      render(
        <ReasoningProcessPage
          embedded
          toolbarContainerId="reasoning-toolbar-root"
          topbarActionsContainerId="reasoning-topbar-root"
          onToolbarContentVisibleChange={onToolbarContentVisibleChange}
        />
      )
      await screen.findByText('主流程')

      // 工具栏容器里渲染返回按钮（embedded 下过滤器与刷新不进工具栏）
      expect(within(toolbarRoot).getByRole('button', { name: '返回观察' })).toBeInTheDocument()
      // 顶栏动作容器在 requestAnimationFrame 后渲染刷新按钮
      await waitFor(() => {
        expect(within(topbarRoot).getByRole('button', { name: '刷新' })).toBeInTheDocument()
      })
      // returnTo 存在 → 工具栏内容可见性回调为 true
      expect(onToolbarContentVisibleChange).toHaveBeenCalledWith(true)
    } finally {
      toolbarRoot.remove()
      topbarRoot.remove()
    }
  })
})
