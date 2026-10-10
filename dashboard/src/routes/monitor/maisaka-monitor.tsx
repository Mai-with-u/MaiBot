/**
 * MaiSaka 聊天流实时监控组件
 *
 * 通过 WebSocket 实时接收 MaiSaka 推理引擎事件，
 * 以时间线形式展示聊天流的推理过程。
 */
import { useNavigate } from '@tanstack/react-router'
import { useVirtualizer } from '@tanstack/react-virtual'
import {
  Activity,
  AlertCircle,
  ArrowLeftRight,
  ArrowRight,
  Bot,
  Brain,
  CheckCircle2,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronUp,
  Clock,
  Database,
  Eraser,
  FileCode2,
  Forward,
  Globe2,
  History,
  Hourglass,
  ImageIcon,
  ImageOff,
  ImagePlus,
  Images,
  Loader2,
  MessageCircle,
  PauseCircle,
  Search,
  Smile,
  Timer,
  UserRound,
  Wrench,
  XCircle,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import type { Key, ReactNode } from 'react'
import { memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'

import { Avatar, AvatarFallback, AvatarImage } from '@/components/ui/avatar'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardHeader, CardTitle } from '@/components/ui/card'
import { Dialog, DialogContent, DialogDescription, DialogTitle } from '@/components/ui/dialog'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Separator } from '@/components/ui/separator'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import { useResolvedAvatarUrl, type AvatarTargetType } from '@/lib/avatar-url'
import { useToast } from '@/hooks/use-toast'
import { backendApi } from '@/lib/http'
import { cn } from '@/lib/utils'

import type {
  MaisakaContextSection,
  MaisakaFinalizedToolResult,
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
import { StageStatusLabel } from './StageStatusLabel'
import { useMaisakaMonitorOverview, useMaisakaMonitorSession } from './use-maisaka-monitor'

// ─── 工具函数 ──────────────────────────────────────────────────

type TimelineScrollBehavior = 'auto' | 'smooth'

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

function EventTimestamp({
  timestamp,
  durationMs,
  startedAt,
  endedAt,
}: {
  timestamp?: number
  durationMs?: number
  startedAt?: number | null
  endedAt?: number | null
}) {
  // 独立完成事件可用结束时间和耗时还原区间；快照必须使用实际记录的起止时间。
  const end = endedAt ?? (durationMs !== undefined ? timestamp : undefined)
  const start =
    startedAt ?? (end != null && durationMs !== undefined ? end - durationMs / 1000 : undefined)
  return (
    <span className="text-muted-foreground text-xs opacity-65 whitespace-nowrap">
      {start != null && end != null
        ? `${formatTimestamp(start)}-${formatTimestamp(end)}`
        : '起止时间未记录'}
    </span>
  )
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

/** 内置工具使用各自的图标，其余工具统一用扳手 */
const BUILTIN_TOOL_ICONS: Record<string, LucideIcon> = {
  reply: MessageCircle,
  wait: Hourglass,
  fetch_history: History,
  query_memory: Database,
  query_image_memory: Images,
  query_person_profile: UserRound,
  send_emoji: Smile,
  send_image: ImagePlus,
  switch_chat: ArrowLeftRight,
  tool_search: Search,
  view_forward_message: Forward,
}

function isMessageTimelineEntry(entry?: TimelineEntry) {
  return entry?.type === 'message.ingested' || entry?.type === 'message.sent'
}

function isWaitTool(toolName?: string) {
  return (toolName ?? '').trim().toLowerCase() === 'wait'
}

/** wait 为内置工具，只需一句「等待 x 秒」，参数、JSON 与执行结果都不展示 */
function formatWaitToolText(toolArgs?: Record<string, unknown>) {
  const seconds = Number(toolArgs?.seconds ?? 30)
  return `等待 ${Number.isFinite(seconds) ? Math.max(0, Math.trunc(seconds)) : 30} 秒`
}

function getToolIcon(toolName?: string): LucideIcon {
  return BUILTIN_TOOL_ICONS[(toolName ?? '').trim().toLowerCase()] ?? Wrench
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
  return (
    status.stage === '空闲' ||
    status.stage === '等待消息' ||
    status.detail.includes('等待消息') ||
    status.agentState === 'wait'
  )
}

function getAgentStateLabel(agentState: string): string | null {
  const normalizedState = agentState.trim().toLowerCase()
  if (!normalizedState || normalizedState === 'stop') return null
  if (normalizedState === 'running') return '运行中'
  if (normalizedState === 'wait') return '等待中'
  return agentState
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
    <Avatar className={cn('ring-border/60 shrink-0 ring-1', className)}>
      {avatarUrl && (
        <AvatarImage src={avatarUrl} alt={`${label} 的头像`} className="object-cover" />
      )}
      <AvatarFallback className={fallbackClassName}>{fallback}</AvatarFallback>
    </Avatar>
  )
}

function SessionAvatar({ session, status }: { session: SessionInfo; status?: StageStatusInfo }) {
  const targetType: AvatarTargetType = session.isGroupChat ? 'group' : 'user'
  const targetId = session.isGroupChat ? session.groupId : session.userId
  const statusDotClassName =
    status && isWaitingForMessage(status) ? 'bg-blue-500' : 'bg-emerald-500'

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
        <span
          className={cn(
            'ring-background absolute -top-0.5 -right-0.5 h-2.5 w-2.5 rounded-full ring-2',
            statusDotClassName
          )}
        />
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
      fallback={
        isSent ? <Bot className="h-3.5 w-3.5" /> : getFallbackInitial(data.speaker_name, '人')
      }
      fallbackClassName={cn(
        'text-xs font-semibold',
        isSent ? 'bg-emerald-500/15 text-emerald-500' : 'bg-blue-500/15 text-blue-500'
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
    (a, b) => b.lastActivity - a.lastActivity
  )

  if (sortedSessions.length === 0) {
    if (collapsed) {
      return <div className="h-full p-2" />
    }

    return (
      <div
        className={cn(
          'text-muted-foreground flex h-full flex-col items-center justify-center gap-2',
          'p-4'
        )}
      >
        <Bot className="h-8 w-8 opacity-40" />
        <p className="text-center text-sm">等待 MaiSaka 会话…</p>
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
              selectedSession === session.sessionId && 'bg-accent text-accent-foreground'
            )}
          >
            <div
              className={cn(
                'flex w-full min-w-0 items-center',
                collapsed ? 'justify-center' : 'justify-between gap-2'
              )}
            >
              <div
                className={cn(
                  'flex min-w-0 items-center gap-2 overflow-hidden',
                  !collapsed && 'flex-1'
                )}
              >
                <SessionAvatar session={session} status={status} />
                {!collapsed && (
                  <span
                    className="block min-w-0 flex-1 overflow-hidden font-medium text-ellipsis whitespace-nowrap"
                    title={session.sessionName}
                  >
                    {session.sessionName}
                  </span>
                )}
              </div>
              {!collapsed && (
                <Badge variant="secondary" className="h-4 shrink-0 px-1 text-[10px]">
                  {session.eventCount}
                </Badge>
              )}
            </div>
            {!collapsed && (
              <div className="text-muted-foreground flex w-full min-w-0 items-center justify-between gap-2 overflow-hidden text-xs">
                <span className="shrink-0">{formatRelativeTime(session.lastActivity)}</span>
                {status && (
                  <span className="text-primary min-w-0 truncate">
                    <StageStatusLabel status={status} />
                  </span>
                )}
              </div>
            )}
          </button>
        )
      })}
    </div>
  )
}

// ─── 单条时间线事件渲染 ──────────────────────────────────────

/** 上下文分段：token 由该轮真实 prompt token 按字符占比换算 */
interface MonitorContextSection {
  key: string
  tokens: number
  percent: number
}

interface MonitorStats {
  messages: number
  cycles: number
  toolCalls: number
  /** 最近一轮 planner 请求的输入 token，即当前上下文占用 */
  contextTokens: number
  /** 与最近一轮 planner 请求对应的上下文分段 */
  contextSections: MonitorContextSection[]
  /** 会话累计 token 用量 */
  totalPromptTokens: number
  totalCompletionTokens: number
  /** 会话累计缓存命中率，无缓存数据时为 null */
  cacheHitRate: number | null
}

/** 上下文分段的展示标签与配色，未知分段直接以 key 展示 */
const CONTEXT_SECTION_META: Record<string, { label: string; color: string }> = {
  messages: { label: '消息', color: 'hsl(var(--color-chart-1))' },
  mcp_tools: { label: 'MCP 工具', color: 'hsl(var(--color-chart-4))' },
  builtin_tools: { label: '系统工具', color: 'hsl(var(--color-chart-2))' },
  plugin_tools: { label: '插件工具', color: 'hsl(var(--primary))' },
  system_prompt: { label: '系统提示词', color: 'hsl(var(--color-chart-5))' },
  instant_notice: { label: '即时提示', color: 'hsl(var(--color-chart-3))' },
  other_tools: { label: '其他工具', color: 'hsl(var(--muted-foreground))' },
}

function resolveContextSectionMeta(key: string) {
  return CONTEXT_SECTION_META[key] ?? { label: key, color: 'hsl(var(--muted-foreground))' }
}

const STATS_TOKEN_FORMATTER = new Intl.NumberFormat('zh-CN', {
  notation: 'compact',
  maximumFractionDigits: 1,
})

function formatTokenCount(value: number): string {
  return STATS_TOKEN_FORMATTER.format(Math.max(Math.round(value), 0))
}

/** 统计浮层内的单行指标 */
function StatsRow({
  children,
  value,
  valueClassName,
}: {
  children: ReactNode
  value: ReactNode
  valueClassName?: string
}) {
  return (
    <div className="flex items-center gap-2 text-[11px] leading-5">
      {children}
      <span className={cn('ml-auto font-mono tabular-nums', valueClassName)}>{value}</span>
    </div>
  )
}

/**
 * 统计浮层：展示最近一轮上下文构成、缓存命中率与会话累计计数。
 *
 * 上下文 token 总量取自模型返回的真实 prompt token，分段占比按后端统计的字符数换算。
 */
function MonitorStatsPanel({ stats }: { stats: MonitorStats }) {
  const hasContext = stats.contextTokens > 0 && stats.contextSections.length > 0

  return (
    <div className="w-72 space-y-2">
      <div className="flex items-baseline justify-between gap-3">
        <span className="text-xs font-medium">上下文容量</span>
        <span className="font-mono text-[11px] tabular-nums">
          {stats.contextTokens > 0 ? `${formatTokenCount(stats.contextTokens)} tokens` : '暂无数据'}
        </span>
      </div>

      {hasContext ? (
        <>
          <div className="bg-muted flex h-1.5 w-full overflow-hidden rounded-full">
            {stats.contextSections.map((section) => (
              <div
                key={section.key}
                className="h-full"
                style={{
                  width: `${section.percent}%`,
                  backgroundColor: resolveContextSectionMeta(section.key).color,
                }}
              />
            ))}
          </div>
          <div className="space-y-0.5">
            {stats.contextSections.map((section) => {
              const sectionMeta = resolveContextSectionMeta(section.key)
              return (
                <StatsRow
                  key={section.key}
                  value={formatTokenCount(section.tokens)}
                  valueClassName="text-muted-foreground w-12 text-right"
                >
                  <span
                    className="h-1.5 w-1.5 shrink-0 rounded-full"
                    style={{ backgroundColor: sectionMeta.color }}
                  />
                  <span className="min-w-0 flex-1 truncate">{sectionMeta.label}</span>
                  <span className="text-muted-foreground w-11 text-right font-mono tabular-nums">
                    {section.percent.toFixed(1)}%
                  </span>
                </StatsRow>
              )
            })}
          </div>
        </>
      ) : (
        <p className="text-muted-foreground text-[11px] leading-5">
          尚无 planner 请求数据，产生一次推理后展示上下文构成。
        </p>
      )}

      <Separator />

      <div className="space-y-0.5">
        <StatsRow
          value={stats.cacheHitRate === null ? '—' : `${stats.cacheHitRate.toFixed(0)}%`}
          valueClassName={stats.cacheHitRate === null ? 'text-muted-foreground' : ''}
        >
          <span className="text-muted-foreground">平均缓存命中率</span>
        </StatsRow>
        <StatsRow
          value={`${formatTokenCount(stats.totalPromptTokens)} / ${formatTokenCount(stats.totalCompletionTokens)}`}
        >
          <span className="text-muted-foreground">累计输入 / 输出</span>
        </StatsRow>
      </div>

      <Separator />

      <div className="space-y-0.5 text-[11px] leading-5">
        <div>消息：{stats.messages}</div>
        <div>循环：{stats.cycles}</div>
        <div>工具调用：{stats.toolCalls}</div>
      </div>
    </div>
  )
}

interface StageStatusPanelProps {
  autoScroll: boolean
  sessionId: string | null
  onClearTimeline: () => void
  onFindPreviousBotMessage: () => void
  onScrollToTop: () => void
  onScrollToBottom: () => void
  stats: MonitorStats
  status?: StageStatusInfo
}

interface ChatActiveContext {
  memory: string[]
  expressions: string[]
  jargon: { name: string; meaning: string }[]
  tools: string[]
  runtime_active: boolean
  replyer_timestamp: number | null
}

function ActiveContextPopover({ sessionId }: { sessionId: string | null }) {
  const [open, setOpen] = useState(false)
  const [context, setContext] = useState<ChatActiveContext | null>(null)
  const [error, setError] = useState(false)

  useEffect(() => {
    if (!open || !sessionId) return
    const controller = new AbortController()
    const load = () => {
      backendApi
        .get<ChatActiveContext>('/api/webui/reasoning-process/active-context', {
          query: { session_id: sessionId },
          signal: controller.signal,
        })
        .then((result) => {
          if (!controller.signal.aborted) {
            setContext(result)
            setError(false)
          }
        })
        .catch(() => {
          if (!controller.signal.aborted) setError(true)
        })
    }
    setContext(null)
    setError(false)
    load()
    const timer = window.setInterval(load, 10000)
    return () => {
      controller.abort()
      window.clearInterval(timer)
    }
  }, [open, sessionId])

  const sections = context
    ? [
        { title: '回想记忆', items: context.memory, emptyText: '当前聊天流尚未生成回想' },
        { title: '表达方式', items: context.expressions, emptyText: '最近一次 Replyer 请求中未使用' },
        { title: '黑话', items: context.jargon.map((entry) => entry.name), emptyText: '当前上下文没有黑话参考' },
        { title: '激活的工具', items: context.tools, emptyText: '当前尚未激活工具' },
      ]
    : []

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button variant="ghost" size="sm" className="h-6 shrink-0 border-0! px-2 text-[11px]" disabled={!sessionId}>
          <Brain className="mr-1 h-3 w-3" />
          当前上下文
        </Button>
      </PopoverTrigger>
      <PopoverContent align="start" side="bottom" className="max-h-[min(70vh,640px)] w-[min(90vw,480px)] overflow-y-auto p-4">
        <div className="mb-2 text-sm font-semibold">当前运行的聊天流</div>
        {error ? <p className="text-muted-foreground text-xs">读取上下文失败</p> : !context ? <p className="text-muted-foreground text-xs">正在读取…</p> : (
          <div className="space-y-3">
            {!context.runtime_active && <p className="text-muted-foreground text-xs">当前聊天流未运行</p>}
            {sections.map((section) => (
              <section key={section.title}>
                <h3 className="mb-1 text-xs font-semibold">{section.title} · {section.items.length}</h3>
                {section.items.length ? section.title === '黑话' ? (
                  <div className="flex flex-wrap gap-1.5">
                    {section.items.map((item) => (
                      <Tooltip key={item}>
                        <TooltipTrigger asChild>
                          <button type="button" className="bg-muted/50 cursor-help rounded px-2 py-1 text-xs">{item}</button>
                        </TooltipTrigger>
                        <TooltipContent side="top" className="z-[70] max-w-sm whitespace-pre-wrap break-words text-xs">
                          {context.jargon.find((entry) => entry.name === item)?.meaning || '暂无释义'}
                        </TooltipContent>
                      </Tooltip>
                    ))}
                  </div>
                ) : section.items.map((item, index) => (
                  <pre key={`${section.title}-${index}`} className="bg-muted/50 mb-1 whitespace-pre-wrap break-words rounded p-2 font-sans text-xs">{item}</pre>
                )) : <p className="text-muted-foreground text-xs">{section.emptyText}</p>}
              </section>
            ))}
            <p className="text-muted-foreground text-[11px]">
              表达方式来自最近一次 Replyer 请求：{context.replyer_timestamp ? new Date(context.replyer_timestamp).toLocaleString() : '无记录'}
            </p>
          </div>
        )}
      </PopoverContent>
    </Popover>
  )
}

function MonitorStatusActions({
  autoScroll,
  sessionId,
  onClearTimeline,
  onFindPreviousBotMessage,
  onScrollToTop,
  onScrollToBottom,
  stats,
}: Omit<StageStatusPanelProps, 'status'>) {
  return (
    <>
      <TooltipProvider delayDuration={150}>
        <Tooltip>
          <TooltipTrigger asChild>
            <div
              className={cn(
                'group bg-background/60 text-muted-foreground flex h-6 shrink-0 items-center gap-1 rounded-md px-1.5 transition-colors',
                // hover / 浮层展开时通过填充色与文字色高亮；主题固定背景色时仍保留图标色反馈。
                'hover:bg-accent',
                'data-[state=delayed-open]:bg-accent',
                'data-[state=instant-open]:bg-accent'
              )}
            >
              <Activity className="group-hover:text-primary h-3 w-3 transition-colors" />
              <span className="group-hover:text-foreground text-[10px] font-medium transition-colors">
                统计
              </span>
            </div>
          </TooltipTrigger>
          <TooltipContent side="bottom" align="start" className="p-3">
            <MonitorStatsPanel stats={stats} />
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>
      <ActiveContextPopover sessionId={sessionId} />
      <div className="ml-auto flex shrink-0 items-center gap-1.5">
        <Button
          variant="ghost"
          size="sm"
          className="h-6 shrink-0 border-0! px-2 text-[11px]"
          onClick={onScrollToTop}
          title="回到顶部"
        >
          <ChevronUp className="mr-1 h-3 w-3" />
          顶部
        </Button>
        <Button
          variant="ghost"
          size="sm"
          className="h-6 shrink-0 border-0! px-2 text-[11px]"
          onClick={onFindPreviousBotMessage}
          title="查找上条麦麦消息"
        >
          <ChevronUp className="mr-1 h-3 w-3" />
          上条
        </Button>
        <Button
          variant="ghost"
          size="sm"
          className="h-6 shrink-0 border-0! px-2 text-[11px]"
          onClick={onScrollToBottom}
          title="回到底部"
        >
          <ChevronDown className={cn('mr-1 h-3 w-3', autoScroll && 'text-primary')} />
          底部
        </Button>
        <Button
          variant="ghost"
          size="icon"
          className="h-6 w-6 shrink-0 border-0!"
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
  sessionId,
  onClearTimeline,
  onFindPreviousBotMessage,
  onScrollToTop,
  onScrollToBottom,
  stats,
  status,
}: StageStatusPanelProps) {
  const agentStateLabel = status ? getAgentStateLabel(status.agentState) : null
  const actions = (
    <MonitorStatusActions
      autoScroll={autoScroll}
      sessionId={sessionId}
      onClearTimeline={onClearTimeline}
      onFindPreviousBotMessage={onFindPreviousBotMessage}
      onScrollToTop={onScrollToTop}
      onScrollToBottom={onScrollToBottom}
      stats={stats}
    />
  )

  if (!status) {
    return (
      <div data-maisaka-toolbar="true" className="bg-muted/30 mb-1.5 flex min-w-0 items-center gap-2 overflow-x-auto rounded-md px-2 py-1">
        {actions}
        <div className="text-muted-foreground shrink-0 text-xs whitespace-nowrap">
          当前聊天流暂无阶段状态
        </div>
      </div>
    )
  }

  return (
    <div data-maisaka-toolbar="true" className="bg-background mb-1.5 flex min-w-0 items-center gap-2 overflow-x-auto rounded-md px-2 py-1">
      {actions}
      <div className="flex shrink-0 items-center gap-1.5">
        <Badge variant="default" className="gap-1 border-0! px-1.5 text-[10px]">
          <Activity className="h-2.5 w-2.5" />
          <StageStatusLabel status={status} />
        </Badge>
        {status.roundText && (
          <Badge variant="secondary" className="border-0! px-1.5 text-[10px]">
            {status.roundText}
          </Badge>
        )}
        {agentStateLabel && (
          <Badge
            variant={status.agentState === 'running' ? 'default' : 'outline'}
            className="border-0! px-1.5 text-[10px]"
          >
            {agentStateLabel}
          </Badge>
        )}
        <span className="text-muted-foreground ml-auto text-[11px]">
          更新于 {formatRelativeTime(status.updatedAt)}
        </span>
      </div>
      {status.detail && (
        <p className="text-muted-foreground shrink-0 text-xs whitespace-nowrap">{status.detail}</p>
      )}
    </div>
  )
}

function ReplyPreviewBlock({
  onJumpToMessage,
  replyTo,
}: {
  onJumpToMessage?: (messageId: string) => void
  replyTo?: MaisakaReplyPreview | null
}) {
  if (!replyTo) {
    return null
  }

  const canJump = Boolean(replyTo.message_id && onJumpToMessage)
  const className = cn(
    'mb-1.5 block max-w-xl rounded-md bg-muted/70 px-2.5 py-1.5 text-left text-xs text-muted-foreground',
    canJump &&
      'cursor-pointer transition-[background-color,box-shadow] hover:bg-muted hover:shadow-[inset_2px_0_0_hsl(var(--primary))] focus-visible:ring-2 focus-visible:ring-primary/55 focus-visible:outline-none'
  )
  const content = (
    <>
      <div className="mb-0.5 flex min-w-0 items-center gap-1.5">
        <span className="text-foreground/80 min-w-0 truncate font-medium">
          回复 {replyTo.sender_name || '未知用户'}
        </span>
        {replyTo.message_id && (
          <span className="text-muted-foreground/70 shrink-0 font-mono text-[10px]">
            #{replyTo.message_id}
          </span>
        )}
      </div>
      <div className="line-clamp-2 leading-4 break-words whitespace-pre-wrap">
        {replyTo.content || '原消息已无法访问'}
      </div>
    </>
  )

  if (canJump) {
    return (
      <button
        type="button"
        className={className}
        title="跳转到原始消息"
        onClick={() => onJumpToMessage?.(replyTo.message_id)}
      >
        {content}
      </button>
    )
  }

  return <div className={className}>{content}</div>
}

function buildMessageMediaKey(media: MaisakaMessageMedia, index: number) {
  return `${media.kind}:${media.hash}:${media.index ?? index}`
}

function MessageMediaItem({ item }: { item: MaisakaMessageMedia }) {
  const inlineSource = item.data_url?.trim() ?? ''
  const remoteSource = item.url.trim()
  const canShowOriginal = Boolean(inlineSource || remoteSource)
  const [showOriginal, setShowOriginal] = useState(
    canShowOriginal && Boolean(item.default_original)
  )
  const [resolvedSource, setResolvedSource] = useState(inlineSource)
  const [loadState, setLoadState] = useState<'idle' | 'loading' | 'error'>(
    showOriginal && !inlineSource ? 'loading' : 'idle'
  )
  const [loadRequestId, setLoadRequestId] = useState(showOriginal && !inlineSource ? 1 : 0)
  const [isPreviewOpen, setIsPreviewOpen] = useState(false)
  const label = item.kind === 'emoji' ? '表情包' : '图片'

  useEffect(() => {
    if (loadRequestId <= 0 || inlineSource || !remoteSource) {
      return
    }

    let cancelled = false
    let objectUrl: string | null = null

    void backendApi
      .get<Blob>(remoteSource, {
        parse: 'blob',
        cache: 'force-cache',
        errorMessage: `读取${label}原文件失败`,
      })
      .then((blob) => {
        if (cancelled) {
          return
        }
        objectUrl = URL.createObjectURL(blob)
        setResolvedSource(objectUrl)
        setLoadState('idle')
      })
      .catch(() => {
        if (!cancelled) {
          setLoadState('error')
        }
      })

    return () => {
      cancelled = true
      if (objectUrl) {
        URL.revokeObjectURL(objectUrl)
      }
    }
  }, [inlineSource, label, loadRequestId, remoteSource])

  return (
    <>
      <div className="relative max-w-full rounded-md">
        {showOriginal ? (
          <button
            type="button"
            className="focus-visible:ring-ring block max-w-full cursor-zoom-in rounded-md text-left focus-visible:ring-2 disabled:cursor-default"
            title={`放大查看${label}`}
            aria-label={`放大查看${label}`}
            disabled={!resolvedSource}
            onClick={() => setIsPreviewOpen(true)}
          >
            {resolvedSource ? (
              <img
                src={resolvedSource}
                alt={`${label}原文件`}
                className={cn(
                  'block rounded object-contain',
                  item.kind === 'emoji' ? 'max-h-24 max-w-24' : 'max-h-56 max-w-full'
                )}
                onError={() => {
                  setResolvedSource('')
                  setLoadState('error')
                }}
              />
            ) : loadState === 'error' ? (
              <span className="text-destructive flex min-h-8 items-center gap-1.5 py-1 pr-9 pl-1 text-xs">
                <ImageOff className="h-3.5 w-3.5 shrink-0" />
                原文件读取失败
              </span>
            ) : (
              <span className="text-muted-foreground flex min-h-8 items-center gap-1.5 py-1 pr-9 pl-1 text-xs">
                <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin" />
                正在读取{label}…
              </span>
            )}
          </button>
        ) : (
          <span className="text-muted-foreground flex min-h-8 max-w-sm items-center gap-1.5 py-1 pr-9 text-xs">
            <ImageIcon className="h-3.5 w-3.5 shrink-0" />
            <span className="min-w-0 break-words whitespace-pre-wrap">
              {item.text || `[${label}]`}
            </span>
          </span>
        )}
        {canShowOriginal && (
          <button
            type="button"
            className={cn(
              'focus-visible:ring-ring absolute top-1 right-1 flex h-6 w-6 items-center justify-center rounded border text-xs font-medium shadow-sm focus-visible:ring-2',
              showOriginal ? 'bg-background/90 text-foreground hover:bg-background' : 'bg-primary text-primary-foreground'
            )}
            title={`切换为${showOriginal ? '识别文本' : '原文件'}`}
            aria-label={`切换为${showOriginal ? '识别文本' : '原文件'}`}
            aria-pressed={!showOriginal}
            onClick={() => {
              if (!showOriginal && !inlineSource && !resolvedSource && loadState !== 'loading') {
                setLoadState('loading')
                setLoadRequestId((current) => current + 1)
              }
              setShowOriginal((current) => !current)
            }}
          >
            文
          </button>
        )}
      </div>
      <Dialog open={isPreviewOpen} onOpenChange={setIsPreviewOpen}>
        <DialogContent className="border-0 bg-black/95 p-2 text-white shadow-2xl [--dialog-width:72rem]">
          <DialogTitle className="sr-only">{label}预览</DialogTitle>
          <DialogDescription className="sr-only">正在查看放大的{label}。</DialogDescription>
          <div className="flex max-h-[calc(100vh-3rem)] min-h-0 w-full items-center justify-center">
            <img
              src={resolvedSource}
              alt={`${label}大图`}
              className="max-h-[calc(100vh-4rem)] max-w-full rounded-md object-contain"
            />
          </div>
        </DialogContent>
      </Dialog>
    </>
  )
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
  const normalizedContent = content ?? ''
  const hasContent = normalizedContent.trim().length > 0
  const hasMedia = media.length > 0

  if (!hasContent && !hasMedia) {
    return (
      <p className="text-foreground/80 text-[14.5px] leading-relaxed wrap-break-word whitespace-pre-wrap">
        {emptyLabel}
      </p>
    )
  }

  return (
    <div className="space-y-1.5">
      {hasContent && (
        <p className="text-foreground/80 text-[14.5px] leading-relaxed wrap-break-word whitespace-pre-wrap">
          {normalizedContent}
        </p>
      )}
      {hasMedia && (
        <div className="flex flex-wrap gap-2">
          {media.map((item, index) => {
            const mediaKey = buildMessageMediaKey(item, index)
            return <MessageMediaItem key={mediaKey} item={item} />
          })}
        </div>
      )}
    </div>
  )
}

function MessageIngestedCard({
  data,
  onJumpToMessage,
}: {
  data: MessageIngestedEvent
  onJumpToMessage: (messageId: string) => void
}) {
  return (
    <div className="flex items-start gap-3">
      <MessageAvatar data={data} kind="ingested" />
      <div className="min-w-0 flex-1">
        <div className="mb-1 flex items-center gap-2">
          <span className="text-[13px] font-medium">{data.speaker_name}</span>
          <span className="text-muted-foreground text-xs opacity-65">{formatTimestamp(data.timestamp)}</span>
        </div>
        <div
          data-maisaka-user-bubble="true"
          className="bg-foreground/[0.07] w-fit max-w-full rounded-lg rounded-tl-none px-3 py-2"
        >
          <ReplyPreviewBlock onJumpToMessage={onJumpToMessage} replyTo={data.reply_to} />
          <MessageMediaContent content={data.content} emptyLabel="[空消息]" media={data.media} />
        </div>
      </div>
    </div>
  )
}

function MessageSentCard({
  data,
  onJumpToMessage,
}: {
  data: MessageSentEvent
  onJumpToMessage: (messageId: string) => void
}) {
  return (
    <div className="flex items-start gap-3">
      <MessageAvatar data={data} kind="sent" />
      <div className="min-w-0 flex-1">
        <div className="mb-1 flex items-center gap-2">
          <span className="text-[13px] font-medium">{data.speaker_name || '麦麦'}</span>
          <span className="text-muted-foreground text-xs opacity-65">{formatTimestamp(data.timestamp)}</span>
        </div>
        <div
          data-maisaka-user-bubble="true"
          className="bg-foreground/[0.07] w-fit max-w-full rounded-lg rounded-tl-none px-3 py-2"
        >
          <ReplyPreviewBlock onJumpToMessage={onJumpToMessage} replyTo={data.reply_to} />
          <MessageMediaContent
            content={data.content}
            emptyLabel="[非文本消息]"
            media={data.media}
          />
        </div>
      </div>
    </div>
  )
}

function TimingGateCard({ data }: { data: TimingGateResultEvent }) {
  const actionConfig: Record<
    string,
    { label: string; variant: 'default' | 'secondary' | 'destructive'; icon: typeof ArrowRight }
  > = {
    continue: { label: '继续执行', variant: 'default', icon: ArrowRight },
    wait: { label: '等待', variant: 'secondary', icon: PauseCircle },
    no_action: { label: '不回复', variant: 'destructive', icon: XCircle },
  }
  const config = actionConfig[data.action] ?? actionConfig.continue
  const Icon = config.icon

  return (
    <div className="bg-background flex items-start gap-3 rounded-md border px-3 py-2 shadow-sm">
      <div className="mt-1 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-amber-500/15 text-amber-500">
        <Timer className="h-3.5 w-3.5" />
      </div>
      <div className="min-w-0 flex-1">
        <div className="mb-1 flex flex-wrap items-center gap-2">
          <span className="text-sm font-medium">反应</span>
          <EventTimestamp timestamp={data.timestamp} durationMs={data.duration_ms} />
          <Badge variant="outline" className="text-[10px]">
            react
          </Badge>
          <Badge variant={config.variant} className="gap-0.5 text-[10px]">
            <Icon className="h-2.5 w-2.5" />
            {config.label}
          </Badge>
          <span className="text-muted-foreground text-xs">{formatMs(data.duration_ms)}</span>
        </div>
        {data.content && <CollapsibleText text={data.content} maxLines={3} />}
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
        <Badge key={`${tc.id || tc.name}-${idx}`} variant="secondary" className="gap-1 text-[10px]">
          <Wrench className="h-2.5 w-2.5" />
          {tc.name}
          {getToolCallSourceLabel(tc.source, tc.source_label) && (
            <span
              className={cn(
                'ml-1 rounded-full border px-1 py-0 text-[9px] leading-4',
                getToolCallSourceBadgeClassName(tc.source)
              )}
            >
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

  if (
    url.origin !== window.location.origin ||
    url.pathname !== '/api/webui/config/maisaka-prompt-preview'
  ) {
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
  return (
    data.interrupted === true ||
    (content.startsWith('Planner ') &&
      data.planner?.prompt_tokens === 0 &&
      data.planner?.completion_tokens === 0 &&
      data.planner?.tool_calls.length === 0)
  )
}

function PlannerInterruptedCard({ data }: { data: PlannerFinalizedEvent }) {
  const planner = data.planner

  return (
    <div className="rounded-md border border-amber-500/35 bg-amber-500/5 px-3 py-2">
      <div className="flex items-center gap-2 text-sm">
        <AlertCircle className="h-4 w-4 shrink-0 text-amber-500" />
        <span className="font-medium">Planner 被新消息打断</span>
        <EventTimestamp startedAt={data.planner_started_at} endedAt={data.planner_ended_at} />
        <Badge variant="outline" className="ml-auto text-[10px]">
          #{data.cycle_id}
        </Badge>
        {planner && planner.duration_ms > 0 && (
          <span className="text-muted-foreground text-xs">{formatMs(planner.duration_ms)}</span>
        )}
      </div>
      <p className="text-muted-foreground mt-1 text-sm">
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
      <div className="min-w-0 flex-1">
        <div className="mb-1 flex flex-wrap items-center gap-2">
          <span className="text-sm font-medium">规划器思考</span>
          <EventTimestamp timestamp={data.timestamp} durationMs={data.duration_ms} />
          <span className="text-muted-foreground text-xs">{formatMs(data.duration_ms)}</span>
          <Badge variant="outline" className="text-[10px]">
            {data.prompt_tokens}+{data.completion_tokens} tokens
          </Badge>
        </div>
        {data.content && <CollapsibleText text={data.content} maxLines={6} />}
        <ToolCallBadges toolCalls={data.tool_calls} />
      </div>
    </div>
  )
}

function ModelNameBadge({ modelName }: { modelName?: string | null }) {
  if (!modelName) return null

  return (
    <Badge variant="outline" className="min-w-0 max-w-full text-[10px] font-normal" title={modelName}>
      <span className="max-w-64 truncate">模型: {modelName}</span>
    </Badge>
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
    <Card data-maisaka-trace-card="true" className="border-l-4 border-l-emerald-500/60">
      <CardHeader className="space-y-2 px-4 py-2">
        <div data-maisaka-trace-meta="true" className="flex flex-wrap items-center gap-2">
          <Brain className="h-4 w-4 text-emerald-500" />
          <CardTitle className="text-sm font-medium">Planner</CardTitle>
          <EventTimestamp startedAt={data.planner_started_at} endedAt={data.planner_ended_at} />
          <ModelNameBadge modelName={planner?.model_name} />
          <Badge variant="outline" className="ml-auto text-xs font-normal">
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
        </div>

        {planner?.content ? (
          <CollapsibleText text={planner.content} maxLines={6} className="text-foreground/90" />
        ) : (
          <p className="text-muted-foreground text-sm">planner 本轮没有文本内容</p>
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

type DisplayTool = MaisakaFinalizedToolResult & { status?: 'running' | 'pending' }

type MonitorMessage = MessageIngestedEvent | MessageSentEvent
type ReplyToolMessageProps = {
  messages?: Map<string, MonitorMessage>
  onJumpToMessage?: (messageId: string) => void
}

/** reply 的 at 和附图参数引用的是消息编号，按真实消息解析发送者与媒体。 */
function ReplyToolArguments({
  tool,
  messages,
  onJumpToMessage,
}: ReplyToolMessageProps & {
  tool: DisplayTool
}) {
  const args = tool.tool_args ?? {}
  const messageId = String(args.msg_id ?? '')
  const message = messages?.get(messageId)
  const atTargets = Array.isArray(args.attach_at) ? args.attach_at : []
  const pictures = Array.isArray(args.attach_pic) ? args.attach_pic : []
  const intentLabels: Record<string, string> = {
    focus: '表达重点',
    reply_act: '回复目的',
    scene: '场景',
    tone: '语气',
    prefer: '偏好',
    avoid: '避免',
  }
  const handledArguments = new Set([
    'msg_id',
    'attach_at',
    'attach_pic',
    'expression_intent',
    'reply_reference',
    'set_quote',
  ])

  return (
    <div className="space-y-2 text-xs">
      <div className="flex flex-wrap items-center gap-2">
        {messageId && (
          <div className="min-w-0 max-w-xl [&>button]:mb-0 [&>div]:mb-0">
          <ReplyPreviewBlock
            onJumpToMessage={onJumpToMessage}
            replyTo={{
              message_id: messageId,
              sender_name: message?.speaker_name ?? '',
              content: message?.content ?? '',
            }}
          />
          </div>
        )}
        {atTargets.length > 0 && (
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="text-muted-foreground">at 对象：</span>
            {atTargets.map((target, index) => {
            const targetId = String(target)
            return (
              <button
                key={`${targetId}-${index}`}
                type="button"
                className="text-primary bg-primary/10 hover:bg-primary/20 rounded-md px-2 py-0.5"
                title={`跳转到目标消息 #${targetId}`}
                onClick={() => onJumpToMessage?.(targetId)}
              >
                @{messages?.get(targetId)?.speaker_name || `消息 #${targetId} 的发送者`}
              </button>
            )
            })}
          </div>
        )}
        {args.set_quote === true && (
          <span className="shrink-0 rounded-md bg-muted/70 px-2.5 py-1.5 text-xs text-muted-foreground">
            引用
          </span>
        )}
        {!tool.status && <ToolFullJsonBlock tool={tool} />}
      </div>
      {args.expression_intent != null && (
        <details className="group rounded-md border px-2.5 py-1.5">
          <summary className="text-muted-foreground flex cursor-pointer list-none items-center gap-1">
            <ChevronRight className="h-3 w-3 transition-transform group-open:rotate-90" />
            表达倾向
          </summary>
          <div className="mt-2 space-y-1 break-words whitespace-pre-wrap">
            {typeof args.expression_intent === 'object' && !Array.isArray(args.expression_intent)
              ? Object.entries(args.expression_intent).map(([name, value]) => (
                  <div key={name}>
                    <span className="text-muted-foreground">{intentLabels[name] ?? name}：</span>
                    {Array.isArray(value)
                      ? value.map(formatToolValue).join('、')
                      : formatToolValue(value)}
                  </div>
                ))
              : formatToolValue(args.expression_intent)}
          </div>
        </details>
      )}
      {typeof args.reply_reference === 'string' && args.reply_reference.trim() && (
        <div className="bg-muted/50 rounded-md px-2.5 py-2">
          <span className="text-muted-foreground mb-1 block">回复参考信息</span>
          <p className="leading-5 break-words whitespace-pre-wrap">
            {args.reply_reference}
          </p>
        </div>
      )}
      {pictures.length > 0 && (
        <div className="space-y-1.5">
          <span className="text-muted-foreground">附加图片</span>
          <div className="flex flex-wrap items-start gap-2">
            {pictures.map((picture, index) => {
              if (!picture || typeof picture !== 'object') {
                return <ToolArgumentBlock key={index} name="图片" value={picture} />
              }
              const reference = picture as Record<string, unknown>
              const sourceId = String(reference.msg_id ?? '')
              const imageIndex = Number(reference.index ?? 0)
              const sourceMessage = messages?.get(sourceId)
              // 工具 index 是图片序号，监控媒体的 index 则是原始消息组件序号。
              const media = sourceMessage?.media?.filter((item) => item.kind === 'image')[
                imageIndex
              ]
              return (
                <div key={index} className="max-w-sm space-y-1 rounded-md border p-2">
                  {sourceId ? (
                    <button
                      type="button"
                      className="text-primary hover:underline"
                      title="跳转到图片来源消息"
                      onClick={() => onJumpToMessage?.(sourceId)}
                    >
                      {sourceMessage?.speaker_name || `消息 #${sourceId}`} · 第 {imageIndex + 1}{' '}
                      张图片
                    </button>
                  ) : (
                    <p className="break-all">工具媒体：{String(reference.media_index ?? '')}</p>
                  )}
                  {media && <MessageMediaItem item={{ ...media, default_original: true }} />}
                  {sourceId && !media && (
                    <p className="text-muted-foreground">图片不在当前监控记录中</p>
                  )}
                </div>
              )
            })}
          </div>
        </div>
      )}
      {Object.entries(args)
        .filter(([name]) => !handledArguments.has(name))
        .map(([name, value]) => (
          <ToolArgumentBlock key={name} name={name} value={value} />
        ))}
    </div>
  )
}

function ToolArgumentBlock({ name, value }: { name: string; value: unknown }) {
  const formattedValue = formatToolValue(value)
  const inlineValue = formattedValue.replace(/\s+/g, ' ')

  return (
    <div
      className="bg-background/60 flex h-6 max-w-full min-w-0 items-center gap-1.5 rounded-md border px-2 text-xs"
      title={`${name} (${getValueTypeLabel(value)}): ${formattedValue}`}
    >
      <span className="text-foreground shrink-0 font-mono font-semibold">{name}</span>
      <span className="text-muted-foreground shrink-0">=</span>
      <span className="text-muted-foreground max-w-72 min-w-0 truncate font-mono text-[11px]">
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
        className="text-muted-foreground hover:text-foreground ml-auto flex h-6 cursor-pointer list-none items-center gap-1 px-1.5 text-[10px]"
        title="完整调用 JSON"
      >
        <ChevronRight className="h-2.5 w-2.5 shrink-0 transition-transform group-open:rotate-90" />
        <span>JSON</span>
      </summary>
      <pre className="bg-background/60 text-muted-foreground basis-full rounded-md border px-2.5 py-1.5 font-mono text-[11px] leading-4 break-words whitespace-pre-wrap">
        {JSON.stringify(payload, null, 2)}
      </pre>
    </details>
  )
}

function ToolResultImage({ image }: { image: NonNullable<MaisakaFinalizedToolResult['images']>[number] }) {
  const [failed, setFailed] = useState(false)

  if (failed || image.error || !image.thumbnail_url) {
    return (
      <span className="text-muted-foreground flex items-center gap-1.5 text-xs" title={image.label}>
        <ImageOff className="h-3.5 w-3.5 shrink-0" />
        {image.error || '图片读取失败'}
      </span>
    )
  }

  return (
    <img
      src={image.thumbnail_url}
      alt={image.label}
      title={image.label}
      loading="lazy"
      decoding="async"
      referrerPolicy="no-referrer"
      className="max-h-48 max-w-full rounded-md border object-contain sm:max-w-48"
      onError={() => setFailed(true)}
    />
  )
}

function ToolResultImages({ images }: { images: MaisakaFinalizedToolResult['images'] }) {
  if (!images?.length) return null

  return (
    <div className="flex flex-wrap items-start gap-2 pt-1" aria-label="工具返回图片">
      {images.map((image, index) => (
        <ToolResultImage key={index} image={image} />
      ))}
    </div>
  )
}

function PlannerToolResultCard({
  tool,
  index,
  hideSourceLabel = false,
  hideHeader = false,
  onOpenReasoning,
  messages,
  onJumpToMessage,
}: ReplyToolMessageProps & {
  tool: DisplayTool
  index: number
  /** 所有工具来源一致时由卡片右上角统一展示，单条工具不再重复 */
  hideSourceLabel?: boolean
  /** 单个工具时名称、耗时与推理入口已合并到卡片标题行 */
  hideHeader?: boolean
  onOpenReasoning: (promptHtmlUri: string) => void
}) {
  const argumentEntries = Object.entries(tool.tool_args ?? {})
  const statusText =
    tool.status === 'running'
      ? '执行中'
      : tool.status === 'pending'
        ? '等待执行'
        : tool.success
          ? '执行成功'
          : '执行失败'
  const sourceLabel = getToolCallSourceLabel(tool.tool_call_source, tool.tool_call_source_label)
  const promptHtmlUri = tool.prompt_html_uri?.trim() ?? ''
  const canOpenReasoning = Boolean(promptHtmlUri && parsePromptHtmlReasoningTarget(promptHtmlUri))
  const isToolSearch = tool.tool_name === 'tool_search'
  const searchQuery = typeof tool.tool_args?.query === 'string' ? tool.tool_args.query : ''
  const activatedTools = tool.matched_tool_names
  const newlyDiscoveredTools = new Set(tool.newly_discovered_tool_names)

  if (isWaitTool(tool.tool_name)) {
    if (hideHeader) return <ToolResultImages images={tool.images} />
    return (
      <div className="space-y-1.5">
        <div data-maisaka-trace-meta="true" className="flex flex-wrap items-center gap-1.5">
          <Hourglass className="text-muted-foreground h-3.5 w-3.5" />
          <span className="text-foreground text-sm font-medium">
            {formatWaitToolText(tool.tool_args)}
          </span>
          <EventTimestamp startedAt={tool.started_at} endedAt={tool.ended_at} />
          <span className="text-muted-foreground ml-auto text-[10px]">#{index + 1}</span>
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
        </div>
        <ToolResultImages images={tool.images} />
      </div>
    )
  }

  return (
    <div className="space-y-1.5">
      {!hideHeader && (
        <div data-maisaka-trace-meta="true" className="flex flex-wrap items-center gap-1.5">
          <span className="text-foreground font-mono text-sm font-semibold">
            {tool.tool_name || 'unknown'}
          </span>
          <EventTimestamp startedAt={tool.started_at} endedAt={tool.ended_at} />
          <ModelNameBadge modelName={tool.model_name} />
          {tool.status && (
            <Badge variant="secondary" className="h-5 px-1.5 text-[10px]">
              {statusText}
            </Badge>
          )}
          {!tool.status && !tool.success && (
            <>
              <XCircle className="h-3.5 w-3.5 text-red-500" />
              <Badge variant="destructive" className="h-5 px-1.5 text-[10px]">
                {statusText}
              </Badge>
            </>
          )}
          {sourceLabel && !hideSourceLabel && (
            <Badge
              variant="outline"
              className={cn(
                'h-5 px-1.5 text-[10px]',
                getToolCallSourceBadgeClassName(tool.tool_call_source)
              )}
            >
              {sourceLabel}
            </Badge>
          )}
          {tool.duration_ms > 0 && (
            <span className="text-muted-foreground text-xs font-medium">
              {formatMs(tool.duration_ms)}
            </span>
          )}
          <span className="text-muted-foreground ml-auto text-[10px]">#{index + 1}</span>
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
        </div>
      )}

      <div className="space-y-1.5">
        {isToolSearch && searchQuery && (
          <div className="flex flex-wrap items-center gap-1.5 text-xs">
            <span className="text-muted-foreground">搜索工具：</span>
            <span className="text-foreground font-mono">{searchQuery}</span>
            {!tool.status && <ToolFullJsonBlock tool={tool} />}
          </div>
        )}
        {tool.tool_name === 'reply' && (
          <ReplyToolArguments tool={tool} messages={messages} onJumpToMessage={onJumpToMessage} />
        )}
        {!isToolSearch && tool.tool_name !== 'reply' && argumentEntries.length > 0 && (
          <div className="flex flex-wrap items-center gap-1.5">
            {argumentEntries.map(([name, value]) => (
              <ToolArgumentBlock key={name} name={name} value={value} />
            ))}
            {!tool.status && <ToolFullJsonBlock tool={tool} />}
          </div>
        )}

        {!tool.status && isToolSearch && tool.success && activatedTools ? (
          <div className="flex flex-wrap items-start gap-x-2 text-xs">
            <span className="text-muted-foreground shrink-0">激活工具：</span>
            {activatedTools.length > 0 ? (
              <div className="flex flex-col gap-0.5">
                {activatedTools.map((name) => (
                  <div key={name} className="flex flex-wrap items-center gap-2">
                    <span className="text-foreground font-mono">{name}</span>
                    <span className="text-muted-foreground text-[10px]">
                      {newlyDiscoveredTools.has(name) ? '本次新发现' : '此前已发现'}
                    </span>
                  </div>
                ))}
              </div>
            ) : (
              <span className="text-muted-foreground">未找到匹配工具</span>
            )}
          </div>
        ) : (
          !tool.status && (
            <div className="flex flex-wrap items-baseline gap-x-1.5">
              <span className="text-muted-foreground shrink-0 text-[10px] leading-4 font-medium">
                执行结果
              </span>
              <p className="text-muted-foreground min-w-0 flex-1 text-xs leading-4 break-words whitespace-pre-wrap">
                {tool.summary || '未返回结果摘要。'}
              </p>
            </div>
          )
        )}
        <ToolResultImages images={tool.images} />
      </div>
    </div>
  )
}

function PlannerToolCallsBlock({
  data,
  isProgress,
  onOpenReasoning,
  messages,
  onJumpToMessage,
}: ReplyToolMessageProps & {
  data: PlannerFinalizedEvent
  isProgress: boolean
  onOpenReasoning: (promptHtmlUri: string) => void
}) {
  const toolCalls = data.planner?.tool_calls ?? []
  const tools = data.tools ?? []
  const completedTools = new Map(tools.map((tool) => [tool.tool_call_id, tool]))
  const displayTools: DisplayTool[] = isProgress
      ? toolCalls.map((toolCall) => completedTools.get(toolCall.id) ?? ({
          tool_call_id: toolCall.id,
          tool_name: toolCall.name,
          tool_args: toolCall.arguments ?? {},
          tool_call_source: toolCall.source,
          tool_call_source_label: toolCall.source_label,
          success: true,
          duration_ms: 0,
          summary: '',
          status: data.active_tool_call_id === toolCall.id ? 'running' : 'pending',
        }))
      : tools.length > 0
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
  const regularTools = displayTools.filter(
    (tool) => !isFinishTool(tool.tool_name) || tool.status || tool.images?.length
  )
  const plannerStopped =
    finishTools.some((tool) => !tool.status) ||
    data.final_state.end_reason === 'tool_stop_after_execution'
  const sharedSource = regularTools[0]?.tool_call_source
  const sharedSourceLabel =
    regularTools.length > 0 &&
    regularTools.every(
      (tool) =>
        tool.tool_call_source === sharedSource &&
        getToolCallSourceLabel(tool.tool_call_source, tool.tool_call_source_label) ===
          getToolCallSourceLabel(sharedSource, regularTools[0].tool_call_source_label)
    )
      ? getToolCallSourceLabel(sharedSource, regularTools[0].tool_call_source_label)
      : ''
  const singleTool = regularTools.length === 1 ? regularTools[0] : null
  const ToolIcon = singleTool ? getToolIcon(singleTool.tool_name) : Wrench
  const singleToolIsWait = Boolean(singleTool && isWaitTool(singleTool.tool_name))
  const singleToolPromptHtmlUri = singleTool?.prompt_html_uri?.trim() ?? ''
  const canOpenSingleToolReasoning = Boolean(
    singleToolPromptHtmlUri && parsePromptHtmlReasoningTarget(singleToolPromptHtmlUri)
  )

  if (displayTools.length <= 0) {
    return null
  }

  if (regularTools.length <= 0 && finishTools.length > 0 && plannerStopped) {
    return (
      <div className="rounded-md border border-emerald-500/30 bg-emerald-500/5 px-3 py-2">
        <div className="flex items-center gap-2 text-sm">
          <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-500" />
          <span className="font-medium">本轮思考暂时结束</span>
          <EventTimestamp startedAt={finishTools[0]?.started_at} endedAt={finishTools[0]?.ended_at} />
          <span className="text-muted-foreground">等待新的消息。</span>
        </div>
      </div>
    )
  }

  return (
    <Card data-maisaka-trace-card="true" className="border-l-4 border-l-teal-500/60">
      <CardHeader className="space-y-2 px-4 py-2">
        <div data-maisaka-trace-meta="true" className="flex flex-wrap items-center gap-2">
          <ToolIcon className="h-4 w-4 text-teal-500" />
          <CardTitle
            className={cn('text-sm font-semibold', !singleToolIsWait && 'font-mono')}
          >
            {singleToolIsWait
              ? formatWaitToolText(singleTool?.tool_args)
              : regularTools.map((tool) => tool.tool_name || 'unknown').join('、')}
          </CardTitle>
          <ModelNameBadge modelName={singleTool?.model_name} />
          {singleTool && !singleTool.status && (
            <EventTimestamp startedAt={singleTool.started_at} endedAt={singleTool.ended_at} />
          )}
          {singleTool?.status && (
            <Badge variant="secondary" className="px-1.5 text-[10px]">
              {singleTool.status === 'running' ? '执行中' : '等待执行'}
            </Badge>
          )}
          {singleTool && !singleTool.status && !singleTool.success && (
            <Badge variant="destructive" className="px-1.5 text-[10px]">
              执行失败
            </Badge>
          )}
          {singleTool && !singleToolIsWait && singleTool.duration_ms > 0 && (
            <span className="text-muted-foreground text-xs font-medium">
              {formatMs(singleTool.duration_ms)}
            </span>
          )}
          {sharedSourceLabel && (
            <Badge
              variant="outline"
              className={cn(
                'ml-auto px-1.5 text-[10px]',
                getToolCallSourceBadgeClassName(sharedSource)
              )}
            >
              {sharedSourceLabel}
            </Badge>
          )}
          {!singleTool && (
            <Badge
              variant="secondary"
              className={cn('text-[10px]', !sharedSourceLabel && 'ml-auto')}
            >
              {regularTools.length} 个
            </Badge>
          )}
          {canOpenSingleToolReasoning && (
            <Button
              variant="ghost"
              size="sm"
              className={cn('h-6 px-1.5 text-[10px]', !sharedSourceLabel && 'ml-auto')}
              onClick={() => onOpenReasoning(singleToolPromptHtmlUri)}
              title="查看这个工具对应的推理"
            >
              <FileCode2 className="mr-1 h-3 w-3" />
              推理
            </Button>
          )}
        </div>
        {plannerStopped && (
          <div className="flex items-center gap-2 rounded-md border border-emerald-500/30 bg-emerald-500/5 px-2.5 py-1.5 text-xs">
            <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-emerald-500" />
            <span className="font-medium">本轮思考暂时结束</span>
            <span className="text-muted-foreground">等待新的消息。</span>
          </div>
        )}
        {(!singleToolIsWait || Boolean(singleTool?.images?.length)) && (
        <div className="space-y-2">
          {regularTools.map((tool, idx) => (
            <div key={`${tool.tool_call_id || tool.tool_name}-${idx}`} className="space-y-2">
              {idx > 0 && <Separator />}
              <PlannerToolResultCard
                messages={messages}
                onJumpToMessage={onJumpToMessage}
                tool={tool}
                index={idx}
                hideSourceLabel={Boolean(sharedSourceLabel)}
                hideHeader={Boolean(singleTool)}
                onOpenReasoning={onOpenReasoning}
              />
            </div>
          ))}
        </div>
        )}
      </CardHeader>
    </Card>
  )
}

function PlannerNativeToolCallsBlock({ data }: { data: PlannerFinalizedEvent }) {
  const nativeToolCalls = data.planner?.native_tool_calls ?? []
  if (nativeToolCalls.length <= 0) {
    return null
  }

  return (
    <Card data-maisaka-trace-card="true" className="border-l-4 border-l-sky-500/60">
      <CardHeader className="space-y-2 px-4 py-2">
        <div data-maisaka-trace-meta="true" className="flex items-center gap-2">
          <Globe2 className="h-4 w-4 text-sky-500" />
          <CardTitle className="text-sm font-medium">Provider 原生工具</CardTitle>
          <span className="text-muted-foreground text-xs opacity-65">推理区间</span>
          <EventTimestamp startedAt={data.planner_started_at} endedAt={data.planner_ended_at} />
          <Badge variant="secondary" className="ml-auto text-[10px]">
            {nativeToolCalls.length} 次
          </Badge>
        </div>
        <div className="space-y-2">
          {nativeToolCalls.map((toolCall, index) => (
            <div
              key={`${toolCall.call_id || toolCall.tool_type}-${index}`}
              className="bg-muted/20 space-y-1 rounded-md border px-2.5 py-2"
            >
              <div className="flex flex-wrap items-center gap-1.5">
                <span className="font-mono text-sm font-semibold">
                  {toolCall.tool_type === 'web_search' ? '联网搜索' : toolCall.tool_type}
                </span>
                {toolCall.action_type && (
                  <Badge variant="outline" className="text-[10px]">
                    {toolCall.action_type}
                  </Badge>
                )}
                {toolCall.status && (
                  <Badge variant="outline" className="text-[10px]">
                    {toolCall.status}
                  </Badge>
                )}
                {toolCall.source_count > 0 && (
                  <span className="text-muted-foreground ml-auto text-[10px]">
                    来源 {toolCall.source_count} 个
                  </span>
                )}
              </div>
              {toolCall.details.length > 0 ? (
                toolCall.details.map((detail, detailIndex) => (
                  <p
                    key={`${toolCall.call_id || index}-detail-${detailIndex}`}
                    className="text-foreground/80 text-xs break-words"
                  >
                    {detail}
                  </p>
                ))
              ) : (
                <p className="text-muted-foreground text-xs">供应商未返回查询详情。</p>
              )}
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
      <div
        className={cn(
          'mt-1 flex h-7 w-7 shrink-0 items-center justify-center rounded-full',
          data.success ? 'bg-teal-500/15 text-teal-500' : 'bg-red-500/15 text-red-500'
        )}
      >
        <Wrench className="h-3.5 w-3.5" />
      </div>
      <div className="min-w-0 flex-1">
        <div className="mb-1 flex flex-wrap items-center gap-2">
          <span className="font-mono text-sm font-medium">{data.tool_name}</span>
          <EventTimestamp timestamp={data.timestamp} durationMs={data.duration_ms} />
          {data.success ? (
            <CheckCircle2 className="h-3.5 w-3.5 text-teal-500" />
          ) : (
            <XCircle className="h-3.5 w-3.5 text-red-500" />
          )}
          <span className="text-muted-foreground text-xs">{formatMs(data.duration_ms)}</span>
        </div>
        {Object.keys(data.tool_args).length > 0 && (
          <div className="text-muted-foreground bg-muted/50 mb-1 rounded px-2 py-1 font-mono text-xs break-all whitespace-pre-wrap">
            {JSON.stringify(data.tool_args, null, 2)}
          </div>
        )}
        {data.result_summary && (
          <CollapsibleText
            text={data.result_summary}
            maxLines={3}
            className="text-muted-foreground"
          />
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
        <p className={cn('text-sm leading-relaxed wrap-break-word whitespace-pre-wrap', className)}>
          {text}
        </p>
        {needsCollapse && (
          <button
            onClick={() => setExpanded(false)}
            className="text-primary mt-1 flex items-center gap-0.5 text-xs hover:underline"
          >
            <ChevronDown className="h-3 w-3" /> 收起
          </button>
        )}
      </div>
    )
  }

  return (
    <div>
      <p className={cn('text-sm leading-relaxed wrap-break-word whitespace-pre-wrap', className)}>
        {lines.slice(0, maxLines).join('\n')}
      </p>
      <button
        onClick={() => setExpanded(true)}
        className="text-primary mt-1 flex items-center gap-0.5 text-xs hover:underline"
      >
        <ChevronRight className="h-3 w-3" /> 展开全部 ({lines.length} 行)
      </button>
    </div>
  )
}

// ─── 回复器响应卡片 ──────────────────────────────────────────

function ReplierResponseCard({ data }: { data: ReplierResponseEvent }) {
  return (
    <Card data-maisaka-trace-card="true" className="border-l-4 border-l-purple-500/60">
      <CardHeader className="space-y-2 px-4 py-2">
        <div data-maisaka-trace-meta="true" className="flex items-center gap-2">
          <Bot className="h-4 w-4 text-purple-500" />
          <CardTitle className="text-sm font-medium">回复器响应</CardTitle>
          <Badge variant="outline" className="ml-auto text-xs font-normal">
            {formatMs(data.duration_ms)}
          </Badge>
          {data.success ? (
            <Badge variant="secondary" className="gap-1 text-xs">
              <CheckCircle2 className="h-3 w-3" /> 成功
            </Badge>
          ) : (
            <Badge variant="destructive" className="gap-1 text-xs">
              <XCircle className="h-3 w-3" /> 失败
            </Badge>
          )}
          <EventTimestamp timestamp={data.timestamp} durationMs={data.duration_ms} />
        </div>
        {data.content && (
          <CollapsibleText text={data.content} maxLines={6} className="text-foreground/90" />
        )}
        {data.reasoning && (
          <details className="mt-1">
            <summary className="text-muted-foreground hover:text-foreground cursor-pointer text-xs">
              思考过程
            </summary>
            <CollapsibleText
              text={data.reasoning}
              maxLines={8}
              className="text-muted-foreground mt-1"
            />
          </details>
        )}
        {(data.prompt_tokens > 0 || data.completion_tokens > 0) && (
          <div className="text-muted-foreground mt-1 flex gap-3 text-xs">
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
  onJumpToMessage,
  onOpenReasoning,
  messages,
}: {
  entry: TimelineEntry
  messages?: Map<string, MonitorMessage>
  onJumpToMessage: (messageId: string) => void
  onOpenReasoning: (promptHtmlUri: string) => void
}) {
  switch (entry.type) {
    case 'message.ingested':
      return (
        <MessageIngestedCard
          data={entry.data as MessageIngestedEvent}
          onJumpToMessage={onJumpToMessage}
        />
      )
    case 'message.sent':
      return (
        <MessageSentCard data={entry.data as MessageSentEvent} onJumpToMessage={onJumpToMessage} />
      )
    case 'timing_gate.result':
      return <TimingGateCard data={entry.data as TimingGateResultEvent} />
    case 'planner.response':
      return <PlannerResponseCard data={entry.data as PlannerResponseEvent} />
    case 'planner.finalized':
    case 'planner.progress':
      if (isPlannerInterrupted(entry.data as PlannerFinalizedEvent)) {
        return <PlannerInterruptedCard data={entry.data as PlannerFinalizedEvent} />
      }
      if ((entry.data as PlannerFinalizedEvent).timing_gate?.result?.action === 'no_action') {
        return null
      }
      return (
        <div className="space-y-2">
          <PlannerFinalizedCard
            data={entry.data as PlannerFinalizedEvent}
            onOpenReasoning={onOpenReasoning}
          />
          <PlannerNativeToolCallsBlock data={entry.data as PlannerFinalizedEvent} />
          <PlannerToolCallsBlock
            messages={messages}
            onJumpToMessage={onJumpToMessage}
            data={entry.data as PlannerFinalizedEvent}
            isProgress={entry.type === 'planner.progress'}
            onOpenReasoning={onOpenReasoning}
          />
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

interface MaisakaMonitorProps {
  /** 嵌入聊天工作区时隐藏重复的会话侧边栏，并使用父容器提供的完整高度。 */
  embedded?: boolean
  /** 打开推理详情后返回的地址；未提供时返回当前页面。 */
  reasoningReturnTo?: string
}

const MonitorTimeline = memo(function MonitorTimeline({
  embedded = false,
  reasoningReturnTo,
}: MaisakaMonitorProps) {
  const navigate = useNavigate()
  const { toast } = useToast()
  const { timeline, selectedSession, selectedStageStatus, clearTimeline } = useMaisakaMonitorSession()

  const scrollRef = useRef<HTMLDivElement>(null)
  const [scrollViewport, setScrollViewport] = useState<HTMLDivElement | null>(null)
  const [autoScroll, setAutoScroll] = useState(true)
  const [focusedMessageId, setFocusedMessageId] = useState<string | null>(null)
  const focusTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  /** 程序化跳转滚动期间忽略滚动监听，防止自动跟随把视图拉回底部 */
  const revealScrollLockRef = useRef(false)
  const previousSelectedSessionRef = useRef<string | null | undefined>(undefined)
  /** 用户向上浏览时记录视口顶部的锚点条目，列表变化后据此恢复位置，避免内容被顶走 */
  const scrollAnchorRef = useRef<{ key: Key; offset: number } | null>(null)

  const handleOpenReasoning = useCallback(
    (promptHtmlUri: string) => {
      const target = parsePromptHtmlReasoningTarget(promptHtmlUri)
      if (!target) return

      const params = new URLSearchParams({
        stage: target.stage,
        session: target.session,
        stem: target.stem,
        returnTo:
          reasoningReturnTo ??
          `${window.location.pathname}${window.location.search}${window.location.hash}`,
      })
      navigate({ to: `/reasoning-process?${params.toString()}` })
    },
    [navigate, reasoningReturnTo]
  )

  useEffect(
    () => () => {
      if (focusTimerRef.current !== null) {
        clearTimeout(focusTimerRef.current)
      }
    },
    []
  )

  useEffect(() => {
    const viewport = scrollRef.current?.querySelector(
      '[data-radix-scroll-area-viewport]'
    ) as HTMLDivElement | null
    setScrollViewport(viewport)
  }, [])

  const visibleTimelineEntries = useMemo(() => {
    const noReplyTimingGateCycles = new Set<string>()
    const visibleEntries: TimelineEntry[] = []

    for (const entry of timeline) {
      if (entry.type === 'timing_gate.result') {
        const data = entry.data as TimingGateResultEvent
        if (data.action === 'no_action') {
          noReplyTimingGateCycles.add(buildCycleKey(data.session_id, data.cycle_id))
        }
        visibleEntries.push(entry)
        continue
      }

      if (entry.type === 'planner.response' || entry.type === 'planner.finalized' || entry.type === 'planner.progress') {
        const data = entry.data as PlannerResponseEvent | PlannerFinalizedEvent
        const cycleKey = buildCycleKey(data.session_id, data.cycle_id)
        if (
          entry.type === 'planner.finalized' &&
          isPlannerInterrupted(data as PlannerFinalizedEvent)
        ) {
          visibleEntries.push(entry)
          continue
        }
        if (noReplyTimingGateCycles.has(cycleKey)) {
          continue
        }
        visibleEntries.push(entry)
        continue
      }

      if (
        entry.type === 'message.ingested' ||
        entry.type === 'message.sent' ||
        entry.type === 'tool.execution' ||
        entry.type === 'replier.response'
      ) {
        visibleEntries.push(entry)
      }
    }

    return visibleEntries
  }, [timeline])

  // TanStack Virtual 与 React Compiler 不兼容，保持现有虚拟列表实现
  // eslint-disable-next-line react-hooks/incompatible-library
  const timelineVirtualizer = useVirtualizer({
    count: visibleTimelineEntries.length,
    getScrollElement: () => scrollViewport,
    estimateSize: () => 140,
    getItemKey: (index) => visibleTimelineEntries[index]?.id ?? index,
    overscan: 8,
  })

  const messageEntryIndexes = useMemo(() => {
    const indexes = new Map<string, number>()
    visibleTimelineEntries.forEach((entry, index) => {
      if (entry.type !== 'message.ingested' && entry.type !== 'message.sent') {
        return
      }
      const data = entry.data as MessageIngestedEvent | MessageSentEvent
      if (data.message_id && !indexes.has(data.message_id)) {
        indexes.set(data.message_id, index)
      }
    })
    return indexes
  }, [visibleTimelineEntries])

  const monitorMessages = useMemo(() => {
    const messages = new Map<string, MonitorMessage>()
    visibleTimelineEntries.forEach((entry) => {
      if (isMessageTimelineEntry(entry)) {
        const message = entry.data as MonitorMessage
        messages.set(message.message_id, message)
      }
    })
    return messages
  }, [visibleTimelineEntries])

  /** 滚动到指定时间线位置，并短暂高亮该条消息（供消息跳转与“查找上条”共用） */
  const revealTimelineMessage = useCallback(
    (targetIndex: number, messageId: string) => {
      setAutoScroll(false)
      // 平滑滚动的开头几帧仍在底部阈值内，锁住滚动监听，避免被当成“用户回到底部”而拉回底部
      revealScrollLockRef.current = true
      setFocusedMessageId(messageId)
      timelineVirtualizer.scrollToIndex(targetIndex, {
        align: 'center',
        behavior: 'smooth',
      })
      if (focusTimerRef.current !== null) {
        clearTimeout(focusTimerRef.current)
      }
      focusTimerRef.current = setTimeout(() => {
        setFocusedMessageId(null)
        revealScrollLockRef.current = false
        focusTimerRef.current = null
      }, 1800)
    },
    [timelineVirtualizer]
  )

  const handleJumpToMessage = useCallback(
    (messageId: string) => {
      const targetIndex = messageEntryIndexes.get(messageId)
      if (targetIndex === undefined) {
        toast({
          title: '原始消息不在当前时间线',
          description: '该消息可能已被清除、尚未加载，或不属于当前聊天流。',
          variant: 'destructive',
        })
        return
      }

      revealTimelineMessage(targetIndex, messageId)
    },
    [messageEntryIndexes, revealTimelineMessage, toast]
  )

  /** 向上查找当前视口上方最近一条麦麦自己发送的消息并滚动过去 */
  const handleFindPreviousBotMessage = useCallback(() => {
    const viewportTop = scrollViewport?.scrollTop ?? 0
    // 虚拟列表带 overscan，按行位置过滤出视口内第一行，避免选中视口上方的预渲染行
    const firstVisibleIndex =
      timelineVirtualizer.getVirtualItems().find((item) => item.end > viewportTop)?.index ?? 0

    for (let index = firstVisibleIndex - 1; index >= 0; index -= 1) {
      const entry = visibleTimelineEntries[index]
      if (entry?.type !== 'message.sent') {
        continue
      }
      const data = entry.data as MessageSentEvent
      if (!data.message_id) {
        continue
      }
      revealTimelineMessage(index, data.message_id)
      return
    }

    toast({
      title: '上方没有更多麦麦发送的消息',
      description: '已经到达当前时间线里麦麦发送消息的最上方。',
    })
  }, [revealTimelineMessage, scrollViewport, timelineVirtualizer, toast, visibleTimelineEntries])

  const scrollToBottom = useCallback(
    (behavior: TimelineScrollBehavior = 'smooth') => {
      if (visibleTimelineEntries.length > 0) {
        timelineVirtualizer.scrollToIndex(visibleTimelineEntries.length - 1, {
          align: 'end',
          behavior,
        })
      } else {
        scrollViewport?.scrollTo({
          top: scrollViewport.scrollHeight,
          behavior,
        })
      }
      setAutoScroll(true)
    },
    [scrollViewport, timelineVirtualizer, visibleTimelineEntries.length]
  )

  const scrollToTop = useCallback(() => {
    setAutoScroll(false)
    revealScrollLockRef.current = true
    if (visibleTimelineEntries.length > 0) {
      timelineVirtualizer.scrollToIndex(0, { align: 'start', behavior: 'smooth' })
    } else {
      scrollViewport?.scrollTo({ top: 0, behavior: 'smooth' })
    }
  }, [scrollViewport, timelineVirtualizer, visibleTimelineEntries.length])

  // 自动滚动到底部
  useEffect(() => {
    if (autoScroll) {
      requestAnimationFrame(() => scrollToBottom('auto'))
    }
  }, [visibleTimelineEntries.length, autoScroll, scrollToBottom])

  useEffect(() => {
    if (previousSelectedSessionRef.current === selectedSession) {
      return
    }
    previousSelectedSessionRef.current = selectedSession
    setAutoScroll(true)
    requestAnimationFrame(() => scrollToBottom('auto'))
  }, [selectedSession, scrollToBottom])

  const handleScroll = useCallback(
    (e: React.UIEvent<HTMLDivElement>) => {
      const target =
        scrollViewport ?? e.currentTarget.querySelector('[data-radix-scroll-area-viewport]')
      if (!target) return
      const { scrollTop, scrollHeight, clientHeight } = target as HTMLElement
      const distanceToBottom = scrollHeight - scrollTop - clientHeight

      // 程序化跳转期间：平滑滚动的起始帧仍在底部阈值内，
      // 此时既不能重新开启自动跟随（会打断滚动并拉回底部），也不需要关闭
      if (revealScrollLockRef.current) {
        if (distanceToBottom < 80) return
        // 已经离开底部阈值，恢复正常判定
        revealScrollLockRef.current = false
      }

      const anchorItem = timelineVirtualizer.getVirtualItems().find((item) => item.end > scrollTop)
      scrollAnchorRef.current = anchorItem
        ? { key: anchorItem.key, offset: scrollTop - anchorItem.start }
        : null

      setAutoScroll(distanceToBottom < 80)
    },
    [scrollViewport, timelineVirtualizer]
  )

  // 未跟随底部时，时间线头部被裁剪或上方插入新条目都会改变锚点条目的位置；
  // 在绘制前把滚动位置补偿回去，保持用户正在浏览的内容不动
  useLayoutEffect(() => {
    if (autoScroll || !scrollViewport || revealScrollLockRef.current) return
    const anchor = scrollAnchorRef.current
    if (!anchor) return
    const anchorItem = timelineVirtualizer.measurementsCache.find((item) => item.key === anchor.key)
    if (!anchorItem) return
    const targetTop = anchorItem.start + anchor.offset
    if (Math.abs(scrollViewport.scrollTop - targetTop) > 1) {
      scrollViewport.scrollTop = targetTop
    }
  }, [autoScroll, scrollViewport, timelineVirtualizer, visibleTimelineEntries])

  // 统计当前会话的各事件类型计数，以及上下文构成与缓存命中情况
  const stats = useMemo(() => {
    const currentStats: MonitorStats = {
      messages: 0,
      cycles: 0,
      toolCalls: 0,
      contextTokens: 0,
      contextSections: [],
      totalPromptTokens: 0,
      totalCompletionTokens: 0,
      cacheHitRate: null,
    }
    let cacheHitTokens = 0
    let cacheMissTokens = 0
    // 最近一轮带上下文分段统计的请求
    let latestSections: MaisakaContextSection[] = []

    for (const entry of timeline) {
      if (entry.type === 'message.ingested' || entry.type === 'message.sent') {
        currentStats.messages += 1
        continue
      }
      if (entry.type === 'tool.execution') {
        currentStats.toolCalls += 1
        continue
      }
      if (entry.type !== 'planner.finalized') {
        continue
      }

      currentStats.cycles += 1
      const finalized = entry.data as PlannerFinalizedEvent
      currentStats.toolCalls += finalized.tools?.length ?? 0
      const planner = finalized.planner
      if (!planner) {
        continue
      }

      currentStats.totalPromptTokens += planner.prompt_tokens
      currentStats.totalCompletionTokens += planner.completion_tokens
      cacheHitTokens += planner.prompt_cache_hit_tokens ?? 0
      cacheMissTokens += planner.prompt_cache_miss_tokens ?? 0
      // 以最近一轮真实请求代表当前上下文
      if (planner.prompt_tokens > 0 && finalized.request?.context_sections?.length) {
        currentStats.contextTokens = planner.prompt_tokens
        latestSections = finalized.request.context_sections
      }
    }

    const cacheTotalTokens = cacheHitTokens + cacheMissTokens
    if (cacheTotalTokens > 0) {
      currentStats.cacheHitRate = (cacheHitTokens / cacheTotalTokens) * 100
    }

    const totalChars = latestSections.reduce((sum, section) => sum + section.chars, 0)
    if (currentStats.contextTokens > 0 && totalChars > 0) {
      // 按字符占比把真实 token 总量分摊到各分段，占比大的分段优先展示
      currentStats.contextSections = latestSections
        .filter((section) => section.chars > 0)
        .map((section) => ({
          key: section.key,
          tokens: (currentStats.contextTokens * section.chars) / totalChars,
          percent: (section.chars / totalChars) * 100,
        }))
        .sort((a, b) => b.percent - a.percent)
    }

    return currentStats
  }, [timeline])
  const virtualItems = timelineVirtualizer.getVirtualItems()

  // 主时间线区域
  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col">
      {/* 时间线 */}
      <StageStatusPanel
        autoScroll={autoScroll}
        sessionId={selectedSession}
        onClearTimeline={clearTimeline}
        onFindPreviousBotMessage={handleFindPreviousBotMessage}
        onScrollToTop={scrollToTop}
        onScrollToBottom={() => scrollToBottom('smooth')}
        stats={stats}
        status={selectedStageStatus}
      />

      <Card
        className={cn(
          'min-w-0 flex-1 overflow-hidden',
          embedded ? 'min-h-0' : 'min-h-[420px] lg:min-h-0'
        )}
      >
        <ScrollArea className="h-full" ref={scrollRef} onScrollCapture={handleScroll}>
          <div className="min-w-0 p-4">
            {visibleTimelineEntries.length === 0 ? (
              <div className="text-muted-foreground flex flex-col items-center justify-center gap-3 py-20">
                <Clock className="h-10 w-10 opacity-30" />
                <p className="text-sm">等待 MaiSaka 推理事件…</p>
                <p className="text-xs opacity-60">
                  当 MaiSaka 处理新消息时，推理过程会实时展示在这里
                </p>
              </div>
            ) : (
              <div
                className="relative min-w-0"
                style={{ height: `${timelineVirtualizer.getTotalSize()}px` }}
              >
                {virtualItems.map((virtualItem) => {
                  const entry = visibleTimelineEntries[virtualItem.index]
                  if (!entry) return null
                  const entryData = entry.data as unknown as Record<string, unknown>
                  const entryMessageId =
                    typeof entryData.message_id === 'string' ? entryData.message_id : undefined
                  // 推理与推理、推理与消息之间用细横线分隔，连续消息之间不加
                  const previousEntry = visibleTimelineEntries[virtualItem.index - 1]
                  const showDivider =
                    Boolean(previousEntry) &&
                    !(isMessageTimelineEntry(entry) && isMessageTimelineEntry(previousEntry))
                  return (
                    <div
                      key={virtualItem.key}
                      ref={timelineVirtualizer.measureElement}
                      data-index={virtualItem.index}
                      className="absolute top-0 right-0 left-0 pb-3"
                      style={{ transform: `translateY(${virtualItem.start}px)` }}
                    >
                      {showDivider && (
                        <div
                          data-maisaka-timeline-divider="true"
                          className="border-border mb-3 border-t"
                        />
                      )}
                      <div
                        data-maisaka-message-id={entryMessageId}
                        data-jump-highlighted={
                          entryMessageId && focusedMessageId === entryMessageId
                            ? 'true'
                            : undefined
                        }
                        className={cn(
                          'animate-in fade-in-0 slide-in-from-bottom-2 rounded-md duration-300',
                          entryMessageId &&
                            focusedMessageId === entryMessageId &&
                            'bg-primary/5 ring-primary/55 ring-offset-background ring-2 ring-offset-2'
                        )}
                      >
                        <TimelineEventRenderer
                          messages={monitorMessages}
                          entry={entry}
                          onJumpToMessage={handleJumpToMessage}
                          onOpenReasoning={handleOpenReasoning}
                        />
                      </div>
                    </div>
                  )
                })}
              </div>
            )}
          </div>
        </ScrollArea>
      </Card>
    </div>
  )
})

// 会话摘要与详情分别订阅，其他群的状态更新不会穿透到虚拟时间线。
function MonitorSidebar() {
  const { sessions, stageStatuses, selectedSession, setSelectedSession, connected } =
    useMaisakaMonitorOverview()
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => {
    const saved = localStorage.getItem('maisaka-monitor-sidebar-collapsed')
    return saved !== 'false'
  })

  useEffect(() => {
    localStorage.setItem('maisaka-monitor-sidebar-collapsed', String(sidebarCollapsed))
  }, [sidebarCollapsed])

  // 会话侧边栏
  return (
    <aside
      className={cn(
        'border-border bg-background/45 flex min-w-0 shrink-0 flex-col overflow-hidden border transition-[width] duration-200',
        sidebarCollapsed ? 'w-full lg:w-16' : 'w-full lg:w-52'
      )}
    >
      <div className={cn('py-2', sidebarCollapsed ? 'px-2' : 'px-3')}>
        <h2
          className={cn(
            'flex items-center gap-2 text-sm font-medium',
            sidebarCollapsed && 'justify-center text-[0px]'
          )}
        >
          {!sidebarCollapsed && <Activity className="h-4 w-4" />}
          聊天流
          {connected && (
            <span
              className={cn(
                'flex h-2 w-2 rounded-full bg-emerald-500',
                !sidebarCollapsed && 'ml-auto'
              )}
            />
          )}
          <Button
            variant="ghost"
            size="icon"
            className="h-6 w-6 shrink-0"
            onClick={() => setSidebarCollapsed((value) => !value)}
            title={sidebarCollapsed ? '展开侧边栏' : '折叠侧边栏'}
          >
            {sidebarCollapsed ? (
              <ChevronRight className="h-3.5 w-3.5" />
            ) : (
              <ChevronLeft className="h-3.5 w-3.5" />
            )}
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
  )
}

export const MaisakaMonitor = memo(function MaisakaMonitor({
  embedded = false,
  reasoningReturnTo,
}: MaisakaMonitorProps = {}) {
  return (
    <div
      className={cn(
        'flex min-w-0 flex-col',
        embedded ? 'h-full min-h-0 p-2 sm:p-3' : 'gap-4 lg:h-[calc(100vh-116px)] lg:flex-row'
      )}
    >
      {!embedded && <MonitorSidebar />}
      <MonitorTimeline embedded={embedded} reasoningReturnTo={reasoningReturnTo} />
    </div>
  )
})
