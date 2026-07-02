import { useMemo, useState } from 'react'

import { ChevronRight, FileText, ListTree, Sparkles } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Checkbox } from '@/components/ui/checkbox'
import { CodeEditor } from '@/components/CodeEditor'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { TabsContent } from '@/components/ui/tabs'

import type { UseMemoryTuningResult } from '../hooks/useMemoryTuning'
import { formatImportTime, getImportStatusVariant } from '../utils'

export interface TuningTabProps {
  tuning: UseMemoryTuningResult
}

type SnapshotViewMode = 'summary' | 'toml'

interface SnapshotEntry {
  path: string
  rawValue: unknown
}

interface SnapshotDiffEntry {
  path: string
  label: string
  runtime: string
  persistable: string
}

interface ReadableSnapshotEntry {
  path: string
  label: string
  value: string
}

interface TuningEvaluationSummary {
  baselineScore?: number
  bestScore?: number
  scoreDelta?: number
  holdoutCaseCount?: number
  reason: string
  recommended: boolean
  hasEvaluation: boolean
  baselineMetrics: Record<string, unknown>
  bestMetrics: Record<string, unknown>
  deltas: Record<string, unknown>
}

const PARAMETER_ORDER = [
  'retrieval.top_k',
  'retrieval.top_k_paragraphs',
  'retrieval.top_k_relations',
  'retrieval.top_k_final',
  'retrieval.alpha',
  'retrieval.enable_ppr',
  'retrieval.ppr_alpha',
  'retrieval.ppr_timeout_seconds',
  'retrieval.search.smart_fallback.enabled',
  'retrieval.sparse.enabled',
  'retrieval.sparse.mode',
  'retrieval.sparse.candidate_k',
  'retrieval.sparse.relation_candidate_k',
  'retrieval.fusion.method',
  'retrieval.fusion.rrf_k',
  'retrieval.fusion.vector_weight',
  'retrieval.fusion.bm25_weight',
  'retrieval.vector_pools.mode',
  'retrieval.vector_pools.paragraph_top_k',
  'retrieval.vector_pools.graph_top_k',
  'retrieval.vector_pools.graph_expand_paragraph_k',
  'retrieval.vector_pools.relation_expand_per_hit',
  'retrieval.vector_pools.entity_expand_per_hit',
  'retrieval.vector_pools.relation_evidence_weight',
  'retrieval.vector_pools.entity_evidence_weight',
  'threshold.percentile',
  'threshold.min_results',
  'threshold.min_threshold',
  'threshold.max_threshold',
  'threshold.enable_auto_adjust',
]

const RESULT_METRICS = [
  { key: 'precision_at_1', labelKey: 'memory.tuning.result.metrics.precisionAt1', format: 'percent' },
  { key: 'recall_at_k', labelKey: 'memory.tuning.result.metrics.recallAtK', format: 'percent' },
  { key: 'empty_rate', labelKey: 'memory.tuning.result.metrics.emptyRate', format: 'percent' },
  { key: 'avg_elapsed_ms', labelKey: 'memory.tuning.result.metrics.avgElapsedMs', format: 'ms' },
] as const

const PARAMETER_KEYS = Object.fromEntries(
  PARAMETER_ORDER.map((path) => [path, path.replaceAll('.', '_')]),
) as Record<string, string>

const VALUE_LABEL_KEYS: Record<string, Record<string, string>> = {
  'retrieval.sparse.mode': {
    auto: 'memory.tuning.values.auto',
    always: 'memory.tuning.values.always',
    off: 'memory.tuning.values.off',
  },
  'retrieval.fusion.method': {
    weighted_rrf: 'memory.tuning.values.weightedRrf',
    rrf: 'memory.tuning.values.rrf',
    alpha_legacy: 'memory.tuning.values.alphaLegacy',
  },
  'retrieval.vector_pools.mode': {
    single: 'memory.tuning.values.singlePool',
    dual: 'memory.tuning.values.dualPool',
  },
}

const TUNING_REASON_KEYS: Record<string, string> = {
  holdout_empty: 'memory.tuning.result.reasons.holdoutEmpty',
  holdout_online_like_validation_failed: 'memory.tuning.result.reasons.holdoutOnlineLikeValidationFailed',
}

function formatSnapshotValue(
  value: unknown,
  t: (key: string, options?: Record<string, unknown>) => string,
  path?: string,
): string {
  if (value === null) {
    return 'null'
  }
  if (value === undefined) {
    return '-'
  }
  if (typeof value === 'boolean') {
    return value ? t('memory.tuning.values.enabled') : t('memory.tuning.values.disabled')
  }
  if (typeof value === 'number') {
    return Number.isFinite(value) ? String(value) : '-'
  }
  if (typeof value === 'string') {
    const valueLabelKey = path ? VALUE_LABEL_KEYS[path]?.[value] : undefined
    if (valueLabelKey) {
      return t(valueLabelKey)
    }
    return value || '""'
  }
  return JSON.stringify(value)
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
}

function numberFrom(value: unknown): number | undefined {
  const numberValue = typeof value === 'number' ? value : Number(value)
  return Number.isFinite(numberValue) ? numberValue : undefined
}

function formatResultValue(value: number | undefined, format: 'number' | 'percent' | 'ms'): string {
  if (value === undefined) {
    return '-'
  }
  if (format === 'percent') {
    return `${(value * 100).toFixed(1)}%`
  }
  if (format === 'ms') {
    return `${value.toFixed(0)} ms`
  }
  return value.toFixed(3)
}

function formatTuningReason(
  reason: string,
  t: (key: string, options?: Record<string, unknown>) => string,
): string {
  const normalized = reason.trim()
  if (!normalized) {
    return ''
  }

  const reasonKey = TUNING_REASON_KEYS[normalized]
  if (reasonKey) {
    return t(reasonKey)
  }
  return t('memory.tuning.result.reasons.unknown', { reason: normalized })
}

function formatResultDelta(value: number | undefined, format: 'number' | 'percent' | 'ms'): string {
  if (value === undefined) {
    return ''
  }
  const prefix = value > 0 ? '+' : ''
  if (format === 'percent') {
    return `${prefix}${(value * 100).toFixed(1)}%`
  }
  if (format === 'ms') {
    return `${prefix}${value.toFixed(0)} ms`
  }
  return `${prefix}${value.toFixed(3)}`
}

function isTuningTaskRecommended(task: Record<string, unknown>): boolean {
  const validation = asRecord(task.validation_summary)
  return validation.recommended === true || task.recommended === true
}

function getTuningEvaluationSummary(task: Record<string, unknown> | undefined): TuningEvaluationSummary | null {
  if (!task) {
    return null
  }

  const validation = asRecord(task.validation_summary)
  const onlineLike = asRecord(validation.online_like)
  const stable = asRecord(validation.stable)
  const evaluationMode = Object.keys(onlineLike).length > 0 ? onlineLike : stable
  const baselineEval = asRecord(evaluationMode.baseline)
  const bestEval = asRecord(evaluationMode.best)
  const baselineMetrics = asRecord(baselineEval.metrics)
  const bestMetrics = asRecord(bestEval.metrics)
  const deltas = asRecord(validation.deltas)
  const baselineScore = numberFrom(baselineEval.score)
  const bestScore = numberFrom(bestEval.score) ?? numberFrom(task.best_score)
  const directScoreDelta = numberFrom(deltas.score)
  const scoreDelta = directScoreDelta ?? (
    baselineScore !== undefined && bestScore !== undefined ? bestScore - baselineScore : undefined
  )
  const holdoutCaseCount = numberFrom(validation.holdout_case_count)
  const recommended = isTuningTaskRecommended(task)
  const hasEvaluation = baselineScore !== undefined
    || bestScore !== undefined
    || Object.keys(baselineMetrics).length > 0
    || Object.keys(bestMetrics).length > 0
    || Object.keys(deltas).length > 0

  return {
    baselineScore,
    bestScore,
    scoreDelta,
    holdoutCaseCount,
    reason: String(validation.reason ?? task.error ?? ''),
    recommended,
    hasEvaluation,
    baselineMetrics,
    bestMetrics,
    deltas,
  }
}

function collectSnapshotEntries(value: unknown, prefix = ''): SnapshotEntry[] {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return prefix ? [{ path: prefix, rawValue: value }] : []
  }

  return Object.entries(value as Record<string, unknown>).flatMap(([key, item]) => {
    const nextPath = prefix ? `${prefix}.${key}` : key
    if (item && typeof item === 'object' && !Array.isArray(item)) {
      return collectSnapshotEntries(item, nextPath)
    }
    return [{ path: nextPath, rawValue: item }]
  })
}

function buildReadableSnapshotEntries(
  entries: SnapshotEntry[],
  t: (key: string, options?: Record<string, unknown>) => string,
): { readableEntries: ReadableSnapshotEntry[], technicalCount: number } {
  const entryMap = new Map(entries.map((entry) => [entry.path, entry.rawValue]))
  const readableEntries = PARAMETER_ORDER
    .filter((path) => entryMap.has(path))
    .map((path) => ({
      path,
      label: t(`memory.tuning.parameters.${PARAMETER_KEYS[path]}`),
      value: formatSnapshotValue(entryMap.get(path), t, path),
    }))
  const technicalCount = entries.filter((entry) => !PARAMETER_KEYS[entry.path]).length

  return { readableEntries, technicalCount }
}

function buildSnapshotDiff(
  runtimeEntries: SnapshotEntry[],
  persistableEntries: SnapshotEntry[],
  t: (key: string, options?: Record<string, unknown>) => string,
): { readableDiffs: SnapshotDiffEntry[], technicalDiffCount: number } {
  const runtimeMap = new Map(runtimeEntries.map((entry) => [entry.path, entry.rawValue]))
  const persistableMap = new Map(persistableEntries.map((entry) => [entry.path, entry.rawValue]))
  const allPaths = Array.from(new Set([...runtimeMap.keys(), ...persistableMap.keys()])).sort()
  const diffPaths = allPaths.filter((path) => {
    const runtimeValue = formatSnapshotValue(runtimeMap.get(path), t, path)
    const persistableValue = formatSnapshotValue(persistableMap.get(path), t, path)
    return runtimeValue !== persistableValue
  })
  const readableDiffs = diffPaths
    .filter((path) => PARAMETER_KEYS[path])
    .map((path) => ({
      path,
      label: t(`memory.tuning.parameters.${PARAMETER_KEYS[path]}`),
      runtime: formatSnapshotValue(runtimeMap.get(path), t, path),
      persistable: formatSnapshotValue(persistableMap.get(path), t, path),
    }))
  const technicalDiffCount = diffPaths.length - readableDiffs.length

  return { readableDiffs, technicalDiffCount }
}

function SnapshotSummarySection({
  title,
  description,
  entries,
  technicalCount,
  technicalNote,
  emptyText,
}: {
  title: string
  description: string
  entries: ReadableSnapshotEntry[]
  technicalCount: number
  technicalNote: string
  emptyText: string
}) {
  return (
    <section className="space-y-3 rounded-md border bg-muted/15 p-4">
      <div>
        <div className="text-sm font-medium">{title}</div>
        <div className="text-xs text-muted-foreground">{description}</div>
      </div>
      {entries.length > 0 ? (
        <div className="grid gap-2 md:grid-cols-2">
          {entries.map((entry) => (
            <div key={entry.path} className="min-w-0 rounded-md border bg-background px-3 py-2">
              <div className="text-xs leading-5 text-muted-foreground">{entry.label}</div>
              <div className="break-all text-sm leading-6">{entry.value}</div>
            </div>
          ))}
        </div>
      ) : (
        <div className="rounded-md border border-dashed p-3 text-sm text-muted-foreground">{emptyText}</div>
      )}
      {technicalCount > 0 ? (
        <div className="rounded-md border border-dashed bg-background/60 p-3 text-xs text-muted-foreground">
          {technicalNote}
        </div>
      ) : null}
    </section>
  )
}

function SnapshotResultCard({
  title,
  description,
}: {
  title: string
  description: string
}) {
  return (
    <div className="min-w-0 rounded-md border bg-muted/15 p-4">
      <div className="text-sm font-medium">{title}</div>
      <div className="mt-2 text-sm leading-6 text-muted-foreground">{description}</div>
    </div>
  )
}

function TuningResultMetricRow({
  label,
  baseline,
  best,
  delta,
  format,
}: {
  label: string
  baseline?: number
  best?: number
  delta?: number
  format: 'percent' | 'ms'
}) {
  return (
    <div className="grid gap-2 rounded-md border bg-background px-3 py-2 sm:grid-cols-[minmax(0,1fr)_auto]">
      <div className="text-sm font-medium">{label}</div>
      <div className="text-sm leading-6 text-muted-foreground sm:text-right">
        {formatResultValue(baseline, format)}
        <span className="mx-1.5">→</span>
        {formatResultValue(best, format)}
        {delta !== undefined ? (
          <span className="ml-2 text-xs">Δ {formatResultDelta(delta, format)}</span>
        ) : null}
      </div>
    </div>
  )
}

function TuningResultOverview({
  task,
  t,
}: {
  task?: Record<string, unknown>
  t: (key: string, options?: Record<string, unknown>) => string
}) {
  if (!task) {
    return (
      <section className="rounded-md border border-dashed p-4 text-sm text-muted-foreground">
        {t('memory.tuning.result.noTask')}
      </section>
    )
  }

  const status = String(task.status ?? '-')
  const statusLabel = t(`memory.tuning.status.${status}`, { defaultValue: status })
  const reasonText = formatTuningReason(String(task.error ?? ''), t)
  const evaluation = getTuningEvaluationSummary(task)
  const isCompleted = status === 'completed'
  const progress = numberFrom(task.progress)
  const roundsDone = numberFrom(task.rounds_done)
  const roundsTotal = numberFrom(task.rounds_total)

  if (!isCompleted || !evaluation?.hasEvaluation) {
    return (
      <section className="space-y-3">
        <div className="grid gap-3 md:grid-cols-3">
          <SnapshotResultCard
            title={t('memory.tuning.result.statusTitle')}
            description={statusLabel}
          />
          <SnapshotResultCard
            title={t('memory.tuning.result.progressTitle')}
            description={progress !== undefined ? `${Math.round(progress)}%` : '-'}
          />
          <SnapshotResultCard
            title={t('memory.tuning.result.roundsTitle')}
            description={roundsDone !== undefined && roundsTotal !== undefined ? `${roundsDone}/${roundsTotal}` : '-'}
          />
        </div>
        {reasonText ? (
          <div className="rounded-md border border-dashed bg-background/60 p-3 text-xs text-muted-foreground">
            {t('memory.tuning.result.errorReason', { reason: reasonText })}
          </div>
        ) : null}
      </section>
    )
  }

  const scoreText = evaluation.baselineScore !== undefined || evaluation.bestScore !== undefined
    ? `${formatResultValue(evaluation.baselineScore, 'number')} → ${formatResultValue(evaluation.bestScore, 'number')}${evaluation.scoreDelta !== undefined ? ` · Δ ${formatResultDelta(evaluation.scoreDelta, 'number')}` : ''}`
    : '-'
  const validationText = evaluation.holdoutCaseCount !== undefined
    ? t('memory.tuning.result.holdoutWithCount', {
      count: evaluation.holdoutCaseCount,
      result: evaluation.recommended ? t('memory.tuning.result.validationPassed') : t('memory.tuning.result.validationFailed'),
    })
    : evaluation.recommended ? t('memory.tuning.result.validationPassed') : t('memory.tuning.result.validationFailed')
  const metricRows = RESULT_METRICS.map((metric) => {
    const baseline = numberFrom(evaluation.baselineMetrics[metric.key])
    const best = numberFrom(evaluation.bestMetrics[metric.key])
    const delta = numberFrom(evaluation.deltas[metric.key]) ?? (
      baseline !== undefined && best !== undefined ? best - baseline : undefined
    )
    return { ...metric, baseline, best, delta }
  }).filter((metric) => metric.baseline !== undefined || metric.best !== undefined || metric.delta !== undefined)

  return (
    <section className="space-y-4">
      <div className="grid gap-3 md:grid-cols-3">
        <SnapshotResultCard
          title={t('memory.tuning.result.recommendationTitle')}
          description={evaluation.recommended ? t('memory.tuning.result.recommended') : t('memory.tuning.result.notRecommended')}
        />
        <SnapshotResultCard
          title={t('memory.tuning.result.scoreTitle')}
          description={scoreText}
        />
        <SnapshotResultCard
          title={t('memory.tuning.result.validationTitle')}
          description={validationText}
        />
      </div>
      {metricRows.length > 0 ? (
        <div className="space-y-2 rounded-md border bg-muted/15 p-4">
          <div>
            <div className="text-sm font-medium">{t('memory.tuning.result.metricsTitle')}</div>
            <div className="text-xs text-muted-foreground">{t('memory.tuning.result.metricsDescription')}</div>
          </div>
          <div className="grid gap-2 xl:grid-cols-2">
            {metricRows.map((metric) => (
              <TuningResultMetricRow
                key={metric.key}
                label={t(metric.labelKey)}
                baseline={metric.baseline}
                best={metric.best}
                delta={metric.delta}
                format={metric.format}
              />
            ))}
          </div>
        </div>
      ) : null}
      {!evaluation.recommended && evaluation.reason ? (
        <div className="rounded-md border border-dashed bg-background/60 p-3 text-xs text-muted-foreground">
          {t('memory.tuning.result.reason', { reason: formatTuningReason(evaluation.reason, t) })}
        </div>
      ) : null}
    </section>
  )
}

function TuningTaskListCard({
  tasks,
  applyBestTask,
  t,
}: {
  tasks: UseMemoryTuningResult['tuningTasks']
  applyBestTask: UseMemoryTuningResult['applyBestTask']
  t: (key: string, options?: Record<string, unknown>) => string
}) {
  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle>{t('memory.tuning.tasks.title')}</CardTitle>
        <CardDescription>{t('memory.tuning.tasks.description')}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {tasks.length > 0 ? (
          <div className="space-y-2" role="list">
            {tasks.map((task, index) => {
              const taskId = String(task.task_id ?? '')
              const status = String(task.status ?? '-')
              const recommended = isTuningTaskRecommended(task)
              const canApply = Boolean(task.task_id) && status === 'completed' && recommended
              const statusLabel = t(`memory.tuning.status.${status}`, { defaultValue: status })
              return (
                <div
                  key={taskId || `tuning-task-${index}`}
                  className="space-y-3 rounded-md border bg-muted/20 p-3 transition-colors hover:bg-muted/30"
                  role="listitem"
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0 space-y-1">
                      <div className="break-all font-mono text-xs leading-5">{taskId || '-'}</div>
                      <div className="text-xs text-muted-foreground">
                        {formatImportTime(Number(task.updated_at ?? task.created_at ?? 0))}
                      </div>
                    </div>
                    <Badge variant={getImportStatusVariant(status)}>{statusLabel}</Badge>
                  </div>
                  <div className="flex items-center justify-between gap-3">
                    <Badge variant={recommended ? 'secondary' : 'outline'}>
                      {recommended ? t('memory.tuning.tasks.recommended') : t('memory.tuning.tasks.notRecommended')}
                    </Badge>
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => void applyBestTask(taskId)}
                      disabled={!canApply}
                    >
                      {t('memory.tuning.actions.applyBest')}
                    </Button>
                  </div>
                </div>
              )
            })}
          </div>
        ) : (
          <div className="rounded-md border border-dashed p-4 text-center text-sm text-muted-foreground">
            {t('memory.tuning.tasks.empty')}
          </div>
        )}
      </CardContent>
    </Card>
  )
}

export function TuningTab({ tuning }: TuningTabProps) {
  const { t } = useTranslation()
  const {
    tuningObjective,
    setTuningObjective,
    tuningIntensity,
    setTuningIntensity,
    tuningSampleSize,
    setTuningSampleSize,
    tuningTopKEval,
    setTuningTopKEval,
    persistBestProfile,
    setPersistBestProfile,
    submitTuningTask,
    creatingTuning,
    tuningProfile,
    tuningProfileToml,
    tuningTasks,
    applyBestTask,
  } = tuning
  const [snapshotViewMode, setSnapshotViewMode] = useState<SnapshotViewMode>('summary')
  const runtimeEntries = useMemo(() => collectSnapshotEntries(tuningProfile.runtime), [tuningProfile.runtime])
  const persistableEntries = useMemo(() => collectSnapshotEntries(tuningProfile.persistable), [tuningProfile.persistable])
  const { readableEntries: readableRuntimeEntries, technicalCount: runtimeTechnicalCount } = useMemo(
    () => buildReadableSnapshotEntries(runtimeEntries, t),
    [runtimeEntries, t],
  )
  const { readableEntries: readablePersistableEntries, technicalCount: persistableTechnicalCount } = useMemo(
    () => buildReadableSnapshotEntries(persistableEntries, t),
    [persistableEntries, t],
  )
  const { readableDiffs, technicalDiffCount } = useMemo(
    () => buildSnapshotDiff(runtimeEntries, persistableEntries, t),
    [persistableEntries, runtimeEntries, t],
  )
  const resultTask = useMemo(
    () => tuningTasks.find((task) => String(task.status ?? '') === 'completed') ?? tuningTasks[0],
    [tuningTasks],
  )
  const runtimeResultText = runtimeEntries.length > 0
    ? t('memory.tuning.snapshot.runtimeResultApplied')
    : t('memory.tuning.snapshot.runtimeResultEmpty')
  const persistableResultText = persistableEntries.length > 0
    ? t('memory.tuning.snapshot.persistableResultReady')
    : t('memory.tuning.snapshot.persistableResultEmpty')
  const hasConfigDiff = readableDiffs.length + technicalDiffCount > 0
  const diffResultText = hasConfigDiff
    ? t('memory.tuning.snapshot.diffResultChanged')
    : t('memory.tuning.snapshot.diffResultClean')
  const isSummaryView = snapshotViewMode === 'summary'
  const toggleSnapshotView = () => setSnapshotViewMode(isSummaryView ? 'toml' : 'summary')
  const SnapshotToggleIcon = isSummaryView ? FileText : ListTree

  return (
    <TabsContent value="tuning" className="space-y-4">
      <div className="grid items-start gap-4 xl:grid-cols-[360px_minmax(0,1fr)] 2xl:grid-cols-[400px_minmax(0,1fr)]">
        <div className="space-y-4 xl:sticky xl:top-4 xl:self-start">
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="flex items-center gap-2">
                <Sparkles className="h-4 w-4" />
                {t('memory.tuning.task.title')}
              </CardTitle>
              <CardDescription>{t('memory.tuning.task.description')}</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="space-y-3">
                <div className="text-sm font-medium">{t('memory.tuning.form.strategy')}</div>
                <div className="grid gap-3">
                  <div className="space-y-2">
                    <Label>{t('memory.tuning.form.objective')}</Label>
                    <Select value={tuningObjective} onValueChange={setTuningObjective}>
                      <SelectTrigger><SelectValue /></SelectTrigger>
                      <SelectContent>
                        <SelectItem value="precision_priority">{t('memory.tuning.objectives.precision')}</SelectItem>
                        <SelectItem value="balanced">{t('memory.tuning.objectives.balanced')}</SelectItem>
                        <SelectItem value="recall_priority">{t('memory.tuning.objectives.recall')}</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="space-y-2">
                    <Label>{t('memory.tuning.form.intensity')}</Label>
                    <Select value={tuningIntensity} onValueChange={setTuningIntensity}>
                      <SelectTrigger><SelectValue /></SelectTrigger>
                      <SelectContent>
                        <SelectItem value="quick">{t('memory.tuning.intensity.quick')}</SelectItem>
                        <SelectItem value="standard">{t('memory.tuning.intensity.standard')}</SelectItem>
                        <SelectItem value="deep">{t('memory.tuning.intensity.deep')}</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                </div>
              </div>
              <div className="space-y-3 border-t pt-4">
                <div className="text-sm font-medium">{t('memory.tuning.form.evalScope')}</div>
                <div className="grid gap-3 sm:grid-cols-2">
                  <div className="space-y-2">
                    <Label>{t('memory.tuning.form.sampleSize')}</Label>
                    <Input type="number" value={tuningSampleSize} onChange={(event) => setTuningSampleSize(event.target.value)} />
                  </div>
                  <div className="space-y-2">
                    <Label>{t('memory.tuning.form.topKEval')}</Label>
                    <Input type="number" value={tuningTopKEval} onChange={(event) => setTuningTopKEval(event.target.value)} />
                  </div>
                </div>
              </div>
              <div className="flex items-center gap-3 border-t pt-4">
                <Checkbox
                  id="persist-best-profile"
                  checked={persistBestProfile}
                  onCheckedChange={(checked) => setPersistBestProfile(checked === true)}
                />
                <Label htmlFor="persist-best-profile">{t('memory.tuning.form.persist')}</Label>
              </div>
              <Button className="w-full" onClick={() => void submitTuningTask()} disabled={creatingTuning}>
                <Sparkles className="mr-2 h-4 w-4" />
                {t('memory.tuning.actions.createTask')}
              </Button>
            </CardContent>
          </Card>
        </div>

        <div className="min-w-0 space-y-4">
          <Card className="min-w-0 self-start">
            <CardHeader className="gap-3 sm:flex-row sm:items-start sm:justify-between">
              <div>
                <CardTitle>{t('memory.tuning.snapshot.title')}</CardTitle>
                <CardDescription>
                  {isSummaryView ? t('memory.tuning.snapshot.summaryDescription') : t('memory.tuning.snapshot.tomlDescription')}
                </CardDescription>
              </div>
              {!isSummaryView ? (
                <Button type="button" variant="outline" size="sm" className="w-full sm:w-auto" onClick={toggleSnapshotView}>
                  <SnapshotToggleIcon className="h-4 w-4" />
                  {t('memory.tuning.actions.showSummary')}
                </Button>
              ) : null}
            </CardHeader>
            <CardContent className="space-y-4">
              {snapshotViewMode === 'summary' ? (
                <>
                  <TuningResultOverview task={resultTask} t={t} />

                  <details className="group overflow-hidden rounded-md border bg-background">
                    <summary className="flex cursor-pointer list-none flex-col gap-1 px-4 py-3 text-sm font-medium outline-none transition-colors hover:bg-muted/30 focus-visible:ring-2 focus-visible:ring-ring sm:flex-row sm:items-center [&::-webkit-details-marker]:hidden">
                      <span className="flex min-w-0 items-center gap-2">
                        <ChevronRight className="h-4 w-4 shrink-0 transition-transform group-open:rotate-90" />
                        <span>{t('memory.tuning.snapshot.detailsTitle')}</span>
                      </span>
                      <span className="text-xs font-normal text-muted-foreground sm:ml-auto">
                        {t('memory.tuning.snapshot.detailsHint')}
                      </span>
                    </summary>
                    <div className="border-t">
                      <section className="grid gap-3 p-4 md:grid-cols-3">
                        <SnapshotResultCard
                          title={t('memory.tuning.snapshot.runtimeTitle')}
                          description={runtimeResultText}
                        />
                        <SnapshotResultCard
                          title={t('memory.tuning.snapshot.persistableTitle')}
                          description={persistableResultText}
                        />
                        <SnapshotResultCard
                          title={t('memory.tuning.snapshot.diffTitle')}
                          description={diffResultText}
                        />
                      </section>
                      <div className="flex items-center justify-between gap-3 border-t px-4 py-3">
                        <div className="text-sm font-medium">{t('memory.tuning.snapshot.parameterDetailsTitle')}</div>
                        <Button type="button" variant="ghost" size="sm" onClick={toggleSnapshotView}>
                          <FileText className="h-4 w-4" />
                          {t('memory.tuning.actions.showToml')}
                        </Button>
                      </div>
                      <div className="max-h-[30vh] overflow-y-auto overscroll-contain border-t px-4 py-4 pr-3">
                        <div className="space-y-4">
                          <SnapshotSummarySection
                            title={t('memory.tuning.snapshot.runtimeTitle')}
                            description={t('memory.tuning.snapshot.runtimeDescription')}
                            entries={readableRuntimeEntries}
                            technicalCount={runtimeTechnicalCount}
                            technicalNote={t('memory.tuning.snapshot.technicalCount', { count: runtimeTechnicalCount })}
                            emptyText={t('memory.tuning.snapshot.empty')}
                          />
                          <SnapshotSummarySection
                            title={t('memory.tuning.snapshot.persistableTitle')}
                            description={t('memory.tuning.snapshot.persistableDescription')}
                            entries={readablePersistableEntries}
                            technicalCount={persistableTechnicalCount}
                            technicalNote={t('memory.tuning.snapshot.technicalCount', { count: persistableTechnicalCount })}
                            emptyText={t('memory.tuning.snapshot.empty')}
                          />
                          <section className="space-y-3 rounded-md border bg-background p-4">
                            <div>
                              <div className="text-sm font-medium">{t('memory.tuning.snapshot.diffTitle')}</div>
                              <div className="text-xs text-muted-foreground">{t('memory.tuning.snapshot.diffDescription')}</div>
                            </div>
                            {readableDiffs.length > 0 ? (
                              <div className="space-y-2">
                                {readableDiffs.map((entry) => (
                                  <div key={entry.path} className="grid gap-2 rounded-md border bg-background p-3 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
                                    <div className="text-xs leading-5 text-muted-foreground lg:col-span-2">{entry.label}</div>
                                    <div className="min-w-0">
                                      <div className="text-[11px] text-muted-foreground">{t('memory.tuning.snapshot.runtimeShort')}</div>
                                      <div className="break-all text-sm leading-6">{entry.runtime}</div>
                                    </div>
                                    <div className="min-w-0">
                                      <div className="text-[11px] text-muted-foreground">{t('memory.tuning.snapshot.persistableShort')}</div>
                                      <div className="break-all text-sm leading-6">{entry.persistable}</div>
                                    </div>
                                  </div>
                                ))}
                              </div>
                            ) : (
                              <div className="rounded-md border border-dashed p-3 text-sm text-muted-foreground">
                                {t('memory.tuning.snapshot.noDiff')}
                              </div>
                            )}
                            {technicalDiffCount > 0 ? (
                              <div className="rounded-md border border-dashed bg-background/60 p-3 text-xs text-muted-foreground">
                                {t('memory.tuning.snapshot.technicalDiffCount', { count: technicalDiffCount })}
                              </div>
                            ) : null}
                          </section>
                        </div>
                      </div>
                    </div>
                  </details>
                </>
              ) : (
                <CodeEditor
                  value={tuningProfileToml}
                  language="toml"
                  readOnly
                  height="640px"
                />
              )}
            </CardContent>
          </Card>

          <TuningTaskListCard tasks={tuningTasks} applyBestTask={applyBestTask} t={t} />
        </div>
      </div>
    </TabsContent>
  )
}
