import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ChatScrollContext } from '../ChatScrollContext'
import { RenderMessageContent, RenderMessageSegment } from '../MessageRenderer'
import type { ChatMessage, MessageSegment } from '../types'

// t 必须是稳定引用，返回 key 并拼上关心的插值参数，方便断言
const { tMock, toastMock } = vi.hoisted(() => {
  const tMock = (key: string, options?: Record<string, unknown>) => {
    if (options) {
      const extras = ['type', 'data']
        .filter((name) => options[name] !== undefined)
        .map((name) => String(options[name]))
      if (extras.length > 0) {
        return `${key}:${extras.join(':')}`
      }
    }
    return key
  }
  return { tMock, toastMock: vi.fn() }
})

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: tMock }),
}))

vi.mock('@/hooks/use-toast', () => ({
  useToast: () => ({ toast: toastMock }),
}))

afterEach(() => cleanup())

describe('RenderMessageSegment', () => {
  it('文本段保留原始文本与换行样式', () => {
    const { container } = render(
      <RenderMessageSegment segment={{ type: 'text', data: '你好\n世界' }} />
    )
    const span = container.querySelector('span.whitespace-pre-wrap')
    expect(span).not.toBeNull()
    expect(span?.textContent).toBe('你好\n世界')
  })

  it('图片段渲染缩略图，点击后打开放大预览对话框', async () => {
    const user = userEvent.setup()
    render(
      <RenderMessageSegment segment={{ type: 'image', data: 'https://example.com/a.png' }} />
    )

    const thumbnail = screen.getByRole('img', { name: 'chat.media.image' })
    expect(thumbnail).toHaveAttribute('src', 'https://example.com/a.png')
    // 图片段使用较大的展示高度
    expect(thumbnail.className).toContain('max-h-64')

    await user.click(
      screen.getByRole('button', { name: 'chat.media.openPreview:chat.media.image' })
    )
    const dialog = screen.getByRole('dialog')
    expect(
      within(dialog).getByText('chat.media.previewTitle:chat.media.image')
    ).toBeInTheDocument()
    expect(within(dialog).getByRole('img', { name: 'chat.media.image' })).toHaveAttribute(
      'src',
      'https://example.com/a.png'
    )
  })

  it('表情段使用较小的展示高度', () => {
    render(<RenderMessageSegment segment={{ type: 'emoji', data: '/emoji/1.gif' }} />)
    const image = screen.getByRole('img', { name: 'chat.media.emoji' })
    expect(image.className).toContain('max-h-32')
  })

  it('图片加载失败时隐藏图片并追加占位提示', () => {
    render(
      <RenderMessageSegment segment={{ type: 'image', data: 'https://example.com/bad.png' }} />
    )
    const image = screen.getByRole('img', { name: 'chat.media.image' })
    fireEvent.error(image)
    expect(image.style.display).toBe('none')
    expect(screen.getByText('chat.media.loadFailed:chat.media.image')).toBeInTheDocument()
  })

  it('语音段渲染音频控件，视频段渲染视频控件', () => {
    const { container: audioContainer } = render(
      <RenderMessageSegment segment={{ type: 'voice', data: '/voice/1.mp3' }} />
    )
    expect(audioContainer.querySelector('audio')).toHaveAttribute('src', '/voice/1.mp3')

    const { container: videoContainer } = render(
      <RenderMessageSegment segment={{ type: 'video', data: '/video/1.mp4' }} />
    )
    expect(videoContainer.querySelector('video')).toHaveAttribute('src', '/video/1.mp4')
  })

  it.each<[MessageSegment, string]>([
    [{ type: 'face', data: 123 }, 'chat.media.face:123'],
    [{ type: 'music', data: '' }, 'chat.media.music'],
    [{ type: 'file', data: '报告.pdf' }, 'chat.media.file:报告.pdf'],
    [{ type: 'forward', data: '' }, 'chat.media.forward'],
    [{ type: 'unknown', data: '', original_type: 'xml' }, 'chat.media.unknown:xml'],
    // 无 original_type 时退回未知消息文案
    [{ type: 'unknown', data: '' }, 'chat.media.unknown:chat.media.unknownMessage'],
  ])('特殊段 %o 显示占位文案 %s', (segment, expected) => {
    render(<RenderMessageSegment segment={segment} />)
    expect(screen.getByText(expected)).toBeInTheDocument()
  })

  it('at 段优先显示群名片，并按昵称、用户 ID 逐级降级', () => {
    const { rerender } = render(
      <RenderMessageSegment
        segment={{
          type: 'at',
          data: {
            target_user_id: '10001',
            target_user_nickname: '昵称',
            target_user_cardname: '名片',
          },
        }}
      />
    )
    expect(screen.getByText('@名片')).toHaveAttribute('title', '@10001')

    rerender(
      <RenderMessageSegment
        segment={{ type: 'at', data: { target_user_id: '10001', target_user_nickname: '昵称' } }}
      />
    )
    expect(screen.getByText('@昵称')).toBeInTheDocument()

    // 原始负载是字符串时按用户 ID 处理
    rerender(<RenderMessageSegment segment={{ type: 'at', data: '20002' }} />)
    expect(screen.getByText('@20002')).toHaveAttribute('title', '@20002')

    // 空负载显示未知消息占位，title 只剩 @
    rerender(<RenderMessageSegment segment={{ type: 'at', data: '' }} />)
    expect(screen.getByText('@chat.media.unknownMessage')).toHaveAttribute('title', '@')
  })
})

describe('RenderMessageContent', () => {
  it('富文本消息把回复块拆到行内内容之前单独渲染', () => {
    const message: ChatMessage = {
      id: 'm1',
      type: 'bot',
      content: '整体内容',
      timestamp: 1,
      message_type: 'rich',
      segments: [
        { type: 'text', data: '正文' },
        {
          type: 'reply',
          data: {
            target_message_id: '42',
            target_message_content: '原文',
            target_message_sender_nickname: '小明',
          },
        },
      ],
    }
    const { container } = render(<RenderMessageContent message={message} />)
    const wrapper = container.firstElementChild
    expect(wrapper).not.toBeNull()
    // 回复块在前，行内文本在后
    expect(wrapper?.children[0]?.textContent).toContain('小明')
    expect(wrapper?.children[0]?.textContent).toContain('原文')
    expect(wrapper?.children[1]?.textContent).toBe('正文')
  })

  it('无消息段的富文本与普通消息都回退为纯文本内容', () => {
    const richWithoutSegments: ChatMessage = {
      id: 'm2',
      type: 'bot',
      content: '回退内容',
      timestamp: 1,
      message_type: 'rich',
      segments: [],
    }
    const { rerender } = render(<RenderMessageContent message={richWithoutSegments} />)
    expect(screen.getByText('回退内容')).toBeInTheDocument()

    const plain: ChatMessage = { id: 'm3', type: 'user', content: '普通文本', timestamp: 2 }
    rerender(<RenderMessageContent message={plain} />)
    expect(screen.getByText('普通文本')).toBeInTheDocument()
  })
})

describe('回复消息块', () => {
  function renderReply(segment: MessageSegment, scrollToMessage?: (id: string) => boolean) {
    if (!scrollToMessage) {
      return render(<RenderMessageSegment segment={segment} />)
    }
    return render(
      <ChatScrollContext.Provider value={{ scrollToMessage }}>
        <RenderMessageSegment segment={segment} />
      </ChatScrollContext.Provider>
    )
  }

  it('无滚动上下文时渲染为不可点击的块，并展示降级文案', () => {
    renderReply({
      type: 'reply',
      data: { target_message_id: '42', target_message_content: '   ' },
    })
    // 没有可点击能力时不渲染按钮
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
    // 发送者与内容都缺失时使用占位文案（纯空白内容视为缺失）
    expect(screen.getByText('chat.message.replyUnknownSender')).toBeInTheDocument()
    expect(screen.getByText('chat.media.replyMissing')).toBeInTheDocument()
  })

  it('缺少原消息 ID 时即使有上下文也不可点击', () => {
    renderReply(
      { type: 'reply', data: { target_message_id: null, target_message_content: '原文' } },
      vi.fn(() => true)
    )
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
    expect(screen.getByText('原文')).toBeInTheDocument()
  })

  it('点击后滚动定位到原消息，找到时不弹提示', async () => {
    const user = userEvent.setup()
    const scrollToMessage = vi.fn(() => true)
    renderReply(
      {
        type: 'reply',
        data: {
          target_message_id: '42',
          target_message_content: '原文',
          target_message_sender_cardname: '名片名',
        },
      },
      scrollToMessage
    )
    await user.click(screen.getByRole('button'))
    expect(scrollToMessage).toHaveBeenCalledWith('42')
    expect(toastMock).not.toHaveBeenCalled()
    expect(screen.getByText('名片名')).toBeInTheDocument()
  })

  it('原消息不在视图中时弹出破坏性提示', async () => {
    const user = userEvent.setup()
    renderReply(
      { type: 'reply', data: { target_message_id: '42', target_message_content: '原文' } },
      vi.fn(() => false)
    )
    await user.click(screen.getByRole('button'))
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({
        title: 'chat.toast.replyNotFoundTitle',
        variant: 'destructive',
      })
    )
  })

  it('原始负载是字符串时按消息 ID 处理并可点击跳转', async () => {
    const user = userEvent.setup()
    const scrollToMessage = vi.fn(() => true)
    renderReply({ type: 'reply', data: 'msg-9' }, scrollToMessage)
    await user.click(screen.getByRole('button'))
    expect(scrollToMessage).toHaveBeenCalledWith('msg-9')
  })
})
