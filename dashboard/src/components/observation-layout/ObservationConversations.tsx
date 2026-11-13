import { useNavigate, useRouterState } from '@tanstack/react-router'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { MessagesSquare, Settings } from 'lucide-react'
import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { ChatStreamSettingsDialog, type ChatStreamSettingsTarget } from '@/components/chat-stream-settings-dialog'
import { Button } from '@/components/ui/button'
import { ConversationAvatar, ConversationListItem } from '@/components/ui/conversation'
import { StatusIndicator } from '@/components/ui/data-browser'
import { Input } from '@/components/ui/input'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { VirtualList } from '@/components/ui/virtual-list'
import { useFeatureFlags } from '@/hooks/use-feature-flags'
import { useHostRuntime } from '@/host/state'
import { backendApi } from '@/lib/http'
import { useResolvedAvatarUrl } from '@/lib/avatar-url'
import { getAllChatStreams } from '@/lib/chat-management-api'
import { useLocalBotAvatarUrl } from '@/routes/chat/local-bot-avatar'
import { buildObservedSessions, isFocusFlowId } from '@/routes/chat/observed-sessions'
import { StageStatusLabel } from '@/routes/monitor/StageStatusLabel'
import { getStageStatusLight } from '@/routes/monitor/stage-status-light'
import { useMaisakaMonitorOverview } from '@/routes/monitor/use-maisaka-monitor'
import type { SessionInfo, StageStatusInfo } from '@/routes/monitor/use-maisaka-monitor'

/** 状态灯的悬停说明：灯本身只有颜色，靠这里告诉人它代表什么。 */
const STATUS_TITLE: Record<ReturnType<typeof getStageStatusLight>, string> = {
  on: '待命中',
  mixed: '正在处理',
  off: '未在观察',
  error: '出错',
}

type Selection = { kind: 'chat' } | { kind: 'agent' } | { kind: 'observed'; id: string } | { kind: 'none' }

type ObservedListEntry =
  | { kind: 'heading'; key: string; title: string }
  | { kind: 'session'; key: string; session: SessionInfo }
  | { kind: 'local'; key: string; agent: boolean }
  | { kind: 'notice'; key: string }

const observedItemKey = (item: ObservedListEntry) => item.key
const observedItemHeight = (item: ObservedListEntry) => item.kind === 'heading' ? 28 : 72

function StackedAvatar({ session }: { session: SessionInfo }) {
  const avatarUrl = useResolvedAvatarUrl(
    session.platform,
    (session.isGroupChat ? session.groupId : session.userId) ?? undefined,
    session.isGroupChat ? 'group' : 'user'
  )
  return <ConversationAvatar src={avatarUrl} name={session.sessionName} />
}

function previewText(data: unknown): string {
  if (!data || typeof data !== 'object') return ''
  const record = data as { content?: string; speaker_name?: string; media?: { text: string }[] }
  const content = record.content?.trim() || record.media?.find((media) => media.text.trim())?.text.trim() || ''
  return record.speaker_name && content ? `${record.speaker_name}: ${content}` : content
}

const ObservedRow = memo(function ObservedRow({
  session,
  status,
  preview,
  active,
  collapsed,
  onSelect,
  onOpenSettings,
}: {
  session: SessionInfo
  status?: StageStatusInfo
  preview: string
  active: boolean
  collapsed: boolean
  onSelect: (sessionId: string) => void
  onOpenSettings: (chat: ChatStreamSettingsTarget) => void
}) {
  const { t } = useTranslation()
  const targetId = session.isGroupChat ? session.groupId : session.userId
  const avatarUrl = useResolvedAvatarUrl(session.platform, targetId ?? undefined, session.isGroupChat ? 'group' : 'user')
  return (
    <div title={collapsed ? session.sessionName : undefined}>
      <ConversationListItem
        title={session.sessionName}
        selected={active}
        onSelect={() => onSelect(session.sessionId)}
        avatar={<ConversationAvatar src={avatarUrl} name={session.sessionName} />}
        status={
          <span className="inline-flex min-w-0 items-center gap-1">
            <StatusIndicator
              variant="inline"
              status={getStageStatusLight(status)}
              title={STATUS_TITLE[getStageStatusLight(status)]}
            />
          </span>
        }
        preview={status?.stage ? <StageStatusLabel status={status} /> : preview || t('chat.sidebar.emptyPreview')}
        actions={!collapsed && !isFocusFlowId(session.sessionId) ? (
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                type="button"
                variant="ghost"
                size="icon-sm"
                aria-label={t('chat.sidebar.openSettings', { name: session.sessionName })}
                onClick={() => onOpenSettings({ session_id: session.sessionId, display_name: session.sessionName })}
              >
                <Settings />
              </Button>
            </TooltipTrigger>
            <TooltipContent side="right">
              {t('chat.sidebar.openSettings', { name: session.sessionName })}
            </TooltipContent>
          </Tooltip>
        ) : undefined}
      />
    </div>
  )
})

/**
 * 侧栏下半区：所有对话。数据与聊天页同源（聊天流列表 + 监控概览），
 * 点一下跳到对应页面，选择状态完全由路由决定。
 */
export function ObservationConversations({ collapsed }: { collapsed: boolean }) {
  const { t } = useTranslation()
  const { selected_bot_id } = useHostRuntime()
  const { data: chatInfo } = useQuery({
    queryKey: ['local-chat-info', selected_bot_id],
    queryFn: () => backendApi.get<{ bot_qq: string | null }>('/api/chat/info'),
    staleTime: 30_000,
  })
  const localBotAvatarUrl = useLocalBotAvatarUrl(chatInfo?.bot_qq ?? undefined)
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const pathname = useRouterState({ select: (state) => state.location.pathname })
  const searchStr = useRouterState({ select: (state) => state.location.searchStr })
  const { flags } = useFeatureFlags()
  const { sessions: monitorSessions, stageStatuses, latestMessages } = useMaisakaMonitorOverview()
  const [query, setQuery] = useState('')
  const [chatsOpen, setChatsOpen] = useState(false)
  const [settingsChat, setSettingsChat] = useState<ChatStreamSettingsTarget | null>(null)
  const { data: chatStreams = [], isError } = useQuery({
    queryKey: ['chat-streams', 'all'],
    queryFn: getAllChatStreams,
    staleTime: 30_000,
  })

  // 监控中新出现的会话可能刚写入数据库，及时刷新已知聊天流列表。
  const previousIds = useRef(new Set(monitorSessions.keys()))
  useEffect(() => {
    const currentIds = new Set(monitorSessions.keys())
    const hasNew = Array.from(currentIds).some((id) => !previousIds.current.has(id))
    previousIds.current = currentIds
    if (hasNew) void queryClient.invalidateQueries({ queryKey: ['chat-streams', 'all'] })
  }, [monitorSessions, queryClient])

  const { sessions } = useMemo(
    () => buildObservedSessions(chatStreams, monitorSessions, stageStatuses),
    [chatStreams, monitorSessions, stageStatuses]
  )
  const sorted = useMemo(
    () => Array.from(sessions.values()).filter((session) => session.platform !== 'internal').sort((a, b) => b.lastActivity - a.lastActivity),
    [sessions]
  )
  const normalizedQuery = query.trim().toLowerCase()
  const observedItems = useMemo<ObservedListEntry[]>(() => {
    const filtered = normalizedQuery
      ? sorted.filter((session) => session.sessionName.toLowerCase().includes(normalizedQuery))
      : sorted
    return [
      { title: t('observation.focusFlows'), items: filtered.filter((session) => isFocusFlowId(session.sessionId)) },
      { title: t('chat.sidebar.observedChats'), items: filtered.filter((session) => !isFocusFlowId(session.sessionId)) },
    ].filter((section) => section.items.length > 0).flatMap<ObservedListEntry>((section) => [
      { kind: 'heading', key: `section:${section.title}`, title: section.title },
      ...section.items.map((session): ObservedListEntry => ({ kind: 'session', key: session.sessionId, session })),
    ])
  }, [normalizedQuery, sorted, t])

  const params = new URLSearchParams(searchStr)
  const selection: Selection =
    pathname === '/chat'
      ? params.get('observe')
        ? { kind: 'observed', id: params.get('observe')! }
        : params.get('agent') === 'true'
          ? { kind: 'agent' }
          : { kind: 'chat' }
      : { kind: 'none' }

  const openObserved = useCallback((sessionId: string) => {
    setChatsOpen(false)
    void navigate({ to: '/chat', search: { observe: sessionId } })
  }, [navigate])

  const openSettings = useCallback((chat: ChatStreamSettingsTarget) => {
    setChatsOpen(false)
    setSettingsChat(chat)
  }, [])

  const renderObservedItem = (item: ObservedListEntry) => {
    if (item.kind === 'heading') return <h3 data-observation-list-section="true">{item.title}</h3>
    if (item.kind === 'notice') return observedList
    if (item.kind === 'local') {
      const title = item.agent ? t('observation.agentHelper') : t('chat.botNameFallback')
      return (
        <div title={collapsed ? title : undefined}>
          <ConversationListItem
            title={title}
            preview=""
            selected={selection.kind === (item.agent ? 'agent' : 'chat')}
            onSelect={() => void navigate({ to: '/chat', search: item.agent ? { agent: true } : {} })}
            avatar={<ConversationAvatar src={item.agent ? undefined : localBotAvatarUrl} name={title} bot />}
          />
        </div>
      )
    }
    const { session } = item
    return (
      <ObservedRow
        session={session}
        status={stageStatuses.get(session.sessionId)}
        preview={previewText(latestMessages.get(session.sessionId)?.data)}
        active={selection.kind === 'observed' && selection.id === session.sessionId}
        collapsed={false}
        onSelect={openObserved}
        onOpenSettings={openSettings}
      />
    )
  }

  const observedList = isError ? (
    <p data-observation-empty="true" className="text-destructive">{t('chat.sidebar.loadObservedChatsFailed')}</p>
  ) : observedItems.length === 0 ? (
    <p data-observation-empty="true">
      {normalizedQuery ? t('chat.sidebar.noSearchResults') : t('chat.sidebar.waitingObservedChats')}
    </p>
  ) : null

  const expandedItems: ObservedListEntry[] = [
    ...(!normalizedQuery ? [
      { kind: 'local' as const, key: 'local:chat', agent: false },
      ...(flags?.agent === true ? [{ kind: 'local' as const, key: 'local:agent', agent: true }] : []),
    ] : []),
    ...(isError || observedItems.length === 0
      ? [{ kind: 'notice' as const, key: 'notice' }]
      : observedItems),
  ]

  return (
    <section data-observation-conversations="true" aria-label={t('observation.allConversations')}>
      {!collapsed && (
        <div data-observation-conversations-head="true">
          <span id="observation-conversations-title">{t('observation.allConversations')}</span>
          <Input
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={t('observation.filterConversations')}
            aria-label={t('observation.filterConversations')}
            controlSize="sm"
          />
          <span data-observation-count="true">{sorted.length + 1}</span>
        </div>
      )}
      <nav data-observation-conversation-list="true" aria-label={t('observation.allConversations')}>
        {!collapsed ? (
          <VirtualList
            items={expandedItems}
            getKey={observedItemKey}
            estimateSize={observedItemHeight}
            renderItem={renderObservedItem}
          />
        ) : (
          <ScrollArea
            data-conversation-scroll-list="true"
            className="min-h-0 flex-1"
            contentClassName="!block w-full min-w-0"
            scrollbars="vertical"
            viewportClassName="[&>div]:!block [&>div]:!min-w-0 [&>div]:w-full"
          >
            {renderObservedItem({ kind: 'local', key: 'local:chat', agent: false })}
            {flags?.agent === true && renderObservedItem({ kind: 'local', key: 'local:agent', agent: true })}
            <div>
              <Popover open={chatsOpen} onOpenChange={setChatsOpen}>
                <PopoverTrigger asChild>
                  <button
                    type="button"
                    data-observation-chat-stack="true"
                    data-active={selection.kind === 'observed'}
                    aria-label={`${t('chat.sidebar.observedChats')} (${sorted.length})`}
                    className="relative mx-auto my-2 flex h-16 w-full max-w-10 items-center justify-center rounded-md focus-visible:outline-2"
                  >
                    <span className="relative block h-12 w-8" aria-hidden="true">
                      {sorted.length > 0 ? sorted.slice(0, 3).reverse().map((session, index, items) => (
                        <span
                          key={session.sessionId}
                          data-observation-stack-avatar="true"
                          className="absolute left-0"
                          style={{ top: (items.length - 1 - index) * 8 }}
                        >
                          <StackedAvatar session={session} />
                        </span>
                      )) : <MessagesSquare className="m-auto h-8 w-8 text-muted-foreground" />}
                    </span>
                    <span
                      data-observation-chat-count="true"
                      className="rounded-sm bg-primary text-primary-foreground"
                    >
                      {sorted.length > 99 ? '99+' : sorted.length}
                    </span>
                  </button>
                </PopoverTrigger>
                <PopoverContent
                  side="right"
                  align="start"
                  sideOffset={12}
                  aria-label={t('chat.sidebar.observedChats')}
                  // 首帧高度独立于定位结果，避免定位变量尚未写入时列表被内容撑开、挂载全部会话。
                  className="flex h-[min(36rem,calc(100dvh-2rem))] max-h-[var(--radix-popover-content-available-height)] w-80 max-w-[calc(100vw-2rem)] flex-col overflow-hidden p-0 data-[state=open]:animate-none data-[state=closed]:animate-none"
                >
                  <div className="shrink-0 space-y-2 border-b p-3">
                    <p className="text-sm font-semibold">{t('chat.sidebar.observedChats')} · {sorted.length}</p>
                    <Input
                      type="search"
                      value={query}
                      onChange={(event) => setQuery(event.target.value)}
                      placeholder={t('observation.filterConversations')}
                      aria-label={t('observation.filterConversations')}
                      controlSize="sm"
                    />
                  </div>
                  {isError || observedItems.length === 0 ? observedList : (
                    <VirtualList
                      items={observedItems}
                      getKey={observedItemKey}
                      estimateSize={observedItemHeight}
                      renderItem={(item) => <div className="px-1">{renderObservedItem(item)}</div>}
                    />
                  )}
                </PopoverContent>
              </Popover>
            </div>
          </ScrollArea>
        )}
      </nav>
      <ChatStreamSettingsDialog
        chat={settingsChat}
        onOpenChange={(open) => !open && setSettingsChat(null)}
        onDeleted={(sessionId) => {
          setSettingsChat(null)
          if (selection.kind === 'observed' && selection.id === sessionId) {
            void navigate({ to: '/chat', search: {} })
          }
        }}
      />
    </section>
  )
}
