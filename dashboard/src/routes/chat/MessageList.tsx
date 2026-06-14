import { Bot, Sparkles, User } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef } from 'react'
import { useTranslation } from 'react-i18next'

import { Avatar, AvatarFallback, AvatarImage } from '@/components/ui/avatar'
import { ScrollArea } from '@/components/ui/scroll-area'
import { useResolvedAvatarUrl } from '@/lib/avatar-url'
import { cn } from '@/lib/utils'

import { ChatScrollContext, type ChatScrollContextValue } from './ChatScrollContext'
import { RenderMessageContent } from './MessageRenderer'
import type { ChatMessage } from './types'

interface MessageListProps {
  messages: ChatMessage[]
  isLoadingHistory: boolean
  botDisplayName: string
  /** 机器人 QQ 号；存在时通过 WebUI 头像缓存接口加载 bot 头像。 */
  botQq?: string
  userName: string
  language: string
}

interface BubbleAvatarProps {
  type: 'user' | 'bot'
  visible: boolean
  /** bot 头像 URL（可选）；加载失败时自动 fallback 到默认 SVG 图标。 */
  imageUrl?: string
}

function BubbleAvatar({ type, visible, imageUrl }: BubbleAvatarProps) {
  return (
    <div className="h-8 w-8 shrink-0 sm:h-9 sm:w-9">
      {visible && (
        <Avatar className="h-full w-full ring-1 ring-border/60">
          {type === 'bot' && imageUrl ? (
            <AvatarImage src={imageUrl} alt="" className="object-cover" />
          ) : null}
          <AvatarFallback
            className={cn(
              'text-xs',
              type === 'user'
                ? 'bg-secondary text-secondary-foreground'
                : 'bg-primary-gradient text-primary-foreground'
            )}
          >
            {type === 'user' ? (
              <User className="h-4 w-4" />
            ) : (
              <Bot className="h-4 w-4" />
            )}
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

/**
 * 聊天消息列表：支持连续同发送者消息分组、富文本与系统/错误信息样式。
 */
export function MessageList({
  messages,
  isLoadingHistory,
  botDisplayName,
  botQq,
  userName,
  language,
}: MessageListProps) {
  const { t } = useTranslation()
  const viewportRef = useRef<HTMLDivElement>(null)
  const endRef = useRef<HTMLDivElement>(null)
  const messageRefs = useRef<Map<string, HTMLDivElement>>(new Map())

  useEffect(() => {
    const viewport = viewportRef.current
    if (!viewport) {
      return
    }

    viewport.scrollTo({
      top: viewport.scrollHeight,
      behavior: 'smooth',
    })
  }, [messages])

  const scrollToMessage = useCallback((messageId: string) => {
    const viewport = viewportRef.current
    const target = messageRefs.current.get(messageId)
    if (!target || !viewport) {
      return false
    }

    const viewportRect = viewport.getBoundingClientRect()
    const targetRect = target.getBoundingClientRect()
    const targetTop =
      viewport.scrollTop +
      targetRect.top -
      viewportRect.top -
      viewport.clientHeight / 2 +
      targetRect.height / 2

    viewport.scrollTo({
      top: Math.max(0, targetTop),
      behavior: 'smooth',
    })
    target.classList.add('chat-message-flash')
    window.setTimeout(() => {
      target.classList.remove('chat-message-flash')
    }, 1600)
    return true
  }, [])

  const scrollContextValue = useMemo<ChatScrollContextValue>(
    () => ({ scrollToMessage }),
    [scrollToMessage]
  )

  const formatTime = (timestamp: number) => {
    const date = new Date(timestamp * 1000)
    return date.toLocaleTimeString(language || 'zh-CN', {
      hour: '2-digit',
      minute: '2-digit',
    })
  }

  const botAvatarUrl = useResolvedAvatarUrl('qq', botQq)

  if (messages.length === 0 && !isLoadingHistory) {
    return (
      <div className="min-w-0 min-h-0 flex-1 overflow-hidden">
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
    <div className="min-w-0 min-h-0 flex-1 overflow-hidden">
      <ScrollArea
        className="h-full w-full"
        contentClassName="!block w-full min-w-0"
        scrollbars="vertical"
        viewportRef={viewportRef}
        viewportClassName="[&>div]:!block [&>div]:!min-w-0 [&>div]:w-full"
      >
        <ChatScrollContext.Provider value={scrollContextValue}>
          <div className="mx-auto flex w-full max-w-4xl min-w-0 flex-col gap-1 px-3 py-5 sm:px-6 sm:py-6">
          {messages.map((message, index) => {
            // 系统消息：作为分隔条
            if (message.type === 'system') {
              return (
                <div key={message.id} className="my-2 flex items-center gap-3">
                  <div className="bg-border/60 h-px flex-1" />
                  <span className="text-muted-foreground bg-card/70 rounded-full border px-3 py-0.5 text-[11px]">
                    {message.content}
                  </span>
                  <div className="bg-border/60 h-px flex-1" />
                </div>
              )
            }

            // 错误消息
            if (message.type === 'error') {
              return (
                <div key={message.id} className="my-2 flex justify-center">
                  <div className="bg-destructive/10 text-destructive border-destructive/30 rounded-full border px-3 py-1 text-xs">
                    {message.content}
                  </div>
                </div>
              )
            }

            const isUser = message.type === 'user'
            const bubbleType: 'user' | 'bot' = isUser ? 'user' : 'bot'

            // 是否与上一条消息属于同一发送者（用于分组：仅首条显示头像 + 名字）
            const previous = messages[index - 1]
            const sameGroup =
              previous &&
              previous.type === message.type &&
              (previous.sender?.user_id ?? previous.sender?.name) ===
                (message.sender?.user_id ?? message.sender?.name)

            const senderName =
              message.sender?.name || (isUser ? userName : botDisplayName)

            return (
              <div
                key={message.id}
                ref={(node) => {
                  if (node) {
                    messageRefs.current.set(message.id, node)
                  } else {
                    messageRefs.current.delete(message.id)
                  }
                }}
                data-message-id={message.id}
                className={cn(
                  'chat-message-row flex w-full min-w-0 items-end gap-2 sm:gap-3',
                  isUser ? 'flex-row-reverse' : 'flex-row',
                  sameGroup ? 'mt-0.5' : 'mt-3 first:mt-0'
                )}
              >
                <BubbleAvatar
                  type={bubbleType}
                  visible={!sameGroup}
                  imageUrl={bubbleType === 'bot' ? botAvatarUrl : undefined}
                />

                <div
                  className={cn(
                    'flex min-w-0 max-w-[80%] flex-col sm:max-w-[70%]',
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
                      'shadow-sm/30 wrap-break-word min-w-0 max-w-full overflow-hidden px-3.5 py-2 text-sm leading-relaxed',
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
          })}
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
