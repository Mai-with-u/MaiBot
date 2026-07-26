import type { ReactNode } from 'react'
import type { UserEmojiItem } from '@/lib/user-emoji-api'
import type { ChatImageAttachment, ChatTab } from '../types'

import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ChatComposer } from '../ChatComposer'
import { ChatHeaderBar } from '../ChatHeaderBar'
import { ChatTabBar } from '../ChatTabBar'
import { useResolvedAvatarUrl } from '@/lib/avatar-url'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, options?: { label?: string; name?: string }) =>
      options?.label ? `${key}:${options.label}` : options?.name ? `${key}:${options.name}` : key,
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
      ...overrides,
    }
    const result = render(<ChatComposer {...props} />)
    return { ...result, props }
  }

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
})

describe('ChatTabBar', () => {
  it('切换和关闭非默认标签，默认标签不提供关闭按钮', async () => {
    const user = userEvent.setup()
    const onSwitch = vi.fn()
    const onClose = vi.fn()
    render(
      <ChatTabBar
        tabs={[
          makeTab('webui-default'),
          makeTab('virtual-a', {
            type: 'virtual',
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
        ]}
        activeTabId="virtual-a"
        userId="user-a"
        userName="小明"
        isUploadingUserAvatar={false}
        onSwitch={onSwitch}
        onClose={onClose}
        onUpdateUserAvatar={vi.fn(async () => {})}
      />
    )

    await user.click(screen.getByRole('button', { name: '小明的私聊' }))
    expect(onSwitch).toHaveBeenCalledWith('virtual-a')
    await user.click(
      screen.getByRole('button', {
        name: 'chat.sidebar.closeConversation:小明的私聊',
      })
    )
    expect(onClose).toHaveBeenCalledWith('virtual-a', expect.anything())
    expect(
      screen.queryByRole('button', {
        name: 'chat.sidebar.closeConversation:机器人-webui-default',
      })
    ).not.toBeInTheDocument()
  })

  it('选择头像文件并在上传期间禁用入口', async () => {
    const onUpdateUserAvatar = vi.fn(async () => {})
    const { container, rerender } = render(
      <ChatTabBar
        tabs={[makeTab('webui-default')]}
        activeTabId="webui-default"
        userId="user-a"
        userName="小明"
        userAvatarVersion={2}
        isUploadingUserAvatar={false}
        onSwitch={vi.fn()}
        onClose={vi.fn()}
        onUpdateUserAvatar={onUpdateUserAvatar}
      />
    )
    expect(useResolvedAvatarUrl).toHaveBeenCalledWith('webui', 'user-a', 'user', 2)
    const file = new File(['avatar'], 'avatar.png', { type: 'image/png' })
    fireEvent.change(container.querySelector('input[type="file"]') as HTMLInputElement, {
      target: { files: [file] },
    })
    expect(onUpdateUserAvatar).toHaveBeenCalledWith(file)

    rerender(
      <ChatTabBar
        tabs={[makeTab('webui-default')]}
        activeTabId="webui-default"
        userId="user-a"
        userName="小明"
        isUploadingUserAvatar
        onSwitch={vi.fn()}
        onClose={vi.fn()}
        onUpdateUserAvatar={onUpdateUserAvatar}
      />
    )
    expect(screen.getByRole('button', { name: 'chat.sidebar.editAvatar' })).toBeDisabled()
  })
})

describe('ChatHeaderBar', () => {
  it.each([
    [true, false, 'chat.status.connected'],
    [false, true, 'chat.status.connecting'],
    [false, false, 'chat.status.disconnected'],
  ])('按连接状态显示对应文案', (isConnected, isConnecting, expected) => {
    render(
      <ChatHeaderBar
        activeTab={makeTab('tab-a', { isConnected })}
        botDisplayName="麦麦"
        isConnecting={isConnecting}
        isLoadingHistory={false}
        onReconnect={vi.fn()}
      />
    )
    expect(screen.getByText(expected)).toBeInTheDocument()
  })

  it('虚拟会话显示身份和群聊信息，重连按钮调用回调', async () => {
    const user = userEvent.setup()
    const onReconnect = vi.fn()
    render(
      <ChatHeaderBar
        activeTab={makeTab('virtual-a', {
          type: 'virtual',
          virtualConfig: {
            platform: 'qq',
            personId: 'person-a',
            userId: 'user-a',
            userName: '小明',
            groupName: '测试群',
            groupId: 'group-a',
          },
        })}
        botDisplayName="麦麦"
        isConnecting={false}
        isLoadingHistory
        onReconnect={onReconnect}
      />
    )
    expect(screen.getByText('小明')).toBeInTheDocument()
    expect(screen.getByText('qq')).toBeInTheDocument()
    expect(screen.getByText('测试群')).toBeInTheDocument()
    expect(useResolvedAvatarUrl).toHaveBeenCalledWith('qq', '10001')
    await user.click(screen.getByRole('button', { name: 'chat.actions.reconnect' }))
    expect(onReconnect).toHaveBeenCalledOnce()
  })

  it('连接中禁用重连按钮', () => {
    render(
      <ChatHeaderBar
        activeTab={undefined}
        botDisplayName="麦麦"
        isConnecting
        isLoadingHistory={false}
        onReconnect={vi.fn()}
      />
    )
    expect(screen.getByRole('button', { name: 'chat.actions.reconnect' })).toBeDisabled()
  })
})
