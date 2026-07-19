import { useMemo, useState } from 'react'

import {
  AlertCircle,
  Check,
  CheckCircle2,
  Clock3,
  History,
  Loader2,
  Play,
  RefreshCw,
  RotateCcw,
  Search,
  X,
  WandSparkles,
} from 'lucide-react'

import { Alert, AlertDescription } from '@/components/ui/alert'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { TabsContent } from '@/components/ui/tabs'
import { Textarea } from '@/components/ui/textarea'
import { ThinkingIllustration } from '@/components/ui/thinking-illustration'
import { formatChatAccountLabel, formatChatDisplayName } from '@/lib/chat-display'
import { cn } from '@/lib/utils'
import type {
  MemoryCorrectionCandidatePayload,
  MemoryCorrectionCascadePreviewPayload,
  MemoryCorrectionOperationPayload,
  MemoryCorrectionPlanPayload,
  MemoryCorrectionRelationCascadeAction,
  MemoryCorrectionScope,
  MemoryCorrectionStaleMarkRollbackPayload,
  MemoryCorrectionStatus,
  MemoryCorrectionTargetCascadePayload,
  MemoryImportChatTargetPayload,
} from '@/lib/memory-api'

import { MEMORY_CORRECTION_PAGE_SIZE } from '../constants'
import type { UseMemoryCorrectionResult } from '../hooks/useMemoryCorrection'

export interface CorrectionTabProps {
  correction: UseMemoryCorrectionResult
}

const STATUS_TEXT: Record<string, string> = {
  awaiting_confirmation: '待确认',
  executing: '执行中',
  executed: '已执行',
  failed: '失败',
  rolled_back: '已回滚',
  rollback_failed: '回滚失败',
}

const SCOPE_TEXT: Record<string, string> = {
  person_profile: '人物画像',
  memory: '记忆段落',
}

function formatCorrectionStatus(status?: MemoryCorrectionStatus): string {
  const key = String(status ?? '').trim()
  return (STATUS_TEXT[key] ?? key) || '未知'
}

function formatCorrectionScope(scope?: MemoryCorrectionScope): string {
  const key = String(scope ?? '').trim()
  return (SCOPE_TEXT[key] ?? key) || '未知'
}

function getStatusVariant(status?: MemoryCorrectionStatus): 'default' | 'secondary' | 'destructive' | 'outline' {
  switch (String(status ?? '').trim()) {
    case 'executed':
      return 'secondary'
    case 'failed':
    case 'rollback_failed':
      return 'destructive'
    case 'awaiting_confirmation':
    case 'executing':
      return 'default'
    default:
      return 'outline'
  }
}

function formatTimestamp(value?: number | null): string {
  if (!value) {
    return '-'
  }
  const normalized = value > 10_000_000_000 ? value : value * 1000
  return new Date(normalized).toLocaleString('zh-CN', { hour12: false })
}

function formatConfidence(value?: number | null): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    return '-'
  }
  return value.toFixed(2)
}

function getOperationLabel(operation: MemoryCorrectionOperationPayload): string {
  switch (operation.action) {
    case 'mark_superseded':
      return '标记失效'
    case 'ingest_text':
      return '写入新记忆'
    case 'refresh_person_profile':
      return '刷新画像'
    default:
      return operation.action || '未知操作'
  }
}

function getOperationSummary(operation: MemoryCorrectionOperationPayload): string {
  switch (operation.action) {
    case 'mark_superseded':
      if ('target_type' in operation && 'hash' in operation) {
        return `${operation.target_type}:${operation.hash}`
      }
      return 'reason' in operation ? operation.reason ?? '' : ''
    case 'ingest_text':
      if ('text' in operation) {
        return operation.text
      }
      return 'reason' in operation ? operation.reason ?? '' : ''
    case 'refresh_person_profile':
      if ('person_id' in operation) {
        return operation.person_id
      }
      return ''
    default:
      return 'reason' in operation ? operation.reason ?? '' : ''
  }
}

function formatCascadeRelationAction(action?: MemoryCorrectionRelationCascadeAction): string {
  switch (action) {
    case 'mark_inactive':
      return '关系失效'
    case 'mark_stale_evidence':
      return '标记旧证据'
    case 'skipped_protected':
      return '受保护跳过'
    default:
      return action || '未知处理'
  }
}

function getCascadeSummaryItems(cascade?: MemoryCorrectionCascadePreviewPayload | null): string[] {
  const counts = cascade?.counts
  if (!counts) {
    return []
  }
  return [
    `关系影响 ${counts.relations}`,
    `失效 ${counts.relations_mark_inactive}`,
    `旧证据 ${counts.relations_mark_stale_evidence}`,
    `保护跳过 ${counts.relations_skipped_protected}`,
    `实体影响 ${counts.entities}`,
  ]
}

function getExecutionCascadeSummaryItems(plan: MemoryCorrectionPlanPayload | null): string[] {
  const targets = plan?.execution?.superseded_targets ?? []
  const cascades = targets
    .map((target) => target.cascade)
    .filter((cascade): cascade is MemoryCorrectionTargetCascadePayload => Boolean(cascade))
  if (cascades.length === 0) {
    return []
  }
  const inactive = cascades.reduce((sum, cascade) => sum + cascade.relations_marked_inactive.length, 0)
  const stale = cascades.reduce((sum, cascade) => sum + cascade.relations_marked_stale.length, 0)
  const skipped = cascades.reduce((sum, cascade) => sum + cascade.relations_skipped.length, 0)
  const entities = cascades.reduce((sum, cascade) => sum + cascade.impacted_entities.length, 0)
  return [`级联失效 ${inactive}`, `旧证据 ${stale}`, `保护跳过 ${skipped}`, `受影响实体 ${entities}`]
}

function getRollbackSummaryItems(plan: MemoryCorrectionPlanPayload | null): string[] {
  const rollback = plan?.execution?.rollback
  if (!rollback) {
    return []
  }
  return [
    `恢复目标 ${rollback.restored_targets.length}`,
    `删除旧证据标记 ${rollback.stale_marks_deleted?.length ?? 0}`,
    `恢复旧证据标记 ${rollback.stale_marks_restored?.length ?? 0}`,
    `跳过旧证据标记 ${rollback.stale_marks_skipped?.length ?? 0}`,
  ]
}

function compactTextParts(parts: Array<string | null | undefined>): string[] {
  return parts.map((part) => String(part ?? '').trim()).filter(Boolean)
}

function getChatUserIdLabel(chat: MemoryImportChatTargetPayload): string {
  const userId = String(chat.user_id ?? '').trim()
  if (!userId) {
    return ''
  }

  const platform = String(chat.platform ?? '').trim().toLowerCase()
  if (platform === 'qq') {
    return `QQ ${userId}`
  }
  if (platform === 'wechat' || platform === 'wx') {
    return `微信 ${userId}`
  }
  return `用户 ID ${userId}`
}

function getChatTargetMetaParts(chat: MemoryImportChatTargetPayload): string[] {
  return compactTextParts([
    chat.platform || '未知平台',
    chat.is_group ? '群聊' : '私聊',
    chat.group_id ? `群号 ${chat.group_id}` : '',
    getChatUserIdLabel(chat),
    formatChatAccountLabel(chat.account_id),
  ])
}

function getChatTargetSearchText(chat: MemoryImportChatTargetPayload): string {
  return compactTextParts([
    chat.chat_name,
    chat.platform,
    chat.group_id,
    chat.user_id,
    chat.account_id,
    chat.scope,
    chat.chat_id,
  ])
    .join(' ')
    .toLowerCase()
}

function getChatTargetValueLabel(chat: MemoryImportChatTargetPayload | undefined): string {
  if (!chat) {
    return ''
  }
  const idLabel = chat.group_id || chat.user_id
  const displayName = formatChatDisplayName(chat.chat_name, chat.account_id)
  return idLabel ? `${displayName} · ${idLabel}` : displayName
}

function filterChatTargets(
  targets: MemoryImportChatTargetPayload[],
  query: string,
): MemoryImportChatTargetPayload[] {
  const normalizedQuery = query.trim().toLowerCase()
  if (!normalizedQuery) {
    return targets.slice(0, 6)
  }
  return targets.filter((chat) => getChatTargetSearchText(chat).includes(normalizedQuery)).slice(0, 8)
}

function renderCascadePreview(cascade?: MemoryCorrectionCascadePreviewPayload | null) {
  if (!cascade || (cascade.relations.length === 0 && cascade.entities.length === 0)) {
    return null
  }
  return (
    <div className="space-y-3 rounded-lg border bg-muted/20 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-semibold">联动影响</span>
        {getCascadeSummaryItems(cascade).map((item) => (
          <Badge key={item} variant="outline">{item}</Badge>
        ))}
      </div>
      {cascade.relations.length > 0 ? (
        <div className="grid gap-2">
          {cascade.relations.slice(0, 6).map((relation) => (
            <div key={`${relation.paragraph_hash}:${relation.relation_hash}`} className="rounded-md border bg-background/70 p-2 text-xs">
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant={relation.action === 'skipped_protected' ? 'secondary' : 'outline'}>
                  {formatCascadeRelationAction(relation.action)}
                </Badge>
                <span className="break-words">{relation.subject} / {relation.predicate} / {relation.object}</span>
              </div>
              <div className="mt-1 font-mono text-[11px] break-all text-muted-foreground">{relation.relation_hash}</div>
            </div>
          ))}
        </div>
      ) : null}
      {cascade.entities.length > 0 ? (
        <div className="text-xs text-muted-foreground">
          受影响实体：{cascade.entities.slice(0, 8).map((entity) => entity.name || entity.entity_hash).join('、')}
        </div>
      ) : null}
    </div>
  )
}

function renderRollbackStaleMarks(title: string, items?: MemoryCorrectionStaleMarkRollbackPayload[]) {
  if (!items || items.length === 0) {
    return null
  }
  return (
    <div className="space-y-2">
      <div className="text-xs font-semibold">{title}</div>
      {items.slice(0, 5).map((item) => (
        <div key={`${title}:${item.paragraph_hash}:${item.relation_hash}:${item.action}`} className="rounded-md border bg-background/70 p-2 text-[11px]">
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="outline">{item.action}</Badge>
            <span className="font-mono break-all">{item.paragraph_hash}</span>
          </div>
          <div className="mt-1 font-mono break-all text-muted-foreground">{item.relation_hash}</div>
        </div>
      ))}
    </div>
  )
}

function canExecutePlan(plan: MemoryCorrectionPlanPayload | null): boolean {
  return ['awaiting_confirmation', 'failed'].includes(String(plan?.status ?? ''))
}

function canRollbackPlan(plan: MemoryCorrectionPlanPayload | null): boolean {
  return String(plan?.status ?? '') === 'executed'
}

function getPlanImpactSummary(plan: MemoryCorrectionPlanPayload | null, operationCount: number, candidateCount: number): string[] {
  if (!plan) {
    return []
  }
  return [
    `操作 ${operationCount} 项`,
    `候选证据 ${candidateCount} 条`,
    `范围 ${formatCorrectionScope(plan.scope)}`,
    `置信度 ${formatConfidence(plan.confidence)}`,
  ]
}

function renderCandidate(candidate: MemoryCorrectionCandidatePayload) {
  return (
    <div key={candidate.candidate_id} className="rounded-lg border bg-background/70 p-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant="outline">{candidate.target_type}</Badge>
          {candidate.evidence_type ? <Badge variant="secondary">{candidate.evidence_type}</Badge> : null}
        </div>
        <div className="text-[11px] text-muted-foreground">
          {typeof candidate.score === 'number' ? candidate.score.toFixed(3) : 'score -'}
        </div>
      </div>
      <div className="mt-2 text-sm break-words">{candidate.content || '无内容摘要'}</div>
      <div className="mt-2 grid gap-1 text-[11px] text-muted-foreground">
        <div className="font-mono break-all">{candidate.hash}</div>
        <div className="break-all">来源：{candidate.source || '-'}</div>
      </div>
    </div>
  )
}

function renderOperation(operation: MemoryCorrectionOperationPayload, index: number) {
  return (
    <div key={`${operation.action}:${index}`} className="rounded-lg border bg-background/70 p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Badge variant="outline">{getOperationLabel(operation)}</Badge>
        {'reason' in operation && operation.reason ? (
          <span className="text-[11px] text-muted-foreground break-words">{operation.reason}</span>
        ) : null}
      </div>
      <div className="mt-2 text-sm break-words">{getOperationSummary(operation) || '无操作摘要'}</div>
    </div>
  )
}

export function CorrectionTab({ correction }: CorrectionTabProps) {
  const {
    requestText,
    setRequestText,
    scope,
    setScope,
    personId,
    setPersonId,
    personKeyword,
    setPersonKeyword,
    chatId,
    setChatId,
    candidateLimit,
    setCandidateLimit,
    candidateLimitMax,
    correctionReason,
    setCorrectionReason,
    planSearch,
    setPlanSearch,
    planStatusFilter,
    setPlanStatusFilter,
    planScopeFilter,
    setPlanScopeFilter,
    plans,
    filteredPlans,
    pagedPlans,
    planPage,
    setPlanPage,
    planPageCount,
    selectedPlan,
    selectedPreview,
    selectedPlanLoading,
    selectedPlanError,
    chatTargets,
    chatTargetsLoading,
    chatTargetsErrorText,
    correctionErrorText,
    previewing,
    executingPlanId,
    rollingBackPlanId,
    setSelectedPlanId,
    submitPreview,
    executePlan,
    rollbackPlan,
    refreshPlans,
  } = correction
  const [executeDialogOpen, setExecuteDialogOpen] = useState(false)
  const [rollbackDialogOpen, setRollbackDialogOpen] = useState(false)

  const selectedChatTarget = useMemo(() => {
    const normalizedChatId = chatId.trim()
    if (!normalizedChatId) {
      return undefined
    }
    return chatTargets.find((chat) => chat.chat_id === normalizedChatId)
  }, [chatId, chatTargets])
  const visibleChatTargets = useMemo(
    () => filterChatTargets(chatTargets, chatId),
    [chatId, chatTargets],
  )
  const displayPlan = selectedPlan?.plan_id ? selectedPlan : null
  const displayPreview = displayPlan ? selectedPreview : null
  const executingSelectedPlan = Boolean(displayPlan?.plan_id && executingPlanId === displayPlan.plan_id)
  const rollingBackSelectedPlan = Boolean(displayPlan?.plan_id && rollingBackPlanId === displayPlan.plan_id)
  const selectedOperations = displayPreview?.operations ?? displayPlan?.plan?.operations ?? []
  const selectedCandidates = displayPreview?.candidates ?? []
  const cascadePreview = displayPreview?.cascade_preview ?? displayPlan?.preview?.cascade_preview ?? null
  const executionCascadeSummary = getExecutionCascadeSummaryItems(displayPlan)
  const rollbackSummary = getRollbackSummaryItems(displayPlan)
  const impactSummary = getPlanImpactSummary(displayPlan, selectedOperations.length, selectedCandidates.length)
  const dialogSummary = [...impactSummary, ...getCascadeSummaryItems(cascadePreview)]

  return (
    <TabsContent value="correction" className="space-y-4">
      <div className="grid items-start gap-4 xl:grid-cols-[minmax(0,0.95fr)_minmax(0,1.05fr)] xl:items-stretch">
        <Card className="flex h-full flex-col">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <WandSparkles className="h-4 w-4" />
              记忆修正
            </CardTitle>
            <CardDescription>输入自然语言修正请求，生成预览后再确认写入</CardDescription>
          </CardHeader>
          <CardContent className="flex flex-1 flex-col space-y-4">
            <div className="flex min-h-0 flex-1 flex-col gap-3">
              <div className="flex min-h-[160px] flex-1 flex-col space-y-2">
                <Label htmlFor="memory-correction-request">修正内容</Label>
                <Textarea
                  id="memory-correction-request"
                  className="min-h-[120px] flex-1 resize-none"
                  autoResize={false}
                  value={requestText}
                  onChange={(event) => setRequestText(event.target.value)}
                  placeholder="例如：把张三的常住城市改为杭州，并保留修改原因"
                />
              </div>

              <div className="grid gap-3 lg:grid-cols-[170px_minmax(0,1fr)_minmax(0,1fr)]">
                <div className="space-y-2">
                  <Label>修正范围</Label>
                  <Select value={scope} onValueChange={(value) => setScope(value as MemoryCorrectionScope)}>
                    <SelectTrigger>
                      <SelectValue placeholder="选择范围" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="person_profile">人物画像</SelectItem>
                      <SelectItem value="memory">记忆段落</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-2">
                  <Label htmlFor="memory-correction-person-id">人物 ID</Label>
                  <Input
                    id="memory-correction-person-id"
                    value={personId}
                    onChange={(event) => setPersonId(event.target.value)}
                    placeholder="person_id"
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="memory-correction-person-keyword">人物关键词</Label>
                  <Input
                    id="memory-correction-person-keyword"
                    value={personKeyword}
                    onChange={(event) => setPersonKeyword(event.target.value)}
                    placeholder="昵称 / 备注 / 名称"
                  />
                </div>
              </div>

              <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_140px]">
                <div className="space-y-2">
                  <Label htmlFor="memory-correction-chat-id">聊天流 ID / 名称</Label>
                  <div className="space-y-2">
                    <div className="relative">
                      <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                      <Input
                        id="memory-correction-chat-id"
                        className="pl-9 pr-9"
                        value={chatId}
                        onChange={(event) => setChatId(event.target.value)}
                        placeholder="输入聊天名、群号、QQ号或 session_id"
                      />
                      {chatId.trim() ? (
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          className="absolute right-1 top-1/2 h-7 w-7 -translate-y-1/2"
                          onClick={() => setChatId('')}
                          aria-label="清空聊天流"
                        >
                          <X className="h-4 w-4" />
                        </Button>
                      ) : null}
                    </div>
                    {selectedChatTarget ? (
                      <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                        <Badge variant="secondary">{getChatTargetValueLabel(selectedChatTarget)}</Badge>
                        <span className="font-mono break-all">{selectedChatTarget.chat_id}</span>
                      </div>
                    ) : (
                      <div className="text-xs text-muted-foreground">
                        可直接填写，名称会在后端尝试转换为真实 session_id
                      </div>
                    )}
                    <div className="overflow-hidden rounded-md border bg-background">
                      {chatTargetsLoading ? (
                        <div className="flex items-center gap-2 px-3 py-2 text-xs text-muted-foreground">
                          <Loader2 className="h-3.5 w-3.5 animate-spin" />
                          加载聊天流
                        </div>
                      ) : chatTargetsErrorText ? (
                        <div className="px-3 py-2 text-xs text-destructive">{chatTargetsErrorText}</div>
                      ) : visibleChatTargets.length > 0 ? (
                        <div className="max-h-48 overflow-y-auto">
                          {visibleChatTargets.map((chat) => {
                            const selected = chat.chat_id === chatId.trim()
                            return (
                              <button
                                key={chat.chat_id}
                                type="button"
                                className={cn(
                                  'flex w-full items-start gap-2 px-3 py-2 text-left text-sm transition-colors hover:bg-muted/70',
                                  selected ? 'bg-muted' : '',
                                )}
                                onClick={() => setChatId(chat.chat_id)}
                              >
                                <Check className={cn('mt-0.5 h-4 w-4 shrink-0', selected ? 'opacity-100' : 'opacity-0')} />
                                <span className="min-w-0 flex-1">
                                  <span className="block truncate font-medium">{chat.chat_name || chat.chat_id}</span>
                                  <span className="mt-0.5 block truncate text-xs text-muted-foreground">
                                    {getChatTargetMetaParts(chat).join(' · ') || chat.chat_id}
                                  </span>
                                </span>
                              </button>
                            )
                          })}
                        </div>
                      ) : (
                        <div className="px-3 py-2 text-xs text-muted-foreground">未匹配到聊天流，可继续手动填写</div>
                      )}
                    </div>
                  </div>
                </div>
                <div className="space-y-2">
                  <Label htmlFor="memory-correction-limit">候选上限</Label>
                  <Input
                    id="memory-correction-limit"
                    type="number"
                    min={1}
                    max={candidateLimitMax ?? undefined}
                    value={candidateLimit}
                    onChange={(event) => setCandidateLimit(event.target.value)}
                    placeholder={candidateLimitMax === null ? '按配置默认' : String(candidateLimitMax)}
                  />
                </div>
              </div>

              <div className="space-y-2">
                <Label htmlFor="memory-correction-reason">操作原因</Label>
                <Input
                  id="memory-correction-reason"
                  value={correctionReason}
                  onChange={(event) => setCorrectionReason(event.target.value)}
                  placeholder="可选，写入计划和回滚日志"
                />
              </div>
            </div>

            <div className="flex flex-wrap items-center gap-2">
              <Button onClick={() => void submitPreview()} disabled={previewing}>
                {previewing ? (
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                ) : (
                  <WandSparkles className="mr-2 h-4 w-4" />
                )}
                生成预览
              </Button>
              <Button
                variant="outline"
                onClick={() => setExecuteDialogOpen(true)}
                disabled={!canExecutePlan(displayPlan) || Boolean(executingPlanId)}
              >
                {executingSelectedPlan ? (
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                ) : (
                  <Play className="mr-2 h-4 w-4" />
                )}
                确认执行
              </Button>
              <Button
                variant="outline"
                onClick={() => setRollbackDialogOpen(true)}
                disabled={!canRollbackPlan(displayPlan) || Boolean(rollingBackPlanId)}
              >
                {rollingBackSelectedPlan ? (
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                ) : (
                  <RotateCcw className="mr-2 h-4 w-4" />
                )}
                回滚计划
              </Button>
            </div>

            {correctionErrorText ? (
              <Alert variant="destructive">
                <AlertCircle className="h-4 w-4" />
                <AlertDescription>{correctionErrorText}</AlertDescription>
              </Alert>
            ) : null}
          </CardContent>
        </Card>

        <Card className="flex h-full flex-col">
          <CardHeader className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
            <div>
              <CardTitle className="flex items-center gap-2">
                <CheckCircle2 className="h-4 w-4" />
                当前预览
              </CardTitle>
              <CardDescription>
                {displayPlan ? `计划 ${displayPlan.plan_id}` : '尚未选择记忆修正计划'}
              </CardDescription>
            </div>
            <Button variant="outline" size="sm" onClick={() => void refreshPlans()}>
              <RefreshCw className="mr-2 h-4 w-4" />
              刷新
            </Button>
          </CardHeader>
          <CardContent className="flex flex-1 flex-col space-y-3">
            {displayPlan ? (
              <>
                <div className="flex flex-wrap items-center gap-2">
                  <Badge variant={getStatusVariant(displayPlan.status)}>
                    {formatCorrectionStatus(displayPlan.status)}
                  </Badge>
                  <Badge variant="outline">{formatCorrectionScope(displayPlan.scope)}</Badge>
                  <Badge variant="secondary">置信度 {formatConfidence(displayPlan.confidence)}</Badge>
                  {displayPlan.executed_at ? (
                    <Badge variant="outline">执行于 {formatTimestamp(displayPlan.executed_at)}</Badge>
                  ) : null}
                </div>
                <div className="rounded-lg border bg-muted/20 p-3">
                  <div className="text-sm font-medium break-words">{displayPlan.request_text}</div>
                  <div className="mt-1.5 grid gap-0.5 text-xs text-muted-foreground">
                    <div>人物：{displayPlan.target_person_id || displayPlan.preview?.person_keyword || '-'}</div>
                    <div>聊天流：{displayPlan.target_chat_id || '-'}</div>
                    <div>创建时间：{formatTimestamp(displayPlan.created_at)}</div>
                    <div>原因：{displayPlan.reason || displayPlan.plan?.reason || displayPlan.preview?.reason || '-'}</div>
                  </div>
                </div>

                {renderCascadePreview(cascadePreview)}

                {selectedPlanLoading ? (
                  <div className="rounded-lg border bg-background/60 p-4">
                    <ThinkingIllustration size="sm" />
                  </div>
                ) : null}

                {selectedPlanError ? (
                  <Alert variant="destructive">
                    <AlertCircle className="h-4 w-4" />
                    <AlertDescription>{selectedPlanError}</AlertDescription>
                  </Alert>
                ) : null}

                <div className="grid gap-3 lg:grid-cols-2">
                  <div className="space-y-2">
                    <div className="text-sm font-semibold">待执行操作</div>
                    <ScrollArea
                      className="h-[280px] rounded-lg border 2xl:h-[320px]"
                      scrollbars="vertical"
                      viewportClassName="pointer-events-none"
                    >
                      <div className="space-y-2 p-2.5">
                        {selectedOperations.length > 0 ? selectedOperations.map(renderOperation) : (
                          <div className="rounded-lg border border-dashed bg-muted/20 p-6 text-center text-sm text-muted-foreground">
                            当前计划没有操作
                          </div>
                        )}
                      </div>
                    </ScrollArea>
                  </div>
                  <div className="space-y-2">
                    <div className="text-sm font-semibold">定位候选</div>
                    <ScrollArea
                      className="h-[280px] rounded-lg border 2xl:h-[320px]"
                      scrollbars="vertical"
                      viewportClassName="pointer-events-none"
                    >
                      <div className="space-y-2 p-2.5">
                        {selectedCandidates.length > 0 ? selectedCandidates.map(renderCandidate) : (
                          <div className="rounded-lg border border-dashed bg-muted/20 p-6 text-center text-sm text-muted-foreground">
                            当前计划没有候选证据
                          </div>
                        )}
                      </div>
                    </ScrollArea>
                  </div>
                </div>

                {displayPlan.execution?.error ? (
                  <Alert variant="destructive">
                    <AlertCircle className="h-4 w-4" />
                    <AlertDescription>{displayPlan.execution.error}</AlertDescription>
                  </Alert>
                ) : null}

                {executionCascadeSummary.length > 0 ? (
                  <div className="rounded-lg border bg-muted/20 p-3">
                    <div className="mb-1.5 text-sm font-semibold">执行联动结果</div>
                    <div className="flex flex-wrap gap-2">
                      {executionCascadeSummary.map((summary) => (
                        <Badge key={summary} variant="outline">{summary}</Badge>
                      ))}
                    </div>
                  </div>
                ) : null}

                {displayPlan.execution?.rollback ? (
                  <div className="space-y-2 rounded-lg border bg-muted/20 p-3">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-sm font-semibold">回滚结果</span>
                      {rollbackSummary.map((summary) => (
                        <Badge key={summary} variant="outline">{summary}</Badge>
                      ))}
                    </div>
                    {renderRollbackStaleMarks('删除的旧证据标记', displayPlan.execution.rollback.stale_marks_deleted)}
                    {renderRollbackStaleMarks('恢复的旧证据标记', displayPlan.execution.rollback.stale_marks_restored)}
                    {renderRollbackStaleMarks('跳过的旧证据标记', displayPlan.execution.rollback.stale_marks_skipped)}
                  </div>
                ) : null}
              </>
            ) : (
              <div className="flex min-h-[420px] items-center justify-center rounded-lg border border-dashed bg-muted/20 p-6 text-center text-sm text-muted-foreground">
                生成预览或从历史计划中选择一项
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
          <div>
            <CardTitle className="flex items-center gap-2">
              <History className="h-4 w-4" />
              修正计划
            </CardTitle>
            <CardDescription>最近 {plans.length} 条记忆修正计划</CardDescription>
          </div>
          <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            <Clock3 className="h-3.5 w-3.5" />
            第 {planPage} / {planPageCount} 页，每页 {MEMORY_CORRECTION_PAGE_SIZE} 条
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_180px_180px]">
            <Input
              value={planSearch}
              onChange={(event) => setPlanSearch(event.target.value)}
              placeholder="搜索计划 / 人物 / 聊天流 / 原因"
            />
            <Select value={planStatusFilter} onValueChange={setPlanStatusFilter}>
              <SelectTrigger>
                <SelectValue placeholder="按状态筛选" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">全部状态</SelectItem>
                <SelectItem value="awaiting_confirmation">待确认</SelectItem>
                <SelectItem value="executing">执行中</SelectItem>
                <SelectItem value="executed">已执行</SelectItem>
                <SelectItem value="failed">失败</SelectItem>
                <SelectItem value="rolled_back">已回滚</SelectItem>
                <SelectItem value="rollback_failed">回滚失败</SelectItem>
              </SelectContent>
            </Select>
            <Select value={planScopeFilter} onValueChange={setPlanScopeFilter}>
              <SelectTrigger>
                <SelectValue placeholder="按范围筛选" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">全部范围</SelectItem>
                <SelectItem value="person_profile">人物画像</SelectItem>
                <SelectItem value="memory">记忆段落</SelectItem>
              </SelectContent>
            </Select>
          </div>

          <div className="grid gap-3 xl:grid-cols-2">
            {pagedPlans.length > 0 ? pagedPlans.map((plan) => {
              const isSelected = displayPlan?.plan_id === plan.plan_id
              return (
                <button
                  key={plan.plan_id}
                  type="button"
                  onClick={() => setSelectedPlanId(plan.plan_id)}
                  className={cn(
                    'rounded-xl border p-4 text-left transition-colors',
                    isSelected ? 'border-primary bg-primary/5 shadow-sm' : 'bg-muted/20 hover:border-primary/40 hover:bg-muted/40'
                  )}
                >
                  <div className="flex flex-col gap-3">
                    <div className="flex flex-wrap items-start justify-between gap-2">
                      <div className="flex flex-wrap items-center gap-2">
                        <Badge variant={getStatusVariant(plan.status)}>
                          {formatCorrectionStatus(plan.status)}
                        </Badge>
                        <Badge variant="outline">{formatCorrectionScope(plan.scope)}</Badge>
                      </div>
                      <div className="text-[11px] text-muted-foreground">{formatTimestamp(plan.created_at)}</div>
                    </div>
                    <div className="text-sm font-medium break-words">{plan.request_text || '无修正内容'}</div>
                    <div className="grid gap-1 text-xs text-muted-foreground">
                      <div className="font-mono break-all">{plan.plan_id}</div>
                      <div className="break-all">人物：{plan.target_person_id || plan.preview?.person_keyword || '-'}</div>
                      <div className="break-all">聊天流：{plan.target_chat_id || '-'}</div>
                    </div>
                  </div>
                </button>
              )
            }) : (
              <div className="rounded-lg border border-dashed bg-muted/20 p-6 text-center text-sm text-muted-foreground xl:col-span-2">
                当前筛选条件下没有记忆修正计划
              </div>
            )}
          </div>

          <div className="flex items-center justify-between gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={() => setPlanPage((current) => Math.max(1, current - 1))}
              disabled={planPage <= 1}
            >
              上一页
            </Button>
            <div className="text-xs text-muted-foreground">
              当前命中 {filteredPlans.length} 条记录
            </div>
            <Button
              variant="outline"
              size="sm"
              onClick={() => setPlanPage((current) => Math.min(planPageCount, current + 1))}
              disabled={planPage >= planPageCount}
            >
              下一页
            </Button>
          </div>
        </CardContent>
      </Card>

      <AlertDialog open={executeDialogOpen} onOpenChange={setExecuteDialogOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>确认执行记忆修正</AlertDialogTitle>
            <AlertDialogDescription>
              执行后会按预览计划写入新记忆、标记旧段落或关系失效，并记录执行日志。
            </AlertDialogDescription>
          </AlertDialogHeader>
          {displayPlan ? (
            <div className="space-y-3 rounded-lg border bg-muted/20 p-3 text-sm">
              <div className="font-medium break-words">{displayPlan.request_text || '无修正内容'}</div>
              <div className="font-mono text-[11px] break-all text-muted-foreground">{displayPlan.plan_id}</div>
              <div className="flex flex-wrap gap-2">
                {dialogSummary.map((summary) => (
                  <Badge key={summary} variant="outline">{summary}</Badge>
                ))}
              </div>
            </div>
          ) : null}
          <AlertDialogFooter>
            <AlertDialogCancel disabled={Boolean(executingPlanId)}>取消</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => void executePlan(displayPlan?.plan_id)}
              disabled={!displayPlan || Boolean(executingPlanId)}
            >
              {executingSelectedPlan ? '执行中' : '确认执行'}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog open={rollbackDialogOpen} onOpenChange={setRollbackDialogOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>确认回滚记忆修正</AlertDialogTitle>
            <AlertDialogDescription>
              回滚会尝试撤销本计划写入的新记忆，并恢复被本计划标记失效的目标。
            </AlertDialogDescription>
          </AlertDialogHeader>
          {displayPlan ? (
            <div className="space-y-3 rounded-lg border bg-muted/20 p-3 text-sm">
              <div className="font-medium break-words">{displayPlan.request_text || '无修正内容'}</div>
              <div className="font-mono text-[11px] break-all text-muted-foreground">{displayPlan.plan_id}</div>
              <div className="flex flex-wrap gap-2">
                {dialogSummary.map((summary) => (
                  <Badge key={summary} variant="outline">{summary}</Badge>
                ))}
              </div>
            </div>
          ) : null}
          <AlertDialogFooter>
            <AlertDialogCancel disabled={Boolean(rollingBackPlanId)}>取消</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              onClick={() => void rollbackPlan(displayPlan?.plan_id)}
              disabled={!displayPlan || Boolean(rollingBackPlanId)}
            >
              {rollingBackSelectedPlan ? '回滚中' : '确认回滚'}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </TabsContent>
  )
}
