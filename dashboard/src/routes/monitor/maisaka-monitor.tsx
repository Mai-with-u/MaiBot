import { ConversationAvatar, MessageBubble, TraceRound, TraceStep } from '@/components/ui/conversation'
import { DataPanel } from '@/components/ui/surface'
import { StatusIndicator } from '@/components/ui/data-browser'
/**
 * MaiSaka 聊天流实时监控组件
 *
 * 通过 WebSocket 实时接收 MaiSaka 推理引擎事件，
 * 以时间线形式展示聊天流的推理过程。
 */
import { useNavigate } from '@tanstack/react-router'
import { elementScroll, useVirtualizer } from '@tanstack/react-virtual'
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
  Database,
  Forward,
  Globe2,
  Hourglass,
  ImageIcon,
  ImageOff,
  ImagePlus,
  Images,
  Loader2,
  MessageCircle,
  PauseCircle,
  Search,
  Share,
  Smile,
  Timer,
  UserRound,
  Wrench,
  XCircle,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { AnimatePresence, motion, useReducedMotion } from 'motion/react'
import type { ReactNode } from 'react'
import { memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'

import {
  EnsureImageGallery,
  ImageGalleryProvider,
  useImageGallery,
  type GalleryImage,
} from '@/components/image-gallery'
import { Avatar, AvatarFallback, AvatarImage } from '@/components/ui/avatar'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { CardTitle } from '@/components/ui/card'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Separator } from '@/components/ui/separator'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import { useResolvedAvatarUrl, type AvatarTargetType } from '@/lib/avatar-url'
import { useVirtualizerUnmountGuard } from '@/lib/use-virtualizer-unmount-guard'
import { useToast } from '@/hooks/use-toast'
import { backendApi } from '@/lib/http'
import { cn } from '@/lib/utils'

import type {
  FlowStepEvent,
  LlmErrorEvent,
  LlmRetryEvent,
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
import { TimelineScrollPosition } from './TimelineScrollPosition'
import { getStageStatusLight } from './stage-status-light'
import { ObservationHeader } from './ObservationHeader'
import { SubagentPanel, SubagentToggleButton } from './subagent-panel'
import { openSubagentPanel } from './subagent-panel-state'
import { useMaisakaMonitorOverview, useMaisakaMonitorSession } from './use-maisaka-monitor'

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
  const recordedTime = start ?? end ?? timestamp
  if (recordedTime == null) return null
  return (
    <span className="text-muted-foreground text-xs opacity-65 whitespace-nowrap">
      {start != null && end != null
        ? `${formatTimestamp(start)}-${formatTimestamp(end)}`
        : formatTimestamp(recordedTime)}
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

/** 内置工具使用各自的图标，其余工具统一用扳手 */
const BUILTIN_TOOL_ICONS: Record<string, LucideIcon> = {
  reply: MessageCircle,
  wait: Hourglass,
  query_memory: Database,
  query_image_memory: Images,
  query_person_profile: UserRound,
  send_emoji: Smile,
  send_image: ImagePlus,
  tool_search: Search,
  view_forward_message: Forward,
}

function isMessageTimelineEntry(entry?: TimelineEntry) {
  return entry?.type === 'message.ingested' || entry?.type === 'message.sent'
}

function isWaitTool(toolName?: string) {
  return (toolName ?? '').trim().toLowerCase() === 'wait'
}

/** wait 为内置工具，只显示等待时长及完成状态，参数、JSON 与执行结果都不展示。 */
function formatWaitToolText(toolArgs?: Record<string, unknown>, completed = false) {
  const seconds = Number(toolArgs?.seconds ?? 30)
  return `${completed ? '已等待' : '等待'} ${Number.isFinite(seconds) ? Math.max(0, Math.trunc(seconds)) : 30} 秒`
}

function getToolIcon(toolName?: string): LucideIcon {
  return BUILTIN_TOOL_ICONS[(toolName ?? '').trim().toLowerCase()] ?? Wrench
}

function getFallbackInitial(label: string, fallback: string) {
  const normalizedLabel = label.trim()
  if (normalizedLabel) return normalizedLabel.slice(0, 1)
  return fallback
}

function getSessionInitial(session: SessionInfo) {
  return getFallbackInitial(session.sessionName, session.isGroupChat ? '群' : '私')
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
  const statusDotClassName = {
    on: 'bg-emerald-500',
    mixed: 'bg-amber-500',
    off: 'bg-muted-foreground',
    error: 'bg-red-500',
  }[getStageStatusLight(status)]

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
  const avatarUrl = useResolvedAvatarUrl(data.platform, data.user_id, 'user')

  return (
    <ConversationAvatar src={avatarUrl} name={data.speaker_name || (isSent ? '麦麦' : '用户')} bot={isSent} />
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
  sessionName?: string
  allExpanded?: boolean
  onToggleAll?: () => void
  autoScroll: boolean
  sessionId: string | null
  onClearTimeline: () => void
  onFindPreviousBotMessage: () => void
  onScrollToTop: () => void
  onScrollToBottom: () => void
  stats: MonitorStats
  status?: StageStatusInfo
  hideStatus?: boolean
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
        { title: '上下文状态摘要', items: context.memory, emptyText: '当前上下文尚未进行压缩' },
        { title: '表达方式', items: context.expressions, emptyText: '最近一次 Replyer 请求中未使用' },
        { title: '黑话', items: context.jargon.map((entry) => entry.name), emptyText: '当前上下文没有黑话参考' },
        { title: '激活的工具', items: context.tools, emptyText: '当前尚未激活工具' },
      ]
    : []

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button variant="outline" size="sm" className="shrink-0" disabled={!sessionId}>
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

function StageStatusPanel({
  sessionName, status, hideStatus = false, sessionId, stats, ...navigation
}: StageStatusPanelProps) {
  return (
    <ObservationHeader
      title={sessionName || status?.sessionName}
      subtitle={!hideStatus && (status ? <StageStatusContent status={status} /> : '当前聊天流暂无阶段状态')}
      {...navigation}
      tools={
        <>
          <TooltipProvider delayDuration={150}>
            <Tooltip>
              <TooltipTrigger asChild>
                <Button variant="outline" size="sm"><Activity />统计</Button>
              </TooltipTrigger>
              <TooltipContent side="bottom" align="start" className="p-3">
                <MonitorStatsPanel stats={stats} />
              </TooltipContent>
            </Tooltip>
          </TooltipProvider>
          <ActiveContextPopover sessionId={sessionId} />
          <SubagentToggleButton />
        </>
      }
    />
  )
}

function ToolExecutionElapsed({ startedAt }: { startedAt: number }) {
  const [now, setNow] = useState(() => Date.now() / 1000)
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now() / 1000), 1000)
    return () => window.clearInterval(timer)
  }, [])
  const seconds = Math.max(0, Math.floor(now - startedAt))
  return <span>已执行 {seconds < 60 ? `${seconds} 秒` : `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`}</span>
}

function FloatingToolStatus({ status, args }: { status: StageStatusInfo; args: Record<string, unknown> }) {
  const [expanded, setExpanded] = useState(false)
  const reducedMotion = useReducedMotion()
  return (
    <div
      data-maisaka-floating-status="true"
      className="bg-background/95 border-border pointer-events-auto max-w-full rounded-md border shadow-lg backdrop-blur-sm"
    >
      <button
        type="button"
        className="flex w-full cursor-pointer flex-wrap items-center gap-2 px-3 py-2 text-left"
        aria-label="查看当前工具调用参数"
        aria-expanded={expanded}
        onClick={() => setExpanded((value) => !value)}
      >
        <StageStatusContent status={status} floating />
        <ChevronDown className={cn('text-muted-foreground h-3 w-3 transition-transform', expanded && 'rotate-180')} />
      </button>
      <AnimatePresence initial={false}>
        {expanded && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: reducedMotion ? 0 : 0.18 }}
            className="overflow-hidden"
          >
            <div className="flex max-h-60 flex-wrap gap-2 overflow-auto border-t px-3 py-2">
              {Object.entries(args).map(([name, value]) => (
                <ToolArgumentBlock key={name} name={name} value={value} />
              ))}
              {Object.keys(args).length === 0 && <span className="text-muted-foreground text-xs">无参数</span>}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}

function StageStatusContent({
  status,
  floating = false,
}: {
  status: StageStatusInfo
  floating?: boolean
}) {
  const agentStateLabel = getAgentStateLabel(status.agentState)
  return (
    <>
      <div className="flex flex-wrap items-center gap-1.5 text-xs">
        <StatusIndicator
          variant="inline"
          status={getStageStatusLight(status)}
        >
          <StageStatusLabel status={status} />
        </StatusIndicator>
        {status.roundText && <span>{status.roundText}</span>}
        {agentStateLabel && <span>{agentStateLabel}</span>}
        <span className="text-muted-foreground ml-auto text-[11px]">
          {floating || status.stage === '组织上下文' ? (
            <ToolExecutionElapsed key={status.stageStartedAt} startedAt={status.stageStartedAt} />
          ) : (
            `更新于 ${formatRelativeTime(status.updatedAt)}`
          )}
        </span>
      </div>
      {status.detail && (
        <p className="text-muted-foreground max-w-72 text-xs break-words" title={status.detail}>
          {status.detail}
        </p>
      )}
    </>
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

/** 可放大查看的媒体对应的图库条目；内联数据直接显示，远端原文件需带登录态读取。 */
function toGalleryImage(item: MaisakaMessageMedia): GalleryImage | null {
  const inlineSource = item.data_url?.trim() ?? ''
  const remoteSource = item.url.trim()
  if (!inlineSource && !remoteSource) return null
  const label = item.kind === 'emoji' ? '表情包' : '图片'
  // 原图显示使用固定媒体框，虚拟列表重挂载时不会从一行加载提示突然撑成大图。
  return { src: inlineSource || remoteSource, alt: `${label}大图`, authenticated: !inlineSource }
}

function MessageMediaItem({ item }: { item: MaisakaMessageMedia }) {
  const galleryImage = useMemo(() => toGalleryImage(item), [item])
  if (!galleryImage) return <MessageMediaItemBody item={item} />
  return (
    <EnsureImageGallery image={galleryImage}>
      <MessageMediaItemBody item={item} galleryImage={galleryImage} />
    </EnsureImageGallery>
  )
}

function MessageMediaItemBody({
  item,
  galleryImage,
}: {
  item: MaisakaMessageMedia
  galleryImage?: GalleryImage
}) {
  const gallery = useImageGallery()
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
            className={cn(
              'focus-visible:ring-ring bg-muted/20 grid max-w-full place-items-center overflow-hidden rounded-md text-left focus-visible:ring-2 disabled:cursor-default cursor-zoom-in',
              item.kind === 'emoji' ? 'h-24 w-24' : 'h-56 w-80'
            )}
            title={`放大查看${label}`}
            aria-label={`放大查看${label}`}
            disabled={!resolvedSource}
            onClick={() => galleryImage && gallery?.open(galleryImage)}
          >
            {resolvedSource ? (
              <img
                src={resolvedSource}
                alt={`${label}原文件`}
                className="block h-full w-full rounded object-contain"
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
    <MessageBubble
      name={data.speaker_name}
      time={formatTimestamp(data.timestamp)}
      avatar={<MessageAvatar data={data} kind="ingested" />}
    >
      <ReplyPreviewBlock onJumpToMessage={onJumpToMessage} replyTo={data.reply_to} />
      <MessageMediaContent content={data.content} emptyLabel="[空消息]" media={data.media} />
    </MessageBubble>
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
    <MessageBubble
      name={data.speaker_name || '麦麦'}
      time={formatTimestamp(data.timestamp)}
      avatar={<MessageAvatar data={data} kind="sent" />}
    >
      <ReplyPreviewBlock onJumpToMessage={onJumpToMessage} replyTo={data.reply_to} />
      <MessageMediaContent content={data.content} emptyLabel="[非文本消息]" media={data.media} />
    </MessageBubble>
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
    <Badge variant="outline" className="text-muted-foreground min-w-0 max-w-full text-[10px] font-normal opacity-65" title={modelName}>
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
    <TraceStep
      data-maisaka-trace-card="true"
      data-maisaka-planner-card="true"
      tone="success"
    >
      <div className="space-y-2">
        <div data-maisaka-trace-meta="true" className="flex flex-wrap items-center gap-2">
          <CardTitle className="text-sm font-medium">Planner</CardTitle>
          <ModelNameBadge modelName={planner?.model_name} />
          {data.request && (
            <span className="text-muted-foreground text-xs">
              上下文 {data.request.selected_history_count} 条 / 可用工具 {data.request.tool_count}
            </span>
          )}
          {canOpenReasoning && (
            <Button
              variant="outline"
              size="icon-xs"

              onClick={() => onOpenReasoning(promptHtmlUri)}
              aria-label="查看推理"
              data-maisaka-reasoning-button="true"
              title="在推理过程页查看对应记录"
            >
              <Share className="h-3.5 w-3.5" />
            </Button>
          )}
        </div>

        {/* 没有文本内容时只保留标题行，后面直接跟工具 */}
        {planner?.reasoning?.trim() && (
          <div className="border-muted-foreground/30 bg-muted/40 space-y-1 rounded-r-md border-l-2 px-3 py-2">
            <div className="text-muted-foreground text-xs font-medium">模型思考</div>
            <CollapsibleText text={planner.reasoning} maxLines={6} className="text-muted-foreground text-xs" />
          </div>
        )}
        {planner?.content && (
          <div className="space-y-1">
            {planner.reasoning?.trim() && <div className="text-xs font-medium">正文</div>}
            <CollapsibleText text={planner.content} maxLines={6} className="text-foreground/90" />
          </div>
        )}
      </div>
    </TraceStep>
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
function ReplyArgumentRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex min-w-0 flex-1 items-baseline gap-2 rounded-md border px-2.5 py-1.5 text-xs leading-5">
      <span className="text-muted-foreground shrink-0">{label}：</span>
      <div className="min-w-0 break-words whitespace-pre-wrap">{children}</div>
    </div>
  )
}

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
    'reply_style',
    'set_quote',
  ])

  return (
    <div className="space-y-2 text-xs">
      <div className="flex flex-wrap items-center gap-2">
        {messageId && (
          <ReplyArgumentRow label="回复对象">
            <button
              type="button"
              className="text-left hover:underline"
              title={`跳转到消息 #${messageId}`}
              onClick={() => onJumpToMessage?.(messageId)}
            >
              {message?.speaker_name && `${message.speaker_name} `}
              <span className="text-muted-foreground">#{messageId}</span>
              {message?.content && ` · ${message.content}`}
            </button>
          </ReplyArgumentRow>
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
        <ReplyArgumentRow label="回复参考信息">{args.reply_reference}</ReplyArgumentRow>
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
          <ReplyArgumentRow key={name} label={name}>
            {formatToolValue(value)}
          </ReplyArgumentRow>
        ))}
    </div>
  )
}

const TOOL_PARAMETER_CLASS_NAME = 'border-border text-muted-foreground inline-flex max-w-full min-w-0 items-center gap-1.5 rounded-md border bg-transparent px-2 py-1 font-mono text-xs'

function ToolArgumentBlock({ name, value }: { name: string; value: unknown }) {
  const formattedValue = formatToolValue(value)
  const inlineValue = formattedValue.replace(/\s+/g, ' ')

  return (
    <div
      className={TOOL_PARAMETER_CLASS_NAME}
      title={`${name} (${getValueTypeLabel(value)}): ${formattedValue}`}
    >
      <span className="shrink-0">{name}</span>
      <span className="shrink-0">=</span>
      <span className="max-w-72 min-w-0 truncate">
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
    error_message?: string
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
  const [expanded, setExpanded] = useState(false)
  const payload = {
    tool_call_id: tool.tool_call_id,
    tool_name: tool.tool_name,
    tool_args: tool.tool_args,
    success: tool.success,
    error_message: tool.error_message || undefined,
    duration_ms: tool.duration_ms,
    summary: tool.summary,
    prompt_html_uri: tool.prompt_html_uri,
    tool_call_source: tool.tool_call_source,
    tool_call_source_label: tool.tool_call_source_label,
  }

  return (
    <>
      <button
        type="button"
        className="text-muted-foreground hover:text-foreground ml-auto flex h-6 cursor-pointer list-none items-center gap-1 px-1.5 text-[10px]"
        title="完整调用 JSON"
        data-maisaka-expand="true"
        aria-expanded={expanded}
        onClick={() => setExpanded((value) => !value)}
      >
        <ChevronRight className={cn('h-2.5 w-2.5 shrink-0 transition-transform', expanded && 'rotate-90')} />
        <span>JSON</span>
      </button>
      {expanded && (
        <pre className="bg-background/60 text-muted-foreground w-full min-w-0 basis-full rounded-md border px-2.5 py-1.5 font-mono text-[11px] leading-4 break-words whitespace-pre-wrap">
          {JSON.stringify(payload, null, 2)}
        </pre>
      )}
    </>
  )
}

function ToolResultImage({ image }: { image: NonNullable<MaisakaFinalizedToolResult['images']>[number] }) {
  const [failed, setFailed] = useState(false)

  // 加载、成功和失败使用同一尺寸，避免屏外缩略图重新加载时改变整行高度。
  const frameClassName = 'h-48 w-48 max-w-full shrink-0 rounded-md border bg-muted/20'
  if (failed || image.error || !image.thumbnail_url) {
    return (
      <span className={cn(frameClassName, 'text-muted-foreground flex items-center justify-center gap-1.5 p-3 text-xs')} title={image.label}>
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
      className={cn(frameClassName, 'object-contain')}
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

/** 倒计时只刷新等待条目，避免每秒重绘整个时间线。 */
function WaitToolLabel({ args, waitUntil, pending = false }: {
  args?: Record<string, unknown>
  waitUntil?: number
  pending?: boolean
}) {
  const [now, setNow] = useState(() => Date.now() / 1000)
  useEffect(() => {
    if (waitUntil === undefined) return
    const timer = window.setInterval(() => setNow(Date.now() / 1000), 1000)
    return () => window.clearInterval(timer)
  }, [waitUntil])
  const remaining = waitUntil === undefined ? undefined : Math.max(0, Math.ceil(waitUntil - now))
  const completed = remaining === 0 || (remaining === undefined && !pending)
  return (
    <span className="inline-flex items-center gap-2">
      <Hourglass
        className={cn('h-4 w-4 text-teal-500', remaining !== undefined && remaining > 0 && 'animate-spin')}
        style={{ animationDuration: '3s' }}
      />
      <span>{completed ? formatWaitToolText(args, true) : remaining === undefined ? formatWaitToolText(args) : `等待 ${remaining} 秒`}</span>
    </span>
  )
}

function ToolDiscoveryBlock({ tools }: { tools: DisplayTool[] }) {
  if (!tools.some((tool) => tool.newly_discovered_tool_names?.length)) return null
  return (
    <TooltipProvider>
      <div className="flex flex-wrap gap-2">
        {tools.flatMap((tool) => (tool.newly_discovered_tool_names ?? []).map((name) => (
          <Tooltip key={`${tool.tool_call_id}-${name}`}>
            <TooltipTrigger asChild>
              <button type="button" className={TOOL_PARAMETER_CLASS_NAME}>
                {name}
              </button>
            </TooltipTrigger>
            <TooltipContent side="top" className="max-h-80 max-w-xl overflow-y-auto p-3 text-xs">
              <p className="mb-2">搜索关键词：{String(tool.tool_args.query ?? '')}</p>
              <pre className="font-mono break-words whitespace-pre-wrap">
                {typeof tool.detail === 'string' ? tool.detail : JSON.stringify(tool.detail ?? {
                  matched_tool_names: tool.matched_tool_names,
                  newly_discovered_tool_names: tool.newly_discovered_tool_names,
                  summary: tool.summary,
                }, null, 2)}
              </pre>
            </TooltipContent>
          </Tooltip>
        )))}
      </div>
    </TooltipProvider>
  )
}

function PlannerToolResultCard({
  tool,
  index,
  hideHeader = false,
  waitUntil,
  onOpenReasoning,
  messages,
  onJumpToMessage,
}: ReplyToolMessageProps & {
  tool: DisplayTool
  index: number
  /** 单个工具时名称、耗时与推理入口已合并到卡片标题行 */
  hideHeader?: boolean
  waitUntil?: number
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
  const promptHtmlUri = tool.prompt_html_uri?.trim() ?? ''
  const canOpenReasoning = Boolean(promptHtmlUri && parsePromptHtmlReasoningTarget(promptHtmlUri))

  const isToolSearch = tool.tool_name === 'tool_search'
  if (isWaitTool(tool.tool_name)) {
    if (hideHeader) return <ToolResultImages images={tool.images} />
    return (
      <div
        data-maisaka-active-tool={tool.tool_call_id}
        className="space-y-1.5"
      >
        <div data-maisaka-trace-meta="true" className="flex flex-wrap items-center gap-1.5">
          <span className="text-foreground text-sm font-medium">
            <WaitToolLabel key={waitUntil} args={tool.tool_args} waitUntil={waitUntil} pending={Boolean(tool.status)} />
          </span>
          <EventTimestamp startedAt={tool.started_at} />
          <span className="text-muted-foreground ml-auto text-[10px]">#{index + 1}</span>
          {canOpenReasoning && (
            <Button
              variant="outline"
              size="icon-xs"

              onClick={() => onOpenReasoning(promptHtmlUri)}
              aria-label="查看推理"
              data-maisaka-reasoning-button="true"
              title="查看这个工具对应的推理"
            >
              <Share className="h-3.5 w-3.5" />
            </Button>
          )}
        </div>
        <ToolResultImages images={tool.images} />
      </div>
    )
  }

  return (
    <div
      data-maisaka-active-tool={!hideHeader && tool.status === 'running' ? tool.tool_call_id : undefined}
      className="space-y-1.5"
    >
      {tool.session_name && (
        <div className="text-muted-foreground text-xs" title={tool.session_id}>
          目标聊天：{tool.session_name}
        </div>
      )}
      {!hideHeader && (
        <div
          data-maisaka-trace-meta="true"
          className="flex flex-wrap items-center gap-1.5"
        >
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
          {tool.duration_ms > 0 && (
            <span className="text-muted-foreground text-xs font-normal opacity-65">
              {formatMs(tool.duration_ms)}
            </span>
          )}
          <span className="text-muted-foreground ml-auto text-[10px]">#{index + 1}</span>
          {canOpenReasoning && (
            <Button
              variant="outline"
              size="icon-xs"

              onClick={() => onOpenReasoning(promptHtmlUri)}
              aria-label="查看推理"
              data-maisaka-reasoning-button="true"
              title="查看这个工具对应的推理"
            >
              <Share className="h-3.5 w-3.5" />
            </Button>
          )}
        </div>
      )}

      <div className="space-y-1.5">
        {tool.tool_name === 'subagent' && (
          <button
            type="button"
            className="text-primary flex items-center gap-1 text-[11px] hover:underline"
            onClick={() => openSubagentPanel(tool.tool_call_id)}
            title="在右侧栏查看这个子代理的运行过程"
          >
            <Bot className="h-3 w-3" />
            查看子代理运行过程
          </button>
        )}
        {tool.tool_name === 'reply' && (
          <ReplyToolArguments tool={tool} messages={messages} onJumpToMessage={onJumpToMessage} />
        )}
        {isToolSearch && !tool.status && tool.success && <ToolDiscoveryBlock tools={[tool]} />}
        {((!isToolSearch && tool.tool_name !== 'reply' && argumentEntries.length > 0) || (!tool.status && !tool.success && tool.error_message)) && (
          <div className="flex flex-wrap items-center gap-1.5">
            {!isToolSearch && tool.tool_name !== 'reply' && argumentEntries.map(([name, value]) => (
              <ToolArgumentBlock key={name} name={name} value={value} />
            ))}
            {!tool.status && <ToolFullJsonBlock tool={tool} />}
          </div>
        )}

        {!tool.status && (!isToolSearch || !tool.success) && (
            <div className="flex flex-wrap items-baseline gap-x-1.5 font-normal opacity-65">
              <span className="text-muted-foreground shrink-0 text-[10px] leading-4">
                执行结果
              </span>
              <p className="text-muted-foreground min-w-0 flex-1 text-xs leading-4 break-words whitespace-pre-wrap">
                {tool.summary || '未返回结果摘要。'}
              </p>
            </div>
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
  waitUntil,
}: ReplyToolMessageProps & {
  data: PlannerFinalizedEvent
  isProgress: boolean
  waitUntil?: number
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
    <TraceStep
      data-maisaka-trace-card="true"
      data-maisaka-active-tool={singleTool && (singleTool.status === 'running' || (singleToolIsWait && data.final_state.agent_state === 'wait')) ? singleTool.tool_call_id : undefined}
      tone="tool"
    >
      <div className="space-y-2">
        <div
          data-maisaka-trace-meta="true"
          className="flex flex-wrap items-center gap-2"
        >
          {!singleToolIsWait && <ToolIcon className="h-4 w-4 text-teal-500" />}
          <CardTitle
            className={cn('text-sm font-semibold', !singleToolIsWait && 'font-mono')}
          >
            {singleToolIsWait
              ? <WaitToolLabel key={waitUntil} args={singleTool?.tool_args} waitUntil={waitUntil} pending={Boolean(singleTool?.status)} />
              : regularTools.map((tool) => tool.tool_name || 'unknown').join('、')}
          </CardTitle>
          <ModelNameBadge modelName={singleTool?.model_name} />
          {singleTool && !singleTool.status && (
            <EventTimestamp
              startedAt={singleTool.started_at}
              endedAt={singleToolIsWait ? undefined : singleTool.ended_at}
            />
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
            <span className="text-muted-foreground text-xs font-normal opacity-65">
              {formatMs(singleTool.duration_ms)}
            </span>
          )}
          {!singleTool && (
            <Badge
              variant="secondary"
              className="ml-auto text-[10px]"
            >
              {regularTools.length} 个
            </Badge>
          )}
          {canOpenSingleToolReasoning && (
            <Button
              variant="outline"
              size="icon-xs"
              className="ml-auto"
              onClick={() => onOpenReasoning(singleToolPromptHtmlUri)}
              aria-label="查看推理"
              data-maisaka-reasoning-button="true"
              title="查看这个工具对应的推理"
            >
              <Share className="h-3.5 w-3.5" />
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
                hideHeader={Boolean(singleTool)}
                waitUntil={isWaitTool(tool.tool_name) ? waitUntil : undefined}
                onOpenReasoning={onOpenReasoning}
              />
            </div>
          ))}
        </div>
        )}
      </div>
    </TraceStep>
  )
}

function PlannerNativeToolCallsBlock({ data }: { data: PlannerFinalizedEvent }) {
  const nativeToolCalls = data.planner?.native_tool_calls ?? []
  if (nativeToolCalls.length <= 0) {
    return null
  }

  return (
    <TraceStep data-maisaka-trace-card="true" tone="tool">
      <div className="space-y-2">
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
      </div>
    </TraceStep>
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
    <TraceStep data-maisaka-trace-card="true" tone="tool">
      <div className="space-y-2">
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
      </div>
    </TraceStep>
  )
}

// 常规流程提示不占时间线行；实际消息、思考轮次和上下文裁切已有独立展示。
const HIDDEN_FLOW_STEPS = new Set([
  'planner.requested', 'wait.completed', 'cycle.finishing', 'messages.merged', 'cycle.started',
])

/** 普通流程节点用横线和右侧文字分隔，异常与轮次结果保留完整说明。 */
function FlowStepRow({
  title,
  detail,
  timestamp,
  warning = false,
  compact = false,
  twoLine = false,
}: {
  title: string
  detail: string
  timestamp: number
  warning?: boolean
  compact?: boolean
  twoLine?: boolean
}) {
  if (compact) {
    return (
      <div className="text-muted-foreground flex items-center gap-3 py-1 text-[11px]">
        <span aria-hidden="true" className="border-border/60 min-w-0 flex-1 border-t" />
        <span className="shrink-0" title={detail}>{title}</span>
      </div>
    )
  }
  return (
    <div
      className={cn(
        'rounded-md border px-3 py-2',
        warning ? 'border-amber-500/40 bg-amber-500/5' : 'bg-muted/30'
      )}
    >
      <div className={cn('flex items-center gap-2 text-xs', !twoLine && 'flex-wrap')}>
        {warning ? (
          <AlertCircle className="h-3.5 w-3.5 text-amber-500" />
        ) : (
          <Activity className="text-muted-foreground h-3.5 w-3.5" />
        )}
        <span className={cn('font-medium', twoLine && 'min-w-0 truncate')} title={twoLine ? title : undefined}>{title}</span>
        {twoLine ? (
          <span className="text-muted-foreground shrink-0 opacity-65">{formatTimestamp(timestamp)}</span>
        ) : <EventTimestamp timestamp={timestamp} />}
      </div>
      {detail && (
        <p
          className={cn('text-muted-foreground mt-1 text-xs leading-5', twoLine ? 'truncate' : 'break-words whitespace-pre-wrap')}
          title={twoLine ? detail : undefined}
        >
          {detail}
        </p>
      )}
    </div>
  )
}

// ─── 时间线入口渲染器 ──────────────────────────────────────────

function ContextTrimCard({ data, open, onOpenChange }: {
  data: FlowStepEvent
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const trim = data.trim
  const labels = {
    pending: '准备学习', running: '正在学习', waiting: '等待学习', completed: '已完成',
    no_results: '无成果', skipped: '已跳过', cancelled: '已取消', disabled: '未启用', failed: '失败',
  }
  const statusVariant = (status: keyof typeof labels) =>
    status === 'completed' ? 'success' : status === 'failed' ? 'destructive' : 'outline'
  return (
    <TraceRound
      label="上下文裁切"
      time={formatTimestamp(data.timestamp)}
      summary={trim && <span>裁切 <strong className="tabular-nums">{trim.removed_count}</strong> 条 · 保留 <strong className="tabular-nums">{trim.remaining_count}</strong> 条</span>}
      metrics={trim && <Badge variant={statusVariant(trim.learning.status)}>{labels[trim.learning.status]}</Badge>}
      open={open}
      onOpenChange={onOpenChange}
    >
      {!trim && <p className="text-muted-foreground text-xs leading-relaxed">{data.detail}</p>}
      {trim?.learning.detail && <p className="text-muted-foreground mb-3 text-xs leading-relaxed">{trim.learning.detail}</p>}
      {trim?.learning.learners.map(learner => (
        <TraceStep
          key={learner.name}
          tone={learner.status === 'completed' ? 'success' : learner.status === 'failed' ? 'error' : learner.status === 'running' ? 'tool' : 'neutral'}
        >
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <span className="font-medium">{learner.name}</span>
            <Badge variant={statusVariant(learner.status)}>{labels[learner.status]}</Badge>
            <span className="text-muted-foreground">{learner.detail}</span>
          </div>
          {learner.contents.length > 0 && (
            <div className="mt-2 text-xs leading-relaxed">
              <ul className="list-disc space-y-1 pl-4 marker:text-muted-foreground">
                {learner.contents.slice(0, 3).map((content, index) => <li key={index} className="break-words">{content}</li>)}
              </ul>
              {learner.contents.length > 3 && (
                <details className="mt-2">
                  <summary className="text-muted-foreground hover:text-foreground cursor-pointer" data-maisaka-expand>
                    其余 {learner.contents.length - 3} 条
                  </summary>
                  <ul className="mt-2 max-h-48 list-disc space-y-1 overflow-auto pl-4 marker:text-muted-foreground">
                    {learner.contents.slice(3).map((content, index) => <li key={index} className="break-words">{content}</li>)}
                  </ul>
                </details>
              )}
            </div>
          )}
        </TraceStep>
      ))}
    </TraceRound>
  )
}

function CollapsedPlannerToolSummary({ data, isProgress }: {
  data: PlannerFinalizedEvent
  isProgress: boolean
}) {
  const results = new Map((data.tools ?? []).map((tool) => [tool.tool_call_id, tool]))
  const calls = new Map((data.planner?.tool_calls ?? []).map((tool) => [tool.id, tool.name]))
  for (const tool of results.values()) calls.set(tool.tool_call_id, tool.tool_name)

  if (calls.size === 0) return <>{isProgress ? '进行中' : data.final_state.end_detail}</>

  return (
    <span className="inline-flex max-w-full items-center gap-3 align-middle">
      {Array.from(calls, ([id, name]) => {
        const result = results.get(id)
        const running = isProgress && data.active_tool_call_id === id && !result
        const status = result ? result.success ? '执行成功' : '执行失败' : running ? '执行中' : isProgress ? '等待执行' : '无执行结果'
        return (
          <span key={id} className="inline-flex min-w-0 items-center gap-1.5" title={`${name}：${status}`}>
            <span
              role="img"
              aria-label={status}
              className={cn(
                'h-2 w-2 shrink-0 rounded-full',
                result ? result.success ? 'bg-emerald-500' : 'bg-red-500' : running ? 'animate-pulse bg-amber-500' : 'bg-muted-foreground/40'
              )}
            />
            <span className="truncate font-mono">{name}</span>
          </span>
        )
      })}
    </span>
  )
}

export function TimelineEventRenderer({
  entry,
  onJumpToMessage,
  onOpenReasoning,
  messages,
  waitUntil,
  roundOpen,
  onRoundOpenChange,
}: {
  roundOpen: boolean
  onRoundOpenChange: (open: boolean) => void
  entry: TimelineEntry
  waitUntil?: number
  messages?: Map<string, MonitorMessage>
  onJumpToMessage: (messageId: string) => void
  onOpenReasoning: (promptHtmlUri: string) => void
}) {
  switch (entry.type) {
    case 'flow.step': {
      const data = entry.data as FlowStepEvent
      if (HIDDEN_FLOW_STEPS.has(data.step)) return null
      if (data.step === 'context.trimmed') return <ContextTrimCard data={data} open={roundOpen} onOpenChange={onRoundOpenChange} />
      return <FlowStepRow compact title={data.title} detail={data.detail} timestamp={data.timestamp} />
    }
    case 'llm.retry': {
      const data = entry.data as LlmRetryEvent
      return (
        <FlowStepRow
          warning
          twoLine
          title={`模型请求重试 ${data.attempt}/${data.max_attempts} · ${data.model_name} · ${data.task_name}`}
          detail={`${data.retry_interval} 秒后重试 · ${data.reason.replace(/\s+/g, ' ')}`}
          timestamp={data.timestamp}
        />
      )
    }
    case 'llm.error': {
      const data = entry.data as LlmErrorEvent
      return (
        <FlowStepRow
          warning
          twoLine
          title={`模型请求失败 · ${data.model_name} · ${data.task_name}`}
          detail={data.message.replace(/\s+/g, ' ')}
          timestamp={data.timestamp}
        />
      )
    }
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
    case 'planner.progress': {
      const data = entry.data as PlannerFinalizedEvent
      const endDetail = data.final_state.end_detail
      if (isPlannerInterrupted(data)) {
        return <PlannerInterruptedCard data={data} />
      }
      if (data.timing_gate?.result?.action === 'no_action') {
        return null
      }
      return (
        <TraceRound
          label={`第 ${data.cycle_id} 轮`}
          time={<EventTimestamp startedAt={data.planner_started_at} endedAt={data.planner_ended_at} />}
          summary={roundOpen
            ? entry.type === 'planner.progress' ? '进行中' : undefined
            : <CollapsedPlannerToolSummary data={data} isProgress={entry.type === 'planner.progress'} />}
          metrics={data.planner && (
            <span title={`Planner 输入 ${data.planner.prompt_tokens} + 输出 ${data.planner.completion_tokens} tokens`}>
              {formatMs(data.planner.duration_ms)} · {data.planner.prompt_tokens + data.planner.completion_tokens} tok
            </span>
          )}
          active={entry.type === 'planner.progress'}
          open={roundOpen}
          onOpenChange={onRoundOpenChange}
        >
          <PlannerFinalizedCard data={data} onOpenReasoning={onOpenReasoning} />
          <PlannerNativeToolCallsBlock data={data} />
          <PlannerToolCallsBlock
            messages={messages}
            onJumpToMessage={onJumpToMessage}
            data={data}
            isProgress={entry.type === 'planner.progress'}
            waitUntil={waitUntil}
            onOpenReasoning={onOpenReasoning}
          />
          {entry.type === 'planner.finalized' && data.final_state.end_reason !== 'tool_pause:wait' && endDetail && (
            <TraceStep tone={data.final_state.end_reason === 'cycle_error' ? 'error' : 'neutral'}>
              <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
                <span className="shrink-0">轮次结果</span>
                <span className="text-xs text-muted-foreground">{formatTimestamp(entry.timestamp)}</span>
                <p className="text-muted-foreground">{endDetail}</p>
              </div>
            </TraceStep>
          )}
        </TraceRound>
      )
    }
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
  sessionName?: string
  /** 嵌入聊天工作区时隐藏重复的会话侧边栏，并使用父容器提供的完整高度。 */
  embedded?: boolean
  /** 打开推理详情后返回的地址；未提供时返回当前页面。 */
  reasoningReturnTo?: string
  /**
   * 手机端悬浮顶栏里的工具栏位置：提供时工具栏渲染到该元素中，
   * 时间线铺满到顶部并在内容顶部留出 `--observe-header-h` 的空间，内容从顶栏下方滚过。
   */
  floatingToolbarContainer?: HTMLElement | null
}

/** 虚拟列表会卸载屏外工具，重新挂载时也需要重新观察其可见性。 */
function useActiveToolVisible(viewport: HTMLDivElement | null, toolCallId?: string) {
  const [visibility, setVisibility] = useState<{ id?: string; visible: boolean }>({ visible: false })
  useLayoutEffect(() => {
    if (!viewport || !toolCallId) return
    let target: Element | null | undefined
    const update = (visible: boolean) =>
      setVisibility((current) =>
        current.id === toolCallId && current.visible === visible ? current : { id: toolCallId, visible }
      )
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.target === target) update(entry.isIntersecting)
      },
      { root: viewport }
    )
    const observeTarget = () => {
      const nextTarget = Array.from(viewport.querySelectorAll('[data-maisaka-active-tool]'))
        .find((element) => element.getAttribute('data-maisaka-active-tool') === toolCallId) ?? null
      if (nextTarget === target) return
      observer.disconnect()
      target = nextTarget
      if (target) {
        const bounds = target.getBoundingClientRect()
        const view = viewport.getBoundingClientRect()
        update(bounds.bottom > view.top && bounds.top < view.bottom)
        observer.observe(target)
      } else {
        update(false)
      }
    }
    observeTarget()
    const mutations = new MutationObserver(observeTarget)
    mutations.observe(viewport, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ['data-maisaka-active-tool'],
    })
    return () => {
      observer.disconnect()
      mutations.disconnect()
    }
  }, [viewport, toolCallId])
  return visibility.id === toolCallId && visibility.visible
}

/** 距时间线顶部多近时开始拉取更早的一页 */
const HISTORY_LOAD_THRESHOLD_PX = 200

const MonitorTimeline = memo(function MonitorTimeline({
  sessionName,
  embedded = false,
  reasoningReturnTo,
  floatingToolbarContainer,
}: MaisakaMonitorProps) {
  const navigate = useNavigate()
  const { toast } = useToast()
  const {
    timeline,
    selectedSession,
    selectedStageStatus,
    clearTimeline,
    historyLoading,
    hasMoreHistory,
    loadOlderHistory,
  } = useMaisakaMonitorSession()

  // 展开状态放在虚拟列表外，屏外轮次重新挂载时仍保留用户选择。
  const [roundExpansion, setRoundExpansion] = useState<{ all: boolean; entries: Record<string, boolean> }>({ all: true, entries: {} })
  const allRoundsExpanded = timeline.filter(entry => entry.type === 'planner.finalized' || entry.type === 'planner.progress' || (entry.type === 'flow.step' && (entry.data as FlowStepEvent).step === 'context.trimmed')).every(entry => roundExpansion.entries[entry.id] ?? roundExpansion.all)
  const scrollRef = useRef<HTMLDivElement>(null)
  const [scrollViewport, setScrollViewport] = useState<HTMLDivElement | null>(null)
  const [timelineElement, setTimelineElement] = useState<HTMLDivElement | null>(null)
  const [timelineScrollMargin, setTimelineScrollMargin] = useState(0)
  const [autoScroll, setAutoScroll] = useState(true)
  const userScrollingRef = useRef(false)
  // 手动接管后，丢弃旧 scrollToIndex 的跨帧重试，直到下一次明确点击导航。
  const allowVirtualScrollRef = useRef(true)
  const [scrollIdleRevision, setScrollIdleRevision] = useState(0)
  const [focusedMessageId, setFocusedMessageId] = useState<string | null>(null)
  const focusTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const messageNavigationRef = useRef<string | null>(null)
  /** 程序化跳转滚动期间忽略滚动监听，防止自动跟随把视图拉回底部 */
  const revealScrollLockRef = useRef(false)
  const previousSelectedSessionRef = useRef<string | null | undefined>(undefined)

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

  useEffect(() => {
    if (!scrollViewport) return
    let touching = false
    let draggingScrollbar = false
    const scrollRoot = scrollRef.current
    let idleTimer: ReturnType<typeof setTimeout> | null = null
    const finishScroll = () => {
      if (touching || draggingScrollbar || !userScrollingRef.current) return
      userScrollingRef.current = false
      // 松手不等于滚动结束；惯性完全停止后才恢复底部跟随和触顶加载。
      const distance = scrollViewport.scrollHeight - scrollViewport.scrollTop - scrollViewport.clientHeight
      setAutoScroll(distance <= 2)
      setScrollIdleRevision((revision) => revision + 1)
    }
    const scheduleIdle = () => {
      if (idleTimer !== null) clearTimeout(idleTimer)
      idleTimer = setTimeout(finishScroll, 180)
    }
    const beginScroll = () => {
      messageNavigationRef.current = null
      userScrollingRef.current = true
      allowVirtualScrollRef.current = false
      revealScrollLockRef.current = false
      setAutoScroll(false)
      scheduleIdle()
    }
    const handleTouchStart = () => {
      touching = true
      beginScroll()
    }
    const handleTouchEnd = () => {
      touching = false
      scheduleIdle()
    }
    const handleNativeScroll = () => {
      if (userScrollingRef.current) scheduleIdle()
    }
    const handlePointerDown = (event: PointerEvent) => {
      if (!(event.target instanceof Element) || !event.target.closest('[data-dashboard-scrollbar]')) return
      draggingScrollbar = true
      beginScroll()
    }
    const handlePointerUp = () => {
      if (!draggingScrollbar) return
      draggingScrollbar = false
      scheduleIdle()
    }
    const handleKeyDown = (event: KeyboardEvent) => {
      // 输入框、工具按钮的键盘操作不属于时间线滚动。
      if (event.target !== scrollViewport || event.ctrlKey || event.metaKey || event.altKey) return
      if (['ArrowUp', 'ArrowDown', 'PageUp', 'PageDown', 'Home', 'End', ' '].includes(event.key)) beginScroll()
    }
    scrollRoot?.addEventListener('pointerdown', handlePointerDown, true)
    document.addEventListener('pointerup', handlePointerUp)
    document.addEventListener('pointercancel', handlePointerUp)
    scrollViewport.addEventListener('keydown', handleKeyDown)
    scrollViewport.addEventListener('touchstart', handleTouchStart, { passive: true })
    scrollViewport.addEventListener('touchend', handleTouchEnd, { passive: true })
    scrollViewport.addEventListener('touchcancel', handleTouchEnd, { passive: true })
    scrollViewport.addEventListener('wheel', beginScroll, { passive: true })
    scrollViewport.addEventListener('scroll', handleNativeScroll, { passive: true })
    // 离散滚轮可能连续发出 scrollend，仍需等最后一次输入/滚动停稳。
    scrollViewport.addEventListener('scrollend', scheduleIdle)
    return () => {
      if (idleTimer !== null) clearTimeout(idleTimer)
      userScrollingRef.current = false
      scrollViewport.removeEventListener('touchstart', handleTouchStart)
      scrollViewport.removeEventListener('touchend', handleTouchEnd)
      scrollViewport.removeEventListener('touchcancel', handleTouchEnd)
      scrollViewport.removeEventListener('wheel', beginScroll)
      scrollViewport.removeEventListener('scroll', handleNativeScroll)
      scrollViewport.removeEventListener('scrollend', scheduleIdle)
      scrollViewport.removeEventListener('keydown', handleKeyDown)
      scrollRoot?.removeEventListener('pointerdown', handlePointerDown, true)
      document.removeEventListener('pointerup', handlePointerUp)
      document.removeEventListener('pointercancel', handlePointerUp)
    }
  }, [scrollViewport, selectedSession])

  const visibleTimelineEntries = useMemo(() => {
    const noReplyTimingGateCycles = new Set<string>()
    const visibleEntries: TimelineEntry[] = []

    for (const entry of timeline) {
      // 先排除常规提示行，避免虚拟列表为不显示的分隔提示保留空白高度。
      if (entry.type === 'flow.step') {
        const data = entry.data as FlowStepEvent
        if (HIDDEN_FLOW_STEPS.has(data.step)) {
          continue
        }
      }
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
        entry.type === 'flow.step' ||
        entry.type === 'llm.retry' ||
        entry.type === 'llm.error' ||
        entry.type === 'tool.execution' ||
        entry.type === 'replier.response'
      ) {
        visibleEntries.push(entry)
      }
    }

    return visibleEntries
  }, [timeline])

  // 当前时间线里可放大的图片，按出现顺序排列，供大图预览切换上一张/下一张。
  const galleryImages = useMemo(() => {
    const images = new Map<string, GalleryImage>()
    for (const entry of visibleTimelineEntries) {
      if (!isMessageTimelineEntry(entry)) continue
      for (const media of (entry.data as MonitorMessage).media ?? []) {
        const image = toGalleryImage(media)
        if (image && !images.has(image.src)) images.set(image.src, image)
      }
    }
    return Array.from(images.values())
  }, [visibleTimelineEntries])

  const latestPlannerEntry = useMemo(() => {
    for (let index = timeline.length - 1; index >= 0; index -= 1) {
      const entry = timeline[index]
      if (entry.type === 'planner.progress' || entry.type === 'planner.finalized') return entry
    }
  }, [timeline])
  const activeWaitUntil =
    selectedStageStatus?.agentState === 'wait' ? selectedStageStatus.waitUntil : undefined
  const latestPlannerData = latestPlannerEntry?.data as PlannerFinalizedEvent | undefined
  // 工具是否在执行由当前调用 ID 决定，不依赖工具名或阶段文案（Replyer 会覆盖阶段名称）。
  const activeToolCallId = selectedStageStatus?.agentState === 'wait'
    ? latestPlannerData?.tools.find((tool) => isWaitTool(tool.tool_name))?.tool_call_id
    : latestPlannerEntry?.type === 'planner.progress' ? latestPlannerData?.active_tool_call_id : undefined
  const executingTool = Boolean(activeToolCallId)
  const activeToolArgs = latestPlannerData?.planner?.tool_calls.find((tool) => tool.id === activeToolCallId)?.arguments
    ?? latestPlannerData?.tools.find((tool) => tool.tool_call_id === activeToolCallId)?.tool_args
    ?? {}
  const activeToolVisible = useActiveToolVisible(scrollViewport, activeToolCallId)
  const reducedMotion = useReducedMotion()

  useLayoutEffect(() => {
    if (!scrollViewport || !timelineElement) return
    // 条目前还有内边距和历史提示，虚拟坐标必须包含这段真实偏移。
    const measureMargin = () => setTimelineScrollMargin(
      timelineElement.getBoundingClientRect().top - scrollViewport.getBoundingClientRect().top + scrollViewport.scrollTop
    )
    measureMargin()
    const observer = new ResizeObserver(measureMargin)
    observer.observe(scrollViewport)
    observer.observe(timelineElement.parentElement!)
    return () => observer.disconnect()
  }, [scrollViewport, timelineElement])

  const getTimelineItemKey = useCallback(
    (index: number) => visibleTimelineEntries[index]?.id ?? index,
    [visibleTimelineEntries]
  )

  // TanStack Virtual 与 React Compiler 不兼容，保持现有虚拟列表实现
  // eslint-disable-next-line react-hooks/incompatible-library
  const timelineVirtualizer = useVirtualizer({
    count: visibleTimelineEntries.length,
    getScrollElement: () => scrollViewport,
    estimateSize: () => 140,
    getItemKey: getTimelineItemKey,
    overscan: 8,
    scrollMargin: timelineScrollMargin,
    scrollToFn: (offset, options, instance) => {
      // 这里只处理明确导航；手动接管后丢弃旧 scrollToIndex 的跨帧重试。
      if (!allowVirtualScrollRef.current || userScrollingRef.current) return
      elementScroll(offset, options, instance)
    },
  })
  useVirtualizerUnmountGuard(timelineVirtualizer)

  // 库负责测量，不再按缓存 scrollOffset 提前写滚动位置。
  // 行高与结构变化全部交给 TimelineScrollPosition 在 DOM 提交后一次性保位。
  timelineVirtualizer.shouldAdjustScrollPositionOnItemSizeChange = () => false

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
      userScrollingRef.current = false
      allowVirtualScrollRef.current = true
      messageNavigationRef.current = messageId
      // 动态行高无法可靠平滑定位；即时跳转让虚拟列表测量并校正实际目标，期间禁止锚点补偿抢回位置。
      revealScrollLockRef.current = true
      setFocusedMessageId(messageId)
      timelineVirtualizer.scrollToIndex(targetIndex, {
        align: 'center',
        behavior: 'auto',
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
    [scrollViewport, timelineVirtualizer]
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
    // 按当前滚动偏移查找首行，避免使用过期的可见范围或视口上方的预渲染行。
    const firstVisibleIndex =
      timelineVirtualizer.getVirtualItemForOffset(viewportTop)?.index ?? 0
    // 连续点击从上次命中的消息继续，不因居中显示而跳过它上方仍在视口内的消息。
    const previousTargetIndex = messageNavigationRef.current
      ? messageEntryIndexes.get(messageNavigationRef.current)
      : undefined

    for (let index = (previousTargetIndex ?? firstVisibleIndex) - 1; index >= 0; index -= 1) {
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
  }, [messageEntryIndexes, revealTimelineMessage, scrollViewport, timelineVirtualizer, toast, visibleTimelineEntries])

  const scrollToBottom = useCallback(
    () => {
      messageNavigationRef.current = null
      userScrollingRef.current = false
      allowVirtualScrollRef.current = true
      revealScrollLockRef.current = false
      if (visibleTimelineEntries.length > 0) {
        timelineVirtualizer.scrollToIndex(visibleTimelineEntries.length - 1, {
          align: 'end',
          behavior: 'smooth',
        })
      } else {
        scrollViewport?.scrollTo({
          top: scrollViewport.scrollHeight,
          behavior: 'smooth',
        })
      }
      setAutoScroll(true)
    },
    [scrollViewport, timelineVirtualizer, visibleTimelineEntries.length]
  )

  const scrollToTop = useCallback(() => {
    messageNavigationRef.current = null
    userScrollingRef.current = false
    allowVirtualScrollRef.current = true
    setAutoScroll(false)
    revealScrollLockRef.current = true
    if (visibleTimelineEntries.length > 0) {
      timelineVirtualizer.scrollToIndex(0, { align: 'start', behavior: 'smooth' })
    } else {
      scrollViewport?.scrollTo({ top: 0, behavior: 'smooth' })
    }
  }, [scrollViewport, timelineVirtualizer, visibleTimelineEntries.length])

  const timelineTotalSize = timelineVirtualizer.getTotalSize()

  // 切换聊天流重置滚动模式；实际底部跟随统一由提交后的保位组件完成。
  useLayoutEffect(() => {
    if (previousSelectedSessionRef.current !== selectedSession) {
      previousSelectedSessionRef.current = selectedSession
      messageNavigationRef.current = null
      userScrollingRef.current = false
      allowVirtualScrollRef.current = true
      revealScrollLockRef.current = false
      setAutoScroll(true)
    }
  }, [selectedSession])

  const handleScroll = useCallback(
    (e: React.UIEvent<HTMLDivElement>) => {
      const target =
        scrollViewport ?? e.currentTarget.querySelector('[data-radix-scroll-area-viewport]')
      if (!target || e.target !== target) return
      const { scrollTop, scrollHeight, clientHeight } = target as HTMLElement
      const distanceToBottom = scrollHeight - scrollTop - clientHeight

      // 消息定位期间由虚拟列表独占滚动，不能提前恢复锚点补偿或插入历史。
      if (revealScrollLockRef.current && messageNavigationRef.current !== null) return

      // 程序化跳转期间：平滑滚动的起始帧仍在底部阈值内，
      // 此时既不能重新开启自动跟随（会打断滚动并拉回底部），也不需要关闭
      if (revealScrollLockRef.current) {
        if (distanceToBottom < 80) return
        // 已经离开底部阈值，恢复正常判定
        revealScrollLockRef.current = false
      }

      // 拖动及惯性阶段不因进入底部附近就抢回滚动控制权。
      if (!userScrollingRef.current) setAutoScroll(distanceToBottom <= 2)

      // 滚到顶部附近时拉取更早的一页；插入后由下方的锚点补偿保持阅读位置。
      if (!userScrollingRef.current && scrollTop < HISTORY_LOAD_THRESHOLD_PX && hasMoreHistory && !historyLoading) {
        loadOlderHistory()
      }
    },
    [hasMoreHistory, historyLoading, loadOlderHistory, scrollViewport]
  )

  // 撑不满视口时继续加载；用户滑到顶部则等惯性结束再插入历史，避免中途改变滚动范围。
  useEffect(() => {
    if (!scrollViewport || !hasMoreHistory || historyLoading || userScrollingRef.current || revealScrollLockRef.current) return
    if (
      scrollViewport.scrollHeight <= scrollViewport.clientHeight + HISTORY_LOAD_THRESHOLD_PX ||
      (scrollIdleRevision > 0 && scrollViewport.scrollTop < HISTORY_LOAD_THRESHOLD_PX)
    ) {
      loadOlderHistory()
    }
  }, [scrollViewport, hasMoreHistory, historyLoading, loadOlderHistory, timelineTotalSize, scrollIdleRevision])

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

  const stageStatusPanel = (
    <StageStatusPanel sessionName={sessionName} allExpanded={allRoundsExpanded} onToggleAll={() => { setAutoScroll(false); setRoundExpansion({ all: !allRoundsExpanded, entries: {} }) }}
      autoScroll={autoScroll}
      sessionId={selectedSession}
      onClearTimeline={clearTimeline}
      onFindPreviousBotMessage={handleFindPreviousBotMessage}
      onScrollToTop={scrollToTop}
      onScrollToBottom={scrollToBottom}
      stats={stats}
      status={selectedStageStatus}
      hideStatus={executingTool}
    />
  )

  // 主时间线区域
  return (
    <ImageGalleryProvider images={galleryImages}>
    <div className="flex min-h-0 min-w-0 flex-1 flex-col">
      {/* 时间线；手机端工具栏放进悬浮顶栏 */}
      {floatingToolbarContainer
        ? createPortal(stageStatusPanel, floatingToolbarContainer)
        : stageStatusPanel}

      <DataPanel padding="none" data-conversation-screen="true"
        data-maisaka-timeline="true"
        data-maisaka-floating-header={floatingToolbarContainer ? 'true' : undefined}
        className={cn(
          'relative min-w-0 flex-1 overflow-hidden',
          embedded ? 'min-h-0' : 'min-h-[420px] lg:min-h-0',
          floatingToolbarContainer && 'rounded-none border-0 shadow-none'
        )}
      >
        <ScrollArea
          className="h-full"
          scrollbars="vertical"
          viewportClassName="!overflow-x-hidden [overflow-anchor:none]"
          ref={scrollRef}
          onScrollCapture={handleScroll}
          onClickCapture={(event) => {
            if (!(event.target instanceof Element) || !event.target.closest('[data-maisaka-expand], [data-trace-round-trigger]') || !scrollViewport) return
            // 用户主动展开内容后停止跟随底部，避免新消息把展开入口推离视野。
            setAutoScroll(false)
          }}
        >
          <TimelineScrollPosition
            viewport={scrollViewport}
            sessionId={selectedSession}
            positions={timelineVirtualizer.measurementsCache}
            getMode={() => revealScrollLockRef.current ? 'navigate' : autoScroll && !userScrollingRef.current ? 'follow' : 'read'}
          >
          <div
            data-conversation-messages="true"
            className="min-w-0"
            style={floatingToolbarContainer ? { paddingTop: 'calc(var(--observe-header-h, 0px) + 0.5rem)' } : undefined}
          >
            {visibleTimelineEntries.length > 0 && (
              // 固定高度：文案切换不改变下方条目的位置，避免加载前后视口跳动。
              <p className="text-muted-foreground h-7 text-center text-xs">
                {historyLoading
                  ? '正在加载更早的记录…'
                  : hasMoreHistory
                    ? '上滑加载更早的记录'
                    : '没有更早的记录了'}
              </p>
            )}
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
                ref={setTimelineElement}
                className="relative min-w-0"
                style={{ height: `${timelineTotalSize}px` }}
              >
                {virtualItems.map((virtualItem) => {
                  const entry = visibleTimelineEntries[virtualItem.index]
                  if (!entry) return null
                  const entryData = entry.data as unknown as Record<string, unknown>
                  const entryMessageId =
                    typeof entryData.message_id === 'string' ? entryData.message_id : undefined
                  return (
                    <div
                      key={virtualItem.key}
                      ref={timelineVirtualizer.measureElement}
                      data-index={virtualItem.index}
                      className="absolute top-0 right-0 left-0 pb-3"
                      style={{ transform: `translateY(${virtualItem.start - timelineScrollMargin}px)` }}
                    >
                      <div
                        data-maisaka-message-id={entryMessageId}
                        data-jump-highlighted={
                          entryMessageId && focusedMessageId === entryMessageId
                            ? 'true'
                            : undefined
                        }
                        className={cn(
                          'rounded-md',
                          entryMessageId &&
                            focusedMessageId === entryMessageId &&
                            'bg-primary/5 ring-primary/55 ring-offset-background ring-2 ring-offset-2'
                        )}
                      >
                        <TimelineEventRenderer
                          messages={monitorMessages}
                          entry={entry}
                          roundOpen={roundExpansion.entries[entry.id] ?? roundExpansion.all}
                          onRoundOpenChange={open => setRoundExpansion(current => ({ ...current, entries: { ...current.entries, [entry.id]: open } }))}
                          waitUntil={entry.id === latestPlannerEntry?.id ? activeWaitUntil : undefined}
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
          </TimelineScrollPosition>
        </ScrollArea>
        <AnimatePresence>
          {executingTool && !activeToolVisible && selectedStageStatus && (
            <motion.div
              key="current-tool-status"
              initial={{ opacity: 0, y: reducedMotion ? 0 : 16 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: reducedMotion ? 0 : 12 }}
              transition={{ duration: reducedMotion ? 0 : 0.22, ease: [0.22, 1, 0.36, 1] }}
              className="pointer-events-none absolute right-4 bottom-3 left-4 z-20 flex justify-center"
            >
              <FloatingToolStatus key={activeToolCallId} status={selectedStageStatus} args={activeToolArgs} />
            </motion.div>
          )}
        </AnimatePresence>
      </DataPanel>
    </div>
    </ImageGalleryProvider>
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
  sessionName,
  embedded = false,
  reasoningReturnTo,
  floatingToolbarContainer,
}: MaisakaMonitorProps = {}) {
  return (
    <div
      className={cn(
        'relative flex min-w-0 flex-col',
        embedded ? 'h-full min-h-0' : 'gap-4 lg:h-[calc(100vh-116px)] lg:flex-row',
        floatingToolbarContainer && 'p-0 sm:p-0'
      )}
    >
      {!embedded && <MonitorSidebar />}
      <MonitorTimeline sessionName={sessionName}
        embedded={embedded}
        reasoningReturnTo={reasoningReturnTo}
        floatingToolbarContainer={floatingToolbarContainer}
      />
      <SubagentPanel embedded={embedded} />
    </div>
  )
})
