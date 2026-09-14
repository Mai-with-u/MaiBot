import {
  Check,
  Database,
  Image as ImageIcon,
  Link2Off,
  Loader2,
  Pencil,
  RefreshCw,
  Trash2,
  X,
} from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'

import { Alert, AlertDescription } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Textarea } from '@/components/ui/textarea'
import { TabsContent } from '@/components/ui/tabs'
import { useToast } from '@/hooks/use-toast'
import {
  deleteMemoryImageLink,
  deleteMemoryImageOccurrence,
  getMemoryImage,
  getMemoryImageContentUrl,
  getMemoryImageStatus,
  getMemoryImages,
  saveMemoryImageObservation,
  type MemoryImageAssetPayload,
  type MemoryImageDetailPayload,
  type MemoryImageStatsPayload,
  type MemoryImageStatusPayload,
} from '@/lib/memory-api'
import { cn } from '@/lib/utils'

import { ImageMaintenancePanel, ImageSearchPanel } from './ImageOperations'

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
    ready: '可查找相似图片',
    unavailable: '相似查找不可用',
  }
  return labels[status ?? ''] ?? status ?? '状态未知'
}

function Stats({ stats }: { stats?: MemoryImageStatsPayload }) {
  const primaryItems = [
    ['已记住的图片', stats?.asset_count ?? 0],
    ['图片说明', stats?.observation_count ?? 0],
    ['关联记忆', stats?.link_count ?? 0],
  ]
  const maintenanceItems = [
    ['待处理', stats?.pending_job_count ?? 0],
    ['失败任务', stats?.failed_job_count ?? 0],
    ['待补偿摘要', stats?.pending_description_count ?? 0],
    ['补偿失败', stats?.failed_description_count ?? 0],
    ['待绑定描述', stats?.pending_unbound_description_count ?? 0],
  ]
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-3 gap-2">
        {primaryItems.map(([label, value]) => (
          <div key={label} className="border-border/70 bg-muted/25 rounded-lg border px-3 py-2">
            <div className="text-muted-foreground text-xs">{label}</div>
            <div className="mt-1 text-lg font-semibold tabular-nums">{value}</div>
          </div>
        ))}
      </div>
      <details className="text-muted-foreground text-xs">
        <summary className="cursor-pointer">后台处理情况</summary>
        <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1">
          {maintenanceItems.map(([label, value]) => (
            <span key={label}>
              {label} <span className="text-foreground font-medium tabular-nums">{value}</span>
            </span>
          ))}
        </div>
      </details>
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
  const [editingObservationId, setEditingObservationId] = useState('')
  const [observationText, setObservationText] = useState('')
  const [savingSemantic, setSavingSemantic] = useState(false)
  const [error, setError] = useState('')
  const [maintenanceOpen, setMaintenanceOpen] = useState(false)
  const maintenanceRef = useRef<HTMLDetailsElement>(null)
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
    setEditingObservationId('')
    setObservationText('')
    setDetail(null)
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
      toast({
        title: '图片说明已保存',
        description: '关联知识不会自动同步修改，请检查下方关联记忆。',
      })
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
        <CardHeader className="gap-3 border-b pb-3 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <CardTitle className="flex items-center gap-2 text-sm">
              <ImageIcon className="h-4 w-4" />
              图片记忆
              <Badge variant={status?.status === 'ready' ? 'default' : 'secondary'}>
                {statusLabel(status?.status)}
              </Badge>
            </CardTitle>
            <CardDescription className="mt-1">
              看看麦麦记住了哪些图片、来自哪里，发现说明有误时可以修正。
            </CardDescription>
          </div>
          <Button variant="outline" size="sm" onClick={() => void loadPage()} disabled={loading}>
            <RefreshCw className={cn('h-4 w-4', loading && 'animate-spin')} />
            刷新
          </Button>
        </CardHeader>
        <CardContent className="pt-4 sm:pt-5">
          <Stats stats={status?.stats} />
          {(status?.message || status?.error) && (
            <Alert
              variant={
                status?.status === 'error' || status?.status === 'unavailable'
                  ? 'destructive'
                  : 'default'
              }
              className="mt-3"
            >
              <AlertDescription>
                {status.status === 'unavailable' || status.status === 'error'
                  ? '相似图片查找暂不可用。你仍可浏览已保存的图片、查看来源和修正说明。请检查图片模型配置与连接。'
                  : (status.message ?? status.error)}
                <details className="mt-2">
                  <summary className="cursor-pointer">查看技术原因</summary>
                  {status.message ?? status.error}
                </details>
              </AlertDescription>
            </Alert>
          )}
        </CardContent>
      </Card>

      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      <div className="grid items-start gap-4 md:grid-cols-[minmax(0,1fr)_360px] xl:grid-cols-[minmax(0,1fr)_420px]">
        <Card className="flex h-[420px] min-w-0 flex-col overflow-hidden md:h-[660px]">
          <CardHeader className="border-b pb-3">
            <CardTitle className="text-sm">已记住的图片</CardTitle>
            <CardDescription>点击图片查看说明和聊天来源，同一张图片会合并展示。</CardDescription>
          </CardHeader>
          <CardContent className="flex min-h-0 flex-1 flex-col pt-4">
            <div className="min-h-0 flex-1 overflow-auto pr-1">
              {loading ? (
                <div className="text-muted-foreground flex h-full items-center justify-center text-sm">
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  正在加载图片
                </div>
              ) : items.length === 0 ? (
                <div className="text-muted-foreground flex h-full flex-col items-center justify-center gap-2 text-sm">
                  <ImageIcon className="h-8 w-8 opacity-50" />
                  暂无图片记忆
                  <p className="max-w-sm px-4 text-center">
                    启用图片记忆并运行机器人后，聊天中的静态图片会自动记录。已有聊天记录也可通过历史图片回填补充。
                  </p>
                  <Button
                    variant="outline"
                    onClick={() => {
                      setMaintenanceOpen(true)
                      maintenanceRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
                    }}
                  >
                    查看历史图片回填
                  </Button>
                </div>
              ) : (
                <div className="grid grid-cols-[repeat(auto-fit,minmax(150px,1fr))] gap-3">
                  {items.map((item, index) => (
                    <button
                      key={item.asset_id}
                      type="button"
                      onClick={() => void selectAsset(item.asset_id)}
                      className={cn(
                        'border-border/70 bg-background hover:border-primary/50 overflow-hidden rounded-lg border text-left transition hover:shadow-sm',
                        selectedId === item.asset_id && 'border-primary ring-primary/20 ring-2'
                      )}
                    >
                      <img
                        src={getMemoryImageContentUrl(item.asset_id)}
                        alt={`图片记忆 ${offset + index + 1}`}
                        className="bg-muted h-36 w-full object-contain"
                        loading="lazy"
                      />
                      <div className="space-y-1.5 p-3">
                        <div className="text-sm font-medium">图片 {offset + index + 1}</div>
                        <div className="text-muted-foreground flex justify-between text-xs">
                          <span>
                            {item.width} × {item.height}
                          </span>
                          <span>{formatBytes(item.byte_size)}</span>
                        </div>
                        <div className="text-muted-foreground text-xs">
                          在 {item.occurrence_count} 处出现 · 点击查看来源
                        </div>
                      </div>
                    </button>
                  ))}
                </div>
              )}
            </div>
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

        <div className="min-w-0 space-y-4">
          <Card className="min-w-0">
            <CardHeader className="border-b pb-3">
              <CardTitle className="flex items-center gap-2 text-sm">
                <Database className="h-4 w-4" />
                这张图片记住了什么
              </CardTitle>
              <CardDescription>查看图片说明、来源和关联内容，说明有误可直接修正。</CardDescription>
            </CardHeader>
            <CardContent className="max-h-[480px] space-y-4 overflow-auto pt-4 break-words">
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
                  <details className="space-y-1 text-xs">
                    <summary className="text-muted-foreground cursor-pointer">文件信息</summary>
                    <div className="font-mono break-all">{detail.asset.content_hash}</div>
                    <div className="text-muted-foreground">
                      {detail.asset.mime_type} · {formatBytes(detail.asset.byte_size)} ·{' '}
                      {detail.asset.width} × {detail.asset.height}
                    </div>
                  </details>
                  <section className="space-y-2">
                    <h3 className="text-sm font-semibold">图片来自哪里</h3>
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
                                  : '来源聊天已不可用')}
                            </div>
                            <div className="text-muted-foreground mt-1">
                              {formatTime(occurrence.occurred_at)}
                            </div>
                            {occurrence.message_id && (
                              <details className="text-muted-foreground mt-1 break-all">
                                <summary className="cursor-pointer">来源记录信息</summary>
                                消息 {occurrence.message_id} · {occurrence.component_path}
                              </details>
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
                    <h3 className="text-sm font-semibold">麦麦记住的说明</h3>
                    {(detail.observations ?? []).map((observation) => (
                      <div
                        key={observation.observation_id}
                        className="bg-muted/35 rounded-lg p-3 text-sm"
                      >
                        {editingObservationId === observation.observation_id ? (
                          <div className="space-y-2">
                            <p className="text-muted-foreground text-xs">
                              原说明：{observation.text}
                            </p>
                            <Textarea
                              aria-label="修正后的图片说明"
                              rows={4}
                              value={observationText}
                              onChange={(event) => setObservationText(event.target.value)}
                              disabled={savingSemantic}
                            />
                            <p className="text-muted-foreground text-xs">
                              本次只修改图片说明，不会自动改写关联知识。保存后请检查下方关联记忆。
                            </p>
                            <Button
                              size="sm"
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
                              保存说明
                            </Button>
                            <Button
                              variant="ghost"
                              size="sm"
                              disabled={savingSemantic}
                              aria-label="取消修正"
                              onClick={() => setEditingObservationId('')}
                            >
                              <X className="h-4 w-4" />
                              取消
                            </Button>
                          </div>
                        ) : (
                          <>
                            <div>{observation.text}</div>
                            <div className="mt-2 flex items-center justify-between gap-2">
                              <div className="text-muted-foreground text-xs">
                                {observation.source_kind === 'model_description'
                                  ? '自动识别'
                                  : '人工或导入说明'}{' '}
                                · {observation.confirm_status === 'confirmed' ? '已确认' : '待核对'}
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
                                    说明正确
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
                                  说明有误
                                </Button>
                              </div>
                            </div>
                          </>
                        )}
                      </div>
                    ))}
                    {(detail.observations ?? []).length === 0 && (
                      <div className="text-muted-foreground text-xs">
                        这张图片还没有说明。识别完成后会显示在这里。
                      </div>
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
                            <div>{link.memory?.content ?? '关联内容暂时无法读取'}</div>
                            <details className="text-muted-foreground mt-1 text-xs">
                              <summary className="cursor-pointer">关联记录信息</summary>
                              {link.link_kind} · {link.target_type}
                              <p>{link.target_id}</p>
                            </details>
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
                            size="sm"
                            aria-label="删除图片记忆关联"
                            onClick={() => void removeLink(link.link_id)}
                          >
                            <Link2Off className="h-4 w-4" />
                            取消关联
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
          <ImageSearchPanel
            assetId={selectedId}
            status={status}
            onSelect={(id) => void selectAsset(id)}
          />
        </div>
      </div>
      <details
        ref={maintenanceRef}
        open={maintenanceOpen}
        onToggle={(event) => setMaintenanceOpen(event.currentTarget.open)}
        className="rounded-lg border p-4"
      >
        <summary className="cursor-pointer text-sm font-medium">
          高级维护 · 历史图片回填与故障处理
        </summary>
        <div className="mt-4">
          <ImageMaintenancePanel status={status} onRefresh={loadPage} />
        </div>
      </details>
    </TabsContent>
  )
}
