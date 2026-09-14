import {
  Check,
  Database,
  Image as ImageIcon,
  Link2Off,
  Loader2,
  Pencil,
  RefreshCw,
  ScanSearch,
  Trash2,
  X,
} from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'

import { Alert, AlertDescription } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { TabsContent } from '@/components/ui/tabs'
import { useToast } from '@/hooks/use-toast'
import {
  deleteMemoryImageLink,
  deleteMemoryImageOccurrence,
  getMemoryImage,
  getMemoryImageContentUrl,
  getMemoryImageStatus,
  getMemoryImages,
  reindexMemoryImages,
  saveMemoryImageObservation,
  type MemoryImageAssetPayload,
  type MemoryImageDetailPayload,
  type MemoryImageStatsPayload,
  type MemoryImageStatusPayload,
} from '@/lib/memory-api'
import { cn } from '@/lib/utils'

import { ImageOperations } from './ImageOperations'

const PAGE_SIZE = 24

function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`
  return `${(value / 1024 / 1024).toFixed(1)} MB`
}

function formatTime(value?: number | null): string {
  if (!value) return '时间未知'
  return new Date(value * 1000).toLocaleString('zh-CN')
}

function statusLabel(status?: string): string {
  const labels: Record<string, string> = {
    disabled: '未启用',
    error: '不可用',
    probing: '探测中',
    ready: '索引就绪',
    unavailable: '模型不可用',
  }
  return labels[status ?? ''] ?? status ?? '状态未知'
}

function Stats({ stats }: { stats?: MemoryImageStatsPayload }) {
  const items = [
    ['图片资产', stats?.asset_count ?? 0],
    ['出现记录', stats?.occurrence_count ?? 0],
    ['认知记录', stats?.observation_count ?? 0],
    ['记忆关联', stats?.link_count ?? 0],
    ['已建向量', stats?.ready_vector_count ?? 0],
    ['待处理', stats?.pending_job_count ?? 0],
    ['失败任务', stats?.failed_job_count ?? 0],
    ['待补偿摘要', stats?.pending_description_count ?? 0],
    ['补偿失败', stats?.failed_description_count ?? 0],
    ['待绑定描述', stats?.pending_unbound_description_count ?? 0],
  ]
  return (
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-5 xl:grid-cols-10">
      {items.map(([label, value]) => (
        <div key={label} className="border-border/70 bg-muted/25 rounded-lg border px-3 py-2">
          <div className="text-muted-foreground text-xs">{label}</div>
          <div className="mt-1 text-lg font-semibold tabular-nums">{value}</div>
        </div>
      ))}
    </div>
  )
}

export function ImagesTab() {
  const { toast } = useToast()
  const [status, setStatus] = useState<MemoryImageStatusPayload | null>(null)
  const [items, setItems] = useState<MemoryImageAssetPayload[]>([])
  const [detail, setDetail] = useState<MemoryImageDetailPayload | null>(null)
  const [selectedId, setSelectedId] = useState('')
  const [offset, setOffset] = useState(0)
  const [loading, setLoading] = useState(true)
  const [detailLoading, setDetailLoading] = useState(false)
  const [reindexing, setReindexing] = useState(false)
  const [editingObservationId, setEditingObservationId] = useState('')
  const [observationText, setObservationText] = useState('')
  const [savingSemantic, setSavingSemantic] = useState(false)
  const [error, setError] = useState('')
  const detailRequestIdRef = useRef(0)

  const loadPage = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const [statusPayload, listPayload] = await Promise.all([
        getMemoryImageStatus(),
        getMemoryImages(PAGE_SIZE, offset),
      ])
      setStatus(statusPayload)
      const nextItems = listPayload.items ?? []
      setItems(nextItems)
      setSelectedId((current) => {
        if (current && !nextItems.some((item) => item.asset_id === current)) {
          setDetail(null)
          return ''
        }
        return current
      })
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '图片记忆加载失败')
    } finally {
      setLoading(false)
    }
  }, [offset])

  useEffect(() => {
    void loadPage()
  }, [loadPage])

  const selectAsset = useCallback(async (assetId: string) => {
    const requestId = ++detailRequestIdRef.current
    setSelectedId(assetId)
    setDetailLoading(true)
    setError('')
    try {
      const payload = await getMemoryImage(assetId)
      if (requestId !== detailRequestIdRef.current) return
      setDetail(payload)
      if (!payload.success) {
        setError(payload.error ?? '图片详情加载失败')
      }
    } catch (reason) {
      if (requestId !== detailRequestIdRef.current) return
      setError(reason instanceof Error ? reason.message : '图片详情加载失败')
    } finally {
      if (requestId === detailRequestIdRef.current) {
        setDetailLoading(false)
      }
    }
  }, [])

  useEffect(
    () => () => {
      detailRequestIdRef.current += 1
    },
    []
  )

  const reindex = async () => {
    setReindexing(true)
    try {
      const result = await reindexMemoryImages()
      if (!result.success) throw new Error(result.error ?? result.message ?? '图片索引处理失败')
      toast({
        title: '图片索引任务已处理',
        description: `领取 ${result.claimed ?? 0} 项，完成 ${result.processed ?? 0} 项`,
      })
      await loadPage()
    } catch (reason) {
      toast({
        title: '图片索引处理失败',
        description: reason instanceof Error ? reason.message : '请查看后端日志',
        variant: 'destructive',
      })
    } finally {
      setReindexing(false)
    }
  }

  const removeOccurrence = async (occurrenceId: string) => {
    if (!window.confirm('确认删除这条图片出现记录？没有其他引用时，原图和向量也会被释放。')) return
    const result = await deleteMemoryImageOccurrence(occurrenceId)
    if (!result.success) {
      toast({ title: '删除失败', description: result.error ?? '未知错误', variant: 'destructive' })
      return
    }
    toast({ title: result.asset_released ? '图片记录及独占资产已删除' : '图片出现记录已删除' })
    if (selectedId) await selectAsset(selectedId)
    await loadPage()
  }

  const saveObservation = async (occurrenceId: string, observationId: string, text: string) => {
    const normalized = text.trim()
    if (!normalized) return
    setSavingSemantic(true)
    try {
      const result = await saveMemoryImageObservation({
        occurrence_id: occurrenceId,
        text: normalized,
        confirm_status: 'confirmed',
        supersedes_id: observationId,
      })
      if (!result.success) throw new Error(result.error ?? '图片认知保存失败')
      setEditingObservationId('')
      setObservationText('')
      if (selectedId) await selectAsset(selectedId)
    } catch (reason) {
      toast({
        title: '图片认知保存失败',
        description: reason instanceof Error ? reason.message : '请查看后端日志',
        variant: 'destructive',
      })
    } finally {
      setSavingSemantic(false)
    }
  }

  const removeLink = async (linkId: string) => {
    if (!window.confirm('确认删除这条图片与记忆的关联？')) return
    const result = await deleteMemoryImageLink(linkId)
    if (!result.success) {
      toast({
        title: '关联删除失败',
        description: result.error ?? '未知错误',
        variant: 'destructive',
      })
      return
    }
    if (selectedId) await selectAsset(selectedId)
  }

  return (
    <TabsContent value="images" className="space-y-4">
      <Card>
        <CardHeader className="gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <CardTitle className="flex items-center gap-2">
              <ImageIcon className="h-4 w-4" />
              图片记忆
              <Badge variant={status?.status === 'ready' ? 'default' : 'secondary'}>
                {statusLabel(status?.status)}
              </Badge>
            </CardTitle>
            <CardDescription className="mt-1">
              查看内容寻址图片、视觉向量、图片认知以及它们关联的文本记忆。
              {status?.dimension ? ` 当前向量维度为 ${status.dimension}。` : ''}
            </CardDescription>
          </div>
          <div className="flex gap-2">
            <Button variant="outline" size="sm" onClick={() => void loadPage()} disabled={loading}>
              <RefreshCw className={cn('mr-2 h-4 w-4', loading && 'animate-spin')} />
              刷新
            </Button>
            <Button size="sm" onClick={() => void reindex()} disabled={reindexing}>
              {reindexing ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              ) : (
                <ScanSearch className="mr-2 h-4 w-4" />
              )}
              处理索引队列
            </Button>
          </div>
        </CardHeader>
        <CardContent>
          <Stats stats={status?.stats} />
          {(status?.message || status?.error) && (
            <div className="text-muted-foreground mt-3 text-sm">
              {status.message ?? status.error}
            </div>
          )}
        </CardContent>
      </Card>

      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      <ImageOperations
        assetId={selectedId}
        status={status}
        onSelect={(id) => void selectAsset(id)}
        onRefresh={loadPage}
      />
      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_420px]">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">资产浏览</CardTitle>
            <CardDescription>
              同一原始文件只保存一次，每次出现在聊天或记忆包中都会形成独立记录。
            </CardDescription>
          </CardHeader>
          <CardContent>
            {loading ? (
              <div className="text-muted-foreground flex min-h-48 items-center justify-center text-sm">
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                加载图片资产
              </div>
            ) : items.length === 0 ? (
              <div className="text-muted-foreground flex min-h-48 flex-col items-center justify-center gap-2 text-sm">
                <ImageIcon className="h-8 w-8 opacity-50" />
                暂无图片记忆
              </div>
            ) : (
              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                {items.map((item) => (
                  <button
                    key={item.asset_id}
                    type="button"
                    onClick={() => void selectAsset(item.asset_id)}
                    className={cn(
                      'border-border/70 bg-background hover:border-primary/50 overflow-hidden rounded-xl border text-left transition hover:shadow-sm',
                      selectedId === item.asset_id && 'border-primary ring-primary/20 ring-2'
                    )}
                  >
                    <img
                      src={getMemoryImageContentUrl(item.asset_id)}
                      alt={`图片记忆 ${item.content_hash.slice(0, 12)}`}
                      className="bg-muted h-36 w-full object-contain"
                      loading="lazy"
                    />
                    <div className="space-y-1.5 p-3">
                      <div className="font-mono text-xs">{item.content_hash.slice(0, 16)}…</div>
                      <div className="text-muted-foreground flex justify-between text-xs">
                        <span>
                          {item.width} × {item.height}
                        </span>
                        <span>{formatBytes(item.byte_size)}</span>
                      </div>
                      <div className="text-muted-foreground text-xs">
                        {item.occurrence_count} 条出现记录
                      </div>
                    </div>
                  </button>
                ))}
              </div>
            )}
            <div className="mt-4 flex items-center justify-between">
              <Button
                variant="outline"
                size="sm"
                disabled={offset === 0 || loading}
                onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
              >
                上一页
              </Button>
              <span className="text-muted-foreground text-xs">
                第 {Math.floor(offset / PAGE_SIZE) + 1} 页
              </span>
              <Button
                variant="outline"
                size="sm"
                disabled={items.length < PAGE_SIZE || loading}
                onClick={() => setOffset(offset + PAGE_SIZE)}
              >
                下一页
              </Button>
            </div>
          </CardContent>
        </Card>

        <Card className="h-fit">
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <Database className="h-4 w-4" />
              图片语义详情
            </CardTitle>
            <CardDescription>
              认知文本保留来源与确认状态，关联项直接指向实际记忆内容。
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {detailLoading ? (
              <div className="text-muted-foreground flex min-h-40 items-center justify-center text-sm">
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                加载详情
              </div>
            ) : !detail?.asset ? (
              <div className="text-muted-foreground min-h-40 py-12 text-center text-sm">
                选择一张图片查看详情
              </div>
            ) : (
              <>
                <img
                  src={getMemoryImageContentUrl(detail.asset.asset_id)}
                  alt="选中的图片记忆"
                  className="bg-muted max-h-64 w-full rounded-lg object-contain"
                />
                <div className="space-y-1 text-xs">
                  <div className="font-mono break-all">{detail.asset.content_hash}</div>
                  <div className="text-muted-foreground">
                    {detail.asset.mime_type} · {formatBytes(detail.asset.byte_size)} ·{' '}
                    {detail.asset.width} × {detail.asset.height}
                  </div>
                </div>
                <section className="space-y-2">
                  <h3 className="text-sm font-semibold">出现记录</h3>
                  {(detail.occurrences ?? []).map((occurrence) => (
                    <div
                      key={occurrence.occurrence_id}
                      className="border-border/70 rounded-lg border p-3 text-xs"
                    >
                      <div className="flex items-start justify-between gap-2">
                        <div>
                          <div className="font-medium">
                            {occurrence.chat_name ||
                              (occurrence.scope_type === 'global'
                                ? '全局记忆'
                                : occurrence.chat_id)}
                          </div>
                          <div className="text-muted-foreground mt-1">
                            {formatTime(occurrence.occurred_at)} · {occurrence.source_kind}
                          </div>
                          {occurrence.message_id && (
                            <div className="text-muted-foreground mt-1 font-mono break-all">
                              消息 {occurrence.message_id} · {occurrence.component_path}
                            </div>
                          )}
                        </div>
                        <Button
                          variant="ghost"
                          size="icon"
                          aria-label="删除图片出现记录"
                          onClick={() => void removeOccurrence(occurrence.occurrence_id)}
                        >
                          <Trash2 className="h-4 w-4" />
                        </Button>
                      </div>
                    </div>
                  ))}
                  {(detail.occurrences ?? []).length === 0 && (
                    <div className="text-muted-foreground text-xs">没有可见的出现记录</div>
                  )}
                </section>
                <section className="space-y-2">
                  <h3 className="text-sm font-semibold">图片认知</h3>
                  {(detail.observations ?? []).map((observation) => (
                    <div
                      key={observation.observation_id}
                      className="bg-muted/35 rounded-lg p-3 text-sm"
                    >
                      {editingObservationId === observation.observation_id ? (
                        <div className="flex gap-2">
                          <Input
                            value={observationText}
                            onChange={(event) => setObservationText(event.target.value)}
                            disabled={savingSemantic}
                          />
                          <Button
                            size="icon"
                            aria-label="保存图片认知修正"
                            disabled={savingSemantic || !observationText.trim()}
                            onClick={() =>
                              void saveObservation(
                                observation.occurrence_id,
                                observation.observation_id,
                                observationText
                              )
                            }
                          >
                            <Check className="h-4 w-4" />
                          </Button>
                          <Button
                            variant="ghost"
                            size="icon"
                            aria-label="取消修正"
                            onClick={() => setEditingObservationId('')}
                          >
                            <X className="h-4 w-4" />
                          </Button>
                        </div>
                      ) : (
                        <>
                          <div>{observation.text}</div>
                          <div className="mt-2 flex items-center justify-between gap-2">
                            <div className="text-muted-foreground text-xs">
                              {observation.source_kind} · {observation.confirm_status}
                            </div>
                            <div className="flex gap-1">
                              {observation.confirm_status !== 'confirmed' && (
                                <Button
                                  variant="ghost"
                                  size="sm"
                                  disabled={savingSemantic}
                                  onClick={() =>
                                    void saveObservation(
                                      observation.occurrence_id,
                                      observation.observation_id,
                                      observation.text
                                    )
                                  }
                                >
                                  <Check className="mr-1 h-3.5 w-3.5" />
                                  确认
                                </Button>
                              )}
                              <Button
                                variant="ghost"
                                size="sm"
                                onClick={() => {
                                  setEditingObservationId(observation.observation_id)
                                  setObservationText(observation.text)
                                }}
                              >
                                <Pencil className="mr-1 h-3.5 w-3.5" />
                                修正
                              </Button>
                            </div>
                          </div>
                        </>
                      )}
                    </div>
                  ))}
                  {(detail.observations ?? []).length === 0 && (
                    <div className="text-muted-foreground text-xs">暂无图片认知文本</div>
                  )}
                </section>
                <section className="space-y-2">
                  <h3 className="text-sm font-semibold">关联记忆</h3>
                  {(detail.links ?? []).map((link) => (
                    <div
                      key={link.link_id}
                      className="border-border/70 rounded-lg border p-3 text-sm"
                    >
                      <div className="flex items-start justify-between gap-2">
                        <div>
                          <div>
                            {link.memory?.content ?? `${link.target_type}: ${link.target_id}`}
                          </div>
                          <div className="text-muted-foreground mt-1 text-xs">
                            {link.link_kind} · {link.target_type}
                          </div>
                          {link.evidence_json && (
                            <details className="mt-2 text-xs">
                              <summary>关联证据</summary>
                              <pre className="break-all whitespace-pre-wrap">
                                {link.evidence_json}
                              </pre>
                            </details>
                          )}
                        </div>
                        <Button
                          variant="ghost"
                          size="icon"
                          aria-label="删除图片记忆关联"
                          onClick={() => void removeLink(link.link_id)}
                        >
                          <Link2Off className="h-4 w-4" />
                        </Button>
                      </div>
                    </div>
                  ))}
                  {(detail.links ?? []).length === 0 && (
                    <div className="text-muted-foreground text-xs">暂无关联记忆</div>
                  )}
                </section>
              </>
            )}
          </CardContent>
        </Card>
      </div>
    </TabsContent>
  )
}
