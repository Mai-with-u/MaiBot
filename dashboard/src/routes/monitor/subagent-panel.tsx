/**
 * 麦麦观察 - 子代理运行情况
 *
 * 子代理的运行过程不进入主时间线，而是作为检查器右侧栏的一个视图：
 * 上半部分是当前聊天流的任务列表，下半部分是选中任务的逐轮过程与结论。
 */
import { useEffect, useMemo, useState } from 'react'

import { StatusLight, type LightStatus } from '@/components/ui/status-light'
import type { SubagentTaskStatus } from '@/lib/maisaka-monitor-client'
import { cn } from '@/lib/utils'

import { InspectorRow, InspectorSection } from './inspector-rows'
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

const STATUS_LIGHTS: Record<SubagentTaskStatus, LightStatus> = {
  running: 'mixed',
  completed: 'on',
  failed: 'error',
  stopped: 'off',
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
  return entries.map(([name, count]) => `${name}×${count}`).join(' · ')
}

function formatArguments(args?: Record<string, unknown>) {
  if (!args || Object.keys(args).length === 0) return ''
  return JSON.stringify(args)
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
      data-rows-row="true"
      data-selected={selected}
      data-inspector-row-button="true"
      className="flex w-full min-w-0 flex-col gap-1 px-3 py-2 text-left"
    >
      <span className="flex min-w-0 items-center gap-2">
        <StatusLight status={STATUS_LIGHTS[task.status]} decorative />
        <span className="font-mono text-xs font-semibold">{task.taskId}</span>
        <span className="text-[11px]">{STATUS_LABELS[task.status]}</span>
        <span className="ml-auto shrink-0 font-mono text-[11px]">
          <TaskElapsed task={task} />
        </span>
      </span>
      <span className="truncate pl-4 text-[11px] opacity-80">{task.description}</span>
      <span className="truncate pl-4 font-mono text-[11px] opacity-80">
        第 {Math.min(task.rounds.length, task.maxRounds)} / {task.maxRounds} 轮 · {formatToolUsage(task.toolUsage)}
      </span>
    </button>
  )
}

// ─── 任务详情 ─────────────────────────────────────────────────

function ToolCallRow({ toolCall }: { toolCall: SubagentToolCallInfo }) {
  const argumentsText = formatArguments(toolCall.call.arguments)
  const result = toolCall.result

  return (
    <InspectorRow className="flex-col items-stretch gap-1">
      <span className="flex min-w-0 items-center gap-2">
        <StatusLight
          status={result ? (result.success ? 'on' : 'error') : 'mixed'}
          label={result ? (result.success ? '成功' : '失败') : '执行中'}
        />
        <span className="font-mono">{toolCall.call.name}</span>
        {!result && <span className="text-[11px]">执行中</span>}
        {result && !result.success && <span className="text-destructive text-[11px]">失败</span>}
        {result && (
          <span className="text-muted-foreground ml-auto shrink-0 font-mono text-[11px]">
            {(result.durationMs / 1000).toFixed(1)} 秒
          </span>
        )}
      </span>
      {argumentsText && (
        <span className="text-muted-foreground pl-4 font-mono text-[11px] leading-snug break-all">
          {argumentsText}
        </span>
      )}
      {result?.summary && (
        <span className="max-h-32 overflow-y-auto pl-4 text-[11px] leading-snug break-words whitespace-pre-wrap">
          {result.summary}
        </span>
      )}
    </InspectorRow>
  )
}

function RoundBlock({ round, isConclusion }: { round: SubagentRoundInfo; isConclusion: boolean }) {
  return (
    <>
      <InspectorSection title={`第 ${round.roundIndex} 轮`} meta={isConclusion ? '给出结论' : undefined} />
      {round.content && !isConclusion && (
        <InspectorRow className="leading-relaxed break-words whitespace-pre-wrap">{round.content}</InspectorRow>
      )}
      {round.toolCalls.map((toolCall) => (
        <ToolCallRow key={toolCall.call.id} toolCall={toolCall} />
      ))}
    </>
  )
}

function TaskDetail({ task }: { task: SubagentTaskInfo }) {
  return (
    <>
      <InspectorSection title="任务" />
      <InspectorRow className="leading-relaxed break-words whitespace-pre-wrap">{task.description}</InspectorRow>
      <InspectorSection title="授权工具" />
      <InspectorRow className="font-mono">
        {task.grantedTools.length ? task.grantedTools.join(' · ') : '无'}
      </InspectorRow>

      {task.rounds.map((round, index) => (
        <RoundBlock
          key={round.roundIndex}
          round={round}
          isConclusion={
            task.status === 'completed' && index === task.rounds.length - 1 && round.toolCalls.length === 0
          }
        />
      ))}

      {task.status === 'running' && (
        <InspectorRow className="text-muted-foreground">
          <StatusLight status="mixed" decorative />
          子代理正在工作…
        </InspectorRow>
      )}
      {task.status === 'completed' && (
        <>
          <InspectorSection title="结论" />
          <InspectorRow className="leading-relaxed break-words whitespace-pre-wrap">{task.conclusion}</InspectorRow>
        </>
      )}
      {task.status === 'failed' && (
        <InspectorRow className={cn('text-destructive leading-relaxed break-words')}>
          {task.errorMessage || '子代理没有完成任务'}
        </InspectorRow>
      )}
      {task.status === 'stopped' && (
        <InspectorRow className="text-muted-foreground">任务已被终止，没有送回结论。</InspectorRow>
      )}
    </>
  )
}

// ─── 检查器视图 ───────────────────────────────────────────────

/** 检查器右侧栏的「子代理」视图：任务列表 + 选中任务的过程。 */
export function SubagentInspectorView() {
  const { selectedTaskKey, focusCallId } = useSubagentPanelState()
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

  if (sortedTasks.length === 0) {
    return (
      <InspectorRow className="text-muted-foreground leading-relaxed">
        当前聊天流还没有子代理任务。麦麦派发子代理后，运行过程会显示在这里。
      </InspectorRow>
    )
  }

  const selectedTask = sortedTasks.find((task) => task.key === selectedTaskKey) ?? sortedTasks[0]

  return (
    <>
      {sortedTasks.map((task) => (
        <TaskListItem
          key={task.key}
          task={task}
          selected={task.key === selectedTask.key}
          onSelect={() => setSubagentPanelState({ selectedTaskKey: task.key })}
        />
      ))}
      <TaskDetail task={selectedTask} />
    </>
  )
}
