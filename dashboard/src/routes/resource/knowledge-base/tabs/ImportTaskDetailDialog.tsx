/**
 * ImportTaskDetailDialog —— 导入任务详情弹窗。
 *
 * 由导入队列里的任务卡片点开，替代原先常驻在页面下方的详情卡片。展示顺序按「先结论后明细」：
 * - 失败原因 → 进度概览 → 任务信息 → 重试摘要 → 文件状态 → 分块状态；
 * - 任务类型走中文标签；状态与步骤文案相同时只显示一次，避免同一词重复两遍；
 * - 重试摘要全为 0 时不展示，避免堆一排没有信息量的 0。
 */
import { ChevronLeft, ChevronRight, Loader2, Trash2, Undo2 } from 'lucide-react'

import { Alert, AlertDescription } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Progress } from '@/components/ui/progress'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { ThinkingIllustration } from '@/components/ui/thinking-illustration'
import { MemoryProgressIndicator } from '@/components/memory/MemoryProgressIndicator'
import { cn } from '@/lib/utils'

import { IMPORT_CHUNK_PAGE_SIZE, RUNNING_IMPORT_STATUS } from '../constants'
import type { UseImportQueueResult } from '../hooks/useImportQueue'
import {
  formatImportTime,
  formatProgressPercent,
  getImportStatusLabel,
  getImportStatusVariant,
  getImportStepLabel,
  getImportTaskKindLabel,
  normalizeProgress,
} from '../utils'

export interface ImportTaskDetailDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  queue: UseImportQueueResult
}

/** 分块计数摘要：仅在失败/取消非零时才追加对应片段 */
function formatChunkSummary(done: unknown, total: unknown, failed: unknown, cancelled: unknown = 0): string {
  const doneCount = Number(done ?? 0)
  const totalCount = Number(total ?? 0)
  const failedCount = Number(failed ?? 0)
  const cancelledCount = Number(cancelled ?? 0)
  const parts = [`成功 ${doneCount} / ${totalCount} 分块`]
  if (failedCount > 0) {
    parts.push(`失败 ${failedCount}`)
  }
  if (cancelledCount > 0) {
    parts.push(`取消 ${cancelledCount}`)
  }
  return parts.join(' · ')
}

function getProgressTone(status: string): 'default' | 'success' | 'warning' | 'destructive' | 'muted' {
  if (status === 'completed') {
    return 'success'
  }
  if (status === 'failed') {
    return 'destructive'
  }
  if (status === 'completed_with_errors') {
    return 'warning'
  }
  if (status === 'cancelled') {
    return 'muted'
  }
  return 'default'
}

/** 键值信息项：标签在上、值在下，值允许换行展示长 ID */
function InfoItem({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="space-y-1">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className={cn('break-all text-sm', mono && 'font-mono text-xs leading-relaxed')}>{value}</div>
    </div>
  )
}

export function ImportTaskDetailDialog({ open, onOpenChange, queue }: ImportTaskDetailDialogProps) {
  const {
    selectedImportTaskResolved,
    selectedImportTaskLoading,
    selectedImportRetrySummary,
    selectedImportTaskErrorText,
    selectedImportFiles,
    selectedImportFileId,
    selectImportFile,
    importChunkTotal,
    importChunkOffset,
    moveImportChunkPage,
    canImportChunkPrev,
    canImportChunkNext,
    importChunksLoading,
    selectedImportChunks,
    cancelSelectedImportTask,
    retrySelectedImportTask,
  } = queue

  const task = selectedImportTaskResolved
  const status = String(task?.status ?? '')
  const statusLabel = task ? getImportStatusLabel(status) : ''
  const stepLabel = task ? getImportStepLabel(String(task.current_step ?? '')) : ''
  // 状态与步骤文案相同时（如 running 既是状态又是当前步骤）只保留一次
  const visibleStepLabel = stepLabel && stepLabel !== statusLabel ? stepLabel : ''
  const retrySummary = selectedImportRetrySummary
  const retryCounts = [
    { label: '按分块重试的文件数', value: Number(retrySummary?.chunk_retry_files ?? 0) },
    { label: '按分块重试的分块数', value: Number(retrySummary?.chunk_retry_chunks ?? 0) },
    { label: '回退整文件重试数', value: Number(retrySummary?.file_fallback_files ?? 0) },
    { label: '跳过文件数', value: Number(retrySummary?.skipped_files ?? 0) },
  ]
  const hasRetrySummary = retryCounts.some((item) => item.value > 0)

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] overflow-y-auto [--dialog-width:56rem]">
        <DialogHeader className="space-y-2">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <DialogTitle className="flex flex-wrap items-center gap-2">
              任务详情
              {task ? (
                <Badge variant={getImportStatusVariant(status)}>{statusLabel}</Badge>
              ) : null}
            </DialogTitle>
            <div className="flex flex-wrap gap-2">
              <Button
                size="sm"
                variant="outline"
                aria-label="取消选中导入任务"
                onClick={() => void cancelSelectedImportTask()}
                disabled={!task}
              >
                <Trash2 className="mr-1.5 h-3.5 w-3.5" />
                取消任务
              </Button>
              <Button
                size="sm"
                aria-label="重试选中导入任务"
                onClick={() => void retrySelectedImportTask()}
                disabled={!task}
              >
                <Undo2 className="mr-1.5 h-3.5 w-3.5" />
                重试失败项
              </Button>
            </div>
          </div>
          <DialogDescription>支持文件级和分块级状态观察，可直接定位失败原因</DialogDescription>
        </DialogHeader>

        {selectedImportTaskLoading && !task ? (
          <div className="flex items-center justify-center py-10">
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <ThinkingIllustration size="sm" />
              正在加载任务详情
            </div>
          </div>
        ) : !task ? (
          <div className="rounded-xl border border-dashed bg-muted/15 px-6 py-10 text-center text-sm text-muted-foreground">
            该任务已不在列表中，刷新后重试
          </div>
        ) : (
          <div className="space-y-6">
            {selectedImportTaskErrorText ? (
              <Alert variant="destructive">
                <AlertDescription className="break-all">{selectedImportTaskErrorText}</AlertDescription>
              </Alert>
            ) : null}

            {/* 状态统一由标题徽章给出，进度组件只在步骤与状态不同时补步骤，避免同一个词出现两次 */}
            <MemoryProgressIndicator
              value={normalizeProgress(task.progress)}
              stepLabel={visibleStepLabel}
              tone={getProgressTone(status)}
              busy={RUNNING_IMPORT_STATUS.has(status)}
              detail={formatChunkSummary(
                task.done_chunks,
                task.total_chunks,
                task.failed_chunks,
                task.cancelled_chunks,
              )}
            />

            <div className="grid gap-4 rounded-xl border bg-muted/10 p-4 sm:grid-cols-2 lg:grid-cols-4">
              <InfoItem label="任务 ID" value={String(task.task_id ?? '-')} mono />
              <InfoItem
                label="任务类型"
                value={getImportTaskKindLabel(String(task.task_kind ?? task.mode ?? '-'))}
              />
              <InfoItem label="创建时间" value={formatImportTime(task.created_at)} />
              <InfoItem label="更新时间" value={formatImportTime(task.updated_at)} />
            </div>

            {hasRetrySummary ? (
              <div className="space-y-2">
                <div className="text-sm font-medium">重试摘要</div>
                <div className="grid gap-3 rounded-xl border bg-muted/10 p-4 sm:grid-cols-2 lg:grid-cols-4">
                  {retryCounts.map((item) => (
                    <div key={item.label} className="space-y-1">
                      <div className="text-xs text-muted-foreground">{item.label}</div>
                      <div className="text-sm font-medium tabular-nums">{item.value}</div>
                    </div>
                  ))}
                </div>
              </div>
            ) : null}

            <div className="space-y-2.5">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="text-sm font-medium">文件状态</div>
                <span className="text-xs text-muted-foreground">共 {selectedImportFiles.length} 个文件</span>
              </div>
              {selectedImportFiles.length > 0 ? (
                <ScrollArea className="h-[240px]">
                  <div className="space-y-2.5 p-2.5">
                    {selectedImportFiles.map((file) => {
                      const isSelected = file.file_id === selectedImportFileId
                      const fileStatusLabel = getImportStatusLabel(String(file.status ?? ''))
                      const fileStepLabel = getImportStepLabel(String(file.current_step ?? ''))
                      return (
                        <button
                          key={file.file_id}
                          type="button"
                          onClick={() => void selectImportFile(file.file_id)}
                          className={cn(
                            'w-full rounded-xl border p-4 text-left transition-all',
                            isSelected
                              ? 'border-primary/70 bg-primary/5 shadow-sm'
                              : 'bg-background/80 hover:border-muted-foreground/40 hover:bg-muted/20',
                          )}
                        >
                          <div className="flex flex-wrap items-center justify-between gap-2">
                            <span className="truncate text-sm font-medium">{file.name || file.file_id}</span>
                            <Badge variant={getImportStatusVariant(String(file.status ?? ''))}>
                              {fileStatusLabel}
                            </Badge>
                          </div>
                          <div className="mt-2 flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
                            <span>{fileStepLabel === fileStatusLabel ? '' : fileStepLabel}</span>
                            <span className="tabular-nums">{formatProgressPercent(file.progress)}</span>
                          </div>
                          <Progress value={normalizeProgress(file.progress)} className="mt-2 h-1.5" />
                          <div className="mt-2 text-xs text-muted-foreground">
                            {formatChunkSummary(
                              file.done_chunks,
                              file.total_chunks,
                              file.failed_chunks,
                              file.cancelled_chunks,
                            )}
                          </div>
                          {file.error ? (
                            <div className="mt-2 text-xs text-destructive">{file.error}</div>
                          ) : null}
                        </button>
                      )
                    })}
                  </div>
                </ScrollArea>
              ) : (
                <div className="rounded-xl border bg-muted/20 p-4 text-sm text-muted-foreground">
                  当前任务没有文件明细
                </div>
              )}
            </div>

            <div className="space-y-2.5">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="text-sm font-medium">分块状态</div>
                <div className="flex items-center gap-2 text-xs text-muted-foreground">
                  <Button
                    size="icon"
                    variant="outline"
                    aria-label="上一页分块"
                    onClick={() => void moveImportChunkPage(-1)}
                    disabled={!canImportChunkPrev}
                  >
                    <ChevronLeft className="h-4 w-4" />
                  </Button>
                  <span className="tabular-nums">
                    {importChunkTotal > 0
                      ? `${importChunkOffset + 1}-${Math.min(importChunkOffset + IMPORT_CHUNK_PAGE_SIZE, importChunkTotal)}`
                      : '0-0'}
                    {' / '}
                    {importChunkTotal}
                  </span>
                  <Button
                    size="icon"
                    variant="outline"
                    aria-label="下一页分块"
                    onClick={() => void moveImportChunkPage(1)}
                    disabled={!canImportChunkNext}
                  >
                    <ChevronRight className="h-4 w-4" />
                  </Button>
                </div>
              </div>

              <div className="overflow-auto rounded-xl border bg-background/80">
                <Table className="min-w-[700px]">
                  <TableHeader>
                    <TableRow>
                      <TableHead className="w-[72px]">序号</TableHead>
                      <TableHead className="w-[108px]">状态</TableHead>
                      <TableHead className="w-[108px]">步骤</TableHead>
                      <TableHead className="w-[84px]">进度</TableHead>
                      <TableHead>错误 / 预览</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {importChunksLoading ? (
                      <TableRow>
                        <TableCell colSpan={5} className="text-center text-muted-foreground">
                          <Loader2 className="mx-auto h-4 w-4 animate-spin" />
                        </TableCell>
                      </TableRow>
                    ) : selectedImportChunks.length > 0 ? (
                      selectedImportChunks.map((chunk) => (
                        <TableRow key={chunk.chunk_id}>
                          <TableCell className="tabular-nums">{chunk.index}</TableCell>
                          <TableCell>{getImportStatusLabel(String(chunk.status ?? ''))}</TableCell>
                          <TableCell>{getImportStepLabel(String(chunk.step ?? ''))}</TableCell>
                          <TableCell className="tabular-nums">
                            {formatProgressPercent(chunk.progress)}
                          </TableCell>
                          <TableCell className="max-w-[360px]">
                            <div className="space-y-2">
                              {String(chunk.error ?? '').trim() ? (
                                <div className="rounded-md border border-destructive/30 bg-destructive/5 px-2.5 py-2 text-sm leading-relaxed text-destructive">
                                  {String(chunk.error)}
                                </div>
                              ) : null}
                              <details className="rounded-md border bg-muted/20 px-2.5 py-2 text-xs text-muted-foreground">
                                <summary className="cursor-pointer font-medium text-foreground">
                                  {String(chunk.error ?? '').trim() ? '查看分块预览' : '查看内容详情'}
                                </summary>
                                <div className="mt-2 whitespace-pre-wrap break-words leading-relaxed">
                                  {String(chunk.content_preview ?? '-') || '-'}
                                </div>
                              </details>
                            </div>
                          </TableCell>
                        </TableRow>
                      ))
                    ) : (
                      <TableRow>
                        <TableCell colSpan={5} className="text-center text-muted-foreground">
                          当前页没有分块数据
                        </TableCell>
                      </TableRow>
                    )}
                  </TableBody>
                </Table>
              </div>
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}
