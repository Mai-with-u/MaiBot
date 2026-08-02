import type { ReactNode } from 'react'
import type { UserEmojiItem } from '@/lib/user-emoji-api'

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { UserEmojiManager } from '../UserEmojiManager'

// t 必须是稳定引用：loadItems 的依赖数组包含 t，不稳定会导致 effect 无限重跑
const { tMock, toastMock, apiMocks } = vi.hoisted(() => ({
  tMock: (key: string) => key,
  toastMock: vi.fn(),
  apiMocks: {
    listUserEmojis: vi.fn(),
    addUserEmoji: vi.fn(),
    deleteUserEmoji: vi.fn(),
    resolveUserEmojiUrl: vi.fn(),
  },
}))

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: tMock }),
}))

vi.mock('@/hooks/use-toast', () => ({
  useToast: () => ({ toast: toastMock }),
}))

vi.mock('@/lib/user-emoji-api', () => apiMocks)

// 用受控上下文替代 Radix Popover，规避 jsdom 浮层限制，同时保留 open/onOpenChange 语义
vi.mock('@/components/ui/popover', async () => {
  const React = await import('react')
  const PopoverContext = React.createContext<{
    open: boolean
    onOpenChange: (open: boolean) => void
  }>({ open: false, onOpenChange: () => {} })

  function Popover({
    open,
    onOpenChange,
    children,
  }: {
    open?: boolean
    onOpenChange?: (open: boolean) => void
    children?: ReactNode
  }) {
    return (
      <PopoverContext.Provider
        value={{ open: Boolean(open), onOpenChange: onOpenChange ?? (() => {}) }}
      >
        {children}
      </PopoverContext.Provider>
    )
  }

  function PopoverTrigger({ children }: { children?: ReactNode; asChild?: boolean }) {
    const context = React.useContext(PopoverContext)
    // 模拟 asChild：把切换开关的点击处理注入到子按钮上
    if (!React.isValidElement(children)) {
      return null
    }
    return React.cloneElement(children as React.ReactElement<{ onClick?: () => void }>, {
      onClick: () => context.onOpenChange(!context.open),
    })
  }

  function PopoverContent({
    children,
  }: {
    children?: ReactNode
    align?: string
    side?: string
    className?: string
  }) {
    const context = React.useContext(PopoverContext)
    return context.open ? <div data-testid="popover-content">{children}</div> : null
  }

  return { Popover, PopoverTrigger, PopoverContent }
})

const MAX_USER_EMOJI_BYTES = 2 * 1024 * 1024

function makeEmoji(id: string): UserEmojiItem {
  return { id, content_type: 'image/png', content_url: `/emojis/${id}.png`, created_at: 1 }
}

function renderManager(overrides: Partial<Parameters<typeof UserEmojiManager>[0]> = {}) {
  const props: Parameters<typeof UserEmojiManager>[0] = {
    disabled: false,
    userId: 'user-1',
    onSendEmoji: vi.fn(async () => {}),
    ...overrides,
  }
  const view = render(<UserEmojiManager {...props} />)
  return { ...view, props }
}

async function openPopover() {
  const user = userEvent.setup()
  await user.click(screen.getByRole('button', { name: 'chat.actions.openEmojiManager' }))
  return user
}

beforeEach(() => {
  // mockReset 会清空测试内设置的实现，这里恢复各接口的默认成功实现
  apiMocks.listUserEmojis.mockResolvedValue({ items: [], limit: 20 })
  apiMocks.resolveUserEmojiUrl.mockImplementation(async (item: UserEmojiItem) => {
    return `resolved:${item.content_url}`
  })
  apiMocks.addUserEmoji.mockResolvedValue(makeEmoji('new'))
  apiMocks.deleteUserEmoji.mockResolvedValue(undefined)
})

afterEach(() => cleanup())

describe('UserEmojiManager', () => {
  it('禁用时触发按钮不可点击，弹层内容默认不渲染', () => {
    renderManager({ disabled: true })
    expect(screen.getByRole('button', { name: 'chat.actions.openEmojiManager' })).toBeDisabled()
    expect(screen.queryByTestId('popover-content')).not.toBeInTheDocument()
  })

  it('打开弹层后先展示加载态，随后按解析地址渲染表情列表', async () => {
    let resolveList: (value: { items: UserEmojiItem[]; limit: number }) => void = () => {}
    apiMocks.listUserEmojis.mockImplementation(
      () =>
        new Promise<{ items: UserEmojiItem[]; limit: number }>((resolve) => {
          resolveList = resolve
        })
    )
    const { container } = renderManager()
    await openPopover()

    // 请求未返回时展示加载动画
    await waitFor(() => expect(container.querySelector('.animate-spin')).not.toBeNull())
    expect(apiMocks.listUserEmojis).toHaveBeenCalledWith('user-1')

    resolveList({ items: [makeEmoji('a'), makeEmoji('b')], limit: 20 })
    const images = await screen.findAllByRole('img', { name: 'chat.media.emoji' })
    expect(images).toHaveLength(2)
    expect(images[0]).toHaveAttribute('src', 'resolved:/emojis/a.png')
    expect(images[1]).toHaveAttribute('src', 'resolved:/emojis/b.png')
  })

  it('列表为空时显示空态提示', async () => {
    renderManager()
    await openPopover()
    expect(await screen.findByText('chat.emojiManager.empty')).toBeInTheDocument()
  })

  it('加载失败时弹出错误提示并结束加载态', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    apiMocks.listUserEmojis.mockRejectedValue(new Error('网络错误'))
    renderManager()
    await openPopover()
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({
          title: 'chat.toast.emojiLoadFailed',
          description: '网络错误',
          variant: 'destructive',
        })
      )
    )
    expect(await screen.findByText('chat.emojiManager.empty')).toBeInTheDocument()
  })

  it('添加合法图片后新表情插入到列表最前', async () => {
    apiMocks.listUserEmojis.mockResolvedValue({ items: [makeEmoji('a')], limit: 20 })
    const { container } = renderManager()
    await openPopover()
    await screen.findAllByRole('img', { name: 'chat.media.emoji' })

    const file = new File(['png-data'], 'new.png', { type: 'image/png' })
    const input = container.querySelector('input[type="file"]') as HTMLInputElement
    fireEvent.change(input, { target: { files: [file] } })

    await waitFor(() => {
      const images = screen.getAllByRole('img', { name: 'chat.media.emoji' })
      expect(images).toHaveLength(2)
      expect(images[0]).toHaveAttribute('src', 'resolved:/emojis/new.png')
    })
    expect(apiMocks.addUserEmoji).toHaveBeenCalledWith('user-1', file)
  })

  it.each<[string, () => File]>([
    ['非图片类型', () => new File(['text'], 'a.txt', { type: 'text/plain' })],
    ['空文件', () => new File([], 'empty.png', { type: 'image/png' })],
    [
      '超过 2MB',
      () => new File([new Uint8Array(MAX_USER_EMOJI_BYTES + 1)], 'big.png', { type: 'image/png' }),
    ],
  ])('%s 的文件被前端校验拒绝且不发起上传', async (_label, createFile) => {
    const { container } = renderManager()
    await openPopover()
    await screen.findByText('chat.emojiManager.empty')

    const input = container.querySelector('input[type="file"]') as HTMLInputElement
    fireEvent.change(input, { target: { files: [createFile()] } })

    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({
        title: 'chat.toast.emojiUploadFailed',
        description: 'chat.toast.emojiUnsupportedDesc',
        variant: 'destructive',
      })
    )
    expect(apiMocks.addUserEmoji).not.toHaveBeenCalled()
  })

  it('发送成功后带解析地址回调并关闭弹层', async () => {
    apiMocks.listUserEmojis.mockResolvedValue({ items: [makeEmoji('a')], limit: 20 })
    const onSendEmoji = vi.fn(async () => {})
    renderManager({ onSendEmoji })
    const user = await openPopover()
    await screen.findAllByRole('img', { name: 'chat.media.emoji' })

    await user.click(screen.getByRole('button', { name: 'chat.emojiManager.send' }))
    expect(onSendEmoji).toHaveBeenCalledWith(
      expect.objectContaining({ id: 'a', content_url: '/emojis/a.png' })
    )
    await waitFor(() => expect(screen.queryByTestId('popover-content')).not.toBeInTheDocument())
  })

  it('发送失败时保留弹层并提示错误', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    apiMocks.listUserEmojis.mockResolvedValue({ items: [makeEmoji('a')], limit: 20 })
    const onSendEmoji = vi.fn(async () => {
      throw new Error('发送失败')
    })
    renderManager({ onSendEmoji })
    const user = await openPopover()
    await screen.findAllByRole('img', { name: 'chat.media.emoji' })

    await user.click(screen.getByRole('button', { name: 'chat.emojiManager.send' }))
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({
          title: 'chat.toast.emojiSendFailed',
          description: '发送失败',
          variant: 'destructive',
        })
      )
    )
    expect(screen.getByTestId('popover-content')).toBeInTheDocument()
  })

  it('删除成功后从列表移除对应表情', async () => {
    apiMocks.listUserEmojis.mockResolvedValue({
      items: [makeEmoji('a'), makeEmoji('b')],
      limit: 20,
    })
    renderManager()
    const user = await openPopover()
    await screen.findAllByRole('img', { name: 'chat.media.emoji' })

    await user.click(screen.getAllByRole('button', { name: 'chat.emojiManager.delete' })[0])
    expect(apiMocks.deleteUserEmoji).toHaveBeenCalledWith('user-1', 'a')
    await waitFor(() => {
      const images = screen.getAllByRole('img', { name: 'chat.media.emoji' })
      expect(images).toHaveLength(1)
      expect(images[0]).toHaveAttribute('src', 'resolved:/emojis/b.png')
    })
  })

  it('删除失败时提示错误且列表保持不变', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    apiMocks.listUserEmojis.mockResolvedValue({
      items: [makeEmoji('a'), makeEmoji('b')],
      limit: 20,
    })
    apiMocks.deleteUserEmoji.mockRejectedValue(new Error('删除失败'))
    renderManager()
    const user = await openPopover()
    await screen.findAllByRole('img', { name: 'chat.media.emoji' })

    await user.click(screen.getAllByRole('button', { name: 'chat.emojiManager.delete' })[0])
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({
          title: 'chat.toast.emojiDeleteFailed',
          description: '删除失败',
          variant: 'destructive',
        })
      )
    )
    expect(screen.getAllByRole('img', { name: 'chat.media.emoji' })).toHaveLength(2)
  })
})
