import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  ArrowRight,
  CalendarClock,
  ChevronLeft,
  ChevronRight,
  ChevronsLeft,
  ChevronsRight,
  GitBranch,
  RefreshCw,
  Search,
} from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Slider } from '@/components/ui/slider'
import { useToast } from '@/hooks/use-toast'
import {
  getMemoryTimeline,
  type MemoryImportChatTargetPayload,
  type MemoryTimelineEventCategory,
  type MemoryTimelineEventPayload,
  type MemoryTimelineJumpTargetPayload,
  type MemoryTimelinePayload,
} from '@/lib/memory-api'
import { cn } from '@/lib/utils'

type TimelineQuickRange = '24h' | '7d' | '30d'
type TimelineTypeFilter = 'all' | MemoryTimelineEventCategory

interface MemoryTimelineManagerProps {
  chatTargets: MemoryImportChatTargetPayload[]
  initialChatId?: string
  initialTimeStart?: number
  initialTimeEnd?: number
  onJump: (target: MemoryTimelineJumpTargetPayload) => void
}

const TIMELINE_FETCH_LIMIT = 500
const DEFAULT_TIMELINE_PAGE_SIZE = 5
const TIMELINE_PAGE_SIZE_OPTIONS = [5, 10, 20]

const TYPE_FILTERS: Array<{ value: TimelineTypeFilter; label: string }> = [
  { value: 'all', label: '全部' },
  { value: 'paragraph', label: '段落' },
  { value: 'episode', label: 'Episode' },
  { value: 'profile', label: '人物画像' },
  { value: 'feedback', label: '反馈纠错' },
  { value: 'delete', label: '删除恢复' },
  { value: 'maintenance', label: '维护操作' },
]

function toDatetimeLocal(timestamp?: number | null): string {
  if (!timestamp) {
    return ''
  }
  const value = new Date(timestamp * 1000)
  if (Number.isNaN(value.getTime())) {
    return ''
  }
  const offset = value.getTimezoneOffset()
  const local = new Date(value.getTime() - offset * 60_000)
  return local.toISOString().slice(0, 16)
}

function fromDatetimeLocal(value: string): number | undefined {
  const trimmed = value.trim()
  if (!trimmed) {
    return undefined
  }
  const timestamp = new Date(trimmed).getTime()
  if (!Number.isFinite(timestamp)) {
    return undefined
  }
  return Math.floor(timestamp / 1000)
}

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
  return `${target.chat_name || target.chat_id} (${suffix}${platform})`
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
    episode_created: 'Episode 新增',
    episode_updated: 'Episode 更新',
    episode_rebuilt: 'Episode 重建',
    profile_updated: '画像更新',
    profile_override_set: '画像覆写',
    profile_override_deleted: '画像覆写删除',
    feedback_correction_applied: '反馈纠错',
    feedback_correction_rollback: '纠错回滚',
    delete_executed: '删除执行',
    delete_restored: '删除恢复',
    relation_reinforced: '关系强化',
    relation_frozen: '关系冻结',
    relation_protected: '关系保护',
    relation_restored: '关系恢复',
  }
  return labels[type] ?? type
}

function normalizeRange(minTime: number, maxTime: number, start?: number, end?: number): [number, number] {
  const safeMin = Math.floor(minTime)
  const safeMax = Math.max(safeMin + 60, Math.floor(maxTime))
  const nextStart = Math.min(Math.max(Math.floor(start ?? safeMin), safeMin), safeMax)
  const nextEnd = Math.min(Math.max(Math.floor(end ?? safeMax), nextStart), safeMax)
  return [nextStart, nextEnd]
}

function getVisiblePageNumbers(currentPage: number, totalPages: number): number[] {
  if (totalPages <= 5) {
    return Array.from({ length: totalPages }, (_, index) => index + 1)
  }
  const start = Math.min(Math.max(currentPage - 2, 1), totalPages - 4)
  return Array.from({ length: 5 }, (_, index) => start + index)
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
  const [rangeBounds, setRangeBounds] = useState<[number, number]>(() => {
    const now = Math.floor(Date.now() / 1000)
    return [now - 7 * 24 * 3600, now]
  })
  const [payload, setPayload] = useState<MemoryTimelinePayload | null>(null)
  const [loading, setLoading] = useState(false)
  const [currentPage, setCurrentPage] = useState(1)
  const [pageSize, setPageSize] = useState(DEFAULT_TIMELINE_PAGE_SIZE)
  const initialAppliedRef = useRef(false)
  const latestRequestRef = useRef(0)

  const selectedChat = useMemo(
    () => chatTargets.find((item) => item.chat_id === chatId) ?? null,
    [chatId, chatTargets],
  )
  const sliderValue = useMemo(
    () => normalizeRange(rangeBounds[0], rangeBounds[1], timeStart, timeEnd),
    [rangeBounds, timeEnd, timeStart],
  )
  const [sliderDraft, setSliderDraft] = useState<[number, number]>(sliderValue)
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
      const minTime = nextPayload.range.min_time
      const maxTime = nextPayload.range.max_time
      if (typeof minTime === 'number' && typeof maxTime === 'number') {
        setRangeBounds(normalizeRange(minTime, maxTime))
      }
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

  const handleSliderChange = useCallback((value: number[]) => {
    if (value.length < 2) {
      return
    }
    setSliderDraft(normalizeRange(rangeBounds[0], rangeBounds[1], value[0], value[1]))
  }, [rangeBounds])

  const handleSliderCommit = useCallback((value: number[]) => {
    if (value.length < 2) {
      return
    }
    setTimeStart(Math.floor(value[0]))
    setTimeEnd(Math.floor(value[1]))
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
  const pageNumbers = getVisiblePageNumbers(safeCurrentPage, totalPages)

  useEffect(() => {
    setCurrentPage(1)
  }, [payload])

  useEffect(() => {
    setSliderDraft(sliderValue)
  }, [sliderValue])

  useEffect(() => {
    setCurrentPage((page) => Math.min(Math.max(page, 1), totalPages))
  }, [totalPages])

  return (
    <div className="space-y-4">
      <div className="grid gap-4 xl:grid-cols-[minmax(0,0.8fr)_minmax(0,1.2fr)]">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <CalendarClock className="h-4 w-4" />
              审计范围
            </CardTitle>
            <CardDescription>选择真实聊天流与时间窗口，核对长期记忆对象的变动记录。</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-2">
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

            <div className="grid gap-2 sm:grid-cols-3">
              {[
                { value: '24h' as const, label: '最近 24 小时' },
                { value: '7d' as const, label: '最近 7 天' },
                { value: '30d' as const, label: '最近 30 天' },
              ].map((item) => (
                <Button key={item.value} type="button" variant="outline" onClick={() => applyQuickRange(item.value)}>
                  {item.label}
                </Button>
              ))}
            </div>

            <div className="grid gap-3 md:grid-cols-2">
              <div className="space-y-2">
                <Label htmlFor="timeline-time-start">开始时间</Label>
                <Input
                  id="timeline-time-start"
                  type="datetime-local"
                  value={toDatetimeLocal(timeStart)}
                  onChange={(event) => setTimeStart(fromDatetimeLocal(event.target.value))}
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="timeline-time-end">结束时间</Label>
                <Input
                  id="timeline-time-end"
                  type="datetime-local"
                  value={toDatetimeLocal(timeEnd)}
                  onChange={(event) => setTimeEnd(fromDatetimeLocal(event.target.value))}
                />
              </div>
            </div>

            <div className="space-y-3 rounded-lg border p-3">
              <div className="flex items-center justify-between gap-3 text-xs text-muted-foreground">
                <span>{formatMemoryTime(rangeBounds[0])}</span>
                <span>{formatMemoryTime(rangeBounds[1])}</span>
              </div>
              <Slider
                min={rangeBounds[0]}
                max={rangeBounds[1]}
                step={60}
                value={sliderDraft}
                onValueChange={handleSliderChange}
                onValueCommit={handleSliderCommit}
              />
              <div className="grid gap-2 text-xs text-muted-foreground sm:grid-cols-2">
                <span>窗口开始：{formatMemoryTime(sliderDraft[0])}</span>
                <span>窗口结束：{formatMemoryTime(sliderDraft[1])}</span>
              </div>
            </div>

            <div className="space-y-2">
              <Label>事件类型</Label>
              <Select value={typeFilter} onValueChange={(value) => setTypeFilter(value as TimelineTypeFilter)}>
                <SelectTrigger>
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

            <Button onClick={() => void loadTimeline()} disabled={loading || !chatId}>
              <RefreshCw className={cn('mr-2 h-4 w-4', loading && 'animate-spin')} />
              刷新时间线
            </Button>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <GitBranch className="h-4 w-4" />
              变动摘要
            </CardTitle>
            <CardDescription>
              {selectedChat ? selectedChat.chat_name : '未选择聊天流'} · {payload?.summary.total ?? 0} 条事件
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
              {TYPE_FILTERS.filter((item) => item.value !== 'all').map((item) => (
                <div key={item.value} className="rounded-lg border p-3">
                  <div className="text-xs text-muted-foreground">{item.label}</div>
                  <div className="mt-1 text-2xl font-semibold">{payload?.summary.by_type[item.value] ?? 0}</div>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Search className="h-4 w-4" />
            事件列表
          </CardTitle>
          <CardDescription>按时间倒序分页展示长期记忆审计事件。</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {events.length > 0 ? (
            <>
              <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
                <div className="flex flex-col gap-1 text-sm text-muted-foreground sm:flex-row sm:items-center sm:gap-4">
                  <span>
                    第 {safeCurrentPage} / {totalPages} 页，每页 {pageSize} 条
                  </span>
                  <span>
                    当前显示 {pageStartIndex + 1}-{pageEndIndex} / {events.length} 条
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  <Label htmlFor="timeline-page-size" className="whitespace-nowrap text-sm text-muted-foreground">
                    每页显示
                  </Label>
                  <Select value={String(pageSize)} onValueChange={handlePageSizeChange}>
                    <SelectTrigger id="timeline-page-size" className="h-9 w-[112px]" aria-label="每页显示条数">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {TIMELINE_PAGE_SIZE_OPTIONS.map((size) => (
                        <SelectItem key={size} value={String(size)}>
                          {size} 条
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              </div>

              <div className="space-y-3">
                {pageEvents.map((event) => (
                  <TimelineEventCard key={event.event_id} event={event} onJump={onJump} />
                ))}
              </div>

              <TimelinePagination
                currentPage={safeCurrentPage}
                pageNumbers={pageNumbers}
                totalPages={totalPages}
                onPageChange={setCurrentPage}
              />
            </>
          ) : (
            <div className="space-y-3">
              <div className="rounded-lg border border-dashed p-8 text-center text-sm text-muted-foreground">
                当前范围内没有可审计事件。
              </div>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}

function TimelinePagination({
  currentPage,
  pageNumbers,
  totalPages,
  onPageChange,
}: {
  currentPage: number
  pageNumbers: number[]
  totalPages: number
  onPageChange: (page: number) => void
}) {
  return (
    <div className="flex flex-col gap-3 border-t pt-4 sm:flex-row sm:items-center sm:justify-between">
      <div className="text-sm text-muted-foreground">共 {totalPages} 页</div>
      <div className="flex flex-wrap items-center gap-2">
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => onPageChange(1)}
          disabled={currentPage <= 1}
          aria-label="跳到第一页"
        >
          <ChevronsLeft className="h-4 w-4" />
        </Button>
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => onPageChange(Math.max(1, currentPage - 1))}
          disabled={currentPage <= 1}
        >
          <ChevronLeft className="mr-1 h-4 w-4" />
          上一页
        </Button>
        {pageNumbers.map((page) => (
          <Button
            key={page}
            type="button"
            variant={page === currentPage ? 'default' : 'outline'}
            size="sm"
            onClick={() => onPageChange(page)}
            aria-current={page === currentPage ? 'page' : undefined}
          >
            {page}
          </Button>
        ))}
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => onPageChange(Math.min(totalPages, currentPage + 1))}
          disabled={currentPage >= totalPages}
        >
          下一页
          <ChevronRight className="ml-1 h-4 w-4" />
        </Button>
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => onPageChange(totalPages)}
          disabled={currentPage >= totalPages}
          aria-label="跳到最后一页"
        >
          <ChevronsRight className="h-4 w-4" />
        </Button>
      </div>
    </div>
  )
}

function TimelineEventCard({
  event,
  onJump,
}: {
  event: MemoryTimelineEventPayload
  onJump: (target: MemoryTimelineJumpTargetPayload) => void
}) {
  return (
    <div className="rounded-lg border bg-card p-4">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
        <div className="min-w-0 space-y-2">
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="secondary">{getCategoryLabel(event.category)}</Badge>
            <Badge variant="outline">{getEventTypeLabel(event.event_type)}</Badge>
            <span className="text-xs text-muted-foreground">{formatMemoryTime(event.occurred_at)}</span>
            <span className="text-xs text-muted-foreground">{event.chat_name}</span>
          </div>
          <div>
            <div className="font-medium">{event.title}</div>
            <div className="mt-1 text-sm text-muted-foreground">{event.summary || '-'}</div>
          </div>
          <div className="grid gap-1 text-xs text-muted-foreground md:grid-cols-3">
            <span>影响对象：{event.object_count}</span>
            <span className="break-all">关键 ID：{event.key_id || '-'}</span>
            <span className="break-all">来源：{event.source || '-'}</span>
          </div>
          {event.attribution ? (
            <div className="text-xs text-muted-foreground">归因：{event.attribution}</div>
          ) : null}
        </div>
        <Button type="button" variant="outline" className="shrink-0" onClick={() => onJump(event.jump_target)}>
          <ArrowRight className="mr-2 h-4 w-4" />
          跳转
        </Button>
      </div>
    </div>
  )
}
