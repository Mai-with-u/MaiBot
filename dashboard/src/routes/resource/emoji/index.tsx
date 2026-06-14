import { useState } from 'react'

import { useMutation, useQuery } from '@tanstack/react-query'
import { Plus, RefreshCw, Search, Trash2, X } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { DashboardTabBar, DashboardTabTrigger } from '@/components/ui/dashboard-tabs'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
} from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { ScrollArea } from '@/components/ui/scroll-area'
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
import { Tabs } from '@/components/ui/tabs'

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
import type { Emoji, EmojiStats, EmojiStatus } from '@/types/emoji'

import {
  EmojiDetailDialog,
  EmojiEditDialog,
  EmojiUploadDialog,
} from './EmojiDialogs'
import { EmojiList } from './EmojiList'

// 表情包筛选项：状态 / 格式 / 排序字段 / 排序方向
interface EmojiFilters {
  status: EmojiStatus | 'all'
  format: string
  sortBy: string
  sortOrder: 'desc' | 'asc'
}

export function EmojiManagementPage() {
  const [selectedEmoji, setSelectedEmoji] = useState<Emoji | null>(null)
  const [detailDialogOpen, setDetailDialogOpen] = useState(false)
  const [editDialogOpen, setEditDialogOpen] = useState(false)
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false)
  const [batchDeleteDialogOpen, setBatchDeleteDialogOpen] = useState(false)
  const [jumpToPage, setJumpToPage] = useState('')
  const [cardSize, setCardSize] = useState<'small' | 'medium' | 'large'>(
    'medium'
  )
  const [uploadDialogOpen, setUploadDialogOpen] = useState(false)

  const { toast } = useToast()

  // 表情包列表：分页/搜索/筛选/排序/多选统一由 useDataList 承载，
  // 翻页/改参自动重置页码并清空选中，搜索内建 300ms 防抖
  const list = useDataList<Emoji, EmojiFilters, number>({
    domain: 'emoji',
    getId: (emoji) => emoji.id,
    initialFilters: { status: 'adopted', format: 'all', sortBy: 'usage_count', sortOrder: 'desc' },
    searchDebounceMs: 300,
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
  // 命中提示用的已去空搜索词（与防抖后驱动查询的值一致）
  const searchKeyword = list.searchInput.trim()

  // 统计数据：失败时保持 null，状态切换 Tabs 自动隐藏，不打断页面
  const statsQuery = useQuery({
    queryKey: ['emoji', 'stats'],
    queryFn: getEmojiStats,
  })
  const stats: EmojiStats | null = statsQuery.data?.data ?? null

  // 查看详情
  const handleViewDetail = async (emoji: Emoji) => {
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
    onSuccess: () => {
      toast({
        title: '成功',
        description: '表情包已删除',
      })
      setDeleteDialogOpen(false)
      setSelectedEmoji(null)
      list.invalidate()
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
      list.invalidate()
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
      list.invalidate()
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
      setBatchDeleteDialogOpen(false)
      list.invalidate()
    },
  })

  // 批量删除
  const handleBatchDelete = () => {
    batchDeleteMutation.mutate(Array.from(list.selectedIds))
  }

  // 页面跳转
  const handleJumpToPage = () => {
    const targetPage = parseInt(jumpToPage)
    if (targetPage >= 1 && targetPage <= list.totalPages) {
      list.goToPage(targetPage)
      setJumpToPage('')
    } else {
      toast({
        title: '无效的页码',
        description: `请输入1-${list.totalPages}之间的页码`,
        variant: 'destructive',
      })
    }
  }

  // 获取格式选项
  const formatOptions = stats?.formats ? Object.keys(stats.formats) : []

  return (
    <div className="h-[calc(100vh-4rem)] flex flex-col p-4 sm:p-6">
      <ScrollArea className="flex-1">
        <div className="space-y-4 sm:space-y-6 pr-4">
          {/* 状态切换 */}
          {stats && (
            <Tabs
              value={list.filters.status === 'all' ? 'adopted' : list.filters.status}
              onValueChange={(value) => list.setFilter('status', value as EmojiStatus)}
            >
              <DashboardTabBar variant="grid" className="h-10 grid-cols-2 sm:grid-cols-4">
                {[
                  { value: 'known' as const, label: '认识', count: stats.known, className: 'text-sky-600' },
                  { value: 'unknown' as const, label: '不认识', count: stats.unknown, className: 'text-gray-600' },
                  { value: 'adopted' as const, label: '据为己用', count: stats.adopted, className: 'text-green-600' },
                  { value: 'discarded' as const, label: '丢弃', count: stats.discarded, className: 'text-red-600' },
                ].map((item) => (
                  <DashboardTabTrigger key={item.value} value={item.value} className="h-10 gap-2">
                    <span>{item.label}</span>
                    <span className={`font-semibold leading-none ${item.className}`}>
                      {item.count}
                    </span>
                  </DashboardTabTrigger>
                ))}
              </DashboardTabBar>
            </Tabs>
          )}

          {/* 筛选和排序 */}
          <Card>
            <CardContent className="space-y-4 pt-6">
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                <div className="space-y-2">
                  <Label htmlFor="emoji-search">搜索 tag</Label>
                  <div className="relative">
                    <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
                    <Input
                      id="emoji-search"
                      value={list.searchInput}
                      onChange={(event) =>
                        list.setSearchInput(event.target.value)
                      }
                      placeholder="搜索 tag、描述或哈希..."
                      className="pr-9 pl-8"
                    />
                    {list.searchInput && (
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon"
                        className="absolute right-1 top-1 h-7 w-7"
                        onClick={() => list.setSearchInput('')}
                        aria-label="清空搜索"
                      >
                        <X className="h-4 w-4" />
                      </Button>
                    )}
                  </div>
                </div>

                <div className="space-y-2">
                  <Label>排序方式</Label>
                  <Select
                    value={`${list.filters.sortBy}-${list.filters.sortOrder}`}
                    onValueChange={(value) => {
                      const [newSortBy, newSortOrder] = value.split('-')
                      list.setFilter('sortBy', newSortBy)
                      list.setFilter('sortOrder', newSortOrder as 'desc' | 'asc')
                    }}
                  >
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="usage_count-desc">
                        使用次数 (多→少)
                      </SelectItem>
                      <SelectItem value="usage_count-asc">
                        使用次数 (少→多)
                      </SelectItem>
                      <SelectItem value="register_time-desc">
                        注册时间 (新→旧)
                      </SelectItem>
                      <SelectItem value="register_time-asc">
                        注册时间 (旧→新)
                      </SelectItem>
                      <SelectItem value="record_time-desc">
                        记录时间 (新→旧)
                      </SelectItem>
                      <SelectItem value="record_time-asc">
                        记录时间 (旧→新)
                      </SelectItem>
                      <SelectItem value="last_used_time-desc">
                        最后使用 (新→旧)
                      </SelectItem>
                      <SelectItem value="last_used_time-asc">
                        最后使用 (旧→新)
                      </SelectItem>
                    </SelectContent>
                  </Select>
                </div>

                <div className="space-y-2">
                  <Label>格式</Label>
                  <Select
                    value={list.filters.format}
                    onValueChange={(value) => list.setFilter('format', value)}
                  >
                    <SelectTrigger>
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
              </div>

              <div className="flex flex-col gap-3 border-t pt-4 sm:flex-row sm:items-center sm:justify-between">
                <div className="flex flex-wrap items-center gap-3">
                  {list.selectedCount > 0 && (
                    <span className="text-sm text-muted-foreground">
                      已选择 {list.selectedCount} 个表情包
                    </span>
                  )}
                  <div className="flex items-center gap-2">
                    <div className="flex h-9 items-center gap-1 border-2 px-1.5">
                      {[
                        { value: 'small' as const, label: '小', sizeClassName: 'h-3 w-3' },
                        { value: 'medium' as const, label: '中', sizeClassName: 'h-4 w-4' },
                        { value: 'large' as const, label: '大', sizeClassName: 'h-5 w-5' },
                      ].map((option) => (
                        <button
                          key={option.value}
                          type="button"
                          onClick={() => setCardSize(option.value)}
                          className={`flex h-7 w-7 items-center justify-center transition-colors ${
                            cardSize === option.value
                              ? 'bg-primary text-primary-foreground'
                              : 'text-muted-foreground hover:bg-muted hover:text-foreground'
                          }`}
                          aria-label={`${option.label}卡片`}
                          title={`${option.label}卡片`}
                        >
                          <span className={`${option.sizeClassName} bg-current`} />
                        </button>
                      ))}
                    </div>
                  </div>

                  <Button
                    variant="outline"
                    size="icon"
                    onClick={() => list.refetch()}
                    disabled={list.isFetching}
                    aria-label="刷新"
                    title="刷新"
                  >
                    <RefreshCw
                      className={`h-4 w-4 ${list.isFetching ? 'animate-spin' : ''}`}
                    />
                  </Button>

                  <Button
                    size="sm"
                    onClick={() => setUploadDialogOpen(true)}
                    className="gap-2"
                  >
                    <Plus className="h-4 w-4" />
                    新增
                  </Button>

                  {list.selectedCount > 0 && (
                    <>
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => list.clearSelection()}
                      >
                        取消选择
                      </Button>
                      <Button
                        variant="destructive"
                        size="sm"
                        onClick={() => setBatchDeleteDialogOpen(true)}
                      >
                        <Trash2 className="h-4 w-4 mr-1" />
                        批量删除
                      </Button>
                    </>
                  )}
                </div>

                <div className="flex items-center gap-2 sm:ml-auto">
                  <Label
                    htmlFor="emoji-page-size"
                    className="text-sm whitespace-nowrap"
                  >
                    每页显示
                  </Label>
                  <Select
                    value={pageSize.toString()}
                    onValueChange={(value) => list.setPageSize(parseInt(value))}
                  >
                    <SelectTrigger id="emoji-page-size" className="w-20">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="20">20</SelectItem>
                      <SelectItem value="40">40</SelectItem>
                      <SelectItem value="60">60</SelectItem>
                      <SelectItem value="100">100</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
              </div>
            </CardContent>
          </Card>

          {/* 表情包卡片列表 */}
          <Card>
            <CardHeader className="pb-3">
              <CardDescription>
                {searchKeyword
                  ? `搜索“${searchKeyword}”命中 ${total} 个表情包,当前第 ${page} 页`
                  : `共 ${total} 个表情包,当前第 ${page} 页`}
              </CardDescription>
            </CardHeader>
            <CardContent>
              {list.isError ? (
                <div className="text-center py-12 space-y-2">
                  <p className="text-sm text-destructive">{list.error?.message}</p>
                  <Button variant="outline" size="sm" onClick={() => list.refetch()}>
                    重试
                  </Button>
                </div>
              ) : (
                <EmojiList
                  emojiList={emojiList}
                  loading={loading}
                  total={total}
                  page={page}
                  pageSize={pageSize}
                  selectedIds={list.selectedIds}
                  cardSize={cardSize}
                  jumpToPage={jumpToPage}
                  onPageChange={list.goToPage}
                  onJumpToPage={handleJumpToPage}
                  onJumpToPageChange={setJumpToPage}
                  onToggleSelect={list.toggle}
                  onEdit={handleEdit}
                  onViewDetail={handleViewDetail}
                  onRegister={handleRegister}
                  onBan={handleBan}
                  onDelete={handleDelete}
                />
              )}
            </CardContent>
          </Card>

          {/* 详情对话框 */}
          <EmojiDetailDialog
            emoji={selectedEmoji}
            open={detailDialogOpen}
            onOpenChange={setDetailDialogOpen}
          />

          {/* 编辑对话框 */}
          <EmojiEditDialog
            emoji={selectedEmoji}
            open={editDialogOpen}
            onOpenChange={setEditDialogOpen}
            onSuccess={() => list.invalidate()}
          />

          {/* 上传对话框 */}
          <EmojiUploadDialog
            open={uploadDialogOpen}
            onOpenChange={setUploadDialogOpen}
            onSuccess={() => list.invalidate()}
          />
        </div>
      </ScrollArea>

      {/* 批量删除确认对话框 */}
      <AlertDialog
        open={batchDeleteDialogOpen}
        onOpenChange={setBatchDeleteDialogOpen}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>确认批量删除</AlertDialogTitle>
            <AlertDialogDescription>
              你确定要删除选中的 {list.selectedCount}{' '}
              个表情包吗?此操作不可撤销。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction onClick={handleBatchDelete}>
              确认删除
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* 删除确认对话框 */}
      <Dialog open={deleteDialogOpen} onOpenChange={setDeleteDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>确认删除</DialogTitle>
            <DialogDescription>
              确定要删除这个表情包吗?此操作无法撤销。
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setDeleteDialogOpen(false)}
            >
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
