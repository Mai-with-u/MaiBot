import type { ReactNode } from 'react'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { useResolvedAvatarUrl } from '@/lib/avatar-url'
import { ThemeProviderContext } from '@/lib/theme-context'
import type { ThemeProviderState } from '@/lib/theme-context'
import { DEFAULT_DASHBOARD_STYLE_CONFIG } from '@/lib/theme/tokens'
import type { DashboardStyle } from '@/lib/theme/tokens'
import type { UserEmojiItem } from '@/lib/user-emoji-api'

import { ChatComposer } from '../ChatComposer'
import { MobileChatList, MobileChatRoomBar } from '../MobileChat'
import type { ChatImageAttachment, ChatTab } from '../types'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, options?: { label?: string; name?: string }) =>
      options?.label ? `${key}:${options.label}` : options?.name ? `${key}:${options.name}` : key,
    i18n: { language: 'zh', resolvedLanguage: 'zh' },
  }),
}))

vi.mock('@/lib/avatar-url', () => ({
  useResolvedAvatarUrl: vi.fn((...args: unknown[]) =>
    args[0] === undefined ? undefined : `avatar://${args.filter(Boolean).join('/')}`
  ),
}))

vi.mock('@/components/ui/tooltip', () => ({
  Tooltip: ({ children }: { children: ReactNode }) => <>{children}</>,
  TooltipTrigger: ({ children }: { children: ReactNode }) => <>{children}</>,
  TooltipContent: ({ children }: { children: ReactNode }) => <div>{children}</div>,
}))

vi.mock('../UserEmojiManager', () => ({
  UserEmojiManager: ({
    disabled,
    onSendEmoji,
  }: {
    disabled: boolean
    onSendEmoji: (item: UserEmojiItem) => Promise<void>
  }) => (
    <button
      type="button"
      disabled={disabled}
      onClick={() =>
        void onSendEmoji({
          id: 'emoji-a',
          content_type: 'image/png',
          content_url: '/emoji-a.png',
          created_at: 1,
        })
      }
    >
      发送用户表情
    </button>
  ),
}))

function makeTab(id: string, overrides: Partial<ChatTab> = {}): ChatTab {
  return {
    id,
    type: 'webui',
    label: `标签-${id}`,
    messages: [],
    isConnected: true,
    isTyping: false,
    sessionInfo: { bot_name: `机器人-${id}`, bot_qq: '10001' },
    ...overrides,
  }
}

afterEach(() => cleanup())

describe('ChatComposer', () => {
  const image: ChatImageAttachment = {
    id: 'image-a',
    name: '截图.png',
    mime_type: 'image/png',
    base64: 'abc',
    data_url: 'data:image/png;base64,abc',
  }

  function renderComposer(overrides: Partial<Parameters<typeof ChatComposer>[0]> = {}) {
    const props: Parameters<typeof ChatComposer>[0] = {
      value: '',
      onChange: vi.fn(),
      onSend: vi.fn(),
      onAddImages: vi.fn(),
      onRemoveImage: vi.fn(),
      onSendEmoji: vi.fn(async () => {}),
      disabled: false,
      images: [],
      isConnected: true,
      userId: 'user-a',
      userName: '人类',
      isUploadingUserAvatar: false,
      onUpdateUserAvatar: vi.fn(async () => {}),
      onUpdateUserName: vi.fn(),
      ...overrides,
    }
    const result = render(<ChatComposer {...props} />)
    return { ...result, props }
  }

  it('编辑昵称后按 Enter 提交去除首尾空白', async () => {
    const user = userEvent.setup()
    const { props } = renderComposer()

    await user.click(screen.getByRole('button', { name: 'chat.sidebar.editName' }))
    const input = screen.getByPlaceholderText('chat.identity.namePlaceholder')
    expect(input).toHaveValue('人类')

    fireEvent.change(input, { target: { value: '  新名字  ' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(props.onUpdateUserName).toHaveBeenCalledWith('新名字')
    // 提交后退出编辑态
    expect(screen.queryByPlaceholderText('chat.identity.namePlaceholder')).not.toBeInTheDocument()
  })

  it('空昵称提交时回退默认昵称（点击保存按钮）', async () => {
    const user = userEvent.setup()
    const { props } = renderComposer()

    await user.click(screen.getByRole('button', { name: 'chat.sidebar.editName' }))
    const input = screen.getByPlaceholderText('chat.identity.namePlaceholder')
    fireEvent.change(input, { target: { value: '   ' } })
    await user.click(screen.getByRole('button', { name: 'chat.sidebar.saveName' }))
    expect(props.onUpdateUserName).toHaveBeenCalledWith('chat.userNameFallback')
  })

  it('按 Escape 取消编辑且不提交昵称', async () => {
    const user = userEvent.setup()
    const { props } = renderComposer()

    await user.click(screen.getByRole('button', { name: 'chat.sidebar.editName' }))
    const input = screen.getByPlaceholderText('chat.identity.namePlaceholder')
    fireEvent.change(input, { target: { value: '改了一半' } })
    fireEvent.keyDown(input, { key: 'Escape' })

    expect(screen.queryByPlaceholderText('chat.identity.namePlaceholder')).not.toBeInTheDocument()
    expect(props.onUpdateUserName).not.toHaveBeenCalled()
    // 再次打开时仍然使用原昵称
    await user.click(screen.getByRole('button', { name: 'chat.sidebar.editName' }))
    expect(screen.getByPlaceholderText('chat.identity.namePlaceholder')).toHaveValue('人类')
  })

  it('输入文本后点击发送，Enter 发送而 Shift+Enter 保留换行', async () => {
    const user = userEvent.setup()
    const { props } = renderComposer({ value: '你好' })
    const textarea = screen.getByRole('textbox', { name: 'chat.input.placeholder' })

    await user.click(screen.getByRole('button', { name: 'chat.actions.send' }))
    fireEvent.keyDown(textarea, {
      key: 'Enter',
      shiftKey: false,
      nativeEvent: { isComposing: false },
    })
    fireEvent.keyDown(textarea, {
      key: 'Enter',
      shiftKey: true,
      nativeEvent: { isComposing: false },
    })
    expect(props.onSend).toHaveBeenCalledTimes(2)

    await user.type(textarea, '新内容')
    expect(props.onChange).toHaveBeenCalled()
  })

  it('空文本无图片时禁用发送，有图片时允许发送并可移除', async () => {
    const user = userEvent.setup()
    const { rerender, props } = renderComposer()
    expect(screen.getByRole('button', { name: 'chat.actions.send' })).toBeDisabled()

    rerender(<ChatComposer {...props} images={[image]} />)
    expect(screen.getByRole('button', { name: 'chat.actions.send' })).toBeEnabled()
    expect(screen.getByRole('img', { name: '截图.png' })).toHaveAttribute('src', image.data_url)
    await user.click(screen.getByRole('button', { name: 'chat.actions.removeImage' }))
    expect(props.onRemoveImage).toHaveBeenCalledWith('image-a')
  })

  it('选择图片把 FileList 交给回调，断开连接后禁用媒体入口', () => {
    const { container, rerender, props } = renderComposer()
    const file = new File(['png'], 'a.png', { type: 'image/png' })
    const input = container.querySelector('input[type="file"]') as HTMLInputElement
    fireEvent.change(input, { target: { files: [file] } })
    expect(props.onAddImages).toHaveBeenCalledWith(expect.objectContaining({ 0: file }))

    rerender(<ChatComposer {...props} isConnected={false} />)
    expect(screen.getByRole('button', { name: 'chat.actions.addImage' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '发送用户表情' })).toBeDisabled()
    expect(screen.getByPlaceholderText('chat.input.waiting')).toBeDisabled()
  })

  it('空白内容禁用发送；输入法组合与禁用态不会触发发送', () => {
    const { rerender, props } = renderComposer({ value: '   ' })
    expect(screen.getByRole('button', { name: 'chat.actions.send' })).toBeDisabled()

    rerender(<ChatComposer {...props} value="你好" />)
    fireEvent.keyDown(screen.getByRole('textbox', { name: 'chat.input.placeholder' }), {
      key: 'Enter',
      shiftKey: false,
      isComposing: true,
    })
    expect(props.onSend).not.toHaveBeenCalled()

    rerender(<ChatComposer {...props} value="你好" disabled />)
    fireEvent.keyDown(screen.getByRole('textbox', { name: 'chat.input.placeholder' }), {
      key: 'Enter',
      shiftKey: false,
      isComposing: false,
    })
    expect(screen.getByRole('button', { name: 'chat.actions.send' })).toBeDisabled()
    expect(props.onSend).not.toHaveBeenCalled()
  })

  it('发送用户表情，空文件列表不触发添加图片', async () => {
    const user = userEvent.setup()
    const { container, props } = renderComposer()

    await user.click(screen.getByRole('button', { name: '发送用户表情' }))
    expect(props.onSendEmoji).toHaveBeenCalledWith({
      id: 'emoji-a',
      content_type: 'image/png',
      content_url: '/emoji-a.png',
      created_at: 1,
    })

    fireEvent.change(container.querySelector('input[type="file"]') as HTMLInputElement, {
      target: { files: null },
    })
    expect(props.onAddImages).not.toHaveBeenCalled()
  })

  it('无文件名的待发图片使用默认替代文本', () => {
    renderComposer({
      images: [{ ...image, name: '' }],
    })
    expect(screen.getByRole('img', { name: 'chat.media.image' })).toHaveAttribute(
      'src',
      image.data_url
    )
  })
})

function makeThemeState(dashboardStyle: DashboardStyle): ThemeProviderState {
  return {
    theme: 'light',
    resolvedTheme: 'light',
    setTheme: () => undefined,
    themeConfig: {
      selectedPreset: 'light',
      accentColor: '',
      styleTokenOverrides: {},
      styleCustomCSS: {},
      dashboardStyle,
      styleBackgroundConfig: {},
      styleConfig: DEFAULT_DASHBOARD_STYLE_CONFIG,
    },
    updateThemeConfig: () => undefined,
    resetTheme: () => undefined,
  }
}

describe('MobileChatList', () => {
  const observedSession = {
    sessionId: 'observed-a',
    sessionName: '测试观察群',
    isGroupChat: true,
    groupId: 'group-a',
    platform: 'qq',
    lastActivity: 1,
    eventCount: 2,
  }

  function renderList(
    overrides: Partial<Parameters<typeof MobileChatList>[0]> = {},
    dashboardStyle: DashboardStyle = 'modern'
  ) {
    const props = {
      tabs: [
        makeTab('webui-default'),
        makeTab('virtual-a', {
          type: 'virtual' as const,
          label: '小明的私聊',
          virtualConfig: {
            platform: 'qq',
            personId: 'person-a',
            userId: 'user-a',
            userName: '小明',
            groupName: '',
            groupId: '',
          },
        }),
      ],
      observedSessions: new Map([['observed-a', observedSession]]),
      observedStageStatuses: new Map(),
      observedLatestMessages: new Map(),
      onOpenTab: vi.fn(),
      onOpenObserved: vi.fn(),
      onOpenObservedSettings: vi.fn(),
      onClose: vi.fn(),
      ...overrides,
    }
    render(
      <ThemeProviderContext.Provider value={makeThemeState(dashboardStyle)}>
        <MobileChatList {...props} />
      </ThemeProviderContext.Provider>
    )
    return props
  }

  it('与麦麦的会话和聊天流在同一个列表里，并可按名称搜索', async () => {
    const user = userEvent.setup()
    renderList({}, 'millennium')

    expect(screen.queryByRole('tab')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /小明的私聊/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /测试观察群/ })).toBeInTheDocument()
    await user.type(screen.getByRole('searchbox'), '不存在')
    expect(screen.queryByRole('button', { name: /测试观察群/ })).not.toBeInTheDocument()
  })

  it('点按本地会话进入对话，只有非默认会话可以关闭', async () => {
    const user = userEvent.setup()
    const props = renderList()

    await user.click(screen.getByRole('button', { name: /小明的私聊/ }))
    expect(props.onOpenTab).toHaveBeenCalledWith('virtual-a')

    await user.click(screen.getByRole('button', { name: 'chat.mobile.close' }))
    expect(props.onClose).toHaveBeenCalledWith('virtual-a')
    expect(screen.getAllByRole('button', { name: 'chat.mobile.close' })).toHaveLength(1)
  })

  it('点按聊天流进入观察，左滑操作可以打开设置', async () => {
    const user = userEvent.setup()
    const props = renderList()

    await user.click(screen.getByRole('button', { name: /测试观察群/ }))
    expect(props.onOpenObserved).toHaveBeenCalledWith('observed-a')

    await user.click(screen.getByRole('button', { name: 'chat.mobile.settings' }))
    expect(props.onOpenObservedSettings).toHaveBeenCalledWith('observed-a')
  })

  it('聊天流加载失败时显示错误提示', () => {
    renderList({ observedSessions: new Map(), observedSessionsError: true })

    expect(screen.getByText('chat.sidebar.loadObservedChatsFailed')).toBeInTheDocument()
  })
})

describe('MobileChatRoomBar', () => {
  it('返回会话列表并打开观察聊天流设置', async () => {
    const user = userEvent.setup()
    const onBack = vi.fn()
    const onOpenSettings = vi.fn()
    render(
      <MobileChatRoomBar
        title="测试观察群"
        subtitle="规划中"
        online
        avatar={null}
        onBack={onBack}
        onOpenSettings={onOpenSettings}
      />
    )

    await user.click(screen.getByRole('button', { name: 'chat.mobile.back' }))
    expect(onBack).toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'chat.sidebar.openSettings:测试观察群' }))
    expect(onOpenSettings).toHaveBeenCalled()
  })

  it('选择头像文件并在上传期间禁用入口，空文件不会上传', () => {
    const onUpdate = vi.fn(async () => {})
    const userAvatar = {
      userId: 'user-a',
      userName: '小明',
      userAvatarVersion: 2,
      isUploading: false,
      onUpdate,
    }
    const { container, rerender } = render(
      <MobileChatRoomBar
        title="麦麦"
        subtitle="chat.sidebar.online"
        online
        avatar={null}
        onBack={vi.fn()}
        userAvatar={userAvatar}
      />
    )
    expect(useResolvedAvatarUrl).toHaveBeenCalledWith('webui', 'user-a', 'user', 2)
    const input = container.querySelector('input[type="file"]') as HTMLInputElement
    fireEvent.change(input, { target: { files: [] } })
    expect(onUpdate).not.toHaveBeenCalled()
    const file = new File(['avatar'], 'avatar.png', { type: 'image/png' })
    fireEvent.change(input, { target: { files: [file] } })
    expect(onUpdate).toHaveBeenCalledWith(file)

    rerender(
      <MobileChatRoomBar
        title="麦麦"
        subtitle="chat.sidebar.online"
        online
        avatar={null}
        onBack={vi.fn()}
        userAvatar={{ ...userAvatar, isUploading: true }}
      />
    )
    expect(screen.getByRole('button', { name: 'chat.sidebar.editAvatar' })).toBeDisabled()
  })
})
