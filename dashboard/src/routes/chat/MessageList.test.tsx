import type { ReactNode, RefObject } from 'react'

import { fireEvent, render } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { MessageList } from './MessageList'
import type { ChatMessage } from './types'

const virtualizerMocks = vi.hoisted(() => ({
  lastScrollResult: null as boolean | null,
  measureElement: vi.fn(),
  scrollToIndex: vi.fn(),
  visibleStart: 0,
}))

// t 必须是稳定引用；带插值的文案拼上参数，方便断言空状态 / 运行状态
const i18nMock = vi.hoisted(() => {
  const t = (
    key: string,
    options?: { attempt?: number; bot?: string; count?: number; max?: number; retry?: string }
  ) => {
    if (key === 'chat.activity.retrySuffix' && options) {
      return `retry ${options.attempt}/${options.max}`
    }
    if (options?.bot) {
      return `${key}:${options.bot}${options.retry ? ` ${options.retry}` : ''}`
    }
    if (typeof options?.count === 'number') {
      return `${key}:${options.count}`
    }
    return key
  }
  return { t }
})

vi.mock('@tanstack/react-virtual', () => ({
  defaultRangeExtractor: ({
    startIndex,
    endIndex,
  }: {
    startIndex: number
    endIndex: number
  }) =>
    Array.from(
      { length: Math.max(0, endIndex - startIndex + 1) },
      (_, offset) => startIndex + offset
    ),
  useVirtualizer: ({
    count,
    rangeExtractor,
  }: {
    count: number
    rangeExtractor?: (range: {
      count: number
      endIndex: number
      overscan: number
      startIndex: number
    }) => number[]
  }) => {
    const startIndex = Math.min(virtualizerMocks.visibleStart, Math.max(0, count - 1))
    const endIndex = Math.min(count - 1, startIndex + 4)
    const range = { count, endIndex, overscan: 8, startIndex }
    const indexes = rangeExtractor
      ? rangeExtractor(range)
      : Array.from(
          { length: Math.max(0, endIndex - startIndex + 1) },
          (_, offset) => startIndex + offset
        )
    return {
      getTotalSize: () => count * 50,
      getVirtualItems: () => indexes.map((index) => ({ index, start: index * 50 })),
      measureElement: virtualizerMocks.measureElement,
      scrollToIndex: virtualizerMocks.scrollToIndex,
    }
  },
}))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: i18nMock.t }),
}))
vi.mock('@/components/ui/avatar', () => ({
  Avatar: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  AvatarFallback: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  AvatarImage: () => null,
}))
vi.mock('@/components/ui/scroll-area', () => ({
  ScrollArea: ({
    children,
    viewportRef,
  }: {
    children: ReactNode
    viewportRef: RefObject<HTMLDivElement | null>
  }) => (
    <div ref={viewportRef} data-testid="chat-viewport">
      {children}
    </div>
  ),
}))
vi.mock('@/lib/avatar-url', () => ({
  useResolvedAvatarUrl: () => undefined,
}))
vi.mock('./MessageRenderer', async () => {
  const { useChatScroll } = await import('./ChatScrollContext')
  return {
    RenderMessageContent: ({ message }: { message: ChatMessage }) => {
      const scroll = useChatScroll()
      return (
        <div>
          {message.id === 'message-0' ? (
            <audio controls data-testid="message-audio">
              <track kind="captions" src="" label="测试字幕" default />
            </audio>
          ) : (
            message.content
          )}
          <button
            type="button"
            data-testid={`scroll-self-${message.id}`}
            onClick={() => {
              virtualizerMocks.lastScrollResult = scroll?.scrollToMessage(message.id) ?? false
            }}
          >
            定位本条
          </button>
          <button
            type="button"
            data-testid={`scroll-missing-${message.id}`}
            onClick={() => {
              virtualizerMocks.lastScrollResult =
                scroll?.scrollToMessage('missing-message') ?? false
            }}
          >
            定位缺失
          </button>
          <button
            type="button"
            data-testid={`scroll-far-${message.id}`}
            onClick={() => {
              virtualizerMocks.lastScrollResult = scroll?.scrollToMessage('message-15') ?? false
            }}
          >
            定位远处
          </button>
        </div>
      )
    },
  }
})

function createMessages(count: number): ChatMessage[] {
  return Array.from({ length: count }, (_, index) => ({
    content: `消息 ${index}`,
    id: `message-${index}`,
    timestamp: 1_753_353_600 + index,
    type: index % 2 === 0 ? 'user' : 'bot',
  }))
}

function createRect(top: number, height: number): DOMRect {
  return {
    bottom: top + height,
    height,
    left: 0,
    right: 100,
    top,
    width: 100,
    x: 0,
    y: top,
    toJSON: () => ({}),
  } as DOMRect
}

describe('MessageList', () => {
  const scrollToMock = vi.fn()

  beforeEach(() => {
    vi.clearAllMocks()
    virtualizerMocks.lastScrollResult = null
    virtualizerMocks.visibleStart = 0
    Object.defineProperty(HTMLElement.prototype, 'scrollTo', {
      configurable: true,
      value: scrollToMock,
    })
    vi.spyOn(window, 'requestAnimationFrame').mockImplementation((callback) => {
      callback(0)
      return 1
    })
  })

  afterEach(() => {
    vi.restoreAllMocks()
    delete (HTMLElement.prototype as Partial<HTMLElement>).scrollTo
  })

  it('只渲染虚拟窗口，并仅在用户接近底部时自动跟随新消息', () => {
    const initialMessages = createMessages(20)
    const view = render(
      <MessageList
        botDisplayName="MaiBot"
        isLoadingHistory={false}
        language="zh-CN"
        messages={initialMessages}
        userName="测试用户"
      />
    )

    expect(view.container.querySelectorAll('[data-message-id]')).toHaveLength(5)
    expect(scrollToMock).toHaveBeenCalled()

    const viewport = view.getByTestId('chat-viewport')
    Object.defineProperties(viewport, {
      clientHeight: { configurable: true, value: 100 },
      scrollHeight: { configurable: true, value: 1000 },
      scrollTop: { configurable: true, value: 100, writable: true },
    })
    fireEvent.scroll(viewport)
    scrollToMock.mockClear()

    view.rerender(
      <MessageList
        botDisplayName="MaiBot"
        isLoadingHistory={false}
        language="zh-CN"
        messages={createMessages(21)}
        userName="测试用户"
      />
    )

    expect(view.container.querySelectorAll('[data-message-id]')).toHaveLength(5)
    expect(scrollToMock).not.toHaveBeenCalled()
  })

  it('达到消息上限删头时保持正在阅读的消息视觉位置', () => {
    const initialMessages = createMessages(1000)
    const view = render(
      <MessageList
        botDisplayName="MaiBot"
        isLoadingHistory={false}
        language="zh-CN"
        messages={initialMessages}
        userName="测试用户"
      />
    )
    const viewport = view.getByTestId('chat-viewport')
    Object.defineProperties(viewport, {
      clientHeight: { configurable: true, value: 100 },
      scrollHeight: { configurable: true, value: 50_000 },
      scrollTop: { configurable: true, value: 125, writable: true },
    })
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (
      this: HTMLElement
    ) {
      if (this === viewport) {
        return createRect(0, 100)
      }
      const index = Number(this.dataset.index)
      return Number.isFinite(index)
        ? createRect(index * 50 - viewport.scrollTop, 50)
        : createRect(0, 0)
    })
    fireEvent.scroll(viewport)

    const appendedMessage: ChatMessage = {
      content: '新消息',
      id: 'message-1000',
      timestamp: 1_753_354_600,
      type: 'bot',
    }
    view.rerender(
      <MessageList
        botDisplayName="MaiBot"
        isLoadingHistory={false}
        language="zh-CN"
        messages={[...initialMessages.slice(1), appendedMessage]}
        userName="测试用户"
      />
    )

    expect(viewport.scrollTop).toBe(75)
    expect(
      view.container
        .querySelector('[data-message-id="message-2"]')
        ?.getBoundingClientRect().top
    ).toBe(-25)
  })

  it('媒体播放期间固定对应虚拟行，暂停后恢复正常回收', () => {
    const messages = createMessages(20)
    const view = render(
      <MessageList
        botDisplayName="MaiBot"
        isLoadingHistory={false}
        language="zh-CN"
        messages={messages}
        userName="测试用户"
      />
    )

    const audio = view.getByTestId('message-audio')
    fireEvent.play(audio)
    virtualizerMocks.visibleStart = 10
    view.rerender(
      <MessageList
        botDisplayName="MaiBot"
        isLoadingHistory={false}
        language="zh-CN"
        messages={messages}
        userName="测试用户"
      />
    )

    expect(view.container.querySelector('[data-message-id="message-0"]')).not.toBeNull()
    expect(view.container.querySelectorAll('[data-message-id]')).toHaveLength(6)

    fireEvent.pause(audio)
    expect(view.container.querySelector('[data-message-id="message-0"]')).toBeNull()
    expect(view.container.querySelectorAll('[data-message-id]')).toHaveLength(5)
  })

  it('无历史且不在加载时展示空状态文案', () => {
    const view = render(
      <MessageList
        botDisplayName="MaiBot"
        isLoadingHistory={false}
        language="zh-CN"
        messages={[]}
        userName="测试用户"
      />
    )

    expect(view.getByText('chat.message.empty:MaiBot')).toBeInTheDocument()
    expect(view.getByText('chat.message.emptyHint')).toBeInTheDocument()
    expect(view.container.querySelector('[data-message-id]')).toBeNull()
  })

  it('正在加载历史时即使没有消息也不展示空状态', () => {
    const view = render(
      <MessageList
        botDisplayName="MaiBot"
        isLoadingHistory
        language="zh-CN"
        messages={[]}
        userName="测试用户"
      />
    )

    expect(view.queryByText('chat.message.empty:MaiBot')).toBeNull()
    expect(view.queryByText('chat.message.emptyHint')).toBeNull()
  })

  it('渲染系统消息分隔条与错误消息胶囊', () => {
    const view = render(
      <MessageList
        botDisplayName="MaiBot"
        isLoadingHistory={false}
        language="zh-CN"
        messages={[
          { content: '已连接会话', id: 'sys-1', timestamp: 1_753_353_600, type: 'system' },
          { content: '发送失败', id: 'err-1', timestamp: 1_753_353_601, type: 'error' },
        ]}
        userName="测试用户"
      />
    )

    expect(view.getByText('已连接会话')).toBeInTheDocument()
    expect(view.getByText('发送失败')).toBeInTheDocument()
    expect(view.container.querySelector('[data-message-id="sys-1"]')).not.toBeNull()
    expect(view.container.querySelector('[data-message-id="err-1"]')).not.toBeNull()
  })

  it('思考状态使用静音气泡并带上机器人名称', () => {
    const view = render(
      <MessageList
        botDisplayName="MaiBot"
        isLoadingHistory={false}
        language="zh-CN"
        messages={createMessages(2)}
        runtimeStatus={{ kind: 'thinking', stage: 'Planner', updatedAt: 1 }}
        userName="测试用户"
      />
    )

    const status = view.getByRole('status')
    expect(status).toHaveTextContent('chat.activity.thinking:MaiBot')
    expect(status.className).toContain('bg-muted/70')
    expect(status.className).not.toContain('bg-destructive/10')
    expect(view.getByText('chat.sidebar.subtitle:2')).toBeInTheDocument()
  })

  it('错误状态折叠空白、截断超长详情，并拼接重试后缀', () => {
    const longTail = 'x'.repeat(130)
    const detail = `  模型调用失败\n\n${longTail}  `
    const view = render(
      <MessageList
        botDisplayName="MaiBot"
        isLoadingHistory={false}
        language="zh-CN"
        messages={createMessages(1)}
        runtimeStatus={{
          detail,
          kind: 'error',
          retry: { attempt: 2, maxAttempts: 3 },
          updatedAt: 1,
        }}
        userName="测试用户"
      />
    )

    const collapsed = `模型调用失败 ${longTail}`
    const status = view.getByRole('status')
    expect(status.className).toContain('bg-destructive/10')
    expect(status).toHaveTextContent(
      `chat.activity.error:MaiBot retry 2/3: ${collapsed.slice(0, 120)}...`
    )
    expect(status.querySelector('[title]')?.getAttribute('title')).toBe(collapsed)
  })

  it('错误详情不超过 120 字时完整展示且不加重试后缀', () => {
    const view = render(
      <MessageList
        botDisplayName="MaiBot"
        isLoadingHistory={false}
        language="zh-CN"
        messages={createMessages(1)}
        runtimeStatus={{ detail: '后端超时', kind: 'error', updatedAt: 1 }}
        userName="测试用户"
      />
    )

    const status = view.getByRole('status')
    expect(status).toHaveTextContent('chat.activity.error:MaiBot: 后端超时')
    expect(status).not.toHaveTextContent('retry')
    expect(status.querySelector('[title]')?.getAttribute('title')).toBe('后端超时')
  })

  it('定位未知消息时直接返回 false 且不滚动虚拟列表', () => {
    const view = render(
      <MessageList
        botDisplayName="MaiBot"
        isLoadingHistory={false}
        language="zh-CN"
        messages={createMessages(6)}
        userName="测试用户"
      />
    )

    fireEvent.click(view.getByTestId('scroll-missing-message-0'))

    expect(virtualizerMocks.lastScrollResult).toBe(false)
    expect(virtualizerMocks.scrollToIndex).not.toHaveBeenCalled()
  })

  it('定位已渲染消息时滚动居中并短暂加上高亮 class', () => {
    vi.useFakeTimers()
    try {
      const view = render(
        <MessageList
          botDisplayName="MaiBot"
          isLoadingHistory={false}
          language="zh-CN"
          messages={createMessages(6)}
          userName="测试用户"
        />
      )

      fireEvent.click(view.getByTestId('scroll-self-message-0'))

      const row = view.container.querySelector('[data-message-id="message-0"]')
      expect(virtualizerMocks.lastScrollResult).toBe(true)
      expect(virtualizerMocks.scrollToIndex).toHaveBeenCalledWith(0, { align: 'center' })
      expect(row?.classList.contains('chat-message-flash')).toBe(true)

      vi.advanceTimersByTime(1600)
      expect(row?.classList.contains('chat-message-flash')).toBe(false)
    } finally {
      vi.useRealTimers()
    }
  })

  it('定位未挂载的远端消息时仍滚动到索引，但不会加上高亮', () => {
    const view = render(
      <MessageList
        botDisplayName="MaiBot"
        isLoadingHistory={false}
        language="zh-CN"
        messages={createMessages(20)}
        userName="测试用户"
      />
    )

    fireEvent.click(view.getByTestId('scroll-far-message-0'))

    expect(virtualizerMocks.lastScrollResult).toBe(true)
    expect(virtualizerMocks.scrollToIndex).toHaveBeenCalledWith(15, { align: 'center' })
    expect(view.container.querySelector('[data-message-id="message-15"]')).toBeNull()
    expect(view.container.querySelector('.chat-message-flash')).toBeNull()
  })

  it('再次定位会取消上一次尚未执行的高亮帧', () => {
    const view = render(
      <MessageList
        botDisplayName="MaiBot"
        isLoadingHistory={false}
        language="zh-CN"
        messages={createMessages(6)}
        userName="测试用户"
      />
    )

    let nextFrameId = 80
    const scheduled: number[] = []
    vi.mocked(window.requestAnimationFrame).mockImplementation(() => {
      const frameId = nextFrameId
      nextFrameId += 1
      scheduled.push(frameId)
      return frameId
    })
    const cancelSpy = vi.spyOn(window, 'cancelAnimationFrame')

    fireEvent.click(view.getByTestId('scroll-self-message-0'))
    fireEvent.click(view.getByTestId('scroll-self-message-0'))

    expect(scheduled.length).toBeGreaterThanOrEqual(2)
    expect(cancelSpy).toHaveBeenCalledWith(scheduled[0])
  })

  it('卸载时取消尚未执行的定位动画帧', () => {
    const view = render(
      <MessageList
        botDisplayName="MaiBot"
        isLoadingHistory={false}
        language="zh-CN"
        messages={createMessages(6)}
        userName="测试用户"
      />
    )

    vi.mocked(window.requestAnimationFrame).mockImplementation(() => 91)
    const cancelSpy = vi.spyOn(window, 'cancelAnimationFrame')

    fireEvent.click(view.getByTestId('scroll-self-message-0'))
    view.unmount()

    expect(cancelSpy).toHaveBeenCalledWith(91)
  })
})
