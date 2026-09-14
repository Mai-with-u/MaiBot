import { AlertTriangle, Loader2, ScanSearch, Search, Wrench } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'

import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { useToast } from '@/hooks/use-toast'
import {
  backfillMemoryImages,
  getImageWritebackJobs,
  retryImageWritebackJobs,
  type ImageWritebackJob,
  getMemoryImageContentUrl,
  getMemoryImageJobs,
  reindexMemoryImages,
  searchMemoryImages,
  type MemoryImageBackfillPayload,
  type MemoryImageJobPayload,
  type MemoryImageSearchPayload,
  type MemoryImageStatusPayload,
} from '@/lib/memory-api'

const labels: Record<string, string> = {
  ready: '可恢复',
  processed: '已回填',
  missing_file: '文件缺失',
  missing_chat: '聊天流缺失',
  missing_source: '来源缺失',
  invalid_image: '图片不支持或损坏',
  write_failed: '写入失败',
  pending: '待处理',
  running: '处理中',
  failed: '失败',
  done: '已完成',
  aborted: '已终止',
}

interface ImageSearchPanelProps {
  assetId: string
  status: MemoryImageStatusPayload | null
  onSelect: (id: string) => void
}

export function ImageSearchPanel({ assetId, status, onSelect }: ImageSearchPanelProps) {
  const [threshold, setThreshold] = useState('0.72')
  const [searching, setSearching] = useState(false)
  const [result, setResult] = useState<MemoryImageSearchPayload | null>(null)
  const [error, setError] = useState('')
  const searchGeneration = useRef(0)
  const resultSelection = useRef('')

  useEffect(() => {
    searchGeneration.current += 1
    // 查看匹配图片时保留本次结果，便于继续对比其他匹配项。
    if (resultSelection.current !== assetId) setResult(null)
    resultSelection.current = ''
    setSearching(false)
    setError('')
  }, [assetId])

  const search = async () => {
    const value = Number(threshold)
    if (!Number.isFinite(value) || value < -1 || value > 1) {
      setError('阈值必须介于-1与1之间')
      return
    }
    const generation = ++searchGeneration.current
    setSearching(true)
    setError('')
    try {
      const payload = await searchMemoryImages(assetId, value)
      if (generation === searchGeneration.current) setResult(payload)
    } catch (reason) {
      if (generation === searchGeneration.current)
        setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      if (generation === searchGeneration.current) setSearching(false)
    }
  }

  return (
    <Card>
      <CardHeader className="border-b pb-3">
        <CardTitle className="flex items-center gap-2 text-sm">
          <Search className="h-4 w-4" />
          相似图片检索
        </CardTitle>
        <CardDescription>
          {assetId
            ? '查找麦麦是否见过这张图片，或记住过类似的图片。'
            : '先在图片列表中选择一张图片，再查找相似内容。'}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3 pt-4">
        {error && (
          <Alert variant="destructive">
            <AlertTriangle className="h-4 w-4" />
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}
        <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-end md:grid-cols-1">
          <details className="text-muted-foreground text-xs">
            <summary className="cursor-pointer">调整匹配范围</summary>
            <label htmlFor="image-search-threshold" className="mt-2 block space-y-1.5 text-sm">
              <span className="font-medium">相似度阈值</span>
              <Input
                id="image-search-threshold"
                type="number"
                min={-1}
                max={1}
                step={0.01}
                value={threshold}
                onChange={(event) => setThreshold(event.target.value)}
              />
            </label>
            <p className="mt-2">默认即可使用。数值越高，匹配越严格。</p>
          </details>
          <Button disabled={!assetId || searching} onClick={() => void search()}>
            {searching ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Search className="h-4 w-4" />
            )}
            {searching ? '检索中' : '查找同图与相似图'}
          </Button>
        </div>
        <p className="text-muted-foreground text-xs">
          管理员检索范围包含全部图片记忆；相似度不代表身份确认。
        </p>
        {result && (
          <div className="space-y-3 border-t pt-3">
            <p className="text-sm font-medium">上次检索结果</p>
            <p className="text-xs">
              找到 {result.hits.length} 张图片、{result.related_memory_count} 条关联记忆。
            </p>
            {result.hits.length === 0 && (
              <p className="text-muted-foreground text-sm">
                没有找到匹配图片。可以换一张图片，或在调整匹配范围中降低相似度阈值。
              </p>
            )}
            <details className="text-muted-foreground text-xs">
              <summary className="cursor-pointer">检索耗时详情</summary>
              {Object.entries(result.timings_ms)
                .map(
                  ([key, value]) =>
                    `${({ scope: '范围筛选', embedding: '嵌入', vector_search: '向量检索', expansion: '关联展开', total: '总计' } as Record<string, string>)[key] ?? key} ${value}ms`
                )
                .join('、')}
            </details>
            {result.status !== 'ready' && (
              <Alert>
                <AlertDescription>视觉索引不可用，当前结果仅可能包含精确同图。</AlertDescription>
              </Alert>
            )}
            <div className="grid max-h-80 gap-3 overflow-auto pr-1 sm:grid-cols-2 md:grid-cols-1">
              {result.hits.map((hit) => (
                <div key={hit.asset_id} className="space-y-2 rounded-lg border p-3">
                  <button
                    type="button"
                    onClick={() => {
                      resultSelection.current = hit.asset_id
                      onSelect(hit.asset_id)
                    }}
                    className="w-full"
                  >
                    <img
                      src={getMemoryImageContentUrl(hit.asset_id)}
                      alt="检索匹配图片"
                      className="bg-muted h-28 w-full rounded-md object-contain"
                    />
                  </button>
                  <p className="text-sm font-medium">
                    {hit.match_kind === 'exact_hash' ? '同一图片' : '相似图片'} ·{' '}
                    {hit.similarity.toFixed(4)}
                  </p>
                  <p className="text-muted-foreground text-xs">
                    {[...new Set(hit.occurrences.map((item) => item.chat_name))].join('、')}
                  </p>
                  {hit.observations.map((item) => (
                    <p key={item.observation_id} className="text-sm">
                      {item.text}
                    </p>
                  ))}
                  {hit.related_memories.map((item, index) => (
                    <p key={index} className="text-sm">
                      关联：{item.content}
                    </p>
                  ))}
                </div>
              ))}
            </div>
          </div>
        )}
        {status?.status !== 'ready' && assetId && !result && (
          <p className="text-muted-foreground text-xs">
            视觉索引当前不可用，但仍可检索完全相同的图片。
          </p>
        )}
      </CardContent>
    </Card>
  )
}

interface ImageMaintenancePanelProps {
  status: MemoryImageStatusPayload | null
  onRefresh: () => Promise<void>
}

export function ImageMaintenancePanel({ status, onRefresh }: ImageMaintenancePanelProps) {
  const { toast } = useToast()
  const [busy, setBusy] = useState(false)
  const [report, setReport] = useState<MemoryImageBackfillPayload | null>(null)
  const [previewed, setPreviewed] = useState(false)
  const [error, setError] = useState('')
  const [jobs, setJobs] = useState<MemoryImageJobPayload[]>([])
  const [jobStatus, setJobStatus] = useState('failed')
  const [jobOffset, setJobOffset] = useState(0)
  const [jobTotal, setJobTotal] = useState(0)
  const [jobLoading, setJobLoading] = useState(false)
  const [reindexing, setReindexing] = useState(false)
  const stop = useRef(false)
  const [writebackJobs, setWritebackJobs] = useState<ImageWritebackJob[]>([])
  const [writebackOffset, setWritebackOffset] = useState(0)
  const [writebackTotal, setWritebackTotal] = useState(0)
  const [retrying, setRetrying] = useState(false)
  const [writebackRevision, setWritebackRevision] = useState(0)
  const [writebackError, setWritebackError] = useState('')
  useEffect(() => {
    let active = true
    getImageWritebackJobs(writebackOffset)
      .then((payload) => {
        if (!payload.success) throw new Error('图片入库任务读取失败')
        if (active) {
          setWritebackJobs(payload.items)
          setWritebackTotal(payload.total)
          setWritebackError('')
        }
      })
      .catch((reason) => {
        if (active) setWritebackError(String(reason))
      })
    return () => {
      active = false
    }
  }, [status, writebackOffset, writebackRevision])

  const retryWriteback = async () => {
    setRetrying(true)
    try {
      const result = await retryImageWritebackJobs()
      if (!result.success) throw new Error('重新排队失败')
      toast({
        title: `已重新排队 ${result.count} 条入库任务`,
        description: '完整机器人运行时会继续处理这些任务。',
      })
      setWritebackOffset(0)
      setWritebackRevision((revision) => revision + 1)
      await onRefresh()
    } catch (reason) {
      setError(String(reason))
    } finally {
      setRetrying(false)
    }
  }

  useEffect(
    () => () => {
      stop.current = true
    },
    []
  )

  useEffect(() => {
    let active = true
    setJobLoading(true)
    getMemoryImageJobs(jobStatus === 'all' ? '' : jobStatus, jobOffset)
      .then((payload) => {
        if (!active) return
        if (!payload.success) throw new Error('任务读取失败')
        setJobs(payload.items)
        setJobTotal(payload.total)
      })
      .catch((reason) => {
        if (active) setError(reason instanceof Error ? reason.message : String(reason))
      })
      .finally(() => {
        if (active) setJobLoading(false)
      })
    return () => {
      active = false
    }
  }, [jobStatus, jobOffset, status])

  const reindex = async () => {
    setReindexing(true)
    setError('')
    try {
      const result = await reindexMemoryImages()
      if (!result.success) throw new Error(result.error ?? result.message ?? '图片索引处理失败')
      toast({
        title: '图片索引任务已处理',
        description: `领取 ${result.claimed ?? 0} 项，完成 ${result.processed ?? 0} 项`,
      })
      await onRefresh()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setReindexing(false)
    }
  }

  const backfill = async (preview: boolean) => {
    setBusy(true)
    setError('')
    stop.current = false
    setPreviewed(false)
    let cursor: number | undefined
    let upper = preview ? undefined : (report?.upper_id ?? undefined)
    const combined: MemoryImageBackfillPayload = {
      success: true,
      scanned_messages: 0,
      processed_messages: 0,
      failed_messages: 0,
      counts: {},
      items: [],
    }
    try {
      do {
        const page = await backfillMemoryImages(100, cursor, preview, upper)
        upper = page.upper_id ?? undefined
        combined.upper_id = upper
        combined.scanned_messages += page.scanned_messages
        combined.processed_messages += page.processed_messages
        combined.failed_messages += page.failed_messages
        for (const [key, count] of Object.entries(page.counts))
          combined.counts[key] = (combined.counts[key] ?? 0) + count
        // 界面只保留最近200项详情；分类总数包含扫描的全部图片。
        combined.items = [...combined.items, ...page.items].slice(-200)
        cursor = page.next_before_id ?? undefined
        setReport({ ...combined, counts: { ...combined.counts } })
      } while (cursor && !stop.current)
      setPreviewed(preview && !stop.current && !!upper)
      await onRefresh()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card>
      <CardHeader className="gap-3 border-b pb-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <CardTitle className="flex items-center gap-2 text-sm">
            <Wrench className="h-4 w-4" />
            图片维护
          </CardTitle>
          <CardDescription className="mt-1">
            从历史消息补充图片，查看索引进度和任务失败原因。
          </CardDescription>
        </div>
        <Button variant="outline" size="sm" onClick={() => void reindex()} disabled={reindexing}>
          {reindexing ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <ScanSearch className="h-4 w-4" />
          )}
          处理索引队列
        </Button>
      </CardHeader>
      <CardContent className="space-y-3 pt-4">
        {error && (
          <Alert variant="destructive">
            <AlertTriangle className="h-4 w-4" />
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}

        <details className="border-border/70 rounded-lg border p-3">
          <summary className="cursor-pointer text-sm font-medium">
            入库失败任务 · {writebackTotal}
          </summary>
          <div className="mt-3 space-y-3">
            {writebackError && (
              <p role="alert" className="text-destructive text-sm">
                {writebackError}
              </p>
            )}
            <p className="text-muted-foreground text-xs">
              首次入库失败会自动重试；达到重试上限后保留错误。修复原因后可重新排队。
            </p>
            <Button
              variant="outline"
              disabled={retrying || writebackTotal === 0}
              onClick={() => void retryWriteback()}
            >
              重试全部失败入库任务
            </Button>
            {writebackJobs.map((job) => (
              <div
                key={`${job.session_id}:${job.message_id}`}
                className="rounded-lg border p-3 text-sm break-words"
              >
                <p>
                  {job.chat_name} · 消息 {job.message_id} · 已尝试 {job.attempts} 次
                </p>
                <p className="text-destructive">{job.last_error}</p>
              </div>
            ))}
            <div className="flex gap-2">
              <Button
                variant="outline"
                disabled={writebackOffset === 0}
                onClick={() => setWritebackOffset(Math.max(0, writebackOffset - 25))}
              >
                上一批入库任务
              </Button>
              <Button
                variant="outline"
                disabled={writebackOffset + 25 >= writebackTotal}
                onClick={() => setWritebackOffset(writebackOffset + 25)}
              >
                下一批入库任务
              </Button>
            </div>
          </div>
        </details>
        <details className="border-border/70 rounded-lg border p-3">
          <summary className="cursor-pointer text-sm font-medium">历史图片回填</summary>
          <div className="mt-3 space-y-3 border-t pt-3">
            <div className="flex flex-wrap gap-2">
              <Button variant="outline" disabled={busy} onClick={() => void backfill(true)}>
                预览历史回填
              </Button>
              <Button disabled={busy || !previewed} onClick={() => void backfill(false)}>
                执行已预览范围
              </Button>
              {busy && (
                <Button
                  variant="outline"
                  onClick={() => {
                    stop.current = true
                  }}
                >
                  本批结束后停止
                </Button>
              )}
            </div>
            <p className="text-muted-foreground text-xs">
              预览不写入记忆。执行时重新校验并忽略预览后新到的消息；已回填记录保持幂等。
            </p>
            {report && (
              <div className="space-y-2">
                <p className="text-sm">
                  已扫描{report.scanned_messages}条消息，处理{report.processed_messages}条，异常
                  {report.failed_messages}条。{busy ? '正在处理…' : ''}
                </p>
                <p className="text-sm">
                  {Object.entries(report.counts)
                    .map(([key, count]) => `${labels[key] ?? key} ${count}`)
                    .join('、')}
                </p>
                <details>
                  <summary className="cursor-pointer text-sm">最近200项图片检查详情</summary>
                  <div className="mt-2 max-h-64 space-y-1 overflow-auto">
                    {report.items.map((item, index) => (
                      <p key={index} className="text-xs">
                        {item.chat_name ?? '来源无法读取'} · 消息{item.message_id ?? item.record_id}{' '}
                        · 图片{item.component_path}：{labels[item.category] ?? item.category}{' '}
                        {item.error}
                      </p>
                    ))}
                  </div>
                </details>
              </div>
            )}
          </div>
        </details>

        <details className="border-border/70 rounded-lg border p-3">
          <summary className="cursor-pointer text-sm font-medium">索引状态与任务诊断</summary>
          <div className="mt-3 space-y-3 border-t pt-3">
            <p className="text-sm">
              当前模型建索引进度：{status?.index_progress?.ready ?? 0}/
              {status?.index_progress?.total ?? 0}；图片占用{' '}
              {((status?.stats?.storage_bytes ?? 0) / 1048576).toFixed(2)} MiB
            </p>
            {!!status?.recovery?.issues.length && (
              <Alert variant="destructive">
                <AlertTriangle className="h-4 w-4" />
                <AlertDescription>
                  启动恢复发现{status.recovery.issues.length}项资产异常，请检查文件缺失或损坏。
                </AlertDescription>
              </Alert>
            )}
            <details>
              <summary className="cursor-pointer text-sm">模型指纹与启动恢复记录</summary>
              <pre className="mt-2 max-h-64 overflow-auto text-xs">
                {JSON.stringify(
                  { fingerprint: status?.fingerprint, recovery: status?.recovery },
                  null,
                  2
                )}
              </pre>
            </details>
            <div className="flex flex-wrap items-end justify-between gap-3">
              <div className="w-40 space-y-1.5 text-sm">
                <span className="font-medium">任务状态</span>
                <Select
                  value={jobStatus}
                  onValueChange={(value) => {
                    setJobStatus(value)
                    setJobOffset(0)
                  }}
                >
                  <SelectTrigger aria-label="图片任务状态">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">全部</SelectItem>
                    {['failed', 'pending', 'running', 'done', 'aborted'].map((key) => (
                      <SelectItem key={key} value={key}>
                        {labels[key]}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="flex gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  disabled={jobOffset === 0 || jobLoading}
                  onClick={() => setJobOffset(Math.max(0, jobOffset - 25))}
                >
                  上一批任务
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={jobOffset + 25 >= jobTotal || jobLoading}
                  onClick={() => setJobOffset(jobOffset + 25)}
                >
                  下一批任务
                </Button>
              </div>
            </div>
            {jobLoading ? (
              <p className="text-muted-foreground text-sm">读取任务中…</p>
            ) : jobs.length === 0 ? (
              <p className="text-muted-foreground text-sm">没有对应任务</p>
            ) : (
              <div className="max-h-64 space-y-2 overflow-auto pr-1">
                {jobs.map((job) => (
                  <div
                    key={`${job.kind}:${job.id}`}
                    className="rounded-lg border p-3 text-xs break-all"
                  >
                    <p>
                      {job.kind === 'embedding' ? '图片嵌入' : '描述补偿'} ·{' '}
                      {labels[job.status] ?? job.status} · 尝试{job.attempt_count}次 ·{' '}
                      {new Date(job.updated_at * 1000).toLocaleString('zh-CN')}
                    </p>
                    <p>{job.asset_id || job.id}</p>
                    {job.last_error && <p className="text-destructive">{job.last_error}</p>}
                  </div>
                ))}
              </div>
            )}
          </div>
        </details>
      </CardContent>
    </Card>
  )
}
