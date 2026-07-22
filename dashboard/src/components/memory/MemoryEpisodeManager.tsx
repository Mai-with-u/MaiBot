import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ChevronDown, Play, RefreshCw, RotateCcw, Search } from 'lucide-react'

import { Alert, AlertDescription } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Textarea } from '@/components/ui/textarea'
import { ThinkingIllustration } from '@/components/ui/thinking-illustration'
import { useToast } from '@/hooks/use-toast'
import {
  getMemoryEpisode,
  getMemoryEpisodes,
  getMemoryEpisodeStatus,
  processMemoryEpisodePending,
  rebuildMemoryEpisodes,
  type MemoryEpisodeActionPayload,
  type MemoryEpisodeDetailPayload,
  type MemoryEpisodeItemPayload,
  type MemoryEpisodeParagraphPayload,
  type MemoryEpisodeStatusPayload,
} from '@/lib/memory-api'
import { cn } from '@/lib/utils'

function formatMemoryTime(timestamp?: number | null): string {
  if (!timestamp) {
    return '-'
  }
  const normalized = timestamp > 1_000_000_000_000 ? timestamp : timestamp * 1000
  const value = new Date(normalized)
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

function parseOptionalNumber(value: string): number | undefined {
  const trimmed = value.trim()
  if (!trimmed) {
    return undefined
  }
  const parsed = Number(trimmed)
  return Number.isFinite(parsed) ? parsed : undefined
}

function parsePositiveInt(value: string, maximum?: number): number | undefined {
  const parsed = Number(value)
  if (!Number.isInteger(parsed) || parsed <= 0 || (maximum !== undefined && parsed > maximum)) {
    return undefined
  }
  return parsed
}

function formatEpisodeIssueReason(reason: string): string {
  const labels: Record<string, string> = {
    superseded: '来源版本已更新',
    lease_lost_or_claim_mismatch: '任务租约已失效或被其他进程接管',
    not_claimed: '本轮未领取到该来源任务',
  }
  return labels[reason] ?? reason
}

function getEpisodeActionDescription(payload: MemoryEpisodeActionPayload): string {
  if (payload.detail) {
    return payload.detail
  }
  if (payload.error) {
    return payload.error
  }
  const firstIssue = payload.failures?.[0] ?? payload.unfinished_items?.[0]
  const issueReason = firstIssue?.error ?? firstIssue?.reason
  if (issueReason) {
    const source = firstIssue?.source ? `${firstIssue.source}: ` : ''
    return `${source}${formatEpisodeIssueReason(issueReason)}`
  }
  if (!payload.success) {
    return `失败 ${payload.failed ?? 0} 项，未完成 ${payload.unfinished ?? 0} 项`
  }
  if (payload.rebuilt !== undefined) {
    return `已重建 ${payload.rebuilt} 个来源`
  }
  return `已处理 ${payload.processed ?? 0} 项`
}

function getEpisodeId(item: MemoryEpisodeItemPayload | null | undefined): string {
  return String(item?.episode_id ?? item?.id ?? '')
}

function getEpisodeTitle(item: MemoryEpisodeItemPayload): string {
  return String(item.title ?? item.summary ?? item.content ?? getEpisodeId(item) ?? '未命名 Episode')
}

function formatTimestampInput(timestamp?: number): string {
  return typeof timestamp === 'number' && Number.isFinite(timestamp) ? String(Math.floor(timestamp)) : ''
}

function getEpisodeParagraphs(
  item: MemoryEpisodeItemPayload | MemoryEpisodeDetailPayload['episode'] | null | undefined,
): MemoryEpisodeParagraphPayload[] {
  const paragraphs = item?.paragraphs
  return Array.isArray(paragraphs) ? paragraphs : []
}

function getStatusCount(status: MemoryEpisodeStatusPayload | null, key: string): number {
  const counts = status?.counts
  if (counts && typeof counts[key] === 'number') {
    return counts[key]
  }
  const value = status?.[key]
  return typeof value === 'number' ? value : 0
}

export interface MemoryEpisodeManagerProps {
  initialEpisodeId?: string
  initialSource?: string
  initialTimeStart?: number
  initialTimeEnd?: number
}

export function MemoryEpisodeManager({
  initialEpisodeId = '',
  initialSource = '',
  initialTimeStart,
  initialTimeEnd,
}: MemoryEpisodeManagerProps) {
  const { toast } = useToast()
  const [query, setQuery] = useState('')
  const [source, setSource] = useState('')
  const [platform, setPlatform] = useState('')
  const [userId, setUserId] = useState('')
  const [personId, setPersonId] = useState('')
  const [showAdvancedPersonId, setShowAdvancedPersonId] = useState(false)
  const [showRawEpisodePayload, setShowRawEpisodePayload] = useState(false)
  const [timeStart, setTimeStart] = useState('')
  const [timeEnd, setTimeEnd] = useState('')
  const [limit, setLimit] = useState('20')
  const [items, setItems] = useState<MemoryEpisodeItemPayload[]>([])
  const [status, setStatus] = useState<MemoryEpisodeStatusPayload | null>(null)
  const [selectedId, setSelectedId] = useState('')
  const [detail, setDetail] = useState<MemoryEpisodeDetailPayload | null>(null)
  const [loading, setLoading] = useState(false)
  const [detailLoading, setDetailLoading] = useState(false)
  const [actionLoading, setActionLoading] = useState(false)
  const [rebuildSource, setRebuildSource] = useState('')
  const [rebuildSources, setRebuildSources] = useState('')
  const [rebuildAll, setRebuildAll] = useState(false)
  const [pendingLimit, setPendingLimit] = useState('20')
  const [pendingMaxRetry, setPendingMaxRetry] = useState('3')
  const initialLoadedRef = useRef(false)
  const initialTargetKeyRef = useRef('')
  const pendingInitialTargetRef = useRef<{ source: string; timeStart: string; timeEnd: string } | null>(null)

  const selectedEpisode = useMemo(() => detail?.episode ?? items.find((item) => getEpisodeId(item) === selectedId), [detail?.episode, items, selectedId])
  const selectedEpisodeParagraphs = useMemo(() => getEpisodeParagraphs(selectedEpisode), [selectedEpisode])
  const failedItems = Array.isArray(status?.failed) ? status.failed : []

  const loadStatus = useCallback(async () => {
    const payload = await getMemoryEpisodeStatus(parsePositiveInt(limit) ?? 20)
    setStatus(payload)
  }, [limit])

  const loadEpisodes = useCallback(async () => {
    setLoading(true)
    try {
      const directPersonId = showAdvancedPersonId ? personId.trim() : ''
      const [listPayload] = await Promise.all([
        getMemoryEpisodes({
          query: query.trim(),
          source: source.trim(),
          platform: platform.trim(),
          userId: userId.trim(),
          personId: directPersonId,
          limit: parsePositiveInt(limit) ?? 20,
          timeStart: parseOptionalNumber(timeStart),
          timeEnd: parseOptionalNumber(timeEnd),
        }),
        loadStatus(),
      ])
      const nextItems = listPayload.items ?? []
      setItems(nextItems)
      if (!selectedId && nextItems.length > 0) {
        setSelectedId(getEpisodeId(nextItems[0]))
      }
    } catch (error) {
      toast({
        title: '加载情节记忆失败',
        description: error instanceof Error ? error.message : String(error),
        variant: 'destructive',
      })
    } finally {
      setLoading(false)
    }
  }, [limit, loadStatus, personId, platform, query, selectedId, showAdvancedPersonId, source, timeEnd, timeStart, toast, userId])

  const loadDetail = useCallback(async (episodeId: string) => {
    if (!episodeId) {
      setDetail(null)
      return
    }
    setDetailLoading(true)
    try {
      const payload = await getMemoryEpisode(episodeId)
      setDetail(payload)
    } catch (error) {
      toast({
        title: '加载 Episode 详情失败',
        description: error instanceof Error ? error.message : String(error),
        variant: 'destructive',
      })
    } finally {
      setDetailLoading(false)
    }
  }, [toast])

  useEffect(() => {
    if (initialLoadedRef.current) {
      return
    }
    initialLoadedRef.current = true
    void loadEpisodes()
  }, [loadEpisodes])

  useEffect(() => {
    const targetKey = [initialEpisodeId, initialSource, initialTimeStart, initialTimeEnd].join('|')
    if (!initialEpisodeId && !initialSource && initialTimeStart === undefined && initialTimeEnd === undefined) {
      return
    }
    if (targetKey === initialTargetKeyRef.current) {
      return
    }
    initialTargetKeyRef.current = targetKey
    const nextTimeStart = formatTimestampInput(initialTimeStart)
    const nextTimeEnd = formatTimestampInput(initialTimeEnd)
    pendingInitialTargetRef.current = {
      source: initialSource,
      timeStart: nextTimeStart,
      timeEnd: nextTimeEnd,
    }
    if (initialSource) {
      setSource(initialSource)
    }
    if (initialTimeStart !== undefined) {
      setTimeStart(nextTimeStart)
    }
    if (initialTimeEnd !== undefined) {
      setTimeEnd(nextTimeEnd)
    }
    if (initialEpisodeId) {
      setSelectedId(initialEpisodeId)
    }
  }, [initialEpisodeId, initialSource, initialTimeEnd, initialTimeStart])

  useEffect(() => {
    const pendingTarget = pendingInitialTargetRef.current
    if (!pendingTarget) {
      return
    }
    if (
      (pendingTarget.source && source !== pendingTarget.source)
      || (pendingTarget.timeStart && timeStart !== pendingTarget.timeStart)
      || (pendingTarget.timeEnd && timeEnd !== pendingTarget.timeEnd)
    ) {
      return
    }
    pendingInitialTargetRef.current = null
    void loadEpisodes()
  }, [loadEpisodes, source, timeEnd, timeStart])

  useEffect(() => {
    if (selectedId) {
      void loadDetail(selectedId)
    }
  }, [loadDetail, selectedId])

  const submitRebuild = useCallback(async () => {
    if (rebuildAll && !window.confirm('确认重建全部可用来源的 Episode？这个操作可能耗时较长。')) {
      return
    }
    const sources = rebuildSources
      .split(',')
      .map((item) => item.trim())
      .filter(Boolean)
    setActionLoading(true)
    try {
      const payload = await rebuildMemoryEpisodes({
        source: rebuildSource.trim(),
        sources,
        all: rebuildAll,
      })
      toast({
        title: payload.success ? 'Episode 重建已提交' : 'Episode 重建失败',
        description: getEpisodeActionDescription(payload),
        variant: payload.success ? 'default' : 'destructive',
      })
      await loadEpisodes()
    } catch (error) {
      toast({
        title: 'Episode 重建失败',
        description: error instanceof Error ? error.message : String(error),
        variant: 'destructive',
      })
    } finally {
      setActionLoading(false)
    }
  }, [loadEpisodes, rebuildAll, rebuildSource, rebuildSources, toast])

  const submitProcessPending = useCallback(async () => {
    const limit = parsePositiveInt(pendingLimit, 200)
    const maxAttempts = parsePositiveInt(pendingMaxRetry, 20)
    if (limit === undefined || maxAttempts === undefined) {
      toast({
        title: '处理参数无效',
        description: '本次处理上限必须是1至200的整数，最大尝试次数必须是1至20的整数。',
        variant: 'destructive',
      })
      return
    }

    setActionLoading(true)
    try {
      const payload = await processMemoryEpisodePending({
        limit,
        max_retry: maxAttempts,
      })
      toast({
        title: payload.success ? '已处理来源重建任务' : '处理来源重建任务失败',
        description: getEpisodeActionDescription(payload),
        variant: payload.success ? 'default' : 'destructive',
      })
      await loadEpisodes()
    } catch (error) {
      toast({
        title: '处理来源重建任务失败',
        description: error instanceof Error ? error.message : String(error),
        variant: 'destructive',
      })
    } finally {
      setActionLoading(false)
    }
  }, [loadEpisodes, pendingLimit, pendingMaxRetry, toast])

  return (
    <div className="space-y-4">
      <div className="grid gap-4 xl:grid-cols-4">
        {[
          { label: '待重建', value: getStatusCount(status, 'pending') },
          { label: '运行中', value: getStatusCount(status, 'running') },
          { label: '已完成', value: getStatusCount(status, 'done') },
          { label: '失败来源', value: getStatusCount(status, 'failed') || failedItems.length },
        ].map((item) => (
          <Card key={item.label}>
            <CardHeader className="pb-3">
              <CardDescription>{item.label}</CardDescription>
              <CardTitle className="text-2xl">{item.value}</CardTitle>
            </CardHeader>
          </Card>
        ))}
      </div>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,0.95fr)_minmax(0,1.05fr)]">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Search className="h-4 w-4" />
              Episode 查询
            </CardTitle>
            <CardDescription>按平台账号、来源和时间范围查看情节记忆构建结果；person_id 查询放在高级入口。</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid gap-3 md:grid-cols-2">
              <div className="space-y-2">
                <Label htmlFor="episode-platform">平台</Label>
                <Input
                  id="episode-platform"
                  value={platform}
                  onChange={(event) => setPlatform(event.target.value)}
                  placeholder="例如 qq、telegram、webui"
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="episode-user-id">用户账号</Label>
                <Input id="episode-user-id" value={userId} onChange={(event) => setUserId(event.target.value)} placeholder="输入平台侧 user_id" />
              </div>
              <div className="space-y-2">
                <Label htmlFor="episode-query">关键词</Label>
                <Input id="episode-query" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索摘要或内容" />
              </div>
              <div className="space-y-2">
                <Label htmlFor="episode-source">来源</Label>
                <Input id="episode-source" value={source} onChange={(event) => setSource(event.target.value)} placeholder="chat_summary:..." />
              </div>
              <div className="space-y-2">
                <Label htmlFor="episode-limit">数量</Label>
                <Input id="episode-limit" type="number" value={limit} onChange={(event) => setLimit(event.target.value)} />
              </div>
              <div className="space-y-2">
                <Label htmlFor="episode-time-start">开始时间戳</Label>
                <Input id="episode-time-start" value={timeStart} onChange={(event) => setTimeStart(event.target.value)} placeholder="可选" />
              </div>
              <div className="space-y-2">
                <Label htmlFor="episode-time-end">结束时间戳</Label>
                <Input id="episode-time-end" value={timeEnd} onChange={(event) => setTimeEnd(event.target.value)} placeholder="可选" />
              </div>
            </div>

            <Collapsible open={showAdvancedPersonId} onOpenChange={setShowAdvancedPersonId} className="rounded-lg border bg-muted/10">
              <CollapsibleTrigger asChild>
                <Button variant="ghost" className="flex h-10 w-full justify-between px-3">
                  <span>高级查询</span>
                  <ChevronDown className={cn('h-4 w-4 transition-transform', showAdvancedPersonId && 'rotate-180')} />
                </Button>
              </CollapsibleTrigger>
              <CollapsibleContent className="space-y-2 border-t px-3 py-3">
                <Label htmlFor="episode-person">person_id</Label>
                <Input
                  id="episode-person"
                  value={personId}
                  onChange={(event) => setPersonId(event.target.value)}
                  placeholder="调试或后台管理时直接输入"
                />
              </CollapsibleContent>
            </Collapsible>

            <Button onClick={() => void loadEpisodes()} disabled={loading}>
              <RefreshCw className={cn('mr-2 h-4 w-4', loading && 'animate-spin')} />
              刷新 Episode
            </Button>

            <ScrollArea className="h-[420px]">
              <Table>
                <TableHeader className="sticky top-0 bg-background">
                  <TableRow>
                    <TableHead>Episode</TableHead>
                    <TableHead>来源</TableHead>
                    <TableHead>更新时间</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {items.length > 0 ? items.map((item) => {
                    const episodeId = getEpisodeId(item)
                    return (
                      <TableRow
                        key={episodeId || getEpisodeTitle(item)}
                        className={cn('cursor-pointer', selectedId === episodeId && 'bg-muted/60')}
                        onClick={() => setSelectedId(episodeId)}
                      >
                        <TableCell>
                          <div className="max-w-[280px] truncate font-medium">{getEpisodeTitle(item)}</div>
                          {item.person_name || item.person_id ? (
                            <div className="max-w-[280px] truncate text-xs text-muted-foreground">
                              {String(item.person_name || item.person_id)}
                              {item.person_name && item.person_id ? <span className="font-mono"> · {String(item.person_id)}</span> : null}
                            </div>
                          ) : null}
                          <div className="font-mono text-[11px] text-muted-foreground break-all">{episodeId || '-'}</div>
                        </TableCell>
                        <TableCell className="max-w-[180px] truncate">{String(item.source ?? '-')}</TableCell>
                        <TableCell>{formatMemoryTime(item.updated_at ?? item.created_at)}</TableCell>
                      </TableRow>
                    )
                  }) : (
                    <TableRow>
                      <TableCell colSpan={3} className="text-center text-muted-foreground">
                        {loading ? <ThinkingIllustration size="sm" className="mx-auto" /> : '没有匹配的 Episode'}
                      </TableCell>
                    </TableRow>
                  )}
                </TableBody>
              </Table>
            </ScrollArea>
          </CardContent>
        </Card>

        <div className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle>Episode 详情</CardTitle>
              <CardDescription>查看情节摘要、原始字段和关联段落。</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              {detailLoading ? (
                <div className="flex items-center gap-2 text-sm text-muted-foreground">
                  <ThinkingIllustration size="sm" />
                </div>
              ) : selectedEpisode ? (
                <>
                  <div className="flex flex-wrap gap-2">
                    <Badge variant="outline">{getEpisodeId(selectedEpisode) || '无 ID'}</Badge>
                    {selectedEpisode.source ? <Badge variant="secondary">{String(selectedEpisode.source)}</Badge> : null}
                    {selectedEpisode.person_name ? <Badge>{String(selectedEpisode.person_name)}</Badge> : null}
                    {selectedEpisode.person_id ? <Badge variant="outline">{String(selectedEpisode.person_id)}</Badge> : null}
                  </div>
                  <Textarea value={String(selectedEpisode.summary ?? selectedEpisode.content ?? '')} readOnly className="min-h-[120px]" />
                  <Collapsible open={showRawEpisodePayload} onOpenChange={setShowRawEpisodePayload} className="rounded-lg border bg-muted/10">
                    <CollapsibleTrigger asChild>
                      <Button variant="ghost" className="flex h-10 w-full justify-between px-3">
                        <span>原始响应 JSON</span>
                        <ChevronDown className={cn('h-4 w-4 transition-transform', showRawEpisodePayload && 'rotate-180')} />
                      </Button>
                    </CollapsibleTrigger>
                    <CollapsibleContent className="border-t">
                      <pre className="max-h-56 overflow-auto p-3 text-xs break-words whitespace-pre-wrap">
                        {JSON.stringify(selectedEpisode, null, 2)}
                      </pre>
                    </CollapsibleContent>
                  </Collapsible>
                  <div className="space-y-2">
                    <div className="text-sm font-medium">关联段落</div>
                    {selectedEpisodeParagraphs.length > 0 ? (
                      <ScrollArea className="h-[220px] rounded-lg border bg-background/60">
                        <div className="space-y-2 p-3">
                          {selectedEpisodeParagraphs.map((paragraph, index) => (
                            <div key={String(paragraph.hash ?? index)} className="rounded-lg border bg-muted/20 p-3">
                              <div className="font-mono text-[11px] text-muted-foreground break-all">{String(paragraph.hash ?? '-')}</div>
                              <div className="mt-2 text-sm break-words">{String(paragraph.preview ?? paragraph.content ?? '')}</div>
                            </div>
                          ))}
                        </div>
                      </ScrollArea>
                    ) : (
                      <div className="rounded-lg border border-dashed bg-muted/20 p-4 text-sm text-muted-foreground">当前详情没有段落明细。</div>
                    )}
                  </div>
                </>
              ) : (
                <div className="rounded-lg border border-dashed bg-muted/20 p-6 text-center text-sm text-muted-foreground">选择一个 Episode 查看详情。</div>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <RotateCcw className="h-4 w-4" />
                Episode 运维
              </CardTitle>
              <CardDescription>重新生成指定来源的情景记忆，或处理后台尚未生成的 Episode 任务。</CardDescription>
            </CardHeader>
            <CardContent className="space-y-5">
              {failedItems.length > 0 ? (
                <Alert>
                  <AlertDescription>
                    最近失败来源：{failedItems.slice(0, 3).map((item) => String(item.source ?? item.id ?? item.error ?? '未知')).join('、')}
                  </AlertDescription>
                </Alert>
              ) : null}

              <div className="space-y-3 rounded-lg border bg-muted/10 p-3">
                <div>
                  <div className="text-sm font-medium">重新生成来源 Episode</div>
                  <div className="mt-1 text-xs text-muted-foreground">
                    适用于导入内容变化、反馈纠错后，需要用来源下的段落替换旧 Episode 的场景。
                  </div>
                </div>
                <div className="grid gap-3 md:grid-cols-2">
                  <div className="space-y-2">
                    <Label htmlFor="episode-rebuild-source">来源 ID</Label>
                    <Input
                      id="episode-rebuild-source"
                      value={rebuildSource}
                      onChange={(event) => setRebuildSource(event.target.value)}
                      placeholder="例如 chat_summary:test-webui:coffee"
                    />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="episode-rebuild-sources">多个来源 ID</Label>
                    <Input
                      id="episode-rebuild-sources"
                      value={rebuildSources}
                      onChange={(event) => setRebuildSources(event.target.value)}
                      placeholder="用英文逗号分隔多个来源"
                    />
                  </div>
                </div>
                <label className="flex items-center gap-2 text-sm">
                  <input type="checkbox" checked={rebuildAll} onChange={(event) => setRebuildAll(event.target.checked)} />
                  重新生成全部可用来源
                </label>
                <Button onClick={() => void submitRebuild()} disabled={actionLoading}>
                  <RotateCcw className="mr-2 h-4 w-4" />
                  重新生成 Episode
                </Button>
              </div>

              <div className="space-y-3 rounded-lg border bg-muted/10 p-3">
                <div>
                  <div className="text-sm font-medium">处理来源重建任务</div>
                  <div className="mt-1 text-xs text-muted-foreground">
                    适用于后台已有待重建来源时，手动推进这些来源生成 Episode。
                  </div>
                </div>
                <div className="grid gap-3 md:grid-cols-[1fr_1fr_auto] md:items-end">
                  <div className="space-y-2">
                    <Label htmlFor="episode-pending-limit">本次处理上限</Label>
                    <Input
                      id="episode-pending-limit"
                      type="number"
                      min={1}
                      max={200}
                      step={1}
                      value={pendingLimit}
                      onChange={(event) => setPendingLimit(event.target.value)}
                    />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="episode-pending-retry">最大尝试次数（含首次）</Label>
                    <Input
                      id="episode-pending-retry"
                      type="number"
                      min={1}
                      max={20}
                      step={1}
                      value={pendingMaxRetry}
                      onChange={(event) => setPendingMaxRetry(event.target.value)}
                    />
                  </div>
                  <Button variant="outline" onClick={() => void submitProcessPending()} disabled={actionLoading}>
                    <Play className="mr-2 h-4 w-4" />
                    处理来源重建任务
                  </Button>
                </div>
              </div>
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  )
}
