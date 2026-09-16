/**
 * ChatPage（src/routes/chat/index.tsx）页面级特征化测试。
 *
 * 策略：mock 掉 chat-ws-client / maisaka-monitor-client / toast / 头像与表情 API，
 * 并把四个子组件（侧边栏、移动端标签条、消息列表、输入区）替换为轻量桩，
 * 通过桩捕获 props 驱动交互，锁定页面的会话生命周期、消息流转与状态映射主路径。
 *
 * 注意：vitest.config.ts 开启了 mockReset，所有 vi.fn 的实现必须在 beforeEach 里重建。
 */
import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { MaisakaMonitorEvent, StageStatusEvent } from '@/lib/maisaka-monitor-client'
import type { UserEmojiItem } from '@/lib/user-emoji-api'
import type { SessionInfo, StageStatusInfo } from '@/routes/monitor/use-maisaka-monitor'

import { ChatPage } from '../index'
import type {
  ChatImageAttachment,
  ChatMessage,
  ChatRuntimeStatus,
  ChatTab,
  ObservedMessagePreview,
} from '../types'

// ---------- 桩组件 props 类型（只声明页面实际传入且测试用到的字段） ----------

interface MessageListStubProps {
  messages: ChatMessage[]
  isLoadingHistory: boolean
  botDisplayName: string
  userName: string
  language: string
  runtimeStatus: ChatRuntimeStatus | null
}

interface ComposerStubProps {
  value: string
  onChange: (value: string) => void
  onAddImages: (files: FileList) => Promise<void> | void
  onRemoveImage: (id: string) => void
  onSendEmoji: (item: UserEmojiItem) => Promise<void>
  onSend: () => void
  disabled: boolean
  images: ChatImageAttachment[]
  isConnected: boolean
  userId: string
}

interface SidebarStubProps {
  tabs: ChatTab[]
  activeTabId: string
  activeObservedSessionId: string | null
  observedSessions: Map<string, SessionInfo>
  observedStageStatuses: Map<string, StageStatusInfo>
  observedLatestMessages: Map<string, ObservedMessagePreview>
  userId: string
  userName: string
  isUploadingUserAvatar: boolean
  onSwitch: (tabId: string) => void
  onSelectObserved: (sessionId: string) => void
  onOpenObservedSettings: (sessionId: string) => void
  onClose: (tabId: string) => void
  onUpdateUserAvatar: (file: File) => Promise<void> | void
  onUpdateUserName: (name: string) => void
}

interface MonitorStubProps {
  embedded?: boolean
  reasoningReturnTo?: string
}

// ---------- 共享 mock 状态（vi.hoisted 保证在 vi.mock 工厂之前初始化） ----------

const mocks = vi.hoisted(() => ({
  navigate: vi.fn(),
  toast: vi.fn(),
  openSession: vi.fn(),
  sendMessage: vi.fn(),
  updateNickname: vi.fn(),
  closeSession: vi.fn(),
  releaseSession: vi.fn(),
  onSessionMessage: vi.fn(),
  onConnectionChange: vi.fn(),
  monitorSubscribe: vi.fn(),
  monitorHook: vi.fn(),
  setSelectedObservedSession: vi.fn(),
  uploadWebuiUserAvatar: vi.fn(),
  loadUserEmojiPayload: vi.fn(),
  // 会话消息监听器：tabId -> 监听器列表，由 onSessionMessage 的实现填充
  sessionListeners: new Map<string, Array<(message: Record<string, unknown>) => void>>(),
  connectionListeners: [] as Array<(connected: boolean) => void>,
  monitorListeners: [] as Array<(event: MaisakaMonitorEvent) => void>,
  observedSessions: new Map<string, SessionInfo>(),
  observedStageStatuses: new Map<string, StageStatusInfo>(),
  // 各桩组件最近一次接收到的 props
  composer: null as ComposerStubProps | null,
  sidebar: null as SidebarStubProps | null,
  messageList: null as MessageListStubProps | null,
}))

vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => mocks.navigate,
}))

// t 必须是稳定引用，直接返回 key；带 count 的场景拼接 count 便于断言
const i18nMock = vi.hoisted(() => {
  const t = (key: string, options?: { count?: number }) =>
    options && typeof options.count === 'number' ? `${key}:${options.count}` : key
  return { t, i18n: { language: 'zh-CN' } }
})

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: i18nMock.t, i18n: i18nMock.i18n }),
}))

vi.mock('motion/react', () => ({
  motion: {
    div: ({
      children,
      className,
    }: {
      children?: React.ReactNode
      className?: string
      variants?: unknown
      transition?: unknown
    }) => <div className={className}>{children}</div>,
  },
}))

vi.mock('@/hooks/use-toast', () => ({
  useToast: () => ({ toast: mocks.toast }),
}))

vi.mock('@/lib/chat-ws-client', () => ({
  chatWsClient: {
    onSessionMessage: mocks.onSessionMessage,
    onConnectionChange: mocks.onConnectionChange,
    openSession: mocks.openSession,
    sendMessage: mocks.sendMessage,
    updateNickname: mocks.updateNickname,
    closeSession: mocks.closeSession,
    releaseSession: mocks.releaseSession,
  },
}))

vi.mock('@/lib/maisaka-monitor-client', () => ({
  maisakaMonitorClient: { subscribe: mocks.monitorSubscribe },
}))

vi.mock('@/routes/monitor/use-maisaka-monitor', () => ({
  useMaisakaMonitor: mocks.monitorHook,
}))

vi.mock('@/routes/monitor/maisaka-monitor', () => ({
  MaisakaMonitor: (props: MonitorStubProps) => (
    <div
      data-testid="maisaka-monitor"
      data-embedded={String(props.embedded)}
      data-return-to={props.reasoningReturnTo}
    />
  ),
}))

vi.mock('@/lib/avatar-url', () => ({
  uploadWebuiUserAvatar: mocks.uploadWebuiUserAvatar,
}))

vi.mock('@/lib/user-emoji-api', () => ({
  loadUserEmojiPayload: mocks.loadUserEmojiPayload,
}))

vi.mock('../MessageList', () => ({
  MessageList: (props: MessageListStubProps) => {
    mocks.messageList = props
    return (
      <div
        data-testid="message-list"
        data-loading={String(props.isLoadingHistory)}
        data-bot={props.botDisplayName}
        data-status={props.runtimeStatus?.kind ?? 'none'}
      >
        {props.messages.map((message) => (
          <div
            key={message.id}
            data-testid="msg"
            data-id={message.id}
            data-segments={
              message.segments
                ?.map((segment) => `${segment.type}:${String(segment.data)}`)
                .join('|') ?? ''
            }
          >
            {`${message.type}:${message.content}`}
          </div>
        ))}
      </div>
    )
  },
}))

vi.mock('../ChatComposer', () => ({
  ChatComposer: (props: ComposerStubProps) => {
    mocks.composer = props
    return (
      <div
        data-testid="composer"
        data-disabled={String(props.disabled)}
        data-connected={String(props.isConnected)}
        data-images={props.images.map((image) => image.name).join(',')}
      >
        <span data-testid="composer-value">{props.value}</span>
      </div>
    )
  },
}))

vi.mock('../ChatWorkspaceSidebar', () => ({
  ChatWorkspaceSidebar: (props: SidebarStubProps) => {
    mocks.sidebar = props
    return (
      <div
        data-testid="sidebar"
        data-active={props.activeTabId}
        data-active-observed={props.activeObservedSessionId ?? ''}
      >
        {props.tabs.map((tab) => (
          <div key={tab.id} data-testid={`tab-${tab.id}`} data-connected={String(tab.isConnected)}>
            {tab.label}
          </div>
        ))}
        {Array.from(props.observedSessions.values()).map((session) => (
          <button
            key={session.sessionId}
            type="button"
            data-testid={`observed-${session.sessionId}`}
            onClick={() => props.onSelectObserved(session.sessionId)}
          >
            {session.sessionName}
          </button>
        ))}
      </div>
    )
  },
}))

// 移动端标签条与侧边栏功能重复，桩成空组件避免重复节点干扰断言
vi.mock('../ChatTabBar', () => ({
  ChatTabBar: () => null,
}))

// ---------- 测试工具函数 ----------

const USER_ID = 'webui_user_test1'
const USER_NAME = '测试用户'
const VIRTUAL_TABS_KEY = 'maibot_webui_virtual_tabs'

function composerProps(): ComposerStubProps {
  if (!mocks.composer) throw new Error('输入区桩尚未渲染')
  return mocks.composer
}

function sidebarProps(): SidebarStubProps {
  if (!mocks.sidebar) throw new Error('侧边栏桩尚未渲染')
  return mocks.sidebar
}

/** 向指定标签页的会话监听器广播一条 WS 消息 */
function emitSession(tabId: string, message: Record<string, unknown>): void {
  act(() => {
    for (const listener of mocks.sessionListeners.get(tabId) ?? []) {
      listener(message)
    }
  })
}

/** 广播一条 MaiSaka 监控事件 */
function emitMonitor(event: MaisakaMonitorEvent): void {
  act(() => {
    for (const listener of mocks.monitorListeners) {
      listener(event)
    }
  })
}

function makeStageStatus(overrides: Partial<StageStatusEvent> = {}): StageStatusEvent {
  return {
    session_id: 'sess-1',
    stage: 'Planner 决策',
    detail: '',
    round_text: '',
    agent_state: 'active',
    stage_started_at: 1,
    updated_at: 2,
    timestamp: 2,
    ...overrides,
  }
}

/** 用普通数组伪造 FileList（jsdom 无法直接构造） */
function makeFileList(files: File[]): FileList {
  const fileLike: Record<string, unknown> = {
    length: files.length,
    item: (index: number) => files[index] ?? null,
  }
  files.forEach((file, index) => {
    fileLike[index] = file
  })
  return fileLike as unknown as FileList
}

/** 在 localStorage 里预置一个保存的虚拟标签页（groupId 留空以测试旧数据兼容） */
function presetVirtualTab(): void {
  localStorage.setItem(
    VIRTUAL_TABS_KEY,
    JSON.stringify([
      {
        id: 'virtual-1',
        label: '虚拟-小明',
        createdAt: 1,
        virtualConfig: {
          platform: 'qq',
          personId: 'p1',
          userId: 'u10086',
          userName: '小明',
          groupName: '',
          groupId: '',
        },
      },
    ])
  )
}

/** 渲染页面并等待默认标签页连接成功 */
async function renderConnectedPage() {
  const result = render(<ChatPage />)
  await waitFor(() => {
    expect(screen.getByTestId('tab-webui-default')).toHaveAttribute('data-connected', 'true')
  })
  return result
}

beforeEach(() => {
  window.history.replaceState({}, '', '/')
  localStorage.clear()
  localStorage.setItem('maibot_webui_user_id', USER_ID)
  localStorage.setItem('maibot_webui_user_name', USER_NAME)

  mocks.sessionListeners.clear()
  mocks.connectionListeners.length = 0
  mocks.monitorListeners.length = 0
  mocks.observedSessions.clear()
  mocks.observedStageStatuses.clear()
  mocks.composer = null
  mocks.sidebar = null
  mocks.messageList = null

  // mockReset 会清空实现，这里重建默认实现
  mocks.openSession.mockResolvedValue(undefined)
  mocks.sendMessage.mockResolvedValue(undefined)
  mocks.updateNickname.mockResolvedValue(undefined)
  mocks.closeSession.mockResolvedValue(undefined)
  mocks.uploadWebuiUserAvatar.mockResolvedValue(undefined)
  mocks.loadUserEmojiPayload.mockResolvedValue({
    name: '猫猫',
    mime_type: 'image/gif',
    base64: 'QUJD',
    data_url: 'data:image/gif;base64,QUJD',
  })
  mocks.onSessionMessage.mockImplementation(
    (tabId: string, listener: (message: Record<string, unknown>) => void) => {
      const listeners = mocks.sessionListeners.get(tabId) ?? []
      listeners.push(listener)
      mocks.sessionListeners.set(tabId, listeners)
      return () => {
        const index = listeners.indexOf(listener)
        if (index >= 0) listeners.splice(index, 1)
      }
    }
  )
  mocks.onConnectionChange.mockImplementation((listener: (connected: boolean) => void) => {
    mocks.connectionListeners.push(listener)
    return () => {}
  })
  mocks.monitorSubscribe.mockImplementation(
    async (listener: (event: MaisakaMonitorEvent) => void) => {
      mocks.monitorListeners.push(listener)
      return async () => {}
    }
  )
  mocks.monitorHook.mockReturnValue({
    sessions: mocks.observedSessions,
    stageStatuses: mocks.observedStageStatuses,
    allTimeline: [],
    setSelectedSession: mocks.setSelectedObservedSession,
  })
})

afterEach(() => cleanup())

describe('聊天页 ChatPage', () => {
  it('首屏打开默认本地会话：注册监听、传入本地身份、连接成功后启用输入区', async () => {
    render(<ChatPage />)

    // 初始为加载历史状态，输入区未连接
    expect(screen.getByTestId('message-list')).toHaveAttribute('data-loading', 'true')
    expect(mocks.onSessionMessage).toHaveBeenCalledWith('webui-default', expect.any(Function))
    expect(mocks.openSession).toHaveBeenCalledWith('webui-default', {
      client: { type: 'webui', name: 'MaiBot WebUI' },
      user_id: USER_ID,
      user_name: USER_NAME,
    })

    // openSession 成功后标签页与输入区进入已连接状态
    await waitFor(() => {
      expect(screen.getByTestId('tab-webui-default')).toHaveAttribute('data-connected', 'true')
    })
    expect(screen.getByTestId('composer')).toHaveAttribute('data-disabled', 'false')

    // 全局连接断开时所有标签页同步为未连接
    act(() => {
      mocks.connectionListeners.forEach((listener) => listener(false))
    })
    expect(screen.getByTestId('tab-webui-default')).toHaveAttribute('data-connected', 'false')
  })

  it('恢复保存的虚拟会话：补齐旧数据缺失的 groupId 并按虚拟身份打开会话', async () => {
    presetVirtualTab()
    await renderConnectedPage()

    expect(screen.getByTestId('tab-virtual-1')).toHaveTextContent('虚拟-小明')
    expect(mocks.openSession).toHaveBeenCalledWith('virtual-1', {
      client: { type: 'webui', name: 'MaiBot WebUI' },
      user_id: 'u10086',
      user_name: '小明',
      platform: 'qq',
      person_id: 'p1',
      group_name: 'chat.virtualGroupFallback',
      group_id: 'webui_virtual_group_qq_u10086',
    })
  })

  it('session_info 消息更新会话信息并驱动机器人名称显示', async () => {
    await renderConnectedPage()

    emitSession('webui-default', {
      type: 'session_info',
      session_id: 'sess-1',
      bot_name: '麦麦',
      bot_qq: '10001',
      platform: 'webui',
    })

    expect(screen.getByTestId('message-list')).toHaveAttribute('data-bot', '麦麦')
  })

  it('history 消息填充历史列表并结束加载状态', async () => {
    await renderConnectedPage()
    expect(screen.getByTestId('message-list')).toHaveAttribute('data-loading', 'true')

    emitSession('webui-default', {
      type: 'history',
      messages: [
        { content: '早', timestamp: 100, is_bot: false, sender_name: '甲', sender_id: 'x' },
        { content: '早上好', timestamp: 101, is_bot: true },
      ],
    })

    expect(screen.getByTestId('message-list')).toHaveAttribute('data-loading', 'false')
    const nodes = screen.getAllByTestId('msg')
    expect(nodes.map((node) => node.textContent)).toEqual(['user:早', 'bot:早上好'])
  })

  it('bot_message 追加机器人消息并按内容加时间戳去重', async () => {
    await renderConnectedPage()

    emitSession('webui-default', { type: 'bot_message', content: '嗨', timestamp: 200 })
    emitSession('webui-default', { type: 'bot_message', content: '嗨', timestamp: 200 })
    expect(screen.getAllByTestId('msg')).toHaveLength(1)

    // 相同内容不同时间戳视为新消息
    emitSession('webui-default', { type: 'bot_message', content: '嗨', timestamp: 201 })
    expect(screen.getAllByTestId('msg')).toHaveLength(2)
  })

  it('user_message 跳过自己发出的回显、接受他人消息', async () => {
    await renderConnectedPage()

    // 自己的回显（去掉 webui_user_ 前缀后与本地 ID 相同）被跳过
    emitSession('webui-default', {
      type: 'user_message',
      content: '自己的消息',
      timestamp: 300,
      sender: { name: '我', user_id: USER_ID },
    })
    expect(screen.queryAllByTestId('msg')).toHaveLength(0)

    emitSession('webui-default', {
      type: 'user_message',
      content: '别人的消息',
      timestamp: 301,
      sender: { name: '别人', user_id: 'qq_123' },
    })
    expect(screen.getByText('user:别人的消息')).toBeInTheDocument()
  })

  it('发送消息：调用 WS 客户端、立即本地上屏并清空输入框', async () => {
    await renderConnectedPage()

    act(() => composerProps().onChange('你好'))
    expect(screen.getByTestId('composer-value')).toHaveTextContent('你好')

    await act(async () => {
      composerProps().onSend()
    })

    expect(mocks.sendMessage).toHaveBeenCalledWith('webui-default', '你好', USER_NAME, {
      images: [],
    })
    expect(screen.getByText('user:你好')).toBeInTheDocument()
    expect(screen.getByTestId('composer-value').textContent).toBe('')
  })

  it('发送失败：本地消息保留并弹出发送失败提示', async () => {
    await renderConnectedPage()
    vi.spyOn(console, 'error').mockImplementation(() => {})
    mocks.sendMessage.mockRejectedValue(new Error('boom'))

    act(() => composerProps().onChange('会失败'))
    await act(async () => {
      composerProps().onSend()
    })

    // 失败前已经本地上屏，失败后只提示不回滚
    expect(screen.getByText('user:会失败')).toBeInTheDocument()
    expect(mocks.toast).toHaveBeenCalledWith(
      expect.objectContaining({ title: 'chat.toast.sendFailed', variant: 'destructive' })
    )
  })

  it('打开会话失败：提示连接失败且后续发送被拦截', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    mocks.openSession.mockRejectedValue(new Error('offline'))
    render(<ChatPage />)

    await waitFor(() => {
      expect(mocks.toast).toHaveBeenCalledWith(
        expect.objectContaining({ title: 'chat.toast.connectionFailed', variant: 'destructive' })
      )
    })
    // 打开失败会结束历史加载状态
    expect(screen.getByTestId('message-list')).toHaveAttribute('data-loading', 'false')

    act(() => composerProps().onChange('发不出去'))
    await act(async () => {
      composerProps().onSend()
    })
    expect(mocks.sendMessage).not.toHaveBeenCalled()
  })

  it('typing 事件切换运行状态指示', async () => {
    await renderConnectedPage()

    emitSession('webui-default', { type: 'typing', is_typing: true, timestamp: 310 })
    expect(screen.getByTestId('message-list')).toHaveAttribute('data-status', 'typing')

    emitSession('webui-default', { type: 'typing', is_typing: false, timestamp: 311 })
    expect(screen.getByTestId('message-list')).toHaveAttribute('data-status', 'none')
  })

  it('error 事件追加错误消息、置为错误状态并弹出提示', async () => {
    await renderConnectedPage()

    emitSession('webui-default', { type: 'error', content: '后端炸了', timestamp: 320 })

    expect(screen.getByText('error:后端炸了')).toBeInTheDocument()
    expect(screen.getByTestId('message-list')).toHaveAttribute('data-status', 'error')
    expect(mocks.toast).toHaveBeenCalledWith(
      expect.objectContaining({
        title: 'chat.toast.error',
        description: '后端炸了',
        variant: 'destructive',
      })
    )
  })

  it('MaiSaka 监控：snapshot/status 映射运行状态，removed 清除状态', async () => {
    await renderConnectedPage()
    emitSession('webui-default', { type: 'session_info', session_id: 'sess-1' })

    // 快照中的回复生成阶段映射为 typing
    emitMonitor({
      type: 'stage.snapshot',
      data: { entries: [makeStageStatus({ stage: '回复生成中' })], timestamp: 1 },
    })
    expect(screen.getByTestId('message-list')).toHaveAttribute('data-status', 'typing')

    // Planner 阶段映射为 thinking
    emitMonitor({ type: 'stage.status', data: makeStageStatus({ stage: 'Planner 决策' }) })
    expect(screen.getByTestId('message-list')).toHaveAttribute('data-status', 'thinking')

    emitMonitor({
      type: 'stage.removed',
      data: { session_id: 'sess-1', timestamp: 3 },
    })
    expect(screen.getByTestId('message-list')).toHaveAttribute('data-status', 'none')
  })

  it('从统一侧边栏选择真实聊天流后显示只读观察，切回本地聊天恢复输入区', async () => {
    mocks.observedSessions.set('observed-1', {
      sessionId: 'observed-1',
      sessionName: '测试群(123)',
      isGroupChat: true,
      groupId: '123',
      platform: 'qq',
      lastActivity: 10,
      eventCount: 4,
    })
    await renderConnectedPage()

    act(() => sidebarProps().onSelectObserved('observed-1'))

    expect(mocks.setSelectedObservedSession).toHaveBeenCalledWith('observed-1')
    expect(screen.getByTestId('sidebar')).toHaveAttribute('data-active-observed', 'observed-1')
    expect(screen.getByTestId('maisaka-monitor')).toHaveAttribute('data-embedded', 'true')
    expect(screen.getByTestId('maisaka-monitor')).toHaveAttribute(
      'data-return-to',
      '/chat?observe=observed-1'
    )
    expect(screen.queryByTestId('composer')).not.toBeInTheDocument()

    act(() => sidebarProps().onOpenObservedSettings('observed-1'))
    expect(mocks.navigate).toHaveBeenCalledWith({
      to: '/chat-management',
      search: { session_id: 'observed-1' },
    })

    act(() => sidebarProps().onSwitch('webui-default'))
    expect(screen.getByTestId('composer')).toBeInTheDocument()
    expect(screen.getByTestId('sidebar')).toHaveAttribute('data-active-observed', '')
  })

  it('从推理详情携带 observe 参数返回时恢复原聊天流观察视图', async () => {
    window.history.replaceState({}, '', '/chat?observe=session%2Fwith%2Fslash')

    render(<ChatPage />)

    await waitFor(() => {
      expect(mocks.setSelectedObservedSession).toHaveBeenCalledWith('session/with/slash')
    })
    expect(screen.getByTestId('maisaka-monitor')).toHaveAttribute(
      'data-return-to',
      '/chat?observe=session%2Fwith%2Fslash'
    )
    expect(screen.queryByTestId('composer')).not.toBeInTheDocument()
  })

  it('MaiSaka 监控：会话身份晚于快照到达时恢复当前思考状态', async () => {
    await renderConnectedPage()

    emitMonitor({
      type: 'stage.snapshot',
      data: { entries: [makeStageStatus({ stage: 'Planner 决策' })], timestamp: 1 },
    })
    expect(screen.getByTestId('message-list')).toHaveAttribute('data-status', 'none')

    emitSession('webui-default', { type: 'session_info', session_id: 'sess-1' })
    expect(screen.getByTestId('message-list')).toHaveAttribute('data-status', 'thinking')
  })

  it('MaiSaka 监控：llm.retry 推断重试状态，llm.error 置为错误状态', async () => {
    await renderConnectedPage()
    emitSession('webui-default', { type: 'session_info', session_id: 'sess-1' })

    emitMonitor({
      type: 'llm.retry',
      data: {
        session_id: 'sess-1',
        task_name: 'replyer_main',
        request_type: 'chat',
        model_name: 'm',
        attempt: 2,
        max_attempts: 3,
        reason: '限流',
        retry_interval: 5,
        timestamp: 400,
      },
    })
    // 无当前状态时按任务名推断：replyer -> typing
    expect(screen.getByTestId('message-list')).toHaveAttribute('data-status', 'typing')

    emitMonitor({
      type: 'llm.error',
      data: {
        session_id: 'sess-1',
        task_name: 'replyer_main',
        request_type: 'chat',
        model_name: 'm',
        message: '模型崩了',
        timestamp: 401,
      },
    })
    expect(screen.getByTestId('message-list')).toHaveAttribute('data-status', 'error')
  })

  it('MaiSaka 监控：当前阶段快照清除历史回放的模型错误', async () => {
    await renderConnectedPage()
    emitSession('webui-default', { type: 'session_info', session_id: 'sess-1' })

    emitMonitor({
      type: 'llm.error',
      data: {
        session_id: 'sess-1',
        task_name: 'replyer_main',
        request_type: 'chat',
        model_name: 'm',
        message: '历史模型错误',
        timestamp: 401,
      },
    })
    expect(screen.getByTestId('message-list')).toHaveAttribute('data-status', 'error')

    emitMonitor({ type: 'stage.snapshot', data: { entries: [], timestamp: 402 } })
    expect(screen.getByTestId('message-list')).toHaveAttribute('data-status', 'none')
  })

  it('MaiSaka 监控：进入等待消息阶段后清除当前模型错误', async () => {
    await renderConnectedPage()
    emitSession('webui-default', { type: 'session_info', session_id: 'sess-1' })

    emitMonitor({
      type: 'llm.error',
      data: {
        session_id: 'sess-1',
        task_name: 'replyer_main',
        request_type: 'chat',
        model_name: 'm',
        message: '模型错误',
        timestamp: 401,
      },
    })
    expect(screen.getByTestId('message-list')).toHaveAttribute('data-status', 'error')

    emitMonitor({
      type: 'stage.status',
      data: makeStageStatus({ stage: '等待消息', agent_state: 'wait', timestamp: 402 }),
    })
    expect(screen.getByTestId('message-list')).toHaveAttribute('data-status', 'none')
  })

  it('关闭虚拟标签页：注销会话、更新持久化并回到默认标签；默认标签不可关闭', async () => {
    presetVirtualTab()
    await renderConnectedPage()

    // 先切换到虚拟标签页
    act(() => sidebarProps().onSwitch('virtual-1'))
    expect(screen.getByTestId('sidebar')).toHaveAttribute('data-active', 'virtual-1')

    act(() => sidebarProps().onClose('virtual-1'))
    expect(mocks.closeSession).toHaveBeenCalledWith('virtual-1')
    expect(screen.queryByTestId('tab-virtual-1')).not.toBeInTheDocument()
    expect(screen.getByTestId('sidebar')).toHaveAttribute('data-active', 'webui-default')
    expect(JSON.parse(localStorage.getItem(VIRTUAL_TABS_KEY) ?? 'null')).toEqual([])

    // 默认本地标签页无法被关闭
    act(() => sidebarProps().onClose('webui-default'))
    expect(screen.getByTestId('tab-webui-default')).toBeInTheDocument()
    expect(mocks.closeSession).not.toHaveBeenCalledWith('webui-default')
  })

  it('修改昵称：保存到本地并同步 WS，空昵称回退默认值', async () => {
    await renderConnectedPage()

    act(() => sidebarProps().onUpdateUserName(' 新名 '))
    expect(localStorage.getItem('maibot_webui_user_name')).toBe('新名')
    expect(mocks.updateNickname).toHaveBeenCalledWith('webui-default', '新名')

    act(() => sidebarProps().onUpdateUserName('   '))
    expect(localStorage.getItem('maibot_webui_user_name')).toBe('chat.userNameFallback')
    expect(mocks.updateNickname).toHaveBeenCalledWith('webui-default', 'chat.userNameFallback')
  })

  it('上传头像：超限文件被拒绝，合法文件上传后保存版本号', async () => {
    await renderConnectedPage()

    // 超过 5MB 的文件直接拒绝
    const bigFile = new File(['x'], 'big.png', { type: 'image/png' })
    Object.defineProperty(bigFile, 'size', { value: 5 * 1024 * 1024 + 1 })
    await act(async () => {
      await sidebarProps().onUpdateUserAvatar(bigFile)
    })
    expect(mocks.uploadWebuiUserAvatar).not.toHaveBeenCalled()
    expect(mocks.toast).toHaveBeenCalledWith(
      expect.objectContaining({ title: 'chat.toast.avatarUnsupported', variant: 'destructive' })
    )

    const okFile = new File(['x'], 'avatar.png', { type: 'image/png' })
    await act(async () => {
      await sidebarProps().onUpdateUserAvatar(okFile)
    })
    expect(mocks.uploadWebuiUserAvatar).toHaveBeenCalledWith(USER_ID, okFile)
    expect(Number(localStorage.getItem('maibot_webui_user_avatar_version'))).toBeGreaterThan(0)
    expect(mocks.toast).toHaveBeenCalledWith(
      expect.objectContaining({ title: 'chat.toast.avatarSaved' })
    )
  })

  it('发送用户表情：解析表情负载后经 WS 发送并本地上屏', async () => {
    await renderConnectedPage()

    const item: UserEmojiItem = {
      id: 'emoji-a',
      content_type: 'image/gif',
      content_url: '/emoji-a.gif',
      created_at: 1,
    }
    await act(async () => {
      await composerProps().onSendEmoji(item)
    })

    expect(mocks.loadUserEmojiPayload).toHaveBeenCalledWith(item)
    expect(mocks.sendMessage).toHaveBeenCalledWith('webui-default', '', USER_NAME, {
      emojis: [{ name: '猫猫', mime_type: 'image/gif', base64: 'QUJD' }],
    })
    expect(screen.getByText('user:[chat.media.emoji]')).toBeInTheDocument()
  })

  it('添加图片：非图片文件被拒绝，图片文件读入后随消息发送', async () => {
    await renderConnectedPage()

    // 纯文本文件不被接受
    const textFile = new File(['hello'], 'note.txt', { type: 'text/plain' })
    await act(async () => {
      await composerProps().onAddImages(makeFileList([textFile]))
    })
    expect(mocks.toast).toHaveBeenCalledWith(
      expect.objectContaining({ title: 'chat.toast.imageUnsupported', variant: 'destructive' })
    )
    expect(screen.getByTestId('composer')).toHaveAttribute('data-images', '')

    // 图片文件经 FileReader 读入后出现在待发送列表
    const pngFile = new File(['fake-image'], '截图.png', { type: 'image/png' })
    await act(async () => {
      await composerProps().onAddImages(makeFileList([pngFile]))
    })
    await waitFor(() => {
      expect(screen.getByTestId('composer')).toHaveAttribute('data-images', '截图.png')
    })

    // 携带图片发送时附带 base64 负载
    await act(async () => {
      composerProps().onSend()
    })
    expect(mocks.sendMessage).toHaveBeenCalledWith('webui-default', '', USER_NAME, {
      images: [
        expect.objectContaining({
          name: '截图.png',
          mime_type: 'image/png',
          base64: expect.any(String),
        }),
      ],
    })
  })

  it('卸载页面时释放所有标签页的会话', async () => {
    presetVirtualTab()
    const { unmount } = await renderConnectedPage()

    unmount()

    expect(mocks.releaseSession).toHaveBeenCalledWith('webui-default')
    expect(mocks.releaseSession).toHaveBeenCalledWith('virtual-1')
  })

  it('单个会话消息超过上限时丢掉最旧的一条再追加', async () => {
    await renderConnectedPage()

    emitSession('webui-default', {
      type: 'history',
      messages: Array.from({ length: 1000 }, (_, index) => ({
        content: `历史${index}`,
        id: `hist-${index}`,
        is_bot: index % 2 === 1,
        timestamp: 1000 + index,
      })),
    })

    const historyNodes = screen.getAllByTestId('msg')
    expect(historyNodes).toHaveLength(1000)
    expect(historyNodes[0]).toHaveTextContent('user:历史0')

    emitSession('webui-default', { type: 'bot_message', content: '溢出新消息', timestamp: 9000 })

    const overflowNodes = screen.getAllByTestId('msg')
    expect(overflowNodes).toHaveLength(1000)
    expect(overflowNodes[0]).toHaveTextContent('bot:历史1')
    expect(overflowNodes[overflowNodes.length - 1]).toHaveTextContent('bot:溢出新消息')
  })

  it('移除待发送图片后输入区不再携带该附件', async () => {
    await renderConnectedPage()

    const pngFile = new File(['fake-image'], '截图.png', { type: 'image/png' })
    await act(async () => {
      await composerProps().onAddImages(makeFileList([pngFile]))
    })
    await waitFor(() => {
      expect(composerProps().images).toHaveLength(1)
    })

    const imageId = composerProps().images[0].id
    act(() => composerProps().onRemoveImage(imageId))

    expect(composerProps().images).toHaveLength(0)
    expect(screen.getByTestId('composer')).toHaveAttribute('data-images', '')
  })

  it('读取图片失败：FileReader 报错时提示读取失败', async () => {
    await renderConnectedPage()
    vi.spyOn(console, 'error').mockImplementation(() => {})

    const OriginalFileReader = window.FileReader
    window.FileReader = class extends OriginalFileReader {
      override readAsDataURL() {
        this.onerror?.(new ProgressEvent('error') as ProgressEvent<FileReader>)
      }
    } as typeof FileReader

    try {
      const pngFile = new File(['fake-image'], '坏图.png', { type: 'image/png' })
      await act(async () => {
        await composerProps().onAddImages(makeFileList([pngFile]))
      })
    } finally {
      window.FileReader = OriginalFileReader
    }

    expect(mocks.toast).toHaveBeenCalledWith(
      expect.objectContaining({ title: 'chat.toast.imageReadFailed', variant: 'destructive' })
    )
    expect(screen.getByTestId('composer')).toHaveAttribute('data-images', '')
  })

  it('读取图片失败：非图片 dataUrl 被拒绝', async () => {
    await renderConnectedPage()
    vi.spyOn(console, 'error').mockImplementation(() => {})

    const OriginalFileReader = window.FileReader
    window.FileReader = class extends OriginalFileReader {
      override readAsDataURL() {
        Object.defineProperty(this, 'result', { value: 'data:text/plain;base64,AAAA' })
        this.onload?.(new ProgressEvent('load') as ProgressEvent<FileReader>)
      }
    } as typeof FileReader

    try {
      const pngFile = new File(['fake-image'], '伪装.png', { type: 'image/png' })
      await act(async () => {
        await composerProps().onAddImages(makeFileList([pngFile]))
      })
    } finally {
      window.FileReader = OriginalFileReader
    }

    expect(mocks.toast).toHaveBeenCalledWith(
      expect.objectContaining({ title: 'chat.toast.imageReadFailed', variant: 'destructive' })
    )
  })

  it('他人富文本消息按 mime/dataUrl 回退拼出图片与表情段', async () => {
    await renderConnectedPage()

    emitSession('webui-default', {
      type: 'user_message',
      content: '看这个',
      raw_content: '看这个',
      timestamp: 500,
      sender: { name: '别人', user_id: 'qq_999' },
      images: [
        { base64: 'AAA111', mimeType: 'image/webp' },
        { base64: 'BBB222' },
        { name: 'empty.png' },
        { dataUrl: 'data:image/jpeg;base64,CCC333' },
        { data_url: 'data:image/png;base64,REAL', base64: 'IGNORED' },
      ],
      emojis: [
        { data_url: 'data:image/gif;base64,EEE' },
        { base64: 'FFF', mime_type: 'image/gif' },
      ],
    })

    const node = screen.getByText('user:看这个')
    expect(node).toHaveAttribute(
      'data-segments',
      [
        'text:看这个',
        'image:data:image/webp;base64,AAA111',
        'image:data:image/png;base64,BBB222',
        'image:data:image/jpeg;base64,CCC333',
        'image:data:image/png;base64,REAL',
        'emoji:data:image/gif;base64,EEE',
        'emoji:data:image/gif;base64,FFF',
      ].join('|')
    )
  })

  it('空历史结束加载后消息列表为空；损坏的虚拟标签不会恢复额外会话', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    localStorage.setItem(VIRTUAL_TABS_KEY, '{not-json')
    await renderConnectedPage()

    emitSession('webui-default', { type: 'history' })

    expect(screen.getByTestId('message-list')).toHaveAttribute('data-loading', 'false')
    expect(screen.queryAllByTestId('msg')).toHaveLength(0)
    expect(screen.getByTestId('tab-webui-default')).toBeInTheDocument()
    expect(screen.queryByTestId('tab-virtual-1')).not.toBeInTheDocument()
  })

  it('error 无内容走兜底文案；阶段失败映射为错误状态', async () => {
    await renderConnectedPage()
    emitSession('webui-default', { type: 'session_info', session_id: 'sess-1' })

    emitSession('webui-default', { type: 'error', timestamp: 320 })
    expect(screen.getByText('error:chat.message.errorFallback')).toBeInTheDocument()
    expect(mocks.toast).toHaveBeenCalledWith(
      expect.objectContaining({
        title: 'chat.toast.error',
        variant: 'destructive',
      })
    )

    emitMonitor({
      type: 'stage.status',
      data: makeStageStatus({ stage: '工具执行失败', timestamp: 321 }),
    })
    expect(screen.getByTestId('message-list')).toHaveAttribute('data-status', 'error')
  })

  it('空白发送被拦截；未连接时发送表情失败', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    mocks.openSession.mockRejectedValue(new Error('offline'))
    render(<ChatPage />)

    await waitFor(() => {
      expect(mocks.toast).toHaveBeenCalledWith(
        expect.objectContaining({ title: 'chat.toast.connectionFailed', variant: 'destructive' })
      )
    })

    act(() => composerProps().onChange('   '))
    await act(async () => {
      composerProps().onSend()
    })
    expect(mocks.sendMessage).not.toHaveBeenCalled()

    await expect(
      composerProps().onSendEmoji({
        id: 'emoji-a',
        content_type: 'image/gif',
        content_url: '/emoji-a.gif',
        created_at: 1,
      })
    ).rejects.toThrow('chat.toast.currentSessionUnavailable')
    expect(mocks.loadUserEmojiPayload).not.toHaveBeenCalled()
  })

  it('表情负载读取失败不会发出消息；头像保存失败弹出错误', async () => {
    await renderConnectedPage()
    vi.spyOn(console, 'error').mockImplementation(() => {})
    mocks.loadUserEmojiPayload.mockRejectedValue(new Error('表情不存在'))

    await expect(
      composerProps().onSendEmoji({
        id: 'emoji-a',
        content_type: 'image/gif',
        content_url: '/emoji-a.gif',
        created_at: 1,
      })
    ).rejects.toThrow('表情不存在')
    expect(mocks.sendMessage).not.toHaveBeenCalled()

    mocks.uploadWebuiUserAvatar.mockRejectedValue(new Error('磁盘满了'))
    const okFile = new File(['x'], 'avatar.png', { type: 'image/png' })
    await act(async () => {
      await sidebarProps().onUpdateUserAvatar(okFile)
    })
    expect(mocks.toast).toHaveBeenCalledWith(
      expect.objectContaining({
        title: 'chat.toast.avatarSaveFailed',
        description: '磁盘满了',
        variant: 'destructive',
      })
    )
  })

  it('会话消息边沿：系统/未知类型、他人消息去重与溢出、富文本历史、双标签跳过非当前页', async () => {
    presetVirtualTab()
    await renderConnectedPage()

    emitSession('webui-default', { type: 'system', content: '已连接', timestamp: 10 })
    emitSession('webui-default', { type: 'system' })
    emitSession('webui-default', { type: 'pong' })
    expect(screen.getByText('system:已连接')).toBeInTheDocument()
    expect(screen.getByText('system:')).toBeInTheDocument()

    emitSession('webui-default', {
      type: 'user_message',
      content: '重复他人',
      timestamp: 20,
      sender: { name: '路人', user_id: 'qq_other' },
    })
    emitSession('webui-default', {
      type: 'user_message',
      content: '重复他人',
      timestamp: 20,
      sender: { name: '路人', user_id: 'qq_other' },
    })
    expect(screen.getAllByText('user:重复他人')).toHaveLength(1)

    emitSession('webui-default', {
      type: 'user_message',
      message_id: 'mid-emoji',
      sender: { name: '路人', user_id: 'qq_other' },
      emojis: [{ data_url: 'data:image/gif;base64,EEE' }],
    })
    expect(screen.getByText('user:')).toHaveAttribute('data-segments', 'emoji:data:image/gif;base64,EEE')

    emitSession('webui-default', { type: 'user_message', content: '无发送者', timestamp: 21 })
    expect(screen.getByText('user:无发送者')).toBeInTheDocument()

    emitSession('webui-default', {
      type: 'bot_message',
      message_type: 'rich',
      content: '富文本回复',
      segments: [{ type: 'text', data: '富文本回复' }],
    })
    expect(screen.getByText('bot:富文本回复')).toHaveAttribute('data-segments', 'text:富文本回复')

    emitSession('webui-default', { type: 'typing', is_typing: true })
    expect(screen.getByTestId('message-list')).toHaveAttribute('data-status', 'typing')
    emitSession('webui-default', { type: 'error' })
    expect(screen.getByText('error:chat.message.errorFallback')).toBeInTheDocument()

    emitSession('virtual-1', {
      type: 'session_info',
      session_id: 'v-sess',
      bot_name: '虚拟机器人',
    })
    emitSession('virtual-1', { type: 'bot_message', content: '只在虚拟页', timestamp: 30 })
    emitSession('virtual-1', { type: 'error', content: '虚拟页错误', timestamp: 31 })
    expect(screen.getByTestId('message-list')).toHaveAttribute('data-bot', 'chat.botNameFallback')
    expect(screen.queryByText('bot:只在虚拟页')).not.toBeInTheDocument()
    expect(screen.queryByText('error:虚拟页错误')).not.toBeInTheDocument()
    expect(mocks.toast).toHaveBeenCalledWith(
      expect.objectContaining({ description: '虚拟页错误', variant: 'destructive' })
    )

    act(() => sidebarProps().onSwitch('virtual-1'))
    expect(screen.getByTestId('message-list')).toHaveAttribute('data-bot', '虚拟机器人')
    expect(screen.getByText('bot:只在虚拟页')).toBeInTheDocument()
    expect(screen.getByText('error:虚拟页错误')).toBeInTheDocument()

    act(() => sidebarProps().onSwitch('webui-default'))
    emitSession('webui-default', {
      type: 'history',
      messages: [
        {
          content: '图文历史',
          timestamp: 1,
          is_bot: false,
          message_type: 'rich',
          segments: [{ type: 'image', data: 'data:image/png;base64,AA' }],
        },
        { content: '无名用户', timestamp: 2, is_bot: false },
        { content: '空段', timestamp: 3, is_bot: true, message_type: 'rich', segments: [] },
        { content: '空段对象', timestamp: 4, is_bot: true, message_type: 'rich', segments: null },
      ],
    })
    expect(screen.getByText('user:图文历史')).toHaveAttribute(
      'data-segments',
      'image:data:image/png;base64,AA'
    )
    expect(screen.getByText('user:无名用户')).toBeInTheDocument()
    expect(screen.getByText('bot:空段')).toHaveAttribute('data-segments', '')
    expect(screen.getByText('bot:空段对象')).toHaveAttribute('data-segments', '')

    act(() => {
      const listeners = mocks.sessionListeners.get('webui-default') ?? []
      for (let index = 0; index < 101; index += 1) {
        for (const listener of listeners) {
          listener({
            type: 'user_message',
            content: `他人${index}`,
            timestamp: 9000 + index,
            sender: { name: '路人', user_id: 'qq_other' },
          })
        }
      }
    })
    expect(screen.getByText('user:他人0')).toBeInTheDocument()
    expect(screen.getByText('user:他人100')).toBeInTheDocument()

    act(() => composerProps().onChange('溢出后再发'))
    await act(async () => {
      composerProps().onSend()
    })
    expect(screen.getByText('user:溢出后再发')).toBeInTheDocument()
  })

  it('MaiSaka 状态映射：空闲/acting/思考类/reply 工具、重试推断、group/user 匹配与未命中', async () => {
    await renderConnectedPage()

    emitMonitor({
      type: 'stage.snapshot',
      data: {
        entries: [
          makeStageStatus({
            session_id: 'snap-1',
            group_id: 'g-chat',
            stage: 'replyer working',
          }),
        ],
        timestamp: 1,
      },
    })
    expect(screen.getByTestId('message-list')).toHaveAttribute('data-status', 'none')

    emitSession('webui-default', {
      type: 'session_info',
      session_id: 'sess-1',
      user_id: 'u-1',
      group_id: 'g-chat',
      platform: 'webui',
    })
    expect(screen.getByTestId('message-list')).toHaveAttribute('data-status', 'typing')

    const expectStatus = (stage: string, kind: string, extra: Partial<StageStatusEvent> = {}) => {
      emitMonitor({
        type: 'stage.status',
        data: makeStageStatus({ stage, timestamp: 10, ...extra }),
      })
      expect(screen.getByTestId('message-list')).toHaveAttribute('data-status', kind)
    }

    expectStatus('空闲', 'none')
    expectStatus('Planner 决策', 'none', { agent_state: 'wait' })
    expectStatus('处理异常', 'error')
    expectStatus('内部错误', 'error')
    expectStatus('error occurred', 'error')
    expectStatus('replier 输出', 'typing')
    expectStatus('工具执行 reply', 'typing')
    expectStatus('思考中', 'thinking')
    expectStatus('消息整理', 'thinking')
    expectStatus('启动循环', 'thinking')
    expectStatus('工具调用', 'acting')
    expectStatus('  ', 'acting', {
      agent_state: '',
      updated_at: 0,
      timestamp: 0,
    })

    emitMonitor({
      type: 'stage.status',
      data: makeStageStatus({
        session_id: 'sess-miss',
        group_id: 'g-other',
        stage: '回复生成中',
        timestamp: 20,
      }),
    })
    expect(screen.getByTestId('message-list')).toHaveAttribute('data-status', 'acting')

    emitMonitor({
      type: 'stage.status',
      data: makeStageStatus({
        session_id: 'sess-user',
        user_id: 'u-1',
        platform: 'webui',
        group_id: null,
        stage: '消息整理',
        updated_at: 0,
        timestamp: 21,
      }),
    })
    expect(screen.getByTestId('message-list')).toHaveAttribute('data-status', 'thinking')

    emitMonitor({
      type: 'stage.status',
      data: makeStageStatus({
        session_id: 'sess-platform-miss',
        user_id: 'u-1',
        platform: 'discord',
        group_id: null,
        stage: '回复生成中',
        timestamp: 22,
      }),
    })
    expect(screen.getByTestId('message-list')).toHaveAttribute('data-status', 'thinking')

    emitMonitor({
      type: 'stage.status',
      data: makeStageStatus({
        session_id: 'sess-platform-empty',
        user_id: 'u-1',
        group_id: null,
        stage: '启动循环',
        timestamp: 23,
      }),
    })
    expect(screen.getByTestId('message-list')).toHaveAttribute('data-status', 'thinking')

    emitMonitor({
      type: 'llm.retry',
      data: {
        session_id: 'sess-miss',
        task_name: 'replyer_main',
        request_type: 'chat',
        model_name: 'm',
        attempt: 1,
        max_attempts: 3,
        reason: '未命中',
        retry_interval: 1,
        timestamp: 24,
      },
    })
    emitMonitor({
      type: 'llm.error',
      data: {
        session_id: 'sess-miss',
        task_name: 'replyer_main',
        request_type: 'chat',
        model_name: 'm',
        message: '未命中错误',
        timestamp: 25,
      },
    })
    emitMonitor({
      type: 'stage.removed',
      data: { session_id: 'sess-miss', timestamp: 26 },
    })
    expect(screen.getByTestId('message-list')).toHaveAttribute('data-status', 'thinking')

    emitMonitor({
      type: 'stage.status',
      data: makeStageStatus({ stage: '空闲', agent_state: 'wait', timestamp: 27 }),
    })
    expect(screen.getByTestId('message-list')).toHaveAttribute('data-status', 'none')

    emitMonitor({
      type: 'llm.retry',
      data: {
        session_id: 'sess-1',
        task_name: 'tool_executor',
        request_type: 'tool',
        model_name: 'm',
        attempt: 1,
        max_attempts: 3,
        reason: '工具重试',
        retry_interval: 1,
        timestamp: 0,
      },
    })
    expect(screen.getByTestId('message-list')).toHaveAttribute('data-status', 'acting')

    emitMonitor({
      type: 'stage.status',
      data: makeStageStatus({ stage: '空闲', timestamp: 28 }),
    })
    emitMonitor({
      type: 'llm.retry',
      data: {
        session_id: 'sess-1',
        task_name: 'planner_main',
        request_type: 'plan',
        model_name: 'm',
        attempt: 1,
        max_attempts: 3,
        reason: '规划重试',
        retry_interval: 1,
        timestamp: 0,
      },
    })
    expect(screen.getByTestId('message-list')).toHaveAttribute('data-status', 'thinking')

    emitMonitor({
      type: 'llm.retry',
      data: {
        session_id: 'sess-1',
        task_name: 'keep',
        request_type: 'replier',
        model_name: 'm',
        attempt: 2,
        max_attempts: 3,
        reason: '保留当前 kind',
        retry_interval: 1,
        timestamp: 29,
      },
    })
    expect(screen.getByTestId('message-list')).toHaveAttribute('data-status', 'thinking')

    emitMonitor({
      type: 'stage.status',
      data: makeStageStatus({ stage: '空闲', timestamp: 30 }),
    })
    emitMonitor({
      type: 'llm.retry',
      data: {
        session_id: 'sess-1',
        task_name: 'x',
        request_type: 'replier_stream',
        model_name: 'm',
        attempt: 1,
        max_attempts: 3,
        reason: 'replier',
        retry_interval: 1,
        timestamp: 31,
      },
    })
    expect(screen.getByTestId('message-list')).toHaveAttribute('data-status', 'typing')

    emitMonitor({
      type: 'llm.error',
      data: {
        session_id: 'sess-1',
        task_name: 'x',
        request_type: 'chat',
        model_name: 'm',
        message: '无时间戳错误',
        timestamp: 0,
      },
    })
    expect(screen.getByTestId('message-list')).toHaveAttribute('data-status', 'error')
  })

  it('虚拟身份：已有 groupId/群名按配置打开，发送用虚拟昵称，监控按群或用户匹配，关闭后持久化剩余标签', async () => {
    localStorage.setItem(
      VIRTUAL_TABS_KEY,
      JSON.stringify([
        {
          id: 'virtual-named',
          label: '虚拟-有群',
          createdAt: 1,
          virtualConfig: {
            platform: 'qq',
            personId: 'p2',
            userId: 'u200',
            userName: '小红',
            groupName: '测试群',
            groupId: 'custom-group',
          },
        },
        {
          id: 'virtual-anon',
          label: '虚拟-匿名',
          createdAt: 2,
          virtualConfig: {
            platform: 'telegram',
            personId: 'p3',
            userId: 'u300',
            userName: '',
            groupName: '',
            groupId: 'g-anon',
          },
        },
      ])
    )
    await renderConnectedPage()

    expect(mocks.openSession).toHaveBeenCalledWith(
      'virtual-named',
      expect.objectContaining({
        user_id: 'u200',
        user_name: '小红',
        group_name: '测试群',
        group_id: 'custom-group',
      })
    )

    act(() => sidebarProps().onSwitch('virtual-named'))
    act(() => composerProps().onChange('虚拟身份发言'))
    await act(async () => {
      composerProps().onSend()
    })
    expect(mocks.sendMessage).toHaveBeenCalledWith('virtual-named', '虚拟身份发言', '小红', {
      images: [],
    })

    await act(async () => {
      await composerProps().onSendEmoji({
        id: 'emoji-a',
        content_type: 'image/gif',
        content_url: '/emoji-a.gif',
        created_at: 1,
      })
    })
    expect(mocks.sendMessage).toHaveBeenCalledWith('virtual-named', '', '小红', {
      emojis: [{ name: '猫猫', mime_type: 'image/gif', base64: 'QUJD' }],
    })

    emitSession('virtual-named', {
      type: 'user_message',
      content: '自己的回显',
      timestamp: 40,
      sender: { name: '小红', user_id: 'u200' },
    })
    expect(screen.queryByText('user:自己的回显')).not.toBeInTheDocument()

    emitSession('virtual-named', {
      type: 'user_message',
      content: '群友消息',
      timestamp: 41,
      sender: { name: '群友', user_id: 'u999' },
    })
    expect(screen.getByText('user:群友消息')).toBeInTheDocument()

    emitMonitor({
      type: 'stage.status',
      data: makeStageStatus({
        session_id: 'other-sess',
        group_id: 'custom-group',
        stage: '工具调用',
        timestamp: 42,
      }),
    })
    expect(screen.getByTestId('message-list')).toHaveAttribute('data-status', 'acting')

    emitMonitor({
      type: 'stage.status',
      data: makeStageStatus({
        session_id: 'by-user',
        user_id: 'u200',
        platform: 'qq',
        group_id: null,
        stage: '思考中',
        timestamp: 43,
      }),
    })
    expect(screen.getByTestId('message-list')).toHaveAttribute('data-status', 'thinking')

    emitMonitor({
      type: 'stage.status',
      data: makeStageStatus({
        session_id: 'by-user-miss',
        user_id: 'u200',
        platform: 'discord',
        group_id: null,
        stage: '回复生成中',
        timestamp: 44,
      }),
    })
    expect(screen.getByTestId('message-list')).toHaveAttribute('data-status', 'thinking')

    act(() => sidebarProps().onSwitch('virtual-anon'))
    act(() => composerProps().onChange('匿名发言'))
    await act(async () => {
      composerProps().onSend()
    })
    expect(mocks.sendMessage).toHaveBeenCalledWith('virtual-anon', '匿名发言', USER_NAME, {
      images: [],
    })

    act(() => sidebarProps().onSwitch('virtual-named'))
    const stopPropagation = vi.fn()
    const onClose = sidebarProps().onClose as (
      tabId: string,
      event?: { stopPropagation: () => void }
    ) => void
    act(() => onClose('virtual-anon', { stopPropagation }))
    expect(stopPropagation).toHaveBeenCalled()
    expect(mocks.closeSession).toHaveBeenCalledWith('virtual-anon')
    expect(screen.queryByTestId('tab-virtual-anon')).not.toBeInTheDocument()
    expect(screen.getByTestId('sidebar')).toHaveAttribute('data-active', 'virtual-named')
    expect(JSON.parse(localStorage.getItem(VIRTUAL_TABS_KEY) ?? '[]')).toEqual([
      expect.objectContaining({
        id: 'virtual-named',
        label: '虚拟-有群',
        virtualConfig: expect.objectContaining({
          groupId: 'custom-group',
          groupName: '测试群',
          userName: '小红',
        }),
      }),
    ])
  })

  it('发送失败在多标签下只清空当前标签运行状态', async () => {
    presetVirtualTab()
    await renderConnectedPage()
    vi.spyOn(console, 'error').mockImplementation(() => {})
    mocks.sendMessage.mockRejectedValue(new Error('boom'))

    act(() => composerProps().onChange('会失败'))
    await act(async () => {
      composerProps().onSend()
    })

    expect(screen.getByText('user:会失败')).toBeInTheDocument()
    expect(screen.getByTestId('tab-virtual-1')).toBeInTheDocument()
    expect(mocks.toast).toHaveBeenCalledWith(
      expect.objectContaining({ title: 'chat.toast.sendFailed', variant: 'destructive' })
    )
  })

  it('图片数量达到上限后拒绝新增，超额文件只保留空位并提示', async () => {
    await renderConnectedPage()

    const textFile = new File(['hello'], 'note.txt', { type: 'text/plain' })
    const firstPng = new File(['img'], '图0.png', { type: 'image/png' })
    await act(async () => {
      await composerProps().onAddImages(makeFileList([textFile, firstPng]))
    })
    await waitFor(() => {
      expect(composerProps().images).toHaveLength(1)
    })

    const morePngs = Array.from(
      { length: 7 },
      (_, index) => new File(['img'], `图${index + 1}.png`, { type: 'image/png' })
    )
    await act(async () => {
      await composerProps().onAddImages(makeFileList(morePngs))
    })
    await waitFor(() => {
      expect(composerProps().images).toHaveLength(8)
    })

    const overflowPng = new File(['img'], '图8.png', { type: 'image/png' })
    await act(async () => {
      await composerProps().onAddImages(makeFileList([overflowPng]))
    })
    expect(composerProps().images).toHaveLength(8)
    expect(mocks.toast).toHaveBeenCalledWith(
      expect.objectContaining({
        title: 'chat.toast.imageLimit',
        description: 'chat.toast.imageLimitDesc:8',
        variant: 'destructive',
      })
    )

    const firstId = composerProps().images[0].id
    act(() => composerProps().onRemoveImage(firstId))
    expect(composerProps().images).toHaveLength(7)

    const extras = ['a', 'b', 'c'].map(
      (name) => new File(['img'], `${name}.png`, { type: 'image/png' })
    )
    await act(async () => {
      await composerProps().onAddImages(makeFileList(extras))
    })
    await waitFor(() => {
      expect(composerProps().images).toHaveLength(8)
    })
    expect(mocks.toast).toHaveBeenCalledWith({
      title: 'chat.toast.imageLimit',
      description: 'chat.toast.imageLimitDesc:8',
    })
  })

  it('头像非图片被拒绝；失败原因非 Error 时走兜底文案；无 MIME 仍可上传', async () => {
    await renderConnectedPage()
    vi.spyOn(console, 'error').mockImplementation(() => {})

    const textFile = new File(['x'], 'avatar.txt', { type: 'text/plain' })
    await act(async () => {
      await sidebarProps().onUpdateUserAvatar(textFile)
    })
    expect(mocks.uploadWebuiUserAvatar).not.toHaveBeenCalled()
    expect(mocks.toast).toHaveBeenCalledWith(
      expect.objectContaining({ title: 'chat.toast.avatarUnsupported', variant: 'destructive' })
    )

    mocks.uploadWebuiUserAvatar.mockRejectedValue('not-an-error')
    const okFile = new File(['x'], 'avatar.png', { type: 'image/png' })
    await act(async () => {
      await sidebarProps().onUpdateUserAvatar(okFile)
    })
    expect(mocks.toast).toHaveBeenCalledWith(
      expect.objectContaining({
        title: 'chat.toast.avatarSaveFailed',
        description: 'chat.toast.avatarSaveFailedDesc',
        variant: 'destructive',
      })
    )

    mocks.uploadWebuiUserAvatar.mockResolvedValue(undefined)
    const noTypeFile = new File(['x'], 'avatar.bin', { type: '' })
    await act(async () => {
      await sidebarProps().onUpdateUserAvatar(noTypeFile)
    })
    expect(mocks.uploadWebuiUserAvatar).toHaveBeenCalledWith(USER_ID, noTypeFile)
  })

  it('MaiSaka 订阅失败只记日志，聊天仍可连接', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    mocks.monitorSubscribe.mockRejectedValue(new Error('monitor down'))
    await renderConnectedPage()

    await waitFor(() => {
      expect(console.error).toHaveBeenCalledWith(
        '[Chat] 订阅 MaiSaka 状态失败:',
        expect.any(Error)
      )
    })
    expect(screen.getByTestId('tab-webui-default')).toHaveAttribute('data-connected', 'true')
  })

  it('卸载后忽略连接/监控回调，未完成的订阅会立即清理', async () => {
    let resolveSubscribe: ((cleanup: () => Promise<void>) => void) | undefined
    const cleanupFn = vi.fn(async () => {})
    mocks.monitorSubscribe.mockImplementation((listener: (event: MaisakaMonitorEvent) => void) => {
      mocks.monitorListeners.push(listener)
      return new Promise((resolve) => {
        resolveSubscribe = resolve
      })
    })

    const { unmount } = await renderConnectedPage()
    unmount()

    act(() => {
      mocks.connectionListeners.forEach((listener) => listener(false))
    })
    emitMonitor({ type: 'stage.status', data: makeStageStatus({ stage: '回复生成中' }) })

    expect(resolveSubscribe).toEqual(expect.any(Function))
    await act(async () => {
      resolveSubscribe?.(cleanupFn)
    })
    expect(cleanupFn).toHaveBeenCalled()
  })

  it('未连接时修改昵称只写入本地，不调用 WS', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    mocks.openSession.mockRejectedValue(new Error('offline'))
    render(<ChatPage />)

    await waitFor(() => {
      expect(mocks.toast).toHaveBeenCalledWith(
        expect.objectContaining({ title: 'chat.toast.connectionFailed', variant: 'destructive' })
      )
    })

    act(() => sidebarProps().onUpdateUserName('离线改名'))
    expect(localStorage.getItem('maibot_webui_user_name')).toBe('离线改名')
    expect(mocks.updateNickname).not.toHaveBeenCalled()
  })

  it('读取图片失败：FileReader 结果非字符串时提示读取失败', async () => {
    await renderConnectedPage()
    vi.spyOn(console, 'error').mockImplementation(() => {})

    const OriginalFileReader = window.FileReader
    window.FileReader = class extends OriginalFileReader {
      override readAsDataURL() {
        Object.defineProperty(this, 'result', { value: new ArrayBuffer(1) })
        this.onload?.(new ProgressEvent('load') as ProgressEvent<FileReader>)
      }
    } as typeof FileReader

    try {
      const pngFile = new File(['fake-image'], '坏结果.png', { type: 'image/png' })
      await act(async () => {
        await composerProps().onAddImages(makeFileList([pngFile]))
      })
    } finally {
      window.FileReader = OriginalFileReader
    }

    expect(mocks.toast).toHaveBeenCalledWith(
      expect.objectContaining({ title: 'chat.toast.imageReadFailed', variant: 'destructive' })
    )
    expect(screen.getByTestId('composer')).toHaveAttribute('data-images', '')
  })
})


