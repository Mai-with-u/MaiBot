import { useNavigate, useRouterState } from '@tanstack/react-router'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Eye } from 'lucide-react'
import { memo, useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { ConversationAvatar, ConversationListItem } from '@/components/ui/conversation'
import { StatusIndicator } from '@/components/ui/data-browser'
import { Input } from '@/components/ui/input'
import { ScrollArea } from '@/components/ui/scroll-area'
import { useFeatureFlags } from '@/hooks/use-feature-flags'
import { useResolvedAvatarUrl } from '@/lib/avatar-url'
import { getAllChatStreams } from '@/lib/chat-management-api'
import { buildObservedSessions, isFocusFlowId } from '@/routes/chat/observed-sessions'
import { StageStatusLabel } from '@/routes/monitor/StageStatusLabel'
import { getStageStatusLight } from '@/routes/monitor/stage-status-light'
import { useMaisakaMonitorOverview } from '@/routes/monitor/use-maisaka-monitor'
import type { SessionInfo, StageStatusInfo } from '@/routes/monitor/use-maisaka-monitor'

type Selection = { kind: 'overview' } | { kind: 'chat' } | { kind: 'agent' } | { kind: 'observed'; id: string } | { kind: 'none' }

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
}: {
  session: SessionInfo
  status?: StageStatusInfo
  preview: string
  active: boolean
  collapsed: boolean
  onSelect: (sessionId: string) => void
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
            <StatusIndicator variant="inline" status={getStageStatusLight(status)} />
          </span>
        }
        preview={status?.stage ? <StageStatusLabel status={status} /> : preview || t('chat.sidebar.emptyPreview')}
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
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const pathname = useRouterState({ select: (state) => state.location.pathname })
  const searchStr = useRouterState({ select: (state) => state.location.searchStr })
  const { flags } = useFeatureFlags()
  const { sessions: monitorSessions, stageStatuses, latestMessages } = useMaisakaMonitorOverview()
  const [query, setQuery] = useState('')
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
    () => Array.from(sessions.values()).sort((a, b) => b.lastActivity - a.lastActivity),
    [sessions]
  )
  const normalizedQuery = query.trim().toLowerCase()
  const filtered = normalizedQuery
    ? sorted.filter((session) => session.sessionName.toLowerCase().includes(normalizedQuery))
    : sorted
  const sections = [
    { title: t('observation.focusFlows'), items: filtered.filter((session) => isFocusFlowId(session.sessionId)) },
    { title: t('chat.sidebar.observedChats'), items: filtered.filter((session) => !isFocusFlowId(session.sessionId)) },
  ].filter((section) => section.items.length > 0)

  const params = new URLSearchParams(searchStr)
  const selection: Selection =
    pathname === '/'
      ? { kind: 'overview' }
      : pathname === '/chat'
        ? params.get('observe')
          ? { kind: 'observed', id: params.get('observe')! }
          : params.get('agent') === 'true'
            ? { kind: 'agent' }
            : { kind: 'chat' }
        : { kind: 'none' }

  const openObserved = (sessionId: string) => {
    void navigate({ to: '/chat', search: { observe: sessionId } })
  }

  return (
    <section data-observation-conversations="true" aria-labelledby="observation-conversations-title">
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
        <ScrollArea
          data-conversation-scroll-list="true"
          className="min-h-0 flex-1"
          contentClassName="!block w-full min-w-0"
          scrollbars="vertical"
          viewportClassName="[&>div]:!block [&>div]:!min-w-0 [&>div]:w-full"
        >
          {!normalizedQuery && (
            <>
              <div title={collapsed ? t('sidebar.menu.maisakaMonitor') : undefined}>
                <ConversationListItem
                  title={t('sidebar.menu.maisakaMonitor')}
                  preview={t('observation.overviewPreview')}
                  selected={selection.kind === 'overview'}
                  onSelect={() => void navigate({ to: '/' })}
                  avatar={<span data-observation-overview-avatar="true"><Eye aria-hidden="true" /></span>}
                />
              </div>
              <div title={collapsed ? t('chat.botNameFallback') : undefined}>
                <ConversationListItem
                  title={t('chat.botNameFallback')}
                  preview={t('observation.localChatPreview')}
                  selected={selection.kind === 'chat'}
                  onSelect={() => void navigate({ to: '/chat', search: {} })}
                  avatar={<ConversationAvatar name={t('chat.botNameFallback')} bot />}
                />
              </div>
              {flags?.agent === true && (
                <div title={collapsed ? t('sidebar.menu.agent') : undefined}>
                  <ConversationListItem
                    title={t('sidebar.menu.agent')}
                    preview=""
                    selected={selection.kind === 'agent'}
                    onSelect={() => void navigate({ to: '/chat', search: { agent: true } })}
                    avatar={<ConversationAvatar name={t('sidebar.menu.agent')} bot />}
                  />
                </div>
              )}
            </>
          )}
          {isError ? (
            <p data-observation-empty="true" className="text-destructive">{t('chat.sidebar.loadObservedChatsFailed')}</p>
          ) : sections.length === 0 ? (
            <p data-observation-empty="true">
              {normalizedQuery ? t('chat.sidebar.noSearchResults') : t('chat.sidebar.waitingObservedChats')}
            </p>
          ) : (
            sections.map((section) => (
              <div key={section.title}>
                {!collapsed && <h3 data-observation-list-section="true">{section.title}</h3>}
                {section.items.map((session) => (
                  <ObservedRow
                    key={session.sessionId}
                    session={session}
                    status={stageStatuses.get(session.sessionId)}
                    preview={previewText(latestMessages.get(session.sessionId)?.data)}
                    active={selection.kind === 'observed' && selection.id === session.sessionId}
                    collapsed={collapsed}
                    onSelect={openObserved}
                  />
                ))}
              </div>
            ))
          )}
        </ScrollArea>
      </nav>
    </section>
  )
}
