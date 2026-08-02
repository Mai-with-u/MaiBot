/**
 * ChatPage（src/routes/chat/index.tsx）页面级特征化测试。
 *
 * 策略：mock 掉 chat-ws-client / maisaka-monitor-client / toast / 头像与表情 API，
 * 并把四个子组件（侧边栏、移动端标签条、消息列表、输入区）替换为轻量桩，
 * 通过桩捕获 props 驱动交互，锁定页面的会话生命周期、消息流转与状态映射主路径。
 *
 * 注意：vitest.config.ts 开启了 mockReset，所有 vi.fn 的实现必须在 beforeEach 里重建。
 */
import type { MaisakaMonitorEvent, StageStatusEvent } from '@/lib/maisaka-monitor-client'
import type { UserEmojiItem } from '@/lib/user-emoji-api'
import type { ChatImageAttachment, ChatMessage, ChatRuntimeStatus, ChatTab } from '../types'

import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ChatPage } from '../index'

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
  userId: string
  userName: string
  isUploadingUserAvatar: boolean
  onSwitch: (tabId: string) => void
  onClose: (tabId: string) => void
  onUpdateUserAvatar: (file: File) => Promise<void> | void
  onUpdateUserName: (name: string) => void
}

// ---------- 共享 mock 状态（vi.hoisted 保证在 vi.mock 工厂之前初始化） ----------

const mocks = vi.hoisted(() => ({
  toast: vi.fn(),
  openSession: vi.fn(),
  sendMessage: vi.fn(),
  updateNickname: vi.fn(),
  closeSession: vi.fn(),
  releaseSession: vi.fn(),
  onSessionMessage: vi.fn(),
  onConnectionChange: vi.fn(),
  monitorSubscribe: vi.fn(),
  uploadWebuiUserAvatar: vi.fn(),
  loadUserEmojiPayload: vi.fn(),
  // 会话消息监听器：tabId -> 监听器列表，由 onSessionMessage 的实现填充
  sessionListeners: new Map<string, Array<(message: Record<string, unknown>) => void>>(),
  connectionListeners: [] as Array<(connected: boolean) => void>,
  monitorListeners: [] as Array<(event: MaisakaMonitorEvent) => void>,
  // 各桩组件最近一次接收到的 props
  composer: null as ComposerStubProps | null,
  sidebar: null as SidebarStubProps | null,
  messageList: null as MessageListStubProps | null,
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
          <div key={message.id} data-testid="msg">
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
      <div data-testid="sidebar" data-active={props.activeTabId}>
        {props.tabs.map((tab) => (
          <div key={tab.id} data-testid={`tab-${tab.id}`} data-connected={String(tab.isConnected)}>
            {tab.label}
          </div>
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
  localStorage.clear()
  localStorage.setItem('maibot_webui_user_id', USER_ID)
  localStorage.setItem('maibot_webui_user_name', USER_NAME)

  mocks.sessionListeners.clear()
  mocks.connectionListeners.length = 0
  mocks.monitorListeners.length = 0
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
    expect(
      Number(localStorage.getItem('maibot_webui_user_avatar_version'))
    ).toBeGreaterThan(0)
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
})
