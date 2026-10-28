import { Search, Settings, X } from 'lucide-react'
import { memo, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { ConversationAvatar, ConversationListItem } from '@/components/ui/conversation'
import { Button } from '@/components/ui/button'
import { StatusIndicator } from '@/components/ui/data-browser'
import { DataPanel, PageFrame } from '@/components/ui/surface'
import { Input } from '@/components/ui/input'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { useResolvedAvatarUrl } from '@/lib/avatar-url'
import { cn } from '@/lib/utils'
import type { SessionInfo, StageStatusInfo } from '@/routes/monitor/use-maisaka-monitor'
import { StageStatusLabel } from '@/routes/monitor/StageStatusLabel'
import { getStageStatusLight } from '@/routes/monitor/stage-status-light'

import type { ChatMessage, ChatTab, ObservedMessagePreview } from './types'
import { getChatTabDisplayName } from './utils'
import { ObservedConversationHoverCard } from './ObservedConversationHoverCard'

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

const ConversationItem = memo(function ConversationItem({
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
  const avatarUrl = useResolvedAvatarUrl(
    isVirtual ? tab.virtualConfig?.platform : 'qq',
    isVirtual ? tab.virtualConfig?.userId : tab.sessionInfo.bot_qq
  )
  return (
    <ConversationListItem
      title={displayName}
      selected={active}
      onSelect={() => onSwitch(tab.id)}
      status={isVirtual ? <span>{t('chat.sidebar.virtualBadge')}</span> : undefined}
      avatar={<ConversationAvatar src={avatarUrl} name={displayName} bot={!isVirtual} />}
      preview={
        <>
          <StatusIndicator variant="inline" status={tab.isConnected ? 'on' : 'off'} />{' '}
          {lastMessage ? (
            preview
          ) : (
            <>
              {isVirtual ? t('chat.sidebar.virtualBadge') : '本地聊天室'} ·{' '}
              {t(tab.isConnected ? 'chat.sidebar.online' : 'chat.sidebar.offline')}
            </>
          )}
        </>
      }
      actions={
        tab.id !== 'webui-default' && (
          <Button
            type="button"
            variant="ghost"
            size="icon-xs"
            aria-label={t('chat.sidebar.closeConversation', { label: displayName })}
            onClick={(e) => onClose(tab.id, e)}
          >
            <X />
          </Button>
        )
      }
    />
  )
})

const ObservedConversationItem = memo(function ObservedConversationItem({
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
  // 最新消息预览：优先正文，纯媒体消息退回媒体占位文案
  const messagePreview =
    latestMessage?.content || latestMessage?.mediaText || t('chat.sidebar.emptyPreview')
  const previewText =
    session.isGroupChat && latestMessage?.speakerName
      ? `${latestMessage.speakerName}: ${messagePreview}`
      : messagePreview
  return (
    <ObservedConversationHoverCard
      sessionId={session.sessionId}
      name={session.sessionName}
      platform={session.platform}
      targetId={targetId}
      isGroupChat={session.isGroupChat}
      status={status?.stage ? <StageStatusLabel status={status} /> : '暂无运行状态'}
      preview={previewText}
    >
      <ConversationListItem
        title={session.sessionName}
        preview={previewText}
        selected={active}
        onSelect={() => onSelect(session.sessionId)}
        avatar={<ConversationAvatar src={avatarUrl} name={session.sessionName} />}
        status={
          <span className="inline-flex min-w-0 max-w-full items-center gap-1">
            <StatusIndicator
              variant="inline"
              status={getStageStatusLight(status)}
            />
            {status?.stage && <span className="truncate"><StageStatusLabel status={status} /></span>}
          </span>
        }
        actions={
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                type="button"
                variant="ghost"
                size="icon-sm"
                aria-label={t('chat.sidebar.openSettings', { name: session.sessionName })}
                onClick={() => onOpenSettings(session.sessionId)}
              >
                <Settings />
              </Button>
            </TooltipTrigger>
            <TooltipContent side="right">
              {t('chat.sidebar.openSettings', { name: session.sessionName })}
            </TooltipContent>
          </Tooltip>
        }
      />
      </ObservedConversationHoverCard>
  )
}, (previous, next) => {
  // 父页面会重建会话/预览对象，只比较本行实际使用的字段。
  // 活动时间由父列表用于排序，事件数量不参与本行展示；稳定的 sessionId key 让 React 移动原有行。
  return previous.active === next.active
    && previous.onSelect === next.onSelect
    && previous.onOpenSettings === next.onOpenSettings
    && previous.session.sessionId === next.session.sessionId
    && previous.session.sessionName === next.session.sessionName
    && previous.session.isGroupChat === next.session.isGroupChat
    && previous.session.platform === next.session.platform
    && previous.session.groupId === next.session.groupId
    && previous.session.userId === next.session.userId
    && previous.status?.agentState === next.status?.agentState
    && previous.status?.stage === next.status?.stage
    && previous.status?.waitUntil === next.status?.waitUntil
    && previous.latestMessage?.content === next.latestMessage?.content
    && previous.latestMessage?.mediaText === next.latestMessage?.mediaText
    && previous.latestMessage?.speakerName === next.latestMessage?.speakerName
})

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
  const sortedObservedSessions = useMemo(
    () => Array.from(observedSessions.values()).sort((a, b) => b.lastActivity - a.lastActivity),
    [observedSessions]
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
  const filteredObservedSessions = useMemo(
    () => normalizedQuery
      ? sortedObservedSessions.filter((session) =>
          session.sessionName.toLowerCase().includes(normalizedQuery)
        )
      : sortedObservedSessions,
    [normalizedQuery, sortedObservedSessions]
  )
  const showLocalSection = !normalizedQuery || filteredTabs.length > 0
  const showObservedSection = !normalizedQuery || filteredObservedSessions.length > 0
  const showNoResults =
    normalizedQuery.length > 0 && filteredTabs.length === 0 && filteredObservedSessions.length === 0

  return (
    <PageFrame asChild>
      <aside data-chat-workspace-sidebar="true" className={cn('h-full', className)}>
        {/* 顶部搜索框 */}
        <div data-conversation-sidebar-search="true">
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
              controlSize="sm"
              className="pl-8 text-xs"
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
              className="shrink-0"
            >
              <h2
                id="chat-sidebar-local-heading"
                className="text-muted-foreground px-2.5 pt-0.5 pb-1 text-[11px] font-medium tracking-wide"
              >
                {t('chat.sidebar.myChats')}
              </h2>
              <DataPanel padding="none">
                {filteredTabs.map((tab) => (
                  <ConversationItem
                    key={tab.id}
                    active={activeObservedSessionId === null && activeTabId === tab.id}
                    tab={tab}
                    onSwitch={onSwitch}
                    onClose={onClose}
                  />
                ))}
              </DataPanel>
            </section>
          )}

          {showObservedSection && (
            <section
              aria-labelledby="chat-sidebar-observed-heading"
              data-chat-sidebar-section="observed"
              className="flex min-h-0 flex-1 flex-col"
            >
              <h2
                id="chat-sidebar-observed-heading"
                className="text-muted-foreground px-2.5 pt-0.5 pb-1 text-[11px] font-medium tracking-wide"
              >
                {t('chat.sidebar.observedChats')}
                <span>{filteredObservedSessions.length}</span>
              </h2>
              <DataPanel padding="none" className="flex min-h-0 flex-1 flex-col">
                <ScrollArea
                  data-conversation-scroll-list="true"
                  className="min-h-0 flex-1"
                  contentClassName="!block w-full min-w-0"
                  scrollbars="vertical"
                  viewportClassName="[&>div]:!block [&>div]:!min-w-0 [&>div]:w-full"
                >
                  <div>
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
              </DataPanel>
            </section>
          )}

          {showNoResults && (
            <p className="text-muted-foreground px-4.5 py-2 text-xs">
              {t('chat.sidebar.noSearchResults')}
            </p>
          )}
        </nav>
      </aside>
    </PageFrame>
  )
}
