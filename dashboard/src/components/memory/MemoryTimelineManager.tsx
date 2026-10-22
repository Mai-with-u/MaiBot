import { DataPagination } from '@/components/ui/data-pagination'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Minus,
  Plus,
  RefreshCw,
} from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { DateTimePicker } from '@/components/ui/date-time-picker'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { useToast } from '@/hooks/use-toast'
import { formatChatDisplayName } from '@/lib/chat-display'
import {
  getMemoryTimeline,
  type MemoryImportChatTargetPayload,
  type MemoryTimelineEventCategory,
  type MemoryTimelineEventPayload,
  type MemoryTimelineJumpTargetPayload,
  type MemoryTimelinePayload,
} from '@/lib/memory-api'
import { cn } from '@/lib/utils'

import { MemoryTimelineEventDialog } from './MemoryTimelineEventDialog'

type TimelineQuickRange = '24h' | '7d' | '30d'
type TimelineTypeFilter = 'all' | MemoryTimelineEventCategory

interface MemoryTimelineManagerProps {
  chatTargets: MemoryImportChatTargetPayload[]
  initialChatId?: string
  initialTimeStart?: number
  initialTimeEnd?: number
  onJump: (target: MemoryTimelineJumpTargetPayload) => void
}

// 单次拉取量；页面按 30/100 条分页展示，200 已足够回溯，避免撑满后端扫描上限
const TIMELINE_FETCH_LIMIT = 200
const DEFAULT_TIMELINE_PAGE_SIZE = 30
const TIMELINE_PAGE_SIZE_OPTIONS = [30, 100]

const TYPE_FILTERS: Array<{ value: TimelineTypeFilter; label: string }> = [
  { value: 'all', label: '全部' },
  { value: 'paragraph', label: '段落' },
  { value: 'episode', label: '记忆片' },
  { value: 'profile', label: '人物画像' },
  { value: 'feedback', label: '反馈纠错' },
  { value: 'delete', label: '删除恢复' },
  { value: 'maintenance', label: '维护操作' },
]

function formatMemoryTime(timestamp?: number | null): string {
  if (!timestamp) {
    return '-'
  }
  const value = new Date(timestamp * 1000)
  if (Number.isNaN(value.getTime())) {
    return '-'
  }
  return value.toLocaleString('zh-CN', {
    hour12: false,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function formatChatTarget(target: MemoryImportChatTargetPayload): string {
  const suffix = target.is_group ? '群聊' : '私聊'
  const platform = target.platform ? ` · ${target.platform}` : ''
  return `${formatChatDisplayName(target.chat_name || target.chat_id, target.account_id)} (${suffix}${platform})`
}

function getDefaultTimelineChatId(targets: MemoryImportChatTargetPayload[]): string {
  const realChat = targets.find((target) => String(target.platform ?? '').trim().toLowerCase() !== 'webui')
  return realChat?.chat_id ?? targets[0]?.chat_id ?? ''
}

function getQuickRangeSeconds(range: TimelineQuickRange): number {
  switch (range) {
    case '24h':
      return 24 * 3600
    case '30d':
      return 30 * 24 * 3600
    case '7d':
    default:
      return 7 * 24 * 3600
  }
}

function getCategoryLabel(category: string): string {
  return TYPE_FILTERS.find((item) => item.value === category)?.label ?? category
}

function getEventTypeLabel(type: string): string {
  const labels: Record<string, string> = {
    paragraph_created: '段落新增',
    paragraph_updated: '段落更新',
    paragraph_deleted: '段落删除',
    paragraph_restored: '段落恢复',
    episode_created: '记忆片新增',
    episode_updated: '记忆片更新',
    episode_rebuilt: '记忆片重建',
    profile_updated: '画像更新',
    profile_override_set: '画像覆写',
    profile_override_deleted: '画像覆写删除',
    feedback_correction_applied: '反馈纠错',
    feedback_correction_rollback: '纠错回滚',
    delete_executed: '删除执行',
    delete_restored: '删除恢复',
    relation_reinforced: '关系强化',
    relation_accessed: '使用强化',
    relation_frozen: '关系冻结',
    relation_protected: '关系保护',
    relation_restored: '关系恢复',
  }
  return labels[type] ?? type
}

function getTimelineCategoryLabel(event: MemoryTimelineEventPayload): string {
  if (event.category !== 'maintenance') return getCategoryLabel(event.category)
  const kind = event.metadata?.reinforcement_kind
  if (kind === 'added') return '新增'
  if (kind === 'access') return '使用'
  if (kind === 'manual') return '手动'
  if (kind === 'unknown') return '未记录'
  return getEventTypeLabel(event.event_type)
}

export function MemoryTimelineManager({
  chatTargets,
  initialChatId,
  initialTimeStart,
  initialTimeEnd,
  onJump,
}: MemoryTimelineManagerProps) {
  const { toast } = useToast()
  const [chatId, setChatId] = useState(initialChatId ?? '')
  const [typeFilter, setTypeFilter] = useState<TimelineTypeFilter>('all')
  const [timeStart, setTimeStart] = useState<number | undefined>(initialTimeStart)
  const [timeEnd, setTimeEnd] = useState<number | undefined>(initialTimeEnd)
  const [payload, setPayload] = useState<MemoryTimelinePayload | null>(null)
  const [loading, setLoading] = useState(false)
  const [currentPage, setCurrentPage] = useState(1)
  const [pageSize, setPageSize] = useState(DEFAULT_TIMELINE_PAGE_SIZE)
  const [selectedEvent, setSelectedEvent] = useState<MemoryTimelineEventPayload | null>(null)
  const initialAppliedRef = useRef(false)
  const latestRequestRef = useRef(0)

  const filteredTypes = useMemo(() => (typeFilter === 'all' ? [] : [typeFilter]), [typeFilter])

  useEffect(() => {
    if (chatId || chatTargets.length === 0) {
      return
    }
    setChatId(getDefaultTimelineChatId(chatTargets))
  }, [chatId, chatTargets])

  useEffect(() => {
    if (initialAppliedRef.current) {
      return
    }
    if (initialChatId) {
      setChatId(initialChatId)
    }
    if (initialTimeStart) {
      setTimeStart(initialTimeStart)
    }
    if (initialTimeEnd) {
      setTimeEnd(initialTimeEnd)
    }
    initialAppliedRef.current = true
  }, [initialChatId, initialTimeEnd, initialTimeStart])

  const loadTimeline = useCallback(async () => {
    if (!chatId) {
      latestRequestRef.current += 1
      setPayload(null)
      setLoading(false)
      return
    }
    const requestId = latestRequestRef.current + 1
    latestRequestRef.current = requestId
    setLoading(true)
    try {
      const nextPayload = await getMemoryTimeline({
        chatId,
        timeStart,
        timeEnd,
        types: filteredTypes,
        limit: TIMELINE_FETCH_LIMIT,
      })
      if (requestId !== latestRequestRef.current) {
        return
      }
      setPayload(nextPayload)
    } catch (error) {
      if (requestId !== latestRequestRef.current) {
        return
      }
      toast({
        title: '加载审计时间线失败',
        description: error instanceof Error ? error.message : String(error),
        variant: 'destructive',
      })
    } finally {
      if (requestId === latestRequestRef.current) {
        setLoading(false)
      }
    }
  }, [chatId, filteredTypes, timeEnd, timeStart, toast])

  useEffect(() => {
    void loadTimeline()
  }, [loadTimeline])

  const applyQuickRange = useCallback((range: TimelineQuickRange) => {
    const end = Math.floor(Date.now() / 1000)
    setTimeStart(end - getQuickRangeSeconds(range))
    setTimeEnd(end)
  }, [])

  const handlePageSizeChange = useCallback((value: string) => {
    setPageSize(Number(value))
    setCurrentPage(1)
  }, [])

  const events = payload?.items ?? []
  const totalPages = Math.max(1, Math.ceil(events.length / pageSize))
  const safeCurrentPage = Math.min(currentPage, totalPages)
  const pageStartIndex = (safeCurrentPage - 1) * pageSize
  const pageEndIndex = Math.min(pageStartIndex + pageSize, events.length)
  const pageEvents = events.slice(pageStartIndex, pageEndIndex)

  useEffect(() => {
    setCurrentPage(1)
  }, [payload])

  useEffect(() => {
    setCurrentPage((page) => Math.min(Math.max(page, 1), totalPages))
  }, [totalPages])

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
      <Card data-memory-timeline-panel="true" className="flex min-h-0 flex-1 flex-col overflow-hidden">
        <CardContent data-memory-timeline-filters="true" className="@container shrink-0 border-b lg:max-h-[45%] lg:overflow-y-auto">
          <div className="flex flex-wrap items-end gap-2 @[70rem]:flex-nowrap">
            <div className="w-full min-w-0 space-y-1 sm:w-56 @[70rem]:w-auto @[70rem]:flex-1">
              <Label>聊天流</Label>
              <Select value={chatId} onValueChange={setChatId}>
                <SelectTrigger>
                  <SelectValue placeholder="选择聊天流" />
                </SelectTrigger>
                <SelectContent>
                  {chatTargets.map((target) => (
                    <SelectItem key={target.chat_id} value={target.chat_id}>
                      {formatChatTarget(target)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="w-28 shrink-0 space-y-1">
              <Label htmlFor="timeline-type-filter">事件类型</Label>
              <Select value={typeFilter} onValueChange={(value) => setTypeFilter(value as TimelineTypeFilter)}>
                <SelectTrigger id="timeline-type-filter">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {TYPE_FILTERS.map((item) => (
                    <SelectItem key={item.value} value={item.value}>
                      {item.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div
              role="group"
              aria-label="时间范围"
              className="flex w-full flex-wrap items-end gap-2 @[70rem]:w-auto @[70rem]:shrink-0 @[70rem]:flex-nowrap"
            >
              <div className="w-full min-w-0 space-y-1 sm:w-52 sm:shrink-0">
                <Label htmlFor="timeline-time-start">开始时间</Label>
                <DateTimePicker
                  id="timeline-time-start"
                  label="开始时间"
                  value={timeStart}
                  onChange={setTimeStart}
                />
              </div>
              <div className="w-full min-w-0 space-y-1 sm:w-52 sm:shrink-0">
                <Label htmlFor="timeline-time-end">结束时间</Label>
                <DateTimePicker
                  id="timeline-time-end"
                  label="结束时间"
                  value={timeEnd}
                  onChange={setTimeEnd}
                />
              </div>
              {[
                { value: '24h' as const, label: '最近 24 小时' },
                { value: '7d' as const, label: '最近 7 天' },
                { value: '30d' as const, label: '最近 30 天' },
              ].map((item) => (
                <Button
                  key={item.value}
                  type="button"
                  variant="outline"
                  size="sm"
                  className="shrink-0 px-3"
                  onClick={() => applyQuickRange(item.value)}
                >
                  {item.label}
                </Button>
              ))}
              <Button
                size="icon"
                className="h-8 w-8 shrink-0"
                aria-label="刷新时间线"
                title="刷新时间线"
                onClick={() => void loadTimeline()}
                disabled={loading || !chatId}
              >
                <RefreshCw className={cn('h-4 w-4', loading && 'animate-spin')} />
              </Button>
            </div>
          </div>
        </CardContent>
        <CardContent data-memory-timeline-browser="true" className="flex h-[65dvh] min-h-0 flex-none flex-col gap-2 overflow-hidden lg:h-auto lg:flex-1">
          {events.length > 0 ? (
            <>
              <div key={safeCurrentPage} data-memory-timeline-list="true" className="min-h-0 flex-1 space-y-3 overflow-y-auto overscroll-contain">
                {pageEvents.map((event) => (
                  <TimelineEventCard key={event.event_id} event={event} onSelect={setSelectedEvent} />
                ))}
              </div>

              <DataPagination total={events.length} page={safeCurrentPage} pageSize={pageSize}
                onPageChange={setCurrentPage} disabled={loading}
                pageSizeOptions={TIMELINE_PAGE_SIZE_OPTIONS}
                onPageSizeChange={(size) => handlePageSizeChange(String(size))} />
            </>
          ) : (
            <div className="min-h-0 flex-1 overflow-y-auto">
              <div data-memory-timeline-list="true" className="rounded-lg border border-dashed p-8 text-center text-sm text-muted-foreground">
                当前范围内没有可审计事件。
              </div>
            </div>
          )}
        </CardContent>
      </Card>
      <MemoryTimelineEventDialog
        event={selectedEvent}
        categoryLabel={selectedEvent ? getTimelineCategoryLabel(selectedEvent) : ''}
        timeLabel={selectedEvent ? formatMemoryTime(selectedEvent.occurred_at) : ''}
        onClose={() => setSelectedEvent(null)}
        onJump={onJump}
      />
    </div>
  )
}

function TimelineEventCard({
  event,
  onSelect,
}: {
  event: MemoryTimelineEventPayload
  onSelect: (event: MemoryTimelineEventPayload) => void
}) {
  const showOperationIcon = event.category === 'paragraph' || event.category === 'episode' || event.category === 'profile'
  const title = event.category === 'episode'
    ? event.title.replace(/^(?:Episode|记忆片)\s*(?:新增|更新|重建)\s*[:：]\s*/, '')
    : event.category === 'paragraph'
      ? event.title.replace(/^段落(?:新增|更新|被标记删除|删除|恢复)(?:\s*[:：]\s*|$)/, '')
    : event.title
  const removed = event.event_type === 'profile_override_deleted' || event.event_type === 'paragraph_deleted'
  const updated = event.event_type === 'paragraph_updated'
  const EventIcon = removed ? Minus : updated ? RefreshCw : Plus

  return (
    <button
      type="button"
      data-memory-timeline-event="true"
      className="hover:bg-muted/40 focus-visible:ring-ring w-full cursor-pointer rounded-lg border bg-card px-3 py-2 text-left transition-colors focus-visible:ring-2 focus-visible:outline-none"
      onClick={() => onSelect(event)}
    >
      <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
        <div className="min-w-0 flex-1 space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            {showOperationIcon && (
              <EventIcon
                className={cn(
                  'h-4 w-4 shrink-0',
                  removed ? 'text-red-600 dark:text-red-400' : updated ? 'text-amber-600 dark:text-amber-400' : 'text-green-600 dark:text-green-400'
                )}
                aria-label={getEventTypeLabel(event.event_type)}
              />
            )}
            <Badge variant={event.category === 'maintenance' ? 'outline' : 'secondary'}>
              {getTimelineCategoryLabel(event)}
            </Badge>
            {title && (
              <span className="min-w-0 font-medium break-words">
                {title}
              </span>
            )}
            <span className="text-xs text-muted-foreground">{formatMemoryTime(event.occurred_at)}</span>
          </div>
          <div className="text-sm text-muted-foreground">{event.summary || '-'}</div>
        </div>
      </div>
    </button>
  )
}
