import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Loader2, RefreshCw } from 'lucide-react'
import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Label } from '@/components/ui/label'
import { Progress } from '@/components/ui/progress'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import {
  cancelJargonInference,
  getJargonInferenceJob,
  getJargonInferenceJobs,
  previewJargonInference,
  retryJargonInference,
  startJargonInference,
} from '@/lib/jargon-inference-api'
import type { JargonChatInfo } from '@/types/jargon'
import type {
  JargonInferenceFilters,
  JargonInferenceJob,
  JargonInferenceRequest,
} from '@/types/jargon-inference'

import { InferenceChoice } from './JargonInferenceControls'
import { JargonInferenceSettingsPanel } from './JargonInferenceSettings'
import { InferenceStatus } from './JargonInferenceStatus'
import { inferenceTime } from '@/lib/jargon-inference-format'

interface Props {
  open: boolean
  onOpenChange: (open: boolean) => void
  selectedIds: number[]
  pageIds: number[]
  filters: JargonInferenceFilters
  scopeValid: boolean
  total: number
  chats: JargonChatInfo[]
  onChanged: () => void
}

export function JargonInferenceDialog({
  open,
  onOpenChange,
  selectedIds,
  pageIds,
  filters,
  scopeValid,
  total,
  chats,
  onChanged,
}: Props) {
  const { t } = useTranslation()
  const [tab, setTab] = useState('batch')
  const [scope, setScope] = useState(selectedIds.length ? 'selected' : 'page')
  const [mode, setMode] = useState<'missing' | 'all'>('missing')
  const [force, setForce] = useState(true)
  const [lastStatus, setLastStatus] = useState('any')
  const [jobId, setJobId] = useState<string | null>(null)
  const queryClient = useQueryClient()

  // An explicit empty ID list must never become an unfiltered, cross-page request.
  const request = useMemo<JargonInferenceRequest>(
    () => ({
      ...(scope === 'all' ? { filters } : { ids: scope === 'selected' ? selectedIds : pageIds }),
      mode,
      force,
      ...(lastStatus !== 'any' ? { last_status: lastStatus } : {}),
    }),
    [scope, filters, selectedIds, pageIds, mode, force, lastStatus]
  )
  const validScope = scope === 'all' ? scopeValid : (request.ids?.length ?? 0) > 0
  const preview = useQuery({
    queryKey: ['jargon-inference', 'preview', request],
    queryFn: () => previewJargonInference(request),
    enabled: open && tab === 'batch' && validScope,
    retry: false,
  })
  const jobs = useQuery({
    queryKey: ['jargon-inference', 'jobs'],
    queryFn: getJargonInferenceJobs,
    enabled: open,
    refetchInterval: open ? 3000 : false,
  })
  const selectedJobId = jobId ?? jobs.data?.items[0]?.id ?? null
  const start = useMutation({
    mutationFn: () => startJargonInference(request),
    onSuccess: (job) => {
      setJobId(job.id)
      setTab('jobs')
      void queryClient.invalidateQueries({ queryKey: ['jargon-inference'] })
      onChanged()
    },
  })
  const selectJob = (job: JargonInferenceJob) => {
    setJobId(job.id)
    setTab('jobs')
    void queryClient.invalidateQueries({ queryKey: ['jargon-inference'] })
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        onOpenChange(next)
        if (!next) onChanged()
      }}
    >
      <DialogContent className="[--dialog-width:54rem]">
        <DialogHeader>
          <DialogTitle>{t('jargonInference.title', '黑话 AI 推断')}</DialogTitle>
          <DialogDescription>
            {t(
              'jargonInference.description',
              '对已有词条补全或更新含义。任务在后台运行，关闭页面后仍可查看结果。'
            )}
          </DialogDescription>
        </DialogHeader>
        <Tabs value={tab} onValueChange={setTab} className="flex min-h-0 flex-1 flex-col">
          <TabsList className="w-full shrink-0">
            <TabsTrigger value="batch">{t('jargonInference.batch', '批量推断')}</TabsTrigger>
            <TabsTrigger value="jobs">{t('jargonInference.jobs', '任务与进度')}</TabsTrigger>
            <TabsTrigger value="settings">
              {t('jargonInference.settings', '触发与定时设置')}
            </TabsTrigger>
          </TabsList>
          <DialogBody className="mt-4">
            <TabsContent value="batch" className="mt-0 space-y-4">
              <InferenceChoice
                id="inference-scope"
                label={t('jargonInference.scope', '处理范围')}
                value={scope}
                onChange={setScope}
                options={[
                  {
                    value: 'selected',
                    label: t('jargonInference.selected', {
                      defaultValue: '已勾选词条（{{count}} 条，含跨页选择）',
                      count: selectedIds.length,
                    }),
                    disabled: !selectedIds.length,
                  },
                  {
                    value: 'page',
                    label: t('jargonInference.page', {
                      defaultValue: '当前页（{{count}} 条）',
                      count: pageIds.length,
                    }),
                    disabled: !pageIds.length,
                  },
                  {
                    value: 'all',
                    label: t('jargonInference.allFiltered', {
                      defaultValue: '当前筛选下的全部结果（{{count}} 条，跨页）',
                      count: total,
                    }),
                    disabled: !scopeValid,
                  },
                ]}
              />
              <InferenceChoice
                id="inference-mode"
                label={t('jargonInference.mode', '推断方式')}
                value={mode}
                onChange={(value) => setMode(value as 'missing' | 'all')}
                options={[
                  { value: 'missing', label: t('jargonInference.missingOnly', '只补全空含义') },
                  {
                    value: 'all',
                    label: t('jargonInference.recompute', '重新推断，包括已有 AI 含义'),
                  },
                ]}
              />
              <InferenceChoice
                id="inference-last-status"
                label={t('jargonInference.lastStatus', '上次推断状态')}
                value={lastStatus}
                onChange={setLastStatus}
                options={[
                  { value: 'any', label: t('jargonInference.anyStatus', '全部状态') },
                  {
                    value: 'unattempted',
                    label: t('jargonInference.states.unattempted', '未推断'),
                  },
                  { value: 'failed', label: t('jargonInference.states.failed', '请求失败') },
                  {
                    value: 'insufficient_info',
                    label: t('jargonInference.states.insufficient_info', '信息不足'),
                  },
                  {
                    value: 'missing_evidence',
                    label: t('jargonInference.states.missing_evidence', '缺少证据'),
                  },
                  { value: 'succeeded', label: t('jargonInference.states.succeeded', '推断成功') },
                  {
                    value: 'interrupted',
                    label: t('jargonInference.states.interrupted', '运行中断'),
                  },
                  { value: 'skipped', label: t('jargonInference.states.skipped', '已跳过') },
                ]}
              />
              <div className="flex items-start gap-2">
                <Checkbox
                  id="inference-force"
                  checked={force}
                  onCheckedChange={(value) => setForce(value === true)}
                />
                <Label htmlFor="inference-force" className="leading-5">
                  {t(
                    'jargonInference.force',
                    '忽略自动次数阈值，立即尝试推断（仍保护人工修改和锁定词条）'
                  )}
                </Label>
              </div>
              {!validScope && (
                <p role="status" className="text-muted-foreground text-sm">
                  {t('jargonInference.noScope', '当前范围没有可处理词条，请更换范围或先选择词条。')}
                </p>
              )}
              {validScope && preview.isFetching && (
                <p role="status" className="text-muted-foreground flex items-center gap-2 text-sm">
                  <Loader2 className="h-4 w-4 animate-spin" />
                  {t('jargonInference.previewing', '正在检查候选词条…')}
                </p>
              )}
              {validScope && preview.error && (
                <p role="alert" className="text-destructive text-sm">
                  {preview.error.message}
                </p>
              )}
              {validScope && preview.data && !preview.isFetching && (
                <div className="bg-muted/40 space-y-2 rounded-md border p-3" aria-live="polite">
                  <p className="text-sm font-medium">
                    {t('jargonInference.previewCounts', {
                      defaultValue:
                        '候选 {{total}} 条 · 可执行 {{eligible}} 条 · 跳过 {{skipped}} 条',
                      ...preview.data,
                    })}
                  </p>
                  {Object.entries(preview.data.reasons).map(([reason, count]) => (
                    <p key={reason} className="text-muted-foreground text-xs">
                      <InferenceStatus status={reason} /> · {count}
                    </p>
                  ))}
                </div>
              )}
              <p className="text-muted-foreground text-xs">
                {t(
                  'jargonInference.costHint',
                  '每个词条可能调用多次模型。候选状态会在真正执行前再次检查。'
                )}
              </p>
              {start.error && (
                <p role="alert" className="text-destructive text-sm">
                  {start.error.message}
                </p>
              )}
              <Button
                disabled={
                  !validScope ||
                  !preview.isSuccess ||
                  preview.isFetching ||
                  !preview.data?.eligible ||
                  start.isPending
                }
                onClick={() => start.mutate()}
              >
                {start.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
                {t('jargonInference.start', '开始批量推断')}
              </Button>
            </TabsContent>
            <TabsContent value="jobs" className="mt-0 space-y-4">
              {jobs.error && (
                <p role="alert" className="text-destructive text-sm">
                  {jobs.error.message}
                </p>
              )}
              {jobs.isPending && <p role="status">{t('jargonInference.loading', '正在加载…')}</p>}
              <div className="flex items-center gap-2">
                <div className="min-w-0 flex-1">
                  <InferenceChoice
                    id="inference-job"
                    label={t('jargonInference.selectJob', '任务')}
                    value={selectedJobId ?? ''}
                    onChange={setJobId}
                    options={(jobs.data?.items ?? []).map((job) => ({
                      value: job.id,
                      label: `${inferenceTime(job.created_at)} · ${t('jargonInference.sources.' + job.source, job.source)} · ${job.completed}/${job.total}`,
                    }))}
                  />
                </div>
                <Button
                  className="self-end"
                  variant="outline"
                  size="icon"
                  aria-label={t('jargonInference.refresh', '刷新')}
                  onClick={() =>
                    void queryClient.invalidateQueries({ queryKey: ['jargon-inference'] })
                  }
                >
                  <RefreshCw className="h-4 w-4" />
                </Button>
              </div>
              {jobs.data?.items.length === 0 && (
                <p className="text-muted-foreground text-sm">
                  {t('jargonInference.noJobs', '暂无推断任务。')}
                </p>
              )}
              {selectedJobId && (
                <JobProgress
                  key={selectedJobId}
                  jobId={selectedJobId}
                  onRetry={selectJob}
                  onChanged={onChanged}
                />
              )}
            </TabsContent>
            <TabsContent value="settings" className="mt-0">
              <JargonInferenceSettingsPanel chats={chats} onRescan={selectJob} />
            </TabsContent>
          </DialogBody>
        </Tabs>
      </DialogContent>
    </Dialog>
  )
}

function JobProgress({
  jobId,
  onRetry,
  onChanged,
}: {
  jobId: string
  onRetry: (job: JargonInferenceJob) => void
  onChanged: () => void
}) {
  const { t } = useTranslation()
  const [page, setPage] = useState(1)
  const queryClient = useQueryClient()
  const detail = useQuery({
    queryKey: ['jargon-inference', 'job', jobId, page],
    queryFn: () => getJargonInferenceJob(jobId, page),
    refetchInterval: (query) =>
      ['queued', 'running', 'cancelling'].includes(query.state.data?.job.status ?? '')
        ? 2000
        : false,
  })
  const cancel = useMutation({
    mutationFn: () => cancelJargonInference(jobId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['jargon-inference'] })
      onChanged()
    },
  })
  const retry = useMutation({ mutationFn: () => retryJargonInference(jobId), onSuccess: onRetry })
  const job = detail.data?.job
  const active = ['queued', 'running', 'cancelling'].includes(job?.status ?? '')
  return (
    <div className="space-y-3 rounded-md border p-3">
      {detail.error && (
        <p role="alert" className="text-destructive text-sm">
          {detail.error.message}
        </p>
      )}
      {detail.isPending && <p role="status">{t('jargonInference.loading', '正在加载…')}</p>}
      {job && (
        <>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <Badge variant="secondary">
              <InferenceStatus status={job.status} />
            </Badge>
            <span className="text-sm tabular-nums">
              {job.completed} / {job.total}
            </span>
          </div>
          <Progress
            value={job.total ? (job.completed / job.total) * 100 : 0}
            aria-label={t('jargonInference.progress', '推断进度')}
          />
          <div className="flex flex-wrap gap-2">
            {Object.entries(job.counts).map(([state, count]) => (
              <Badge key={state} variant="outline">
                <InferenceStatus status={state} /> {count}
              </Badge>
            ))}
          </div>
          <div className="flex flex-wrap gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={!active || cancel.isPending || job.status === 'cancelling'}
              onClick={() => cancel.mutate()}
            >
              {t('jargonInference.cancelPending', '取消未开始任务')}
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={active || retry.isPending || !(job.counts.failed || job.counts.interrupted)}
              onClick={() => retry.mutate()}
            >
              {t('jargonInference.retryFailed', '重试失败项')}
            </Button>
          </div>
          {(cancel.error || retry.error) && (
            <p role="alert" className="text-destructive text-sm">
              {(cancel.error || retry.error)?.message}
            </p>
          )}
          <ul className="divide-y" aria-label={t('jargonInference.results', '词条处理结果')}>
            {detail.data?.items.map((item) => (
              <li key={item.jargon_id} className="space-y-1 py-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="font-medium">{item.content}</span>
                  <Badge variant="outline">
                    <InferenceStatus status={item.status} />
                  </Badge>
                </div>
                <p className="text-muted-foreground text-xs">
                  {item.session_name} ·{' '}
                  {t('jargonInference.requestCounts', {
                    defaultValue: '{{attempts}} 次尝试 / {{requests}} 次模型请求',
                    attempts: item.attempts,
                    requests: item.requests,
                  })}
                </p>
                {item.reason && (
                  <p className="text-muted-foreground text-xs break-words">{item.reason}</p>
                )}
              </li>
            ))}
          </ul>
          <div className="flex items-center justify-end gap-2">
            <Button
              size="sm"
              variant="outline"
              disabled={page === 1}
              onClick={() => setPage((value) => value - 1)}
            >
              {t('jargonInference.previous', '上一页')}
            </Button>
            <span className="text-sm">
              {page} / {Math.max(1, Math.ceil((detail.data?.total ?? 0) / 50))}
            </span>
            <Button
              size="sm"
              variant="outline"
              disabled={page * 50 >= (detail.data?.total ?? 0)}
              onClick={() => setPage((value) => value + 1)}
            >
              {t('jargonInference.next', '下一页')}
            </Button>
          </div>
        </>
      )}
    </div>
  )
}
