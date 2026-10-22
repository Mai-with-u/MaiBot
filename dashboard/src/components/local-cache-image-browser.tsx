import { Card } from '@/components/ui/card'
import {
  CalendarDays,
  ChevronLeft,
  ChevronRight,
  ImageOff,
  Loader2,
  RefreshCw,
  Trash2,
} from 'lucide-react'
import { useEffect, useState } from 'react'

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from '@/components/ui/alert-dialog'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { backendApi } from '@/lib/http'
import {
  formatLocalCacheBytes,
  formatLocalCacheModifiedTime,
  LOCAL_CACHE_IMAGE_PAGE_SIZE,
  type ImageDateFilters,
} from '@/components/local-cache-image-utils'
import {
  getLocalCacheImagePreviewUrl,
  type LocalCacheImageItem,
  type LocalCacheImageListResponse,
  type LocalCacheImageTarget,
} from '@/lib/system-api'

function CacheImagePreview({
  item,
  target,
}: {
  item: LocalCacheImageItem
  target: LocalCacheImageTarget
}) {
  const previewKey = `${target}:${item.relative_path}`
  const previewUrl = getLocalCacheImagePreviewUrl(target, item.relative_path)
  const [preview, setPreview] = useState<{
    hasError: boolean
    key: string
    objectUrl: string | null
  }>({
    hasError: false,
    key: previewKey,
    objectUrl: null,
  })

  useEffect(() => {
    let cancelled = false
    let createdUrl: string | null = null

    void backendApi
      .get<Blob>(previewUrl, { parse: 'blob', errorMessage: '图片预览加载失败' })
      .then((blob) => {
        if (cancelled) {
          return
        }
        createdUrl = URL.createObjectURL(blob)
        setPreview({ hasError: false, key: previewKey, objectUrl: createdUrl })
      })
      .catch(() => {
        if (!cancelled) {
          setPreview({ hasError: true, key: previewKey, objectUrl: null })
        }
      })

    return () => {
      cancelled = true
      if (createdUrl) {
        URL.revokeObjectURL(createdUrl)
      }
    }
  }, [previewKey, previewUrl])

  if (preview.key === previewKey && preview.hasError) {
    return (
      <div className="flex h-full w-full items-center justify-center bg-muted">
        <ImageOff className="h-7 w-7 text-muted-foreground" />
      </div>
    )
  }

  if (preview.key !== previewKey || !preview.objectUrl) {
    return <Skeleton className="h-full w-full" />
  }

  return (
    <img
      src={preview.objectUrl}
      alt={item.file_name}
      className="h-full w-full object-contain"
    />
  )
}

export function CacheImageListPanel({
  cleanupDisabled,
  deletingKey,
  filters,
  isLoading,
  list,
  onDelete,
  onDeleteAll,
  onDeleteDateRange,
  onDeleteOlderThanRecentDays,
  onFilterChange,
  onFilterClear,
  onFilterSubmit,
  onPageChange,
  onRefresh,
  target,
}: {
  cleanupDisabled: boolean
  deletingKey: string | null
  filters: ImageDateFilters
  isLoading: boolean
  list: LocalCacheImageListResponse | null
  onDelete: (target: LocalCacheImageTarget, item: LocalCacheImageItem) => void
  onDeleteAll: (target: LocalCacheImageTarget) => void
  onDeleteDateRange: (target: LocalCacheImageTarget) => void
  onDeleteOlderThanRecentDays: (target: LocalCacheImageTarget, days: 1 | 7 | 30) => void
  onFilterChange: (target: LocalCacheImageTarget, filters: ImageDateFilters) => void
  onFilterClear: (target: LocalCacheImageTarget) => void
  onFilterSubmit: (target: LocalCacheImageTarget, filters?: ImageDateFilters) => void
  onPageChange: (target: LocalCacheImageTarget, page: number) => void
  onRefresh: (target: LocalCacheImageTarget) => void
  target: LocalCacheImageTarget
}) {
  const targetLabel = target === 'images' ? '图片缓存' : '表情包缓存'
  const items = list?.data ?? []
  const dateGroups = list?.date_groups ?? []
  const currentPage = list?.page ?? 1
  const pageSize = list?.page_size ?? LOCAL_CACHE_IMAGE_PAGE_SIZE
  const total = list?.total ?? 0
  const totalPages = Math.max(1, Math.ceil(total / pageSize))
  const hasDateFilter = Boolean(filters.startDate || filters.endDate)

  return (
    <div className="space-y-4">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h4 className="text-sm font-semibold">{targetLabel}列表</h4>
          <p className="mt-1 text-xs text-muted-foreground">
            {hasDateFilter ? '当前日期范围内' : '当前列表'}共 {total} 个文件，占用 {formatLocalCacheBytes(list?.total_size ?? 0)}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" size="sm" onClick={() => onRefresh(target)} disabled={isLoading} className="gap-2">
            <RefreshCw className={`h-4 w-4 ${isLoading ? 'animate-spin' : ''}`} />
            刷新列表
          </Button>
          <AlertDialog>
            <AlertDialogTrigger asChild>
              <Button variant="outline" size="sm" className="gap-2" disabled={cleanupDisabled || total === 0}>
                <Trash2 className="h-4 w-4" />
                全部删除
              </Button>
            </AlertDialogTrigger>
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>确认删除全部{targetLabel}？</AlertDialogTitle>
                <AlertDialogDescription>
                  这会删除当前缓存目录中的所有文件，并移除数据库里的相关记录。操作不可撤销。
                </AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel>取消</AlertDialogCancel>
                <AlertDialogAction onClick={() => onDeleteAll(target)}>确认删除</AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>
        </div>
      </div>

      <Card className="p-4">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
          <div className="grid gap-3 sm:grid-cols-2 lg:min-w-[360px]">
            <label className="space-y-1.5" htmlFor={`${target}-cache-start-date`}>
              <span className="text-xs text-muted-foreground">开始日期</span>
              <Input
                id={`${target}-cache-start-date`}
                type="date"
                value={filters.startDate}
                onChange={(event) => onFilterChange(target, { ...filters, startDate: event.target.value })}
              />
            </label>
            <label className="space-y-1.5" htmlFor={`${target}-cache-end-date`}>
              <span className="text-xs text-muted-foreground">结束日期</span>
              <Input
                id={`${target}-cache-end-date`}
                type="date"
                value={filters.endDate}
                onChange={(event) => onFilterChange(target, { ...filters, endDate: event.target.value })}
              />
            </label>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button variant="outline" size="sm" className="gap-2" onClick={() => onFilterSubmit(target)} disabled={isLoading}>
              <CalendarDays className="h-4 w-4" />
              按日期浏览
            </Button>
            <Button variant="ghost" size="sm" onClick={() => onFilterClear(target)} disabled={isLoading || !hasDateFilter}>
              清空日期
            </Button>
            <AlertDialog>
              <AlertDialogTrigger asChild>
                <Button variant="outline" size="sm" className="gap-2" disabled={cleanupDisabled || !hasDateFilter}>
                  <Trash2 className="h-4 w-4" />
                  删除区间
                </Button>
              </AlertDialogTrigger>
              <AlertDialogContent>
                <AlertDialogHeader>
                  <AlertDialogTitle>确认删除当前日期区间内的{targetLabel}？</AlertDialogTitle>
                  <AlertDialogDescription>
                    这会删除 {filters.startDate || '最早'} 到 {filters.endDate || '最晚'} 之间的缓存文件，并移除对应数据库记录。操作不可撤销。
                  </AlertDialogDescription>
                </AlertDialogHeader>
                <AlertDialogFooter>
                  <AlertDialogCancel>取消</AlertDialogCancel>
                  <AlertDialogAction onClick={() => onDeleteDateRange(target)}>确认删除</AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>
          </div>
        </div>

        <div className="mt-4 flex flex-wrap gap-2">
          {dateGroups.slice(0, 30).map((group) => (
            <Button
              key={group.date}
              variant={filters.startDate === group.date && filters.endDate === group.date ? 'default' : 'outline'}
              size="sm"
              className="h-auto gap-2 py-1.5"
              onClick={() => {
                const nextFilters = { startDate: group.date, endDate: group.date }
                onFilterChange(target, nextFilters)
                onFilterSubmit(target, nextFilters)
              }}
            >
              <span>{group.date}</span>
              <span className="text-xs opacity-70">{group.file_count} 个 / {formatLocalCacheBytes(group.total_size)}</span>
            </Button>
          ))}
          {dateGroups.length === 0 && (
            <span className="text-sm text-muted-foreground">暂无可按日期浏览的缓存文件</span>
          )}
        </div>

        <div className="mt-4 flex flex-wrap gap-2 border-t pt-4">
          {[1, 7, 30].map((days) => (
            <AlertDialog key={days}>
              <AlertDialogTrigger asChild>
                <Button variant="outline" size="sm" disabled={cleanupDisabled || total === 0}>
                  删除最近 {days} 天以外
                </Button>
              </AlertDialogTrigger>
              <AlertDialogContent>
                <AlertDialogHeader>
                  <AlertDialogTitle>确认清理旧{targetLabel}？</AlertDialogTitle>
                  <AlertDialogDescription>
                    这会保留最近 {days} 天内的缓存，删除更早的文件，并移除对应数据库记录。操作不可撤销。
                  </AlertDialogDescription>
                </AlertDialogHeader>
                <AlertDialogFooter>
                  <AlertDialogCancel>取消</AlertDialogCancel>
                  <AlertDialogAction onClick={() => onDeleteOlderThanRecentDays(target, days as 1 | 7 | 30)}>
                    确认删除
                  </AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>
          ))}
        </div>
      </Card>

      <div className="space-y-3">
        {isLoading && !list ? (
          Array.from({ length: 3 }).map((_, index) => (
            <div key={index} className="flex gap-3 rounded-md border p-3">
              <Skeleton className="h-20 w-20 shrink-0" />
              <div className="flex-1 space-y-2">
                <Skeleton className="h-4 w-1/3" />
                <Skeleton className="h-3 w-2/3" />
                <Skeleton className="h-3 w-1/2" />
              </div>
            </div>
          ))
        ) : items.length === 0 ? (
          <div className="rounded-md border border-dashed p-8 text-center text-sm text-muted-foreground">
            暂无图片缓存
          </div>
        ) : (
          items.map((item) => {
            const itemKey = `${target}:${item.relative_path}`
            const deleting = deletingKey === itemKey
            return (
              <div key={item.relative_path} className="flex flex-col gap-3 rounded-md border p-3 sm:flex-row sm:items-center">
                <div className="h-20 w-20 shrink-0 overflow-hidden rounded-md border bg-muted">
                  <CacheImagePreview item={item} target={target} />
                </div>

                <div className="min-w-0 flex-1 space-y-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="max-w-full truncate text-sm font-medium">{item.file_name}</span>
                    <Badge variant="outline">{item.format.toUpperCase()}</Badge>
                    {item.db_id !== null ? (
                      <Badge variant="secondary">数据库记录</Badge>
                    ) : (
                      <Badge variant="outline">仅文件</Badge>
                    )}
                    {target === 'emoji' && item.is_registered !== null && (
                      <Badge variant={item.is_registered ? 'default' : 'secondary'}>
                        {item.is_registered ? '已注册' : '未注册'}
                      </Badge>
                    )}
                    {item.is_banned && <Badge variant="destructive">已禁用</Badge>}
                  </div>
                  <p className="break-all text-xs text-muted-foreground">{item.relative_path}</p>
                  <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
                    <span>{formatLocalCacheBytes(item.size)}</span>
                    <span>{formatLocalCacheModifiedTime(item.modified_time)}</span>
                    {item.image_hash && <span className="max-w-full truncate font-mono">hash: {item.image_hash}</span>}
                  </div>
                  {item.description && (
                    <p className="line-clamp-2 text-xs text-muted-foreground">{item.description}</p>
                  )}
                </div>

                <AlertDialog>
                  <AlertDialogTrigger asChild>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="self-end text-destructive hover:text-destructive sm:self-center"
                      disabled={cleanupDisabled || deleting}
                      title="删除"
                    >
                      {deleting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Trash2 className="h-4 w-4" />}
                    </Button>
                  </AlertDialogTrigger>
                  <AlertDialogContent>
                    <AlertDialogHeader>
                      <AlertDialogTitle>确认删除这张图片？</AlertDialogTitle>
                      <AlertDialogDescription className="break-words">
                        将删除 <span className="break-all font-mono">{item.file_name}</span>
                        ，并移除对应数据库记录。操作不可撤销。
                      </AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                      <AlertDialogCancel>取消</AlertDialogCancel>
                      <AlertDialogAction onClick={() => onDelete(target, item)}>确认删除</AlertDialogAction>
                    </AlertDialogFooter>
                  </AlertDialogContent>
                </AlertDialog>
              </div>
            )
          })
        )}
      </div>

      {total > 0 && (
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="text-xs text-muted-foreground">
            显示 {(currentPage - 1) * pageSize + 1} 到 {Math.min(currentPage * pageSize, total)}，共 {total} 个
          </div>
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={() => onPageChange(target, Math.max(1, currentPage - 1))}
              disabled={isLoading || currentPage <= 1}
            >
              <ChevronLeft className="h-4 w-4" />
              上一页
            </Button>
            <span className="text-xs text-muted-foreground">
              {currentPage} / {totalPages}
            </span>
            <Button
              variant="outline"
              size="sm"
              onClick={() => onPageChange(target, Math.min(totalPages, currentPage + 1))}
              disabled={isLoading || currentPage >= totalPages}
            >
              下一页
              <ChevronRight className="h-4 w-4" />
            </Button>
          </div>
        </div>
      )}
    </div>
  )
}
