/**
 * ChatManagementPage 页面集成测试（特征化）。
 *
 * 2398 行巨页：只 mock 请求层（chat-management-api / config-api）、头像解析与 toast，
 * 用真实组件树验证聊天流列表（搜索/筛选/分页）、详情弹窗（适配器策略、发言频率、
 * 聊天 Prompt、学习配置）、删除聊天流确认流程以及共享组管理的可见行为与请求形状。
 */
import type { ReactNode } from 'react'

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ChatManagementPage } from '../chat-management'
import * as chatApi from '@/lib/chat-management-api'
import * as configApi from '@/lib/config-api'

import type {
  AdapterPolicyStatus,
  ChatAdapterStatus,
  ChatStream,
  ChatStreamDeleteResult,
  ChatStreamDetail,
  ChatTalkFrequencyRule,
} from '@/lib/chat-management-api'

// jsdom 未实现 Pointer Capture；时间轴拖拽依赖这三项才能进入 move/up 分支
const pointerCaptures = new WeakMap<EventTarget, Set<number>>()
Object.defineProperty(HTMLElement.prototype, 'hasPointerCapture', {
  configurable: true,
  value(this: HTMLElement, pointerId: number) {
    return pointerCaptures.get(this)?.has(pointerId) ?? false
  },
})
Object.defineProperty(HTMLElement.prototype, 'setPointerCapture', {
  configurable: true,
  value(this: HTMLElement, pointerId: number) {
    const ids = pointerCaptures.get(this) ?? new Set<number>()
    ids.add(pointerId)
    pointerCaptures.set(this, ids)
  },
})
Object.defineProperty(HTMLElement.prototype, 'releasePointerCapture', {
  configurable: true,
  value(this: HTMLElement, pointerId: number) {
    pointerCaptures.get(this)?.delete(pointerId)
  },
})

const toastMock = vi.fn()

vi.mock('@/hooks/use-toast', () => ({ useToast: () => ({ toast: toastMock }) }))

// 头像解析依赖 settings-manager 与 api-base 探测，这里固定为无头像，避免异步副作用
vi.mock('@/lib/avatar-url', () => ({ useResolvedAvatarUrl: () => undefined }))

vi.mock('@/lib/chat-management-api', () => ({
  deleteChatStream: vi.fn(),
  deleteChatStreamPrompt: vi.fn(),
  deleteChatStreamTalkFrequency: vi.fn(),
  getAdapterPolicyDefaults: vi.fn(),
  getChatStreamDetail: vi.fn(),
  getChatStreams: vi.fn(),
  updateAdapterPolicyDefaults: vi.fn(),
  updateChatStreamAdapterPolicy: vi.fn(),
  updateChatStreamLearning: vi.fn(),
  updateChatStreamTalkFrequency: vi.fn(),
  upsertChatStreamPrompt: vi.fn(),
}))

vi.mock('@/lib/config-api', () => ({
  getBotConfig: vi.fn(),
  updateBotConfigSection: vi.fn(),
}))

function makePolicy(overrides: Partial<AdapterPolicyStatus> = {}): AdapterPolicyStatus {
  return {
    allowed: true,
    configured: true,
    chat_type: 'group',
    target_id: '1',
    list_type: '',
    source: '',
    reason: '',
    matched_ids: [],
    ...overrides,
  }
}

/** 构造一个字段完整的聊天流（按需覆盖）。 */
function makeChat(id: number, overrides: Partial<ChatStream> = {}): ChatStream {
  return {
    id,
    session_id: `sess-${id}`,
    display_name: `聊天流${id}`,
    chat_type: 'group',
    target_id: `${10000 + id}`,
    platform: 'qq',
    account_id: null,
    scope: null,
    user_id: null,
    user_nickname: null,
    user_cardname: null,
    group_id: `${10000 + id}`,
    group_name: `聊天流${id}`,
    message_count: 10 * id,
    expression_count: id,
    jargon_count: 0,
    created_at: null,
    last_active_at: null,
    latest_message: '',
    latest_message_at: null,
    ...overrides,
  }
}

// 两条基础聊天流：群聊带账号后缀，私聊在 telegram 平台
const groupChat = makeChat(1, {
  display_name: '测试群',
  account_id: '123',
  target_id: '10001',
  group_id: '10001',
  message_count: 42,
  expression_count: 7,
  jargon_count: 3,
})
const privateChat = makeChat(2, {
  display_name: '小明的私聊',
  chat_type: 'private',
  platform: 'telegram',
  target_id: '20002',
  user_id: '20002',
  user_nickname: '小明',
  group_id: null,
  group_name: null,
})

function makeTalkRule(overrides: Partial<ChatTalkFrequencyRule> = {}): ChatTalkFrequencyRule {
  return {
    platform: 'qq',
    item_id: '10001',
    type: 'group',
    time: '08:00-12:00',
    value: 0.8,
    value_label: '0.8',
    target_priority: 2,
    time_priority: 1,
    time_active: true,
    is_effective: true,
    is_default_target: false,
    ...overrides,
  }
}

function makeAdapter(overrides: Partial<ChatAdapterStatus> = {}): ChatAdapterStatus {
  return {
    adapter_id: 'qq-adapter-1',
    plugin_id: 'plugin.qq',
    gateway_name: 'qq-gateway',
    platform: 'qq',
    account_id: '123',
    scope: null,
    protocol: 'onebot',
    route_type: 'default',
    send_bound: true,
    receive_bound: true,
    routed: true,
    ...overrides,
    policy: {
      allowed: true,
      configured: false,
      chat_type: 'group',
      target_id: '10001',
      list_type: '',
      source: '',
      reason: '',
      matched_ids: [],
      ...overrides.policy,
    },
  }
}

/** 构造完整的聊天流详情：含精确频率规则、默认规则、专属 Prompt 与适配器。 */
function makeDetail(overrides: Partial<ChatStreamDetail> = {}): ChatStreamDetail {
  return {
    session_id: 'sess-1',
    display_name: '测试群',
    chat_type: 'group',
    platform: 'qq',
    target_id: '10001',
    group_id: '10001',
    user_id: null,
    expression: {
      use: true,
      learn: true,
      matched_rule: { platform: 'qq', item_id: '10001', type: 'group' },
    },
    jargon: { use: false, learn: false, matched_rule: null },
    behavior: {
      use: true,
      learn: false,
      matched_rule: { platform: '', item_id: '', type: 'group', is_default: true },
    },
    talk_frequency: {
      enabled: true,
      base_value: 0.5,
      base_value_label: '0.5',
      effective_value: 0.8,
      effective_value_label: '0.8',
      current_time: '12:34',
      matched_rules: [
        makeTalkRule(),
        makeTalkRule({
          platform: '',
          item_id: '',
          type: '',
          time: '',
          value: 0.5,
          value_label: '0.5',
          target_priority: 0,
          time_priority: null,
          time_active: false,
          is_effective: false,
          is_default_target: true,
        }),
      ],
    },
    prompts: {
      base_prompt_type: 'group',
      base_prompt_title: '群聊基础 Prompt',
      base_prompt: '基础发言要求',
      chat_prompts: [
        { index: 0, platform: 'qq', item_id: '10001', rule_type: 'group', prompt: '已有专属要求' },
      ],
    },
    adapters: [makeAdapter()],
    ...overrides,
  }
}

function makeTimelineRect(width: number): DOMRect {
  return {
    x: 0,
    y: 0,
    top: 0,
    left: 0,
    right: width,
    bottom: 28,
    width,
    height: 28,
    toJSON: () => ({}),
  }
}

/** 删除结果：包含普通计数项、黑话（带解除关联）与零计数项（应被摘要过滤）。 */
function makeDeleteResult(): ChatStreamDeleteResult {
  return {
    success: true,
    session_id: 'sess-1',
    deleted_total: 7,
    items: [
      { key: 'messages', label: '消息', count: 5 },
      { key: 'jargons', label: '黑话', count: 2, unlinked: 1 },
      { key: 'expressions', label: '表达', count: 0 },
    ],
  }
}

/** Bot 配置：表达组含一个在册目标与一个找不到聊天流的目标；记忆开启全局共享。 */
function makeBotConfig(): Record<string, unknown> {
  return {
    expression: {
      enabled: true,
      expression_groups: [
        {
          targets: [
            { platform: 'qq', item_id: '10001', rule_type: 'group' },
            { platform: 'qq', item_id: '99999', rule_type: 'private' },
          ],
        },
      ],
    },
    jargon: { jargon_groups: [] },
    a_memorix: { global_memory_sharing_enabled: true, shared_memory_groups: [] },
  }
}

function makeWrapper() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
}

type TestUser = ReturnType<typeof userEvent.setup>

/** 打开“测试群”的详情弹窗并等待详情加载完成。 */
async function openDetail(user: TestUser): Promise<HTMLElement> {
  render(<ChatManagementPage />, { wrapper: makeWrapper() })
  await user.click(await screen.findByRole('button', { name: '查看 测试群 · 账号 123 详情' }))
  const dialog = await screen.findByRole('dialog')
  await within(dialog).findByText('Session ID')
  return dialog
}

/** 通过 URL 查询参数直达共享组视图。 */
async function renderGroupsView(query = '?view=groups') {
  window.history.replaceState(null, '', `/${query}`)
  render(<ChatManagementPage />, { wrapper: makeWrapper() })
  await screen.findByText('共享组管理')
}

/**
 * 等待编辑器挂载时 requestAnimationFrame 重置本地草稿落定，
 * 避免随后的输入被 rAF 回调覆盖（发言频率编辑器有此行为）。
 */
async function flushRafResets() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 40))
  })
}

beforeEach(() => {
  window.history.replaceState(null, '', '/')
  vi.mocked(chatApi.getChatStreams).mockResolvedValue([groupChat, privateChat])
  vi.mocked(chatApi.getChatStreamDetail).mockResolvedValue(makeDetail())
  vi.mocked(chatApi.getAdapterPolicyDefaults).mockResolvedValue({
    group: 'allow',
    private: 'allow',
  })
  vi.mocked(chatApi.updateAdapterPolicyDefaults).mockResolvedValue({
    group: 'allow',
    private: 'allow',
  })
  vi.mocked(chatApi.updateChatStreamLearning).mockResolvedValue(makeDetail())
  vi.mocked(chatApi.updateChatStreamAdapterPolicy).mockResolvedValue(makeDetail())
  vi.mocked(chatApi.updateChatStreamTalkFrequency).mockResolvedValue(makeDetail())
  vi.mocked(chatApi.deleteChatStreamTalkFrequency).mockResolvedValue(makeDetail())
  vi.mocked(chatApi.upsertChatStreamPrompt).mockResolvedValue(makeDetail())
  vi.mocked(chatApi.deleteChatStreamPrompt).mockResolvedValue(makeDetail())
  vi.mocked(chatApi.deleteChatStream).mockResolvedValue(makeDeleteResult())
  vi.mocked(configApi.getBotConfig).mockResolvedValue(makeBotConfig())
  vi.mocked(configApi.updateBotConfigSection).mockResolvedValue({})
})

afterEach(() => {
  cleanup()
  window.history.replaceState(null, '', '/')
})

describe('ChatManagementPage 聊天流列表', () => {
  it('初始加载：渲染统计卡、行数据与分页信息', async () => {
    render(<ChatManagementPage />, { wrapper: makeWrapper() })

    expect(await screen.findByText('显示 1-2 / 2 个聊天流')).toBeInTheDocument()
    expect(chatApi.getChatStreams).toHaveBeenCalledWith()

    // 顶部统计卡：全部 2、群聊 1、私聊 1
    const allLabel = screen.getByText('全部', { selector: 'div' })
    const statsGrid = allLabel.parentElement?.parentElement as HTMLElement
    expect(statsGrid).toHaveTextContent('全部2群聊1私聊1')

    // 群聊行：名称、账号后缀、平台、逻辑 ID、类型徽标、计数与空活跃时间
    const groupRow = screen.getByRole('button', { name: '查看 测试群 · 账号 123 详情' })
    expect(within(groupRow).getByText('测试群')).toBeInTheDocument()
    expect(within(groupRow).getByText('账号 123')).toBeInTheDocument()
    expect(within(groupRow).getByText('qq')).toBeInTheDocument()
    expect(within(groupRow).getByText('10001')).toBeInTheDocument()
    expect(within(groupRow).getByText('群聊')).toBeInTheDocument()
    expect(within(groupRow).getByText('42')).toBeInTheDocument()
    expect(within(groupRow).getByText('7')).toBeInTheDocument()
    expect(within(groupRow).getByText('3')).toBeInTheDocument()
    expect(within(groupRow).getByText('-')).toBeInTheDocument()

    // 私聊行：无账号后缀时 aria-label 不带分隔符
    const privateRow = screen.getByRole('button', { name: '查看 小明的私聊 详情' })
    expect(within(privateRow).getByText('私聊')).toBeInTheDocument()
    expect(within(privateRow).getByText('20002')).toBeInTheDocument()
  })

  it('加载中显示占位行', async () => {
    vi.mocked(chatApi.getChatStreams).mockImplementation(() => new Promise<never>(() => {}))
    render(<ChatManagementPage />, { wrapper: makeWrapper() })

    expect(await screen.findByText('正在加载聊天流...')).toBeInTheDocument()
  })

  it('列表加载失败显示错误行', async () => {
    vi.mocked(chatApi.getChatStreams).mockRejectedValue(new Error('boom'))
    render(<ChatManagementPage />, { wrapper: makeWrapper() })

    expect(await screen.findByText('加载聊天流失败')).toBeInTheDocument()
  })

  it('搜索按昵称过滤，清空后恢复全量', async () => {
    const user = userEvent.setup()
    render(<ChatManagementPage />, { wrapper: makeWrapper() })
    await screen.findByText('显示 1-2 / 2 个聊天流')

    const searchInput = screen.getByPlaceholderText('搜索名称、平台、用户、群号或会话 ID')
    await user.type(searchInput, '小明')
    expect(await screen.findByText('显示 1-1 / 1 个聊天流')).toBeInTheDocument()
    expect(screen.queryByText('测试群')).not.toBeInTheDocument()

    await user.clear(searchInput)
    expect(await screen.findByText('显示 1-2 / 2 个聊天流')).toBeInTheDocument()
  })

  it('类型页签在全部/私聊之间切换过滤', async () => {
    const user = userEvent.setup()
    render(<ChatManagementPage />, { wrapper: makeWrapper() })
    await screen.findByText('显示 1-2 / 2 个聊天流')

    await user.click(screen.getByRole('tab', { name: '私聊' }))
    expect(await screen.findByText('显示 1-1 / 1 个聊天流')).toBeInTheDocument()
    expect(screen.getByText('小明的私聊')).toBeInTheDocument()
    expect(screen.queryByText('测试群')).not.toBeInTheDocument()

    await user.click(screen.getByRole('tab', { name: '全部' }))
    expect(await screen.findByText('显示 1-2 / 2 个聊天流')).toBeInTheDocument()
  })

  it('分页：每页 10 条，翻页按钮与边界禁用状态正确', async () => {
    const user = userEvent.setup()
    vi.mocked(chatApi.getChatStreams).mockResolvedValue(
      Array.from({ length: 12 }, (_, index) => makeChat(index + 1))
    )
    render(<ChatManagementPage />, { wrapper: makeWrapper() })

    expect(await screen.findByText('显示 1-10 / 12 个聊天流')).toBeInTheDocument()
    expect(screen.getByText('1 / 2')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '第一页' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '上一页' })).toBeDisabled()

    await user.click(screen.getByRole('button', { name: '下一页' }))
    expect(await screen.findByText('显示 11-12 / 12 个聊天流')).toBeInTheDocument()
    expect(screen.getByText('聊天流11')).toBeInTheDocument()
    expect(screen.queryByText('聊天流1')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '下一页' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '最后一页' })).toBeDisabled()

    await user.click(screen.getByRole('button', { name: '第一页' }))
    expect(await screen.findByText('显示 1-10 / 12 个聊天流')).toBeInTheDocument()
  })
})

describe('ChatManagementPage 详情弹窗', () => {
  it('点击行打开详情：渲染基础信息、适配器、频率规则栈与 Prompt', async () => {
    const user = userEvent.setup()
    const dialog = await openDetail(user)

    expect(chatApi.getChatStreamDetail).toHaveBeenCalledWith('sess-1')
    const scope = within(dialog)
    expect(scope.getByText('sess-1')).toBeInTheDocument()

    // 适配器：显示名做了美化，未配置规则时回退默认放行文案
    expect(scope.getByText('Qq Gateway')).toBeInTheDocument()
    expect(scope.getByText('使用默认：允许')).toBeInTheDocument()
    expect(scope.getByText('未设置统一规则，主程序默认放行。')).toBeInTheDocument()
    expect(scope.getByText('已接入当前聊天，负责收消息、发消息；账号 123')).toBeInTheDocument()

    // 发言频率摘要：数值格式化为三位小数，当前时间原样展示
    expect(scope.getByText('0.500')).toBeInTheDocument()
    expect(scope.getByText('12:34')).toBeInTheDocument()
    // 规则栈：精确规则生效中，默认规则时间未命中
    expect(scope.getByText('生效中')).toBeInTheDocument()
    expect(scope.getByText('时间未命中')).toBeInTheDocument()
    expect(scope.getByText('优先级 2.1')).toBeInTheDocument()
    expect(scope.getByText('频率：0.800')).toBeInTheDocument()
    expect(scope.getByText('*:*:-')).toBeInTheDocument()
    expect(scope.getByText('时间：默认')).toBeInTheDocument()
    // 默认时间轴编辑模式：已有规则与新增规则各有一对拖拽手柄
    expect(scope.getByText('仅编辑 qq:10001:群聊 的精确规则。')).toBeInTheDocument()
    expect(scope.getAllByRole('button', { name: '调整开始时间' })).toHaveLength(2)

    // Prompt：基础 Prompt 与专属 Prompt
    expect(scope.getByText('群聊基础 Prompt')).toBeInTheDocument()
    expect(scope.getByText('基础发言要求')).toBeInTheDocument()
    expect(scope.getByDisplayValue('已有专属要求')).toBeInTheDocument()

    // 学习配置：三条规则的命中说明
    expect(scope.getByText('qq:10001:群聊')).toBeInTheDocument()
    expect(scope.getByText('未命中显式规则，使用默认行为')).toBeInTheDocument()
    expect(scope.getByText('默认规则')).toBeInTheDocument()
  })

  it('详情加载失败时弹窗内显示错误提示', async () => {
    const user = userEvent.setup()
    vi.mocked(chatApi.getChatStreamDetail).mockRejectedValue(new Error('detail boom'))
    render(<ChatManagementPage />, { wrapper: makeWrapper() })

    await user.click(await screen.findByRole('button', { name: '查看 测试群 · 账号 123 详情' }))
    const dialog = await screen.findByRole('dialog')
    expect(await within(dialog).findByText('加载详情失败')).toBeInTheDocument()
  })

  it('切换表达“使用”开关提交学习配置并提示保存成功', async () => {
    const user = userEvent.setup()
    const dialog = await openDetail(user)

    // 顺序：表达(使用/学习)、黑话(使用/学习)、行为(使用/学习)
    const checkboxes = within(dialog).getAllByRole('checkbox')
    expect(checkboxes).toHaveLength(6)

    await user.click(checkboxes[0])
    await waitFor(() =>
      expect(chatApi.updateChatStreamLearning).toHaveBeenCalledWith('sess-1', 'expression', {
        use: false,
        learn: true,
      })
    )
    await waitFor(() => expect(toastMock).toHaveBeenCalledWith({ title: '表达学习配置已保存' }))
  })

  it('学习配置保存失败时提示错误 toast', async () => {
    const user = userEvent.setup()
    vi.mocked(chatApi.updateChatStreamLearning).mockRejectedValue(new Error('后端拒绝'))
    const dialog = await openDetail(user)

    // 黑话“使用”当前为关闭，点击后应提交开启
    await user.click(within(dialog).getAllByRole('checkbox')[2])
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith({
        title: '黑话学习配置保存失败',
        description: '后端拒绝',
        variant: 'destructive',
      })
    )
    expect(chatApi.updateChatStreamLearning).toHaveBeenCalledWith('sess-1', 'jargon', {
      use: true,
      learn: false,
    })
  })

  it('适配器允许/阻止按钮提交对应的放行策略', async () => {
    const user = userEvent.setup()
    const dialog = await openDetail(user)

    await user.click(within(dialog).getByRole('button', { name: '允许' }))
    await waitFor(() =>
      expect(chatApi.updateChatStreamAdapterPolicy).toHaveBeenCalledWith('sess-1', {
        adapter_id: 'qq-adapter-1',
        action: 'allow',
      })
    )
    await waitFor(() => expect(toastMock).toHaveBeenCalledWith({ title: '适配器放行规则已保存' }))

    await user.click(within(dialog).getByRole('button', { name: '阻止' }))
    await waitFor(() =>
      expect(chatApi.updateChatStreamAdapterPolicy).toHaveBeenCalledWith('sess-1', {
        adapter_id: 'qq-adapter-1',
        action: 'block',
      })
    )
  })

  it('新增聊天流专属 Prompt：提交内容且不带 index', async () => {
    const user = userEvent.setup()
    const dialog = await openDetail(user)

    const textarea = within(dialog).getByPlaceholderText('只写这个聊天流额外需要遵守的发言要求。')
    await user.type(textarea, '新的专属要求')
    const editor = textarea.closest('.border-dashed') as HTMLElement
    await user.click(within(editor).getByRole('button', { name: '新增' }))

    await waitFor(() =>
      expect(chatApi.upsertChatStreamPrompt).toHaveBeenCalledWith('sess-1', {
        prompt: '新的专属要求',
      })
    )
    await waitFor(() => expect(toastMock).toHaveBeenCalledWith({ title: '聊天 Prompt 已新增' }))
  })

  it('编辑并保存已有 Prompt 后可删除：请求携带规则 index', async () => {
    const user = userEvent.setup()
    const dialog = await openDetail(user)

    const promptBox = within(dialog).getByDisplayValue('已有专属要求')
    await user.clear(promptBox)
    await user.type(promptBox, '更新后的要求')
    const editor = promptBox.parentElement as HTMLElement
    await user.click(within(editor).getByRole('button', { name: '保存' }))
    await waitFor(() =>
      expect(chatApi.upsertChatStreamPrompt).toHaveBeenCalledWith(
        'sess-1',
        { prompt: '更新后的要求' },
        0
      )
    )
    await waitFor(() => expect(toastMock).toHaveBeenCalledWith({ title: '聊天 Prompt 已保存' }))

    await user.click(within(dialog).getByRole('button', { name: '删除聊天 Prompt' }))
    await waitFor(() => expect(chatApi.deleteChatStreamPrompt).toHaveBeenCalledWith('sess-1', 0))
    await waitFor(() => expect(toastMock).toHaveBeenCalledWith({ title: '聊天 Prompt 已删除' }))
  })

  it('发言频率普通模式：保存已有规则、新增规则与删除规则', async () => {
    const user = userEvent.setup()
    const dialog = await openDetail(user)

    await user.click(within(dialog).getByRole('button', { name: '普通' }))
    await flushRafResets()

    // 保存已有规则：previous_time 指向原时间段
    const timeInput = within(dialog).getByDisplayValue('08:00-12:00')
    const existingEditor = timeInput.parentElement?.parentElement as HTMLElement
    await user.click(within(existingEditor).getByRole('button', { name: '保存' }))
    await waitFor(() =>
      expect(chatApi.updateChatStreamTalkFrequency).toHaveBeenCalledWith('sess-1', {
        previous_time: '08:00-12:00',
        time: '08:00-12:00',
        value: 0.8,
      })
    )
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith({
        title: '发言频率规则已保存',
        description: '已写入当前聊天流的精确动态频率规则。',
      })
    )

    // 新增规则：previous_time 为 null，默认频率取当前生效值
    const newTimeInput = within(dialog).getByDisplayValue('*')
    const newEditor = newTimeInput.parentElement?.parentElement as HTMLElement
    await user.clear(newTimeInput)
    await user.type(newTimeInput, '20:00-22:00')
    await user.click(within(newEditor).getByRole('button', { name: '新增' }))
    await waitFor(() =>
      expect(chatApi.updateChatStreamTalkFrequency).toHaveBeenCalledWith('sess-1', {
        previous_time: null,
        time: '20:00-22:00',
        value: 0.8,
      })
    )

    // 删除已有规则
    await user.click(
      within(dialog).getByRole('button', { name: '删除时间段 08:00-12:00 的发言频率规则' })
    )
    await waitFor(() =>
      expect(chatApi.deleteChatStreamTalkFrequency).toHaveBeenCalledWith('sess-1', '08:00-12:00')
    )
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith({
        title: '发言频率规则已删除',
        description: '已删除当前聊天流的这条精确规则。',
      })
    )
  })

  it('发言频率保存失败时提示错误 toast', async () => {
    const user = userEvent.setup()
    vi.mocked(chatApi.updateChatStreamTalkFrequency).mockRejectedValue(
      new Error('频率保存失败原因')
    )
    const dialog = await openDetail(user)

    await user.click(within(dialog).getByRole('button', { name: '普通' }))
    await flushRafResets()
    const timeInput = within(dialog).getByDisplayValue('08:00-12:00')
    const existingEditor = timeInput.parentElement?.parentElement as HTMLElement
    await user.click(within(existingEditor).getByRole('button', { name: '保存' }))

    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith({
        title: '保存发言频率失败',
        description: '频率保存失败原因',
        variant: 'destructive',
      })
    )
  })
})

describe('ChatManagementPage 删除聊天流', () => {
  it('确认输入 session_id 后执行删除并展示清理摘要', async () => {
    const user = userEvent.setup()
    const dialog = await openDetail(user)

    await user.click(within(dialog).getByRole('button', { name: '删除聊天流' }))
    const deleteDialog = (await screen.findByText('严肃确认：删除聊天流')).closest(
      '[role="dialog"]'
    ) as HTMLElement
    const confirmButton = within(deleteDialog).getByRole('button', { name: '永久删除' })
    expect(confirmButton).toBeDisabled()

    // 输入不匹配的 session_id 仍不可删除
    const confirmInput = within(deleteDialog).getByLabelText('请输入完整 session_id 以确认删除')
    await user.type(confirmInput, 'sess-wrong')
    expect(confirmButton).toBeDisabled()

    await user.clear(confirmInput)
    await user.type(confirmInput, 'sess-1')
    expect(confirmButton).toBeEnabled()
    await user.click(confirmButton)

    await waitFor(() => expect(chatApi.deleteChatStream).toHaveBeenCalledWith('sess-1'))
    expect(await within(deleteDialog).findByText('删除完成')).toBeInTheDocument()
    expect(within(deleteDialog).getByText('100%')).toBeInTheDocument()
    // 摘要过滤零计数项，黑话展示删除与解除关联两个数字
    expect(
      within(deleteDialog).getByText('消息 5 条；黑话 删除 2 条，解除关联 1 条')
    ).toBeInTheDocument()
    expect(toastMock).toHaveBeenCalledWith({
      title: '聊天流已删除',
      description: '消息 5 条；黑话 删除 2 条，解除关联 1 条',
    })
    // 删除成功后详情弹窗随之关闭
    await waitFor(() => expect(screen.queryByText('Session ID')).not.toBeInTheDocument())
  })

  it('删除失败时提示错误 toast 且详情弹窗保持打开', async () => {
    const user = userEvent.setup()
    vi.mocked(chatApi.deleteChatStream).mockRejectedValue(new Error('后端拒绝删除'))
    const dialog = await openDetail(user)

    await user.click(within(dialog).getByRole('button', { name: '删除聊天流' }))
    const deleteDialog = (await screen.findByText('严肃确认：删除聊天流')).closest(
      '[role="dialog"]'
    ) as HTMLElement
    const confirmInput = within(deleteDialog).getByLabelText('请输入完整 session_id 以确认删除')
    await user.type(confirmInput, 'sess-1')
    await user.click(within(deleteDialog).getByRole('button', { name: '永久删除' }))

    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith({
        title: '删除聊天流失败',
        description: '后端拒绝删除',
        variant: 'destructive',
      })
    )
    // 失败后确认弹窗与详情弹窗都还在
    expect(screen.getByText('严肃确认：删除聊天流')).toBeInTheDocument()
    expect(screen.getByText('Session ID')).toBeInTheDocument()
  })
})

describe('ChatManagementPage 共享组管理', () => {
  it('渲染共享组成员：在册聊天流显示名称，缺失目标显示未找到', async () => {
    await renderGroupsView()

    expect(await screen.findByText('共享组 1')).toBeInTheDocument()
    expect(screen.getByText('测试群 · 账号 123')).toBeInTheDocument()
    expect(screen.getByText('未找到聊天流')).toBeInTheDocument()
  })

  it('新建共享组：保留原组并追加空组写回表达配置', async () => {
    const user = userEvent.setup()
    await renderGroupsView()
    await screen.findByText('共享组 1')

    await user.click(screen.getByRole('button', { name: '新建共享组' }))
    await waitFor(() =>
      expect(configApi.updateBotConfigSection).toHaveBeenCalledWith('expression', {
        enabled: true,
        expression_groups: [
          {
            targets: [
              { platform: 'qq', item_id: '10001', rule_type: 'group' },
              { platform: 'qq', item_id: '99999', rule_type: 'private' },
            ],
          },
          { targets: [] },
        ],
      })
    )
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith({
        title: '共享组已保存',
        description: '表达共享组配置已更新。',
      })
    )
  })

  it('移除组内成员与删除整组分别写回收缩后的配置', async () => {
    const user = userEvent.setup()
    await renderGroupsView()
    await screen.findByText('共享组 1')

    await user.click(screen.getByRole('button', { name: '移除 测试群 · 账号 123' }))
    await waitFor(() =>
      expect(configApi.updateBotConfigSection).toHaveBeenCalledWith(
        'expression',
        expect.objectContaining({
          expression_groups: [
            { targets: [{ platform: 'qq', item_id: '99999', rule_type: 'private' }] },
          ],
        })
      )
    )

    await user.click(screen.getByRole('button', { name: '删除共享组 1' }))
    await waitFor(() =>
      expect(configApi.updateBotConfigSection).toHaveBeenCalledWith(
        'expression',
        expect.objectContaining({ expression_groups: [] })
      )
    )
  })

  it('添加聊天弹窗：过滤已在组内的聊天流，支持搜索与勾选加入', async () => {
    const user = userEvent.setup()
    await renderGroupsView()
    await screen.findByText('共享组 1')

    await user.click(screen.getByRole('button', { name: '添加聊天' }))
    const dialog = await screen.findByRole('dialog')
    // 已在组内的“测试群”不再出现在候选列表
    expect(within(dialog).queryByText('测试群 · 账号 123')).not.toBeInTheDocument()

    // 搜索无匹配时显示空态
    const searchInput = within(dialog).getByPlaceholderText('搜索名称、平台、用户、群号或会话 ID')
    await user.type(searchInput, '不存在的关键词')
    expect(await within(dialog).findByText('没有可加入的聊天流')).toBeInTheDocument()
    await user.clear(searchInput)

    await user.click(await within(dialog).findByRole('checkbox', { name: '选择 小明的私聊' }))
    await user.click(within(dialog).getByRole('button', { name: '加入 1 个聊天' }))

    await waitFor(() =>
      expect(configApi.updateBotConfigSection).toHaveBeenCalledWith('expression', {
        enabled: true,
        expression_groups: [
          {
            targets: [
              { platform: 'qq', item_id: '10001', rule_type: 'group' },
              { platform: 'qq', item_id: '99999', rule_type: 'private' },
              { platform: 'telegram', item_id: '20002', rule_type: 'private' },
            ],
          },
        ],
      })
    )
    // 提交后弹窗关闭
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })

  it('记忆共享组在开启全局共享时禁止编辑', async () => {
    const user = userEvent.setup()
    await renderGroupsView()
    await screen.findByText('共享组 1')

    await user.click(screen.getByRole('button', { name: '记忆' }))
    expect(
      await screen.findByText('全局共享记忆已开启，记忆共享组暂不参与普通记忆检索范围控制。')
    ).toBeInTheDocument()
    expect(screen.getByText('暂无记忆共享组。')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '新建共享组' })).toBeDisabled()
    expect(configApi.updateBotConfigSection).not.toHaveBeenCalled()
  })

  it('URL kind=jargon 时初始进入黑话共享组视图', async () => {
    await renderGroupsView('?view=groups&kind=jargon')

    expect(await screen.findByText('暂无黑话共享组。')).toBeInTheDocument()
  })

  it('共享组配置加载失败时显示错误提示', async () => {
    vi.mocked(configApi.getBotConfig).mockRejectedValue(new Error('config boom'))
    await renderGroupsView()

    expect(await screen.findByText('加载共享组失败')).toBeInTheDocument()
  })
})

describe('ChatManagementPage 筛选与空态', () => {
  it('空列表与无匹配搜索显示空态文案', async () => {
    const user = userEvent.setup()
    vi.mocked(chatApi.getChatStreams).mockResolvedValue([])
    render(<ChatManagementPage />, { wrapper: makeWrapper() })

    expect(await screen.findByText('暂无匹配的聊天流')).toBeInTheDocument()
    expect(screen.getByText('显示 0-0 / 0 个聊天流')).toBeInTheDocument()

    vi.mocked(chatApi.getChatStreams).mockResolvedValue([groupChat, privateChat])
    await user.click(screen.getByRole('button', { name: '刷新' }))
    expect(await screen.findByText('显示 1-2 / 2 个聊天流')).toBeInTheDocument()

    await user.type(screen.getByPlaceholderText('搜索名称、平台、用户、群号或会话 ID'), '不存在的关键词')
    expect(await screen.findByText('暂无匹配的聊天流')).toBeInTheDocument()
    expect(screen.getByText('显示 0-0 / 0 个聊天流')).toBeInTheDocument()
  })

  it('群聊页签、多字段搜索、逻辑 ID 回退与活跃时间格式化', async () => {
    const user = userEvent.setup()
    const activeAt = 1_710_000_000
    const expectedTime = new Intl.DateTimeFormat('zh-CN', {
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    }).format(new Date(activeAt * 1000))
    vi.mocked(chatApi.getChatStreams).mockResolvedValue([
      { ...groupChat, last_active_at: activeAt },
      {
        ...privateChat,
        target_id: '',
        user_cardname: '特殊卡片',
        last_active_at: 0,
      },
      makeChat(3, {
        display_name: '无target群',
        target_id: '',
        group_id: 'gid-3',
        account_id: null,
      }),
      makeChat(4, {
        display_name: '全空ID',
        chat_type: 'private',
        target_id: '',
        group_id: null,
        user_id: null,
      }),
    ])
    render(<ChatManagementPage />, { wrapper: makeWrapper() })
    await screen.findByText('显示 1-4 / 4 个聊天流')

    expect(screen.getByText(expectedTime)).toBeInTheDocument()
    expect(screen.getByText('gid-3')).toBeInTheDocument()
    const emptyIdRow = screen.getByRole('button', { name: '查看 全空ID 详情' })
    expect(within(emptyIdRow).getAllByText('-').length).toBeGreaterThan(0)

    await user.click(screen.getByRole('tab', { name: '群聊' }))
    expect(await screen.findByText('显示 1-2 / 2 个聊天流')).toBeInTheDocument()
    expect(screen.getByText('测试群')).toBeInTheDocument()
    expect(screen.queryByText('小明的私聊')).not.toBeInTheDocument()

    await user.click(screen.getByRole('tab', { name: '全部' }))
    const searchInput = screen.getByPlaceholderText('搜索名称、平台、用户、群号或会话 ID')
    await user.type(searchInput, 'sess-2')
    expect(await screen.findByText('显示 1-1 / 1 个聊天流')).toBeInTheDocument()
    expect(screen.getByText('小明的私聊')).toBeInTheDocument()

    await user.clear(searchInput)
    await user.type(searchInput, '特殊卡片')
    expect(await screen.findByText('显示 1-1 / 1 个聊天流')).toBeInTheDocument()

    await user.clear(searchInput)
    await user.type(searchInput, '   ')
    expect(await screen.findByText('显示 1-4 / 4 个聊天流')).toBeInTheDocument()
  })

  it('筛选会把分页重置回第一页，最后一页与上一页可用', async () => {
    const user = userEvent.setup()
    vi.mocked(chatApi.getChatStreams).mockResolvedValue(
      Array.from({ length: 12 }, (_, index) => makeChat(index + 1))
    )
    render(<ChatManagementPage />, { wrapper: makeWrapper() })
    await screen.findByText('显示 1-10 / 12 个聊天流')

    await user.click(screen.getByRole('button', { name: '最后一页' }))
    expect(await screen.findByText('显示 11-12 / 12 个聊天流')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '上一页' }))
    expect(await screen.findByText('显示 1-10 / 12 个聊天流')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '下一页' }))
    expect(await screen.findByText('显示 11-12 / 12 个聊天流')).toBeInTheDocument()
    await user.type(screen.getByPlaceholderText('搜索名称、平台、用户、群号或会话 ID'), '聊天流12')
    expect(await screen.findByText('显示 1-1 / 1 个聊天流')).toBeInTheDocument()
    expect(screen.getByText('1 / 1')).toBeInTheDocument()
  })

  it('视图切换、键盘 Enter/Space 打开详情', async () => {
    const user = userEvent.setup()
    render(<ChatManagementPage />, { wrapper: makeWrapper() })
    await screen.findByText('显示 1-2 / 2 个聊天流')

    await user.click(screen.getByRole('tab', { name: '共享组' }))
    expect(await screen.findByText('共享组管理')).toBeInTheDocument()
    await user.click(screen.getByRole('tab', { name: '聊天流' }))
    expect(await screen.findByText('显示 1-2 / 2 个聊天流')).toBeInTheDocument()

    const groupRow = screen.getByRole('button', { name: '查看 测试群 · 账号 123 详情' })
    fireEvent.keyDown(groupRow, { key: 'Enter' })
    expect(await screen.findByText('Session ID')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '关闭' }))
    await waitFor(() => expect(screen.queryByText('Session ID')).not.toBeInTheDocument())

    fireEvent.keyDown(groupRow, { key: ' ' })
    expect(await screen.findByText('Session ID')).toBeInTheDocument()
  })
})

describe('ChatManagementPage 时间轴规则', () => {
  it('拖动手柄调用时间换算并在保存时提交新时间段', async () => {
    const user = userEvent.setup()
    const dialog = await openDetail(user)
    await flushRafResets()

    const startHandle = within(dialog).getAllByRole('button', { name: '调整开始时间' })[0]
    const endHandle = within(dialog).getAllByRole('button', { name: '调整结束时间' })[0]
    const track = startHandle.closest('[data-chat-talk-timeline-track]') as HTMLElement
    const labelRow = track.nextElementSibling as HTMLElement
    const rectSpy = vi.spyOn(track, 'getBoundingClientRect')

    // width=0 走 getTimelineMinuteFromClient 的零宽回退，得到 00:00
    rectSpy.mockReturnValue(makeTimelineRect(0))
    fireEvent.pointerDown(startHandle, { pointerId: 1, clientX: 80, clientY: 10 })
    expect(labelRow).toHaveTextContent('00:00')
    expect(labelRow).toHaveTextContent('12:00')

    // 240px 轨道上 x=120 对应正午，formatTalkTimeRange 写成 12:00-12:00
    rectSpy.mockReturnValue(makeTimelineRect(240))
    fireEvent.pointerMove(startHandle, { pointerId: 1, clientX: 120, clientY: 10 })
    expect(labelRow.textContent).toContain('12:00')
    fireEvent.pointerUp(startHandle, { pointerId: 1 })
    // 释放后的 move 不应再改时间
    fireEvent.pointerMove(startHandle, { pointerId: 1, clientX: 200, clientY: 10 })

    fireEvent.pointerDown(endHandle, { pointerId: 2, clientX: 180, clientY: 10 })
    fireEvent.pointerMove(endHandle, { pointerId: 2, clientX: 180, clientY: 10 })
    fireEvent.pointerUp(endHandle, { pointerId: 2 })
    expect(labelRow).toHaveTextContent('18:00')

    await user.click(within(dialog).getAllByRole('button', { name: '保存' }).find((button) => !button.hasAttribute('disabled'))!)
    await waitFor(() =>
      expect(chatApi.updateChatStreamTalkFrequency).toHaveBeenCalledWith('sess-1', {
        previous_time: '08:00-12:00',
        time: '12:00-18:00',
        value: 0.8,
      })
    )
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith({
        title: '发言频率规则已保存',
        description: '已写入当前聊天流的精确动态频率规则。',
      })
    )
  })

  it('时间轴新增、删除与失败 toast', async () => {
    const user = userEvent.setup()
    const dialog = await openDetail(user)
    await flushRafResets()

    const newRuleBlock = within(dialog).getByText('新增规则').parentElement as HTMLElement
    await user.click(within(newRuleBlock).getByRole('button', { name: '新增' }))
    await waitFor(() =>
      expect(chatApi.updateChatStreamTalkFrequency).toHaveBeenCalledWith('sess-1', {
        previous_time: null,
        time: '00:00-23:59',
        value: 0.8,
      })
    )
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith({
        title: '发言频率规则已新增',
        description: '已写入当前聊天流的精确动态频率规则。',
      })
    )

    await user.click(
      within(dialog).getByRole('button', { name: '删除时间段 08:00-12:00 的发言频率规则' })
    )
    await waitFor(() =>
      expect(chatApi.deleteChatStreamTalkFrequency).toHaveBeenCalledWith('sess-1', '08:00-12:00')
    )

    vi.mocked(chatApi.updateChatStreamTalkFrequency).mockRejectedValue(new Error('轴保存失败'))
    vi.mocked(chatApi.deleteChatStreamTalkFrequency).mockRejectedValue('轴删除失败')
    const saveButton = within(dialog)
      .getAllByRole('button', { name: '保存' })
      .find((button) => !button.hasAttribute('disabled'))
    await user.click(saveButton!)
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith({
        title: '保存发言频率失败',
        description: '轴保存失败',
        variant: 'destructive',
      })
    )
    await user.click(
      within(dialog).getByRole('button', { name: '删除时间段 08:00-12:00 的发言频率规则' })
    )
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith({
        title: '删除发言频率规则失败',
        description: '请稍后重试',
        variant: 'destructive',
      })
    )
  })

  it('无精确规则、跨夜分段、非法时间与通配时间', async () => {
    const user = userEvent.setup()
    vi.mocked(chatApi.getChatStreamDetail).mockResolvedValue(
      makeDetail({
        talk_frequency: {
          enabled: false,
          base_value: 0.5,
          base_value_label: '中等',
          effective_value: 0.3,
          effective_value_label: '偏低',
          current_time: '03:00',
          matched_rules: [
            makeTalkRule({
              time: '22:00-02:00',
              value: 0.3,
              value_label: '偏低',
              is_effective: false,
              time_active: true,
            }),
            makeTalkRule({
              time: '10:00-11:00',
              value: 0.8,
              value_label: '0.8',
              is_effective: false,
              time_active: true,
            }),
            makeTalkRule({
              time: '*',
              value: 0.5,
              value_label: '中等',
              is_effective: false,
              time_active: false,
            }),
            makeTalkRule({
              time: 'bad-range',
              value: 0.9,
              value_label: '偏高',
              is_effective: false,
              time_active: true,
            }),
            makeTalkRule({
              time: '08:00-12:00-13:00',
              value: 0.6,
              value_label: '0.6',
              is_effective: false,
              time_active: true,
            }),
          ],
        },
      })
    )
    const dialog = await openDetail(user)
    const scope = within(dialog)
    const freqSection = scope.getByText('发言频率规则').closest('section') as HTMLElement

    expect(within(freqSection).getByText('关闭')).toBeInTheDocument()
    expect(scope.getByText('中等')).toBeInTheDocument()
    expect(scope.getByText('偏低')).toBeInTheDocument()
    expect(scope.getByText('bad-range')).toBeInTheDocument()
    expect(scope.getByText('08:00-12:00-13:00')).toBeInTheDocument()

    const tracks = dialog.querySelectorAll('[data-chat-talk-timeline-track]')
    expect(tracks[0].querySelectorAll('[class*="bg-sky-500"]')).toHaveLength(2)
    expect(tracks[1].querySelectorAll('[class*="bg-emerald-500"]').length).toBeGreaterThan(0)
    expect(tracks[2].querySelectorAll('[class*="bg-amber-500"]').length).toBeGreaterThan(0)
    expect(scope.getAllByRole('button', { name: '调整开始时间' })).toHaveLength(3)

    vi.mocked(chatApi.getChatStreamDetail).mockResolvedValue(
      makeDetail({
        talk_frequency: {
          enabled: true,
          base_value: 0.5,
          base_value_label: '0.5',
          effective_value: 0.8,
          effective_value_label: '0.8',
          current_time: '12:34',
          matched_rules: [],
        },
      })
    )
    await user.click(scope.getByRole('button', { name: '关闭' }))
    await user.click(await screen.findByRole('button', { name: '查看 小明的私聊 详情' }))
    const emptyDialog = await screen.findByRole('dialog')
    expect(
      await within(emptyDialog).findByText('没有可应用的动态发言频率规则，使用默认频率。')
    ).toBeInTheDocument()
    expect(within(emptyDialog).getByText('当前聊天流还没有专属发言频率规则。')).toBeInTheDocument()

    await user.click(within(emptyDialog).getByRole('button', { name: '普通' }))
    expect(within(emptyDialog).getByText('当前聊天流还没有专属发言频率规则。')).toBeInTheDocument()
  })
})

describe('ChatManagementPage 详情空态与错误', () => {
  it('默认策略加载中按钮禁用', async () => {
    vi.mocked(chatApi.getAdapterPolicyDefaults).mockImplementation(() => new Promise(() => {}))
    const user = userEvent.setup()
    const dialog = await openDetail(user)
    expect(within(dialog).getAllByRole('button', { name: '放行' })[0]).toBeDisabled()
  })

  it('适配器默认策略保存、继承与失败 toast', async () => {
    const user = userEvent.setup()
    vi.mocked(chatApi.getAdapterPolicyDefaults).mockResolvedValue({
      group: 'allow',
      private: 'block',
    })
    const dialog = await openDetail(user)

    await user.click(within(dialog).getAllByRole('button', { name: '拒绝' })[0])
    await waitFor(() =>
      expect(vi.mocked(chatApi.updateAdapterPolicyDefaults).mock.calls[0]?.[0]).toEqual({
        group: 'block',
        private: 'block',
      })
    )
    await waitFor(() => expect(toastMock).toHaveBeenCalledWith({ title: '适配器默认策略已保存' }))

    await user.click(within(dialog).getByRole('button', { name: '使用默认' }))
    await waitFor(() =>
      expect(chatApi.updateChatStreamAdapterPolicy).toHaveBeenCalledWith('sess-1', {
        adapter_id: 'qq-adapter-1',
        action: 'inherit',
      })
    )

    vi.mocked(chatApi.updateAdapterPolicyDefaults).mockRejectedValue(new Error('默认策略失败'))
    vi.mocked(chatApi.updateChatStreamAdapterPolicy).mockRejectedValue('策略字符串错误')
    await user.click(within(dialog).getAllByRole('button', { name: '放行' })[1])
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith({
        title: '适配器默认策略保存失败',
        description: '默认策略失败',
        variant: 'destructive',
      })
    )
    await user.click(within(dialog).getByRole('button', { name: '允许' }))
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith({
        title: '适配器放行规则保存失败',
        description: '请稍后重试',
        variant: 'destructive',
      })
    )
  })

  it('适配器策略标签、路由文案与显示名回退', async () => {
    const user = userEvent.setup()
    vi.mocked(chatApi.getChatStreamDetail).mockResolvedValue(
      makeDetail({
        adapters: [
          makeAdapter({
            adapter_id: 'allow-1',
            gateway_name: 'onebot.gateway',
            policy: makePolicy({ reason: 'matched_allow_override', configured: true, allowed: true }),
          }),
          makeAdapter({
            adapter_id: 'deny-1',
            gateway_name: 'deny-gw',
            policy: makePolicy({ reason: 'matched_deny_override', configured: true, allowed: false }),
          }),
          makeAdapter({
            adapter_id: 'bl-miss',
            gateway_name: 'list-gw',
            policy: makePolicy({
              configured: true,
              allowed: true,
              list_type: 'blacklist',
              source: 'defaults',
            }),
          }),
          makeAdapter({
            adapter_id: 'wl-pass',
            gateway_name: 'wl-gw',
            policy: makePolicy({
              configured: true,
              allowed: true,
              list_type: 'whitelist',
              source: 'adapter',
            }),
          }),
          makeAdapter({
            adapter_id: 'bl-hit',
            gateway_name: 'bl-gw',
            policy: makePolicy({
              configured: true,
              allowed: false,
              list_type: 'blacklist',
              source: 'defaults',
            }),
          }),
          makeAdapter({
            adapter_id: 'wl-miss',
            gateway_name: 'wl-miss-gw',
            policy: makePolicy({
              configured: true,
              allowed: false,
              list_type: 'whitelist',
              source: 'adapter',
            }),
          }),
          makeAdapter({
            adapter_id: 'adapter',
            plugin_id: '',
            gateway_name: '',
            account_id: null,
            scope: 'guild',
            routed: false,
            send_bound: false,
            receive_bound: false,
            policy: makePolicy({ configured: false, allowed: false }),
          }),
          makeAdapter({
            adapter_id: 'recv-only',
            plugin_id: 'plugin.foo-adapter',
            gateway_name: '',
            routed: true,
            send_bound: false,
            receive_bound: true,
            account_id: null,
            policy: makePolicy({ configured: false, allowed: true }),
          }),
        ],
      })
    )
    const dialog = await openDetail(user)
    const scope = within(dialog)

    expect(scope.getByText('已允许当前聊天')).toBeInTheDocument()
    expect(scope.getByText('这条聊天已被单独加入允许规则。')).toBeInTheDocument()
    expect(scope.getByText('已阻止当前聊天')).toBeInTheDocument()
    expect(scope.getByText('这条聊天已被单独加入阻止规则。')).toBeInTheDocument()
    expect(scope.getByText('黑名单未命中')).toBeInTheDocument()
    expect(scope.getByText('当前聊天被全局适配器规则放行。')).toBeInTheDocument()
    expect(scope.getByText('白名单已放行')).toBeInTheDocument()
    expect(scope.getByText('当前聊天被这个适配器的规则放行。')).toBeInTheDocument()
    expect(scope.getByText('黑名单已阻止')).toBeInTheDocument()
    expect(scope.getByText('当前聊天被全局适配器规则阻止。')).toBeInTheDocument()
    expect(scope.getByText('白名单未放行')).toBeInTheDocument()
    expect(scope.getByText('当前聊天被这个适配器的规则阻止。')).toBeInTheDocument()
    expect(scope.getByText('使用默认：拒绝')).toBeInTheDocument()
    expect(scope.getByText('未设置统一规则，主程序默认拒绝。')).toBeInTheDocument()
    expect(scope.getByText('未接入当前聊天')).toBeInTheDocument()
    expect(scope.getByText('适配器')).toBeInTheDocument()
    expect(scope.getByText('已接入当前聊天，负责收消息')).toBeInTheDocument()
    expect(scope.getByText('Foo')).toBeInTheDocument()
    expect(scope.getByText(/范围：guild/)).toBeInTheDocument()
  })

  it('无适配器、空 Prompt、缺失学习行', async () => {
    const user = userEvent.setup()
    vi.mocked(chatApi.getChatStreamDetail).mockResolvedValue(
      makeDetail({
        adapters: [],
        behavior: undefined,
        prompts: {
          base_prompt_type: 'group',
          base_prompt_title: '群聊基础 Prompt',
          base_prompt: '   ',
          chat_prompts: [],
        },
      })
    )
    const dialog = await openDetail(user)
    const scope = within(dialog)

    expect(scope.getByText('当前没有运行中的适配器插件路由。')).toBeInTheDocument()
    expect(scope.getByText('当前基础 Prompt 为空。')).toBeInTheDocument()
    expect(scope.getByText('当前聊天流没有专属额外 Prompt。')).toBeInTheDocument()
    expect(scope.queryByText('行为')).not.toBeInTheDocument()
    expect(scope.getByText('0 个')).toBeInTheDocument()
  })

  it('Prompt 保存、删除、新增失败分别提示', async () => {
    const user = userEvent.setup()
    vi.mocked(chatApi.upsertChatStreamPrompt).mockRejectedValue(new Error('prompt 写失败'))
    vi.mocked(chatApi.deleteChatStreamPrompt).mockRejectedValue('prompt 删失败')
    const dialog = await openDetail(user)

    const promptBox = within(dialog).getByDisplayValue('已有专属要求')
    await user.clear(promptBox)
    await user.type(promptBox, '改过的要求')
    await user.click(within(promptBox.parentElement as HTMLElement).getByRole('button', { name: '保存' }))
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith({
        title: '聊天 Prompt 保存失败',
        description: 'prompt 写失败',
        variant: 'destructive',
      })
    )

    await user.click(within(dialog).getByRole('button', { name: '删除聊天 Prompt' }))
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith({
        title: '聊天 Prompt 删除失败',
        description: '请稍后重试',
        variant: 'destructive',
      })
    )

    const textarea = within(dialog).getByPlaceholderText('只写这个聊天流额外需要遵守的发言要求。')
    await user.type(textarea, '新失败要求')
    const editor = textarea.closest('.border-dashed') as HTMLElement
    await user.click(within(editor).getByRole('button', { name: '新增' }))
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith({
        title: '聊天 Prompt 新增失败',
        description: 'prompt 写失败',
        variant: 'destructive',
      })
    )
  })

  it('删除摘要为空、取消删除与非 Error 失败', async () => {
    const user = userEvent.setup()
    vi.mocked(chatApi.deleteChatStream).mockResolvedValue({
      success: true,
      session_id: 'sess-1',
      deleted_total: 2,
      items: [
        { key: 'messages', label: '消息', count: 0 },
        { key: 'jargons', label: '黑话', count: 2 },
      ],
    })
    const dialog = await openDetail(user)

    await user.click(within(dialog).getByRole('button', { name: '删除聊天流' }))
    let deleteDialog = (await screen.findByText('严肃确认：删除聊天流')).closest(
      '[role="dialog"]'
    ) as HTMLElement
    await user.click(within(deleteDialog).getByRole('button', { name: '取消' }))
    await waitFor(() => expect(screen.queryByText('严肃确认：删除聊天流')).not.toBeInTheDocument())
    expect(screen.getByText('Session ID')).toBeInTheDocument()

    await user.click(within(dialog).getByRole('button', { name: '删除聊天流' }))
    deleteDialog = (await screen.findByText('严肃确认：删除聊天流')).closest(
      '[role="dialog"]'
    ) as HTMLElement
    await user.type(
      within(deleteDialog).getByLabelText('请输入完整 session_id 以确认删除'),
      'sess-1'
    )
    await user.click(within(deleteDialog).getByRole('button', { name: '永久删除' }))
    expect(await within(deleteDialog).findByText('黑话 删除 2 条，解除关联 0 条')).toBeInTheDocument()
    expect(toastMock).toHaveBeenCalledWith({
      title: '聊天流已删除',
      description: '黑话 删除 2 条，解除关联 0 条',
    })
  })

  it('学习配置切换学习开关，非 Error 失败使用回退文案', async () => {
    const user = userEvent.setup()
    vi.mocked(chatApi.updateChatStreamLearning).mockRejectedValue('plain-fail')
    const dialog = await openDetail(user)

    await user.click(within(dialog).getAllByRole('checkbox')[1])
    await waitFor(() =>
      expect(chatApi.updateChatStreamLearning).toHaveBeenCalledWith('sess-1', 'expression', {
        use: true,
        learn: false,
      })
    )
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith({
        title: '表达学习配置保存失败',
        description: '请稍后重试',
        variant: 'destructive',
      })
    )
  })

  it('普通模式删除失败与频率钳制', async () => {
    const user = userEvent.setup()
    vi.mocked(chatApi.deleteChatStreamTalkFrequency).mockRejectedValue(new Error('删规则失败'))
    const dialog = await openDetail(user)
    await user.click(within(dialog).getByRole('button', { name: '普通' }))
    await flushRafResets()

    const timeInput = within(dialog).getByDisplayValue('08:00-12:00')
    const existingEditor = timeInput.parentElement?.parentElement as HTMLElement
    const valueInput = within(existingEditor).getByRole('spinbutton')
    await user.clear(valueInput)
    await user.type(valueInput, '2')
    await user.click(within(existingEditor).getByRole('button', { name: '保存' }))
    await waitFor(() =>
      expect(chatApi.updateChatStreamTalkFrequency).toHaveBeenCalledWith('sess-1', {
        previous_time: '08:00-12:00',
        time: '08:00-12:00',
        value: 1,
      })
    )

    await user.click(
      within(dialog).getByRole('button', { name: '删除时间段 08:00-12:00 的发言频率规则' })
    )
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith({
        title: '删除发言频率规则失败',
        description: '删规则失败',
        variant: 'destructive',
      })
    )
  })
})

describe('ChatManagementPage 共享组批量与边界', () => {
  it('添加弹窗限制 50 条，可反选后批量加入多个聊天', async () => {
    const user = userEvent.setup()
    const extraChats = Array.from({ length: 51 }, (_, index) =>
      makeChat(index + 3, { display_name: `批量聊天${index + 3}` })
    )
    vi.mocked(chatApi.getChatStreams).mockResolvedValue([groupChat, privateChat, ...extraChats])
    await renderGroupsView()
    await screen.findByText('共享组 1')

    await user.click(screen.getByRole('button', { name: '添加聊天' }))
    const dialog = await screen.findByRole('dialog')
    expect(
      within(dialog).getByText('仅显示前 50 个匹配项，请输入关键词缩小范围。')
    ).toBeInTheDocument()

    const searchInput = within(dialog).getByPlaceholderText('搜索名称、平台、用户、群号或会话 ID')
    await user.type(searchInput, '小明')
    await user.click(await within(dialog).findByRole('checkbox', { name: '选择 小明的私聊' }))
    await user.click(within(dialog).getByRole('checkbox', { name: '选择 小明的私聊' }))
    expect(within(dialog).getByRole('button', { name: '加入 0 个聊天' })).toBeDisabled()

    await user.clear(searchInput)
    await user.type(searchInput, '批量聊天3')
    await user.click(await within(dialog).findByRole('checkbox', { name: '选择 批量聊天3' }))
    await user.clear(searchInput)
    await user.type(searchInput, '小明')
    await user.click(await within(dialog).findByRole('checkbox', { name: '选择 小明的私聊' }))
    await user.click(within(dialog).getByRole('button', { name: '加入 2 个聊天' }))

    await waitFor(() =>
      expect(configApi.updateBotConfigSection).toHaveBeenCalledWith('expression', {
        enabled: true,
        expression_groups: [
          {
            targets: [
              { platform: 'qq', item_id: '10001', rule_type: 'group' },
              { platform: 'qq', item_id: '99999', rule_type: 'private' },
              { platform: 'telegram', item_id: '20002', rule_type: 'private' },
              { platform: 'qq', item_id: '10003', rule_type: 'group' },
            ],
          },
        ],
      })
    )
  })

  it('取消添加、空组、黑话新建、记忆可编辑与保存失败', async () => {
    const user = userEvent.setup()
    vi.mocked(configApi.getBotConfig).mockResolvedValue({
      expression: {
        enabled: true,
        expression_groups: [
          { targets: [] },
          {
            targets: [
              null,
              { platform: '', item_id: 'x' },
              { platform: 'qq', item_id: '10001', type: 'group' },
            ],
          },
        ],
      },
      jargon: { jargon_groups: 'not-array' },
      a_memorix: {
        global_memory_sharing_enabled: false,
        shared_memory_groups: [
          {
            expression_groups: [{ platform: 'telegram', item_id: '20002', rule_type: 'private' }],
          },
        ],
      },
    })
    await renderGroupsView()
    expect(await screen.findByText('空共享组')).toBeInTheDocument()
    expect(screen.getByText('测试群 · 账号 123')).toBeInTheDocument()

    await user.click(screen.getAllByRole('button', { name: '添加聊天' })[0])
    const dialog = await screen.findByRole('dialog')
    await user.click(within(dialog).getByRole('button', { name: '取消' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())

    await user.click(screen.getByRole('button', { name: '黑话' }))
    expect(await screen.findByText('暂无黑话共享组。')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '新建共享组' }))
    await waitFor(() =>
      expect(configApi.updateBotConfigSection).toHaveBeenCalledWith(
        'jargon',
        expect.objectContaining({ jargon_groups: [{ targets: [] }] })
      )
    )

    await user.click(screen.getByRole('button', { name: '记忆' }))
    expect(await screen.findByText('共享组 1')).toBeInTheDocument()
    expect(screen.getByText('小明的私聊')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '新建共享组' })).toBeEnabled()

    vi.mocked(configApi.updateBotConfigSection).mockRejectedValue(new Error('写配置失败'))
    await user.click(screen.getByRole('button', { name: '新建共享组' }))
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith({
        title: '保存共享组失败',
        description: '写配置失败',
        variant: 'destructive',
      })
    )
  })

  it('删除后没有可见清理项时展示空摘要', async () => {
    const user = userEvent.setup()
    vi.mocked(chatApi.deleteChatStream).mockResolvedValue({
      success: true,
      session_id: 'sess-1',
      deleted_total: 0,
      items: [{ key: 'messages', label: '消息', count: 0 }],
    })
    const dialog = await openDetail(user)
    await user.click(within(dialog).getByRole('button', { name: '删除聊天流' }))
    const deleteDialog = (await screen.findByText('严肃确认：删除聊天流')).closest(
      '[role="dialog"]'
    ) as HTMLElement
    await user.type(
      within(deleteDialog).getByLabelText('请输入完整 session_id 以确认删除'),
      'sess-1'
    )
    await user.click(within(deleteDialog).getByRole('button', { name: '永久删除' }))
    expect(await within(deleteDialog).findByText('未发现可清理的数据。')).toBeInTheDocument()
  })

  it('删除结果仅黑话时展示解除关联，非 Error 删除失败回退文案', async () => {
    const user = userEvent.setup()
    vi.mocked(chatApi.deleteChatStream).mockRejectedValue('delete-plain')
    const dialog = await openDetail(user)
    await user.click(within(dialog).getByRole('button', { name: '删除聊天流' }))
    const deleteDialog = (await screen.findByText('严肃确认：删除聊天流')).closest(
      '[role="dialog"]'
    ) as HTMLElement
    await user.type(
      within(deleteDialog).getByLabelText('请输入完整 session_id 以确认删除'),
      'sess-1'
    )
    await user.click(within(deleteDialog).getByRole('button', { name: '永久删除' }))
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith({
        title: '删除聊天流失败',
        description: '请稍后重试',
        variant: 'destructive',
      })
    )
  })
})
