import { useMemo, useState } from 'react'
import { AlertTriangle, RotateCcw, Search, Trash2 } from 'lucide-react'

import { Alert, AlertDescription } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
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
import { ScrollArea } from '@/components/ui/scroll-area'
import type {
  MemoryDeleteExecutePayload,
  MemoryDeletePreviewItemPayload,
  MemoryDeletePreviewPayload,
} from '@/lib/memory-api'

const DELETE_PREVIEW_PAGE_SIZE = 8
const EMPTY_PREVIEW_ITEMS: MemoryDeletePreviewItemPayload[] = []

function formatMode(mode: string): string {
  switch (mode) {
    case 'entity':
      return '实体删除'
    case 'relation':
      return '关系删除'
    case 'paragraph':
      return '段落删除'
    case 'source':
      return '来源删除'
    case 'mixed':
      return '混合删除'
    default:
      return mode || '删除'
  }
}

function formatCountLabel(label: string, value: number): string {
  return `${label} ${value}`
}

function PreviewItemList({ items }: { items: MemoryDeletePreviewItemPayload[] }) {
  if (items.length <= 0) {
    return <p className="text-sm text-muted-foreground">当前预览没有可展示的明细项。</p>
  }

  return (
    <div className="space-y-2">
      {items.slice(0, 16).map((item) => (
        <div key={`${item.item_type}:${item.item_hash}:${item.item_key ?? ''}`} className="rounded-lg border bg-muted/30 p-3">
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="outline">{item.item_type}</Badge>
            {item.source ? <Badge variant="secondary">{item.source}</Badge> : null}
          </div>
          <div className="mt-2 text-sm font-medium break-words">{item.label || item.item_key || item.item_hash}</div>
          {item.preview ? <div className="mt-1 text-xs text-muted-foreground break-words">{item.preview}</div> : null}
          <code className="mt-2 block break-all text-[11px] text-muted-foreground">{item.item_hash || item.item_key}</code>
        </div>
      ))}
    </div>
  )
}

interface MemoryDeleteDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  description?: string
  preview: MemoryDeletePreviewPayload | null
  result: MemoryDeleteExecutePayload | null
  loadingPreview?: boolean
  executing?: boolean
  restoring?: boolean
  error?: string | null
  onExecute: () => void
  onRestore?: () => void
}

export function MemoryDeleteDialog({
  open,
  onOpenChange,
  title,
  description,
  preview,
  result,
  loadingPreview = false,
  executing = false,
  restoring = false,
  error,
  onExecute,
  onRestore,
}: MemoryDeleteDialogProps) {
  const [itemSearch, setItemSearch] = useState('')
  const [itemPage, setItemPage] = useState(1)
  const previewResetKey = `${open}:${preview?.mode ?? ''}:${preview?.item_count ?? ''}`
  const [seenPreviewResetKey, setSeenPreviewResetKey] = useState(previewResetKey)
  const [seenItemSearch, setSeenItemSearch] = useState(itemSearch)
  const counts = preview?.counts ?? result?.counts ?? {}
  const previewSources = Array.isArray(preview?.sources) ? preview.sources : []
  const previewItems = Array.isArray(preview?.items) ? preview.items : EMPTY_PREVIEW_ITEMS
  const filteredPreviewItems = useMemo(() => {
    const keyword = itemSearch.trim().toLowerCase()
    if (!keyword) {
      return previewItems
    }
    return previewItems.filter((item) =>
      [
        item.item_type,
        item.item_hash,
        item.item_key,
        item.label,
        item.preview,
        item.source,
      ]
        .map((value) => String(value ?? '').toLowerCase())
        .some((value) => value.includes(keyword)),
    )
  }, [itemSearch, previewItems])
  const itemPageCount = Math.max(1, Math.ceil(filteredPreviewItems.length / DELETE_PREVIEW_PAGE_SIZE))
  const pagedPreviewItems = useMemo(() => {
    const start = (itemPage - 1) * DELETE_PREVIEW_PAGE_SIZE
    return filteredPreviewItems.slice(start, start + DELETE_PREVIEW_PAGE_SIZE)
  }, [filteredPreviewItems, itemPage])
  const countBadges = [
    { key: 'entities', label: '实体', value: Number(counts.entities ?? 0) },
    { key: 'relations', label: '关系', value: Number(counts.relations ?? 0) },
    { key: 'paragraphs', label: '段落', value: Number(counts.paragraphs ?? 0) },
    { key: 'sources', label: '来源', value: Number(counts.sources ?? 0) },
  ].filter((item) => item.value > 0)

  if (seenPreviewResetKey !== previewResetKey) {
    setSeenPreviewResetKey(previewResetKey)
    setSeenItemSearch('')
    setItemSearch('')
    setItemPage(1)
  } else if (seenItemSearch !== itemSearch) {
    setSeenItemSearch(itemSearch)
    setItemPage(1)
  } else if (itemPage > itemPageCount) {
    setItemPage(itemPageCount)
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-3xl max-h-[85vh] grid grid-rows-[auto_1fr_auto]" confirmOnEnter>
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <AlertTriangle className="h-5 w-5 text-amber-500" />
            {title}
          </DialogTitle>
          {description ? <DialogDescription>{description}</DialogDescription> : null}
        </DialogHeader>

        <DialogBody className="space-y-4 overflow-y-auto">
          {loadingPreview ? (
            <div className="rounded-lg border bg-muted/30 p-4 text-sm text-muted-foreground">正在生成删除预览...</div>
          ) : null}

          {error ? (
            <Alert variant="destructive">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          ) : null}

          {preview ? (
            <>
              <div className="rounded-xl border bg-muted/30 p-4">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge>{formatMode(preview.mode)}</Badge>
                  <Badge variant="secondary">{formatCountLabel('预览项', Number(preview.item_count ?? previewItems.length))}</Badge>
                  {countBadges.map((item) => (
                    <Badge key={item.key} variant="outline">
                      {formatCountLabel(item.label, item.value)}
                    </Badge>
                  ))}
                </div>
                {previewSources.length > 0 ? (
                  <div className="mt-3 text-sm text-muted-foreground break-words">
                    关联来源：{previewSources.join('、')}
                  </div>
                ) : null}
                {preview.matched_source_count ? (
                  <div className="mt-2 text-xs text-muted-foreground">
                    命中来源 {preview.matched_source_count}
                    {preview.requested_source_count ? ` / 请求来源 ${preview.requested_source_count}` : ''}
                  </div>
                ) : null}
              </div>

              <div className="space-y-2">
                <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
                  <div>
                    <div className="text-sm font-semibold">本次将删除的对象摘要</div>
                    <div className="text-xs text-muted-foreground">
                      命中 {filteredPreviewItems.length} / {previewItems.length} 项
                    </div>
                  </div>
                  <div className="flex flex-col gap-2 md:min-w-[300px]">
                    <div className="relative">
                      <Search className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
                      <Input
                        value={itemSearch}
                        onChange={(event) => setItemSearch(event.target.value)}
                        placeholder="搜索类型 / hash / item_key / source"
                        className="pl-8"
                      />
                    </div>
                    <div className="flex items-center justify-between text-xs text-muted-foreground">
                      <span>第 {itemPage} / {itemPageCount} 页</span>
                      <span>每页 {DELETE_PREVIEW_PAGE_SIZE} 项</span>
                    </div>
                  </div>
                </div>
                <ScrollArea className="h-[320px] rounded-lg border bg-background/60">
                  <div className="p-3">
                    <PreviewItemList items={pagedPreviewItems} />
                  </div>
                </ScrollArea>
                <div className="flex items-center justify-between gap-2">
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => setItemPage((current) => Math.max(1, current - 1))}
                    disabled={itemPage <= 1}
                  >
                    上一页
                  </Button>
                  <div className="text-xs text-muted-foreground">
                    支持按对象类型、hash、item_key、source 和预览内容检索
                  </div>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => setItemPage((current) => Math.min(itemPageCount, current + 1))}
                    disabled={itemPage >= itemPageCount}
                  >
                    下一页
                  </Button>
                </div>
              </div>
            </>
          ) : null}

          {result?.success ? (
            <Alert>
              <AlertDescription className="space-y-1">
                <div>删除执行成功，操作 ID：<code>{result.operation_id}</code></div>
                <div>
                  实际删除：实体 {result.deleted_entity_count}，关系 {result.deleted_relation_count}，段落 {result.deleted_paragraph_count}，来源 {result.deleted_source_count}
                </div>
              </AlertDescription>
            </Alert>
          ) : null}
        </DialogBody>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            关闭
          </Button>
          {result?.success && onRestore ? (
            <Button variant="outline" onClick={onRestore} disabled={restoring}>
              <RotateCcw className="mr-2 h-4 w-4" />
              {restoring ? '恢复中...' : '恢复本次删除'}
            </Button>
          ) : null}
          {!result?.success ? (
            <Button data-dialog-action="confirm" variant="destructive" onClick={onExecute} disabled={loadingPreview || executing || !preview}>
              <Trash2 className="mr-2 h-4 w-4" />
              {executing ? '执行中...' : '确认删除'}
            </Button>
          ) : null}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
