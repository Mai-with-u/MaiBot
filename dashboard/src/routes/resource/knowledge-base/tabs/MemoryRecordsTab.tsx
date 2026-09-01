import { useMemo, useState } from 'react'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle,
  CheckCircle2,
  Database,
  FileText,
  Loader2,
  Network,
  Pencil,
  Plus,
  RefreshCw,
  RotateCcw,
  Search,
  Shield,
  Snowflake,
  Sparkles,
  Trash2,
  UserRound,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { MemoryFactEditorDialog } from '@/components/memory/MemoryFactEditorDialog'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { ScrollArea } from '@/components/ui/scroll-area'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import { TabsContent } from '@/components/ui/tabs'
import { useToast } from '@/hooks/use-toast'
import {
  createMemoryFact,
  getMemoryRecordContext,
  restoreMemoryFact,
  retractMemoryFact,
  searchMemoryRecords,
  updateMemoryFact,
  type MemoryFactWritePayload,
  type MemoryRecordContextPayload,
  type MemoryRecordPayload,
  type MemoryRecordType,
} from '@/lib/memory-api'
import { cn } from '@/lib/utils'

const RECORD_LABELS: Record<MemoryRecordType, string> = {
  paragraph: '段落',
  entity: '实体',
  relation: '关系',
  fact: '事实',
}

const ACTION_LABELS: Record<string, string> = {
  graph: '图谱',
  correct: '修正',
  reinforce: '强化',
  freeze: '冻结',
  protect: '保护',
  delete: '删除',
  profile: '画像',
  edit_fact: '编辑',
  retract_fact: '撤回',
  restore_fact: '恢复',
}

const ACTION_ICONS: Record<string, typeof Database> = {
  graph: Network,
  correct: Pencil,
  reinforce: Sparkles,
  freeze: Snowflake,
  protect: Shield,
  delete: Trash2,
  profile: UserRound,
  edit_fact: Pencil,
  retract_fact: Trash2,
  restore_fact: RotateCcw,
}

const FACT_TRANSITION_LABELS: Record<string, string> = {
  assert: '写入',
  reinforce: '确认',
  restore: '恢复',
  supersede: '取代',
  retract: '撤回',
  support_evidence: '补充支持证据',
  refute_evidence: '补充反向证据',
}

const GRAPH_JOB_STATUS_LABELS: Record<string, string> = {
  pending: 'memory.records.graphJobStatus.pending',
  running: 'memory.records.graphJobStatus.running',
  failed: 'memory.records.graphJobStatus.failed',
  completed: 'memory.records.graphJobStatus.completed',
  cancelled: 'memory.records.graphJobStatus.cancelled',
}

const RECORD_STATUS_LABELS: Record<string, string> = {
  active: 'memory.records.status.active',
  inactive: 'memory.records.status.inactive',
  deleted: 'memory.records.status.deleted',
  conflicted: 'memory.records.status.conflicted',
  superseded: 'memory.records.status.superseded',
  retracted: 'memory.records.status.retracted',
}

const EVIDENCE_TYPE_LABELS: Record<string, string> = {
  paragraph: 'memory.records.evidenceType.paragraph',
  relation: 'memory.records.evidenceType.relation',
  entity: 'memory.records.evidenceType.entity',
  fact: 'memory.records.evidenceType.fact',
  episode: 'memory.records.evidenceType.episode',
  profile: 'memory.records.evidenceType.profile',
  manual: 'memory.records.evidenceType.manual',
  unknown: 'memory.records.evidenceType.unknown',
}

const EVIDENCE_STANCE_LABELS: Record<string, string> = {
  support: 'memory.records.evidenceStance.support',
  refute: 'memory.records.evidenceStance.refute',
}

function LocalizedEnumLabel({
  value,
  labels,
}: {
  value: string
  labels: Record<string, string>
}) {
  const { t } = useTranslation()
  const translationKey = labels[value]
  return <>{translationKey ? t(translationKey) : value}</>
}

interface MemoryRecordsTabProps {
  onAction: (
    action: string,
    record: MemoryRecordPayload,
    context: MemoryRecordContextPayload,
    targetId?: string
  ) => void
}

function formatTimestamp(value?: number | null): string {
  if (!value) {
    return ''
  }
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(value * 1000))
}

function RecordTypeIcon({ type, className }: { type: MemoryRecordType; className?: string }) {
  const Icon =
    type === 'paragraph'
      ? FileText
      : type === 'entity'
        ? UserRound
        : type === 'relation'
          ? Network
          : CheckCircle2
  return <Icon className={className} />
}

function RecordButton({
  record,
  selected,
  onSelect,
  compact = false,
}: {
  record: MemoryRecordPayload
  selected?: boolean
  onSelect: (record: MemoryRecordPayload) => void
  compact?: boolean
}) {
  return (
    <button
      type="button"
      onClick={() => onSelect(record)}
      className={cn(
        'hover:bg-muted/60 focus-visible:ring-ring flex w-full items-start gap-3 rounded-md border border-transparent px-3 py-2.5 text-left transition focus-visible:ring-2 focus-visible:outline-none',
        selected && 'border-primary/35 bg-primary/5',
        compact && 'py-2'
      )}
    >
      <span className="bg-muted mt-0.5 flex h-7 w-7 flex-none items-center justify-center rounded-md">
        <RecordTypeIcon type={record.type} className="h-3.5 w-3.5" />
      </span>
      <span className="min-w-0 flex-1">
        <span className={cn('block font-medium break-words', compact ? 'text-xs' : 'text-sm')}>
          {record.title || record.id}
        </span>
        {!compact && record.summary ? (
          <span className="text-muted-foreground mt-1 line-clamp-2 block text-xs leading-relaxed">
            {record.summary}
          </span>
        ) : null}
        <span className="text-muted-foreground mt-1 flex flex-wrap items-center gap-1.5 text-[11px]">
          <span>{RECORD_LABELS[record.type]}</span>
          {record.status !== 'active' ? (
            <Badge variant="outline">
              <LocalizedEnumLabel value={record.status} labels={RECORD_STATUS_LABELS} />
            </Badge>
          ) : null}
          {record.source ? <span className="max-w-48 truncate">{record.source}</span> : null}
        </span>
      </span>
    </button>
  )
}

function RelatedRecords({
  title,
  records,
  onSelect,
}: {
  title: string
  records: MemoryRecordPayload[]
  onSelect: (record: MemoryRecordPayload) => void
}) {
  if (records.length === 0) {
    return null
  }
  return (
    <section className="space-y-1.5">
      <div className="text-muted-foreground flex items-center justify-between px-1 text-xs font-medium">
        <span>{title}</span>
        <span>{records.length}</span>
      </div>
      <div className="divide-border/60 divide-y rounded-md border">
        {records.slice(0, 12).map((record) => (
          <RecordButton
            key={`${record.type}:${record.id}`}
            record={record}
            onSelect={onSelect}
            compact
          />
        ))}
      </div>
    </section>
  )
}

export function MemoryRecordsTab({ onAction }: MemoryRecordsTabProps) {
  const { toast } = useToast()
  const queryClient = useQueryClient()
  const [draftQuery, setDraftQuery] = useState('')
  const [query, setQuery] = useState('')
  const [recordType, setRecordType] = useState<'all' | MemoryRecordType>('all')
  const [includeInactive, setIncludeInactive] = useState(false)
  const [selectedRecord, setSelectedRecord] = useState<MemoryRecordPayload | null>(null)
  const [factEditorOpen, setFactEditorOpen] = useState(false)
  const [editingFact, setEditingFact] = useState<MemoryRecordPayload | null>(null)

  const searchQuery = useQuery({
    queryKey: ['memory-records', query, recordType, includeInactive],
    queryFn: () =>
      searchMemoryRecords({
        query,
        types: recordType === 'all' ? undefined : [recordType],
        includeInactive,
      }),
  })

  const effectiveSelectedRecord = selectedRecord ?? searchQuery.data?.items[0] ?? null

  const detailQuery = useQuery({
    queryKey: [
      'memory-record-context',
      effectiveSelectedRecord?.type,
      effectiveSelectedRecord?.id,
    ],
    queryFn: () =>
      getMemoryRecordContext(effectiveSelectedRecord!.type, effectiveSelectedRecord!.id),
    enabled: Boolean(effectiveSelectedRecord),
  })

  const detail = detailQuery.data
  const error = searchQuery.error ?? detailQuery.error
  const errorText = error instanceof Error ? error.message : error ? '查询记忆失败' : ''
  const resultCounts = useMemo(
    () => Object.entries(searchQuery.data?.counts ?? {}).filter(([, count]) => Number(count) > 0),
    [searchQuery.data?.counts]
  )

  const refreshRecords = async () => {
    setSelectedRecord(null)
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['memory-records'] }),
      queryClient.invalidateQueries({ queryKey: ['memory-record-context'] }),
    ])
  }

  const saveFactMutation = useMutation({
    mutationFn: async (payload: MemoryFactWritePayload) => {
      const response = editingFact
        ? await updateMemoryFact(editingFact.id, payload)
        : await createMemoryFact(payload)
      if (!response.success) {
        throw new Error(response.error || '事实保存失败')
      }
      return response
    },
    onSuccess: async (payload) => {
      setFactEditorOpen(false)
      setEditingFact(null)
      await refreshRecords()
      toast({
        title: payload.replaced ? '事实已修订' : '事实已保存',
        description: payload.refresh_queued ? '相关人物画像已进入刷新队列。' : undefined,
      })
    },
    onError: (error) => {
      toast({
        title: '保存事实失败',
        description: error instanceof Error ? error.message : String(error),
        variant: 'destructive',
      })
    },
  })

  const factStatusMutation = useMutation({
    mutationFn: async ({ action, claimId }: { action: 'retract' | 'restore'; claimId: string }) => {
      const response =
        action === 'retract'
          ? await retractMemoryFact(claimId, 'knowledge_base_fact_retract')
          : await restoreMemoryFact(claimId, 'knowledge_base_fact_restore')
      if (!response.success) {
        throw new Error(response.error || '事实状态更新失败')
      }
      return { action, response }
    },
    onSuccess: async ({ action, response }) => {
      await refreshRecords()
      toast({
        title: action === 'retract' ? '事实已撤回' : '事实已恢复',
        description: response.refresh_queued ? '相关人物画像已进入刷新队列。' : undefined,
      })
    },
    onError: (error) => {
      toast({
        title: '更新事实状态失败',
        description: error instanceof Error ? error.message : String(error),
        variant: 'destructive',
      })
    },
  })

  const submitSearch = () => {
    const nextQuery = draftQuery.trim()
    if (nextQuery === query) {
      void searchQuery.refetch()
      return
    }
    setSelectedRecord(null)
    setQuery(nextQuery)
  }

  const triggerAction = (action: string, detail: MemoryRecordContextPayload, targetId?: string) => {
    if (action === 'edit_fact') {
      setEditingFact(detail.record)
      setFactEditorOpen(true)
      return
    }
    if (action === 'retract_fact') {
      if (window.confirm(`确认撤回事实“${detail.record.title}”？`)) {
        factStatusMutation.mutate({ action: 'retract', claimId: detail.record.id })
      }
      return
    }
    if (action === 'restore_fact') {
      factStatusMutation.mutate({ action: 'restore', claimId: detail.record.id })
      return
    }
    if (['correct', 'reinforce', 'freeze', 'protect', 'delete'].includes(action)) {
      setSelectedRecord(null)
    }
    onAction(action, detail.record, detail, targetId)
  }

  return (
    <TabsContent value="records" className="space-y-4">
      <Card>
        <CardContent className="pt-4 sm:pt-5">
          <form
            className="grid gap-3 md:grid-cols-[minmax(260px,1fr)_160px_auto_auto] md:items-end"
            onSubmit={(event) => {
              event.preventDefault()
              submitSearch()
            }}
          >
            <div className="space-y-1.5">
              <Label htmlFor="memory-record-query">搜索记忆</Label>
              <div className="relative">
                <Search className="text-muted-foreground pointer-events-none absolute top-1/2 left-3 h-4 w-4 -translate-y-1/2" />
                <Input
                  id="memory-record-query"
                  value={draftQuery}
                  onChange={(event) => setDraftQuery(event.target.value)}
                  placeholder="内容、名称、关系、事实或 ID"
                  className="pl-9"
                />
              </div>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="memory-record-type">记录类型</Label>
              <Select
                value={recordType}
                onValueChange={(value) => {
                  setSelectedRecord(null)
                  setRecordType(value as 'all' | MemoryRecordType)
                }}
              >
                <SelectTrigger id="memory-record-type">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">全部类型</SelectItem>
                  <SelectItem value="paragraph">段落</SelectItem>
                  <SelectItem value="entity">实体</SelectItem>
                  <SelectItem value="relation">关系</SelectItem>
                  <SelectItem value="fact">事实</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="flex h-9 items-center gap-2">
              <Switch
                id="memory-record-inactive"
                checked={includeInactive}
                onCheckedChange={(checked) => {
                  setSelectedRecord(null)
                  setIncludeInactive(checked)
                }}
              />
              <Label htmlFor="memory-record-inactive" className="whitespace-nowrap">
                显示停用
              </Label>
            </div>
            <Button type="submit" disabled={searchQuery.isFetching}>
              {searchQuery.isFetching ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Search className="h-4 w-4" />
              )}
              查询
            </Button>
          </form>
        </CardContent>
      </Card>

      {errorText ? (
        <Alert variant="destructive">
          <AlertTriangle className="h-4 w-4" />
          <AlertDescription>{errorText}</AlertDescription>
        </Alert>
      ) : null}

      <div className="grid gap-4 xl:grid-cols-[minmax(300px,0.8fr)_minmax(520px,1.6fr)]">
        <Card className="h-[660px] overflow-hidden">
          <CardHeader className="border-b pb-3">
            <div className="flex items-center justify-between gap-3">
              <CardTitle className="text-sm">查询结果</CardTitle>
              <div className="flex items-center gap-1.5">
                {resultCounts.map(([type, count]) => (
                  <Badge key={type} variant="secondary">
                    {RECORD_LABELS[type as MemoryRecordType]} {count}
                  </Badge>
                ))}
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  onClick={() => {
                    setEditingFact(null)
                    setFactEditorOpen(true)
                  }}
                >
                  <Plus className="h-4 w-4" />
                  新增事实
                </Button>
                <Button
                  type="button"
                  size="icon"
                  variant="ghost"
                  title="刷新查询"
                  onClick={() => void refreshRecords()}
                  disabled={searchQuery.isFetching}
                >
                  <RefreshCw className={cn('h-4 w-4', searchQuery.isFetching && 'animate-spin')} />
                </Button>
              </div>
            </div>
          </CardHeader>
          <ScrollArea className="h-[598px]">
            <CardContent className="space-y-1 pt-3">
              {searchQuery.isLoading ? (
                <div className="text-muted-foreground flex h-40 items-center justify-center gap-2 text-sm">
                  <Loader2 className="h-4 w-4 animate-spin" />
                  正在查询
                </div>
              ) : searchQuery.data?.items.length ? (
                searchQuery.data.items.map((record) => (
                  <RecordButton
                    key={`${record.type}:${record.id}`}
                    record={record}
                    selected={
                      record.type === effectiveSelectedRecord?.type &&
                      record.id === effectiveSelectedRecord.id
                    }
                    onSelect={setSelectedRecord}
                  />
                ))
              ) : (
                <div className="text-muted-foreground flex h-40 items-center justify-center text-sm">
                  没有匹配记录
                </div>
              )}
            </CardContent>
          </ScrollArea>
        </Card>

        <Card className="h-[660px] overflow-hidden">
          <CardHeader className="border-b pb-3">
            <div className="flex min-w-0 flex-col items-start justify-between gap-3 sm:flex-row">
              <div className="min-w-0 space-y-1">
                <CardTitle className="text-sm break-words">
                  {detail?.record.title || effectiveSelectedRecord?.title || '记录详情'}
                </CardTitle>
                {effectiveSelectedRecord ? (
                  <div className="text-muted-foreground font-mono text-[11px] break-all">
                    {effectiveSelectedRecord.id}
                  </div>
                ) : null}
              </div>
              {detail ? (
                <div className="flex w-full max-w-full flex-wrap gap-1.5 sm:w-auto sm:justify-end">
                  {detail.available_actions.map((action) => {
                    const Icon = ACTION_ICONS[action] ?? Database
                    return (
                      <Button
                        key={action}
                        type="button"
                        size="sm"
                        variant={action === 'delete' || action === 'retract_fact' ? 'destructive' : 'outline'}
                        onClick={() => triggerAction(action, detail)}
                        disabled={
                          factStatusMutation.isPending &&
                          (action === 'retract_fact' || action === 'restore_fact')
                        }
                      >
                        {factStatusMutation.isPending &&
                        (action === 'retract_fact' || action === 'restore_fact') ? (
                          <Loader2 className="h-3.5 w-3.5 animate-spin" />
                        ) : (
                          <Icon className="h-3.5 w-3.5" />
                        )}
                        {ACTION_LABELS[action] ?? action}
                      </Button>
                    )
                  })}
                </div>
              ) : null}
            </div>
          </CardHeader>
          <ScrollArea className="h-[598px]">
            <CardContent className="space-y-5 pt-4">
              {!effectiveSelectedRecord ? (
                <div className="text-muted-foreground flex h-40 items-center justify-center text-sm">
                  请选择一条记录
                </div>
              ) : detailQuery.isLoading ? (
                <div className="text-muted-foreground flex h-40 items-center justify-center gap-2 text-sm">
                  <Loader2 className="h-4 w-4 animate-spin" />
                  正在派生关联内容
                </div>
              ) : detail ? (
                <>
                  <section className="space-y-2">
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge>{RECORD_LABELS[detail.record.type]}</Badge>
                      <Badge variant="outline">
                        <LocalizedEnumLabel
                          value={detail.record.status}
                          labels={RECORD_STATUS_LABELS}
                        />
                      </Badge>
                      {detail.record.updated_at || detail.record.created_at ? (
                        <span className="text-muted-foreground text-xs">
                          {formatTimestamp(detail.record.updated_at || detail.record.created_at)}
                        </span>
                      ) : null}
                    </div>
                    <p className="text-sm leading-6 break-words whitespace-pre-wrap">
                      {detail.record.summary || detail.record.title}
                    </p>
                    {detail.record.source ? (
                      <div className="text-muted-foreground text-xs break-all">
                        来源：{detail.record.source}
                      </div>
                    ) : null}
                  </section>

                  {detail.projection.graph_jobs.length > 0 ? (
                    <Alert
                      variant={
                        detail.projection.graph_jobs.some((job) => job.status === 'failed')
                          ? 'destructive'
                          : 'default'
                      }
                    >
                      {detail.projection.graph_jobs.some((job) => job.status === 'failed') ? (
                        <AlertTriangle className="h-4 w-4" />
                      ) : (
                        <RefreshCw className="h-4 w-4" />
                      )}
                      <AlertDescription>
                        有 {detail.projection.graph_jobs.length} 条关系图投影任务未完成
                      </AlertDescription>
                    </Alert>
                  ) : null}

                  {detail.projection.graph_jobs.length > 0 ? (
                    <section className="space-y-1.5">
                      <div className="text-muted-foreground px-1 text-xs font-medium">
                        图投影状态
                      </div>
                      <div className="divide-border/60 divide-y rounded-md border text-xs">
                        {detail.projection.graph_jobs.map((job, index) => {
                          const status = String(job.status || 'pending')
                          return (
                            <div
                              key={`${String(job.relation_hash || 'relation')}:${index}`}
                              className="space-y-1.5 px-3 py-2.5"
                            >
                              <div className="flex flex-wrap items-center gap-2">
                                <Badge
                                  variant={status === 'failed' ? 'destructive' : 'secondary'}
                                >
                                  <LocalizedEnumLabel
                                    value={status}
                                    labels={GRAPH_JOB_STATUS_LABELS}
                                  />
                                </Badge>
                                <span className="text-muted-foreground">
                                  {job.desired_active ? '目标：启用' : '目标：停用'}
                                </span>
                                {Number(job.attempt_count || 0) > 0 ? (
                                  <span className="text-muted-foreground">
                                    已尝试 {Number(job.attempt_count)} 次
                                  </span>
                                ) : null}
                              </div>
                              {job.last_error ? (
                                <p className="text-destructive break-words">
                                  {String(job.last_error)}
                                </p>
                              ) : null}
                              {job.relation_hash ? (
                                <div className="text-muted-foreground font-mono break-all">
                                  {String(job.relation_hash)}
                                </div>
                              ) : null}
                            </div>
                          )
                        })}
                      </div>
                    </section>
                  ) : null}

                  <RelatedRecords
                    title="关联段落"
                    records={detail.related.paragraphs}
                    onSelect={setSelectedRecord}
                  />
                  <RelatedRecords
                    title="关联实体"
                    records={detail.related.entities}
                    onSelect={setSelectedRecord}
                  />
                  <RelatedRecords
                    title="关联关系"
                    records={detail.related.relations}
                    onSelect={setSelectedRecord}
                  />
                  <RelatedRecords
                    title="关联事实"
                    records={detail.related.facts}
                    onSelect={setSelectedRecord}
                  />

                  {detail.related.episodes.length ? (
                    <section className="space-y-1.5">
                      <div className="text-muted-foreground px-1 text-xs font-medium">关联情景</div>
                      <div className="divide-border/60 divide-y rounded-md border">
                        {detail.related.episodes.map((episode) => (
                          <button
                            key={episode.id}
                            type="button"
                            className="hover:bg-muted/60 w-full px-3 py-2.5 text-left transition"
                            onClick={() => triggerAction('episode', detail, episode.id)}
                          >
                            <div className="text-sm font-medium break-words">
                              {episode.title || episode.id}
                            </div>
                            <div className="text-muted-foreground mt-1 line-clamp-2 text-xs">
                              {episode.summary}
                            </div>
                          </button>
                        ))}
                      </div>
                    </section>
                  ) : null}

                  {detail.related.profiles.length ? (
                    <section className="space-y-1.5">
                      <div className="text-muted-foreground px-1 text-xs font-medium">关联画像</div>
                      <div className="divide-border/60 divide-y rounded-md border">
                        {detail.related.profiles.map((profile) => (
                          <button
                            key={`${profile.person_id}:${profile.profile_version}`}
                            type="button"
                            className="hover:bg-muted/60 w-full px-3 py-2.5 text-left transition"
                            onClick={() =>
                              triggerAction('profile', detail, profile.person_id)
                            }
                          >
                            <div className="text-sm font-medium">{profile.person_id}</div>
                            <div className="text-muted-foreground mt-1 line-clamp-2 text-xs">
                              {profile.profile_text}
                            </div>
                          </button>
                        ))}
                      </div>
                    </section>
                  ) : null}

                  {detail.fact_evidence.length ? (
                    <section className="space-y-1.5">
                      <div className="text-muted-foreground px-1 text-xs font-medium">事实证据</div>
                      <div className="divide-border/60 divide-y rounded-md border text-xs">
                        {detail.fact_evidence.map((evidence, index) => (
                          <div
                            key={`${evidence.evidence_id || 'evidence'}:${index}`}
                            className="flex flex-wrap gap-x-3 gap-y-1 px-3 py-2.5"
                          >
                            <span>
                              <LocalizedEnumLabel
                                value={String(evidence.evidence_type || 'unknown')}
                                labels={EVIDENCE_TYPE_LABELS}
                              />
                            </span>
                            <span className="font-mono break-all">
                              {String(evidence.evidence_id || '')}
                            </span>
                            <span className="text-muted-foreground">
                              <LocalizedEnumLabel
                                value={String(evidence.stance || '')}
                                labels={EVIDENCE_STANCE_LABELS}
                              />
                            </span>
                          </div>
                        ))}
                      </div>
                    </section>
                  ) : null}

                  {detail.fact_transitions.length ? (
                    <section className="space-y-1.5">
                      <div className="text-muted-foreground px-1 text-xs font-medium">
                        状态变更
                      </div>
                      <div className="divide-border/60 divide-y rounded-md border text-xs">
                        {detail.fact_transitions.map((transition, index) => {
                          const transitionType = String(transition.transition_type || 'unknown')
                          return (
                            <div
                              key={`${String(transition.transition_id || 'transition')}:${index}`}
                              className="space-y-1.5 px-3 py-2.5"
                            >
                              <div className="flex flex-wrap items-center gap-2">
                                <Badge variant="secondary">
                                  {FACT_TRANSITION_LABELS[transitionType] ?? transitionType}
                                </Badge>
                                {transition.created_at ? (
                                  <span className="text-muted-foreground">
                                    {formatTimestamp(Number(transition.created_at))}
                                  </span>
                                ) : null}
                              </div>
                              {transition.reason ? (
                                <p className="leading-relaxed break-words">
                                  {String(transition.reason)}
                                </p>
                              ) : null}
                              {transition.evidence_type || transition.evidence_id ? (
                                <div className="text-muted-foreground flex flex-wrap gap-x-2 gap-y-1">
                                  <span>
                                    <LocalizedEnumLabel
                                      value={String(transition.evidence_type || 'unknown')}
                                      labels={EVIDENCE_TYPE_LABELS}
                                    />
                                  </span>
                                  <span className="font-mono break-all">
                                    {String(transition.evidence_id || '')}
                                  </span>
                                </div>
                              ) : null}
                            </div>
                          )
                        })}
                      </div>
                    </section>
                  ) : null}
                </>
              ) : null}
            </CardContent>
          </ScrollArea>
        </Card>
      </div>

      {factEditorOpen ? (
        <MemoryFactEditorDialog
          open
          onOpenChange={(open) => {
            setFactEditorOpen(open)
            if (!open) {
              setEditingFact(null)
            }
          }}
          record={editingFact}
          saving={saveFactMutation.isPending}
          onSubmit={(payload) => saveFactMutation.mutate(payload)}
        />
      ) : null}
    </TabsContent>
  )
}
