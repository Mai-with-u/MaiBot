import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle,
  Ban,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  ChevronsLeft,
  ChevronsRight,
  Edit3,
  Plus,
  RefreshCw,
  Save,
  Search,
  Trash2,
  Undo2,
  UserRound,
  UsersRound,
  X,
} from 'lucide-react'
import type { CSSProperties, PointerEvent, ReactNode } from 'react'
import { useEffect, useMemo, useRef, useState } from 'react'

import { Avatar, AvatarFallback, AvatarImage } from '@/components/ui/avatar'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { DashboardTabBar, DashboardTabTrigger } from '@/components/ui/dashboard-tabs'
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
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { Tabs } from '@/components/ui/tabs'
import { Textarea } from '@/components/ui/textarea'
import { useResolvedAvatarUrl } from '@/lib/avatar-url'
import { formatChatAccountLabel, formatChatDisplayName } from '@/lib/chat-display'
import {
  deleteChatStream,
  deleteChatStreamPrompt,
  deleteChatStreamTalkFrequency,
  getChatStreamDetail,
  getChatStreams,
  updateChatStreamLearning,
  updateChatStreamAdapterPolicy,
  updateChatStreamTalkFrequency,
  upsertChatStreamPrompt,
  type ChatConfigRule,
  type ChatAdapterStatus,
  type ChatLearningStatus,
  type ChatPromptRule,
  type ChatStream,
  type ChatStreamDeleteResult,
  type ChatTalkFrequencyRule,
  type ChatStreamDetail,
  type ChatStreamType,
} from '@/lib/chat-management-api'
import { getBotConfig, updateBotConfigSection } from '@/lib/config-api'
import { useToast } from '@/hooks/use-toast'
import { cn } from '@/lib/utils'

const PAGE_SIZE = 10
type ChatTypeFilter = 'all' | ChatStreamType
type ChatManagementView = 'groups' | 'streams'
type MutualGroupKind = 'expression' | 'jargon' | 'memory'
type LearningKind = 'expression' | 'jargon' | 'behavior'
const MUTUAL_GROUP_CHAT_RESULT_LIMIT = 50

const MUTUAL_GROUP_KIND_LABEL: Record<MutualGroupKind, string> = {
  expression: '表达',
  jargon: '黑话',
  memory: '记忆',
}

interface TargetItem {
  platform: string
  item_id: string
  rule_type?: ChatStreamType | string
  type?: ChatStreamType | string
}

interface ChatStreamGroupConfig {
  targets?: TargetItem[]
  expression_groups?: TargetItem[]
  jargon_groups?: TargetItem[]
}

function formatTimestamp(timestamp: number | null): string {
  if (!timestamp) {
    return '-'
  }

  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(timestamp * 1000))
}

function getChatTypeLabel(chat: ChatStream): string {
  return chat.chat_type === 'group' ? '群聊' : '私聊'
}

function getChatTypeText(chatType: ChatStreamType): string {
  return chatType === 'group' ? '群聊' : '私聊'
}

function getChatLogicalId(chat: ChatStream): string {
  return chat.target_id || (chat.chat_type === 'group' ? chat.group_id : chat.user_id) || '-'
}

function getTargetRuleType(target: TargetItem): ChatStreamType {
  return target.rule_type === 'private' || target.type === 'private' ? 'private' : 'group'
}

function normalizeTarget(target: unknown): TargetItem | null {
  if (!target || typeof target !== 'object') {
    return null
  }
  const rawTarget = target as Record<string, unknown>
  const platform = String(rawTarget.platform ?? '').trim()
  const itemId = String(rawTarget.item_id ?? '').trim()
  const rawRuleType = rawTarget.rule_type ?? rawTarget.type
  const ruleType = rawRuleType === 'private' ? 'private' : 'group'
  if (!platform || !itemId) {
    return null
  }
  return { platform, item_id: itemId, rule_type: ruleType }
}

function normalizeMutualGroups(value: unknown): ChatStreamGroupConfig[] {
  if (!Array.isArray(value)) {
    return []
  }
  return value.map((group) => {
    if (!group || typeof group !== 'object') {
      return { targets: [] }
    }
    const rawGroup = group as ChatStreamGroupConfig
    const rawTargets =
      rawGroup.targets ?? rawGroup.expression_groups ?? rawGroup.jargon_groups ?? []
    const targets = Array.isArray(rawTargets)
      ? rawTargets.map(normalizeTarget).filter((target): target is TargetItem => target !== null)
      : []
    return { targets }
  })
}

function serializeMutualGroups(groups: ChatStreamGroupConfig[]): ChatStreamGroupConfig[] {
  return groups.map((group) => ({
    targets: (group.targets ?? []).map((target) => ({
      platform: target.platform,
      item_id: target.item_id,
      rule_type: getTargetRuleType(target),
    })),
  }))
}

function targetKey(target: TargetItem): string {
  return `${target.platform}:${target.item_id}:${getTargetRuleType(target)}`
}

function targetLabel(target: TargetItem): string {
  return `${target.platform}:${target.item_id}:${getChatTypeText(getTargetRuleType(target))}`
}

function getTargetDisplayName(
  target: TargetItem,
  chatNameByTargetKey: Map<string, string>
): string {
  return chatNameByTargetKey.get(targetKey(target)) ?? '未找到聊天流'
}

function chatToTarget(chat: ChatStream): TargetItem {
  return {
    platform: chat.platform,
    item_id: getChatLogicalId(chat),
    rule_type: chat.chat_type,
  }
}

function HoverScrollText({
  className,
  maxChars,
  value,
}: {
  className?: string
  maxChars: number
  value: string | null | undefined
}) {
  const text = value || '-'
  const containerRef = useRef<HTMLSpanElement>(null)
  const textRef = useRef<HTMLSpanElement>(null)
  const [shouldScroll, setShouldScroll] = useState(false)
  const [scrollDurationMs, setScrollDurationMs] = useState(900)

  useEffect(() => {
    const containerElement = containerRef.current
    const textElement = textRef.current
    if (!containerElement || !textElement) return

    const updateOverflowState = () => {
      const overflowWidth = textElement.scrollWidth - containerElement.clientWidth
      const nextShouldScroll = overflowWidth > 1
      setShouldScroll((current) => (current === nextShouldScroll ? current : nextShouldScroll))
      setScrollDurationMs(Math.max(900, Math.min(2800, overflowWidth * 36)))
    }

    updateOverflowState()

    if (typeof ResizeObserver === 'undefined') {
      window.addEventListener('resize', updateOverflowState)
      return () => window.removeEventListener('resize', updateOverflowState)
    }

    const resizeObserver = new ResizeObserver(updateOverflowState)
    resizeObserver.observe(containerElement)
    resizeObserver.observe(textElement)
    return () => resizeObserver.disconnect()
  }, [maxChars, text])

  return (
    <span
      ref={containerRef}
      className={cn('group inline-block overflow-hidden align-bottom', className)}
      style={{ width: `${maxChars}ch` }}
      title={text}
    >
      <span
        ref={textRef}
        className={cn(
          'block max-w-full overflow-hidden text-ellipsis whitespace-nowrap',
          shouldScroll &&
            'group-hover:w-max group-hover:max-w-none group-hover:animate-[chat-management-text-scroll_1s_linear_infinite_alternate] group-hover:overflow-visible'
        )}
        style={
          {
            '--scroll-container-width': `${maxChars}ch`,
            animationDuration: `${scrollDurationMs}ms`,
          } as CSSProperties
        }
      >
        {text}
      </span>
    </span>
  )
}

function matchesSearch(chat: ChatStream, query: string): boolean {
  const normalizedQuery = query.trim().toLowerCase()
  if (!normalizedQuery) {
    return true
  }

  return [
    chat.id,
    chat.display_name,
    chat.session_id,
    chat.chat_type,
    chat.target_id,
    chat.platform,
    chat.account_id,
    chat.group_id,
    chat.group_name,
    chat.user_id,
    chat.user_nickname,
    chat.user_cardname,
  ]
    .filter(Boolean)
    .some((value) => String(value).toLowerCase().includes(normalizedQuery))
}

function matchesTypeFilter(chat: ChatStream, filter: ChatTypeFilter): boolean {
  return filter === 'all' || chat.chat_type === filter
}

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

function ChatStreamAvatar({ chat }: { chat: ChatStream }) {
  const targetType = chat.chat_type === 'group' ? 'group' : 'user'
  const targetId = chat.chat_type === 'group' ? chat.group_id : chat.user_id
  const avatarUrl = useResolvedAvatarUrl(chat.platform, targetId, targetType)
  const Icon = chat.chat_type === 'group' ? UsersRound : UserRound

  return (
    <Avatar className="border-border ring-background h-8 w-8 rounded-md border-2 ring-1">
      {avatarUrl && (
        <AvatarImage src={avatarUrl} alt={`${chat.display_name} 的头像`} className="object-cover" />
      )}
      <AvatarFallback className="text-muted-foreground rounded-md">
        <Icon className="h-4 w-4" />
      </AvatarFallback>
    </Avatar>
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
  const timePriority = rule.time_priority ?? 0

  return (
    <div
      className={cn(
        'rounded-md border px-3 py-2 text-sm',
        rule.is_effective
          ? 'border-primary bg-primary/10 text-foreground'
          : 'bg-muted text-muted-foreground'
      )}
    >
      <div className="flex flex-wrap items-center gap-2">
        {rule.is_effective && <Badge variant="default">生效中</Badge>}
        {!rule.is_effective && !rule.time_active && <Badge variant="outline">时间未命中</Badge>}
        <span className="font-mono text-xs">{targetLabel}</span>
        <span className="text-xs">
          优先级 {rule.target_priority}.{timePriority}
        </span>
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
  rule?: ChatTalkFrequencyRule
}) {
  const queryClient = useQueryClient()
  const { toast } = useToast()
  const isNewRule = !rule
  const [time, setTime] = useState(rule?.time ?? '*')
  const [value, setValue] = useState(() =>
    clampTalkFrequencyValue(rule?.value ?? detail.talk_frequency.effective_value)
  )

  useEffect(() => {
    const frameId = window.requestAnimationFrame(() => {
      setTime(rule?.time ?? '*')
      setValue(clampTalkFrequencyValue(rule?.value ?? detail.talk_frequency.effective_value))
    })

    return () => window.cancelAnimationFrame(frameId)
  }, [detail.session_id, detail.talk_frequency.effective_value, rule])

  const updateDetailCache = (updatedDetail: ChatStreamDetail) => {
    queryClient.setQueryData(['chat-stream-detail', detail.session_id], updatedDetail)
    void queryClient.invalidateQueries({ queryKey: ['chat-streams'] })
  }

  const saveMutation = useMutation({
    mutationFn: () =>
      updateChatStreamTalkFrequency(detail.session_id, {
        previous_time: rule?.time ?? null,
        time: time.trim(),
        value: clampTalkFrequencyValue(value),
      }),
    onSuccess: (updatedDetail) => {
      updateDetailCache(updatedDetail)
      toast({
        title: isNewRule ? '发言频率规则已新增' : '发言频率规则已保存',
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
    mutationFn: () => deleteChatStreamTalkFrequency(detail.session_id, rule?.time ?? ''),
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
        <Label className="text-xs">{isNewRule ? '新增时间段' : '时间段'}</Label>
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
          {saveMutation.isPending ? '保存中...' : isNewRule ? '新增' : '保存'}
        </Button>
        {!isNewRule && (
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
        )}
      </div>
    </div>
  )
}

function TalkFrequencyTimelineRule({
  detail,
  rule,
}: {
  detail: ChatStreamDetail
  rule?: ChatTalkFrequencyRule
}) {
  const queryClient = useQueryClient()
  const { toast } = useToast()
  const isNewRule = !rule
  const [time, setTime] = useState(rule?.time || '00:00-23:59')
  const [value, setValue] = useState(() =>
    clampTalkFrequencyValue(rule?.value ?? detail.talk_frequency.effective_value)
  )
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
      setTime(rule?.time || '00:00-23:59')
      setValue(clampTalkFrequencyValue(rule?.value ?? detail.talk_frequency.effective_value))
    })

    return () => window.cancelAnimationFrame(frameId)
  }, [detail.session_id, detail.talk_frequency.effective_value, rule])

  const updateDetailCache = (updatedDetail: ChatStreamDetail) => {
    queryClient.setQueryData(['chat-stream-detail', detail.session_id], updatedDetail)
    void queryClient.invalidateQueries({ queryKey: ['chat-streams'] })
  }

  const saveMutation = useMutation({
    mutationFn: () =>
      updateChatStreamTalkFrequency(detail.session_id, {
        previous_time: rule?.time ?? null,
        time: time.trim(),
        value: clampTalkFrequencyValue(value),
      }),
    onSuccess: (updatedDetail) => {
      updateDetailCache(updatedDetail)
      toast({
        title: isNewRule ? '发言频率规则已新增' : '发言频率规则已保存',
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
    mutationFn: () => deleteChatStreamTalkFrequency(detail.session_id, rule?.time ?? ''),
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
        <span className="w-10 text-right font-mono text-xs tabular-nums">{value.toFixed(2)}</span>
      </div>
      <div className="flex justify-end gap-2">
        <Button
          type="button"
          size="sm"
          disabled={saveMutation.isPending}
          onClick={() => saveMutation.mutate()}
        >
          {saveMutation.isPending ? '保存中...' : isNewRule ? '新增' : '保存'}
        </Button>
        {!isNewRule && (
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
        )}
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
      <div className="space-y-2">
        <div className="flex items-center gap-2 text-sm font-medium">
          <Plus className="h-4 w-4" />
          新增规则
        </div>
        <TalkFrequencyTimelineRule detail={detail} />
      </div>
    </div>
  )
}

function TalkFrequencyEditor({ detail }: { detail: ChatStreamDetail }) {
  const exactRules = useMemo(() => getExactTalkRules(detail), [detail])
  const [mode, setMode] = useState<TalkFrequencyEditMode>('timeline')

  return (
    <div className="bg-muted/10 space-y-3 rounded-md border p-3">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <div className="text-sm font-medium">当前聊天流规则</div>
          <div className="text-muted-foreground mt-1 text-xs">
            仅编辑 {detail.platform}:{detail.target_id}:{getChatTypeText(detail.chat_type)}{' '}
            的精确规则。
          </div>
        </div>
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
          <div className="space-y-2">
            <div className="flex items-center gap-2 text-sm font-medium">
              <Plus className="h-4 w-4" />
              新增规则
            </div>
            <TalkFrequencyRuleEditor detail={detail} />
          </div>
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

  useEffect(() => {
    setDraft(prompt.prompt)
  }, [prompt.prompt])

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

function MutualGroupsView({ chats }: { chats: ChatStream[] }) {
  const queryClient = useQueryClient()
  const { toast } = useToast()
  const [kind, setKind] = useState<MutualGroupKind>(() => {
    if (typeof window === 'undefined') {
      return 'expression'
    }
    const queryKind = new URLSearchParams(window.location.search).get('kind')
    return queryKind === 'memory' || queryKind === 'jargon' ? queryKind : 'expression'
  })
  const [addDialogGroupIndex, setAddDialogGroupIndex] = useState<number | null>(null)
  const [addDialogSearch, setAddDialogSearch] = useState('')
  const [selectedTargetKeys, setSelectedTargetKeys] = useState<string[]>([])
  const configQuery = useQuery({
    queryKey: ['chat-management-mutual-groups-config'],
    queryFn: () => getBotConfig(),
  })
  const sectionName = kind === 'memory' ? 'a_memorix' : kind
  const groupFieldName =
    kind === 'memory'
      ? 'shared_memory_groups'
      : kind === 'expression'
        ? 'expression_groups'
        : 'jargon_groups'
  const sectionData = (configQuery.data?.[sectionName] && typeof configQuery.data[sectionName] === 'object'
    ? configQuery.data[sectionName]
    : {}) as Record<string, unknown>
  const globalMemorySharingEnabled =
    kind === 'memory' && sectionData.global_memory_sharing_enabled === true
  const groups = useMemo(
    () => normalizeMutualGroups(sectionData[groupFieldName]),
    [groupFieldName, sectionData]
  )
  const addDialogGroup = addDialogGroupIndex === null ? null : (groups[addDialogGroupIndex] ?? null)
  const selectedTargetKeySet = useMemo(() => new Set(selectedTargetKeys), [selectedTargetKeys])
  const addDialogExistingKeySet = useMemo(
    () => new Set((addDialogGroup?.targets ?? []).map(targetKey)),
    [addDialogGroup]
  )
  const chatNameByTargetKey = useMemo(
    () => new Map(chats.map((chat) => [
      targetKey(chatToTarget(chat)),
      formatChatDisplayName(chat.display_name, chat.account_id),
    ])),
    [chats]
  )
  const addDialogChats = useMemo(() => {
    const keyword = addDialogSearch.trim().toLowerCase()
    return chats.filter((chat) => {
      const target = chatToTarget(chat)
      if (addDialogExistingKeySet.has(targetKey(target))) {
        return false
      }
      if (!keyword) {
        return true
      }
      return [
        chat.display_name,
        chat.account_id,
        chat.platform,
        getChatLogicalId(chat),
        chat.user_id,
        chat.group_id,
        chat.session_id,
        getChatTypeText(chat.chat_type),
      ]
        .join(' ')
        .toLowerCase()
        .includes(keyword)
    })
  }, [addDialogExistingKeySet, addDialogSearch, chats])
  const visibleAddDialogChats = addDialogChats.slice(0, MUTUAL_GROUP_CHAT_RESULT_LIMIT)
  const isAddDialogLimited = addDialogChats.length > visibleAddDialogChats.length

  const saveMutation = useMutation({
    mutationFn: (nextSectionData: Record<string, unknown>) =>
      updateBotConfigSection(sectionName, nextSectionData),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['chat-management-mutual-groups-config'] })
      toast({
        title: '共享组已保存',
        description: `${MUTUAL_GROUP_KIND_LABEL[kind]}共享组配置已更新。`,
      })
    },
    onError: (error) => {
      toast({
        title: '保存共享组失败',
        description: error instanceof Error ? error.message : '请稍后重试',
        variant: 'destructive',
      })
    },
  })

  const updateGroups = (nextGroups: ChatStreamGroupConfig[]) => {
    if (globalMemorySharingEnabled) {
      return
    }
    saveMutation.mutate({
      ...sectionData,
      [groupFieldName]: serializeMutualGroups(nextGroups),
    })
  }

  const createGroup = () => {
    if (globalMemorySharingEnabled) {
      return
    }
    updateGroups([...groups, { targets: [] }])
  }

  const openAddDialog = (groupIndex: number) => {
    if (globalMemorySharingEnabled) {
      return
    }
    setAddDialogGroupIndex(groupIndex)
    setAddDialogSearch('')
    setSelectedTargetKeys([])
  }

  const closeAddDialog = () => {
    setAddDialogGroupIndex(null)
    setAddDialogSearch('')
    setSelectedTargetKeys([])
  }

  const toggleAddDialogChat = (target: TargetItem) => {
    const key = targetKey(target)
    setSelectedTargetKeys((currentKeys) =>
      currentKeys.includes(key)
        ? currentKeys.filter((currentKey) => currentKey !== key)
        : [...currentKeys, key]
    )
  }

  const applySelectedChatsToGroup = () => {
    if (globalMemorySharingEnabled || addDialogGroupIndex === null || selectedTargetKeys.length === 0) {
      return
    }
    const selectedKeySet = new Set(selectedTargetKeys)
    const selectedTargets = chats
      .map(chatToTarget)
      .filter((target) => selectedKeySet.has(targetKey(target)))
    const nextGroups = groups.map((group, index) => {
      if (index !== addDialogGroupIndex) {
        return group
      }
      const targets = group.targets ?? []
      const existingKeys = new Set(targets.map(targetKey))
      const nextTargets = selectedTargets.filter((target) => !existingKeys.has(targetKey(target)))
      return { targets: [...targets, ...nextTargets] }
    })
    updateGroups(nextGroups)
    closeAddDialog()
  }

  const removeTarget = (groupIndex: number, targetIndex: number) => {
    if (globalMemorySharingEnabled) {
      return
    }
    updateGroups(
      groups.map((group, index) =>
        index === groupIndex
          ? {
              targets: (group.targets ?? []).filter(
                (_, memberIndex) => memberIndex !== targetIndex
              ),
            }
          : group
      )
    )
  }

  const deleteGroup = (groupIndex: number) => {
    if (globalMemorySharingEnabled) {
      return
    }
    updateGroups(groups.filter((_, index) => index !== groupIndex))
  }
  const editingDisabled = saveMutation.isPending || globalMemorySharingEnabled

  return (
    <section className="bg-background flex min-h-0 flex-1 flex-col overflow-hidden rounded-md border">
      <div className="flex shrink-0 flex-col gap-3 border-b p-4 lg:flex-row lg:items-end lg:justify-between">
        <div className="space-y-1">
          <h2 className="text-base font-semibold">共享组管理</h2>
          <p className="text-sm text-muted-foreground">
            管理表达、黑话和记忆的聊天流共享组。
          </p>
        </div>
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
          <div className="bg-background inline-flex rounded-md border p-1">
            {[
              ['expression', '表达'],
              ['jargon', '黑话'],
              ['memory', '记忆'],
            ].map(([value, label]) => (
              <Button
                key={value}
                type="button"
                variant={kind === value ? 'secondary' : 'ghost'}
                size="sm"
                className="h-8"
                onClick={() => setKind(value as MutualGroupKind)}
              >
                {label}
              </Button>
            ))}
          </div>
          <Button type="button" disabled={editingDisabled} onClick={createGroup}>
            <Plus className="mr-2 h-4 w-4" />
            新建共享组
          </Button>
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-auto p-4">
        {globalMemorySharingEnabled && (
          <div className="mb-3 rounded-md border bg-muted/30 px-3 py-2 text-sm text-muted-foreground">
            全局共享记忆已开启，记忆共享组暂不参与普通记忆检索范围控制。
          </div>
        )}
        {configQuery.isLoading ? (
          <div className="space-y-3">
            <Skeleton className="h-20" />
            <Skeleton className="h-20" />
          </div>
        ) : configQuery.error ? (
          <div className="border-destructive/40 text-destructive rounded-md border p-4 text-sm">
            加载共享组失败
          </div>
        ) : groups.length === 0 ? (
          <div className="text-muted-foreground rounded-md border border-dashed p-6 text-center text-sm">
            暂无{MUTUAL_GROUP_KIND_LABEL[kind]}共享组。
          </div>
        ) : (
          <div className="grid gap-3">
            {groups.map((group, groupIndex) => (
              <div key={groupIndex} className="rounded-md border p-3">
                <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
                  <div className="font-medium">共享组 {groupIndex + 1}</div>
                  <div className="flex items-center gap-2">
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      disabled={editingDisabled}
                      onClick={() => openAddDialog(groupIndex)}
                    >
                      <Plus className="mr-2 h-4 w-4" />
                      添加聊天
                    </Button>
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      className="text-destructive hover:text-destructive"
                      disabled={editingDisabled}
                      aria-label={`删除共享组 ${groupIndex + 1}`}
                      onClick={() => deleteGroup(groupIndex)}
                    >
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </div>
                </div>
                <div className="mt-3 flex flex-wrap gap-2">
                  {(group.targets ?? []).length === 0 ? (
                    <span className="text-muted-foreground text-sm">空共享组</span>
                  ) : (
                    (group.targets ?? []).map((target, targetIndex) => (
                      <Badge
                        key={`${targetKey(target)}:${targetIndex}`}
                        variant="outline"
                        className="gap-2"
                        title={targetLabel(target)}
                      >
                        <span className="max-w-48 truncate text-xs">
                          {getTargetDisplayName(target, chatNameByTargetKey)}
                        </span>
                        <button
                          type="button"
                          className="text-muted-foreground hover:text-destructive"
                          disabled={editingDisabled}
                          aria-label={`移除 ${getTargetDisplayName(target, chatNameByTargetKey)}`}
                          onClick={() => removeTarget(groupIndex, targetIndex)}
                        >
                          <X className="h-3 w-3" />
                        </button>
                      </Badge>
                    ))
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      <Dialog
        open={addDialogGroupIndex !== null}
        onOpenChange={(open) => !open && closeAddDialog()}
      >
        <DialogContent style={{ '--dialog-width': '42rem' } as CSSProperties}>
          <DialogHeader>
            <DialogTitle>添加聊天</DialogTitle>
            <DialogDescription>
              选择要加入共享组 {addDialogGroupIndex === null ? '' : addDialogGroupIndex + 1}{' '}
              的聊天流。
            </DialogDescription>
          </DialogHeader>
          <DialogBody>
            <div className="space-y-3">
              <Input
                value={addDialogSearch}
                onChange={(event) => setAddDialogSearch(event.target.value)}
                placeholder="搜索名称、平台、用户、群号或会话 ID"
              />
              <div className="max-h-[22rem] overflow-auto rounded-md border">
                {addDialogChats.length === 0 ? (
                  <div className="text-muted-foreground p-4 text-center text-sm">
                    没有可加入的聊天流
                  </div>
                ) : (
                  <div className="divide-y">
                    {visibleAddDialogChats.map((chat) => {
                      const target = chatToTarget(chat)
                      const key = targetKey(target)
                      const checked = selectedTargetKeySet.has(key)
                      return (
                        <label
                          key={chat.session_id}
                          className="hover:bg-muted/60 flex cursor-pointer items-center gap-3 px-3 py-2"
                        >
                          <Checkbox
                            checked={checked}
                            onCheckedChange={() => toggleAddDialogChat(target)}
                            aria-label={`选择 ${formatChatDisplayName(chat.display_name, chat.account_id)}`}
                          />
                          <div className="min-w-0 flex-1">
                            <div className="truncate text-sm font-medium">
                              {formatChatDisplayName(chat.display_name, chat.account_id)}
                            </div>
                            <div className="text-muted-foreground truncate font-mono text-xs">
                              {chat.platform}:{getChatLogicalId(chat)}
                            </div>
                          </div>
                          <Badge variant="outline">{getChatTypeText(chat.chat_type)}</Badge>
                        </label>
                      )
                    })}
                  </div>
                )}
              </div>
              {isAddDialogLimited && (
                <div className="text-xs text-muted-foreground">
                  仅显示前 {MUTUAL_GROUP_CHAT_RESULT_LIMIT} 个匹配项，请输入关键词缩小范围。
                </div>
              )}
            </div>
          </DialogBody>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={closeAddDialog}>
              取消
            </Button>
            <Button
              type="button"
              disabled={selectedTargetKeys.length === 0 || editingDisabled}
              onClick={applySelectedChatsToGroup}
            >
              加入 {selectedTargetKeys.length} 个聊天
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </section>
  )
}

function ConfigStatusRows({ detail }: { detail: ChatStreamDetail }) {
  const configRows = [
    { kind: 'expression' as const, title: '表达', status: detail.expression },
    { kind: 'jargon' as const, title: '黑话', status: detail.jargon },
    {
      kind: 'behavior' as const,
      title: '行为',
      status: detail.behavior,
    },
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
  return namePart
    .replace(/[-_]+/g, ' ')
    .replace(/\badapter\b/gi, '')
    .trim()
    .replace(/\b\w/g, (char) => char.toUpperCase()) || '适配器'
}

function getAdapterPolicyLabel(adapter: ChatAdapterStatus): string {
  if (adapter.policy.reason === 'matched_allow_override') {
    return '已允许当前聊天'
  }
  if (adapter.policy.reason === 'matched_deny_override') {
    return '已阻止当前聊天'
  }
  if (!adapter.policy.configured) {
    return '使用默认：允许'
  }
  if (adapter.policy.allowed) {
    return adapter.policy.list_type === 'blacklist' ? '黑名单未命中' : '白名单已放行'
  }
  return adapter.policy.list_type === 'blacklist' ? '黑名单已阻止' : '白名单未放行'
}

function getAdapterPolicyDescription(adapter: ChatAdapterStatus): string {
  if (adapter.policy.reason === 'matched_allow_override') {
    return '这条聊天已被单独加入允许规则。'
  }
  if (adapter.policy.reason === 'matched_deny_override') {
    return '这条聊天已被单独加入阻止规则。'
  }
  if (!adapter.policy.configured) {
    return '未设置统一规则，主程序默认放行。'
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

function getAdapterRouteDescription(adapter: ChatAdapterStatus): string {
  const routeState = adapter.routed ? '已接入当前聊天' : '未接入当前聊天'
  const directions = [
    adapter.receive_bound ? '收消息' : '',
    adapter.send_bound ? '发消息' : '',
  ].filter(Boolean)
  return `${routeState}${directions.length > 0 ? `，负责${directions.join('、')}` : ''}`
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
  const policyMutation = useMutation({
    mutationFn: (payload: { adapter_id: string; action: 'allow' | 'block' | 'inherit' }) =>
      updateChatStreamAdapterPolicy(detail.session_id, payload),
    onSuccess: (nextDetail) => {
      queryClient.setQueryData(['chat-stream-detail', detail.session_id], nextDetail)
      toast({ title: '适配器放行规则已保存' })
    },
    onError: (error) => {
      toast({
        title: '适配器放行规则保存失败',
        description: error instanceof Error ? error.message : '请稍后重试',
        variant: 'destructive',
      })
    },
  })
  const savingAdapterId = policyMutation.variables?.adapter_id

  const saveAdapterPolicy = (
    adapter: ChatAdapterStatus,
    action: 'allow' | 'block' | 'inherit'
  ) => {
    policyMutation.mutate({ adapter_id: adapter.adapter_id, action })
  }

  return (
    <section className="space-y-3 rounded-md border p-3">
      <div className="flex items-center justify-between gap-3">
        <div>
          <div className="font-medium">适配器放行</div>
          <div className="text-muted-foreground text-xs">设置当前聊天是否允许经过适配器收发消息</div>
        </div>
        <Badge variant="outline">{adapters.length} 个</Badge>
      </div>
      {adapters.length === 0 ? (
        <div className="text-muted-foreground rounded-md border border-dashed px-3 py-2 text-sm">
          当前没有运行中的适配器插件路由。
        </div>
      ) : (
        <div className="space-y-2">
          {adapters.map((adapter) => (
            <div
              key={adapter.adapter_id}
              className="bg-muted/20 grid gap-3 rounded-md border p-3 md:grid-cols-[minmax(0,1fr)_auto] md:items-center"
            >
              <div className="min-w-0 space-y-1">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-medium">{getAdapterDisplayName(adapter)}</span>
                  <Badge
                    variant={
                      !adapter.policy.configured
                        ? 'outline'
                        : adapter.policy.allowed
                          ? 'default'
                          : 'destructive'
                    }
                  >
                    {getAdapterPolicyLabel(adapter)}
                  </Badge>
                </div>
                <div className="text-muted-foreground text-sm">
                  {getAdapterPolicyDescription(adapter)}
                </div>
                <div className="text-muted-foreground text-xs">
                  {getAdapterRouteDescription(adapter)}
                  {adapter.account_id ? `；账号 ${adapter.account_id}` : ''}
                </div>
              </div>
              <div className="flex flex-wrap gap-2 md:justify-end">
                <Button
                  type="button"
                  size="sm"
                  variant={hasAdapterAllowOverride(adapter) ? 'secondary' : 'outline'}
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
                  {policyMutation.isPending && savingAdapterId === adapter.adapter_id ? '保存中' : '使用默认'}
                </Button>
              </div>
              <div className="text-muted-foreground min-w-0 text-xs break-all md:col-span-2">
                插件：{adapter.plugin_id || adapter.adapter_id}
                {adapter.gateway_name ? `；网关：${adapter.gateway_name}` : ''}
                {adapter.scope ? `；范围：${adapter.scope}` : ''}
              </div>
            </div>
          ))}
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
        <CompactDetailItem
          label="Session ID"
          value={<span className="font-mono text-xs font-normal">{detail.session_id}</span>}
        />
        <div className="grid gap-3 sm:grid-cols-3">
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
  chat: ChatStream | null
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
                <li>表达学习、黑话关联、工具调用记录、行为学习记录。</li>
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

export function ChatManagementPage() {
  const [activeView, setActiveView] = useState<ChatManagementView>(() => {
    if (typeof window === 'undefined') {
      return 'streams'
    }
    return new URLSearchParams(window.location.search).get('view') === 'groups' ? 'groups' : 'streams'
  })
  const [search, setSearch] = useState('')
  const [typeFilter, setTypeFilter] = useState<ChatTypeFilter>('all')
  const [page, setPage] = useState(1)
  const [selectedChat, setSelectedChat] = useState<ChatStream | null>(null)
  const [deletingChat, setDeletingChat] = useState<ChatStream | null>(null)
  const {
    data: chats = [],
    error,
    isFetching,
    isLoading,
    refetch,
  } = useQuery({
    queryKey: ['chat-streams'],
    queryFn: () => getChatStreams(),
  })
  const detailQuery = useQuery({
    queryKey: ['chat-stream-detail', selectedChat?.session_id],
    queryFn: () => getChatStreamDetail(selectedChat?.session_id ?? ''),
    enabled: Boolean(selectedChat?.session_id),
  })

  const filteredChats = useMemo(
    () =>
      chats.filter((chat) => matchesTypeFilter(chat, typeFilter) && matchesSearch(chat, search)),
    [chats, search, typeFilter]
  )
  const pageCount = Math.max(1, Math.ceil(filteredChats.length / PAGE_SIZE))
  const currentPage = Math.min(page, pageCount)
  const paginatedChats = useMemo(() => {
    const start = (currentPage - 1) * PAGE_SIZE
    return filteredChats.slice(start, start + PAGE_SIZE)
  }, [currentPage, filteredChats])
  const visibleStart = filteredChats.length === 0 ? 0 : (currentPage - 1) * PAGE_SIZE + 1
  const visibleEnd = Math.min(currentPage * PAGE_SIZE, filteredChats.length)
  const groupCount = chats.filter((chat) => chat.chat_type === 'group').length
  const privateCount = chats.length - groupCount

  useEffect(() => {
    const frameId = window.requestAnimationFrame(() => setPage(1))
    return () => window.cancelAnimationFrame(frameId)
  }, [search, typeFilter])

  useEffect(() => {
    if (page > pageCount) {
      const frameId = window.requestAnimationFrame(() => setPage(pageCount))
      return () => window.cancelAnimationFrame(frameId)
    }
  }, [page, pageCount])

  const handleChatDeleted = (sessionId: string) => {
    if (selectedChat?.session_id === sessionId) {
      setSelectedChat(null)
    }
  }

  return (
    <main className="flex h-full min-h-0 flex-col gap-4 overflow-hidden p-4 md:p-6">
      <header className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
        <div className="bg-background grid w-full grid-cols-3 border-2 text-sm sm:w-auto">
          <div className="px-4 py-2">
            <div className="text-muted-foreground">全部</div>
            <div className="text-lg leading-tight font-semibold">{chats.length}</div>
          </div>
          <div className="border-l-2 px-4 py-2">
            <div className="text-muted-foreground">群聊</div>
            <div className="text-lg leading-tight font-semibold">{groupCount}</div>
          </div>
          <div className="border-l-2 px-4 py-2">
            <div className="text-muted-foreground">私聊</div>
            <div className="text-lg leading-tight font-semibold">{privateCount}</div>
          </div>
        </div>
        <Tabs
          value={activeView}
          onValueChange={(value) => setActiveView(value as ChatManagementView)}
        >
          <DashboardTabBar className="bg-background h-10 w-full border-2 sm:w-fit">
            <DashboardTabTrigger value="streams" className="h-8 px-4">
              聊天流
            </DashboardTabTrigger>
            <DashboardTabTrigger value="groups" className="h-8 px-4">
              共享组
            </DashboardTabTrigger>
          </DashboardTabBar>
        </Tabs>
      </header>

      {activeView === 'groups' ? (
        <MutualGroupsView chats={chats} />
      ) : (
        <>
          <section className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div className="relative w-full sm:max-w-sm">
              <Search className="text-muted-foreground pointer-events-none absolute top-1/2 left-3 h-4 w-4 -translate-y-1/2" />
              <Input
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                placeholder="搜索名称、平台、用户、群号或会话 ID"
                className="pl-9"
              />
            </div>
            <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
              <Tabs
                value={typeFilter}
                onValueChange={(value) => setTypeFilter(value as ChatTypeFilter)}
              >
                <DashboardTabBar className="bg-background h-10 w-full border-2 sm:w-fit">
                  <DashboardTabTrigger value="all" className="h-8 px-4">
                    全部
                  </DashboardTabTrigger>
                  <DashboardTabTrigger value="group" className="h-8 px-4">
                    群聊
                  </DashboardTabTrigger>
                  <DashboardTabTrigger value="private" className="h-8 px-4">
                    私聊
                  </DashboardTabTrigger>
                </DashboardTabBar>
              </Tabs>
              <Button
                type="button"
                variant="outline"
                onClick={() => void refetch()}
                disabled={isFetching}
                className="shrink-0"
              >
                <RefreshCw className={cn('mr-2 h-4 w-4', isFetching && 'animate-spin')} />
                刷新
              </Button>
            </div>
          </section>

          <section className="bg-background flex min-h-0 flex-1 flex-col overflow-hidden rounded-md border">
            <div className="min-h-0 flex-1 overflow-auto">
              <Table className="table-fixed">
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-[7rem] px-3">聊天流</TableHead>
                    <TableHead className="w-[2rem] px-2">平台</TableHead>
                    <TableHead className="w-[5rem] px-2">ID</TableHead>
                    <TableHead className="w-[2.5rem] px-2">Type</TableHead>
                    <TableHead className="w-[3rem] px-2 text-right">消息数</TableHead>
                    <TableHead className="w-[3rem] px-2 text-right">表达数</TableHead>
                    <TableHead className="w-[3rem] px-2 text-right">黑话数</TableHead>
                    <TableHead className="w-[3rem] px-2">最后活跃</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {isLoading ? (
                    <TableRow>
                      <TableCell colSpan={8} className="text-muted-foreground h-28 text-center">
                        正在加载聊天流...
                      </TableCell>
                    </TableRow>
                  ) : error ? (
                    <TableRow>
                      <TableCell colSpan={8} className="text-destructive h-28 text-center">
                        加载聊天流失败
                      </TableCell>
                    </TableRow>
                  ) : filteredChats.length === 0 ? (
                    <TableRow>
                      <TableCell colSpan={8} className="text-muted-foreground h-28 text-center">
                        暂无匹配的聊天流
                      </TableCell>
                    </TableRow>
                  ) : (
                    paginatedChats.map((chat) => (
                      <TableRow
                        key={chat.session_id}
                        role="button"
                        tabIndex={0}
                        aria-label={`查看 ${formatChatDisplayName(chat.display_name, chat.account_id)} 详情`}
                        className="cursor-pointer hover:bg-primary/10 focus-visible:bg-primary/10 focus-visible:outline-primary/60 focus-visible:outline-2 focus-visible:outline-offset-[-2px]"
                        onClick={() => setSelectedChat(chat)}
                        onKeyDown={(event) => {
                          if (event.key === 'Enter' || event.key === ' ') {
                            event.preventDefault()
                            setSelectedChat(chat)
                          }
                        }}
                      >
                        <TableCell className="px-3">
                          <div className="flex min-w-0 items-center gap-3">
                            <ChatStreamAvatar chat={chat} />
                            <div className="min-w-0">
                              <HoverScrollText
                                className="font-medium"
                                maxChars={12}
                                value={chat.display_name}
                              />
                              {chat.account_id && (
                                <div className="text-muted-foreground truncate font-mono text-xs">
                                  {formatChatAccountLabel(chat.account_id)}
                                </div>
                              )}
                            </div>
                          </div>
                        </TableCell>
                        <TableCell className="text-muted-foreground px-2 font-mono text-xs">
                          <HoverScrollText maxChars={4} value={chat.platform} />
                        </TableCell>
                        <TableCell className="text-muted-foreground px-2 font-mono text-xs">
                          <HoverScrollText maxChars={12} value={getChatLogicalId(chat)} />
                        </TableCell>
                        <TableCell className="px-2">
                          <Badge variant="outline">{getChatTypeLabel(chat)}</Badge>
                        </TableCell>
                        <TableCell className="px-2 text-right tabular-nums">
                          {chat.message_count}
                        </TableCell>
                        <TableCell className="px-2 text-right tabular-nums">
                          {chat.expression_count}
                        </TableCell>
                        <TableCell className="px-2 text-right tabular-nums">
                          {chat.jargon_count}
                        </TableCell>
                        <TableCell className="text-muted-foreground px-2">
                          {formatTimestamp(chat.last_active_at)}
                        </TableCell>
                      </TableRow>
                    ))
                  )}
                </TableBody>
              </Table>
            </div>
            <div className="text-muted-foreground flex shrink-0 flex-col gap-2 border-t px-3 py-2 text-xs sm:flex-row sm:items-center sm:justify-between">
              <div className="min-w-0">
                显示 {visibleStart}-{visibleEnd} / {filteredChats.length} 个聊天流
              </div>
              <div className="flex max-w-full min-w-0 items-center gap-1 overflow-x-auto sm:justify-end">
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  className="h-7 w-7 shrink-0"
                  disabled={currentPage <= 1}
                  aria-label="第一页"
                  onClick={() => setPage(1)}
                >
                  <ChevronsLeft className="h-3.5 w-3.5" />
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  className="h-7 w-7 shrink-0"
                  disabled={currentPage <= 1}
                  aria-label="上一页"
                  onClick={() => setPage((value) => Math.max(1, value - 1))}
                >
                  <ChevronLeft className="h-3.5 w-3.5" />
                </Button>
                <span className="min-w-16 shrink-0 px-1 text-center tabular-nums">
                  {currentPage} / {pageCount}
                </span>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  className="h-7 w-7 shrink-0"
                  disabled={currentPage >= pageCount}
                  aria-label="下一页"
                  onClick={() => setPage((value) => Math.min(pageCount, value + 1))}
                >
                  <ChevronRight className="h-3.5 w-3.5" />
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  className="h-7 w-7 shrink-0"
                  disabled={currentPage >= pageCount}
                  aria-label="最后一页"
                  onClick={() => setPage(pageCount)}
                >
                  <ChevronsRight className="h-3.5 w-3.5" />
                </Button>
              </div>
            </div>
          </section>
        </>
      )}

      <Dialog open={selectedChat !== null} onOpenChange={(open) => !open && setSelectedChat(null)}>
        <DialogContent style={{ '--dialog-width': '44rem' } as CSSProperties}>
          <DialogHeader>
            <DialogTitle>
              {selectedChat
                ? formatChatDisplayName(selectedChat.display_name, selectedChat.account_id)
                : '聊天流详情'}
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
              disabled={!selectedChat}
              onClick={() => {
                if (selectedChat) {
                  setDeletingChat(selectedChat)
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
        onDeleted={handleChatDeleted}
        onOpenChange={(open) => !open && setDeletingChat(null)}
      />
    </main>
  )
}
