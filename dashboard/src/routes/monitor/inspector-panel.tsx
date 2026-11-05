/**
 * 麦麦观察 - 检查器右侧栏
 *
 * 「统计」「当前上下文」「子代理」共用一个右侧栏：卡片标题条 + 一块数据屏，
 * 屏里只有分节标题和虚线分隔的行，不再嵌套卡片。
 */
import { Activity, Bot, Brain, ChevronRight, X } from 'lucide-react'
import { Fragment, useEffect, useState } from 'react'

import { Button } from '@/components/ui/button'
import { DataPanel, PageFrame } from '@/components/ui/surface'
import { backendApi } from '@/lib/http'
import { cn } from '@/lib/utils'

import {
  setSubagentPanelState,
  toggleInspectorView,
  useSubagentPanelState,
  type InspectorView,
} from './subagent-panel-state'
import { InspectorRow, InspectorSection } from './inspector-rows'
import { SubagentInspectorView } from './subagent-panel'
import { useMaisakaMonitorSession } from './use-maisaka-monitor'

/** 上下文分段：token 由该轮真实 prompt token 按字符占比换算 */
export interface MonitorContextSection {
  key: string
  tokens: number
  percent: number
}

export interface MonitorStats {
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

// ─── 统计 ─────────────────────────────────────────────────────

/** 最近一轮上下文构成、缓存命中率与会话累计计数 */
function StatsView({ stats }: { stats: MonitorStats }) {
  const hasContext = stats.contextTokens > 0 && stats.contextSections.length > 0
  const totals: [string, string][] = [
    ['平均缓存命中率', stats.cacheHitRate === null ? '—' : `${stats.cacheHitRate.toFixed(0)}%`],
    ['累计输入', formatTokenCount(stats.totalPromptTokens)],
    ['累计输出', formatTokenCount(stats.totalCompletionTokens)],
    ['消息', String(stats.messages)],
    ['循环', String(stats.cycles)],
    ['工具调用', String(stats.toolCalls)],
  ]

  return (
    <>
      <InspectorSection
        title="上下文容量"
        meta={stats.contextTokens > 0 ? `${formatTokenCount(stats.contextTokens)} tokens` : '暂无数据'}
      />
      {hasContext ? (
        <>
          <div className="px-3 pt-2.5 pb-1">
            <div data-inspector-meter="true" className="flex h-[18px] w-full gap-0.5">
              {stats.contextSections.map((section) => (
                <span
                  key={section.key}
                  className="h-full"
                  style={{
                    width: `${section.percent}%`,
                    backgroundColor: resolveContextSectionMeta(section.key).color,
                  }}
                />
              ))}
            </div>
          </div>
          {stats.contextSections.map((section) => {
            const sectionMeta = resolveContextSectionMeta(section.key)
            return (
              <InspectorRow key={section.key}>
                <span
                  aria-hidden="true"
                  data-inspector-swatch="true"
                  className="h-2.5 w-2.5 shrink-0"
                  style={{ backgroundColor: sectionMeta.color }}
                />
                <span className="min-w-0 flex-1 truncate">{sectionMeta.label}</span>
                <span className="text-muted-foreground font-mono tabular-nums">{section.percent.toFixed(1)}%</span>
                <span className="w-12 text-right font-mono tabular-nums">{formatTokenCount(section.tokens)}</span>
              </InspectorRow>
            )
          })}
        </>
      ) : (
        <InspectorRow className="text-muted-foreground">尚无 planner 请求数据，产生一次推理后展示上下文构成。</InspectorRow>
      )}
      <InspectorSection title="运行累计" />
      {totals.map(([label, value]) => (
        <InspectorRow key={label}>
          <span className="flex-1">{label}</span>
          <span className="font-mono text-sm tabular-nums">{value}</span>
        </InspectorRow>
      ))}
    </>
  )
}

// ─── 当前上下文 ───────────────────────────────────────────────

interface ChatActiveContext {
  memory: string[]
  expressions: string[]
  jargon: { name: string; meaning: string }[]
  tools: string[]
  runtime_active: boolean
  replyer_timestamp: number | null
}

const TOOL_PREVIEW_COUNT = 8

function ContextView({ sessionId }: { sessionId: string | null }) {
  const [context, setContext] = useState<ChatActiveContext | null>(null)
  const [error, setError] = useState(false)
  const [openJargon, setOpenJargon] = useState<string | null>(null)
  const [showAllTools, setShowAllTools] = useState(false)

  useEffect(() => {
    if (!sessionId) return
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
  }, [sessionId])

  if (!sessionId) return <InspectorRow className="text-muted-foreground">请先选择一个聊天流</InspectorRow>
  if (error) return <InspectorRow className="text-muted-foreground">读取上下文失败</InspectorRow>
  if (!context) return <InspectorRow className="text-muted-foreground">正在读取…</InspectorRow>

  const visibleTools = showAllTools ? context.tools : context.tools.slice(0, TOOL_PREVIEW_COUNT)
  const textSections = [
    { title: '状态摘要', items: context.memory, emptyText: '当前上下文尚未进行压缩' },
    { title: '表达方式', items: context.expressions, emptyText: '最近一次 Replyer 请求中未使用' },
  ]

  return (
    <>
      {!context.runtime_active && <InspectorRow className="text-muted-foreground">当前聊天流未运行</InspectorRow>}
      {textSections.map((section) => (
        <Fragment key={section.title}>
          <InspectorSection title={section.title} meta={section.items.length} />
          {section.items.length ? (
            section.items.map((item, index) => (
              <InspectorRow key={`${section.title}-${index}`} className="leading-relaxed whitespace-pre-wrap break-words">
                {item}
              </InspectorRow>
            ))
          ) : (
            <InspectorRow className="text-muted-foreground">{section.emptyText}</InspectorRow>
          )}
        </Fragment>
      ))}

      <InspectorSection title="命中的黑话" meta={context.jargon.length} />
      {context.jargon.length ? (
        context.jargon.map((entry) => {
          const expanded = openJargon === entry.name
          return (
            <div key={entry.name} data-rows-row="true">
              <button
                type="button"
                aria-expanded={expanded}
                onClick={() => setOpenJargon(expanded ? null : entry.name)}
                data-selected={expanded}
                data-inspector-row-button="true"
                className="flex w-full min-w-0 items-center gap-2 px-3 py-2 text-left text-xs font-semibold"
              >
                <span className="min-w-0 flex-1 truncate">{entry.name}</span>
                <ChevronRight className={cn('h-3.5 w-3.5 shrink-0 transition-transform', expanded && 'rotate-90')} />
              </button>
              {expanded && (
                <p className="px-3 pt-1 pb-3 text-xs leading-relaxed whitespace-pre-wrap break-words">
                  {entry.meaning || '暂无释义'}
                </p>
              )}
            </div>
          )
        })
      ) : (
        <InspectorRow className="text-muted-foreground">当前上下文没有黑话参考</InspectorRow>
      )}

      <InspectorSection title="激活的工具" meta={context.tools.length} />
      {context.tools.length ? (
        <>
          {visibleTools.map((tool) => (
            <InspectorRow key={tool} className="font-mono">{tool}</InspectorRow>
          ))}
          {context.tools.length > TOOL_PREVIEW_COUNT && (
            <div className="px-3 py-2">
              <Button variant="outline" size="xs" onClick={() => setShowAllTools((current) => !current)}>
                {showAllTools ? '收起' : `再显示 ${context.tools.length - TOOL_PREVIEW_COUNT} 个`}
              </Button>
            </div>
          )}
        </>
      ) : (
        <InspectorRow className="text-muted-foreground">当前尚未激活工具</InspectorRow>
      )}
      <p className="text-muted-foreground px-3 py-2 text-[11px]">
        表达方式来自最近一次 Replyer 请求：
        {context.replyer_timestamp ? new Date(context.replyer_timestamp).toLocaleString() : '无记录'}
      </p>
    </>
  )
}

// ─── 工具栏开关与右侧栏 ───────────────────────────────────────

const VIEW_META: Record<InspectorView, { label: string; icon: typeof Activity }> = {
  stats: { label: '统计', icon: Activity },
  context: { label: '当前上下文', icon: Brain },
  subagents: { label: '子代理', icon: Bot },
}

/** 时间线工具栏上的检查器开关；三个视图共用一个右侧栏，选中项是按下的键。 */
export function InspectorToggleButton({ view, disabled }: { view: InspectorView; disabled?: boolean }) {
  const { open, view: activeView } = useSubagentPanelState()
  const { subagentTasks } = useMaisakaMonitorSession()
  const runningCount = subagentTasks.filter((task) => task.status === 'running').length
  const { label, icon: Icon } = VIEW_META[view]
  const active = open && activeView === view
  return (
    <Button
      variant="outline"
      size="sm"
      className="shrink-0"
      disabled={disabled}
      aria-pressed={active}
      title={active ? `收起${label}` : `在右侧栏查看${label}`}
      onClick={() => toggleInspectorView(view)}
    >
      <Icon className="mr-1 h-3 w-3" />
      {label}
      {view === 'subagents' && runningCount > 0 && (
        <span className="ml-1 flex items-center gap-1 font-mono">· {runningCount}</span>
      )}
    </Button>
  )
}

/** 聊天工作区的检查器右侧栏，浮在观察时间线上方。 */
export function InspectorPanel({ stats, sessionId }: { stats: MonitorStats; sessionId: string | null }) {
  const { open, view } = useSubagentPanelState()
  const { subagentTasks } = useMaisakaMonitorSession()
  if (!open) return null

  const label = VIEW_META[view].label
  const meta =
    view === 'stats' ? '本聊天流累计' : view === 'subagents' ? `${subagentTasks.length} 个任务` : null

  return (
    <PageFrame
      asChild
      data-maisaka-inspector-panel="true"
      data-maisaka-subagent-panel={view === 'subagents' ? 'true' : undefined}
    >
      <aside
        aria-label={label}
        className="absolute inset-y-0 right-0 z-30 flex w-[min(24rem,100%)] min-w-0 flex-col overflow-hidden"
      >
        <div data-inspector-head="true" className="flex shrink-0 items-center gap-2 px-3 py-2">
          <h2 className="text-sm font-semibold">{label}</h2>
          {meta && <span className="text-muted-foreground text-xs">{meta}</span>}
          <Button
            variant="outline"
            size="icon-xs"
            className="ml-auto"
            onClick={() => setSubagentPanelState({ open: false })}
            title="收起右侧栏"
            aria-label="收起右侧栏"
          >
            <X className="h-3.5 w-3.5" />
          </Button>
        </div>
        <DataPanel padding="none" inset className="min-h-0 flex-1 overflow-y-auto">
          {view === 'stats' && <StatsView stats={stats} />}
          {view === 'context' && <ContextView sessionId={sessionId} />}
          {view === 'subagents' && <SubagentInspectorView />}
        </DataPanel>
      </aside>
    </PageFrame>
  )
}
