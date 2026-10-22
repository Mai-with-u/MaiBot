import { useEffect, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'

import { useMutation, useQuery } from '@tanstack/react-query'
import { ArrowUpDown, Plus, Search, SlidersHorizontal, Trash2 } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'

import { MOBILE_GLASS_CLASS } from '@/components/layout/mobile-glass'
import { MobileHeaderActions } from '@/components/layout/MobileHeaderActions'
import { useHideMobileTabBar, useMobileTitleOverride } from '@/components/layout/mobile-nav-context'
import { useMediaQuery } from '@/hooks/use-media-query'
import { useDataList } from '@/hooks/useDataList'
import { useToast } from '@/hooks/use-toast'
import {
  banEmoji,
  batchDeleteEmojis,
  deleteEmoji,
  getEmojiList,
  getEmojiStats,
  registerEmoji,
} from '@/lib/emoji-api'
import { cn } from '@/lib/utils'
import type { Emoji, EmojiStats } from '@/types/emoji'

import { EmojiCacheMaintenancePanel } from './EmojiCacheMaintenancePanel'
import { EmojiDetailDialog, EmojiEditDialog, EmojiUploadDialog } from './EmojiDialogs'
import { EmojiBrowser, type EmojiFilters } from './EmojiBrowser'
import { MobileEmojiGrid } from './MobileEmojiGrid'

// 页面可见时每 10 秒同步后台收录和使用状态，离开页面后停止轮询。
const EMOJI_REFRESH_INTERVAL = 10_000

// 与表情包列表接口的每页上限一致；补齐整行时不能把 100 向上凑成 102 或 104。
const EMOJI_MAX_PAGE_SIZE = 100

function alignEmojiPageSize(preferredSize: number, columns: number): number {
  const rows = Math.min(
    Math.ceil(preferredSize / columns),
    Math.floor(EMOJI_MAX_PAGE_SIZE / columns)
  )
  return rows * columns
}

export function EmojiManagementPage({ browserHeader }: { browserHeader?: ReactNode }) {
  const [selectedEmoji, setSelectedEmoji] = useState<Emoji | null>(null)
  const [detailDialogOpen, setDetailDialogOpen] = useState(false)
  const [editDialogOpen, setEditDialogOpen] = useState(false)
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false)
  const [batchDeleteDialogOpen, setBatchDeleteDialogOpen] = useState(false)
  const [cacheOpen, setCacheOpen] = useState(false)
  const [cardSize, setCardSize] = useState<'small' | 'medium' | 'large'>('medium')
  const [uploadDialogOpen, setUploadDialogOpen] = useState(false)
  // 手机端：长按进入多选，筛选收进底部弹层
  const isMobile = useMediaQuery('(max-width: 1023.98px)')
  const isWideGrid = useMediaQuery('(min-width: 1280px)')
  const gridColumns = isMobile
    ? 3
    : {
        small: isWideGrid ? 8 : 6,
        medium: isWideGrid ? 6 : 4,
        large: isWideGrid ? 4 : 3,
      }[cardSize]
  const [preferredPageSize, setPreferredPageSize] = useState(24)
  const alignedPageSize = alignEmojiPageSize(preferredPageSize, gridColumns)
  const pageSizeOptions = [24, 48, 72, 96].map((value) => ({
    value,
    count: alignEmojiPageSize(value, gridColumns),
  }))
  const [mobileSelecting, setMobileSelecting] = useState(false)
  const [mobileFilterOpen, setMobileFilterOpen] = useState(false)
  const selecting = isMobile && mobileSelecting

  const { toast } = useToast()

  // 表情包列表：分页/搜索/筛选/排序/多选统一由 useDataList 承载，
  // 翻页保留选中项，改参重置页码但保留选择，搜索内建 300ms 防抖。
  const list = useDataList<Emoji, EmojiFilters, number>({
    domain: 'emoji',
    initialPageSize: alignedPageSize,
    preserveSelectionOnParamsChange: true,
    getId: (emoji) => emoji.id,
    initialFilters: {
      status: 'adopted',
      format: 'all',
      sortBy: 'register_time',
      sortOrder: 'desc',
    },
    searchDebounceMs: 300,
    queryOptions: { refetchInterval: EMOJI_REFRESH_INTERVAL },
    queryFn: async ({ page, pageSize, search, filters }) => {
      const result = await getEmojiList({
        page,
        page_size: pageSize,
        status: filters.status === 'all' ? undefined : filters.status,
        format: filters.format === 'all' ? undefined : filters.format,
        search: search.trim() || undefined,
        sort_by: filters.sortBy,
        sort_order: filters.sortOrder,
      })
      return { items: result.data, total: result.total }
    },
  })
  const emojiList = list.items
  const total = list.total
  const loading = list.isPending
  const page = list.page
  const pageSize = list.pageSize
  // 分页数量必须与实际网格列数一致，避免每个非末页都留出缺角。
  const { setPageSize } = list
  useEffect(() => {
    if (pageSize !== alignedPageSize) setPageSize(alignedPageSize)
  }, [alignedPageSize, pageSize, setPageSize])
  // 删除末页最后一项后，真实查询也回到有效页，不能只修正分页文案。
  useEffect(() => {
    if (!list.isPending && !list.isFetching && !list.isError && list.page > list.totalPages) {
      list.goToPage(list.totalPages)
    }
  }, [list.isPending, list.isFetching, list.isError, list.page, list.totalPages, list.goToPage])

  // 统计失败时显示未知数，筛选和列表仍可使用。
  const statsQuery = useQuery({
    queryKey: ['emoji', 'stats'],
    queryFn: getEmojiStats,
    refetchInterval: EMOJI_REFRESH_INTERVAL,
  })
  const stats: EmojiStats | null = statsQuery.data?.data ?? null
  const refreshData = () => {
    list.invalidate()
    void statsQuery.refetch()
  }

  // 多选时页头标题换成已选数量，底部换成批量操作栏
  useMobileTitleOverride(selecting ? `已选择 ${list.selectedCount} 项` : null)
  useHideMobileTabBar(selecting)

  const exitMobileSelecting = () => {
    list.clearSelection()
    setMobileSelecting(false)
  }

  // 查看详情
  const handleViewDetail = (emoji: Emoji) => {
    setSelectedEmoji(emoji)
    setDetailDialogOpen(true)
  }

  // 编辑表情包
  const handleEdit = (emoji: Emoji) => {
    setSelectedEmoji(emoji)
    setEditDialogOpen(true)
  }

  // 删除表情包
  const handleDelete = (emoji: Emoji) => {
    setSelectedEmoji(emoji)
    setDeleteDialogOpen(true)
  }

  // 确认删除（失败由全局 mutation 错误 toast 呈现）
  const deleteMutation = useMutation({
    mutationFn: (emoji: Emoji) => deleteEmoji(emoji.id),
    meta: { errorTitle: '错误' },
    onSuccess: (_result, emoji) => {
      toast({
        title: '成功',
        description: '表情包已删除',
      })
      setDeleteDialogOpen(false)
      setSelectedEmoji(null)
      if (list.isSelected(emoji.id)) {
        list.toggle(emoji.id)
      }
      refreshData()
    },
  })

  // 确认删除
  const confirmDelete = () => {
    if (!selectedEmoji) return
    deleteMutation.mutate(selectedEmoji)
  }

  // 快速注册（失败由全局 mutation 错误 toast 呈现）
  const registerMutation = useMutation({
    mutationFn: (emoji: Emoji) => registerEmoji(emoji.id),
    meta: { errorTitle: '错误' },
    onSuccess: () => {
      toast({
        title: '成功',
        description: '表情包已注册',
      })
      refreshData()
    },
  })

  // 快速注册
  const handleRegister = (emoji: Emoji) => {
    registerMutation.mutate(emoji)
  }

  // 快速封禁（失败由全局 mutation 错误 toast 呈现）
  const banMutation = useMutation({
    mutationFn: (emoji: Emoji) => banEmoji(emoji.id),
    meta: { errorTitle: '错误' },
    onSuccess: () => {
      toast({
        title: '成功',
        description: '表情包已封禁',
      })
      refreshData()
    },
  })

  // 快速封禁
  const handleBan = (emoji: Emoji) => {
    banMutation.mutate(emoji)
  }

  // 批量删除（失败由全局 mutation 错误 toast 呈现）
  const batchDeleteMutation = useMutation({
    mutationFn: (emojiIds: number[]) => batchDeleteEmojis(emojiIds),
    meta: { errorTitle: '批量删除失败' },
    onSuccess: (result) => {
      toast({
        title: '批量删除完成',
        description: result.message,
      })
      list.clearSelection()
      setMobileSelecting(false)
      setBatchDeleteDialogOpen(false)
      refreshData()
    },
  })

  // 手机多选：批量注册 / 封禁，逐个调用单项接口
  const batchStatusMutation = useMutation({
    mutationFn: async ({ ids, action }: { ids: number[]; action: 'register' | 'ban' }) => {
      await Promise.all(ids.map((id) => (action === 'register' ? registerEmoji(id) : banEmoji(id))))
      return action
    },
    meta: { errorTitle: '批量操作失败' },
    onSuccess: (action, { ids }) => {
      toast({
        title: '成功',
        description: `已${action === 'register' ? '注册' : '封禁'} ${ids.length} 个表情包`,
      })
      exitMobileSelecting()
      refreshData()
    },
  })

  // 批量删除
  const handleBatchDelete = () => {
    batchDeleteMutation.mutate(Array.from(list.selectedIds))
  }

  // 获取格式选项
  const formatOptions = stats ? Object.keys(stats.formats) : []
  const statusOptions = stats
    ? [
        { value: 'known' as const, label: '认识', count: stats.known },
        { value: 'unknown' as const, label: '不认识', count: stats.unknown },
        { value: 'adopted' as const, label: '据为己用', count: stats.adopted },
        { value: 'discarded' as const, label: '丢弃', count: stats.discarded },
      ]
    : []

  return (
    <div className="flex h-full min-h-0 min-w-0 flex-col p-3 pb-4 max-lg:h-auto sm:p-4">
      {!isMobile && (
        <EmojiBrowser
          browserHeader={browserHeader}
          stats={stats}
          filters={list.filters}
          onFilterChange={list.setFilter}
          search={list.searchInput}
          onSearchChange={list.setSearchInput}
          cardSize={cardSize}
          onCardSizeChange={setCardSize}
          columns={gridColumns}
          emojiList={emojiList}
          loading={loading}
          total={total}
          page={page}
          pageSize={pageSize}
          pageSizeOptions={pageSizeOptions.map((option) => option.count)}
          onPageSizeChange={(size) => {
            const option = pageSizeOptions.find((item) => item.count === size)
            if (option) setPreferredPageSize(option.value)
          }}
          onPageChange={list.goToPage}
          selectedIds={list.selectedIds}
          onToggleSelect={list.toggle}
          onEdit={handleEdit}
          onViewDetail={handleViewDetail}
          onRegister={handleRegister}
          onBan={handleBan}
          onDelete={handleDelete}
          onUpload={() => setUploadDialogOpen(true)}
          onOpenCache={() => setCacheOpen(true)}
          error={list.isError ? list.error?.message : undefined}
          onRetry={() => list.refetch()}
          batchActions={
            list.selectedCount > 0 && (
              <div className="mt-3 flex flex-wrap items-center gap-2 border-t pt-3">
                <span className="text-muted-foreground mr-auto text-xs">
                  已选择 {list.selectedCount} 个表情包
                </span>
                <Button variant="outline" size="xs" onClick={list.toggleAll}>
                  全选本页
                </Button>
                <Button variant="outline" size="xs" onClick={list.clearSelection}>
                  取消选择
                </Button>
                <Button
                  variant="destructive"
                  size="xs"
                  onClick={() => setBatchDeleteDialogOpen(true)}
                >
                  <Trash2 />
                  批量删除
                </Button>
              </div>
            )
          }
        />
      )}
      <div className="space-y-4 lg:hidden">
        {isMobile && (
          <div data-mobile-emoji-toolbar="true" className="space-y-3">
            <div className="relative">
              <Search className="text-muted-foreground absolute top-1/2 left-3 h-4 w-4 -translate-y-1/2" />
              <Input
                value={list.searchInput}
                onChange={(event) => list.setSearchInput(event.target.value)}
                placeholder="搜索 tag、描述或哈希..."
                aria-label="搜索表情包"
                className="h-10 rounded-xl pl-9"
              />
            </div>
            {stats && (
              <nav
                aria-label="表情包状态"
                data-mobile-pill-nav="true"
                className="-mx-4 flex gap-2 overflow-x-auto overflow-y-hidden px-4 py-1 [scrollbar-width:none]"
                style={{ touchAction: 'pan-x pan-y' }}
              >
                {statusOptions.map((option) => {
                  const active = list.filters.status === option.value
                  return (
                    <button
                      key={option.value}
                      type="button"
                      aria-pressed={active}
                      data-active={active ? 'true' : 'false'}
                      onClick={() => list.setFilter('status', option.value)}
                      className={cn(
                        'h-9 shrink-0 rounded-full px-4 text-[15px]',
                        active ? 'bg-foreground text-background font-semibold' : 'bg-card'
                      )}
                    >
                      {option.label} {option.count}
                    </button>
                  )
                })}
              </nav>
            )}
          </div>
        )}

        {/* 手机：满宽三列网格，点按看详情、长按多选 */}
        {isMobile && !list.isError && (
          <MobileEmojiGrid
            emojiList={emojiList}
            total={total}
            page={page}
            pageSize={pageSize}
            selecting={selecting}
            selectedIds={list.selectedIds}
            onPageChange={list.goToPage}
            onOpen={handleViewDetail}
            onStartSelecting={(id) => {
              setMobileSelecting(true)
              if (!list.isSelected(id)) list.toggle(id)
            }}
            onToggleSelect={list.toggle}
          />
        )}

        {isMobile && list.isError && (
          <div className="py-12 text-center">
            <p className="text-destructive">{list.error?.message}</p>
            <Button variant="outline" size="sm" onClick={() => list.refetch()}>
              重试
            </Button>
          </div>
        )}
        {isMobile && (
          <Button variant="outline" size="sm" onClick={() => setCacheOpen(true)}>
            缓存维护
          </Button>
        )}
      </div>
      <Dialog open={cacheOpen} onOpenChange={setCacheOpen}>
        <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-4xl">
          <DialogHeader>
            <DialogTitle>缓存维护</DialogTitle>
            <DialogDescription>管理缩略图和本地图片缓存</DialogDescription>
          </DialogHeader>
          <EmojiCacheMaintenancePanel onCacheChanged={refreshData} />
        </DialogContent>
      </Dialog>
      {/* 详情对话框 */}
      <EmojiDetailDialog
        emoji={selectedEmoji}
        open={detailDialogOpen}
        onOpenChange={setDetailDialogOpen}
        actions={
          isMobile && selectedEmoji ? (
            <div className="grid w-full grid-cols-2 gap-2">
              <Button
                variant="outline"
                onClick={() => {
                  setDetailDialogOpen(false)
                  handleEdit(selectedEmoji)
                }}
              >
                编辑
              </Button>
              {selectedEmoji.status !== 'adopted' ? (
                <Button
                  variant="outline"
                  onClick={() => {
                    setDetailDialogOpen(false)
                    handleRegister(selectedEmoji)
                  }}
                >
                  注册
                </Button>
              ) : (
                <Button
                  variant="outline"
                  onClick={() => {
                    setDetailDialogOpen(false)
                    handleBan(selectedEmoji)
                  }}
                >
                  封禁
                </Button>
              )}
              <Button
                variant="destructive"
                className="col-span-2"
                onClick={() => {
                  setDetailDialogOpen(false)
                  handleDelete(selectedEmoji)
                }}
              >
                删除
              </Button>
            </div>
          ) : undefined
        }
      />

      {/* 编辑对话框 */}
      <EmojiEditDialog
        emoji={selectedEmoji}
        open={editDialogOpen}
        onOpenChange={setEditDialogOpen}
        onSuccess={refreshData}
      />

      {/* 上传对话框 */}
      <EmojiUploadDialog
        open={uploadDialogOpen}
        onOpenChange={setUploadDialogOpen}
        onSuccess={refreshData}
      />

      {/* 手机页头右上角：多选时为全选 / 完成，平时为筛选与新增 */}
      <MobileHeaderActions>
        {selecting ? (
          <>
            <button
              type="button"
              onClick={list.toggleAll}
              data-mobile-header-text-button="true"
              className={cn('h-11 rounded-full px-4 text-[15px]', MOBILE_GLASS_CLASS)}
            >
              全选
            </button>
            <button
              type="button"
              onClick={exitMobileSelecting}
              data-mobile-header-text-button="primary"
              className="bg-foreground text-background h-11 rounded-full px-4 text-[15px] font-semibold"
            >
              完成
            </button>
          </>
        ) : (
          <>
            <button
              type="button"
              aria-label="筛选与排序"
              onClick={() => setMobileFilterOpen(true)}
              data-mobile-header-icon-button="true"
              className={cn(
                'flex h-11 w-11 items-center justify-center rounded-full',
                MOBILE_GLASS_CLASS
              )}
            >
              <SlidersHorizontal className="h-5 w-5" strokeWidth={1.7} />
            </button>
            <button
              type="button"
              aria-label="新增表情包"
              onClick={() => setUploadDialogOpen(true)}
              data-mobile-header-button="true"
              className={cn(
                'flex h-11 w-11 items-center justify-center rounded-full',
                MOBILE_GLASS_CLASS
              )}
            >
              <Plus className="h-[22px] w-[22px]" strokeWidth={1.7} />
            </button>
          </>
        )}
      </MobileHeaderActions>

      {/* 手机多选的批量操作栏，占据底部标签栏的位置；页面外层有 transform，需挂到 body 上才能贴底 */}
      {selecting &&
        createPortal(
          <div
            role="toolbar"
            aria-label="批量操作"
            data-mobile-batch-toolbar="true"
            className={cn(
              'fixed inset-x-4 bottom-[calc(env(safe-area-inset-bottom)+14px)] z-40 grid h-[62px] grid-cols-3 items-center rounded-full px-2',
              MOBILE_GLASS_CLASS
            )}
          >
            {[
              { key: 'register', label: '注册' },
              { key: 'ban', label: '禁用' },
              { key: 'delete', label: '删除' },
            ].map((action) => (
              <button
                key={action.key}
                type="button"
                data-mobile-batch-action={action.key}
                disabled={list.selectedCount === 0 || batchStatusMutation.isPending}
                onClick={() => {
                  const ids = Array.from(list.selectedIds)
                  if (action.key === 'delete') setBatchDeleteDialogOpen(true)
                  else
                    batchStatusMutation.mutate({
                      ids,
                      action: action.key as 'register' | 'ban',
                    })
                }}
                className={cn(
                  'h-12 rounded-full text-[15px] font-medium disabled:opacity-40',
                  action.key === 'delete' && 'text-[#ff3b30]'
                )}
              >
                {action.label}
              </button>
            ))}
          </div>,
          document.body
        )}

      {/* 手机筛选弹层：排序、格式与每页数量 */}
      <Dialog open={mobileFilterOpen} onOpenChange={setMobileFilterOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>筛选与排序</DialogTitle>
            <DialogDescription className="sr-only">
              调整表情包的排序、格式与每页数量
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-2">
              <Label>排序方式</Label>
              <div className="flex gap-2">
                <Select
                  value={list.filters.sortBy}
                  onValueChange={(value) => list.setFilter('sortBy', value)}
                >
                  <SelectTrigger aria-label="排序字段" className="min-w-0 flex-1">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="usage_count">使用次数</SelectItem>
                    <SelectItem value="register_time">注册时间</SelectItem>
                    <SelectItem value="record_time">记录时间</SelectItem>
                    <SelectItem value="last_used_time">最后使用</SelectItem>
                  </SelectContent>
                </Select>
                <Button
                  type="button"
                  variant="outline"
                  className="shrink-0 gap-1.5"
                  onClick={() =>
                    list.setFilter('sortOrder', list.filters.sortOrder === 'desc' ? 'asc' : 'desc')
                  }
                >
                  <ArrowUpDown className="h-4 w-4" />
                  {list.filters.sortOrder === 'desc' ? '倒序' : '正序'}
                </Button>
              </div>
            </div>
            <div className="space-y-2">
              <Label>格式</Label>
              <Select
                value={list.filters.format}
                onValueChange={(value) => list.setFilter('format', value)}
              >
                <SelectTrigger aria-label="格式">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">全部</SelectItem>
                  {formatOptions.map((format) => (
                    <SelectItem key={format} value={format}>
                      {format.toUpperCase()} ({stats?.formats[format]})
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <Label>每页显示</Label>
              <Select
                value={preferredPageSize.toString()}
                onValueChange={(value) => setPreferredPageSize(parseInt(value))}
              >
                <SelectTrigger aria-label="每页显示">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {pageSizeOptions.map((option) => (
                    <SelectItem key={option.value} value={option.value.toString()}>
                      {option.count}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
          <DialogFooter>
            <Button onClick={() => setMobileFilterOpen(false)}>完成</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 批量删除确认对话框 */}
      <AlertDialog open={batchDeleteDialogOpen} onOpenChange={setBatchDeleteDialogOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>确认批量删除</AlertDialogTitle>
            <AlertDialogDescription>
              你确定要删除选中的 {list.selectedCount} 个表情包吗?此操作不可撤销。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction onClick={handleBatchDelete}>确认删除</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* 删除确认对话框 */}
      <Dialog open={deleteDialogOpen} onOpenChange={setDeleteDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>确认删除</DialogTitle>
            <DialogDescription>确定要删除这个表情包吗?此操作无法撤销。</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteDialogOpen(false)}>
              取消
            </Button>
            <Button variant="destructive" onClick={confirmDelete}>
              删除
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
