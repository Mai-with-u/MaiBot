import { defaultRangeExtractor, useVirtualizer, type Range } from '@tanstack/react-virtual'
import { Bot, Sparkles, User } from 'lucide-react'
import {
  type ReactNode,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from 'react'
import { useTranslation } from 'react-i18next'

import { Avatar, AvatarFallback, AvatarImage } from '@/components/ui/avatar'
import { ScrollArea } from '@/components/ui/scroll-area'
import { useResolvedAvatarUrl } from '@/lib/avatar-url'
import { cn } from '@/lib/utils'

import { ChatScrollContext, type ChatScrollContextValue } from './ChatScrollContext'
import { RenderMessageContent } from './MessageRenderer'
import type { ChatMessage, ChatRuntimeStatus } from './types'

interface MessageListProps {
  messages: ChatMessage[]
  isLoadingHistory: boolean
  botDisplayName: string
  /** 机器人 QQ 号；存在时通过 WebUI 头像缓存接口加载 bot 头像。 */
  botQq?: string
  userName: string
  userAvatarPlatform?: string
  userAvatarId?: string
  userAvatarVersion?: number
  language: string
  runtimeStatus?: ChatRuntimeStatus | null
}

interface ScrollAnchor {
  messageId: string
  offsetTop: number
}

interface BubbleAvatarProps {
  type: 'user' | 'bot'
  visible: boolean
  /** 头像 URL（可选）；加载失败时自动 fallback 到默认 SVG 图标。 */
  imageUrl?: string
}

function BubbleAvatar({ type, visible, imageUrl }: BubbleAvatarProps) {
  return (
    <div className="h-8 w-8 shrink-0 sm:h-9 sm:w-9">
      {visible && (
        <Avatar className="ring-border/60 h-full w-full ring-1">
          {imageUrl ? <AvatarImage src={imageUrl} alt="" className="object-cover" /> : null}
          <AvatarFallback
            className={cn(
              'text-xs',
              type === 'user'
                ? 'bg-secondary text-secondary-foreground'
                : 'bg-primary-gradient text-primary-foreground'
            )}
          >
            {type === 'user' ? <User className="h-4 w-4" /> : <Bot className="h-4 w-4" />}
          </AvatarFallback>
        </Avatar>
      )}
    </div>
  )
}

function EmptyState({ botName }: { botName: string }) {
  const { t } = useTranslation()
  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 px-6 py-16 text-center">
      <div className="bg-primary-gradient text-primary-foreground relative flex h-16 w-16 items-center justify-center rounded-2xl shadow-lg">
        <Sparkles className="h-7 w-7" />
        <span className="bg-primary/30 absolute inset-0 -z-10 animate-pulse rounded-2xl blur-xl" />
      </div>
      <div className="space-y-1">
        <h2 className="text-base font-semibold sm:text-lg">
          {t('chat.message.empty', { bot: botName })}
        </h2>
        <p className="text-muted-foreground text-xs sm:text-sm">{t('chat.message.emptyHint')}</p>
      </div>
    </div>
  )
}

function RuntimeStatusIndicator({
  botDisplayName,
  status,
}: {
  botDisplayName: string
  status: ChatRuntimeStatus
}) {
  const { t } = useTranslation()
  const errorDetail =
    status.kind === 'error' ? (status.detail || '').replace(/\s+/g, ' ').trim() : ''
  const visibleErrorDetail =
    errorDetail.length > 120 ? `${errorDetail.slice(0, 120)}...` : errorDetail
  const retryText = status.retry
    ? t('chat.activity.retrySuffix', {
        attempt: status.retry.attempt,
        max: status.retry.maxAttempts,
      })
    : ''
  const label = t(`chat.activity.${status.kind}`, {
    bot: botDisplayName,
    retry: retryText,
  })
  const displayText =
    visibleErrorDetail && status.kind === 'error' ? `${label}: ${visibleErrorDetail}` : label

  return (
    <div className="mt-3 flex w-full items-end gap-2 sm:gap-3">
      <BubbleAvatar type="bot" visible={false} />
      <div
        className={cn(
          'flex max-w-[80%] items-center gap-2 rounded-2xl rounded-bl-md px-3.5 py-2 text-xs sm:max-w-[70%]',
          status.kind === 'error'
            ? 'border-destructive/30 bg-destructive/10 text-destructive border'
            : 'bg-muted/70 text-muted-foreground'
        )}
        role="status"
        aria-live="polite"
      >
        <span className="flex h-4 items-center gap-1" aria-hidden="true">
          <span
            className={cn(
              'h-1.5 w-1.5 animate-pulse rounded-full',
              status.kind === 'error' ? 'bg-destructive/80' : 'bg-primary/70'
            )}
          />
          <span
            className={cn(
              'h-1.5 w-1.5 animate-pulse rounded-full [animation-delay:150ms]',
              status.kind === 'error' ? 'bg-destructive/70' : 'bg-primary/60'
            )}
          />
          <span
            className={cn(
              'h-1.5 w-1.5 animate-pulse rounded-full [animation-delay:300ms]',
              status.kind === 'error' ? 'bg-destructive/60' : 'bg-primary/50'
            )}
          />
        </span>
        <span className="min-w-0 truncate" title={errorDetail || undefined}>
          {displayText}
        </span>
      </div>
    </div>
  )
}

/**
 * 聊天消息列表：支持连续同发送者消息分组、富文本与系统/错误信息样式。
 */
export function MessageList({
  messages,
  isLoadingHistory,
  botDisplayName,
  botQq,
  userName,
  userAvatarPlatform,
  userAvatarId,
  userAvatarVersion,
  language,
  runtimeStatus,
}: MessageListProps) {
  const { t } = useTranslation()
  const viewportRef = useRef<HTMLDivElement>(null)
  const endRef = useRef<HTMLDivElement>(null)
  const messageRefs = useRef<Map<string, HTMLDivElement>>(new Map())
  const isNearBottomRef = useRef(true)
  const highlightFrameRef = useRef<number | null>(null)
  const highlightTimerRef = useRef<number | null>(null)
  const highlightedElementRef = useRef<HTMLDivElement | null>(null)
  const previousMessagesRef = useRef(messages)
  const scrollAnchorsRef = useRef<ScrollAnchor[]>([])
  const [playingMessageIds, setPlayingMessageIds] = useState<Set<string>>(
    () => new Set()
  )

  const captureScrollAnchors = useCallback(() => {
    const viewport = viewportRef.current
    if (!viewport || isNearBottomRef.current) {
      scrollAnchorsRef.current = []
      return
    }

    const viewportRect = viewport.getBoundingClientRect()
    const anchors: ScrollAnchor[] = []
    messageRefs.current.forEach((element, messageId) => {
      const elementRect = element.getBoundingClientRect()
      if (
        elementRect.bottom <= viewportRect.top ||
        elementRect.top >= viewportRect.bottom
      ) {
        return
      }
      anchors.push({
        messageId,
        offsetTop: elementRect.top - viewportRect.top,
      })
    })
    anchors.sort((left, right) => left.offsetTop - right.offsetTop)
    scrollAnchorsRef.current = anchors
  }, [])

  useEffect(() => {
    const viewport = viewportRef.current
    if (!viewport) {
      return
    }

    const updateNearBottom = () => {
      const distanceFromBottom = viewport.scrollHeight - viewport.scrollTop - viewport.clientHeight
      isNearBottomRef.current = distanceFromBottom <= 80
      captureScrollAnchors()
    }
    updateNearBottom()
    viewport.addEventListener('scroll', updateNearBottom, { passive: true })
    return () => viewport.removeEventListener('scroll', updateNearBottom)
  }, [captureScrollAnchors])

  const messageIndexById = useMemo(
    () => new Map(messages.map((message, index) => [message.id, index])),
    [messages]
  )
  const rangeExtractor = useCallback(
    (range: Range) => {
      const indexes = new Set(defaultRangeExtractor(range))
      for (const messageId of playingMessageIds) {
        const index = messageIndexById.get(messageId)
        if (index !== undefined) {
          indexes.add(index)
        }
      }
      return Array.from(indexes).sort((left, right) => left - right)
    },
    [messageIndexById, playingMessageIds]
  )

  const rowVirtualizer = useVirtualizer({
    count: messages.length,
    getScrollElement: () => viewportRef.current,
    estimateSize: () => 96,
    getItemKey: (index) => messages[index]?.id ?? index,
    overscan: 8,
    rangeExtractor,
  })
  const virtualListSize = rowVirtualizer.getTotalSize()

  useLayoutEffect(() => {
    const previousMessages = previousMessagesRef.current
    previousMessagesRef.current = messages

    if (
      isNearBottomRef.current ||
      previousMessages.length !== messages.length ||
      messages.length === 0
    ) {
      return
    }

    const firstRetainedIndex = previousMessages.findIndex(
      (message) => message.id === messages[0].id
    )
    if (firstRetainedIndex <= 0) {
      return
    }

    const retainedMessageCount = previousMessages.length - firstRetainedIndex
    const retainedSequenceMatches = previousMessages
      .slice(firstRetainedIndex)
      .every((message, index) => message.id === messages[index]?.id)
    if (!retainedSequenceMatches || retainedMessageCount >= messages.length) {
      return
    }

    const anchor = scrollAnchorsRef.current.find((candidate) =>
      messageIndexById.has(candidate.messageId)
    )
    const viewport = viewportRef.current
    const target = anchor ? messageRefs.current.get(anchor.messageId) : null
    if (!anchor || !viewport || !target) {
      return
    }

    const viewportTop = viewport.getBoundingClientRect().top
    const offsetDelta = target.getBoundingClientRect().top - viewportTop - anchor.offsetTop
    if (Number.isFinite(offsetDelta) && Math.abs(offsetDelta) >= 0.5) {
      viewport.scrollTop += offsetDelta
    }
    captureScrollAnchors()
  }, [captureScrollAnchors, messageIndexById, messages])

  useEffect(() => {
    if (!isNearBottomRef.current) {
      return
    }

    const frameId = window.requestAnimationFrame(() => {
      const viewport = viewportRef.current
      if (!viewport) {
        return
      }
      viewport.scrollTo({
        top: viewport.scrollHeight,
        behavior: 'auto',
      })
    })
    return () => window.cancelAnimationFrame(frameId)
  }, [messages, runtimeStatus, virtualListSize])

  useEffect(() => {
    return () => {
      if (highlightFrameRef.current !== null) {
        window.cancelAnimationFrame(highlightFrameRef.current)
      }
      if (highlightTimerRef.current !== null) {
        window.clearTimeout(highlightTimerRef.current)
      }
    }
  }, [])

  const scrollToMessage = useCallback(
    (messageId: string) => {
      const messageIndex = messageIndexById.get(messageId)
      if (messageIndex === undefined) {
        return false
      }

      rowVirtualizer.scrollToIndex(messageIndex, { align: 'center' })
      highlightedElementRef.current?.classList.remove('chat-message-flash')
      if (highlightFrameRef.current !== null) {
        window.cancelAnimationFrame(highlightFrameRef.current)
      }
      if (highlightTimerRef.current !== null) {
        window.clearTimeout(highlightTimerRef.current)
      }

      let attempts = 0
      const highlight = () => {
        const target = messageRefs.current.get(messageId)
        if (!target && attempts < 5) {
          attempts += 1
          highlightFrameRef.current = window.requestAnimationFrame(highlight)
          return
        }
        highlightFrameRef.current = null
        if (!target) {
          return
        }

        highlightedElementRef.current = target
        target.classList.add('chat-message-flash')
        highlightTimerRef.current = window.setTimeout(() => {
          target.classList.remove('chat-message-flash')
          if (highlightedElementRef.current === target) {
            highlightedElementRef.current = null
          }
          highlightTimerRef.current = null
        }, 1600)
      }
      highlightFrameRef.current = window.requestAnimationFrame(highlight)
      return true
    },
    [messageIndexById, rowVirtualizer]
  )

  const scrollContextValue = useMemo<ChatScrollContextValue>(
    () => ({ scrollToMessage }),
    [scrollToMessage]
  )

  const timeFormatter = useMemo(
    () =>
      new Intl.DateTimeFormat(language || 'zh-CN', {
        hour: '2-digit',
        minute: '2-digit',
      }),
    [language]
  )
  const formatTime = (timestamp: number) => timeFormatter.format(new Date(timestamp * 1000))

  const botAvatarUrl = useResolvedAvatarUrl('qq', botQq)
  const userAvatarUrl = useResolvedAvatarUrl(
    userAvatarPlatform,
    userAvatarId,
    'user',
    userAvatarVersion
  )

  if (messages.length === 0 && !isLoadingHistory) {
    return (
      <div className="min-h-0 min-w-0 flex-1 overflow-hidden">
        <ScrollArea
          className="h-full w-full"
          contentClassName="!block w-full min-w-0"
          scrollbars="vertical"
          viewportRef={viewportRef}
          viewportClassName="[&>div]:!block [&>div]:!min-w-0 [&>div]:w-full"
        >
          <EmptyState botName={botDisplayName} />
        </ScrollArea>
      </div>
    )
  }

  return (
    <div className="min-h-0 min-w-0 flex-1 overflow-hidden">
      <ScrollArea
        className="h-full w-full"
        contentClassName="!block w-full min-w-0"
        scrollbars="vertical"
        viewportRef={viewportRef}
        viewportClassName="[&>div]:!block [&>div]:!min-w-0 [&>div]:w-full"
      >
        <ChatScrollContext.Provider value={scrollContextValue}>
          <div className="mx-auto flex w-full max-w-4xl min-w-0 flex-col gap-1 px-3 py-5 sm:px-6 sm:py-6">
            <div
              className="relative w-full"
              style={{ height: `${virtualListSize}px` }}
            >
              {rowVirtualizer.getVirtualItems().map((virtualRow) => {
                const index = virtualRow.index
                const message = messages[index]
                if (!message) {
                  return null
                }

                const previous = messages[index - 1]
                const sameGroup = Boolean(
                  previous &&
                  previous.type === message.type &&
                  (previous.sender?.user_id ?? previous.sender?.name) ===
                    (message.sender?.user_id ?? message.sender?.name)
                )
                let rowContent: ReactNode
                let spacingClass = 'py-2'

                // 系统消息：作为分隔条
                if (message.type === 'system') {
                  rowContent = (
                    <div className="flex items-center gap-3">
                      <div className="bg-border/60 h-px flex-1" />
                      <span className="text-muted-foreground bg-card/70 rounded-full border px-3 py-0.5 text-[11px]">
                        {message.content}
                      </span>
                      <div className="bg-border/60 h-px flex-1" />
                    </div>
                  )
                } else if (message.type === 'error') {
                  // 错误消息
                  rowContent = (
                    <div className="flex justify-center">
                      <div className="bg-destructive/10 text-destructive border-destructive/30 rounded-full border px-3 py-1 text-xs">
                        {message.content}
                      </div>
                    </div>
                  )
                } else {
                  const isUser = message.type === 'user'
                  const bubbleType: 'user' | 'bot' = isUser ? 'user' : 'bot'
                  const senderName = message.sender?.name || (isUser ? userName : botDisplayName)
                  spacingClass = index === 0 ? 'pb-0.5' : sameGroup ? 'py-0.5' : 'pt-3 pb-0.5'
                  rowContent = (
                    <div
                      className={cn(
                        'chat-message-row flex w-full min-w-0 items-end gap-2 sm:gap-3',
                        isUser ? 'flex-row-reverse' : 'flex-row'
                      )}
                    >
                      <BubbleAvatar
                        type={bubbleType}
                        visible={!sameGroup}
                        imageUrl={bubbleType === 'bot' ? botAvatarUrl : userAvatarUrl}
                      />

                      <div
                        className={cn(
                          'flex max-w-[80%] min-w-0 flex-col sm:max-w-[70%]',
                          isUser ? 'items-end' : 'items-start'
                        )}
                      >
                        {!sameGroup && (
                          <div
                            className={cn(
                              'text-muted-foreground mb-1 flex items-center gap-2 px-1 text-[11px]',
                              isUser && 'flex-row-reverse'
                            )}
                          >
                            <span className="hidden font-medium sm:inline">{senderName}</span>
                            <span>{formatTime(message.timestamp)}</span>
                          </div>
                        )}

                        <div
                          className={cn(
                            'max-w-full min-w-0 overflow-hidden px-3.5 py-2 text-sm leading-relaxed wrap-break-word shadow-sm/30',
                            isUser
                              ? 'bg-primary text-primary-foreground rounded-2xl rounded-br-md'
                              : 'bg-muted text-foreground rounded-2xl rounded-bl-md'
                          )}
                        >
                          <RenderMessageContent message={message} />
                        </div>
                      </div>
                    </div>
                  )
                }

                return (
                  <div
                    key={message.id}
                    ref={(node) => {
                      if (node) {
                        rowVirtualizer.measureElement(node)
                        messageRefs.current.set(message.id, node)
                      } else {
                        messageRefs.current.delete(message.id)
                      }
                    }}
                    data-index={virtualRow.index}
                    data-message-id={message.id}
                    className={cn('absolute top-0 left-0 w-full', spacingClass)}
                    style={{ transform: `translateY(${virtualRow.start}px)` }}
                    onPlayCapture={() => {
                      setPlayingMessageIds((current) => {
                        if (current.has(message.id)) return current
                        const next = new Set(current)
                        next.add(message.id)
                        return next
                      })
                    }}
                    onPauseCapture={() => {
                      setPlayingMessageIds((current) => {
                        if (!current.has(message.id)) return current
                        const next = new Set(current)
                        next.delete(message.id)
                        return next
                      })
                    }}
                  >
                    {rowContent}
                  </div>
                )
              })}
            </div>
            {runtimeStatus && (
              <RuntimeStatusIndicator botDisplayName={botDisplayName} status={runtimeStatus} />
            )}
            <div ref={endRef} />
            {/* 用于读屏 / 避免悬空 */}
            <span className="sr-only" aria-live="polite">
              {messages.length > 0 ? t('chat.sidebar.subtitle', { count: messages.length }) : ''}
            </span>
          </div>
        </ChatScrollContext.Provider>
      </ScrollArea>
    </div>
  )
}
