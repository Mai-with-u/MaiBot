import {
  Check,
  Image as ImageIcon,
  Info,
  Link2Off,
  Loader2,
  Pencil,
  RefreshCw,
  Trash2,
  X,
} from 'lucide-react'
import type { CSSProperties } from 'react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'

import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import { TabsContent } from '@/components/ui/tabs'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import { useToast } from '@/hooks/use-toast'
import {
  deleteMemoryImageLink,
  deleteMemoryImageOccurrence,
  getMemoryImage,
  getMemoryImageChats,
  getMemoryImageContentUrl,
  getMemoryImageStatus,
  getMemoryImages,
  saveMemoryImageObservation,
  type MemoryImageAssetPayload,
  type MemoryImageChatSummaryPayload,
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

function Stats({
  stats,
  onOpenMaintenance,
}: {
  stats?: MemoryImageStatsPayload
  onOpenMaintenance: () => void
}) {
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
    <div className="flex min-h-8 min-w-0 flex-1 flex-wrap items-center gap-x-5 gap-y-1 text-sm">
      {primaryItems.map(([label, value]) => (
        <span key={label} className="flex items-baseline gap-1.5">
          <span className="text-muted-foreground">{label}</span>
          <span className="text-foreground font-semibold tabular-nums">{value}</span>
        </span>
      ))}
      <TooltipProvider delayDuration={150}>
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="text-muted-foreground h-7 w-7 shrink-0"
              aria-label="后台处理情况"
              aria-haspopup="dialog"
              onClick={onOpenMaintenance}
            >
              <Info className="h-4 w-4" />
            </Button>
          </TooltipTrigger>
          <TooltipContent className="w-56 space-y-2 p-3">
            <div className="font-semibold">后台处理情况</div>
            <div className="space-y-1.5">
              {maintenanceItems.map(([label, value]) => (
                <div key={label} className="flex items-center justify-between gap-4">
                  <span>{label}</span>
                  <span className="font-medium tabular-nums">{value}</span>
                </div>
              ))}
            </div>
            <div className="text-muted-foreground pt-2 text-xs">点击打开高级维护</div>
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>
    </div>
  )
}

interface ImagesTabProps {
  /** 页面提供的顶部操作区；独立使用时在面板顶部显示操作按钮。 */
  toolbarTarget?: HTMLElement | null
}

export function ImagesTab({ toolbarTarget }: ImagesTabProps) {
  const { toast } = useToast()
  const [status, setStatus] = useState<MemoryImageStatusPayload | null>(null)
  const [items, setItems] = useState<MemoryImageAssetPayload[]>([])
  const [chatOptions, setChatOptions] = useState<MemoryImageChatSummaryPayload[]>([])
  const [chatId, setChatId] = useState('')
  const [detail, setDetail] = useState<MemoryImageDetailPayload | null>(null)
  const [detailOpen, setDetailOpen] = useState(false)
  const [selectedId, setSelectedId] = useState('')
  const [offset, setOffset] = useState(0)
  const [loading, setLoading] = useState(true)
  const [detailLoading, setDetailLoading] = useState(false)
  const [editingObservationId, setEditingObservationId] = useState('')
  const [observationText, setObservationText] = useState('')
  const [savingSemantic, setSavingSemantic] = useState(false)
  const [error, setError] = useState('')
  const [maintenanceOpen, setMaintenanceOpen] = useState(false)
  const detailRequestIdRef = useRef(0)

  const loadPage = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const [statusPayload, listPayload, chatList] = await Promise.all([
        getMemoryImageStatus(),
        getMemoryImages(PAGE_SIZE, offset, chatId),
        getMemoryImageChats(),
      ])
      setStatus(statusPayload)
      setChatOptions(chatList)
      const nextItems = listPayload.items ?? []
      setItems(nextItems)
      setSelectedId((current) => {
        if (current && !nextItems.some((item) => item.asset_id === current)) {
          setDetail(null)
          setDetailOpen(false)
          return ''
        }
        return current
      })
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '图片记忆加载失败')
    } finally {
      setLoading(false)
    }
  }, [offset, chatId])

  useEffect(() => {
    void loadPage()
  }, [loadPage])

  const selectAsset = useCallback(async (assetId: string) => {
    const requestId = ++detailRequestIdRef.current
    setSelectedId(assetId)
    setEditingObservationId('')
    setObservationText('')
    setDetail(null)
    setDetailOpen(true)
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

  // 切换聊天筛选时回到第一页再重新加载
  const handleChatFilterChange = (value: string) => {
    setOffset(0)
    setChatId(value === 'all' ? '' : value)
  }

  const removeOccurrence = async (occurrenceId: string) => {
    if (!window.confirm('确认删除这条图片出现记录？没有其他引用时，原图和向量也会被释放。')) return
    const result = await deleteMemoryImageOccurrence(occurrenceId)
    if (!result.success) {
      toast({ title: '删除失败', description: result.error ?? '未知错误', variant: 'destructive' })
      return
    }
    toast({ title: result.asset_released ? '图片记录及独占资产已删除' : '图片出现记录已删除' })
    if (result.asset_released) {
      setDetailOpen(false)
    }
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

  const refreshButton = (
    <Button
      variant="outline"
      size="sm"
      className="shrink-0"
      onClick={() => void loadPage()}
      disabled={loading}
    >
      <RefreshCw className={cn('h-4 w-4', loading && 'animate-spin')} />
      刷新
    </Button>
  )

  return (
    <TabsContent value="images" className="mt-0 flex min-h-0 flex-1 flex-col gap-2 overflow-hidden">
      {toolbarTarget ? createPortal(refreshButton, toolbarTarget) : refreshButton}
      {error && (
        <Alert variant="destructive" className="shrink-0">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      <Card className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
        <CardHeader className="shrink-0 space-y-0 gap-2 border-b pb-3 sm:flex-row sm:items-center sm:justify-between">
          <CardTitle className="sr-only">已记住的图片</CardTitle>
          <Stats stats={status?.stats} onOpenMaintenance={() => setMaintenanceOpen(true)} />
          <Select value={chatId || 'all'} onValueChange={handleChatFilterChange}>
            <SelectTrigger aria-label="按聊天筛选" className="w-full sm:w-52 sm:shrink-0">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">全部聊天</SelectItem>
              {chatOptions.map((chat) => (
                <SelectItem key={chat.chat_id} value={chat.chat_id}>
                  {chat.chat_name} ({chat.asset_count})
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </CardHeader>
        <CardContent className="flex min-h-0 flex-1 flex-col pt-4">
          {(status?.status === 'error' || status?.status === 'unavailable') && (
            <Alert variant="destructive" className="mb-3 shrink-0">
              <AlertDescription>
                相似图片查找暂不可用。你仍可浏览已保存的图片、查看来源和修正说明。请检查图片模型配置与连接。
                <details className="mt-2">
                  <summary className="cursor-pointer">查看技术原因</summary>
                  {status.message ?? status.error}
                </details>
              </AlertDescription>
            </Alert>
          )}
          <div key={`${chatId}:${offset}`} className="@container h-[60dvh] min-h-0 flex-none overflow-auto overscroll-contain pr-1 lg:h-auto lg:flex-1">
            {loading ? (
              <div className="text-muted-foreground flex h-full items-center justify-center text-sm">
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                正在加载图片
              </div>
            ) : items.length === 0 ? (
              chatId ? (
                <div className="text-muted-foreground flex h-full items-center justify-center text-sm">
                  该聊天还没有图片记忆
                </div>
              ) : (
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
                    }}
                  >
                    查看历史图片回填
                  </Button>
                </div>
              )
            ) : (
              // 列数取每页 24 张的约数，按浏览区宽度切换，避免完整页的最后一行缺格。
              <div className="grid grid-cols-1 gap-3 @[20rem]:grid-cols-2 @[30rem]:grid-cols-3 @[40rem]:grid-cols-4 @[60rem]:grid-cols-6 @[80rem]:grid-cols-8 @[120rem]:grid-cols-12">
                {items.map((item, index) => (
                  <button
                    key={item.asset_id}
                    type="button"
                    onClick={() => void selectAsset(item.asset_id)}
                    className={cn(
                      'border-border/70 bg-background hover:border-primary/50 min-w-0 overflow-hidden rounded-lg border text-left transition hover:shadow-sm',
                      selectedId === item.asset_id && 'border-primary ring-primary/20 ring-2'
                    )}
                  >
                    <img
                      src={getMemoryImageContentUrl(item.asset_id)}
                      alt={`图片记忆 ${offset + index + 1}`}
                      className="bg-muted h-36 w-full object-contain"
                      loading="lazy"
                    />
                    <div className="text-muted-foreground flex items-center justify-between gap-2 p-2 text-xs">
                      <span className="min-w-0 truncate" title={`${item.width} × ${item.height}`}>
                        {item.width} × {item.height}
                      </span>
                      <span className="shrink-0 whitespace-nowrap">{formatBytes(item.byte_size)}</span>
                    </div>
                  </button>
                ))}
              </div>
            )}
          </div>
          <div className="mt-2 flex shrink-0 items-center justify-between">
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

      <Dialog open={detailOpen} onOpenChange={setDetailOpen}>
        <DialogContent style={{ '--dialog-width': '48rem' } as CSSProperties}>
          <DialogBody>
            <div className="space-y-4 break-words">
              {detailLoading ? (
                <div className="text-muted-foreground flex min-h-40 items-center justify-center text-sm">
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  加载详情
                </div>
              ) : !detail?.asset ? (
                <div className="text-muted-foreground min-h-40 py-12 text-center text-sm">
                  图片详情加载失败
                </div>
              ) : (
                (() => {
                  // 竖图时图片与信息左右排布，横图保持上下排布
                  const isPortrait = detail.asset.height > detail.asset.width
                  return (
                    <>
                      <div
                        className={
                          isPortrait
                            ? 'flex flex-col gap-4 sm:flex-row sm:items-start'
                            : 'space-y-4'
                        }
                      >
                        <img
                          src={getMemoryImageContentUrl(detail.asset.asset_id)}
                          alt="选中的图片记忆"
                          className={
                            isPortrait
                              ? 'bg-muted max-h-72 w-full rounded-lg object-contain sm:max-w-[45%] sm:self-start sm:w-auto'
                              : 'bg-muted max-h-64 w-full rounded-lg object-contain'
                          }
                        />
                        <div className={isPortrait ? 'min-w-0 flex-1 space-y-4' : 'space-y-4'}>
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
                        </div>
                      </div>
                    </>
                  )
                })()
              )}

              {/* 相似图片检索：放进详情弹窗，命中图片直接更新上方详情，结果保留可继续对比 */}
              <ImageSearchPanel
                assetId={selectedId}
                status={status}
                onSelect={(id) => void selectAsset(id)}
              />
            </div>
          </DialogBody>
        </DialogContent>
      </Dialog>

      <Dialog open={maintenanceOpen} onOpenChange={setMaintenanceOpen}>
        <DialogContent className="max-h-[85vh] [--dialog-width:64rem]">
          <DialogHeader>
            <DialogTitle>图片记忆高级维护</DialogTitle>
            <DialogDescription className="sr-only">历史图片回填、失败任务与索引诊断。</DialogDescription>
          </DialogHeader>
          <DialogBody>
            <ImageMaintenancePanel status={status} onRefresh={loadPage} />
          </DialogBody>
        </DialogContent>
      </Dialog>
    </TabsContent>
  )
}
