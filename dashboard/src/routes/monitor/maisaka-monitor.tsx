/**
 * MaiSaka 聊天流实时监控组件
 *
 * 通过 WebSocket 实时接收 MaiSaka 推理引擎事件，
 * 以时间线形式展示聊天流的推理过程。
 */
import { useNavigate } from '@tanstack/react-router'
import {
  Activity,
  AlertCircle,
  ArrowRight,
  Bot,
  Brain,
  CheckCircle2,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Clock,
  Eraser,
  FileCode2,
  ImageIcon,
  PauseCircle,
  Timer,
  Wrench,
  XCircle,
} from 'lucide-react'
import type { ReactNode } from 'react'
import { useCallback, useEffect, useRef, useState } from 'react'

import { Avatar, AvatarFallback, AvatarImage } from '@/components/ui/avatar'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardHeader, CardTitle } from '@/components/ui/card'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Separator } from '@/components/ui/separator'
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip'
import { useResolvedAvatarUrl, type AvatarTargetType } from '@/lib/avatar-url'
import { cn } from '@/lib/utils'

import type {
  MaisakaMessageMedia,
  MaisakaToolCall,
  MessageIngestedEvent,
  MaisakaReplyPreview,
  MessageSentEvent,
  PlannerFinalizedEvent,
  PlannerResponseEvent,
  ReplierResponseEvent,
  TimingGateResultEvent,
  ToolExecutionEvent,
} from '@/lib/maisaka-monitor-client'
import type { SessionInfo, StageStatusInfo, TimelineEntry } from './use-maisaka-monitor'
import { useMaisakaMonitor } from './use-maisaka-monitor'

// ─── 工具函数 ──────────────────────────────────────────────────

function formatMs(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)}ms`
  return `${(ms / 1000).toFixed(2)}s`
}

function buildCycleKey(sessionId: string, cycleId: number) {
  return `${sessionId}:${cycleId}`
}

function formatTimestamp(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
}

function formatRelativeTime(ts: number): string {
  const diff = Date.now() / 1000 - ts
  if (diff < 10) return '刚刚'
  if (diff < 60) return `${Math.round(diff)}秒前`
  if (diff < 3600) return `${Math.round(diff / 60)}分钟前`
  return `${Math.round(diff / 3600)}小时前`
}

function getToolCallSourceLabel(source?: string, fallbackLabel?: string): string {
  const normalizedSource = (source ?? '').trim().toLowerCase()
  if (normalizedSource === 'reasoning') return '推理中调用'
  if (normalizedSource === 'response') return '正文调用'
  return fallbackLabel?.trim() || ''
}

function getToolCallSourceBadgeClassName(source?: string): string {
  const normalizedSource = (source ?? '').trim().toLowerCase()
  if (normalizedSource === 'reasoning') {
    return 'border-teal-500/45 bg-teal-500/10 text-teal-700 dark:text-teal-300'
  }
  if (normalizedSource === 'response') {
    return 'border-amber-500/45 bg-amber-500/10 text-amber-700 dark:text-amber-300'
  }
  return 'border-muted-foreground/30 bg-muted/40 text-muted-foreground'
}

function getFallbackInitial(label: string, fallback: string) {
  const normalizedLabel = label.trim()
  if (normalizedLabel) return normalizedLabel.slice(0, 1)
  return fallback
}

function getSessionInitial(session: SessionInfo) {
  return getFallbackInitial(session.sessionName, session.isGroupChat ? '群' : '私')
}

function isWaitingForMessage(status: StageStatusInfo) {
  return status.stage === '等待消息' || status.detail.includes('等待消息') || status.agentState === 'wait'
}

function MonitorAvatar({
  className,
  fallback,
  fallbackClassName,
  label,
  platform,
  targetId,
  targetType,
}: {
  className?: string
  fallback: ReactNode
  fallbackClassName?: string
  label: string
  platform?: string | null
  targetId?: string | null
  targetType: AvatarTargetType
}) {
  const avatarUrl = useResolvedAvatarUrl(platform, targetId, targetType)

  return (
    <Avatar className={cn('shrink-0 ring-1 ring-border/60', className)}>
      {avatarUrl && <AvatarImage src={avatarUrl} alt={`${label} 的头像`} className="object-cover" />}
      <AvatarFallback className={fallbackClassName}>
        {fallback}
      </AvatarFallback>
    </Avatar>
  )
}

function SessionAvatar({ session, status }: { session: SessionInfo; status?: StageStatusInfo }) {
  const targetType: AvatarTargetType = session.isGroupChat ? 'group' : 'user'
  const targetId = session.isGroupChat ? session.groupId : session.userId
  const statusDotClassName = status && isWaitingForMessage(status) ? 'bg-blue-500' : 'bg-emerald-500'

  return (
    <span className="relative flex h-7 w-7 shrink-0">
      <MonitorAvatar
        className="h-7 w-7 rounded-md"
        fallback={getSessionInitial(session)}
        fallbackClassName="rounded-md bg-primary/10 text-xs font-semibold text-primary"
        label={session.sessionName}
        platform={session.platform}
        targetId={targetId}
        targetType={targetType}
      />
      {status && (
        <span className={cn('absolute -right-0.5 -top-0.5 h-2.5 w-2.5 rounded-full ring-2 ring-background', statusDotClassName)} />
      )}
    </span>
  )
}

function MessageAvatar({
  data,
  kind,
}: {
  data: MessageIngestedEvent | MessageSentEvent
  kind: 'ingested' | 'sent'
}) {
  const isSent = kind === 'sent'

  return (
    <MonitorAvatar
      className="mt-1 h-7 w-7 rounded-full"
      fallback={isSent ? <Bot className="h-3.5 w-3.5" /> : getFallbackInitial(data.speaker_name, '人')}
      fallbackClassName={cn(
        'text-xs font-semibold',
        isSent ? 'bg-emerald-500/15 text-emerald-500' : 'bg-blue-500/15 text-blue-500',
      )}
      label={data.speaker_name || (isSent ? '麦麦' : '用户')}
      platform={data.platform}
      targetId={data.user_id}
      targetType="user"
    />
  )
}

// ─── 会话侧边栏 ──────────────────────────────────────────────

function SessionSidebar({
  sessions,
  stageStatuses,
  selectedSession,
  onSelect,
  collapsed,
}: {
  sessions: Map<string, SessionInfo>
  stageStatuses: Map<string, StageStatusInfo>
  selectedSession: string | null
  onSelect: (id: string) => void
  collapsed: boolean
}) {
  const sortedSessions = Array.from(sessions.values()).sort(
    (a, b) => b.lastActivity - a.lastActivity,
  )

  if (sortedSessions.length === 0) {
    if (collapsed) {
      return <div className="h-full p-2" />
    }

    return (
      <div className={cn(
        'flex flex-col items-center justify-center h-full text-muted-foreground gap-2',
        'p-4',
      )}>
        <Bot className="h-8 w-8 opacity-40" />
        <p className="text-sm text-center">等待 MaiSaka 会话…</p>
      </div>
    )
  }

  return (
    <div className={cn('flex flex-col gap-1', collapsed ? 'items-center p-2' : 'p-2')}>
      {sortedSessions.map((session) => {
        const status = stageStatuses.get(session.sessionId)
        return (
        <button
          key={session.sessionId}
          onClick={() => onSelect(session.sessionId)}
          title={session.sessionName}
          className={cn(
            'max-w-full overflow-hidden rounded-lg text-left text-sm transition-colors',
            'hover:bg-accent/50',
            collapsed
              ? 'flex h-10 w-10 items-center justify-center p-0'
              : 'flex w-full min-w-0 flex-col items-start gap-0.5 px-2.5 py-2',
            selectedSession === session.sessionId && 'bg-accent text-accent-foreground',
          )}
        >
          <div className={cn('flex w-full min-w-0 items-center', collapsed ? 'justify-center' : 'justify-between gap-2')}>
            <div className={cn('flex min-w-0 items-center gap-2 overflow-hidden', !collapsed && 'flex-1')}>
              <SessionAvatar session={session} status={status} />
              {!collapsed && <span className="block min-w-0 flex-1 overflow-hidden text-ellipsis whitespace-nowrap font-medium" title={session.sessionName}>
                {session.sessionName}
              </span>}
            </div>
            {!collapsed && <Badge variant="secondary" className="h-4 shrink-0 px-1 text-[10px]">
              {session.eventCount}
            </Badge>}
          </div>
          {!collapsed && (
            <div className="flex w-full min-w-0 items-center justify-between gap-2 overflow-hidden text-xs text-muted-foreground">
              <span className="shrink-0">{formatRelativeTime(session.lastActivity)}</span>
              {status && <span className="min-w-0 truncate text-primary">{status.stage}</span>}
            </div>
          )}
        </button>
        )
      })}
    </div>
  )
}

// ─── 单条时间线事件渲染 ──────────────────────────────────────

interface MonitorStats {
  messages: number
  cycles: number
  toolCalls: number
}

interface StageStatusPanelProps {
  autoScroll: boolean
  onClearTimeline: () => void
  onScrollToBottom: () => void
  stats: MonitorStats
  status?: StageStatusInfo
}

function MonitorStatusActions({
  autoScroll,
  onClearTimeline,
  onScrollToBottom,
  stats,
}: Omit<StageStatusPanelProps, 'status'>) {
  return (
    <>
      <TooltipProvider delayDuration={150}>
        <Tooltip>
          <TooltipTrigger asChild>
            <div className="flex h-6 shrink-0 items-center gap-1 rounded-md border bg-background/60 px-1.5 text-muted-foreground">
              <Activity className="h-3 w-3" />
              <span className="text-[10px] font-medium">统计</span>
            </div>
          </TooltipTrigger>
          <TooltipContent side="bottom" align="start" className="space-y-1">
            <div>消息：{stats.messages}</div>
            <div>循环：{stats.cycles}</div>
            <div>工具调用：{stats.toolCalls}</div>
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>
      <div className="ml-auto flex shrink-0 items-center gap-1.5">
        <Button
          variant="ghost"
          size="sm"
          className="h-6 shrink-0 px-2 text-[11px]"
          onClick={onScrollToBottom}
          title="回到底部"
        >
          <ChevronDown className={cn('h-3 w-3 mr-1', autoScroll && 'text-primary')} />
          回到底部
        </Button>
        <Button
          variant="ghost"
          size="icon"
          className="h-6 w-6 shrink-0"
          onClick={onClearTimeline}
          title="清空"
          aria-label="清空"
        >
          <Eraser className="h-3 w-3" />
        </Button>
      </div>
    </>
  )
}

function StageStatusPanel({
  autoScroll,
  onClearTimeline,
  onScrollToBottom,
  stats,
  status,
}: StageStatusPanelProps) {
  const actions = (
      <MonitorStatusActions
        autoScroll={autoScroll}
        onClearTimeline={onClearTimeline}
        onScrollToBottom={onScrollToBottom}
        stats={stats}
      />
  )

  if (!status) {
    return (
      <div className="mb-1.5 flex min-w-0 items-center gap-2 overflow-x-auto rounded-md border bg-muted/30 px-2 py-1">
        {actions}
        <div className="shrink-0 whitespace-nowrap text-xs text-muted-foreground">
          当前聊天流暂无阶段状态
        </div>
      </div>
    )
  }

  return (
    <div className="mb-1.5 flex min-w-0 items-center gap-2 overflow-x-auto rounded-md border bg-background px-2 py-1">
      {actions}
      <div className="flex shrink-0 items-center gap-1.5">
        <Badge variant="default" className="gap-1 px-1.5 text-[10px]">
          <Activity className="h-2.5 w-2.5" />
          {status.stage || '未知阶段'}
        </Badge>
        {status.roundText && (
          <Badge variant="secondary" className="px-1.5 text-[10px]">
            {status.roundText}
          </Badge>
        )}
        {status.agentState && (
          <Badge variant={status.agentState === 'running' ? 'default' : 'outline'} className="px-1.5 text-[10px]">
            {status.agentState}
          </Badge>
        )}
        <span className="ml-auto text-[11px] text-muted-foreground">
          更新于 {formatRelativeTime(status.updatedAt)}
        </span>
      </div>
      {status.detail && (
        <p className="shrink-0 whitespace-nowrap text-xs text-muted-foreground">{status.detail}</p>
      )}
    </div>
  )
}

function ReplyPreviewBlock({ replyTo }: { replyTo?: MaisakaReplyPreview | null }) {
  if (!replyTo) {
    return null
  }

  return (
    <div className="mb-1.5 max-w-xl rounded-md bg-muted/70 px-2.5 py-1.5 text-xs text-muted-foreground">
      <div className="mb-0.5 flex min-w-0 items-center gap-1.5">
        <span className="min-w-0 truncate font-medium text-foreground/80">
          回复 {replyTo.sender_name || '未知用户'}
        </span>
        {replyTo.message_id && (
          <span className="shrink-0 font-mono text-[10px] text-muted-foreground/70">
            #{replyTo.message_id}
          </span>
        )}
      </div>
      <div className="line-clamp-2 whitespace-pre-wrap break-words leading-4">
        {replyTo.content || '原消息已无法访问'}
      </div>
    </div>
  )
}

function buildMessageMediaKey(media: MaisakaMessageMedia, index: number) {
  return `${media.kind}:${media.hash}:${media.index ?? index}`
}

function MessageMediaContent({
  content,
  emptyLabel,
  media = [],
}: {
  content?: string
  emptyLabel: string
  media?: MaisakaMessageMedia[]
}) {
  const [displayOverrides, setDisplayOverrides] = useState<Record<string, boolean>>({})
  const normalizedContent = content ?? ''
  const hasContent = normalizedContent.trim().length > 0
  const hasMedia = media.length > 0

  if (!hasContent && !hasMedia) {
    return (
      <p className="text-sm text-foreground/80 whitespace-pre-wrap wrap-break-word leading-relaxed">
        {emptyLabel}
      </p>
    )
  }

  return (
    <div className="space-y-1.5">
      {hasContent && (
        <p className="text-sm text-foreground/80 whitespace-pre-wrap wrap-break-word leading-relaxed">
          {normalizedContent}
        </p>
      )}
      {hasMedia && (
        <div className="flex flex-wrap gap-2">
          {media.map((item, index) => {
            const mediaKey = buildMessageMediaKey(item, index)
            const source = item.data_url || item.url
            const canShowOriginal = source.trim().length > 0
            const showOriginal = canShowOriginal && (displayOverrides[mediaKey] ?? Boolean(item.default_original))
            const label = item.kind === 'emoji' ? '表情包' : '图片'
            return (
              <button
                key={mediaKey}
                type="button"
                className={cn(
                  'group max-w-full overflow-hidden rounded-md border bg-muted/40 text-left transition-colors hover:border-primary/60 hover:bg-muted/70',
                  showOriginal ? 'p-1.5' : 'px-2.5 py-1.5',
                )}
                title={`点击切换为${showOriginal ? '识别文本' : '原文件'}`}
                onClick={() => {
                  if (!canShowOriginal) {
                    return
                  }
                  setDisplayOverrides((current) => ({
                    ...current,
                    [mediaKey]: !showOriginal,
                  }))
                }}
              >
                {showOriginal ? (
                  <img
                    src={source}
                    alt={`${label}原文件`}
                    className={cn(
                      'block rounded object-contain',
                      item.kind === 'emoji' ? 'max-h-24 max-w-24' : 'max-h-56 max-w-full',
                    )}
                    loading="lazy"
                  />
                ) : (
                  <span className="flex max-w-sm items-center gap-1.5 text-xs text-muted-foreground">
                    <ImageIcon className="h-3.5 w-3.5 shrink-0" />
                    <span className="min-w-0 whitespace-pre-wrap break-words">
                      {item.text || `[${label}]`}
                    </span>
                  </span>
                )}
              </button>
            )
          })}
        </div>
      )}
    </div>
  )
}

function MessageIngestedCard({ data }: { data: MessageIngestedEvent }) {
  return (
    <div className="flex items-start gap-3">
      <MessageAvatar data={data} kind="ingested" />
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 mb-1">
          <span className="font-medium text-sm">{data.speaker_name}</span>
          <span className="text-xs text-muted-foreground">{formatTimestamp(data.timestamp)}</span>
        </div>
        <ReplyPreviewBlock replyTo={data.reply_to} />
        <MessageMediaContent content={data.content} emptyLabel="[空消息]" media={data.media} />
      </div>
    </div>
  )
}

function MessageSentCard({ data }: { data: MessageSentEvent }) {
  return (
    <div className="flex items-start gap-3 rounded-md border border-emerald-500/30 bg-emerald-500/5 px-3 py-2">
      <MessageAvatar data={data} kind="sent" />
      <div className="flex-1 min-w-0">
        <div className="mb-1 flex items-center gap-2">
          <span className="font-medium text-sm">{data.speaker_name || '麦麦'}</span>
          <Badge variant="outline" className="text-[10px]">已发送</Badge>
          <span className="text-xs text-muted-foreground">{formatTimestamp(data.timestamp)}</span>
        </div>
        <ReplyPreviewBlock replyTo={data.reply_to} />
        <MessageMediaContent content={data.content} emptyLabel="[非文本消息]" media={data.media} />
      </div>
    </div>
  )
}

function TimingGateCard({ data }: { data: TimingGateResultEvent }) {
  const actionConfig: Record<string, { label: string; variant: 'default' | 'secondary' | 'destructive'; icon: typeof ArrowRight }> = {
    continue: { label: '继续执行', variant: 'default', icon: ArrowRight },
    wait: { label: '等待', variant: 'secondary', icon: PauseCircle },
    no_action: { label: '不回复', variant: 'destructive', icon: XCircle },
  }
  const config = actionConfig[data.action] ?? actionConfig.continue
  const Icon = config.icon

  return (
    <div className="flex items-start gap-3 rounded-md border bg-background px-3 py-2 shadow-sm">
      <div className="mt-1 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-amber-500/15 text-amber-500">
        <Timer className="h-3.5 w-3.5" />
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 mb-1 flex-wrap">
          <span className="text-sm font-medium">反应</span>
          <Badge variant="outline" className="text-[10px]">react</Badge>
          <Badge variant={config.variant} className="text-[10px] gap-0.5">
            <Icon className="h-2.5 w-2.5" />
            {config.label}
          </Badge>
          <span className="text-xs text-muted-foreground">{formatMs(data.duration_ms)}</span>
        </div>
        {data.content && (
          <CollapsibleText text={data.content} maxLines={3} />
        )}
      </div>
    </div>
  )
}

function ToolCallBadges({ toolCalls }: { toolCalls: MaisakaToolCall[] }) {
  if (toolCalls.length <= 0) {
    return null
  }

  return (
    <div className="mt-2 flex flex-wrap gap-1.5">
      {toolCalls.map((tc: MaisakaToolCall, idx: number) => (
        <Badge key={`${tc.id || tc.name}-${idx}`} variant="secondary" className="text-[10px] gap-1">
          <Wrench className="h-2.5 w-2.5" />
          {tc.name}
          {getToolCallSourceLabel(tc.source, tc.source_label) && (
            <span className={cn(
              'ml-1 rounded-full border px-1 py-0 text-[9px] leading-4',
              getToolCallSourceBadgeClassName(tc.source),
            )}>
              {getToolCallSourceLabel(tc.source, tc.source_label)}
            </span>
          )}
        </Badge>
      ))}
    </div>
  )
}

interface ReasoningRecordTarget {
  session: string
  stage: string
  stem: string
}

function parsePromptHtmlReasoningTarget(uri: string): ReasoningRecordTarget | null {
  const normalized = uri.trim()
  if (!normalized || typeof window === 'undefined') return null

  let url: URL
  try {
    url = new URL(normalized, window.location.origin)
  } catch {
    return null
  }

  if (url.origin !== window.location.origin || url.pathname !== '/api/webui/config/maisaka-prompt-preview') {
    return null
  }

  const previewPath = url.searchParams.get('path')?.trim() ?? ''
  const parts = previewPath.split('/').filter(Boolean)
  if (parts.length < 3) return null

  const [stage, session, filename] = parts
  const supportedSuffix = ['.html', '.json'].find((suffix) => filename.endsWith(suffix))
  if (!supportedSuffix) return null

  const stem = filename.slice(0, -supportedSuffix.length)
  if (!stage || !session || !stem) return null

  return { stage, session, stem }
}

function isPlannerInterrupted(data: PlannerFinalizedEvent) {
  const content = data.planner?.content?.trim() ?? ''
  return data.interrupted === true || (
    content.startsWith('Planner ') &&
    data.planner?.prompt_tokens === 0 &&
    data.planner?.completion_tokens === 0 &&
    data.planner?.tool_calls.length === 0
  )
}

function PlannerInterruptedCard({ data }: { data: PlannerFinalizedEvent }) {
  const planner = data.planner

  return (
    <div className="rounded-md border border-amber-500/35 bg-amber-500/5 px-3 py-2">
      <div className="flex items-center gap-2 text-sm">
        <AlertCircle className="h-4 w-4 shrink-0 text-amber-500" />
        <span className="font-medium">Planner 被新消息打断</span>
        <Badge variant="outline" className="ml-auto text-[10px]">
          #{data.cycle_id}
        </Badge>
        {planner && planner.duration_ms > 0 && (
          <span className="text-xs text-muted-foreground">{formatMs(planner.duration_ms)}</span>
        )}
      </div>
      <p className="mt-1 text-sm text-muted-foreground">
        {planner?.content || '收到新消息，已停止当前思考并准备重新决策。'}
      </p>
    </div>
  )
}

function PlannerResponseCard({ data }: { data: PlannerResponseEvent }) {
  return (
    <div className="flex items-start gap-3">
      <div className="mt-1 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-emerald-500/15 text-emerald-500">
        <Brain className="h-3.5 w-3.5" />
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 mb-1 flex-wrap">
          <span className="text-sm font-medium">规划器思考</span>
          <span className="text-xs text-muted-foreground">{formatMs(data.duration_ms)}</span>
          <Badge variant="outline" className="text-[10px]">
            {data.prompt_tokens}+{data.completion_tokens} tokens
          </Badge>
        </div>
        {data.content && (
          <CollapsibleText text={data.content} maxLines={6} />
        )}
        <ToolCallBadges toolCalls={data.tool_calls} />
      </div>
    </div>
  )
}

function PlannerFinalizedCard({
  data,
  onOpenReasoning,
}: {
  data: PlannerFinalizedEvent
  onOpenReasoning: (promptHtmlUri: string) => void
}) {
  const planner = data.planner
  const promptHtmlUri = planner?.prompt_html_uri?.trim() ?? ''
  const canOpenReasoning = Boolean(promptHtmlUri && parsePromptHtmlReasoningTarget(promptHtmlUri))

  return (
    <Card className="border-l-4 border-l-emerald-500/60">
      <CardHeader className="py-3 px-4 space-y-3">
        <div className="flex items-center gap-2 flex-wrap">
          <Brain className="h-4 w-4 text-emerald-500" />
          <CardTitle className="text-sm font-medium">Planner</CardTitle>
          {canOpenReasoning && (
            <Button
              variant="ghost"
              size="sm"
              className="h-6 px-2 text-[10px]"
              onClick={() => onOpenReasoning(promptHtmlUri)}
              title="在推理过程页查看对应记录"
            >
              <FileCode2 className="mr-1 h-3 w-3" />
              推理
            </Button>
          )}
          <Badge variant="outline" className="text-xs font-normal ml-auto">
            {formatMs(planner?.duration_ms ?? 0)}
          </Badge>
          {data.request && (
            <Badge variant="secondary" className="text-[10px]">
              上下文 {data.request.selected_history_count} 条 / 可用工具 {data.request.tool_count}
            </Badge>
          )}
          {planner && (planner.prompt_tokens > 0 || planner.completion_tokens > 0) && (
            <Badge variant="outline" className="text-[10px]">
              {planner.prompt_tokens}+{planner.completion_tokens} tokens
            </Badge>
          )}
        </div>

        {planner?.content ? (
          <CollapsibleText text={planner.content} maxLines={6} className="text-foreground/90" />
        ) : (
          <p className="text-sm text-muted-foreground">planner 本轮没有文本内容</p>
        )}

      </CardHeader>
    </Card>
  )
}

function getValueTypeLabel(value: unknown) {
  if (Array.isArray(value)) return `array(${value.length})`
  if (value === null) return 'null'
  return typeof value
}

function formatToolValue(value: unknown) {
  if (typeof value === 'string') return value
  if (value === undefined) return 'undefined'
  return JSON.stringify(value, null, 2)
}

function ToolArgumentBlock({
  name,
  value,
}: {
  name: string
  value: unknown
}) {
  const formattedValue = formatToolValue(value)
  const inlineValue = formattedValue.replace(/\s+/g, ' ')

  return (
    <div
      className="flex h-6 max-w-full min-w-0 items-center gap-1.5 rounded-md border bg-background/60 px-2 text-xs"
      title={`${name} (${getValueTypeLabel(value)}): ${formattedValue}`}
    >
      <span className="shrink-0 font-mono font-semibold text-foreground">{name}</span>
      <span className="shrink-0 text-muted-foreground">=</span>
      <span className="min-w-0 max-w-72 truncate font-mono text-[11px] text-muted-foreground">
        {inlineValue}
      </span>
    </div>
  )
}

function ToolFullJsonBlock({
  tool,
}: {
  tool: {
    duration_ms: number
    prompt_html_uri?: string
    success: boolean
    summary: string
    tool_args: Record<string, unknown>
    tool_call_id: string
    tool_call_source?: string
    tool_call_source_label?: string
    tool_name: string
  }
}) {
  const payload = {
    tool_call_id: tool.tool_call_id,
    tool_name: tool.tool_name,
    tool_args: tool.tool_args,
    success: tool.success,
    duration_ms: tool.duration_ms,
    summary: tool.summary,
    prompt_html_uri: tool.prompt_html_uri,
    tool_call_source: tool.tool_call_source,
    tool_call_source_label: tool.tool_call_source_label,
  }

  return (
    <details className="group contents text-xs">
      <summary
        className="ml-auto flex h-6 cursor-pointer list-none items-center gap-1 rounded border border-dashed bg-background/40 px-1.5 text-[10px] text-muted-foreground hover:bg-muted/40"
        title="完整调用 JSON"
      >
        <ChevronRight className="h-2.5 w-2.5 shrink-0 transition-transform group-open:rotate-90" />
        <span>JSON</span>
      </summary>
      <pre className="basis-full rounded-md border bg-background/60 px-2.5 py-1.5 font-mono text-[11px] leading-4 whitespace-pre-wrap break-words text-muted-foreground">
        {JSON.stringify(payload, null, 2)}
      </pre>
    </details>
  )
}

function PlannerToolResultCard({
  tool,
  index,
  onOpenReasoning,
}: {
  tool: {
    duration_ms: number
    prompt_html_uri?: string
    success: boolean
    summary: string
    tool_args: Record<string, unknown>
    tool_call_id: string
    tool_call_source?: string
    tool_call_source_label?: string
    tool_name: string
  }
  index: number
  onOpenReasoning: (promptHtmlUri: string) => void
}) {
  const argumentEntries = Object.entries(tool.tool_args ?? {})
  const statusText = tool.success ? '执行成功' : '执行失败'
  const sourceLabel = getToolCallSourceLabel(tool.tool_call_source, tool.tool_call_source_label)
  const promptHtmlUri = tool.prompt_html_uri?.trim() ?? ''
  const canOpenReasoning = Boolean(promptHtmlUri && parsePromptHtmlReasoningTarget(promptHtmlUri))

  return (
    <div className="space-y-1.5">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="font-mono text-sm font-semibold text-foreground">{tool.tool_name || 'unknown'}</span>
        {tool.success
          ? <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500" />
          : <XCircle className="h-3.5 w-3.5 text-red-500" />
        }
        <Badge variant={tool.success ? 'secondary' : 'destructive'} className="h-5 px-1.5 text-[10px]">
          {statusText}
        </Badge>
        {sourceLabel && (
          <Badge
            variant="outline"
            className={cn('h-5 px-1.5 text-[10px]', getToolCallSourceBadgeClassName(tool.tool_call_source))}
          >
            {sourceLabel}
          </Badge>
        )}
        {tool.duration_ms > 0 && (
          <span className="text-xs font-medium text-muted-foreground">{formatMs(tool.duration_ms)}</span>
        )}
        {canOpenReasoning && (
          <Button
            variant="ghost"
            size="sm"
            className="h-6 px-1.5 text-[10px]"
            onClick={() => onOpenReasoning(promptHtmlUri)}
            title="查看这个工具对应的推理"
          >
            <FileCode2 className="mr-1 h-3 w-3" />
            推理
          </Button>
        )}
        <span className="ml-auto text-[10px] text-muted-foreground">#{index + 1}</span>
      </div>

      <div className="space-y-1.5">
        {argumentEntries.length > 0 && (
          <div className="flex flex-wrap items-center gap-1.5">
            {argumentEntries.map(([name, value]) => (
              <ToolArgumentBlock key={name} name={name} value={value} />
            ))}
            <ToolFullJsonBlock tool={tool} />
          </div>
        )}

        <div className="flex items-start gap-1.5 rounded-md border bg-muted/20 px-2.5 py-1">
          <span className="shrink-0 text-[10px] font-medium leading-4 text-muted-foreground">执行结果</span>
          <p className="min-w-0 flex-1 text-xs leading-4 whitespace-pre-wrap break-words text-foreground/80">
            {tool.summary || '未返回结果摘要。'}
          </p>
        </div>
      </div>
    </div>
  )
}

function PlannerToolCallsBlock({
  data,
  onOpenReasoning,
}: {
  data: PlannerFinalizedEvent
  onOpenReasoning: (promptHtmlUri: string) => void
}) {
  const toolCalls = data.planner?.tool_calls ?? []
  const tools = data.tools ?? []
  const displayTools = tools.length > 0
    ? tools
    : toolCalls.map((toolCall) => ({
        tool_call_id: toolCall.id,
        tool_name: toolCall.name,
        tool_args: toolCall.arguments ?? {},
        tool_call_source: toolCall.source,
        tool_call_source_label: toolCall.source_label,
        success: true,
        duration_ms: 0,
        summary: '',
      }))
  const isFinishTool = (toolName?: string) => toolName?.trim().toLowerCase() === 'finish'
  const finishTools = displayTools.filter((tool) => isFinishTool(tool.tool_name))
  const regularTools = displayTools.filter((tool) => !isFinishTool(tool.tool_name))

  if (displayTools.length <= 0) {
    return null
  }

  if (regularTools.length <= 0 && finishTools.length > 0) {
    return (
      <div className="rounded-md border border-emerald-500/30 bg-emerald-500/5 px-3 py-2">
        <div className="flex items-center gap-2 text-sm">
          <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-500" />
          <span className="font-medium">本轮思考暂时结束</span>
          <span className="text-muted-foreground">等待新的消息。</span>
        </div>
      </div>
    )
  }

  return (
    <Card className="border-l-4 border-l-teal-500/60">
      <CardHeader className="py-3 px-4 space-y-2">
        <div className="flex items-center gap-2">
          <Wrench className="h-4 w-4 text-teal-500" />
          <CardTitle className="text-sm font-medium">使用工具</CardTitle>
          <Badge variant="secondary" className="ml-auto text-[10px]">
            {regularTools.length} 个
          </Badge>
        </div>
        {finishTools.length > 0 && (
          <div className="flex items-center gap-2 rounded-md border border-emerald-500/30 bg-emerald-500/5 px-2.5 py-1.5 text-xs">
            <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-emerald-500" />
            <span className="font-medium">本轮思考暂时结束</span>
            <span className="text-muted-foreground">等待新的消息。</span>
          </div>
        )}
        <div className="space-y-2">
          {regularTools.map((tool, idx) => (
            <div key={`${tool.tool_call_id || tool.tool_name}-${idx}`} className="space-y-2">
              {idx > 0 && <Separator />}
              <PlannerToolResultCard
                tool={tool}
                index={idx}
                onOpenReasoning={onOpenReasoning}
              />
            </div>
          ))}
        </div>
      </CardHeader>
    </Card>
  )
}

function ToolExecutionCard({ data }: { data: ToolExecutionEvent }) {
  return (
    <div className="flex items-start gap-3">
      <div className={cn(
        'mt-1 flex h-7 w-7 shrink-0 items-center justify-center rounded-full',
        data.success
          ? 'bg-teal-500/15 text-teal-500'
          : 'bg-red-500/15 text-red-500',
      )}>
        <Wrench className="h-3.5 w-3.5" />
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 mb-1 flex-wrap">
          <span className="text-sm font-medium font-mono">{data.tool_name}</span>
          {data.success
            ? <CheckCircle2 className="h-3.5 w-3.5 text-teal-500" />
            : <XCircle className="h-3.5 w-3.5 text-red-500" />
          }
          <span className="text-xs text-muted-foreground">{formatMs(data.duration_ms)}</span>
        </div>
        {Object.keys(data.tool_args).length > 0 && (
          <div className="text-xs text-muted-foreground font-mono bg-muted/50 rounded px-2 py-1 mb-1 whitespace-pre-wrap break-all">
            {JSON.stringify(data.tool_args, null, 2)}
          </div>
        )}
        {data.result_summary && (
          <CollapsibleText text={data.result_summary} maxLines={3} className="text-muted-foreground" />
        )}
      </div>
    </div>
  )
}

// ─── 可折叠文本组件 ────────────────────────────────────────────

function CollapsibleText({
  text,
  maxLines = 4,
  className,
}: {
  text: string
  maxLines?: number
  className?: string
}) {
  const [expanded, setExpanded] = useState(false)
  const lines = text.split('\n')
  const needsCollapse = lines.length > maxLines

  if (!needsCollapse || expanded) {
    return (
      <div className="relative">
        <p className={cn(
          'text-sm whitespace-pre-wrap wrap-break-word leading-relaxed',
          className,
        )}>
          {text}
        </p>
        {needsCollapse && (
          <button
            onClick={() => setExpanded(false)}
            className="text-xs text-primary hover:underline mt-1 flex items-center gap-0.5"
          >
            <ChevronDown className="h-3 w-3" /> 收起
          </button>
        )}
      </div>
    )
  }

  return (
    <div>
      <p className={cn(
        'text-sm whitespace-pre-wrap wrap-break-word leading-relaxed',
        className,
      )}>
        {lines.slice(0, maxLines).join('\n')}
      </p>
      <button
        onClick={() => setExpanded(true)}
        className="text-xs text-primary hover:underline mt-1 flex items-center gap-0.5"
      >
        <ChevronRight className="h-3 w-3" /> 展开全部 ({lines.length} 行)
      </button>
    </div>
  )
}

// ─── 回复器响应卡片 ──────────────────────────────────────────

function ReplierResponseCard({ data }: { data: ReplierResponseEvent }) {
  return (
    <Card className="border-l-4 border-l-purple-500/60">
      <CardHeader className="py-2.5 px-4 space-y-2">
        <div className="flex items-center gap-2">
          <Bot className="h-4 w-4 text-purple-500" />
          <CardTitle className="text-sm font-medium">回复器响应</CardTitle>
          <Badge variant="outline" className="text-xs font-normal ml-auto">
            {formatMs(data.duration_ms)}
          </Badge>
          {data.success ? (
            <Badge variant="secondary" className="text-xs gap-1">
              <CheckCircle2 className="h-3 w-3" /> 成功
            </Badge>
          ) : (
            <Badge variant="destructive" className="text-xs gap-1">
              <XCircle className="h-3 w-3" /> 失败
            </Badge>
          )}
          <span className="text-xs text-muted-foreground">{formatTimestamp(data.timestamp)}</span>
        </div>
        {data.content && (
          <CollapsibleText text={data.content} maxLines={6} className="text-foreground/90" />
        )}
        {data.reasoning && (
          <details className="mt-1">
            <summary className="text-xs text-muted-foreground cursor-pointer hover:text-foreground">
              思考过程
            </summary>
            <CollapsibleText text={data.reasoning} maxLines={8} className="mt-1 text-muted-foreground" />
          </details>
        )}
        {(data.prompt_tokens > 0 || data.completion_tokens > 0) && (
          <div className="flex gap-3 text-xs text-muted-foreground mt-1">
            {data.model_name && <span>模型: {data.model_name}</span>}
            <span>输入: {data.prompt_tokens}</span>
            <span>输出: {data.completion_tokens}</span>
            <span>总计: {data.total_tokens}</span>
          </div>
        )}
      </CardHeader>
    </Card>
  )
}

// ─── 时间线入口渲染器 ──────────────────────────────────────────

function TimelineEventRenderer({
  entry,
  onOpenReasoning,
}: {
  entry: TimelineEntry
  onOpenReasoning: (promptHtmlUri: string) => void
}) {
  switch (entry.type) {
    case 'message.ingested':
      return <MessageIngestedCard data={entry.data as MessageIngestedEvent} />
    case 'message.sent':
      return <MessageSentCard data={entry.data as MessageSentEvent} />
    case 'timing_gate.result':
      return <TimingGateCard data={entry.data as TimingGateResultEvent} />
    case 'planner.response':
      return <PlannerResponseCard data={entry.data as PlannerResponseEvent} />
    case 'planner.finalized':
      if (isPlannerInterrupted(entry.data as PlannerFinalizedEvent)) {
        return <PlannerInterruptedCard data={entry.data as PlannerFinalizedEvent} />
      }
      if ((entry.data as PlannerFinalizedEvent).timing_gate?.result?.action === 'no_action') {
        return null
      }
      return (
        <div className="space-y-2">
          <PlannerFinalizedCard data={entry.data as PlannerFinalizedEvent} onOpenReasoning={onOpenReasoning} />
          <PlannerToolCallsBlock data={entry.data as PlannerFinalizedEvent} onOpenReasoning={onOpenReasoning} />
        </div>
      )
    case 'tool.execution':
      return <ToolExecutionCard data={entry.data as ToolExecutionEvent} />
    case 'replier.response':
      return <ReplierResponseCard data={entry.data as ReplierResponseEvent} />
    // planner.request, replier.request 和 session.start 通常不需要在 timeline 中主要展示
    default:
      return null
  }
}

// ─── 主组件 ─────────────────────────────────────────────────

export function MaisakaMonitor() {
  const navigate = useNavigate()
  const {
    timeline,
    sessions,
    stageStatuses,
    selectedSession,
    setSelectedSession,
    connected,
    clearTimeline,
  } = useMaisakaMonitor()

  const scrollRef = useRef<HTMLDivElement>(null)
  const [autoScroll, setAutoScroll] = useState(true)
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => {
    const saved = localStorage.getItem('maisaka-monitor-sidebar-collapsed')
    return saved !== 'false'
  })

  const handleOpenReasoning = useCallback((promptHtmlUri: string) => {
    const target = parsePromptHtmlReasoningTarget(promptHtmlUri)
    if (!target) return

    const params = new URLSearchParams({
      stage: target.stage,
      session: target.session,
      stem: target.stem,
      returnTo: `${window.location.pathname}${window.location.search}${window.location.hash}`,
    })
    navigate({ to: `/reasoning-process?${params.toString()}` })
  }, [navigate])

  useEffect(() => {
    localStorage.setItem('maisaka-monitor-sidebar-collapsed', String(sidebarCollapsed))
  }, [sidebarCollapsed])

  const scrollToBottom = useCallback((behavior: ScrollBehavior = 'smooth') => {
    const viewport = scrollRef.current?.querySelector('[data-radix-scroll-area-viewport]')
    if (!viewport) return
    viewport.scrollTo({
      top: viewport.scrollHeight,
      behavior,
    })
    setAutoScroll(true)
  }, [])

  // 自动滚动到底部
  useEffect(() => {
    if (autoScroll) {
      requestAnimationFrame(() => scrollToBottom('auto'))
    }
  }, [timeline, autoScroll, scrollToBottom])

  useEffect(() => {
    requestAnimationFrame(() => scrollToBottom('auto'))
  }, [selectedSession, scrollToBottom])

  const handleScroll = useCallback((e: React.UIEvent<HTMLDivElement>) => {
    const target = e.currentTarget.querySelector('[data-radix-scroll-area-viewport]')
    if (!target) return
    const { scrollTop, scrollHeight, clientHeight } = target as HTMLElement
    setAutoScroll(scrollHeight - scrollTop - clientHeight < 80)
  }, [])

  // 统计当前会话的各事件类型计数
  const stats = {
    messages: timeline.filter((e) => e.type === 'message.ingested' || e.type === 'message.sent').length,
    cycles: timeline.filter((e) => e.type === 'planner.finalized').length,
    toolCalls: timeline.reduce((count, entry) => {
      if (entry.type === 'tool.execution') {
        return count + 1
      }
      if (entry.type === 'planner.finalized') {
        return count + ((entry.data as PlannerFinalizedEvent).tools?.length ?? 0)
      }
      return count
    }, 0),
  }
  const selectedStageStatus = selectedSession ? stageStatuses.get(selectedSession) : undefined

  return (
    <div className="flex min-w-0 flex-col gap-4 lg:h-[calc(100vh-116px)] lg:flex-row">
      {/* 会话侧边栏 */}
      <aside className={cn(
        'flex min-w-0 shrink-0 flex-col overflow-hidden border border-border bg-background/45 transition-[width] duration-200',
        sidebarCollapsed ? 'w-full lg:w-16' : 'w-full lg:w-52',
      )}>
        <div className={cn('py-2', sidebarCollapsed ? 'px-2' : 'px-3')}>
          <h2 className={cn(
            'text-sm font-medium flex items-center gap-2',
            sidebarCollapsed && 'justify-center text-[0px]',
          )}>
            {!sidebarCollapsed && <Activity className="h-4 w-4" />}
            聊天流
            {connected && (
              <span className={cn('flex h-2 w-2 rounded-full bg-emerald-500', !sidebarCollapsed && 'ml-auto')} />
            )}
            <Button
              variant="ghost"
              size="icon"
              className="h-6 w-6 shrink-0"
              onClick={() => setSidebarCollapsed((value) => !value)}
              title={sidebarCollapsed ? '展开侧边栏' : '折叠侧边栏'}
            >
              {sidebarCollapsed ? <ChevronRight className="h-3.5 w-3.5" /> : <ChevronLeft className="h-3.5 w-3.5" />}
            </Button>
          </h2>
        </div>
        <Separator />
        <ScrollArea className="max-h-40 flex-1 lg:max-h-none">
          <SessionSidebar
            sessions={sessions}
            stageStatuses={stageStatuses}
            selectedSession={selectedSession}
            onSelect={setSelectedSession}
            collapsed={sidebarCollapsed}
          />
        </ScrollArea>
      </aside>

      {/* 主时间线区域 */}
      <div className="flex min-w-0 flex-1 flex-col">
        {/* 时间线 */}
        <StageStatusPanel
          autoScroll={autoScroll}
          onClearTimeline={clearTimeline}
          onScrollToBottom={() => scrollToBottom('smooth')}
          stats={stats}
          status={selectedStageStatus}
        />

        <Card className="min-h-[420px] min-w-0 flex-1 overflow-hidden lg:min-h-0">
          <ScrollArea
            className="h-full"
            ref={scrollRef}
            onScrollCapture={handleScroll}
          >
            <div className="min-w-0 space-y-3 p-4">
              {timeline.length === 0 ? (
                <div className="flex flex-col items-center justify-center py-20 text-muted-foreground gap-3">
                  <Clock className="h-10 w-10 opacity-30" />
                  <p className="text-sm">等待 MaiSaka 推理事件…</p>
                  <p className="text-xs opacity-60">
                    当 MaiSaka 处理新消息时，推理过程会实时展示在这里
                  </p>
                </div>
              ) : (
                (() => {
                  const noReplyTimingGateCycles = new Set<string>()

                  return timeline.map((entry) => {
                    if (entry.type === 'timing_gate.result') {
                      const data = entry.data as TimingGateResultEvent
                      if (data.action === 'no_action') {
                        noReplyTimingGateCycles.add(buildCycleKey(data.session_id, data.cycle_id))
                      }
                    }

                    if (entry.type === 'planner.response' || entry.type === 'planner.finalized') {
                      const data = entry.data as PlannerResponseEvent | PlannerFinalizedEvent
                      const cycleKey = buildCycleKey(data.session_id, data.cycle_id)
                      if (entry.type === 'planner.finalized' && isPlannerInterrupted(data as PlannerFinalizedEvent)) {
                        const rendered = (
                          <TimelineEventRenderer
                            entry={entry}
                            onOpenReasoning={handleOpenReasoning}
                          />
                        )
                        if (!rendered) return null
                        return (
                          <div
                            key={entry.id}
                            className="animate-in fade-in-0 slide-in-from-bottom-2 duration-300"
                          >
                            {rendered}
                          </div>
                        )
                      }
                      if (noReplyTimingGateCycles.has(cycleKey)) {
                        return null
                      }
                    }

                    const rendered = (
                      <TimelineEventRenderer
                        entry={entry}
                        onOpenReasoning={handleOpenReasoning}
                      />
                    )
                    if (!rendered) return null
                    return (
                      <div
                        key={entry.id}
                        className="animate-in fade-in-0 slide-in-from-bottom-2 duration-300"
                      >
                        {rendered}
                      </div>
                    )
                  })
                })()
              )}
            </div>
          </ScrollArea>
        </Card>
      </div>
    </div>
  )
}
