/**
 * 麦麦观察 - 子代理运行情况
 *
 * 子代理的运行过程不进入主时间线，而是放在一个可展开的右侧栏里：
 * 上半部分是当前聊天流的任务列表，下半部分是选中任务的逐轮过程与结论。
 */
import { Bot } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Separator } from '@/components/ui/separator'
import type { SubagentTaskStatus } from '@/lib/maisaka-monitor-client'
import { cn } from '@/lib/utils'

import { setSubagentPanelState, useSubagentPanelState } from './subagent-panel-state'
import type { SubagentRoundInfo, SubagentTaskInfo, SubagentToolCallInfo } from './use-maisaka-monitor'
import { useMaisakaMonitorSession } from './use-maisaka-monitor'

// ─── 展示辅助 ─────────────────────────────────────────────────

const STATUS_LABELS: Record<SubagentTaskStatus, string> = {
  running: '运行中',
  completed: '已完成',
  failed: '失败',
  stopped: '已终止',
}

const STATUS_CLASS_NAMES: Record<SubagentTaskStatus, string> = {
  running: 'text-primary',
  completed: 'text-emerald-600',
  failed: 'text-destructive',
  stopped: 'text-muted-foreground',
}

/** 运行中的方块指示：整块明暗跳变，不做平滑旋转。 */
function RunningBlock({ className }: { className?: string }) {
  return (
    <span
      aria-hidden="true"
      className={cn(
        'bg-primary inline-block h-1.5 w-1.5 shrink-0 animate-[pulse_1s_steps(2,jump-none)_infinite]',
        className
      )}
    />
  )
}

function StatusLabel({ status }: { status: SubagentTaskStatus }) {
  return (
    <span className={cn('flex shrink-0 items-center gap-1 text-[10px]', STATUS_CLASS_NAMES[status])}>
      {status === 'running' && <RunningBlock />}
      {STATUS_LABELS[status]}
    </span>
  )
}

function formatSeconds(seconds: number) {
  if (seconds < 60) return `${Math.max(0, Math.round(seconds))} 秒`
  return `${Math.floor(seconds / 60)} 分 ${Math.round(seconds % 60)} 秒`
}

/** 运行中的任务每秒刷新一次已用时间，结束后显示最终用时。 */
function TaskElapsed({ task }: { task: SubagentTaskInfo }) {
  const [now, setNow] = useState(() => Date.now() / 1000)
  const running = task.status === 'running'

  useEffect(() => {
    if (!running) return
    const timer = setInterval(() => setNow(Date.now() / 1000), 1000)
    return () => clearInterval(timer)
  }, [running])

  if (!running) {
    const seconds =
      task.durationMs !== undefined
        ? task.durationMs / 1000
        : (task.finishedAt ?? task.startedAt) - task.startedAt
    return <span>{formatSeconds(seconds)}</span>
  }
  return <span>{formatSeconds(now - task.startedAt)}</span>
}

function formatToolUsage(toolUsage: Record<string, number>) {
  const entries = Object.entries(toolUsage)
  if (entries.length === 0) return '未调用工具'
  return entries.map(([name, count]) => `${name}×${count}`).join('、')
}

function formatArguments(args?: Record<string, unknown>) {
  if (!args || Object.keys(args).length === 0) return ''
  return JSON.stringify(args)
}

// ─── 工具栏按钮 ───────────────────────────────────────────────

/** 时间线工具栏上的开关，样式与旁边的“当前上下文”按钮一致；有任务在跑时带上数量。 */
export function SubagentToggleButton() {
  const { open } = useSubagentPanelState()
  const { subagentTasks } = useMaisakaMonitorSession()
  const runningCount = subagentTasks.filter((task) => task.status === 'running').length

  return (
    <Button
      variant="outline"
      size="sm"
      className="shrink-0"
      onClick={() => setSubagentPanelState({ open: !open, focusCallId: null })}
      title={open ? '收起子代理侧边栏' : '展开子代理侧边栏'}
      aria-pressed={open}
    >
      <Bot className="mr-1 h-3 w-3" />
      子代理
      {runningCount > 0 && (
        <span className="ml-1 flex items-center gap-1">
          <RunningBlock />
          {runningCount}
        </span>
      )}
    </Button>
  )
}

// ─── 任务列表 ─────────────────────────────────────────────────

function TaskListItem({
  task,
  selected,
  onSelect,
}: {
  task: SubagentTaskInfo
  selected: boolean
  onSelect: () => void
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      aria-pressed={selected}
      className={cn(
        'hover:bg-accent/60 flex w-full min-w-0 flex-col gap-1 rounded-md px-2 py-1.5 text-left transition-colors',
        selected && 'bg-accent'
      )}
    >
      <div className="flex min-w-0 items-center gap-1.5">
        <span className="font-mono text-xs font-medium">{task.taskId}</span>
        <StatusLabel status={task.status} />
        {task.background && (
          <Badge variant="outline" className="h-4 px-1 text-[9px]">
            后台
          </Badge>
        )}
        <span className="text-muted-foreground ml-auto shrink-0 text-[10px]">
          <TaskElapsed task={task} />
        </span>
      </div>
      <div className="text-muted-foreground line-clamp-2 text-[11px] leading-snug">
        {task.description}
      </div>
      <div className="text-muted-foreground flex min-w-0 items-center gap-1.5 text-[10px]">
        <span className="shrink-0">
          第 {Math.min(task.rounds.length, task.maxRounds)} / {task.maxRounds} 轮
        </span>
        <span className="truncate">{formatToolUsage(task.toolUsage)}</span>
      </div>
    </button>
  )
}

// ─── 任务详情 ─────────────────────────────────────────────────

function ToolCallRow({ toolCall }: { toolCall: SubagentToolCallInfo }) {
  const argumentsText = formatArguments(toolCall.call.arguments)
  const result = toolCall.result

  return (
    <div className="border-border bg-muted/30 min-w-0 space-y-1 rounded-md border px-2 py-1.5">
      <div className="flex min-w-0 items-center gap-1.5">
        <span className="font-mono text-xs font-medium">{toolCall.call.name}</span>
        {result ? (
          <>
            <span className={cn('text-[10px]', result.success ? 'text-emerald-600' : 'text-destructive')}>
              {result.success ? '成功' : '失败'}
            </span>
            <span className="text-muted-foreground ml-auto shrink-0 text-[10px]">
              {(result.durationMs / 1000).toFixed(1)} 秒
            </span>
          </>
        ) : (
          <span className="text-primary ml-auto flex shrink-0 items-center gap-1 text-[10px]">
            <RunningBlock />
            执行中
          </span>
        )}
      </div>
      {argumentsText && (
        <div className="text-muted-foreground font-mono text-[10px] leading-snug break-all">
          {argumentsText}
        </div>
      )}
      {result?.summary && (
        <div className="max-h-32 overflow-y-auto text-[11px] leading-snug break-words whitespace-pre-wrap">
          {result.summary}
        </div>
      )}
    </div>
  )
}

function RoundBlock({ round, isConclusion }: { round: SubagentRoundInfo; isConclusion: boolean }) {
  return (
    <div className="min-w-0 space-y-1.5">
      <div className="text-muted-foreground flex items-center gap-1.5 text-[10px]">
        <span className="font-medium">第 {round.roundIndex} 轮</span>
        {isConclusion && <span>给出结论</span>}
      </div>
      {round.content && !isConclusion && (
        <div className="text-xs leading-relaxed break-words whitespace-pre-wrap">{round.content}</div>
      )}
      {round.toolCalls.map((toolCall) => (
        <ToolCallRow key={toolCall.call.id} toolCall={toolCall} />
      ))}
    </div>
  )
}

function TaskDetail({ task }: { task: SubagentTaskInfo }) {
  return (
    <div className="min-w-0 space-y-3 p-3">
      <div className="space-y-1">
        <div className="text-muted-foreground text-[10px] font-medium">任务</div>
        <div className="text-xs leading-relaxed break-words whitespace-pre-wrap">{task.description}</div>
      </div>
      <div className="space-y-1">
        <div className="text-muted-foreground text-[10px] font-medium">授权工具</div>
        <div className="flex flex-wrap gap-1">
          {task.grantedTools.map((toolName) => (
            <Badge key={toolName} variant="secondary" className="h-5 px-1.5 font-mono text-[10px]">
              {toolName}
            </Badge>
          ))}
        </div>
      </div>

      {task.rounds.length > 0 && (
        <div className="space-y-2.5">
          <div className="text-muted-foreground text-[10px] font-medium">运行过程</div>
          {task.rounds.map((round, index) => (
            <RoundBlock
              key={round.roundIndex}
              round={round}
              isConclusion={
                task.status === 'completed' &&
                index === task.rounds.length - 1 &&
                round.toolCalls.length === 0
              }
            />
          ))}
        </div>
      )}

      {task.status === 'running' && (
        <div className="text-primary flex items-center gap-1.5 text-[11px]">
          <RunningBlock />
          子代理正在工作…
        </div>
      )}
      {task.status === 'completed' && (
        <div className="space-y-1">
          <div className="text-muted-foreground text-[10px] font-medium">结论</div>
          <div className="border-border bg-muted/30 rounded-md border px-2 py-1.5 text-xs leading-relaxed break-words whitespace-pre-wrap">
            {task.conclusion}
          </div>
        </div>
      )}
      {task.status === 'failed' && (
        <div className="text-destructive text-xs leading-relaxed break-words">
          {task.errorMessage || '子代理没有完成任务'}
        </div>
      )}
      {task.status === 'stopped' && (
        <div className="text-muted-foreground text-xs">任务已被终止，没有送回结论。</div>
      )}
    </div>
  )
}

// ─── 右侧栏 ───────────────────────────────────────────────────

/** 子代理右侧栏；宽屏下与时间线并排，窄屏或嵌入模式下浮在时间线上方。 */
export function SubagentPanel({ embedded = false }: { embedded?: boolean }) {
  const { open, selectedTaskKey, focusCallId } = useSubagentPanelState()
  const { subagentTasks } = useMaisakaMonitorSession()

  // 运行中的排在前面，其余按开始时间从新到旧
  const sortedTasks = useMemo(
    () =>
      subagentTasks.slice().sort((a, b) => {
        const runningDelta = Number(b.status === 'running') - Number(a.status === 'running')
        return runningDelta !== 0 ? runningDelta : b.startedAt - a.startedAt
      }),
    [subagentTasks]
  )

  // 从时间线卡片跳转过来时，定位到对应的任务
  useEffect(() => {
    if (!focusCallId) return
    const focusedTask = subagentTasks.find((task) => task.callId === focusCallId)
    if (focusedTask) {
      setSubagentPanelState({ selectedTaskKey: focusedTask.key, focusCallId: null })
    }
  }, [focusCallId, subagentTasks])

  if (!open) return null

  const selectedTask =
    sortedTasks.find((task) => task.key === selectedTaskKey) ?? sortedTasks[0] ?? null

  return (
    <aside
      data-maisaka-subagent-panel="true"
      className={cn(
        'border-border bg-background absolute inset-y-0 right-0 z-30 flex w-[min(30rem,100%)] min-w-0 flex-col overflow-hidden border shadow-lg',
        !embedded && 'lg:bg-background/45 lg:static lg:z-auto lg:w-[30rem] lg:shrink-0 lg:shadow-none'
      )}
    >
      <div className="flex items-center gap-2 px-3 py-2">
        <h2 className="text-sm font-medium">子代理</h2>
        <span className="text-muted-foreground text-[11px]">{subagentTasks.length} 个任务</span>
        <Button
          variant="ghost"
          size="sm"
          className="ml-auto h-6 shrink-0 border-0! px-2 text-[11px]"
          onClick={() => setSubagentPanelState({ open: false })}
          title="收起子代理侧边栏"
        >
          收起
        </Button>
      </div>
      <Separator />

      {sortedTasks.length === 0 ? (
        <div className="text-muted-foreground flex flex-1 items-center justify-center px-6 text-center text-xs leading-relaxed">
          当前聊天流还没有子代理任务。麦麦派发子代理后，运行过程会显示在这里。
        </div>
      ) : (
        <>
          <ScrollArea className="max-h-[38%] shrink-0">
            <div className="space-y-0.5 p-1.5">
              {sortedTasks.map((task) => (
                <TaskListItem
                  key={task.key}
                  task={task}
                  selected={task.key === selectedTask?.key}
                  onSelect={() => setSubagentPanelState({ selectedTaskKey: task.key })}
                />
              ))}
            </div>
          </ScrollArea>
          <Separator />
          <ScrollArea className="min-h-0 flex-1">
            {selectedTask && <TaskDetail task={selectedTask} />}
          </ScrollArea>
        </>
      )}
    </aside>
  )
}
