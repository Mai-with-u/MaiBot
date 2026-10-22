import { useMemo, useState } from 'react'
import type { ReactNode } from 'react'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Activity,
  AlertTriangle,
  BookOpen,
  Box,
  CheckCircle2,
  Database,
  FileText,
  Loader2,
  Network,
  Pencil,
  RefreshCw,
  RotateCcw,
  Search,
  Trash2,
  UserRound,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { MemoryFactEditorDialog } from '@/components/memory/MemoryFactEditorDialog'
import { MemoryMiniTabs } from '@/components/memory/MemoryMiniTabs'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader } from '@/components/ui/card'
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
import { ScrollArea } from '@/components/ui/scroll-area'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import { Tabs, TabsContent } from '@/components/ui/tabs'
import { Textarea } from '@/components/ui/textarea'
import { useToast } from '@/hooks/use-toast'
import {
  getMemoryCorrectionPlan,
  getMemoryRecordContext,
  previewMemoryCorrection,
  restoreMemoryFact,
  retractMemoryFact,
  searchMemoryRecords,
  updateMemoryFact,
  type MemoryCorrectionPlanListPayload,
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
  episode: '情景记忆',
}

const ACTION_LABELS: Record<string, string> = {
  graph: '图谱',
  correct: '修正',
  status: '状态',
  delete: '删除',
  profile: '画像',
  edit_fact: '编辑',
  retract_fact: '撤回',
  restore_fact: '恢复',
}

const ACTION_ICONS: Record<string, typeof Database> = {
  graph: Network,
  correct: Pencil,
  status: Activity,
  delete: Trash2,
  profile: UserRound,
  edit_fact: Pencil,
  retract_fact: Trash2,
  restore_fact: RotateCcw,
}

const KNOWLEDGE_TYPE_LABELS: Record<string, string> = {
  structured: '结构化资料',
  narrative: '叙事资料',
  factual: '事实资料',
  quote: '引用资料',
  mixed: '混合资料',
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

const RELATED_TAB_LABELS: Record<string, string> = {
  paragraphs: '段落',
  entities: '实体',
  relations: '关系',
  facts: '事实',
  episodes: '情景',
  profiles: '画像',
}

const LEDGER_TAB_LABELS: Record<string, string> = {
  evidence: '证据',
  transitions: '变更',
}

function LocalizedEnumLabel({ value, labels }: { value: string; labels: Record<string, string> }) {
  const { t } = useTranslation()
  const translationKey = labels[value]
  return <>{translationKey ? t(translationKey) : value}</>
}

interface MemoryRecordsTabProps {
  searchModeTabs?: ReactNode
  onCorrectionPlan?: (planId: string, requestText: string) => void
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

function getKnowledgeTypeLabel(record: MemoryRecordPayload): string {
  if (record.type !== 'paragraph') {
    return ''
  }
  const knowledgeType = String(record.metadata.knowledge_type ?? '')
    .trim()
    .toLowerCase()
  return KNOWLEDGE_TYPE_LABELS[knowledgeType] ?? knowledgeType
}

function detailDialogTitle(
  detail: MemoryRecordContextPayload | undefined,
  selectedRecord: MemoryRecordPayload | null
): string {
  const record = detail?.record ?? selectedRecord
  if (!record) {
    return '记录详情'
  }
  // 段落的 title 是自动截取的正文预览，与正文区重复，标题改用类型标识
  if (record.type === 'paragraph') {
    const knowledge = getKnowledgeTypeLabel(record)
    return knowledge ? `${RECORD_LABELS.paragraph} · ${knowledge}` : RECORD_LABELS.paragraph
  }
  return record.title || RECORD_LABELS[record.type] || '记录详情'
}

function RecordTypeIcon({ type, className }: { type: MemoryRecordType; className?: string }) {
  const Icon =
    type === 'paragraph'
      ? FileText
      : type === 'entity'
        ? Box
        : type === 'relation'
          ? Network
          : type === 'episode'
            ? BookOpen
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
  const knowledgeTypeLabel = getKnowledgeTypeLabel(record)
  return (
    <button
      type="button"
      data-memory-record-button="true"
      onClick={() => onSelect(record)}
      className={cn(
        // 不带 border 类：retro 主题会把所有 .border 强制描边，列表内每条都会多出一圈框线
        'hover:bg-muted/60 focus-visible:ring-ring flex w-full min-w-0 items-start gap-3 rounded-md px-3 py-2.5 text-left transition focus-visible:ring-2 focus-visible:outline-none',
        selected && 'bg-primary/5',
        compact && 'py-2'
      )}
    >
      <span className="mt-0.5 flex w-12 flex-none flex-col items-center gap-1">
        <span className="bg-muted flex h-7 w-7 items-center justify-center rounded-md">
          <RecordTypeIcon type={record.type} className="h-3.5 w-3.5" />
        </span>
        <span className="text-muted-foreground text-xs font-medium whitespace-nowrap">
          {RECORD_LABELS[record.type]}
        </span>
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
        {knowledgeTypeLabel || record.status !== 'active' || record.source_label ? (
          <span className="text-muted-foreground mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs">
            {knowledgeTypeLabel ? <Badge variant="secondary">{knowledgeTypeLabel}</Badge> : null}
            {record.status !== 'active' ? (
              <Badge variant="outline">
                <LocalizedEnumLabel value={record.status} labels={RECORD_STATUS_LABELS} />
              </Badge>
            ) : null}
            {/* 只展示可读来源（聊天流名称 / 人物姓名 / 导入来源），不暴露内部来源标记 */}
            {record.source_label ? (
              <>
                {knowledgeTypeLabel || record.status !== 'active' ? (
                  <span aria-hidden="true">·</span>
                ) : null}
                <span className="max-w-full truncate" title={record.source_label}>
                  来自 {record.source_label}
                </span>
              </>
            ) : null}
          </span>
        ) : null}
      </span>
    </button>
  )
}

function RelatedRecordsList({
  records,
  onSelect,
}: {
  records: MemoryRecordPayload[]
  onSelect: (record: MemoryRecordPayload) => void
}) {
  if (records.length === 0) {
    return null
  }
  return (
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
  )
}

export function MemoryRecordsTab({ onAction, onCorrectionPlan, searchModeTabs }: MemoryRecordsTabProps) {
  const { toast } = useToast()
  const queryClient = useQueryClient()
  const [draftQuery, setDraftQuery] = useState('')
  const [query, setQuery] = useState('')
  const [recordType, setRecordType] = useState<'all' | MemoryRecordType>('all')
  const [includeInactive, setIncludeInactive] = useState(false)
  const [selectedRecord, setSelectedRecord] = useState<MemoryRecordPayload | null>(null)
  const [checkedKeys, setCheckedKeys] = useState<Set<string>>(() => new Set())
  const [batchEditorOpen, setBatchEditorOpen] = useState(false)
  const [batchRequest, setBatchRequest] = useState('')
  const [factEditorOpen, setFactEditorOpen] = useState(false)
  const [editingFact, setEditingFact] = useState<MemoryRecordPayload | null>(null)
  // 详情弹窗「关联内容」「事实账本」两组胶囊标签的当前页签；切换记录后自动回退到首个可用页签
  const [relatedTab, setRelatedTab] = useState('')
  const [ledgerTab, setLedgerTab] = useState('')

  const searchQuery = useQuery({
    queryKey: ['memory-records', query, recordType, includeInactive],
    queryFn: () =>
      searchMemoryRecords({
        query,
        types: recordType === 'all' ? undefined : [recordType],
        includeInactive,
      }),
  })

  const records = searchQuery.data?.items ?? []
  const checkedRecords = records.filter((record) => checkedKeys.has(`${record.type}:${record.id}`))
  const allChecked = records.length > 0 && checkedRecords.length === records.length
  const hasUnsupportedRecords = checkedRecords.some(
    (record) => record.type !== 'paragraph' && record.type !== 'relation'
  )

  const previewBatchMutation = useMutation({
    mutationFn: async () => {
      const requestText = batchRequest.trim()
      if (!requestText || !checkedRecords.length || hasUnsupportedRecords) {
        throw new Error('请选择段落或关系，并填写修正内容')
      }
      const payload = await previewMemoryCorrection({
        request_text: requestText,
        scope: 'memory',
        targets: checkedRecords.map((record) => ({
          type: record.type as 'paragraph' | 'relation',
          id: record.id,
        })),
        requested_by: 'knowledge_base',
        reason: '用户多选记忆修正',
      })
      const planId = payload.plan_id || payload.plan?.plan_id
      if (!payload.success || !planId) {
        throw new Error(payload.error || '未能生成批量修正预览')
      }
      const plan = payload.plan ?? (await getMemoryCorrectionPlan(planId)).plan
      if (!plan) throw new Error('预览已生成，但无法读取修正计划，请重试')
      return plan
    },
    onSuccess: (plan) => {
      // 先让计划列表包含新计划，再跳转，避免选择状态被旧列表清空。
      queryClient.setQueryData<MemoryCorrectionPlanListPayload>(
        ['memory-correction', 'plans'],
        (previous) => {
          const items = [
            plan,
            ...(previous?.items ?? []).filter((item) => item.plan_id !== plan.plan_id),
          ]
          return { ...previous, success: true, items, count: items.length }
        }
      )
      setBatchEditorOpen(false)
      setBatchRequest('')
      setCheckedKeys(new Set())
      onCorrectionPlan?.(plan.plan_id, plan.request_text)
      toast({
        title: '已生成批量修正预览',
        description: '请检查修改操作和关联影响，确认后再执行。',
      })
    },
  })

  const detailQuery = useQuery({
    queryKey: ['memory-record-context', selectedRecord?.type, selectedRecord?.id],
    queryFn: () => getMemoryRecordContext(selectedRecord!.type, selectedRecord!.id),
    enabled: Boolean(selectedRecord),
  })

  const detail = detailQuery.data
  // 三种维护动作共用一个入口，保留原先的位置与其它操作的顺序。
  const detailActions = Array.from(
    new Set(
      (detail?.available_actions ?? []).map((action) =>
        ['reinforce', 'freeze', 'protect'].includes(action) ? 'status' : action
      )
    )
  )
  const error = searchQuery.error ?? detailQuery.error
  const errorText = error instanceof Error ? error.message : error ? '查询记忆失败' : ''
  // 「关联内容」「事实账本」两组胶囊标签；只保留有内容的页签，计数直接进入标签文案
  const relatedTabItems = detail
    ? [
        { value: 'paragraphs', count: detail.related.paragraphs.length },
        { value: 'entities', count: detail.related.entities.length },
        { value: 'relations', count: detail.related.relations.length },
        { value: 'facts', count: detail.related.facts.length },
        { value: 'episodes', count: detail.related.episodes.length },
        { value: 'profiles', count: detail.related.profiles.length },
      ]
        .filter((item) => item.count > 0)
        .map((item) => ({
          value: item.value,
          label: `${RELATED_TAB_LABELS[item.value]} ${item.count}`,
        }))
    : []
  const activeRelatedTab = relatedTabItems.some((item) => item.value === relatedTab)
    ? relatedTab
    : (relatedTabItems[0]?.value ?? '')
  const ledgerTabItems = detail
    ? [
        { value: 'evidence', count: detail.fact_evidence.length },
        { value: 'transitions', count: detail.fact_transitions.length },
      ]
        .filter((item) => item.count > 0)
        .map((item) => ({
          value: item.value,
          label: `${LEDGER_TAB_LABELS[item.value]} ${item.count}`,
        }))
    : []
  const activeLedgerTab = ledgerTabItems.some((item) => item.value === ledgerTab)
    ? ledgerTab
    : (ledgerTabItems[0]?.value ?? '')
  const resultCounts = useMemo(
    () => Object.entries(searchQuery.data?.counts ?? {}).filter(([, count]) => Number(count) > 0),
    [searchQuery.data?.counts]
  )

  const refreshRecords = async () => {
    setSelectedRecord(null)
    setCheckedKeys(new Set())
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['memory-records'] }),
      queryClient.invalidateQueries({ queryKey: ['memory-record-context'] }),
    ])
  }

  const saveFactMutation = useMutation({
    mutationFn: async (payload: MemoryFactWritePayload) => {
      if (!editingFact) {
        throw new Error('没有可更新的事实')
      }
      const response = await updateMemoryFact(editingFact.id, payload)
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
    setCheckedKeys(new Set())
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
    // 跳转或改写类动作会离开当前详情，统一关闭弹窗；画像动作可能仅提示不跳转，
    // 未知动作保持弹窗，均不关闭
    if (
      [
        'graph',
        'correct',
        'status',
        'reinforce',
        'freeze',
        'protect',
        'delete',
        'episode',
      ].includes(action)
    ) {
      setSelectedRecord(null)
    }
    onAction(action, detail.record, detail, targetId)
  }

  return (
    <TabsContent
      value="records"
      className="mt-0 flex min-h-0 flex-1 flex-col gap-2 overflow-hidden"
    >
      {/* 搜索栏不套卡片外框，直接贴在结果卡片上方 */}
      <form
        className="flex min-w-0 shrink-0 flex-wrap items-center gap-3"
        onSubmit={(event) => {
          event.preventDefault()
          submitSearch()
        }}
      >
        {searchModeTabs}
        <div className="relative min-w-[12rem] flex-1">
          <Search className="text-muted-foreground pointer-events-none absolute top-1/2 left-3 h-4 w-4 -translate-y-1/2" />
          <Input
            id="memory-record-query"
            value={draftQuery}
            onChange={(event) => setDraftQuery(event.target.value)}
            aria-label="搜索记忆"
            placeholder="搜索记忆：内容、名称、关系、事实、情节或 ID"
            className="h-9 pl-9"
          />
        </div>
        <Select
          value={recordType}
          onValueChange={(value) => {
            setSelectedRecord(null)
            setCheckedKeys(new Set())
            setRecordType(value as 'all' | MemoryRecordType)
          }}
        >
          <SelectTrigger id="memory-record-type" aria-label="记录类型" className="h-9 w-40 min-w-0">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">全部类型</SelectItem>
            <SelectItem value="paragraph">段落</SelectItem>
            <SelectItem value="entity">实体</SelectItem>
            <SelectItem value="relation">关系</SelectItem>
            <SelectItem value="fact">事实</SelectItem>
            <SelectItem value="episode">情景记忆</SelectItem>
          </SelectContent>
        </Select>
        <div className="flex min-h-9 min-w-0 items-center gap-2">
          <Switch
            id="memory-record-inactive"
            checked={includeInactive}
            onCheckedChange={(checked) => {
              setSelectedRecord(null)
              setCheckedKeys(new Set())
              setIncludeInactive(checked)
            }}
          />
          <Label htmlFor="memory-record-inactive" className="whitespace-nowrap">
            显示停用
          </Label>
        </div>
        {/* 查询与刷新为两个独立按钮，同色同高保持风格统一 */}
        <div className="flex min-h-9 items-center justify-end gap-2 lg:justify-start">
          <Button type="submit" className="h-9" disabled={searchQuery.isFetching}>
            {searchQuery.isFetching ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Search className="h-4 w-4" />
            )}
            查询
          </Button>
          <Button
            type="button"
            size="icon"
            className="h-9 w-9"
            title="刷新查询"
            aria-label="刷新查询"
            onClick={() => void refreshRecords()}
            disabled={searchQuery.isFetching}
          >
            <RefreshCw className="h-4 w-4" />
          </Button>
        </div>
      </form>

      {errorText ? (
        <Alert variant="destructive" className="shrink-0">
          <AlertTriangle className="h-4 w-4" />
          <AlertDescription>{errorText}</AlertDescription>
        </Alert>
      ) : null}

      {/* 结果卡用弹性列布局：头部按内容取高，滚动区吃掉剩余高度，
          避免头部与滚动区各写死高度后互相留空隙或溢出 */}
      <Card className="flex min-h-0 flex-1 flex-col overflow-hidden">
        {/* 结果头部单行：左侧多选操作，右侧各类型计数小字 */}
        <CardHeader
          data-memory-records-result-header="true"
          className="mb-0! shrink-0 flex-row flex-wrap items-center space-y-0 gap-x-3 gap-y-1 border-b-0! shadow-none!"
          aria-label="记忆多选操作"
        >
          <label
            htmlFor="memory-record-select-all"
            className="flex cursor-pointer items-center gap-2 text-sm"
          >
            <Checkbox
              id="memory-record-select-all"
              size="sm"
              className="h-3.5 w-3.5 [&_svg]:size-2.5"
              aria-label="全选"
              checked={allChecked ? true : checkedRecords.length > 0 ? 'indeterminate' : false}
              disabled={!records.length || searchQuery.isFetching}
              onCheckedChange={(checked) =>
                setCheckedKeys(
                  checked === true
                    ? new Set(records.map((record) => `${record.type}:${record.id}`))
                    : new Set()
                )
              }
            />
            全选
          </label>
          {checkedRecords.length > 0 ? (
            <>
              <span className="text-sm font-medium" aria-live="polite">
                已选 {checkedRecords.length} 条
              </span>
              <Button
                variant="ghost"
                size="sm"
                className="h-7 px-2"
                onClick={() => setCheckedKeys(new Set())}
              >
                清空选择
              </Button>
            </>
          ) : null}
          <Button
            size="sm"
            className="h-7 px-2.5"
            disabled={!checkedRecords.length || hasUnsupportedRecords || searchQuery.isFetching}
            onClick={() => {
              previewBatchMutation.reset()
              setBatchEditorOpen(true)
            }}
          >
            <Pencil className="h-3.5 w-3.5" />
            修正所选
          </Button>
          <div className="text-muted-foreground ml-auto flex flex-wrap items-center gap-x-3 text-xs">
            {resultCounts.map(([type, count]) => (
              <span key={type}>
                {RECORD_LABELS[type as MemoryRecordType]} {count}
              </span>
            ))}
          </div>
          {hasUnsupportedRecords ? (
            <span className="text-muted-foreground w-full text-xs">
              批量修正支持段落和关系；事实请逐条编辑，实体请在图谱中修改。
            </span>
          ) : null}
        </CardHeader>
        {/* 列表放进与记忆流一致的下沉屏幕：边框属于固定外框，不随内容滚动 */}
        <div className="flex min-h-0 flex-1 flex-col px-3 pb-3 sm:px-4 sm:pb-4">
          <ScrollArea
            data-memory-delete-screen="true"
            className="bg-background h-[60dvh] min-h-0 flex-none rounded-lg border lg:h-auto lg:flex-1"
          >
            <CardContent className="space-y-2 border-0! pt-3 shadow-none!">
              {searchQuery.isLoading ? (
                <div className="text-muted-foreground flex h-40 items-center justify-center gap-2 text-sm">
                  <Loader2 className="h-4 w-4 animate-spin" />
                  正在查询
                </div>
              ) : searchQuery.data?.items.length ? (
                searchQuery.data.items.map((record) => {
                  const key = `${record.type}:${record.id}`
                  const checked = checkedKeys.has(key)
                  // 勾选框与内容同处一个边框内；左侧整列都是勾选热区，右侧点击打开详情
                  return (
                    <div
                      key={key}
                      data-memory-record-row="true"
                      data-checked={checked ? 'true' : 'false'}
                      className={cn(
                        'flex items-stretch rounded-md border transition-colors',
                        checked && 'border-primary bg-primary/5'
                      )}
                    >
                      <label
                        htmlFor={`memory-record-select-${key}`}
                        className="flex shrink-0 cursor-pointer items-start py-3 pr-1 pl-3"
                      >
                        <Checkbox
                          id={`memory-record-select-${key}`}
                          size="sm"
                          className="h-3.5 w-3.5 [&_svg]:size-2.5"
                          aria-label={`选择${RECORD_LABELS[record.type]}：${record.title || record.id}`}
                          checked={checked}
                          onCheckedChange={(next) =>
                            setCheckedKeys((previous) => {
                              const updated = new Set(previous)
                              if (next === true) updated.add(key)
                              else updated.delete(key)
                              return updated
                            })
                          }
                        />
                      </label>
                      <RecordButton
                        record={record}
                        selected={
                          record.type === selectedRecord?.type && record.id === selectedRecord?.id
                        }
                        onSelect={setSelectedRecord}
                      />
                    </div>
                  )
                })
              ) : (
                <div className="text-muted-foreground flex h-40 items-center justify-center text-sm">
                  没有匹配记录
                </div>
              )}
            </CardContent>
          </ScrollArea>
        </div>
      </Card>

      <Dialog
        modal={false}
        open={selectedRecord !== null}
        onOpenChange={(open) => {
          if (!open) {
            setSelectedRecord(null)
          }
        }}
      >
        <DialogContent
          side="right"
          hideOverlay
          className="[--dialog-width:36rem]"
          onInteractOutside={(event) => {
            // 焦点移出或点击其他记录时保持浮层打开，直接切换详情，避免重复播放入场动画。
            const originalEvent = event.detail.originalEvent
            const target = originalEvent.target
            if (
              originalEvent.type === 'focusin' ||
              (target instanceof Element && target.closest('[data-memory-record-button="true"]'))
            ) {
              event.preventDefault()
            }
          }}
        >
          <DialogHeader className="flex-row items-start justify-between gap-3 space-y-0 pr-4">
            <DialogTitle className="min-w-0 text-sm leading-6 break-words">
              {detailDialogTitle(detail, selectedRecord)}
            </DialogTitle>
            {detail ? (
              <div className="flex flex-wrap gap-1.5">
                {detailActions.map((action) => {
                  const Icon = ACTION_ICONS[action] ?? Database
                  return (
                    <Button
                      key={action}
                      type="button"
                      size="sm"
                      variant={
                        action === 'delete' || action === 'retract_fact' ? 'destructive' : 'outline'
                      }
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
          </DialogHeader>
          <DialogBody contentClassName="space-y-5">
            {detailQuery.isLoading ? (
              <div className="text-muted-foreground flex h-40 items-center justify-center gap-2 text-sm">
                <Loader2 className="h-4 w-4 animate-spin" />
                正在派生关联内容
              </div>
            ) : detail ? (
              <>
                <section className="space-y-2">
                  <div className="flex flex-wrap items-center gap-2">
                    {detail.record.type !== 'paragraph' ? (
                      <Badge>{RECORD_LABELS[detail.record.type]}</Badge>
                    ) : null}
                    {detail.record.status !== 'active' ? (
                      <Badge variant="outline">
                        <LocalizedEnumLabel
                          value={detail.record.status}
                          labels={RECORD_STATUS_LABELS}
                        />
                      </Badge>
                    ) : null}
                    {typeof detail.record.metadata.confidence === 'number' ? (
                      <span className="text-muted-foreground text-xs tabular-nums">
                        置信度 {detail.record.metadata.confidence.toFixed(2)}
                      </span>
                    ) : null}
                    {detail.record.updated_at || detail.record.created_at ? (
                      <span className="text-muted-foreground text-xs">
                        {formatTimestamp(detail.record.updated_at || detail.record.created_at)}
                      </span>
                    ) : null}
                  </div>
                  {detail.record.type !== 'relation' ? (
                    <p className="text-sm leading-6 break-words whitespace-pre-wrap">
                      {detail.record.summary || detail.record.title}
                    </p>
                  ) : null}
                  {detail.record.type !== 'relation' &&
                  (detail.record.source_label || detail.record.source) ? (
                    <div className="text-muted-foreground text-xs break-all">
                      来源：{detail.record.source_label || detail.record.source}
                      {detail.record.source_label &&
                      detail.record.source_label !== detail.record.source ? (
                        <code className="ml-1 text-[11px]">{detail.record.source}</code>
                      ) : null}
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
                    <div className="text-muted-foreground px-1 text-xs font-medium">图投影状态</div>
                    <div className="divide-border/60 divide-y rounded-md border text-xs">
                      {detail.projection.graph_jobs.map((job, index) => {
                        const status = String(job.status || 'pending')
                        return (
                          <div
                            key={`${String(job.relation_hash || 'relation')}:${index}`}
                            className="space-y-1.5 px-3 py-2.5"
                          >
                            <div className="flex flex-wrap items-center gap-2">
                              <Badge variant={status === 'failed' ? 'destructive' : 'secondary'}>
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

                {relatedTabItems.length > 0 ? (
                  <section className="space-y-2">
                    <Tabs value={activeRelatedTab} onValueChange={setRelatedTab}>
                      <MemoryMiniTabs items={relatedTabItems} />
                      <TabsContent value="paragraphs" className="mt-2">
                        <RelatedRecordsList
                          records={detail.related.paragraphs}
                          onSelect={setSelectedRecord}
                        />
                      </TabsContent>
                      <TabsContent value="entities" className="mt-2">
                        <RelatedRecordsList
                          records={detail.related.entities}
                          onSelect={setSelectedRecord}
                        />
                      </TabsContent>
                      <TabsContent value="relations" className="mt-2">
                        <RelatedRecordsList
                          records={detail.related.relations}
                          onSelect={setSelectedRecord}
                        />
                      </TabsContent>
                      <TabsContent value="facts" className="mt-2">
                        <RelatedRecordsList
                          records={detail.related.facts}
                          onSelect={setSelectedRecord}
                        />
                      </TabsContent>
                      <TabsContent value="episodes" className="mt-2">
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
                      </TabsContent>
                      <TabsContent value="profiles" className="mt-2">
                        <div className="divide-border/60 divide-y rounded-md border">
                          {detail.related.profiles.map((profile) => (
                            <button
                              key={`${profile.person_id}:${profile.profile_version}`}
                              type="button"
                              className="hover:bg-muted/60 w-full px-3 py-2.5 text-left transition"
                              onClick={() => triggerAction('profile', detail, profile.person_id)}
                            >
                              <div className="text-sm font-medium">{profile.person_id}</div>
                              <div className="text-muted-foreground mt-1 line-clamp-2 text-xs">
                                {profile.profile_text}
                              </div>
                            </button>
                          ))}
                        </div>
                      </TabsContent>
                    </Tabs>
                  </section>
                ) : null}

                {ledgerTabItems.length > 0 ? (
                  <section className="space-y-2">
                    <div className="text-muted-foreground px-1 text-xs font-medium">事实账本</div>
                    <Tabs value={activeLedgerTab} onValueChange={setLedgerTab}>
                      <MemoryMiniTabs items={ledgerTabItems} />
                      <TabsContent value="evidence" className="mt-2">
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
                      </TabsContent>
                      <TabsContent value="transitions" className="mt-2">
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
                      </TabsContent>
                    </Tabs>
                  </section>
                ) : null}
              </>
            ) : (
              <div className="text-muted-foreground flex h-40 items-center justify-center text-sm">
                详情加载失败
              </div>
            )}
          </DialogBody>
        </DialogContent>
      </Dialog>

      <Dialog
        open={batchEditorOpen}
        onOpenChange={(open) => {
          if (!previewBatchMutation.isPending) setBatchEditorOpen(open)
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>修正所选记忆</DialogTitle>
            <DialogDescription>
              已选择 {checkedRecords.length} 条记忆。先生成预览，检查连带影响后再确认执行。
            </DialogDescription>
          </DialogHeader>
          <ul className="max-h-40 space-y-1 overflow-y-auto text-sm">
            {checkedRecords.map((record) => (
              <li key={`${record.type}:${record.id}`} className="break-words">
                {RECORD_LABELS[record.type]}：{record.title || record.id}
              </li>
            ))}
          </ul>
          <Label htmlFor="batch-memory-correction">修正内容</Label>
          <Textarea
            id="batch-memory-correction"
            value={batchRequest}
            onChange={(event) => setBatchRequest(event.target.value)}
            placeholder="说明这些记忆中哪些信息需要修改，以及正确的信息。"
            disabled={previewBatchMutation.isPending}
          />
          {previewBatchMutation.error ? (
            <Alert variant="destructive">
              <AlertDescription>{previewBatchMutation.error.message}</AlertDescription>
            </Alert>
          ) : null}
          <DialogFooter>
            <Button
              variant="outline"
              disabled={previewBatchMutation.isPending}
              onClick={() => setBatchEditorOpen(false)}
            >
              取消
            </Button>
            <Button
              disabled={
                !batchRequest.trim() || !checkedRecords.length || previewBatchMutation.isPending
              }
              onClick={() => previewBatchMutation.mutate()}
            >
              {previewBatchMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
              生成预览
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

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
