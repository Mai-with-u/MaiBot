import { Bot, Search, Settings, UserCircle2, UserRound, UsersRound, X } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { Avatar, AvatarFallback, AvatarImage } from '@/components/ui/avatar'
import { Input } from '@/components/ui/input'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { useResolvedAvatarUrl } from '@/lib/avatar-url'
import { cn } from '@/lib/utils'
import type { SessionInfo, StageStatusInfo } from '@/routes/monitor/use-maisaka-monitor'
import { StageStatusLabel } from '@/routes/monitor/StageStatusLabel'

import type { ChatMessage, ChatTab, ObservedMessagePreview } from './types'
import { getChatTabDisplayName } from './utils'

interface ChatWorkspaceSidebarProps {
  className?: string
  tabs: ChatTab[]
  activeTabId: string
  activeObservedSessionId: string | null
  observedSessions: Map<string, SessionInfo>
  observedSessionsError?: boolean
  observedStageStatuses: Map<string, StageStatusInfo>
  observedLatestMessages: Map<string, ObservedMessagePreview>
  onSwitch: (tabId: string) => void
  onSelectObserved: (sessionId: string) => void
  onOpenObservedSettings: (sessionId: string) => void
  onClose: (tabId: string, e?: React.MouseEvent | React.KeyboardEvent) => void
}

function getMessagePreview(message: ChatMessage | undefined, fallback: string) {
  if (!message) return fallback
  if (message.type === 'system' || message.type === 'error') return message.content || fallback
  return message.content || fallback
}

function ConversationItem({
  tab,
  active,
  onSwitch,
  onClose,
}: {
  tab: ChatTab
  active: boolean
  onSwitch: (id: string) => void
  onClose: (id: string, e?: React.MouseEvent | React.KeyboardEvent) => void
}) {
  const { t } = useTranslation()
  const isVirtual = tab.type === 'virtual'
  const lastMessage = tab.messages[tab.messages.length - 1]
  const preview = getMessagePreview(lastMessage, t('chat.sidebar.emptyPreview'))
  const displayName = getChatTabDisplayName(tab, t('chat.botNameFallback'))
  const Icon = isVirtual ? UserCircle2 : Bot
  const avatarUrl = useResolvedAvatarUrl(
    isVirtual ? tab.virtualConfig?.platform : 'qq',
    isVirtual ? tab.virtualConfig?.userId : tab.sessionInfo.bot_qq
  )
  const avatarAlt = isVirtual
    ? `${tab.virtualConfig?.userName || tab.label} 的头像`
    : `${displayName} 的头像`

  return (
    <div
      data-chat-sidebar-item="true"
      data-active={active ? 'true' : 'false'}
      className={cn(
        'group relative flex w-full min-w-0 items-center gap-1 rounded-xl pr-1 transition-colors',
        active
          ? 'bg-primary/12 text-foreground shadow-inner'
          : 'hover:bg-muted/70 text-foreground/90'
      )}
    >
      {active && (
        <span aria-hidden className="bg-primary absolute top-2 bottom-2 left-0 w-1 rounded-full" />
      )}
      <button
        type="button"
        className="flex w-full min-w-0 flex-1 items-center gap-2.5 overflow-hidden rounded-xl px-2.5 py-2 text-left"
        onClick={() => onSwitch(tab.id)}
      >
        <div className="relative shrink-0">
          <Avatar className="ring-border/60 h-9 w-9 ring-1">
            {avatarUrl && <AvatarImage src={avatarUrl} alt={avatarAlt} className="object-cover" />}
            <AvatarFallback
              className={cn(
                'text-xs',
                isVirtual
                  ? 'bg-secondary text-secondary-foreground'
                  : 'bg-primary-gradient text-primary-foreground'
              )}
            >
              <Icon className="h-5 w-5" />
            </AvatarFallback>
          </Avatar>
          <span
            aria-hidden
            className={cn(
              'border-card absolute right-0 bottom-0 h-3 w-3 rounded-full border-2 transition-colors',
              tab.isConnected ? 'bg-emerald-500' : 'bg-muted-foreground/40'
            )}
          />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex min-w-0 items-center justify-between gap-2">
            <span className="min-w-0 flex-1 truncate text-sm font-medium">{displayName}</span>
            {isVirtual && (
              <span className="bg-secondary text-secondary-foreground shrink-0 rounded-full px-1.5 py-0.5 text-[10px] font-medium tracking-wide">
                {t('chat.sidebar.virtualBadge')}
              </span>
            )}
          </div>
          <p className="text-muted-foreground mt-0.5 truncate text-xs">{preview}</p>
        </div>
      </button>

      {tab.id !== 'webui-default' && (
        <button
          type="button"
          aria-label={t('chat.sidebar.closeConversation', { label: displayName })}
          className="text-muted-foreground hover:bg-background hover:text-foreground rounded-md p-1 opacity-0 transition group-hover:opacity-100 focus-visible:opacity-100"
          onClick={(e) => onClose(tab.id, e)}
        >
          <X className="h-3.5 w-3.5" />
        </button>
      )}
    </div>
  )
}

function ObservedConversationItem({
  session,
  status,
  latestMessage,
  active,
  onSelect,
  onOpenSettings,
}: {
  session: SessionInfo
  status?: StageStatusInfo
  latestMessage?: ObservedMessagePreview
  active: boolean
  onSelect: (sessionId: string) => void
  onOpenSettings: (sessionId: string) => void
}) {
  const { t } = useTranslation()
  const targetId = session.isGroupChat ? session.groupId : session.userId
  const targetType = session.isGroupChat ? 'group' : 'user'
  const avatarUrl = useResolvedAvatarUrl(session.platform, targetId ?? undefined, targetType)
  const Icon = session.isGroupChat ? UsersRound : UserRound
  // 最新消息预览：优先正文，纯媒体消息退回媒体占位文案
  const messagePreview =
    latestMessage?.content || latestMessage?.mediaText || t('chat.sidebar.emptyPreview')
  const previewText =
    session.isGroupChat && latestMessage?.speakerName
      ? `${latestMessage.speakerName}: ${messagePreview}`
      : messagePreview
  return (
    <div
      data-chat-sidebar-item="true"
      data-active={active ? 'true' : 'false'}
      className={cn(
        'group relative flex w-full min-w-0 items-center gap-1 rounded-xl pr-1 transition-colors',
        active
          ? 'bg-primary/12 text-foreground shadow-inner'
          : 'hover:bg-muted/70 text-foreground/90'
      )}
    >
      {active && (
        <span aria-hidden className="bg-primary absolute top-2 bottom-2 left-0 w-1 rounded-full" />
      )}
      <button
        type="button"
        className="flex w-full min-w-0 flex-1 items-center gap-2.5 overflow-hidden rounded-xl px-2.5 py-2 text-left"
        onClick={() => onSelect(session.sessionId)}
      >
        <div className="relative shrink-0">
          <Avatar className="ring-border/60 h-9 w-9 ring-1">
            {avatarUrl && (
              <AvatarImage
                src={avatarUrl}
                alt={t('chat.sidebar.observedAvatarAlt', { name: session.sessionName })}
                className="object-cover"
              />
            )}
            <AvatarFallback className="bg-secondary text-secondary-foreground">
              <Icon className="h-4.5 w-4.5" />
            </AvatarFallback>
          </Avatar>
          <span
            aria-hidden
            className={cn(
              'border-card absolute right-0 bottom-0 h-3 w-3 rounded-full border-2',
              status?.agentState === 'wait'
                ? 'bg-blue-500'
                : status
                  ? 'bg-emerald-500'
                  : 'bg-muted-foreground/40'
            )}
          />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex min-w-0 items-center gap-1">
            <span className="min-w-0 flex-1 truncate text-sm font-medium">
              {session.sessionName}
            </span>
          </div>
          {status?.stage && (
            <p className="text-muted-foreground mt-0.5 truncate text-xs">
              <StageStatusLabel status={status} />
            </p>
          )}
          <p className="text-muted-foreground mt-0.5 truncate text-xs">{previewText}</p>
        </div>
      </button>
      <Tooltip>
        <TooltipTrigger asChild>
          <button
            type="button"
            aria-label={t('chat.sidebar.openSettings', { name: session.sessionName })}
            className="text-muted-foreground hover:bg-background hover:text-foreground rounded-md p-1 opacity-60 transition group-hover:opacity-100 focus-visible:opacity-100"
            onClick={() => onOpenSettings(session.sessionId)}
          >
            <Settings className="h-3.5 w-3.5" />
          </button>
        </TooltipTrigger>
        <TooltipContent side="right">
          {t('chat.sidebar.openSettings', { name: session.sessionName })}
        </TooltipContent>
      </Tooltip>
    </div>
  )
}

export function ChatWorkspaceSidebar({
  className,
  tabs,
  activeTabId,
  activeObservedSessionId,
  observedSessions,
  observedSessionsError = false,
  observedStageStatuses,
  observedLatestMessages,
  onSwitch,
  onSelectObserved,
  onOpenObservedSettings,
  onClose,
}: ChatWorkspaceSidebarProps) {
  const { t } = useTranslation()
  const [searchQuery, setSearchQuery] = useState('')
  const sortedObservedSessions = Array.from(observedSessions.values()).sort(
    (a, b) => b.lastActivity - a.lastActivity
  )

  // 顶部搜索框：按显示名过滤本地会话与观察聊天流
  const normalizedQuery = searchQuery.trim().toLowerCase()
  const filteredTabs = normalizedQuery
    ? tabs.filter((tab) =>
        getChatTabDisplayName(tab, t('chat.botNameFallback'))
          .toLowerCase()
          .includes(normalizedQuery)
      )
    : tabs
  const filteredObservedSessions = normalizedQuery
    ? sortedObservedSessions.filter((session) =>
        session.sessionName.toLowerCase().includes(normalizedQuery)
      )
    : sortedObservedSessions
  const showLocalSection = !normalizedQuery || filteredTabs.length > 0
  const showObservedSection = !normalizedQuery || filteredObservedSessions.length > 0
  const showNoResults =
    normalizedQuery.length > 0 && filteredTabs.length === 0 && filteredObservedSessions.length === 0

  return (
    <aside
      data-chat-workspace-sidebar="true"
      className={cn(
        'bg-card/90 supports-backdrop-filter:bg-card/70 flex h-full shrink-0 flex-col border-r backdrop-blur',
        'w-52 xl:w-56',
        className
      )}
    >
      {/* 顶部搜索框 */}
      <div className="border-border border-b p-2">
        <div className="relative">
          <Search
            aria-hidden
            className="text-muted-foreground pointer-events-none absolute top-1/2 left-2.5 h-3.5 w-3.5 -translate-y-1/2"
          />
          <Input
            type="search"
            value={searchQuery}
            onChange={(event) => setSearchQuery(event.target.value)}
            placeholder={t('chat.sidebar.searchPlaceholder')}
            aria-label={t('chat.sidebar.searchPlaceholder')}
            className="h-8 pl-8 text-xs"
          />
        </div>
      </div>

      {/* 会话列表：「与麦麦聊天」固定在上方，其余聊天流在下方独立滚动 */}
      <nav
        aria-label={t('chat.sidebar.conversations')}
        data-chat-sidebar-list="true"
        className="flex min-h-0 flex-1 flex-col"
      >
        {showLocalSection && (
          <section
            aria-labelledby="chat-sidebar-local-heading"
            data-chat-sidebar-section="local"
            className="mx-2 mt-2 shrink-0"
          >
            <h2
              id="chat-sidebar-local-heading"
              className="text-muted-foreground px-2.5 pt-0.5 pb-1 text-[11px] font-medium tracking-wide"
            >
              {t('chat.sidebar.myChats')}
            </h2>
            <div data-chat-sidebar-screen="true" className="space-y-0.5">
              {filteredTabs.map((tab) => (
                <ConversationItem
                  key={tab.id}
                  active={activeObservedSessionId === null && activeTabId === tab.id}
                  tab={tab}
                  onSwitch={onSwitch}
                  onClose={onClose}
                />
              ))}
            </div>
          </section>
        )}

        {showObservedSection && (
          <section
            aria-labelledby="chat-sidebar-observed-heading"
            data-chat-sidebar-section="observed"
            className={cn(
              'mx-2 flex min-h-0 flex-1 flex-col pt-2',
              showLocalSection && 'border-border mt-2 border-t'
            )}
          >
            <h2
              id="chat-sidebar-observed-heading"
              className="text-muted-foreground px-2.5 pt-0.5 pb-1 text-[11px] font-medium tracking-wide"
            >
              {t('chat.sidebar.observedChats')}
            </h2>
            <ScrollArea
              className="min-h-0 flex-1"
              contentClassName="!block w-full min-w-0"
              scrollbars="vertical"
              viewportClassName="[&>div]:!block [&>div]:!min-w-0 [&>div]:w-full"
            >
              <div data-chat-sidebar-screen="true" className="space-y-0.5 pb-2">
                {observedSessionsError ? (
                  <p className="text-destructive px-2.5 py-2 text-xs">
                    {t('chat.sidebar.loadObservedChatsFailed')}
                  </p>
                ) : filteredObservedSessions.length === 0 ? (
                  <p className="text-muted-foreground px-2.5 py-2 text-xs">
                    {t('chat.sidebar.waitingObservedChats')}
                  </p>
                ) : (
                  filteredObservedSessions.map((session) => (
                    <ObservedConversationItem
                      key={session.sessionId}
                      session={session}
                      status={observedStageStatuses.get(session.sessionId)}
                      latestMessage={observedLatestMessages.get(session.sessionId)}
                      active={activeObservedSessionId === session.sessionId}
                      onSelect={onSelectObserved}
                      onOpenSettings={onOpenObservedSettings}
                    />
                  ))
                )}
              </div>
            </ScrollArea>
          </section>
        )}

        {showNoResults && (
          <p className="text-muted-foreground px-4.5 py-2 text-xs">
            {t('chat.sidebar.noSearchResults')}
          </p>
        )}
      </nav>
    </aside>
  )
}
