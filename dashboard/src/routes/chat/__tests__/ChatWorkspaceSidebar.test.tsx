import type { ReactNode } from 'react'
import type { ChatTab } from '../types'

import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ChatWorkspaceSidebar } from '../ChatWorkspaceSidebar'
import { useResolvedAvatarUrl } from '@/lib/avatar-url'

// t 稳定引用，拼上关心的插值参数便于断言
const { tMock } = vi.hoisted(() => ({
  tMock: (key: string, options?: Record<string, unknown>) => {
    if (options) {
      const extras = ['label', 'name', 'count']
        .filter((name) => options[name] !== undefined)
        .map((name) => String(options[name]))
      if (extras.length > 0) {
        return `${key}:${extras.join(':')}`
      }
    }
    return key
  },
}))

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: tMock }),
}))

vi.mock('@/lib/avatar-url', () => ({
  useResolvedAvatarUrl: vi.fn(() => undefined),
}))

vi.mock('@/components/ui/tooltip', () => ({
  Tooltip: ({ children }: { children: ReactNode }) => <>{children}</>,
  TooltipTrigger: ({ children }: { children: ReactNode }) => <>{children}</>,
  TooltipContent: ({ children }: { children: ReactNode }) => <div>{children}</div>,
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

function makeVirtualTab(id: string, overrides: Partial<ChatTab> = {}): ChatTab {
  return makeTab(id, {
    type: 'virtual',
    label: '小明的私聊',
    virtualConfig: {
      platform: 'qq',
      personId: 'person-a',
      userId: 'user-x',
      userName: '小明',
      groupName: '',
      groupId: '',
    },
    ...overrides,
  })
}

function renderSidebar(overrides: Partial<Parameters<typeof ChatWorkspaceSidebar>[0]> = {}) {
  const props: Parameters<typeof ChatWorkspaceSidebar>[0] = {
    tabs: [makeTab('webui-default')],
    activeTabId: 'webui-default',
    userId: 'user-a',
    userName: '人类',
    isUploadingUserAvatar: false,
    onSwitch: vi.fn(),
    onClose: vi.fn(),
    onUpdateUserAvatar: vi.fn(async () => {}),
    onUpdateUserName: vi.fn(),
    ...overrides,
  }
  const view = render(<ChatWorkspaceSidebar {...props} />)
  return { ...view, props }
}

afterEach(() => cleanup())

describe('ChatWorkspaceSidebar', () => {
  it('展示会话显示名、消息预览与虚拟徽章，点击会话触发切换', async () => {
    const user = userEvent.setup()
    const virtualTab = makeVirtualTab('virtual-a', {
      messages: [
        { id: 'm1', type: 'user', content: '第一条', timestamp: 1 },
        { id: 'm2', type: 'bot', content: '最后一条', timestamp: 2 },
      ],
    })
    const { props } = renderSidebar({
      tabs: [makeTab('webui-default'), virtualTab],
      activeTabId: 'virtual-a',
    })

    expect(screen.queryByText('chat.sidebar.title')).not.toBeInTheDocument()
    expect(screen.queryByText('chat.sidebar.subtitle:2')).not.toBeInTheDocument()
    // webui 会话展示机器人名称，空消息展示占位预览
    expect(screen.getByText('机器人-webui-default')).toBeInTheDocument()
    expect(screen.getByText('chat.sidebar.emptyPreview')).toBeInTheDocument()
    // 虚拟会话展示标签名、最后一条消息与虚拟徽章
    expect(screen.getByText('小明的私聊')).toBeInTheDocument()
    expect(screen.getByText('最后一条')).toBeInTheDocument()
    expect(screen.getByText('chat.sidebar.virtualBadge')).toBeInTheDocument()

    // 用前缀锚定避免匹配到关闭按钮（其可访问名也包含会话名）
    await user.click(screen.getByRole('button', { name: /^小明的私聊/ }))
    expect(props.onSwitch).toHaveBeenCalledWith('virtual-a')
  })

  it('默认会话不提供关闭按钮，非默认会话可关闭', async () => {
    const user = userEvent.setup()
    const { props } = renderSidebar({
      tabs: [makeTab('webui-default'), makeVirtualTab('virtual-a')],
    })

    expect(
      screen.queryByRole('button', {
        name: 'chat.sidebar.closeConversation:机器人-webui-default',
      })
    ).not.toBeInTheDocument()

    await user.click(
      screen.getByRole('button', { name: 'chat.sidebar.closeConversation:小明的私聊' })
    )
    expect(props.onClose).toHaveBeenCalledWith('virtual-a', expect.anything())
  })

  it('按连接状态渲染在线/离线指示点', () => {
    const { container } = renderSidebar({
      tabs: [
        makeTab('webui-default', { isConnected: true }),
        makeVirtualTab('virtual-a', { isConnected: false }),
      ],
    })
    expect(container.querySelector('.bg-emerald-500')).not.toBeNull()
    expect(container.querySelector('[class*="bg-muted-foreground/40"]')).not.toBeNull()
  })

  it('编辑昵称后按 Enter 提交去除首尾空白', async () => {
    const user = userEvent.setup()
    const { props } = renderSidebar()

    await user.click(screen.getByRole('button', { name: 'chat.sidebar.editName' }))
    const input = screen.getByPlaceholderText('chat.identity.namePlaceholder')
    expect(input).toHaveValue('人类')

    fireEvent.change(input, { target: { value: '  新名字  ' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(props.onUpdateUserName).toHaveBeenCalledWith('新名字')
    // 提交后退出编辑态
    expect(
      screen.queryByPlaceholderText('chat.identity.namePlaceholder')
    ).not.toBeInTheDocument()
  })

  it('空昵称提交时回退默认昵称（点击保存按钮）', async () => {
    const user = userEvent.setup()
    const { props } = renderSidebar()

    await user.click(screen.getByRole('button', { name: 'chat.sidebar.editName' }))
    const input = screen.getByPlaceholderText('chat.identity.namePlaceholder')
    fireEvent.change(input, { target: { value: '   ' } })
    await user.click(screen.getByRole('button', { name: 'chat.sidebar.saveName' }))
    expect(props.onUpdateUserName).toHaveBeenCalledWith('chat.userNameFallback')
  })

  it('按 Escape 取消编辑且不提交昵称', async () => {
    const user = userEvent.setup()
    const { props } = renderSidebar()

    await user.click(screen.getByRole('button', { name: 'chat.sidebar.editName' }))
    const input = screen.getByPlaceholderText('chat.identity.namePlaceholder')
    fireEvent.change(input, { target: { value: '改了一半' } })
    fireEvent.keyDown(input, { key: 'Escape' })

    expect(
      screen.queryByPlaceholderText('chat.identity.namePlaceholder')
    ).not.toBeInTheDocument()
    expect(props.onUpdateUserName).not.toHaveBeenCalled()
    // 原昵称保持展示
    expect(screen.getByText('人类')).toBeInTheDocument()
  })

  it('选择头像文件交给回调，上传中禁用入口', () => {
    const { container, props, rerender } = renderSidebar({ userAvatarVersion: 2 })
    // 有头像版本号时按 webui 平台解析用户头像
    expect(useResolvedAvatarUrl).toHaveBeenCalledWith('webui', 'user-a', 'user', 2)

    const file = new File(['avatar'], 'avatar.png', { type: 'image/png' })
    fireEvent.change(container.querySelector('input[type="file"]') as HTMLInputElement, {
      target: { files: [file] },
    })
    expect(props.onUpdateUserAvatar).toHaveBeenCalledWith(file)

    rerender(<ChatWorkspaceSidebar {...props} isUploadingUserAvatar />)
    expect(screen.getByRole('button', { name: 'chat.sidebar.editAvatar' })).toBeDisabled()
    expect(screen.getByText('chat.sidebar.savingAvatar')).toBeInTheDocument()
  })

  it('无头像版本号时不解析用户头像地址', () => {
    renderSidebar()
    expect(useResolvedAvatarUrl).toHaveBeenCalledWith(undefined, 'user-a', 'user', undefined)
  })
})
