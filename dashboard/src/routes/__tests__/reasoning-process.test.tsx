/**
 * ReasoningProcessPage 页面特征化测试。
 *
 * 只 mock 请求层（reasoning-process-api / config-api）、路由、toast 与头像开关，
 * 用真实组件树锁定类型卡片、筛选搜索、复制导出、清空、重放 Items 与结构化预览。
 */
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { resolveApiPath } from '@/lib/api-base'
import * as configApi from '@/lib/config-api'
import * as reasoningApi from '@/lib/reasoning-process-api'
import type {
  ReasoningPromptFile,
  ReasoningPromptListResponse,
  ReasoningPromptSessionInfo,
  ReasoningPromptStageInfo,
} from '@/lib/reasoning-process-api'

import { ReasoningProcessPage } from '../reasoning-process'

const { toastMock, navigateMock, useAvatarFetchEnabledMock } = vi.hoisted(() => ({
  toastMock: vi.fn(),
  navigateMock: vi.fn(),
  useAvatarFetchEnabledMock: vi.fn(() => false),
}))

vi.mock('@/hooks/use-toast', () => ({
  useToast: () => ({ toast: toastMock }),
}))

vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => navigateMock,
}))

vi.mock('@/lib/avatar-url', () => ({
  useAvatarFetchEnabled: () => useAvatarFetchEnabledMock(),
}))

vi.mock('@/lib/api-base', () => ({
  resolveApiPath: vi.fn(async (path: string) => `resolved:${path}`),
}))

vi.mock('@/lib/config-api', () => ({
  getModelConfig: vi.fn(),
}))

vi.mock('@/lib/reasoning-process-api', () => ({
  listReasoningPromptFiles: vi.fn(),
  listReasoningPromptStages: vi.fn(),
  clearReasoningPromptStage: vi.fn(),
  getReasoningPromptFile: vi.fn(),
  getReasoningPromptHtmlUrl: vi.fn(),
  getReasoningPromptImageUrl: vi.fn(),
  replayReasoningPrompt: vi.fn(),
}))

vi.mock('@/components/CodeEditor', () => ({
  CodeEditor: ({
    value,
    onChange,
  }: {
    value: string
    onChange?: (next: string) => void
  }) => (
    <textarea
      aria-label="可编辑编辑器"
      data-testid="replay-json-editor"
      value={value}
      onChange={(event) => onChange?.(event.target.value)}
    />
  ),
}))

const extraExportUsers = Array.from({ length: 27 }, (_, index) => ({
  user_name: `Name${index}`,
}))

const defaultStructuredPayload = {
  schema_version: 6,
  request: {
    kind: 'planner',
    selection_reason: '会话 ID：sess-abc\n调用 ID：call-1\n今晚吃什么',
  },
  metadata: {
    model_name: 'gpt-test',
    duration_ms: 250,
    status: 'succeeded',
    client_type: 'openai',
    provider_name: 'openai',
    request_id: 'req-1',
    created_at: '2026-08-01T12:00:00.000Z',
    updated_at: '2026-08-01T12:00:01.000Z',
  },
  presentation: { output_title: '规划结果' },
  request_items: [
    {
      item_type: 'UserMessageItem',
      meta: {
        item_id: 'u1',
        logical_turn_id: null,
        timestamp: '2026-08-01T12:00:00.000Z',
      },
      parts: [
        {
          type: 'text',
          text: '<message user="张三" group_card="群名片甲" msg_id="m1">你好</message>',
        },
      ],
    },
    {
      item_type: 'SystemMessageItem',
      meta: {
        item_id: 's1',
        logical_turn_id: null,
        timestamp: '2026-08-01T12:00:00.000Z',
      },
      parts: [{ type: 'text', text: '系统提示' }],
    },
  ],
  output_items: [
    {
      item_type: 'AssistantMessageItem',
      meta: {
        item_id: 'a1',
        logical_turn_id: null,
        timestamp: '2026-08-01T12:00:01.000Z',
      },
      parts: [{ type: 'text', text: '张三 说晚饭' }],
    },
  ],
  generation_attempts: [],
  tool_definitions: [{ name: 'search', description: 'search tool' }],
  users: extraExportUsers,
  special: { user: '李(四)' },
  stats: 3,
  flags: [true, null],
}

const defaultStructuredJson = JSON.stringify(defaultStructuredPayload)

const llmErrorPayload = {
  schema_version: 6,
  request: {
    kind: 'llm_error',
    task_name: 'reply',
    request_type: 'chat',
    operation: 'generate',
  },
  metadata: {
    status: 'succeeded_after_retry',
    client_type: 'openai',
    model_name: 'gpt-error',
    provider_name: 'openai',
    request_id: 'req-err',
    created_at: 'not-a-date',
    updated_at: '2026-08-01T12:00:01.000Z',
  },
  request_items: [],
  output_items: [],
  generation_attempts: [
    {
      attempt_id: 'att-1',
      workflow_purpose: 'reply',
      workflow_attempt: 1,
      provider_attempt: 1,
      model_attempt: 1,
      status: 'failed',
      started_at: '2026-08-01T12:00:00.000Z',
      duration_ms: 12.3,
      provider: 'openai',
      endpoint: 'https://api.openai.com',
      model: 'gpt-error',
      client_type: 'openai',
      operation: 'generate',
      wire_protocol: 'openai',
      request_items: [],
      tool_definitions: [],
      request_parameters: {},
      wire_request: null,
      wire_response: null,
      output_items: [],
    },
  ],
}

const jargonPayloadA = {
  schema_version: 6,
  request: {
    kind: 'jargon_learning_update',
    selection_reason: '推断阶段: 初轮推断\n词条上下文',
  },
  metadata: { model_name: 'jargon-a', duration_ms: 12.3 },
  request_items: [
    {
      item_type: 'UserMessageItem',
      meta: {
        item_id: 'j1',
        logical_turn_id: null,
        timestamp: '2026-08-01T12:00:00.000Z',
      },
      parts: [{ type: 'text', text: '这是什么梗' }],
    },
  ],
  output_items: [],
  generation_attempts: [],
}

const jargonPayloadB = {
  schema_version: 6,
  request: {
    kind: 'jargon_learning_update',
    selection_reason: '推断阶段:\n没有有效阶段名',
  },
  metadata: { model_name: 'jargon-b', duration_ms: 1500 },
  request_items: [
    {
      item_type: 'UserMessageItem',
      meta: {
        item_id: 'j2',
        logical_turn_id: null,
        timestamp: '2026-08-01T12:00:00.000Z',
      },
      parts: [{ type: 'text', text: '再推断一次' }],
    },
  ],
  output_items: [],
  generation_attempts: [],
}

function makeStage(
  name: string,
  overrides: Partial<ReasoningPromptStageInfo> = {}
): ReasoningPromptStageInfo {
  return {
    name,
    session_count: 1,
    latest_modified_at: 0,
    ...overrides,
  }
}

const defaultStageInfos: ReasoningPromptStageInfo[] = [
  makeStage('planner', { session_count: 2, latest_modified_at: 1_700_000_000 }),
  makeStage('replyer'),
  makeStage('expression_learner'),
  makeStage('emotion'),
  makeStage('llm_error'),
  makeStage('timing_gate'),
  makeStage('jargon_learning_update'),
]

const defaultStages = defaultStageInfos.map((item) => item.name)

function makeSession(
  name: string,
  overrides: Partial<ReasoningPromptSessionInfo> = {}
): ReasoningPromptSessionInfo {
  return {
    name,
    platform: 'qq',
    chat_type: 'group',
    target_id: '10001',
    resolved_session_id: `resolved-${name}`,
    display_name: name === 'sess-1' ? '测试群' : `会话${name}`,
    account_id: '123',
    matched_current_account: true,
    ...overrides,
  }
}

const sessionInfos = [
  makeSession('sess-1'),
  makeSession('sess-2', { display_name: '备用群', target_id: '10002' }),
]

function makeItem(overrides: Partial<ReasoningPromptFile> = {}): ReasoningPromptFile {
  return {
    stage: 'planner',
    session_id: 'sess-1',
    resolved_session_id: 'resolved-sess-1',
    session_display_name: '测试群',
    platform: 'qq',
    chat_type: 'group',
    target_id: '10001',
    stem: 'stem-dinner',
    timestamp: 1_700_000_000_000,
    text_path: '/data/planner/dinner.txt',
    html_path: '/data/planner/dinner.html',
    json_path: '/data/planner/dinner.json',
    output_preview: null,
    action_preview: '动作：安排晚饭',
    display_title: '晚饭计划',
    related_json_paths: [],
    has_behavior_choice_insert: false,
    model_name: 'gpt-test',
    duration_ms: 250,
    prompt_tokens: 10,
    completion_tokens: 5,
    total_tokens: 15,
    size: 2048,
    modified_at: 1_700_000_000,
    ...overrides,
  }
}

const plannerItem = makeItem()
const plannerAltItem = makeItem({
  stem: 'stem-lunch',
  display_title: '午饭计划',
  action_preview: '动作：安排午饭',
  text_path: '/data/planner/lunch.txt',
  json_path: '/data/planner/lunch.json',
  html_path: null,
  has_behavior_choice_insert: true,
  duration_ms: 12.3,
  size: 512,
  timestamp: null,
})
const replyerItem = makeItem({
  stage: 'replyer',
  stem: 'stem-reply',
  display_title: null,
  action_preview: '动作：不该出现',
  output_preview: '动作：回复内容预览',
  text_path: '/data/replyer/a.txt',
  json_path: '/data/replyer/a.json',
  html_path: null,
  duration_ms: 1500,
  size: 2 * 1024 * 1024,
  prompt_tokens: 8,
  completion_tokens: null,
  total_tokens: null,
})
const textOnlyItem = makeItem({
  stem: 'stem-text',
  display_title: '纯文本记录',
  json_path: null,
  html_path: null,
  text_path: '/data/planner/plain.txt',
  related_json_paths: [],
  model_name: null,
  duration_ms: null,
  prompt_tokens: null,
  completion_tokens: null,
  total_tokens: null,
})
const htmlOnlyItem = makeItem({
  stem: 'stem-html',
  display_title: '纯 HTML 记录',
  json_path: null,
  text_path: null,
  html_path: '/data/planner/only.html',
})
const invalidJsonItem = makeItem({
  stem: 'stem-invalid',
  display_title: '坏 JSON',
  json_path: '/data/planner/invalid.json',
  text_path: null,
  html_path: null,
})
const llmErrorItem = makeItem({
  stage: 'llm_error',
  stem: 'stem-llm',
  display_title: 'LLM 重试成功',
  json_path: '/data/llm_error/retry.json',
  text_path: null,
  html_path: null,
  model_name: 'gpt-error',
})
const jargonItem = makeItem({
  stage: 'jargon_learning_update',
  stem: 'stem-jargon',
  display_title: '离谱黑话',
  json_path: '/data/jargon/jargon-a.json',
  related_json_paths: ['/data/jargon/jargon-a.json', '/data/jargon/jargon-b.json'],
  text_path: null,
  html_path: null,
})

function makeListResponse(
  overrides: Partial<ReasoningPromptListResponse> = {}
): ReasoningPromptListResponse {
  const items = overrides.items ?? [plannerItem, plannerAltItem]
  return {
    items,
    total: overrides.total ?? items.length,
    page: overrides.page ?? 1,
    page_size: overrides.page_size ?? 50,
    stages: overrides.stages ?? defaultStages,
    stage_infos: overrides.stage_infos ?? defaultStageInfos,
    sessions: overrides.sessions ?? ['sess-1', 'sess-2'],
    session_infos: overrides.session_infos ?? sessionInfos,
    selected_session: overrides.selected_session ?? 'auto',
  }
}

function makeFileContent(
  path: string,
  content: string,
  extra: Partial<Awaited<ReturnType<typeof reasoningApi.getReasoningPromptFile>>> = {}
) {
  return {
    path,
    content,
    size: content.length,
    modified_at: 1_700_000_000,
    model_name: 'gpt-test',
    duration_ms: 250,
    prompt_tokens: 10,
    completion_tokens: 5,
    total_tokens: 15,
    message_avatars: {},
    ...extra,
  }
}

function setLocationSearch(search = '') {
  const url = search ? `/reasoning-process?${search}` : '/reasoning-process'
  window.history.replaceState({}, '', url)
}

function lastFilesQuery() {
  const calls = vi.mocked(reasoningApi.listReasoningPromptFiles).mock.calls
  return calls[calls.length - 1]?.[0]
}

function installDefaultApiMocks() {
  vi.mocked(reasoningApi.listReasoningPromptStages).mockResolvedValue({
    stages: defaultStages,
    stage_infos: defaultStageInfos,
  })
  vi.mocked(reasoningApi.listReasoningPromptFiles).mockImplementation(async (params) => {
    const stage = params.stage ?? 'planner'
    const itemsByStage: Record<string, ReasoningPromptFile[]> = {
      planner: [plannerItem, plannerAltItem],
      replyer: [replyerItem],
      llm_error: [llmErrorItem],
      jargon_learning_update: [jargonItem],
    }
    let items = itemsByStage[stage] ?? []
    if (params.search) {
      items = items.filter((item) =>
        `${item.display_title ?? ''} ${item.action_preview ?? ''} ${item.stem}`.includes(
          params.search ?? ''
        )
      )
    }
    if (params.action) {
      items = items.filter((item) => (item.action_preview ?? '').includes(params.action ?? ''))
    }
    return makeListResponse({
      items,
      total: params.pageSize === 50 && stage === 'planner' && !params.search && !params.action
        ? items.length
        : items.length,
      page: params.page ?? 1,
      selected_session: params.session === 'auto' ? 'auto' : (params.session ?? 'auto'),
    })
  })
  vi.mocked(reasoningApi.getReasoningPromptFile).mockImplementation(async (path) => {
    if (path.includes('invalid')) {
      return makeFileContent(path, '{')
    }
    if (path.includes('jargon-a')) {
      return makeFileContent(path, JSON.stringify(jargonPayloadA))
    }
    if (path.includes('jargon-b')) {
      return makeFileContent(path, JSON.stringify(jargonPayloadB))
    }
    if (path.includes('llm_error') || path.includes('retry.json')) {
      return makeFileContent(path, JSON.stringify(llmErrorPayload))
    }
    if (path.endsWith('.txt')) {
      return makeFileContent(path, '完整文本 Prompt')
    }
    return makeFileContent(path, defaultStructuredJson, {
      message_avatars: {
        m1: {
          message_id: 'm1',
          platform: 'qq',
          user_id: 'u1',
          display_name: '张三',
          avatar_url: '/avatar/zhang.png',
        },
        m2: {
          message_id: 'm2',
          platform: 'qq',
          user_id: 'u2',
          display_name: '无头像',
          avatar_url: null,
        },
      },
    })
  })
  vi.mocked(reasoningApi.getReasoningPromptHtmlUrl).mockResolvedValue(
    'http://html-preview/dinner.html'
  )
  vi.mocked(reasoningApi.getReasoningPromptImageUrl).mockResolvedValue('/resolved-image')
  vi.mocked(reasoningApi.clearReasoningPromptStage).mockResolvedValue({
    stage: 'planner',
    deleted_files: 4,
  })
  vi.mocked(reasoningApi.replayReasoningPrompt).mockResolvedValue({
    schema_version: 6,
    success: true,
    output_items: [],
    generation_attempts: [],
    model_name: 'gpt-test',
    prompt_tokens: 1,
    completion_tokens: 1,
    total_tokens: 2,
    prompt_cache_hit_tokens: 0,
    prompt_cache_miss_tokens: 1,
    duration_ms: 10,
  })
  vi.mocked(configApi.getModelConfig).mockResolvedValue({
    models: [{ name: 'gpt-test' }, { name: 'other-model' }],
  } as never)
  vi.mocked(resolveApiPath).mockImplementation(async (path: string) => `resolved:${path}`)
  useAvatarFetchEnabledMock.mockReturnValue(false)
}

function renderPage(
  props: Parameters<typeof ReasoningProcessPage>[0] = {},
  extra?: { toolbar?: boolean }
) {
  if (!extra?.toolbar) {
    return render(<ReasoningProcessPage {...props} />)
  }
  return render(
    <>
      <div id="rp-toolbar" />
      <div id="rp-topbar" />
      <ReasoningProcessPage
        embedded
        toolbarContainerId="rp-toolbar"
        toolbarVisible
        topbarActionsContainerId="rp-topbar"
        {...props}
      />
    </>
  )
}

async function enterStage(user: ReturnType<typeof userEvent.setup>, stageName: string) {
  await user.click(await screen.findByRole('button', { name: new RegExp(`^${stageName}\\b`) }))
}

async function selectRecord(preview: string) {
  await userEvent.click(await screen.findByRole('button', { name: new RegExp(preview) }))
  await screen.findByRole('button', { name: '复制' })
}

beforeEach(() => {
  if (!Element.prototype.hasPointerCapture) {
    Element.prototype.hasPointerCapture = () => false
  }
  if (!Element.prototype.setPointerCapture) {
    Element.prototype.setPointerCapture = () => {}
  }
  if (!Element.prototype.releasePointerCapture) {
    Element.prototype.releasePointerCapture = () => {}
  }
  setLocationSearch()
  installDefaultApiMocks()
})

afterEach(() => {
  cleanup()
  setLocationSearch()
})

describe('ReasoningProcessPage 类型列表', () => {
  it('按类别渲染阶段卡片，折叠 LLM 请求与不再使用', async () => {
    renderPage()

    expect(await screen.findByText('主流程')).toBeInTheDocument()
    expect(screen.getByText('学习器')).toBeInTheDocument()
    expect(screen.getByText('其余')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^planner\b/ })).toBeInTheDocument()
    expect(screen.getByText('规划器')).toBeInTheDocument()
    expect(screen.getByText(/2 个会话/)).toBeInTheDocument()
    expect(screen.getByText('表达学习')).toBeInTheDocument()
    expect(screen.getByText('表情包发送')).toBeInTheDocument()

    // 折叠组默认不展示卡片正文
    expect(screen.queryByRole('button', { name: /^llm_error\b/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^timing_gate\b/ })).not.toBeInTheDocument()

    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: /LLM 请求/ }))
    expect(await screen.findByRole('button', { name: /^llm_error\b/ })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /不再使用/ }))
    expect(await screen.findByRole('button', { name: /^timing_gate\b/ })).toBeInTheDocument()
  })

  it('stage_infos 缺失时回退到阶段名卡片，并显示空态', async () => {
    vi.mocked(reasoningApi.listReasoningPromptStages).mockResolvedValue({
      stages: ['custom_stage'],
      stage_infos: undefined as unknown as ReasoningPromptStageInfo[],
    })
    renderPage()

    expect(await screen.findByRole('button', { name: /^custom_stage\b/ })).toBeInTheDocument()
    expect(screen.getByText('0 个会话')).toBeInTheDocument()
  })

  it('没有类型时显示空态，加载失败与非 Error 失败分别展示文案', async () => {
    vi.mocked(reasoningApi.listReasoningPromptStages).mockResolvedValue({
      stages: [],
      stage_infos: [],
    })
    renderPage()
    expect(await screen.findByText('没有找到推理过程类型')).toBeInTheDocument()
    cleanup()

    vi.mocked(reasoningApi.listReasoningPromptStages).mockRejectedValue(new Error('网络断开'))
    renderPage()
    expect(await screen.findByText('网络断开')).toBeInTheDocument()
    cleanup()

    vi.mocked(reasoningApi.listReasoningPromptStages).mockRejectedValue('oops')
    renderPage()
    expect(await screen.findByText('加载推理过程类型失败')).toBeInTheDocument()
  })

  it('刷新会重新拉取类型列表', async () => {
    const user = userEvent.setup()
    renderPage()
    await screen.findByText('主流程')

    await user.click(screen.getByRole('button', { name: '刷新' }))
    await waitFor(() => {
      expect(reasoningApi.listReasoningPromptStages).toHaveBeenCalledTimes(2)
    })
  })
})

describe('ReasoningProcessPage 浏览筛选与分页', () => {
  it('进入阶段后渲染记录元数据、行为点与回复器预览', async () => {
    const user = userEvent.setup()
    renderPage()
    await enterStage(user, 'planner')

    await waitFor(() => {
      expect(lastFilesQuery()).toMatchObject({
        stage: 'planner',
        session: 'auto',
        action: '',
        search: '',
        targetStem: '',
        page: 1,
        pageSize: 50,
      })
    })
    expect(await screen.findByText('晚饭计划')).toBeInTheDocument()
    expect(screen.getByText('午饭计划')).toBeInTheDocument()
    expect(screen.getByLabelText('包含行为表现参考')).toBeInTheDocument()
    expect(screen.getAllByText('gpt-test').length).toBeGreaterThan(0)
    expect(screen.getByText('250 ms')).toBeInTheDocument()
    expect(screen.getByText('12.3 ms')).toBeInTheDocument()
    expect(screen.getByText('2.0 KB')).toBeInTheDocument()
    expect(screen.getByText('512 B')).toBeInTheDocument()
    expect(screen.getAllByText('输入 10 / 输出 5 / 总计 15 Token').length).toBeGreaterThan(0)
    expect(screen.getByText('2 条记录')).toBeInTheDocument()
    expect(screen.getAllByText('未选择记录').length).toBeGreaterThan(0)
    expect(screen.getByText('从左侧列表选择一条记录')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '类型' }))
    await screen.findByText('主流程')
    await enterStage(user, 'replyer')
    expect(await screen.findByText('回复内容预览')).toBeInTheDocument()
    expect(screen.queryByText('不该出现')).not.toBeInTheDocument()
    expect(screen.getByText('1.50 s')).toBeInTheDocument()
    expect(screen.getByText('2.0 MB')).toBeInTheDocument()
    expect(screen.getByText('输入 8 Token')).toBeInTheDocument()
  })

  it('动作过滤与搜索会重置到第一页并带入查询参数', async () => {
    renderPage()
    await enterStage(userEvent.setup(), 'planner')
    await screen.findByText('晚饭计划')

    fireEvent.change(screen.getByPlaceholderText('动作过滤'), {
      target: { value: '午饭' },
    })
    await waitFor(() => {
      expect(lastFilesQuery()).toMatchObject({ action: '午饭', search: '', page: 1 })
    })
    expect(screen.queryByText('晚饭计划')).not.toBeInTheDocument()
    expect(screen.getByText('午饭计划')).toBeInTheDocument()

    fireEvent.change(screen.getByPlaceholderText('搜索会话、文件名、模型或记录摘要'), {
      target: { value: '不存在的关键词' },
    })
    await waitFor(() => {
      expect(lastFilesQuery()).toMatchObject({
        action: '午饭',
        search: '不存在的关键词',
        page: 1,
      })
    })
    expect(await screen.findByText('没有找到推理过程记录')).toBeInTheDocument()
  })

  it('分页按钮按当前页请求，并接受后端回写的页码', async () => {
    const user = userEvent.setup()
    vi.mocked(reasoningApi.listReasoningPromptFiles).mockImplementation(async (params) =>
      makeListResponse({
        items: [plannerItem],
        total: 51,
        page: params.page === 2 ? 1 : (params.page ?? 1),
      })
    )
    renderPage()
    await enterStage(user, 'planner')
    expect(await screen.findByText('第 1 / 2 页')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '上一页' })).toBeDisabled()

    await user.click(screen.getByRole('button', { name: '下一页' }))
    await waitFor(() => {
      expect(
        vi.mocked(reasoningApi.listReasoningPromptFiles).mock.calls.some(
          ([params]) => params.page === 2
        )
      ).toBe(true)
    })
    // 后端把 page=2 纠正回 1 后，页面展示与后续请求都回到第 1 页
    expect(await screen.findByText('第 1 / 2 页')).toBeInTheDocument()
  })

  it('列表失败与非 Error 失败分别展示文案', async () => {
    const user = userEvent.setup()
    vi.mocked(reasoningApi.listReasoningPromptFiles).mockRejectedValue(new Error('列表失败'))
    renderPage()
    await enterStage(user, 'planner')
    expect(await screen.findByText('列表失败')).toBeInTheDocument()
    cleanup()

    vi.mocked(reasoningApi.listReasoningPromptFiles).mockRejectedValue('bad')
    renderPage()
    await enterStage(user, 'planner')
    expect(await screen.findByText('加载推理过程失败')).toBeInTheDocument()
  })

  it('后端 selected_session 会覆盖自动会话并触发二次拉取', async () => {
    const user = userEvent.setup()
    vi.mocked(reasoningApi.listReasoningPromptFiles).mockImplementation(async (params) =>
      makeListResponse({
        items: [plannerItem],
        selected_session: params.session === 'auto' ? 'sess-1' : params.session,
      })
    )
    renderPage()
    await enterStage(user, 'planner')
    await waitFor(() => {
      expect(lastFilesQuery()).toMatchObject({ session: 'sess-1' })
    })
  })

  it('刷新浏览列表，并在记录仍存在时保留选中', async () => {
    const user = userEvent.setup()
    renderPage()
    await enterStage(user, 'planner')
    await selectRecord('晚饭计划')
    expect(screen.getAllByText(/规划器\/测试群\/晚饭计划/).length).toBeGreaterThan(0)

    const callsBefore = vi.mocked(reasoningApi.listReasoningPromptFiles).mock.calls.length
    await user.click(screen.getByRole('button', { name: '刷新' }))
    await waitFor(() => {
      expect(reasoningApi.listReasoningPromptFiles.mock.calls.length).toBeGreaterThan(callsBefore)
    })
    expect(screen.getAllByText(/规划器\/测试群\/晚饭计划/).length).toBeGreaterThan(0)
  })

  it('筛选掉当前记录后取消选中', async () => {
    const user = userEvent.setup()
    renderPage()
    await enterStage(user, 'planner')
    await selectRecord('晚饭计划')

    fireEvent.change(screen.getByPlaceholderText('搜索会话、文件名、模型或记录摘要'), {
      target: { value: '午饭' },
    })
    await waitFor(() => {
      expect(screen.getAllByText('未选择记录').length).toBeGreaterThan(0)
    })
  })
})

describe('ReasoningProcessPage 复制导出与预览', () => {
  it('选中记录后复制优先使用文本内容', async () => {
    const writeText = vi.spyOn(navigator.clipboard, 'writeText').mockResolvedValue()
    const user = userEvent.setup()
    renderPage()
    await enterStage(user, 'planner')
    await selectRecord('晚饭计划')

    expect(screen.getByText('会话ID: sess-abc')).toBeInTheDocument()
    expect(screen.getByText('调用ID: call-1')).toBeInTheDocument()
    expect(screen.getByText('请求 Items')).toBeInTheDocument()
    expect(screen.getByText('规划结果')).toBeInTheDocument()
    expect(screen.getByText('工具定义 · 1 个')).toBeInTheDocument()
    expect(screen.getByText('这条记录没有 Provider 调用诊断。')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '复制' }))
    await waitFor(() => {
      expect(writeText).toHaveBeenCalledWith('完整文本 Prompt')
    })
    expect(toastMock).toHaveBeenCalledWith({
      title: '已复制完整 Prompt',
      description: expect.stringContaining('规划器/测试群/晚饭计划'),
    })
  })

  it('没有文本时复制结构化 Prompt；失败区分 Error 与非 Error', async () => {
    const writeText = vi.spyOn(navigator.clipboard, 'writeText').mockResolvedValue()
    const user = userEvent.setup()
    renderPage()
    await enterStage(user, 'planner')
    await selectRecord('午饭计划')

    // 午饭记录也有 txt，先改成复制失败
    writeText.mockRejectedValueOnce(new Error('denied'))
    await user.click(screen.getByRole('button', { name: '复制' }))
    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith({
        title: '复制失败',
        description: 'denied',
        variant: 'destructive',
      })
    })

    writeText.mockRejectedValueOnce('blocked')
    await user.click(screen.getByRole('button', { name: '复制' }))
    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith({
        title: '复制失败',
        description: '请手动选择文本复制',
        variant: 'destructive',
      })
    })
  })

  it('无 text_path 时用结构化文本复制', async () => {
    const writeText = vi.spyOn(navigator.clipboard, 'writeText').mockResolvedValue()
    vi.mocked(reasoningApi.listReasoningPromptFiles).mockResolvedValue(
      makeListResponse({
        items: [makeItem({ text_path: null, display_title: '无文本晚饭', stem: 'stem-notxt' })],
      })
    )
    const user = userEvent.setup()
    renderPage()
    await enterStage(user, 'planner')
    await selectRecord('无文本晚饭')

    await user.click(screen.getByRole('button', { name: '复制' }))
    await waitFor(() => {
      expect(writeText).toHaveBeenCalled()
    })
    const copied = writeText.mock.calls[0][0]
    expect(copied).toContain('[请求 Items]')
    expect(copied).toContain('UserMessageItem')
  })

  it('默认抹去昵称导出 JSON，并清洗文件名', async () => {
    const createObjectUrl = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:reasoning')
    const revokeObjectUrl = vi.spyOn(URL, 'revokeObjectURL')
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    vi.mocked(reasoningApi.listReasoningPromptFiles).mockResolvedValue(
      makeListResponse({
        items: [
          makeItem({
            display_title: '晚饭/计划',
            session_display_name: '测试 群',
          }),
        ],
      })
    )
    const user = userEvent.setup()
    renderPage()
    await enterStage(user, 'planner')
    await selectRecord('晚饭/计划')

    await user.click(screen.getByRole('button', { name: '导出' }))
    await user.click(screen.getByRole('button', { name: '下载 JSON' }))

    await waitFor(() => {
      expect(createObjectUrl).toHaveBeenCalledTimes(1)
    })
    const blob = createObjectUrl.mock.calls[0][0] as Blob
    const exported = JSON.parse(await blob.text()) as {
      users: Array<{ user_name: string }>
      special: { user: string }
    }
    const exportedText = JSON.stringify(exported)
    expect(exportedText).not.toContain('张三')
    expect(exportedText).not.toContain('群名片甲')
    expect(exportedText).not.toContain('李(四)')
    expect(exportedText).not.toContain('Name26')
    expect(exportedText).toContain('用户A')
    expect(exportedText).toContain('用户AA')
    expect(exported.special.user).toMatch(/^用户/)
    expect(exported.users[26].user_name).toMatch(/^用户/)

    const anchor = clickSpy.mock.contexts[0] as HTMLAnchorElement
    expect(anchor.download).toBe('reasoning-planner-测试_群-晚饭_计划-匿名.json')
    expect(revokeObjectUrl).toHaveBeenCalledWith('blob:reasoning')
    expect(toastMock).toHaveBeenCalledWith({
      title: '已导出推理过程',
      description: '已将昵称抹去为用户A、用户B等占位名',
    })
  })

  it('关闭抹去昵称后保留原名；无效 JSON 导出失败', async () => {
    const createObjectUrl = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:raw')
    const user = userEvent.setup()
    renderPage()
    await enterStage(user, 'planner')
    await selectRecord('晚饭计划')

    await user.click(screen.getByRole('button', { name: '导出' }))
    await user.click(screen.getByRole('switch', { name: '抹去昵称' }))
    await user.click(screen.getByRole('button', { name: '下载 JSON' }))

    const blob = createObjectUrl.mock.calls[0][0] as Blob
    await expect(blob.text()).resolves.toContain('张三')
    expect(toastMock).toHaveBeenCalledWith({
      title: '已导出推理过程',
      description: '已保留原始昵称',
    })
    cleanup()
    toastMock.mockClear()

    vi.mocked(reasoningApi.listReasoningPromptFiles).mockResolvedValue(
      makeListResponse({ items: [invalidJsonItem] })
    )
    renderPage()
    await enterStage(user, 'planner')
    await selectRecord('坏 JSON')
    expect(await screen.findByText('没有结构化内容')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '导出' }))
    await user.click(screen.getByRole('button', { name: '下载 JSON' }))
    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith({
        title: '导出失败',
        description: expect.any(String),
        variant: 'destructive',
      })
    })
  })

  it('纯文本 / 纯 HTML / HTML 预览分支，以及文本读取失败', async () => {
    const user = userEvent.setup()
    vi.mocked(reasoningApi.listReasoningPromptFiles).mockResolvedValue(
      makeListResponse({ items: [textOnlyItem, htmlOnlyItem] })
    )
    vi.mocked(reasoningApi.getReasoningPromptFile).mockImplementation(async (path) => {
      if (path.endsWith('plain.txt')) {
        throw new Error('文本损坏')
      }
      return makeFileContent(path, '完整文本 Prompt')
    })
    renderPage()
    await enterStage(user, 'planner')

    await user.click(await screen.findByRole('button', { name: /纯文本记录/ }))
    expect(await screen.findByText('文本损坏')).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: /文本/ })).toBeInTheDocument()
    expect(screen.queryByRole('tab', { name: /结构化/ })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '导出' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '重放' })).toBeDisabled()

    await user.click(screen.getByRole('button', { name: /纯 HTML 记录/ }))
    expect(await screen.findByTitle('推理过程 HTML 预览')).toHaveAttribute(
      'src',
      'http://html-preview/dinner.html'
    )
    expect(screen.getByRole('tab', { name: /HTML/ })).toBeInTheDocument()
  })

  it('开启头像拉取时解析 avatar_url；结构化读取失败回写到文本区', async () => {
    useAvatarFetchEnabledMock.mockReturnValue(true)
    const user = userEvent.setup()
    renderPage()
    await enterStage(user, 'planner')
    await selectRecord('晚饭计划')

    await waitFor(() => {
      expect(resolveApiPath).toHaveBeenCalledWith('/avatar/zhang.png')
    })
    cleanup()

    vi.mocked(reasoningApi.listReasoningPromptFiles).mockResolvedValue(
      makeListResponse({
        items: [makeItem({ text_path: null, display_title: 'JSON 失败' })],
      })
    )
    vi.mocked(reasoningApi.getReasoningPromptFile).mockRejectedValue('broken')
    renderPage()
    await enterStage(user, 'planner')
    await user.click(await screen.findByRole('button', { name: /JSON 失败/ }))
    // json 失败时结构化页显示空态；错误文案写入 textContent，复制时会用到
    expect(await screen.findByText('没有结构化内容')).toBeInTheDocument()
    const writeText = vi.spyOn(navigator.clipboard, 'writeText').mockResolvedValue()
    await user.click(screen.getByRole('button', { name: '复制' }))
    await waitFor(() => {
      expect(writeText).toHaveBeenCalledWith('读取结构化内容失败')
    })
  })
})

describe('ReasoningProcessPage 清空类型', () => {
  it('取消关闭确认框，确认后删除文件并提示', async () => {
    const user = userEvent.setup()
    renderPage()
    await screen.findByText('主流程')

    await user.click(screen.getByRole('button', { name: '清空规划器' }))
    expect(screen.getByText('清空推理过程记录')).toBeInTheDocument()
    expect(screen.getByText(/当前包含 2 个会话/)).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '取消' }))
    await waitFor(() => {
      expect(screen.queryByText('清空推理过程记录')).not.toBeInTheDocument()
    })
    expect(reasoningApi.clearReasoningPromptStage).not.toHaveBeenCalled()

    await user.click(screen.getByRole('button', { name: '清空规划器' }))
    await user.click(screen.getByRole('button', { name: '确认清空' }))
    await waitFor(() => {
      expect(reasoningApi.clearReasoningPromptStage).toHaveBeenCalledWith('planner')
    })
    expect(toastMock).toHaveBeenCalledWith({
      title: '已清空推理过程',
      description: '规划器：删除 4 个文件',
    })
  })

  it('清空当前浏览阶段会退回类型列表；失败区分 Error 与非 Error', async () => {
    const user = userEvent.setup()
    renderPage()
    await enterStage(user, 'planner')
    await screen.findByText('晚饭计划')
    await user.click(screen.getByRole('button', { name: '类型' }))
    await screen.findByText('主流程')

    await user.click(screen.getByRole('button', { name: '清空规划器' }))
    await user.click(screen.getByRole('button', { name: '确认清空' }))
    await waitFor(() => {
      expect(reasoningApi.clearReasoningPromptStage).toHaveBeenCalledWith('planner')
    })
    await waitFor(() => {
      expect(screen.queryByText('清空推理过程记录')).not.toBeInTheDocument()
    })

    vi.mocked(reasoningApi.clearReasoningPromptStage).mockRejectedValueOnce(new Error('磁盘只读'))
    await user.click(await screen.findByRole('button', { name: '清空回复器' }))
    await user.click(screen.getByRole('button', { name: '确认清空' }))
    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith({
        title: '清空失败',
        description: '磁盘只读',
        variant: 'destructive',
      })
    })
    // 失败时确认框保持打开，可再次确认
    expect(screen.getByText('清空推理过程记录')).toBeInTheDocument()

    vi.mocked(reasoningApi.clearReasoningPromptStage).mockRejectedValueOnce('nope')
    await user.click(screen.getByRole('button', { name: '确认清空' }))
    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith({
        title: '清空失败',
        description: '请稍后再试',
        variant: 'destructive',
      })
    })
  })

  it('清空进行中禁用确认按钮', async () => {
    let resolveClear: ((value: { stage: string; deleted_files: number }) => void) | undefined
    vi.mocked(reasoningApi.clearReasoningPromptStage).mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveClear = resolve
        })
    )
    const user = userEvent.setup()
    renderPage()
    await screen.findByText('主流程')
    await user.click(screen.getByRole('button', { name: '清空规划器' }))
    await user.click(screen.getByRole('button', { name: '确认清空' }))

    expect(screen.getByRole('button', { name: /确认清空/ })).toBeDisabled()
    expect(screen.getByRole('button', { name: '取消' })).toBeDisabled()
    resolveClear?.({ stage: 'planner', deleted_files: 1 })
    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({ title: '已清空推理过程' })
      )
    })
  })
})

describe('ReasoningProcessPage 重放 Items 与特殊预览', () => {
  it('打开重放后渲染可编辑 Items，支持添加删除并加载模型', async () => {
    const user = userEvent.setup()
    renderPage()
    await enterStage(user, 'planner')
    await selectRecord('晚饭计划')

    await user.click(screen.getByRole('button', { name: '重放' }))
    expect(await screen.findByText('编辑重放 Items')).toBeInTheDocument()
    expect(screen.getByText('2 个')).toBeInTheDocument()
    expect(screen.getByText('UserMessageItem')).toBeInTheDocument()
    expect(screen.getByText('SystemMessageItem')).toBeInTheDocument()
    expect(screen.getByText('重放推理请求')).toBeInTheDocument()
    expect(screen.getAllByText('正文').length).toBeGreaterThan(0)

    await waitFor(() => {
      expect(configApi.getModelConfig).toHaveBeenCalled()
    })

    await user.click(screen.getByRole('button', { name: '添加 Item' }))
    expect(screen.getByText('3 个')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '删除第 3 个 Item' })).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '删除第 1 个 Item' }))
    expect(screen.getByText('2 个')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '退出重放编辑' }))
    await waitFor(() => {
      expect(screen.queryByText('编辑重放 Items')).not.toBeInTheDocument()
    })
    expect(screen.getByRole('button', { name: '重放' })).toBeInTheDocument()
  })

  it('离开浏览会关闭重放面板', async () => {
    const user = userEvent.setup()
    renderPage()
    await enterStage(user, 'planner')
    await selectRecord('晚饭计划')
    await user.click(screen.getByRole('button', { name: '重放' }))
    await screen.findByText('编辑重放 Items')

    await user.click(screen.getByRole('button', { name: '类型' }))
    await screen.findByText('主流程')
    expect(screen.queryByText('编辑重放 Items')).not.toBeInTheDocument()
  })

  it('LLM 异常记录展示状态、摘要与无效时间原文', async () => {
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: /LLM 请求/ }))
    await enterStage(user, 'llm_error')
    await selectRecord('LLM 重试成功')

    expect(await screen.findByLabelText('LLM 请求异常详情')).toBeInTheDocument()
    expect(screen.getByText('重试后成功')).toBeInTheDocument()
    expect(screen.getByText('1 次尝试')).toBeInTheDocument()
    expect(screen.getByText('reply')).toBeInTheDocument()
    expect(screen.getByText('not-a-date')).toBeInTheDocument()
    expect(screen.getAllByText('gpt-error').length).toBeGreaterThan(0)
  })

  it('黑话含义推断多 JSON 会合并推断阶段，空阶段名回退 stage_N', async () => {
    const user = userEvent.setup()
    renderPage()
    await enterStage(user, 'jargon_learning_update')
    await selectRecord('离谱黑话')

    expect(await screen.findByText('#1 初轮推断')).toBeInTheDocument()
    expect(screen.getByText('#2 stage_2')).toBeInTheDocument()
    expect(screen.getByText('这是什么梗')).toBeInTheDocument()
    expect(screen.getByText('再推断一次')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '重放' })).toBeDisabled()
  })
})

describe('ReasoningProcessPage 深链与嵌入', () => {
  it('stage/session/stem 深链自动进入浏览并选中记录', async () => {
    setLocationSearch('stage=planner&session=sess-1&stem=stem-dinner')
    renderPage()

    await waitFor(() => {
      expect(reasoningApi.listReasoningPromptFiles).toHaveBeenCalled()
    })
    expect(vi.mocked(reasoningApi.listReasoningPromptFiles).mock.calls[0][0]).toMatchObject({
      stage: 'planner',
      session: 'sess-1',
      targetStem: 'stem-dinner',
    })
    expect((await screen.findAllByText(/规划器\/测试群\/晚饭计划/)).length).toBeGreaterThan(0)
    await waitFor(() => {
      expect(lastFilesQuery()).toMatchObject({ targetStem: '' })
    })
  })

  it('stem 精确匹配失败时回退到同名 stem', async () => {
    setLocationSearch('stage=planner&session=sess-1&stem=stem-fallback')
    vi.mocked(reasoningApi.listReasoningPromptFiles).mockResolvedValue(
      makeListResponse({
        items: [
          makeItem({
            stem: 'stem-fallback',
            session_id: 'sess-other',
            display_title: '回退记录',
            session_display_name: '其他群',
          }),
        ],
        selected_session: 'sess-1',
      })
    )
    renderPage()
    expect(
      (await screen.findAllByText('规划器/其他群/回退记录/qq/群聊/10001')).length
    ).toBeGreaterThan(0)
  })

  it('安全 returnTo 显示返回按钮，非法值忽略', async () => {
    const user = userEvent.setup()
    setLocationSearch(`returnTo=${encodeURIComponent('/monitor?tab=1#hash')}`)
    renderPage()
    await screen.findByText('主流程')

    await user.click(screen.getByRole('button', { name: '返回观察' }))
    expect(navigateMock).toHaveBeenCalledWith({ to: '/monitor?tab=1#hash' })
    cleanup()

    setLocationSearch('returnTo=//evil.example')
    renderPage()
    await screen.findByText('主流程')
    expect(screen.queryByRole('button', { name: '返回观察' })).not.toBeInTheDocument()
  })

  it('嵌入模式把刷新放入顶栏，浏览时在侧栏提供会话筛选', async () => {
    const onToolbarContentVisibleChange = vi.fn()
    const user = userEvent.setup()
    renderPage({ onToolbarContentVisibleChange }, { toolbar: true })

    const topbar = document.getElementById('rp-topbar')
    await waitFor(() => {
      expect(topbar && within(topbar).getByRole('button', { name: '刷新' })).toBeTruthy()
    })
    expect(onToolbarContentVisibleChange).toHaveBeenCalledWith(false)

    await enterStage(user, 'planner')
    await screen.findByText('晚饭计划')
    expect(onToolbarContentVisibleChange).toHaveBeenCalledWith(true)

    const sessionTrigger = screen.getByText('自动选择最近会话')
    await user.click(sessionTrigger)
    await user.click(await screen.findByRole('option', { name: /全部群聊/ }))
    await waitFor(() => {
      expect(lastFilesQuery()).toMatchObject({ session: '__all_group_chats__', page: 1 })
    })

    await user.click(screen.getByText('全部群聊'))
    await user.click(await screen.findByRole('option', { name: /测试群/ }))
    await waitFor(() => {
      expect(lastFilesQuery()).toMatchObject({ session: 'sess-1' })
    })
  })

  it('HTML 标签页在结构化记录上可用；无 html 地址时显示空态', async () => {
    const user = userEvent.setup()
    vi.mocked(reasoningApi.getReasoningPromptHtmlUrl).mockResolvedValue('')
    renderPage()
    await enterStage(user, 'planner')
    await selectRecord('晚饭计划')

    await user.click(screen.getByRole('tab', { name: /HTML/ }))
    expect(await screen.findByText('没有 HTML 预览')).toBeInTheDocument()
  })
})
