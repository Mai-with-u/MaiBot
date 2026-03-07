import { useCallback, useEffect, useState } from 'react'
import { Filter, RefreshCw, Trash2, Upload } from 'lucide-react'

import { Button } from '@/components/ui/button'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
// import { Input } from '@/components/ui/input'
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

import { useToast } from '@/hooks/use-toast'
import {
  banEmoji,
  batchDeleteEmojis,
  deleteEmoji,
  getEmojiList,
  getEmojiStats,
  registerEmoji,
} from '@/lib/emoji-api'
import type { Emoji, EmojiStats } from '@/types/emoji'

import {
  EmojiDetailDialog,
  EmojiEditDialog,
  EmojiUploadDialog,
} from './EmojiDialogs'
import { EmojiList } from './EmojiList'

export function EmojiManagementPage() {
  const [emojiList, setEmojiList] = useState<Emoji[]>([])
  const [stats, setStats] = useState<EmojiStats | null>(null)
  const [loading, setLoading] = useState(false)
  const [page, setPage] = useState(1)
  const [total, setTotal] = useState(0)
  const [pageSize, setPageSize] = useState(20)
  const [registeredFilter, setRegisteredFilter] = useState<string>('all')
  const [bannedFilter, setBannedFilter] = useState<string>('all')
  const [formatFilter, setFormatFilter] = useState<string>('all')
  const [sortBy, setSortBy] = useState<string>('usage_count')
  const [sortOrder, setSortOrder] = useState<'desc' | 'asc'>('desc')
  const [selectedEmoji, setSelectedEmoji] = useState<Emoji | null>(null)
  const [detailDialogOpen, setDetailDialogOpen] = useState(false)
  const [editDialogOpen, setEditDialogOpen] = useState(false)
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false)
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set())
  const [batchDeleteDialogOpen, setBatchDeleteDialogOpen] = useState(false)
  const [jumpToPage, setJumpToPage] = useState('')
  const [cardSize, setCardSize] = useState<'small' | 'medium' | 'large'>(
    'medium'
  )
  const [uploadDialogOpen, setUploadDialogOpen] = useState(false)

  const { toast } = useToast()

  // 加载表情包列表
  const loadEmojiList = useCallback(async () => {
    try {
      setLoading(true)
      const response = await getEmojiList({
        page,
        page_size: pageSize,
        is_registered:
          registeredFilter === 'all'
            ? undefined
            : registeredFilter === 'registered',
        is_banned:
          bannedFilter === 'all' ? undefined : bannedFilter === 'banned',
        format: formatFilter === 'all' ? undefined : formatFilter,
        sort_by: sortBy,
        sort_order: sortOrder,
      })
      setEmojiList(response.data)
      setTotal(response.total)
    } catch (error) {
      const message =
        error instanceof Error ? error.message : '加载表情包列表失败'
      toast({
        title: '错误',
        description: message,
        variant: 'destructive',
      })
    } finally {
      setLoading(false)
    }
  }, [
    page,
    pageSize,
    registeredFilter,
    bannedFilter,
    formatFilter,
    sortBy,
    sortOrder,
    toast,
  ])

  // 加载统计数据
  const loadStats = async () => {
    try {
      const response = await getEmojiStats()
      setStats(response.data)
    } catch (error) {
      console.error('加载统计数据失败:', error)
    }
  }

  useEffect(() => {
    loadEmojiList()
  }, [loadEmojiList])

  useEffect(() => {
    loadStats()
  }, [])

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

  // 确认删除
  const confirmDelete = async () => {
    if (!selectedEmoji) return

    try {
      await deleteEmoji(selectedEmoji.id)
      toast({
        title: '成功',
        description: '表情包已删除',
      })
      setDeleteDialogOpen(false)
      setSelectedEmoji(null)
      loadEmojiList()
      loadStats()
    } catch (error) {
      const message = error instanceof Error ? error.message : '删除失败'
      toast({
        title: '错误',
        description: message,
        variant: 'destructive',
      })
    }
  }

  // 快速注册
  const handleRegister = async (emoji: Emoji) => {
    try {
      await registerEmoji(emoji.id)
      toast({
        title: '成功',
        description: '表情包已注册',
      })
      loadEmojiList()
      loadStats()
    } catch (error) {
      const message = error instanceof Error ? error.message : '注册失败'
      toast({
        title: '错误',
        description: message,
        variant: 'destructive',
      })
    }
  }

  // 快速封禁
  const handleBan = async (emoji: Emoji) => {
    try {
      await banEmoji(emoji.id)
      toast({
        title: '成功',
        description: '表情包已封禁',
      })
      loadEmojiList()
      loadStats()
    } catch (error) {
      const message = error instanceof Error ? error.message : '封禁失败'
      toast({
        title: '错误',
        description: message,
        variant: 'destructive',
      })
    }
  }

  // 切换选择
  const toggleSelect = (id: number) => {
    const newSelected = new Set(selectedIds)
    if (newSelected.has(id)) {
      newSelected.delete(id)
    } else {
      newSelected.add(id)
    }
    setSelectedIds(newSelected)
  }

  // 批量删除
  const handleBatchDelete = async () => {
    try {
      const result = await batchDeleteEmojis(Array.from(selectedIds))
      toast({
        title: '批量删除完成',
        description: result.message,
      })
      setSelectedIds(new Set())
      setBatchDeleteDialogOpen(false)
      loadEmojiList()
      loadStats()
    } catch (error) {
      toast({
        title: '批量删除失败',
        description:
          error instanceof Error ? error.message : '批量删除失败',
        variant: 'destructive',
      })
    }
  }

  // 页面跳转
  const handleJumpToPage = () => {
    const targetPage = parseInt(jumpToPage)
    const totalPages = Math.ceil(total / pageSize)
    if (targetPage >= 1 && targetPage <= totalPages) {
      setPage(targetPage)
      setJumpToPage('')
    } else {
      toast({
        title: '无效的页码',
        description: `请输入1-${totalPages}之间的页码`,
        variant: 'destructive',
      })
    }
  }

  // 获取格式选项
  const formatOptions = stats?.formats ? Object.keys(stats.formats) : []

  return (
    <div className="h-[calc(100vh-4rem)] flex flex-col p-4 sm:p-6">
      {/* 页面标题 */}
      <div className="mb-4 sm:mb-6 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <h1 className="text-2xl sm:text-3xl font-bold">表情包管理</h1>
          <p className="text-sm text-muted-foreground mt-1">
            管理麦麦的表情包资源
          </p>
        </div>
        <Button
          onClick={() => setUploadDialogOpen(true)}
          className="gap-2"
        >
          <Upload className="h-4 w-4" />
          上传表情包
        </Button>
      </div>

      <ScrollArea className="flex-1">
        <div className="space-y-4 sm:space-y-6 pr-4">
          {/* 统计卡片 */}
          {stats && (
            <div className="grid gap-4 grid-cols-2 lg:grid-cols-4">
              <Card>
                <CardHeader className="pb-2">
                  <CardDescription>总数</CardDescription>
                  <CardTitle className="text-2xl">{stats.total}</CardTitle>
                </CardHeader>
              </Card>
              <Card>
                <CardHeader className="pb-2">
                  <CardDescription>已注册</CardDescription>
                  <CardTitle className="text-2xl text-green-600">
                    {stats.registered}
                  </CardTitle>
                </CardHeader>
              </Card>
              <Card>
                <CardHeader className="pb-2">
                  <CardDescription>已封禁</CardDescription>
                  <CardTitle className="text-2xl text-red-600">
                    {stats.banned}
                  </CardTitle>
                </CardHeader>
              </Card>
              <Card>
                <CardHeader className="pb-2">
                  <CardDescription>未注册</CardDescription>
                  <CardTitle className="text-2xl text-gray-600">
                    {stats.unregistered}
                  </CardTitle>
                </CardHeader>
              </Card>
            </div>
          )}

          {/* 筛选和排序 */}
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Filter className="h-5 w-5" />
                筛选和排序
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                <div className="space-y-2">
                  <Label>排序方式</Label>
                  <Select
                    value={`${sortBy}-${sortOrder}`}
                    onValueChange={(value) => {
                      const [newSortBy, newSortOrder] = value.split('-')
                      setSortBy(newSortBy)
                      setSortOrder(newSortOrder as 'desc' | 'asc')
                      setPage(1)
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
                  <Label>注册状态</Label>
                  <Select
                    value={registeredFilter}
                    onValueChange={(value) => {
                      setRegisteredFilter(value)
                      setPage(1)
                    }}
                  >
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="all">全部</SelectItem>
                      <SelectItem value="registered">已注册</SelectItem>
                      <SelectItem value="unregistered">未注册</SelectItem>
                    </SelectContent>
                  </Select>
                </div>

                <div className="space-y-2">
                  <Label>封禁状态</Label>
                  <Select
                    value={bannedFilter}
                    onValueChange={(value) => {
                      setBannedFilter(value)
                      setPage(1)
                    }}
                  >
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="all">全部</SelectItem>
                      <SelectItem value="banned">已封禁</SelectItem>
                      <SelectItem value="unbanned">未封禁</SelectItem>
                    </SelectContent>
                  </Select>
                </div>

                <div className="space-y-2">
                  <Label>格式</Label>
                  <Select
                    value={formatFilter}
                    onValueChange={(value) => {
                      setFormatFilter(value)
                      setPage(1)
                    }}
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

              <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 pt-4 border-t">
                <div className="flex items-center gap-4">
                  {selectedIds.size > 0 && (
                    <span className="text-sm text-muted-foreground">
                      已选择 {selectedIds.size} 个表情包
                    </span>
                  )}
                  {/* 卡片尺寸切换 */}
                  <div className="flex items-center gap-2">
                    <Label className="text-sm whitespace-nowrap">
                      卡片大小
                    </Label>
                    <Select
                      value={cardSize}
                      onValueChange={(
                        value: 'small' | 'medium' | 'large'
                      ) => setCardSize(value)}
                    >
                      <SelectTrigger className="w-24">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="small">小</SelectItem>
                        <SelectItem value="medium">中</SelectItem>
                        <SelectItem value="large">大</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <Label
                    htmlFor="emoji-page-size"
                    className="text-sm whitespace-nowrap"
                  >
                    每页显示
                  </Label>
                  <Select
                    value={pageSize.toString()}
                    onValueChange={(value) => {
                      setPageSize(parseInt(value))
                      setPage(1)
                      setSelectedIds(new Set())
                    }}
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
                  {selectedIds.size > 0 && (
                    <>
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => setSelectedIds(new Set())}
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
              </div>

              <div className="flex justify-end pt-4 border-t">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={loadEmojiList}
                  disabled={loading}
                >
                  <RefreshCw
                    className={`h-4 w-4 mr-2 ${loading ? 'animate-spin' : ''}`}
                  />
                  刷新
                </Button>
              </div>
            </CardContent>
          </Card>

          {/* 表情包卡片列表 */}
          <Card>
            <CardHeader>
              <CardTitle>表情包列表</CardTitle>
              <CardDescription>
                共 {total} 个表情包,当前第 {page} 页
              </CardDescription>
            </CardHeader>
            <CardContent>
              <EmojiList
                emojiList={emojiList}
                loading={loading}
                total={total}
                page={page}
                pageSize={pageSize}
                selectedIds={selectedIds}
                cardSize={cardSize}
                jumpToPage={jumpToPage}
                onPageChange={setPage}
                onJumpToPage={handleJumpToPage}
                onJumpToPageChange={setJumpToPage}
                onToggleSelect={toggleSelect}
                onEdit={handleEdit}
                onViewDetail={handleViewDetail}
                onRegister={handleRegister}
                onBan={handleBan}
                onDelete={handleDelete}
              />
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
            onSuccess={() => {
              loadEmojiList()
              loadStats()
            }}
          />

          {/* 上传对话框 */}
          <EmojiUploadDialog
            open={uploadDialogOpen}
            onOpenChange={setUploadDialogOpen}
            onSuccess={() => {
              loadEmojiList()
              loadStats()
            }}
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
              你确定要删除选中的 {selectedIds.size}{' '}
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
