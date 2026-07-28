import type { ReactNode, RefObject } from 'react'

import { fireEvent, render } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { MessageList } from './MessageList'
import type { ChatMessage } from './types'

const virtualizerMocks = vi.hoisted(() => ({
  measureElement: vi.fn(),
  scrollToIndex: vi.fn(),
  visibleStart: 0,
}))

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
  useTranslation: () => ({ t: (key: string) => key }),
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
vi.mock('./MessageRenderer', () => ({
  RenderMessageContent: ({ message }: { message: ChatMessage }) =>
    message.id === 'message-0' ? (
      <audio controls data-testid="message-audio">
        <track kind="captions" src="" label="测试字幕" default />
      </audio>
    ) : (
      <>{message.content}</>
    ),
}))

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
})
