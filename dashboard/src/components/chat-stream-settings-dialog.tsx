/**
 * 麦麦聊天页的聊天流设置弹窗。
 *
 * 弹窗内展示 Session 基本信息、适配器规则、发言频率规则、聊天 Prompt 与学习配置，
 * 并提供删除聊天流的严肃确认入口。
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle,
  Ban,
  CheckCircle2,
  Edit3,
  Plus,
  Save,
  Trash2,
  Undo2,
} from 'lucide-react'
import type { CSSProperties, PointerEvent, ReactNode } from 'react'
import { useEffect, useMemo, useRef, useState } from 'react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Progress } from '@/components/ui/progress'
import { Slider } from '@/components/ui/slider'
import { Skeleton } from '@/components/ui/skeleton'
import { Textarea } from '@/components/ui/textarea'
import { useToast } from '@/hooks/use-toast'
import { formatChatDisplayName, getChatTypeText } from '@/lib/chat-display'
import {
  CHAT_ADAPTER_STATUS_QUERY_KEY,
  deleteChatStream,
  deleteChatStreamPrompt,
  deleteChatStreamTalkFrequency,
  getAdapterPolicyDefaults,
  getChatStreamDetail,
  updateChatStreamAdapterPolicy,
  updateChatStreamLearning,
  updateChatStreamTalkFrequency,
  upsertChatStreamPrompt,
  type ChatAdapterStatus,
  type ChatConfigRule,
  type ChatLearningStatus,
  type ChatPromptRule,
  type ChatStreamDetail,
  type ChatStreamDeleteResult,
  type ChatTalkFrequencyRule,
} from '@/lib/chat-management-api'
import { cn } from '@/lib/utils'

type LearningKind = 'expression' | 'jargon'

function formatRuleTarget(rule: ChatConfigRule | null): string {
  if (!rule) {
    return '未命中显式规则，使用默认行为'
  }
  if (rule.is_default) {
    return '默认规则'
  }
  const platform = rule.platform || '*'
  const itemId = rule.item_id || '*'
  return `${platform}:${itemId}:${getChatTypeText(rule.type === 'private' ? 'private' : 'group')}`
}

function StatusBadge({ enabled }: { enabled: boolean }) {
  return (
    <Badge
      variant={enabled ? 'default' : 'outline'}
      className={enabled ? '' : 'text-muted-foreground'}
    >
      {enabled ? '开启' : '关闭'}
    </Badge>
  )
}

function ConfigStatusRow({
  detail,
  kind,
  title,
  status,
}: {
  detail: ChatStreamDetail
  kind: LearningKind
  title: string
  status: ChatLearningStatus
}) {
  const queryClient = useQueryClient()
  const { toast } = useToast()
  const updateMutation = useMutation({
    mutationFn: (payload: { learn: boolean; use: boolean }) =>
      updateChatStreamLearning(detail.session_id, kind, payload),
    onSuccess: (nextDetail) => {
      queryClient.setQueryData(['chat-stream-detail', detail.session_id], nextDetail)
      void queryClient.invalidateQueries({ queryKey: ['chat-streams'] })
      toast({ title: `${title}学习配置已保存` })
    },
    onError: (error) => {
      toast({
        title: `${title}学习配置保存失败`,
        description: error instanceof Error ? error.message : '请稍后重试',
        variant: 'destructive',
      })
    },
  })
  const isSaving = updateMutation.isPending

  const saveStatus = (nextStatus: { learn: boolean; use: boolean }) => {
    updateMutation.mutate(nextStatus)
  }

  return (
    <div className="grid gap-3 rounded-md border p-3 text-sm lg:grid-cols-[5rem_1fr_1fr_minmax(12rem,1.5fr)] lg:items-center">
      <div className="text-base font-medium">{title}</div>
      <div className="flex items-center justify-between gap-3 lg:justify-start">
        <Label className="text-muted-foreground flex items-center gap-2">
          <Checkbox
            checked={status.use}
            disabled={isSaving}
            onCheckedChange={(checked) =>
              saveStatus({ use: checked === true, learn: status.learn })
            }
          />
          使用
        </Label>
        <StatusBadge enabled={status.use} />
      </div>
      <div className="flex items-center justify-between gap-3 lg:justify-start">
        <Label className="text-muted-foreground flex items-center gap-2">
          <Checkbox
            checked={status.learn}
            disabled={isSaving}
            onCheckedChange={(checked) => saveStatus({ use: status.use, learn: checked === true })}
          />
          学习
        </Label>
        <StatusBadge enabled={status.learn} />
      </div>
      <div className="text-muted-foreground min-w-0 text-xs">
        命中规则：<span className="break-all">{formatRuleTarget(status.matched_rule)}</span>
      </div>
    </div>
  )
}

type TalkFrequencyEditMode = 'input' | 'timeline'
type TimelineEdge = 'end' | 'start'

interface TimelineRange {
  end: number
  start: number
}

const DAY_MINUTES = 24 * 60
const TIMELINE_TICKS = [0, 3, 6, 9, 12, 15, 18, 21, 24]
const TIMELINE_DRAG_STEP_MINUTES = 5

function clampTalkFrequencyValue(value: number): number {
  if (!Number.isFinite(value)) {
    return 0
  }
  return Math.max(0, Math.min(1, value))
}

function parseTimelineMinute(value: string): number | null {
  const match = /^(\d{1,2}):(\d{1,2})$/.exec(value.trim())
  if (!match) {
    return null
  }
  const hour = Number(match[1])
  const minute = Number(match[2])
  if (!Number.isInteger(hour) || !Number.isInteger(minute)) {
    return null
  }
  if (hour < 0 || hour > 23 || minute < 0 || minute > 59) {
    return null
  }
  return hour * 60 + minute
}

function formatTimelineMinute(minute: number): string {
  const normalizedMinute = Math.max(0, Math.min(DAY_MINUTES - 1, Math.round(minute)))
  const hour = Math.floor(normalizedMinute / 60)
  const minuteInHour = normalizedMinute % 60
  return `${hour.toString().padStart(2, '0')}:${minuteInHour.toString().padStart(2, '0')}`
}

function parseTalkTimeRange(time: string): TimelineRange | null {
  const normalizedTime = time.trim()
  if (!normalizedTime || normalizedTime === '*') {
    return { start: 0, end: DAY_MINUTES - 1 }
  }
  const [startRaw, endRaw, extra] = normalizedTime.split('-')
  if (extra !== undefined) {
    return null
  }
  const start = startRaw ? parseTimelineMinute(startRaw) : null
  const end = endRaw ? parseTimelineMinute(endRaw) : null
  if (start === null || end === null) {
    return null
  }
  return { start, end }
}

function formatTalkTimeRange(range: TimelineRange): string {
  return `${formatTimelineMinute(range.start)}-${formatTimelineMinute(range.end)}`
}

function getTimelineSegments(range: TimelineRange): Array<{ left: number; width: number }> {
  if (range.start <= range.end) {
    return [
      {
        left: (range.start / DAY_MINUTES) * 100,
        width: ((range.end - range.start + 1) / DAY_MINUTES) * 100,
      },
    ]
  }
  return [
    {
      left: (range.start / DAY_MINUTES) * 100,
      width: ((DAY_MINUTES - range.start) / DAY_MINUTES) * 100,
    },
    {
      left: 0,
      width: ((range.end + 1) / DAY_MINUTES) * 100,
    },
  ]
}

function getTimelineMinuteFromClient(clientX: number, timelineElement: HTMLElement): number {
  const rect = timelineElement.getBoundingClientRect()
  const ratio = rect.width > 0 ? (clientX - rect.left) / rect.width : 0
  const rawMinute = Math.max(0, Math.min(DAY_MINUTES - 1, ratio * DAY_MINUTES))
  return Math.round(rawMinute / TIMELINE_DRAG_STEP_MINUTES) * TIMELINE_DRAG_STEP_MINUTES
}

function talkValueColor(value: number): string {
  if (value >= 0.75) {
    return 'bg-emerald-500'
  }
  if (value >= 0.45) {
    return 'bg-amber-500'
  }
  return 'bg-sky-500'
}

function getExactTalkRules(detail: ChatStreamDetail): ChatTalkFrequencyRule[] {
  return detail.talk_frequency.matched_rules.filter((rule) => {
    return (
      String(rule.platform || '').trim() === detail.platform &&
      String(rule.item_id || '').trim() === detail.target_id &&
      String(rule.type || '').trim() === detail.chat_type
    )
  })
}

function formatFrequencySummary(label: string): string {
  const numericValue = Number.parseFloat(label)
  if (!Number.isFinite(numericValue)) {
    return label
  }
  return numericValue.toFixed(2)
}

function FrequencySummaryItem({
  formatValue = true,
  label,
  value,
}: {
  formatValue?: boolean
  label: string
  value: string
}) {
  return (
    <div className="min-w-0 space-y-1 text-sm">
      <div className="text-muted-foreground">{label}</div>
      <div className="font-mono font-semibold whitespace-nowrap tabular-nums">
        {formatValue ? formatFrequencySummary(value) : value}
      </div>
    </div>
  )
}

function TalkFrequencyRuleStackItem({ rule }: { rule: ChatTalkFrequencyRule }) {
  const targetLabel = `${rule.platform || '*'}:${rule.item_id || '*'}:${rule.type || '-'}`
  const timeLabel = rule.time || '默认'

  return (
    <div
      className={cn(
        'rounded-md border px-3 py-2 text-sm',
        rule.is_effective ? 'text-foreground' : 'bg-muted text-muted-foreground'
      )}
    >
      <div className="flex flex-wrap items-center gap-2">
        {!rule.is_effective && !rule.time_active && <Badge variant="outline">时间未命中</Badge>}
        <span className="font-mono text-xs">{targetLabel}</span>
      </div>
      <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1">
        <span>时间：{timeLabel}</span>
        <span>频率：{formatFrequencySummary(rule.value_label)}</span>
      </div>
    </div>
  )
}

function TalkFrequencyRuleEditor({
  detail,
  rule,
}: {
  detail: ChatStreamDetail
  rule: ChatTalkFrequencyRule
}) {
  const queryClient = useQueryClient()
  const { toast } = useToast()
  const [time, setTime] = useState(rule.time)
  const [value, setValue] = useState(() => clampTalkFrequencyValue(rule.value))

  useEffect(() => {
    const frameId = window.requestAnimationFrame(() => {
      setTime(rule.time)
      setValue(clampTalkFrequencyValue(rule.value))
    })

    return () => window.cancelAnimationFrame(frameId)
  }, [detail.session_id, rule])

  const updateDetailCache = (updatedDetail: ChatStreamDetail) => {
    queryClient.setQueryData(['chat-stream-detail', detail.session_id], updatedDetail)
    void queryClient.invalidateQueries({ queryKey: ['chat-streams'] })
  }

  const saveMutation = useMutation({
    mutationFn: () =>
      updateChatStreamTalkFrequency(detail.session_id, {
        previous_time: rule.time,
        time: time.trim(),
        value: clampTalkFrequencyValue(value),
      }),
    onSuccess: (updatedDetail) => {
      updateDetailCache(updatedDetail)
      toast({
        title: '发言频率规则已保存',
        description: '已写入当前聊天流的精确动态频率规则。',
      })
    },
    onError: (error) => {
      toast({
        title: '保存发言频率失败',
        description: error instanceof Error ? error.message : '请稍后重试',
        variant: 'destructive',
      })
    },
  })

  const deleteMutation = useMutation({
    mutationFn: () => deleteChatStreamTalkFrequency(detail.session_id, rule.time),
    onSuccess: (updatedDetail) => {
      updateDetailCache(updatedDetail)
      toast({
        title: '发言频率规则已删除',
        description: '已删除当前聊天流的这条精确规则。',
      })
    },
    onError: (error) => {
      toast({
        title: '删除发言频率规则失败',
        description: error instanceof Error ? error.message : '请稍后重试',
        variant: 'destructive',
      })
    },
  })

  return (
    <div className="bg-muted/25 grid gap-3 rounded-md border p-3 sm:grid-cols-[minmax(8rem,12rem)_1fr_auto] sm:items-end">
      <div className="space-y-2">
        <Label className="text-xs">时间段</Label>
        <Input
          value={time}
          placeholder="* 或 HH:MM-HH:MM"
          onChange={(event) => setTime(event.target.value)}
        />
      </div>
      <div className="space-y-2">
        <Label className="text-xs">发言频率</Label>
        <Input
          type="number"
          min={0}
          max={1}
          step={0.01}
          value={value}
          onChange={(event) => setValue(clampTalkFrequencyValue(Number(event.target.value)))}
        />
      </div>
      <div className="flex items-center justify-end gap-2">
        <Button
          type="button"
          className="shrink-0"
          disabled={saveMutation.isPending}
          onClick={() => saveMutation.mutate()}
        >
          {saveMutation.isPending ? '保存中...' : '保存'}
        </Button>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className="text-destructive hover:text-destructive shrink-0"
          disabled={deleteMutation.isPending}
          aria-label={`删除时间段 ${rule.time || '默认'} 的发言频率规则`}
          onClick={() => deleteMutation.mutate()}
        >
          <Trash2 className="h-4 w-4" />
        </Button>
      </div>
    </div>
  )
}

function TalkFrequencyTimelineRule({
  detail,
  rule,
}: {
  detail: ChatStreamDetail
  rule: ChatTalkFrequencyRule
}) {
  const queryClient = useQueryClient()
  const { toast } = useToast()
  const [time, setTime] = useState(rule.time || '00:00-23:59')
  const [value, setValue] = useState(() => clampTalkFrequencyValue(rule.value))
  const draggingEdgeRef = useRef<TimelineEdge | null>(null)
  const range = parseTalkTimeRange(time)
  const draggableRange = range && time.trim() !== '' && time.trim() !== '*'
  const segments = range ? getTimelineSegments(range) : []
  const startLabelLeft = range ? (range.start / DAY_MINUTES) * 100 : 0
  const endLabelLeft = range ? ((range.end + 1) / DAY_MINUTES) * 100 : 100
  const startLabelTransform = startLabelLeft < 4 ? 'translateX(0)' : 'translateX(-50%)'
  const endLabelTransform = endLabelLeft > 96 ? 'translateX(-100%)' : 'translateX(-50%)'

  useEffect(() => {
    const frameId = window.requestAnimationFrame(() => {
      setTime(rule.time || '00:00-23:59')
      setValue(clampTalkFrequencyValue(rule.value))
    })

    return () => window.cancelAnimationFrame(frameId)
  }, [detail.session_id, rule])

  const updateDetailCache = (updatedDetail: ChatStreamDetail) => {
    queryClient.setQueryData(['chat-stream-detail', detail.session_id], updatedDetail)
    void queryClient.invalidateQueries({ queryKey: ['chat-streams'] })
  }

  const saveMutation = useMutation({
    mutationFn: () =>
      updateChatStreamTalkFrequency(detail.session_id, {
        previous_time: rule.time,
        time: time.trim(),
        value: clampTalkFrequencyValue(value),
      }),
    onSuccess: (updatedDetail) => {
      updateDetailCache(updatedDetail)
      toast({
        title: '发言频率规则已保存',
        description: '已写入当前聊天流的精确动态频率规则。',
      })
    },
    onError: (error) => {
      toast({
        title: '保存发言频率失败',
        description: error instanceof Error ? error.message : '请稍后重试',
        variant: 'destructive',
      })
    },
  })

  const deleteMutation = useMutation({
    mutationFn: () => deleteChatStreamTalkFrequency(detail.session_id, rule.time),
    onSuccess: (updatedDetail) => {
      updateDetailCache(updatedDetail)
      toast({
        title: '发言频率规则已删除',
        description: '已删除当前聊天流的这条精确规则。',
      })
    },
    onError: (error) => {
      toast({
        title: '删除发言频率规则失败',
        description: error instanceof Error ? error.message : '请稍后重试',
        variant: 'destructive',
      })
    },
  })

  const updateTimeFromPointer = (event: PointerEvent<HTMLElement>, edge: TimelineEdge) => {
    if (!range) {
      return
    }
    const timelineElement = event.currentTarget.closest('[data-chat-talk-timeline-track]')
    if (!(timelineElement instanceof HTMLElement)) {
      return
    }
    const nextMinute = getTimelineMinuteFromClient(event.clientX, timelineElement)
    const nextRange =
      edge === 'start' ? { ...range, start: nextMinute } : { ...range, end: nextMinute }
    setTime(formatTalkTimeRange(nextRange))
  }

  const startDrag = (event: PointerEvent<HTMLElement>, edge: TimelineEdge) => {
    event.preventDefault()
    draggingEdgeRef.current = edge
    event.currentTarget.setPointerCapture(event.pointerId)
    updateTimeFromPointer(event, edge)
  }

  return (
    <div className="bg-muted/25 grid min-w-0 gap-3 rounded-md border p-3 xl:grid-cols-[minmax(12rem,1fr)_8rem_8rem] xl:items-center">
      <div className="min-w-0">
        <div className="text-muted-foreground relative mb-1 h-4 px-1 text-[10px]">
          {TIMELINE_TICKS.map((hour) => (
            <span
              key={hour}
              className="absolute -translate-x-1/2"
              style={{ left: `${(hour / 24) * 100}%` }}
            >
              {hour.toString().padStart(2, '0')}
            </span>
          ))}
        </div>
        <div className="bg-background relative h-8 rounded-md border" data-chat-talk-timeline-track>
          {TIMELINE_TICKS.slice(1, -1).map((hour) => (
            <span
              key={hour}
              className="border-muted-foreground/20 absolute top-0 h-full border-l border-dashed"
              style={{ left: `${(hour / 24) * 100}%` }}
            />
          ))}
          {segments.map((segment, index) => (
            <span
              key={index}
              className={cn(
                'absolute top-1/2 h-4 -translate-y-1/2 rounded-sm opacity-85',
                talkValueColor(value),
                !range && 'opacity-35'
              )}
              style={{ left: `${segment.left}%`, width: `${segment.width}%` }}
            />
          ))}
          {draggableRange && (
            <>
              <button
                type="button"
                className="border-background bg-foreground/80 absolute top-1/2 h-7 w-2 -translate-x-1/2 -translate-y-1/2 cursor-ew-resize rounded-sm border"
                style={{ left: `${(range.start / DAY_MINUTES) * 100}%` }}
                aria-label="调整开始时间"
                onPointerDown={(event) => startDrag(event, 'start')}
                onPointerMove={(event) => {
                  if (
                    draggingEdgeRef.current === 'start' &&
                    event.currentTarget.hasPointerCapture(event.pointerId)
                  ) {
                    updateTimeFromPointer(event, 'start')
                  }
                }}
                onPointerUp={(event) => {
                  draggingEdgeRef.current = null
                  event.currentTarget.releasePointerCapture(event.pointerId)
                }}
              />
              <button
                type="button"
                className="border-background bg-foreground/80 absolute top-1/2 h-7 w-2 -translate-x-1/2 -translate-y-1/2 cursor-ew-resize rounded-sm border"
                style={{ left: `${((range.end + 1) / DAY_MINUTES) * 100}%` }}
                aria-label="调整结束时间"
                onPointerDown={(event) => startDrag(event, 'end')}
                onPointerMove={(event) => {
                  if (
                    draggingEdgeRef.current === 'end' &&
                    event.currentTarget.hasPointerCapture(event.pointerId)
                  ) {
                    updateTimeFromPointer(event, 'end')
                  }
                }}
                onPointerUp={(event) => {
                  draggingEdgeRef.current = null
                  event.currentTarget.releasePointerCapture(event.pointerId)
                }}
              />
            </>
          )}
        </div>
        <div className="text-muted-foreground relative mt-1 h-4 text-[11px]">
          {range ? (
            <>
              <span
                className="absolute top-0 font-mono tabular-nums"
                style={{ left: `${startLabelLeft}%`, transform: startLabelTransform }}
              >
                {formatTimelineMinute(range.start)}
              </span>
              <span
                className="absolute top-0 font-mono tabular-nums"
                style={{ left: `${endLabelLeft}%`, transform: endLabelTransform }}
              >
                {formatTimelineMinute(range.end)}
              </span>
            </>
          ) : (
            <span className="font-mono tabular-nums">{time || '-'}</span>
          )}
        </div>
      </div>
      <div className="flex min-w-0 items-center gap-2">
        <Slider
          value={[value]}
          min={0}
          max={1}
          step={0.01}
          onValueChange={(values) => setValue(clampTalkFrequencyValue(values[0] ?? 0))}
          data-dashboard-slider="config"
          data-dashboard-slider-value-format="fixed-2"
        />
        <span className="w-12 text-right font-mono text-xs tabular-nums">{value.toFixed(2)}</span>
      </div>
      <div className="flex justify-end gap-2">
        <Button
          type="button"
          size="sm"
          disabled={saveMutation.isPending}
          onClick={() => saveMutation.mutate()}
        >
          {saveMutation.isPending ? '保存中...' : '保存'}
        </Button>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className="text-destructive hover:text-destructive h-8 w-8"
          disabled={deleteMutation.isPending}
          aria-label={`删除时间段 ${rule.time || '默认'} 的发言频率规则`}
          onClick={() => deleteMutation.mutate()}
        >
          <Trash2 className="h-4 w-4" />
        </Button>
      </div>
    </div>
  )
}

function TalkFrequencyTimelineEditor({
  detail,
  exactRules,
}: {
  detail: ChatStreamDetail
  exactRules: ChatTalkFrequencyRule[]
}) {
  return (
    <div className="space-y-2">
      <div className="text-muted-foreground hidden grid-cols-[minmax(12rem,1fr)_8rem_8rem] gap-3 px-3 text-[11px] xl:grid">
        <div>时间轴</div>
        <div>频率</div>
        <div className="text-right">操作</div>
      </div>
      {exactRules.length === 0 ? (
        <div className="text-muted-foreground rounded-md border border-dashed px-3 py-2 text-sm">
          当前聊天流还没有专属发言频率规则。
        </div>
      ) : (
        <div className="space-y-2">
          {exactRules.map((rule, index) => (
            <TalkFrequencyTimelineRule key={`${rule.time}:${index}`} detail={detail} rule={rule} />
          ))}
        </div>
      )}
    </div>
  )
}

/** 一键新增规则：直接写入一条频率 1.0 的全天精确规则，时间可在保存后再调整。 */
function AddTalkFrequencyRuleButton({ detail }: { detail: ChatStreamDetail }) {
  const queryClient = useQueryClient()
  const { toast } = useToast()
  const addMutation = useMutation({
    mutationFn: () =>
      updateChatStreamTalkFrequency(detail.session_id, {
        previous_time: null,
        time: '00:00-23:59',
        value: 1,
      }),
    onSuccess: (updatedDetail) => {
      queryClient.setQueryData(['chat-stream-detail', detail.session_id], updatedDetail)
      void queryClient.invalidateQueries({ queryKey: ['chat-streams'] })
      toast({
        title: '发言频率规则已新增',
        description: '已写入当前聊天流的精确动态频率规则。',
      })
    },
    onError: (error) => {
      toast({
        title: '新增发言频率规则失败',
        description: error instanceof Error ? error.message : '请稍后重试',
        variant: 'destructive',
      })
    },
  })

  return (
    <Button
      type="button"
      size="sm"
      variant="outline"
      disabled={addMutation.isPending}
      onClick={() => addMutation.mutate()}
    >
      <Plus className="mr-2 h-3.5 w-3.5" />
      {addMutation.isPending ? '新增中...' : '新增规则'}
    </Button>
  )
}

function TalkFrequencyEditor({ detail }: { detail: ChatStreamDetail }) {
  const exactRules = useMemo(() => getExactTalkRules(detail), [detail])
  const [mode, setMode] = useState<TalkFrequencyEditMode>('timeline')

  return (
    <div className="bg-muted/10 space-y-3 rounded-md border p-3">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <div className="text-sm font-medium">当前聊天流规则</div>
        <div className="flex shrink-0 items-center gap-2">
          <AddTalkFrequencyRuleButton detail={detail} />
          <div className="bg-background inline-flex shrink-0 rounded-md border p-1">
            <Button
              type="button"
              size="sm"
              variant={mode === 'timeline' ? 'secondary' : 'ghost'}
              className="h-7"
              onClick={() => setMode('timeline')}
            >
              时间轴
            </Button>
            <Button
              type="button"
              size="sm"
              variant={mode === 'input' ? 'secondary' : 'ghost'}
              className="h-7"
              onClick={() => setMode('input')}
            >
              普通
            </Button>
          </div>
        </div>
      </div>

      {mode === 'timeline' ? (
        <TalkFrequencyTimelineEditor detail={detail} exactRules={exactRules} />
      ) : (
        <div className="space-y-3">
          {exactRules.length === 0 ? (
            <div className="text-muted-foreground rounded-md border border-dashed px-3 py-2 text-sm">
              当前聊天流还没有专属发言频率规则。
            </div>
          ) : (
            <div className="space-y-2">
              {exactRules.map((rule, index) => (
                <TalkFrequencyRuleEditor
                  key={`${rule.time}:${index}`}
                  detail={detail}
                  rule={rule}
                />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function TalkFrequencySection({ detail }: { detail: ChatStreamDetail }) {
  return (
    <section className="space-y-3 rounded-md border p-3">
      <div className="flex items-center justify-between gap-3">
        <div className="font-medium">发言频率规则</div>
        <StatusBadge enabled={detail.talk_frequency.enabled} />
      </div>
      <div className="grid gap-2 text-sm sm:grid-cols-3">
        <FrequencySummaryItem label="默认频率" value={detail.talk_frequency.base_value_label} />
        <FrequencySummaryItem
          label="当前生效"
          value={detail.talk_frequency.effective_value_label}
        />
        <FrequencySummaryItem
          formatValue={false}
          label="当前时间"
          value={detail.talk_frequency.current_time}
        />
      </div>
      <div className="space-y-2">
        {detail.talk_frequency.matched_rules.length === 0 ? (
          <div className="bg-muted text-muted-foreground rounded-md px-3 py-2 text-sm">
            没有可应用的动态发言频率规则，使用默认频率。
          </div>
        ) : (
          detail.talk_frequency.matched_rules.map((rule, index) => (
            <TalkFrequencyRuleStackItem
              key={`${rule.platform}:${rule.item_id}:${rule.time}:${index}`}
              rule={rule}
            />
          ))
        )}
      </div>
      <TalkFrequencyEditor detail={detail} />
    </section>
  )
}

function PromptTextBlock({
  content,
  emptyText,
  title,
}: {
  content: string
  emptyText: string
  title: string
}) {
  const normalizedContent = content.trim()
  return (
    <div className="space-y-2">
      <div className="text-sm font-medium">{title}</div>
      {normalizedContent ? (
        <pre className="bg-muted/25 text-foreground max-h-40 overflow-auto rounded-md border p-3 text-xs leading-5 break-words whitespace-pre-wrap">
          {normalizedContent}
        </pre>
      ) : (
        <div className="text-muted-foreground rounded-md border border-dashed px-3 py-2 text-sm">
          {emptyText}
        </div>
      )}
    </div>
  )
}

function PromptRuleEditor({
  detail,
  prompt,
}: {
  detail: ChatStreamDetail
  prompt: ChatPromptRule
}) {
  const queryClient = useQueryClient()
  const { toast } = useToast()
  const [draft, setDraft] = useState(prompt.prompt)
  const saveMutation = useMutation({
    mutationFn: () => upsertChatStreamPrompt(detail.session_id, { prompt: draft }, prompt.index),
    onSuccess: (nextDetail) => {
      queryClient.setQueryData(['chat-stream-detail', detail.session_id], nextDetail)
      toast({ title: '聊天 Prompt 已保存' })
    },
    onError: (error) => {
      toast({
        title: '聊天 Prompt 保存失败',
        description: error instanceof Error ? error.message : '请稍后重试',
        variant: 'destructive',
      })
    },
  })
  const deleteMutation = useMutation({
    mutationFn: () => deleteChatStreamPrompt(detail.session_id, prompt.index),
    onSuccess: (nextDetail) => {
      queryClient.setQueryData(['chat-stream-detail', detail.session_id], nextDetail)
      toast({ title: '聊天 Prompt 已删除' })
    },
    onError: (error) => {
      toast({
        title: '聊天 Prompt 删除失败',
        description: error instanceof Error ? error.message : '请稍后重试',
        variant: 'destructive',
      })
    },
  })
  const isBusy = saveMutation.isPending || deleteMutation.isPending
  const normalizedDraft = draft.trim()
  const changed = normalizedDraft !== prompt.prompt.trim()
  const [seenPrompt, setSeenPrompt] = useState(prompt.prompt)
  if (seenPrompt !== prompt.prompt) {
    setSeenPrompt(prompt.prompt)
    setDraft(prompt.prompt)
  }

  return (
    <div className="bg-muted/20 space-y-2 rounded-md border p-3">
      <div className="flex items-center justify-between gap-3">
        <div className="text-muted-foreground min-w-0 text-xs">
          专属目标：
          <span className="font-mono break-all">
            {prompt.platform}:{prompt.item_id}:{prompt.rule_type}
          </span>
        </div>
        <Button
          type="button"
          size="icon"
          variant="ghost"
          disabled={isBusy}
          aria-label="删除聊天 Prompt"
          onClick={() => deleteMutation.mutate()}
        >
          <Trash2 className="text-destructive h-4 w-4" />
        </Button>
      </div>
      <Textarea
        value={draft}
        disabled={isBusy}
        onChange={(event) => setDraft(event.target.value)}
        className="min-h-24 text-xs leading-5"
      />
      <div className="flex justify-end">
        <Button
          type="button"
          size="sm"
          disabled={!changed || !normalizedDraft || isBusy}
          onClick={() => saveMutation.mutate()}
        >
          <Save className="mr-2 h-3.5 w-3.5" />
          保存
        </Button>
      </div>
    </div>
  )
}

function NewPromptEditor({ detail }: { detail: ChatStreamDetail }) {
  const queryClient = useQueryClient()
  const { toast } = useToast()
  const [draft, setDraft] = useState('')
  const saveMutation = useMutation({
    mutationFn: () => upsertChatStreamPrompt(detail.session_id, { prompt: draft }),
    onSuccess: (nextDetail) => {
      setDraft('')
      queryClient.setQueryData(['chat-stream-detail', detail.session_id], nextDetail)
      toast({ title: '聊天 Prompt 已新增' })
    },
    onError: (error) => {
      toast({
        title: '聊天 Prompt 新增失败',
        description: error instanceof Error ? error.message : '请稍后重试',
        variant: 'destructive',
      })
    },
  })
  const normalizedDraft = draft.trim()

  return (
    <div className="space-y-2 rounded-md border border-dashed p-3">
      <div className="flex items-center gap-2 text-sm font-medium">
        <Edit3 className="h-4 w-4" />
        新增当前聊天流专属 Prompt
      </div>
      <Textarea
        value={draft}
        disabled={saveMutation.isPending}
        onChange={(event) => setDraft(event.target.value)}
        placeholder="只写这个聊天流额外需要遵守的发言要求。"
        className="min-h-24 text-xs leading-5"
      />
      <div className="flex justify-end">
        <Button
          type="button"
          size="sm"
          disabled={!normalizedDraft || saveMutation.isPending}
          onClick={() => saveMutation.mutate()}
        >
          <Plus className="mr-2 h-3.5 w-3.5" />
          新增
        </Button>
      </div>
    </div>
  )
}

function ChatPromptSection({ detail }: { detail: ChatStreamDetail }) {
  return (
    <section className="space-y-3 rounded-md border p-3">
      <div className="font-medium">聊天 Prompt</div>
      <PromptTextBlock
        title={detail.prompts.base_prompt_title}
        content={detail.prompts.base_prompt}
        emptyText="当前基础 Prompt 为空。"
      />
      <div className="space-y-2">
        <div className="text-sm font-medium">额外聊天流 Prompt</div>
        {detail.prompts.chat_prompts.length === 0 ? (
          <div className="text-muted-foreground rounded-md border border-dashed px-3 py-2 text-sm">
            当前聊天流没有专属额外 Prompt。
          </div>
        ) : (
          <div className="space-y-2">
            {detail.prompts.chat_prompts.map((prompt, index) => (
              <PromptRuleEditor key={`${prompt.index}:${index}`} detail={detail} prompt={prompt} />
            ))}
          </div>
        )}
      </div>
      <NewPromptEditor detail={detail} />
    </section>
  )
}

function ConfigStatusRows({ detail }: { detail: ChatStreamDetail }) {
  const configRows = [
    { kind: 'expression' as const, title: '表达', status: detail.expression },
    { kind: 'jargon' as const, title: '黑话', status: detail.jargon },
  ]

  return (
    <section className="space-y-2">
      {configRows.map((row) =>
        row.status ? (
          <ConfigStatusRow
            key={row.kind}
            detail={detail}
            kind={row.kind}
            title={row.title}
            status={row.status}
          />
        ) : null
      )}
    </section>
  )
}

function CompactDetailItem({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="min-w-0 space-y-1">
      <div className="text-muted-foreground text-xs">{label}</div>
      <div className="min-w-0 text-sm font-medium break-all">{value}</div>
    </div>
  )
}

function getAdapterDisplayName(adapter: ChatAdapterStatus): string {
  const rawName = adapter.gateway_name || adapter.plugin_id || adapter.adapter_id
  const namePart = rawName.split('.').at(-1) || rawName
  return (
    namePart
      .replace(/[-_]+/g, ' ')
      .replace(/\badapter\b/gi, '')
      .trim()
      .replace(/\b\w/g, (char) => char.toUpperCase()) || '适配器'
  )
}

function getAdapterPolicyLabel(adapter: ChatAdapterStatus): string {
  if (adapter.policy.reason === 'matched_allow_override') {
    // 单独放行的状态由高亮的「允许」按钮与下方说明文案表达，不再额外使用标签
    return ''
  }
  if (adapter.policy.reason === 'matched_deny_override') {
    return '已阻止当前聊天'
  }
  if (!adapter.policy.configured) {
    return adapter.policy.allowed ? '使用默认：允许' : '使用默认：拒绝'
  }
  if (adapter.policy.allowed) {
    return adapter.policy.list_type === 'blacklist' ? '黑名单未命中' : '白名单已放行'
  }
  return adapter.policy.list_type === 'blacklist' ? '黑名单已阻止' : '白名单未放行'
}

function getAdapterPolicyDescription(adapter: ChatAdapterStatus): string {
  if (adapter.policy.reason === 'matched_deny_override') {
    return '这条聊天已被单独加入阻止规则。'
  }
  if (!adapter.policy.configured) {
    return adapter.policy.allowed
      ? '未设置统一规则，主程序默认放行。'
      : '未设置统一规则，主程序默认拒绝。'
  }
  if (adapter.policy.allowed) {
    return adapter.policy.source === 'defaults'
      ? '当前聊天被全局适配器规则放行。'
      : '当前聊天被这个适配器的规则放行。'
  }
  return adapter.policy.source === 'defaults'
    ? '当前聊天被全局适配器规则阻止。'
    : '当前聊天被这个适配器的规则阻止。'
}

function hasAdapterAllowOverride(adapter: ChatAdapterStatus): boolean {
  return adapter.policy.reason === 'matched_allow_override'
}

function hasAdapterBlockOverride(adapter: ChatAdapterStatus): boolean {
  return adapter.policy.reason === 'matched_deny_override'
}

function ChatAdapterSection({ detail }: { detail: ChatStreamDetail }) {
  const adapters = detail.adapters ?? []
  const queryClient = useQueryClient()
  const { toast } = useToast()
  const defaultsQuery = useQuery({
    queryKey: ['adapter-policy-defaults'],
    queryFn: getAdapterPolicyDefaults,
  })
  const policyMutation = useMutation({
    mutationFn: (payload: { adapter_id: string; action: 'allow' | 'block' | 'inherit' }) =>
      updateChatStreamAdapterPolicy(detail.session_id, payload),
    onSuccess: (nextDetail) => {
      queryClient.setQueryData(['chat-stream-detail', detail.session_id], nextDetail)
      // 放行结果变化会改变聊天页侧边栏的分组，需要让适配器放行状态缓存失效
      void queryClient.invalidateQueries({ queryKey: [CHAT_ADAPTER_STATUS_QUERY_KEY] })
      toast({ title: '适配器规则已保存' })
    },
    onError: (error) => {
      toast({
        title: '适配器规则保存失败',
        description: error instanceof Error ? error.message : '请稍后重试',
        variant: 'destructive',
      })
    },
  })
  const savingAdapterId = policyMutation.variables?.adapter_id
  const defaultPolicySummary = defaultsQuery.isError
    ? '获取失败'
    : defaultsQuery.data
      ? `群聊 ${defaultsQuery.data.group === 'allow' ? '放行' : '拒绝'} · 私聊 ${defaultsQuery.data.private === 'allow' ? '放行' : '拒绝'}`
      : '加载中'

  const saveAdapterPolicy = (adapter: ChatAdapterStatus, action: 'allow' | 'block' | 'inherit') => {
    policyMutation.mutate({ adapter_id: adapter.adapter_id, action })
  }
  return (
    <section className="space-y-3 rounded-md border p-3">
      <div className="flex items-center justify-between gap-3">
        <div className="font-medium">适配器规则</div>
        <Badge variant="outline">{adapters.length} 个</Badge>
      </div>
      <p className="text-muted-foreground text-xs">全局默认：{defaultPolicySummary}</p>
      {adapters.length === 0 ? (
        <div className="text-muted-foreground rounded-md border border-dashed px-3 py-2 text-sm">
          当前没有运行中的适配器插件路由。
        </div>
      ) : (
        <div className="space-y-2">
          {adapters.map((adapter) => {
            const policyDescription = getAdapterPolicyDescription(adapter)
            const policyLabel = getAdapterPolicyLabel(adapter)
            return (
              <div
                key={adapter.adapter_id}
                className="bg-muted/20 grid gap-2 rounded-md border p-2 md:grid-cols-[minmax(0,1fr)_auto] md:items-center"
              >
                <div className="min-w-0 space-y-0.5">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-medium">{getAdapterDisplayName(adapter)}</span>
                    {policyLabel && (
                      <Badge
                        variant={
                          !adapter.policy.configured
                            ? 'outline'
                            : adapter.policy.allowed
                              ? 'default'
                              : 'destructive'
                        }
                      >
                        {policyLabel}
                      </Badge>
                    )}
                  </div>
                  {policyDescription && (
                    <div className="text-muted-foreground text-sm">{policyDescription}</div>
                  )}
                  {adapter.account_id && (
                    <div className="text-muted-foreground text-xs">账号 {adapter.account_id}</div>
                  )}
                </div>
                <div className="flex flex-wrap gap-1.5 md:justify-end">
                  <Button
                    type="button"
                    size="sm"
                    variant={hasAdapterAllowOverride(adapter) ? 'default' : 'outline'}
                    className={
                      hasAdapterAllowOverride(adapter) ? 'bg-green-600 hover:bg-green-700' : undefined
                    }
                    disabled={policyMutation.isPending}
                    onClick={() => saveAdapterPolicy(adapter, 'allow')}
                  >
                    <CheckCircle2 className="h-4 w-4" />
                    允许
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    variant={hasAdapterBlockOverride(adapter) ? 'destructive' : 'outline'}
                    disabled={policyMutation.isPending}
                    onClick={() => saveAdapterPolicy(adapter, 'block')}
                  >
                    <Ban className="h-4 w-4" />
                    阻止
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    disabled={policyMutation.isPending}
                    onClick={() => saveAdapterPolicy(adapter, 'inherit')}
                  >
                    <Undo2 className="h-4 w-4" />
                    {policyMutation.isPending && savingAdapterId === adapter.adapter_id
                      ? '保存中'
                      : '使用默认'}
                  </Button>
                </div>
                <div className="text-muted-foreground min-w-0 text-xs break-all md:col-span-2">
                  插件：{adapter.plugin_id || adapter.adapter_id}
                  {adapter.gateway_name ? `；网关：${adapter.gateway_name}` : ''}
                  {adapter.scope ? `；范围：${adapter.scope}` : ''}
                </div>
              </div>
            )
          })}
        </div>
      )}
    </section>
  )
}

function ChatDetailContent({
  detail,
  loading,
  error,
}: {
  detail: ChatStreamDetail | undefined
  loading: boolean
  error: unknown
}) {
  if (loading) {
    return (
      <div className="space-y-3">
        <Skeleton className="h-16" />
        <Skeleton className="h-24" />
        <Skeleton className="h-32" />
      </div>
    )
  }

  if (error || !detail) {
    return (
      <div className="border-destructive/40 text-destructive rounded-md border p-4 text-sm">
        加载详情失败
      </div>
    )
  }

  return (
    <div className="space-y-5">
      <section className="space-y-3 rounded-md border p-3">
        <div className="grid gap-3 sm:grid-cols-[minmax(0,2fr)_minmax(0,1fr)_minmax(0,0.8fr)_minmax(0,1.3fr)]">
          <CompactDetailItem
            label="Session ID"
            value={<span className="font-mono text-xs font-normal">{detail.session_id}</span>}
          />
          <CompactDetailItem label="Platform" value={detail.platform || '-'} />
          <CompactDetailItem label="Type" value={getChatTypeText(detail.chat_type)} />
          <CompactDetailItem
            label="ID"
            value={<span className="font-mono">{detail.target_id || '-'}</span>}
          />
        </div>
      </section>

      <ChatAdapterSection detail={detail} />
      <TalkFrequencySection detail={detail} />
      <ChatPromptSection detail={detail} />
      <ConfigStatusRows detail={detail} />
    </div>
  )
}

/** 打开设置弹窗所需的最小聊天流信息。 */
export interface ChatStreamSettingsTarget {
  session_id: string
  display_name: string
  account_id?: string | null
}

function formatDeleteSummary(result: ChatStreamDeleteResult): string {
  const visibleItems = result.items.filter((item) => item.count > 0 || (item.unlinked ?? 0) > 0)
  if (visibleItems.length === 0) {
    return '未发现可清理的数据。'
  }

  return visibleItems
    .map((item) => {
      if (item.key === 'jargons') {
        return `${item.label} 删除 ${item.count} 条，解除关联 ${item.unlinked ?? 0} 条`
      }
      return `${item.label} ${item.count} 条`
    })
    .join('；')
}

function DeleteChatStreamDialog({
  chat,
  onDeleted,
  onOpenChange,
}: {
  chat: ChatStreamSettingsTarget | null
  onDeleted: (sessionId: string) => void
  onOpenChange: (open: boolean) => void
}) {
  const queryClient = useQueryClient()
  const { toast } = useToast()
  const [confirmText, setConfirmText] = useState('')
  const [progress, setProgress] = useState(0)
  const [stage, setStage] = useState('等待确认')
  const [deleteResult, setDeleteResult] = useState<ChatStreamDeleteResult | null>(null)
  const deleteMutation = useMutation({
    mutationFn: (sessionId: string) => deleteChatStream(sessionId),
  })
  const isDeleting = deleteMutation.isPending
  const canDelete =
    Boolean(chat?.session_id) &&
    confirmText.trim() === chat?.session_id &&
    !isDeleting &&
    !deleteResult

  useEffect(() => {
    if (!chat) {
      const frameId = window.requestAnimationFrame(() => {
        setConfirmText('')
        setProgress(0)
        setStage('等待确认')
        setDeleteResult(null)
      })

      return () => window.cancelAnimationFrame(frameId)
    }
  }, [chat])

  const handleDelete = async () => {
    if (!chat || !canDelete) {
      return
    }

    setDeleteResult(null)
    setProgress(12)
    setStage('提交删除请求')
    try {
      setProgress(35)
      setStage('清理聊天流关联数据')
      const result = await deleteMutation.mutateAsync(chat.session_id)
      setProgress(82)
      setStage('刷新聊天流列表')
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['chat-streams'] }),
        queryClient.removeQueries({ queryKey: ['chat-stream-detail', chat.session_id] }),
      ])
      setProgress(100)
      setStage('删除完成')
      setDeleteResult(result)
      onDeleted(chat.session_id)
      toast({
        title: '聊天流已删除',
        description: formatDeleteSummary(result),
      })
    } catch (error) {
      setProgress(0)
      setStage('删除失败')
      toast({
        title: '删除聊天流失败',
        description: error instanceof Error ? error.message : '请稍后重试',
        variant: 'destructive',
      })
    }
  }

  return (
    <Dialog open={chat !== null} onOpenChange={(open) => !isDeleting && onOpenChange(open)}>
      <DialogContent style={{ '--dialog-width': '38rem' } as CSSProperties}>
        <DialogHeader>
          <DialogTitle className="text-destructive flex items-center gap-2">
            <AlertTriangle className="h-5 w-5" />
            严肃确认：删除聊天流
          </DialogTitle>
          <DialogDescription>
            此操作不可撤销。删除后会清理所有与该 session_id 直接相关的数据。
          </DialogDescription>
        </DialogHeader>
        <DialogBody>
          <div className="space-y-4">
            <div className="border-destructive/40 bg-destructive/10 rounded-md border p-3 text-sm">
              <div className="text-destructive font-medium">将被清理的数据包括：</div>
              <ul className="text-muted-foreground mt-2 list-disc space-y-1 pl-5">
                <li>聊天流记录和该 session_id 下的所有消息。</li>
                <li>表达学习、黑话关联、工具调用记录。</li>
                <li>消息统计、高频词等以该聊天流为归属的数据。</li>
              </ul>
            </div>

            <div className="grid gap-2 text-sm">
              <div className="bg-muted/30 grid gap-1 rounded-md border p-3">
                <span className="text-muted-foreground">聊天流</span>
                <span className="font-medium">{chat?.display_name || '-'}</span>
                <span className="text-muted-foreground font-mono text-xs break-all">
                  {chat?.session_id || '-'}
                </span>
              </div>
              <Label htmlFor="delete-chat-session-confirm">请输入完整 session_id 以确认删除</Label>
              <Input
                id="delete-chat-session-confirm"
                value={confirmText}
                disabled={isDeleting}
                onChange={(event) => setConfirmText(event.target.value)}
                placeholder={chat?.session_id}
                className="font-mono text-xs"
              />
            </div>

            {(isDeleting || progress > 0 || deleteResult) && (
              <div className="space-y-2 rounded-md border p-3">
                <div className="flex items-center justify-between gap-3 text-sm">
                  <span className="font-medium">{stage}</span>
                  <span className="text-muted-foreground font-mono text-xs tabular-nums">
                    {progress}%
                  </span>
                </div>
                <Progress value={progress} className="h-2" />
                {deleteResult && (
                  <p className="text-muted-foreground text-xs">
                    {formatDeleteSummary(deleteResult)}
                  </p>
                )}
              </div>
            )}
          </div>
        </DialogBody>
        <DialogFooter>
          <Button variant="outline" disabled={isDeleting} onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button variant="destructive" disabled={!canDelete} onClick={() => void handleDelete()}>
            {isDeleting ? '删除中...' : '永久删除'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

export function ChatStreamSettingsDialog({
  chat,
  onOpenChange,
  onDeleted,
}: {
  chat: ChatStreamSettingsTarget | null
  onOpenChange: (open: boolean) => void
  onDeleted: (sessionId: string) => void
}) {
  const [deletingChat, setDeletingChat] = useState<ChatStreamSettingsTarget | null>(null)
  const detailQuery = useQuery({
    queryKey: ['chat-stream-detail', chat?.session_id],
    queryFn: () => getChatStreamDetail(chat?.session_id ?? ''),
    enabled: Boolean(chat?.session_id),
  })

  return (
    <>
      <Dialog open={chat !== null} onOpenChange={onOpenChange}>
        <DialogContent style={{ '--dialog-width': '44rem' } as CSSProperties}>
          <DialogHeader>
            <DialogTitle>
              {chat ? formatChatDisplayName(chat.display_name, chat.account_id) : '聊天流详情'}
            </DialogTitle>
          </DialogHeader>
          <DialogBody>
            <ChatDetailContent
              detail={detailQuery.data}
              loading={detailQuery.isLoading || detailQuery.isFetching}
              error={detailQuery.error}
            />
          </DialogBody>
          <DialogFooter className="border-t pt-4">
            <Button
              type="button"
              variant="destructive"
              disabled={!chat}
              onClick={() => {
                if (chat) {
                  setDeletingChat(chat)
                }
              }}
            >
              <Trash2 className="mr-2 h-4 w-4" />
              删除聊天流
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <DeleteChatStreamDialog
        chat={deletingChat}
        onDeleted={onDeleted}
        onOpenChange={(open) => !open && setDeletingChat(null)}
      />
    </>
  )
}
