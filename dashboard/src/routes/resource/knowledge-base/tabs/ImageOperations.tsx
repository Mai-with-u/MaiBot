import { useEffect, useRef, useState } from 'react'

import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import {
  backfillMemoryImages,
  getMemoryImageContentUrl,
  getMemoryImageJobs,
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

export function ImageOperations({
  assetId,
  status,
  onSelect,
  onRefresh,
}: {
  assetId: string
  status: MemoryImageStatusPayload | null
  onSelect: (id: string) => void
  onRefresh: () => Promise<void>
}) {
  const [busy, setBusy] = useState(false)
  const [report, setReport] = useState<MemoryImageBackfillPayload | null>(null)
  const [previewed, setPreviewed] = useState(false)
  const [error, setError] = useState('')
  const [jobs, setJobs] = useState<MemoryImageJobPayload[]>([])
  const [jobStatus, setJobStatus] = useState('failed')
  const [jobOffset, setJobOffset] = useState(0)
  const [jobTotal, setJobTotal] = useState(0)
  const [jobLoading, setJobLoading] = useState(false)
  const [threshold, setThreshold] = useState('0.72')
  const [searching, setSearching] = useState(false)
  const [result, setResult] = useState<MemoryImageSearchPayload | null>(null)
  const searchGeneration = useRef(0)
  const stop = useRef(false)

  useEffect(() => {
    searchGeneration.current += 1
    setResult(null)
  }, [assetId])
  useEffect(
    () => () => {
      stop.current = true
    },
    []
  )
  useEffect(() => {
    let active = true
    setJobLoading(true)
    getMemoryImageJobs(jobStatus, jobOffset)
      .then((payload) => {
        if (!active) return
        if (!payload.success) throw new Error('任务读取失败')
        setJobs(payload.items)
        setJobTotal(payload.total)
      })
      .catch((reason) => {
        if (active) setError(String(reason))
      })
      .finally(() => {
        if (active) setJobLoading(false)
      })
    return () => {
      active = false
    }
  }, [jobStatus, jobOffset, status])

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
      setError(String(reason))
    } finally {
      setBusy(false)
    }
  }

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
      setError(String(reason))
    } finally {
      setSearching(false)
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">图片检索与维护</CardTitle>
      </CardHeader>
      <CardContent className="space-y-5">
        {error && (
          <Alert variant="destructive">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}
        <section className="space-y-2">
          <div className="flex flex-wrap items-center gap-2">
            <Button disabled={!assetId || searching} onClick={() => void search()}>
              {searching ? '检索中' : '查找当前图片的同图与相似图'}
            </Button>
            <label htmlFor="image-search-threshold" className="flex items-center gap-2 text-sm">
              相似度阈值
              <Input
                id="image-search-threshold"
                className="w-24"
                type="number"
                min={-1}
                max={1}
                step={0.01}
                value={threshold}
                onChange={(event) => setThreshold(event.target.value)}
              />
            </label>
          </div>
          <p className="text-muted-foreground text-xs">
            在下方选择图片。管理员检索范围包含全部图片记忆；相似度不代表身份确认。
          </p>
          {result && (
            <>
              <p className="text-xs">
                {result.hits.length}个匹配，{result.related_memory_count}条关联；
                {Object.entries(result.timings_ms)
                  .map(
                    ([key, value]) =>
                      `${({ scope: '范围筛选', embedding: '嵌入', vector_search: '向量检索', expansion: '关联展开', total: '总计' } as Record<string, string>)[key] ?? key} ${value}ms`
                  )
                  .join('、')}
              </p>
              {result.status !== 'ready' && (
                <p className="text-sm text-amber-600">
                  视觉索引不可用，当前结果仅可能包含精确同图。
                </p>
              )}
              <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                {result.hits.map((hit) => (
                  <div key={hit.asset_id} className="space-y-2 rounded border p-3">
                    <button onClick={() => onSelect(hit.asset_id)} className="w-full">
                      <img
                        src={getMemoryImageContentUrl(hit.asset_id)}
                        alt="检索匹配图片"
                        className="h-28 w-full object-contain"
                      />
                    </button>
                    <p className="text-sm">
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
            </>
          )}
        </section>
        <section className="space-y-2 border-t pt-4">
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
            预览不写入记忆。执行时重新校验，忽略预览后新到的消息；停止后可重新预览，已回填记录保持幂等。
          </p>
          {report && (
            <>
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
                <div className="max-h-64 space-y-1 overflow-auto">
                  {report.items.map((item, index) => (
                    <p key={index} className="text-xs">
                      {item.chat_name ?? '来源无法读取'} · 消息{item.message_id ?? item.record_id} ·
                      图片{item.component_path}：{labels[item.category] ?? item.category}{' '}
                      {item.error}
                    </p>
                  ))}
                </div>
              </details>
            </>
          )}
        </section>
        <section className="space-y-2 border-t pt-4">
          <p className="text-sm">
            当前模型建索引进度：{status?.index_progress?.ready ?? 0}/
            {status?.index_progress?.total ?? 0}；图片占用{' '}
            {((status?.stats?.storage_bytes ?? 0) / 1048576).toFixed(2)} MiB
          </p>
          {!!status?.recovery?.issues.length && (
            <p className="text-destructive text-sm" role="alert">
              启动恢复发现{status.recovery.issues.length}项资产异常，请展开记录检查文件缺失或损坏。
            </p>
          )}
          <details>
            <summary className="cursor-pointer text-sm">模型指纹与启动恢复记录</summary>
            <pre className="overflow-auto text-xs">
              {JSON.stringify(
                { fingerprint: status?.fingerprint, recovery: status?.recovery },
                null,
                2
              )}
            </pre>
          </details>
          <label className="text-sm">
            任务状态{' '}
            <select
              aria-label="图片任务状态"
              className="bg-background rounded border p-1"
              value={jobStatus}
              onChange={(event) => {
                setJobStatus(event.target.value)
                setJobOffset(0)
              }}
            >
              <option value="">全部</option>
              {['failed', 'pending', 'running', 'done', 'aborted'].map((key) => (
                <option key={key} value={key}>
                  {labels[key]}
                </option>
              ))}
            </select>
          </label>
          {jobLoading ? (
            <p>读取任务中…</p>
          ) : (
            jobs.map((job) => (
              <div key={`${job.kind}:${job.id}`} className="rounded border p-2 text-xs break-all">
                <p>
                  {job.kind === 'embedding' ? '图片嵌入' : '描述补偿'} ·{' '}
                  {labels[job.status] ?? job.status} · 尝试{job.attempt_count}次 ·{' '}
                  {new Date(job.updated_at * 1000).toLocaleString('zh-CN')}
                </p>
                <p>{job.asset_id || job.id}</p>
                {job.last_error && <p className="text-destructive">{job.last_error}</p>}
              </div>
            ))
          )}
          {!jobLoading && jobs.length === 0 && (
            <p className="text-muted-foreground text-sm">没有对应任务</p>
          )}
          <div className="flex gap-2">
            <Button
              variant="outline"
              disabled={jobOffset === 0 || jobLoading}
              onClick={() => setJobOffset(jobOffset - 25)}
            >
              上一批任务
            </Button>
            <Button
              variant="outline"
              disabled={jobOffset + 25 >= jobTotal || jobLoading}
              onClick={() => setJobOffset(jobOffset + 25)}
            >
              下一批任务
            </Button>
          </div>
        </section>
      </CardContent>
    </Card>
  )
}
