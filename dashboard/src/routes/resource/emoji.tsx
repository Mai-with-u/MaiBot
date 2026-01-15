import { useState, useEffect, useCallback, useMemo } from 'react'
import {
  Filter,
  RefreshCw,
  Trash2,
  Edit,
  Info,
  ChevronLeft,
  ChevronRight,
  ChevronsLeft,
  ChevronsRight,
  CheckCircle2,
  Ban,
  Upload,
  ArrowLeft,
  Check,
  X,
  ImageIcon,
} from 'lucide-react'
import Uppy from '@uppy/core'
import Dashboard from '@uppy/react/dashboard'
import '@uppy/core/css/style.min.css'
import '@uppy/dashboard/css/style.min.css'
import '@/styles/uppy-custom.css'
import { fetchWithAuth } from '@/lib/fetch-with-auth'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { EmojiThumbnail } from '@/components/emoji-thumbnail'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
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
import { Badge } from '@/components/ui/badge'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { Checkbox } from '@/components/ui/checkbox'
import { ScrollArea } from '@/components/ui/scroll-area'
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

import { Markdown } from '@/components/ui/markdown'
import { useToast } from '@/hooks/use-toast'
import type { Emoji, EmojiStats } from '@/types/emoji'
import {
  getEmojiList,
  getEmojiDetail,
  getEmojiStats,
  updateEmoji,
  deleteEmoji,
  registerEmoji,
  banEmoji,
  getEmojiThumbnailUrl,
  getEmojiOriginalUrl,
  batchDeleteEmojis,
  getEmojiUploadUrl,
} from '@/lib/emoji-api'

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
  const [cardSize, setCardSize] = useState<'small' | 'medium' | 'large'>('medium')
  const [uploadDialogOpen, setUploadDialogOpen] = useState(false)

  const { toast } = useToast()

  // 加载表情包列表
  const loadEmojiList = useCallback(async () => {
    try {
      setLoading(true)
      const response = await getEmojiList({
        page,
        page_size: pageSize,
        is_registered: registeredFilter === 'all' ? undefined : registeredFilter === 'registered',
        is_banned: bannedFilter === 'all' ? undefined : bannedFilter === 'banned',
        format: formatFilter === 'all' ? undefined : formatFilter,
        sort_by: sortBy,
        sort_order: sortOrder,
      })
      setEmojiList(response.data)
      setTotal(response.total)
    } catch (error) {
      const message = error instanceof Error ? error.message : '加载表情包列表失败'
      toast({
        title: '错误',
        description: message,
        variant: 'destructive',
      })
    } finally {
      setLoading(false)
    }
  }, [page, pageSize, registeredFilter, bannedFilter, formatFilter, sortBy, sortOrder, toast])

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
    try {
      const response = await getEmojiDetail(emoji.id)
      setSelectedEmoji(response.data)
      setDetailDialogOpen(true)
    } catch (error) {
      const message = error instanceof Error ? error.message : '加载详情失败'
      toast({
        title: '错误',
        description: message,
        variant: 'destructive',
      })
    }
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
        description: error instanceof Error ? error.message : '批量删除失败',
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
        <Button onClick={() => setUploadDialogOpen(true)} className="gap-2">
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
                  <SelectItem value="usage_count-desc">使用次数 (多→少)</SelectItem>
                  <SelectItem value="usage_count-asc">使用次数 (少→多)</SelectItem>
                  <SelectItem value="register_time-desc">注册时间 (新→旧)</SelectItem>
                  <SelectItem value="register_time-asc">注册时间 (旧→新)</SelectItem>
                  <SelectItem value="record_time-desc">记录时间 (新→旧)</SelectItem>
                  <SelectItem value="record_time-asc">记录时间 (旧→新)</SelectItem>
                  <SelectItem value="last_used_time-desc">最后使用 (新→旧)</SelectItem>
                  <SelectItem value="last_used_time-asc">最后使用 (旧→新)</SelectItem>
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
                <span className="text-sm text-muted-foreground">已选择 {selectedIds.size} 个表情包</span>
              )}
              {/* 卡片尺寸切换 */}
              <div className="flex items-center gap-2">
                <Label className="text-sm whitespace-nowrap">卡片大小</Label>
                <Select
                  value={cardSize}
                  onValueChange={(value: 'small' | 'medium' | 'large') => setCardSize(value)}
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
              <Label htmlFor="emoji-page-size" className="text-sm whitespace-nowrap">每页显示</Label>
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
              <RefreshCw className={`h-4 w-4 mr-2 ${loading ? 'animate-spin' : ''}`} />
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
            共 {total} 个表情包，当前第 {page} 页
          </CardDescription>
        </CardHeader>
        <CardContent>
          {/* 卡片网格视图 */}
          {emojiList.length === 0 ? (
            <div className="text-center py-12 text-muted-foreground">
              暂无数据
            </div>
          ) : (
            <div className={`grid gap-3 ${
              cardSize === 'small' 
                ? 'grid-cols-3 sm:grid-cols-4 md:grid-cols-6 lg:grid-cols-8 xl:grid-cols-10' 
                : cardSize === 'medium'
                ? 'grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6 xl:grid-cols-8'
                : 'grid-cols-2 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5'
            }`}>
              {emojiList.map((emoji) => (
                <div 
                  key={emoji.id} 
                  className={`group relative rounded-lg border bg-card overflow-hidden hover:ring-2 hover:ring-primary transition-all cursor-pointer ${
                    selectedIds.has(emoji.id) ? 'ring-2 ring-primary bg-primary/5' : ''
                  }`}
                  onClick={() => toggleSelect(emoji.id)}
                >
                  {/* 选中指示器 */}
                  <div className={`absolute top-1 left-1 z-10 transition-opacity ${
                    selectedIds.has(emoji.id) ? 'opacity-100' : 'opacity-0 group-hover:opacity-100'
                  }`}>
                    <div className={`w-5 h-5 rounded-full border-2 flex items-center justify-center ${
                      selectedIds.has(emoji.id) 
                        ? 'bg-primary border-primary text-primary-foreground' 
                        : 'bg-background/80 border-muted-foreground/50'
                    }`}>
                      {selectedIds.has(emoji.id) && <CheckCircle2 className="h-3 w-3" />}
                    </div>
                  </div>

                  {/* 状态标签 */}
                  <div className="absolute top-1 right-1 z-10 flex flex-col gap-0.5">
                    {emoji.is_registered && (
                      <Badge variant="default" className="bg-green-600 text-[10px] px-1 py-0">
                        已注册
                      </Badge>
                    )}
                    {emoji.is_banned && (
                      <Badge variant="destructive" className="text-[10px] px-1 py-0">
                        已封禁
                      </Badge>
                    )}
                  </div>

                  {/* 图片 */}
                  <div className={`aspect-square bg-muted flex items-center justify-center overflow-hidden ${
                    cardSize === 'small' ? 'p-1' : cardSize === 'medium' ? 'p-2' : 'p-3'
                  }`}>
                    <EmojiThumbnail
                      src={getEmojiThumbnailUrl(emoji.id)}
                      alt="表情包"
                    />
                  </div>

                  {/* 底部信息和操作 */}
                  <div className={`border-t bg-card ${cardSize === 'small' ? 'p-1' : 'p-2'}`}>
                    {/* 使用次数和格式 */}
                    <div className="flex items-center justify-between gap-1 text-xs text-muted-foreground mb-1">
                      <Badge variant="outline" className="text-[10px] px-1 py-0">
                        {emoji.format.toUpperCase()}
                      </Badge>
                      <span className="font-mono">{emoji.usage_count}次</span>
                    </div>
                    
                    {/* 操作按钮 - 悬停时显示 */}
                    <div className={`flex gap-1 justify-center opacity-0 group-hover:opacity-100 transition-opacity ${
                      cardSize === 'small' ? 'flex-wrap' : ''
                    }`}>
                      <Button
                        variant="ghost"
                        size="icon"
                        className="h-6 w-6"
                        onClick={(e) => {
                          e.stopPropagation()
                          handleEdit(emoji)
                        }}
                        title="编辑"
                      >
                        <Edit className="h-3 w-3" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon"
                        className="h-6 w-6"
                        onClick={(e) => {
                          e.stopPropagation()
                          handleViewDetail(emoji)
                        }}
                        title="详情"
                      >
                        <Info className="h-3 w-3" />
                      </Button>
                      {!emoji.is_registered && (
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-6 w-6 text-green-600 hover:text-green-700"
                          onClick={(e) => {
                            e.stopPropagation()
                            handleRegister(emoji)
                          }}
                          title="注册"
                        >
                          <CheckCircle2 className="h-3 w-3" />
                        </Button>
                      )}
                      {!emoji.is_banned && (
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-6 w-6 text-orange-600 hover:text-orange-700"
                          onClick={(e) => {
                            e.stopPropagation()
                            handleBan(emoji)
                          }}
                          title="封禁"
                        >
                          <Ban className="h-3 w-3" />
                        </Button>
                      )}
                      <Button
                        variant="ghost"
                        size="icon"
                        className="h-6 w-6 text-red-600 hover:text-red-700"
                        onClick={(e) => {
                          e.stopPropagation()
                          handleDelete(emoji)
                        }}
                        title="删除"
                      >
                        <Trash2 className="h-3 w-3" />
                      </Button>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}

          {/* 分页 */}
          {/* 分页 - 增强版 */}
          {total > 0 && (
            <div className="flex flex-col sm:flex-row items-center justify-between gap-4 mt-4">
              <div className="text-sm text-muted-foreground">
                显示 {(page - 1) * pageSize + 1} 到{' '}
                {Math.min(page * pageSize, total)} 条，共 {total} 条
              </div>
              <div className="flex items-center gap-2">
                {/* 首页 */}
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setPage(1)}
                  disabled={page === 1}
                  className="hidden sm:flex"
                >
                  <ChevronsLeft className="h-4 w-4" />
                </Button>
                
                {/* 上一页 */}
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setPage((p) => Math.max(1, p - 1))}
                  disabled={page === 1}
                >
                  <ChevronLeft className="h-4 w-4 sm:mr-1" />
                  <span className="hidden sm:inline">上一页</span>
                </Button>

                {/* 页码跳转 */}
                <div className="flex items-center gap-2">
                  <Input
                    type="number"
                    value={jumpToPage}
                    onChange={(e) => setJumpToPage(e.target.value)}
                    onKeyDown={(e) => e.key === 'Enter' && handleJumpToPage()}
                    placeholder={page.toString()}
                    className="w-16 h-8 text-center"
                    min={1}
                    max={Math.ceil(total / pageSize)}
                  />
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={handleJumpToPage}
                    disabled={!jumpToPage}
                    className="h-8"
                  >
                    跳转
                  </Button>
                </div>
                
                {/* 下一页 */}
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setPage((p) => p + 1)}
                  disabled={page >= Math.ceil(total / pageSize)}
                >
                  <span className="hidden sm:inline">下一页</span>
                  <ChevronRight className="h-4 w-4 sm:ml-1" />
                </Button>

                {/* 末页 */}
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setPage(Math.ceil(total / pageSize))}
                  disabled={page >= Math.ceil(total / pageSize)}
                  className="hidden sm:flex"
                >
                  <ChevronsRight className="h-4 w-4" />
                </Button>
              </div>
            </div>
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
      <AlertDialog open={batchDeleteDialogOpen} onOpenChange={setBatchDeleteDialogOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>确认批量删除</AlertDialogTitle>
            <AlertDialogDescription>
              你确定要删除选中的 {selectedIds.size} 个表情包吗？此操作不可撤销。
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
              确定要删除这个表情包吗？此操作无法撤销。
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

// 详情对话框组件
function EmojiDetailDialog({
  emoji,
  open,
  onOpenChange,
}: {
  emoji: Emoji | null
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  if (!emoji) return null

  const formatTime = (timestamp: number | null) => {
    if (!timestamp) return '-'
    return new Date(timestamp * 1000).toLocaleString('zh-CN')
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl max-h-[90vh]">
        <DialogHeader>
          <DialogTitle>表情包详情</DialogTitle>
        </DialogHeader>
        <ScrollArea className="max-h-[calc(90vh-8rem)] pr-4">
          <div className="space-y-4">
          {/* 表情包预览图 - 使用原图 */}
          <div className="flex justify-center">
            <div className="w-32 h-32 bg-muted rounded-lg flex items-center justify-center overflow-hidden">
              <img
                src={getEmojiOriginalUrl(emoji.id)}
                alt={emoji.description || '表情包'}
                className="w-full h-full object-cover"
                onError={(e) => {
                  const target = e.target as HTMLImageElement
                  target.style.display = 'none'
                  const parent = target.parentElement
                  if (parent) {
                    parent.innerHTML = '<svg class="h-16 w-16 text-muted-foreground" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z" /></svg>'
                  }
                }}
              />
            </div>
          </div>

          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <Label className="text-muted-foreground">ID</Label>
              <div className="mt-1 font-mono">{emoji.id}</div>
            </div>
            <div>
              <Label className="text-muted-foreground">格式</Label>
              <div className="mt-1">
                <Badge variant="outline">{emoji.format.toUpperCase()}</Badge>
              </div>
            </div>
          </div>

          <div>
            <Label className="text-muted-foreground">文件路径</Label>
            <div className="mt-1 font-mono text-sm break-all bg-muted p-2 rounded">
              {emoji.full_path}
            </div>
          </div>

          <div>
            <Label className="text-muted-foreground">哈希值</Label>
            <div className="mt-1 font-mono text-sm break-all bg-muted p-2 rounded">
              {emoji.emoji_hash}
            </div>
          </div>

          <div>
            <Label className="text-muted-foreground">描述</Label>
            {emoji.description ? (
              <div className="mt-1 rounded-lg border bg-muted/50 p-3">
                <Markdown className="prose-sm">{emoji.description}</Markdown>
              </div>
            ) : (
              <div className="mt-1 text-sm text-muted-foreground">-</div>
            )}
          </div>

          <div>
            <Label className="text-muted-foreground">情绪</Label>
            <div className="mt-1">
              {emoji.emotion ? (
                <span className="text-sm">{emoji.emotion}</span>
              ) : (
                <span className="text-sm text-muted-foreground">-</span>
              )}
            </div>
          </div>

          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <Label className="text-muted-foreground">状态</Label>
              <div className="mt-2 flex gap-2">
                {emoji.is_registered && (
                  <Badge variant="default" className="bg-green-600">
                    已注册
                  </Badge>
                )}
                {emoji.is_banned && (
                  <Badge variant="destructive">已封禁</Badge>
                )}
                {!emoji.is_registered && !emoji.is_banned && (
                  <Badge variant="outline">未注册</Badge>
                )}
              </div>
            </div>
            <div>
              <Label className="text-muted-foreground">使用次数</Label>
              <div className="mt-1 font-mono text-lg">{emoji.usage_count}</div>
            </div>
          </div>

          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <Label className="text-muted-foreground">记录时间</Label>
              <div className="mt-1 text-sm">{formatTime(emoji.record_time)}</div>
            </div>
            <div>
              <Label className="text-muted-foreground">注册时间</Label>
              <div className="mt-1 text-sm">{formatTime(emoji.register_time)}</div>
            </div>
          </div>

          <div>
            <Label className="text-muted-foreground">最后使用</Label>
            <div className="mt-1 text-sm">{formatTime(emoji.last_used_time)}</div>
          </div>
        </div>
        </ScrollArea>
      </DialogContent>
    </Dialog>
  )
}

// 编辑对话框组件
function EmojiEditDialog({
  emoji,
  open,
  onOpenChange,
  onSuccess,
}: {
  emoji: Emoji | null
  open: boolean
  onOpenChange: (open: boolean) => void
  onSuccess: () => void
}) {
  const [emotionInput, setEmotionInput] = useState('')
  const [isRegistered, setIsRegistered] = useState(false)
  const [isBanned, setIsBanned] = useState(false)
  const [saving, setSaving] = useState(false)

  const { toast } = useToast()

  useEffect(() => {
    if (emoji) {
      setEmotionInput(emoji.emotion || '')
      setIsRegistered(emoji.is_registered)
      setIsBanned(emoji.is_banned)
    }
  }, [emoji])

  const handleSave = async () => {
    if (!emoji) return

    try {
      setSaving(true)
      // 将输入的标签字符串标准化为逗号分隔格式
      const emotionString = emotionInput
        .split(/[,,]/)
        .map((s) => s.trim())
        .filter(Boolean)
        .join(',')

      await updateEmoji(emoji.id, {
        emotion: emotionString || undefined,
        is_registered: isRegistered,
        is_banned: isBanned,
      })

      toast({
        title: '成功',
        description: '表情包信息已更新',
      })
      onOpenChange(false)
      onSuccess()
    } catch (error) {
      const message = error instanceof Error ? error.message : '保存失败'
      toast({
        title: '错误',
        description: message,
        variant: 'destructive',
      })
    } finally {
      setSaving(false)
    }
  }

  if (!emoji) return null

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>编辑表情包</DialogTitle>
          <DialogDescription>修改表情包的情绪和状态信息</DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <div>
            <Label>情绪</Label>
            <Textarea
              value={emotionInput}
              onChange={(e) => setEmotionInput(e.target.value)}
              placeholder="输入情绪描述..."
              rows={2}
              className="mt-1"
            />
            <p className="text-xs text-muted-foreground mt-1">
              输入情绪相关的文本描述
            </p>
          </div>

          <div className="grid gap-4 sm:grid-cols-2">
            <div className="flex items-center space-x-2">
              <Checkbox
                id="is_registered"
                checked={isRegistered}
                onCheckedChange={(checked) => {
                  if (checked === true) {
                    setIsRegistered(true)
                    setIsBanned(false) // 注册时自动取消封禁
                  } else {
                    setIsRegistered(false)
                  }
                }}
              />
              <Label htmlFor="is_registered" className="cursor-pointer">
                已注册
              </Label>
            </div>

            <div className="flex items-center space-x-2">
              <Checkbox
                id="is_banned"
                checked={isBanned}
                onCheckedChange={(checked) => {
                  if (checked === true) {
                    setIsBanned(true)
                    setIsRegistered(false) // 封禁时自动取消注册
                  } else {
                    setIsBanned(false)
                  }
                }}
              />
              <Label htmlFor="is_banned" className="cursor-pointer">
                已封禁
              </Label>
            </div>
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button onClick={handleSave} disabled={saving}>
            {saving ? '保存中...' : '保存'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

// 上传对话框组件
// 上传文件的元数据类型
interface UploadedFileInfo {
  id: string
  name: string
  previewUrl: string
  emotion: string
  description: string
  isRegistered: boolean
  file: File
}

// 上传步骤类型
type UploadStep = 'select' | 'edit-single' | 'edit-multiple'

function EmojiUploadDialog({
  open,
  onOpenChange,
  onSuccess,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  onSuccess: () => void
}) {
  const [step, setStep] = useState<UploadStep>('select')
  const [uploadedFiles, setUploadedFiles] = useState<UploadedFileInfo[]>([])
  const [selectedFileId, setSelectedFileId] = useState<string | null>(null)
  const [uploading, setUploading] = useState(false)
  const { toast } = useToast()

  // 创建 Uppy 实例（仅用于文件选择，不自动上传）
  const uppy = useMemo(() => {
    const uppyInstance = new Uppy({
      id: 'emoji-uploader',
      autoProceed: false,
      restrictions: {
        maxFileSize: 10 * 1024 * 1024, // 10MB
        allowedFileTypes: ['image/jpeg', 'image/png', 'image/gif', 'image/webp'],
        maxNumberOfFiles: 20,
      },
      locale: {
        pluralize: () => 0,
        strings: {
          addMoreFiles: '添加更多文件',
          addingMoreFiles: '正在添加更多文件',
          allowedFileTypes: '允许的文件类型：%{types}',
          cancel: '取消',
          closeModal: '关闭',
          complete: '完成',
          connectedToInternet: '已连接到互联网',
          copyLink: '复制链接',
          copyLinkToClipboardFallback: '复制下方链接',
          copyLinkToClipboardSuccess: '链接已复制到剪贴板',
          dashboardTitle: '选择文件',
          dashboardWindowTitle: '文件选择窗口（按 ESC 关闭）',
          done: '完成',
          dropHereOr: '拖放文件到这里或 %{browse}',
          dropHint: '将文件拖放到此处',
          dropPasteFiles: '将文件拖放到这里或 %{browseFiles}',
          dropPasteFolders: '将文件拖放到这里或 %{browseFolders}',
          dropPasteBoth: '将文件拖放到这里，%{browseFiles} 或 %{browseFolders}',
          dropPasteImportFiles: '将文件拖放到这里，%{browseFiles} 或从以下位置导入：',
          dropPasteImportFolders: '将文件拖放到这里，%{browseFolders} 或从以下位置导入：',
          dropPasteImportBoth: '将文件拖放到这里，%{browseFiles}，%{browseFolders} 或从以下位置导入：',
          editFile: '编辑文件',
          editing: '正在编辑 %{file}',
          emptyFolderAdded: '未从空文件夹添加文件',
          exceedsSize: '%{file} 超过了最大允许大小 %{size}',
          failedToUpload: '上传 %{file} 失败',
          fileSource: '文件来源：%{name}',
          filesUploadedOfTotal: {
            0: '已上传 %{complete} / %{smart_count} 个文件',
            1: '已上传 %{complete} / %{smart_count} 个文件',
          },
          filter: '筛选',
          finishEditingFile: '完成编辑文件',
          folderAdded: {
            0: '已从 %{folder} 添加 %{smart_count} 个文件',
            1: '已从 %{folder} 添加 %{smart_count} 个文件',
          },
          generatingThumbnails: '正在生成缩略图...',
          import: '导入',
          importFiles: '从以下位置导入文件：',
          importFrom: '从 %{name} 导入',
          loading: '加载中...',
          logOut: '登出',
          myDevice: '我的设备',
          noFilesFound: '这里没有文件或文件夹',
          noInternetConnection: '无网络连接',
          openFolderNamed: '打开文件夹 %{name}',
          pause: '暂停',
          pauseUpload: '暂停上传',
          paused: '已暂停',
          poweredBy: '技术支持：%{uppy}',
          processingXFiles: {
            0: '正在处理 %{smart_count} 个文件',
            1: '正在处理 %{smart_count} 个文件',
          },
          recording: '录制中',
          removeFile: '移除文件',
          resetFilter: '重置筛选',
          resume: '继续',
          resumeUpload: '继续上传',
          retry: '重试',
          retryUpload: '重试上传',
          save: '保存',
          saveChanges: '保存更改',
          selectFileNamed: '选择文件 %{name}',
          selectX: {
            0: '选择 %{smart_count}',
            1: '选择 %{smart_count}',
          },
          smile: '笑一个！',
          startRecording: '开始录制视频',
          stopRecording: '停止录制视频',
          takePicture: '拍照',
          timedOut: '上传已停滞 %{seconds} 秒，正在中止。',
          upload: '下一步',
          uploadComplete: '上传完成',
          uploadFailed: '上传失败',
          uploadPaused: '上传已暂停',
          uploadXFiles: {
            0: '下一步（%{smart_count} 个文件）',
            1: '下一步（%{smart_count} 个文件）',
          },
          uploadXNewFiles: {
            0: '下一步（+%{smart_count} 个文件）',
            1: '下一步（+%{smart_count} 个文件）',
          },
          uploading: '正在上传',
          uploadingXFiles: {
            0: '正在上传 %{smart_count} 个文件',
            1: '正在上传 %{smart_count} 个文件',
          },
          xFilesSelected: {
            0: '已选择 %{smart_count} 个文件',
            1: '已选择 %{smart_count} 个文件',
          },
          xMoreFilesAdded: {
            0: '又添加了 %{smart_count} 个文件',
            1: '又添加了 %{smart_count} 个文件',
          },
          xTimeLeft: '剩余 %{time}',
          youCanOnlyUploadFileTypes: '您只能上传：%{types}',
          youCanOnlyUploadX: {
            0: '您只能上传 %{smart_count} 个文件',
            1: '您只能上传 %{smart_count} 个文件',
          },
          youHaveToAtLeastSelectX: {
            0: '您至少需要选择 %{smart_count} 个文件',
            1: '您至少需要选择 %{smart_count} 个文件',
          },
          browseFiles: '浏览文件',
          browseFolders: '浏览文件夹',
          cancelUpload: '取消上传',
          addMore: '添加更多',
          back: '返回',
          editFileWithFilename: '编辑文件 %{file}',
        },
      },
    })
    
    return uppyInstance
  }, [])

  // 处理"下一步"按钮点击 - 进入编辑阶段
  useEffect(() => {
    const handleUpload = () => {
      const files = uppy.getFiles()
      if (files.length === 0) return
      
      // 将选择的文件转换为我们的数据结构
      const fileInfos: UploadedFileInfo[] = files.map((file) => ({
        id: file.id,
        name: file.name,
        previewUrl: file.preview || URL.createObjectURL(file.data as File),
        emotion: '',
        description: '',
        isRegistered: true,
        file: file.data as File,
      }))
      
      setUploadedFiles(fileInfos)
      
      // 根据文件数量决定进入哪个步骤
      if (files.length === 1) {
        setSelectedFileId(fileInfos[0].id)
        setStep('edit-single')
      } else {
        setStep('edit-multiple')
      }
    }

    uppy.on('upload', handleUpload)
    return () => {
      uppy.off('upload', handleUpload)
    }
  }, [uppy])

  // 对话框关闭时重置状态
  useEffect(() => {
    if (!open) {
      uppy.cancelAll()
      setStep('select')
      setUploadedFiles([])
      setSelectedFileId(null)
      setUploading(false)
    }
  }, [open, uppy])

  // 更新单个文件的元数据
  const updateFileInfo = useCallback((fileId: string, updates: Partial<UploadedFileInfo>) => {
    setUploadedFiles(prev => 
      prev.map(f => f.id === fileId ? { ...f, ...updates } : f)
    )
  }, [])

  // 检查文件是否填写完成必填项（情感标签必填）
  const isFileComplete = useCallback((file: UploadedFileInfo) => {
    return file.emotion.trim().length > 0
  }, [])

  // 检查所有文件是否都填写完成
  const allFilesComplete = useMemo(() => {
    return uploadedFiles.length > 0 && uploadedFiles.every(isFileComplete)
  }, [uploadedFiles, isFileComplete])

  // 获取当前选中的文件
  const selectedFile = useMemo(() => {
    return uploadedFiles.find(f => f.id === selectedFileId) || null
  }, [uploadedFiles, selectedFileId])

  // 返回上一步
  const handleBack = useCallback(() => {
    if (step === 'edit-single' || step === 'edit-multiple') {
      setStep('select')
      setUploadedFiles([])
      setSelectedFileId(null)
    }
  }, [step])

  // 执行实际上传
  const handleSubmit = useCallback(async () => {
    if (!allFilesComplete) {
      toast({
        title: '请填写必填项',
        description: '每个表情包的情感标签都是必填的',
        variant: 'destructive',
      })
      return
    }

    setUploading(true)
    let successCount = 0
    let failedCount = 0

    try {
      for (const fileInfo of uploadedFiles) {
        const formData = new FormData()
        formData.append('file', fileInfo.file)
        formData.append('emotion', fileInfo.emotion)
        formData.append('description', fileInfo.description)
        formData.append('is_registered', fileInfo.isRegistered.toString())

        try {
          const response = await fetchWithAuth(getEmojiUploadUrl(), {
            method: 'POST',
            body: formData,
          })

          if (response.ok) {
            successCount++
          } else {
            failedCount++
          }
        } catch {
          failedCount++
        }
      }

      if (failedCount === 0) {
        toast({
          title: '上传成功',
          description: `成功上传 ${successCount} 个表情包`,
        })
        onOpenChange(false)
        onSuccess()
      } else {
        toast({
          title: '部分上传失败',
          description: `成功 ${successCount} 个，失败 ${failedCount} 个`,
          variant: 'destructive',
        })
        onSuccess()
      }
    } finally {
      setUploading(false)
    }
  }, [allFilesComplete, uploadedFiles, toast, onOpenChange, onSuccess])

  // 渲染文件选择步骤
  const renderSelectStep = () => (
    <div className="space-y-4">
      <div className="border rounded-lg overflow-hidden w-full">
        <Dashboard
          uppy={uppy}
          proudlyDisplayPoweredByUppy={false}
          hideProgressDetails
          height={350}
          width="100%"
          theme="auto"
          note="支持 JPG、PNG、GIF、WebP 格式，最多 20 个文件"
        />
      </div>
    </div>
  )

  // 渲染单个文件编辑步骤
  const renderEditSingleStep = () => {
    const file = uploadedFiles[0]
    if (!file) return null

    return (
      <div className="space-y-4">
        <div className="flex items-center gap-4">
          <Button variant="ghost" size="sm" onClick={handleBack}>
            <ArrowLeft className="h-4 w-4 mr-1" />
            返回
          </Button>
          <span className="text-sm text-muted-foreground">编辑表情包信息</span>
        </div>

        <div className="flex gap-6">
          {/* 预览图 */}
          <div className="flex-shrink-0">
            <div className="w-32 h-32 rounded-lg border overflow-hidden bg-muted flex items-center justify-center">
              <img
                src={file.previewUrl}
                alt={file.name}
                className="max-w-full max-h-full object-contain"
              />
            </div>
            <p className="text-xs text-muted-foreground mt-2 text-center truncate max-w-32">
              {file.name}
            </p>
          </div>

          {/* 表单 */}
          <div className="flex-1 space-y-4">
            <div className="space-y-2">
              <Label htmlFor="single-emotion">
                情感标签 <span className="text-destructive">*</span>
              </Label>
              <Input
                id="single-emotion"
                value={file.emotion}
                onChange={(e) => updateFileInfo(file.id, { emotion: e.target.value })}
                placeholder="多个标签用逗号分隔，如：开心,高兴"
                className={!file.emotion.trim() ? 'border-destructive' : ''}
              />
              <p className="text-xs text-muted-foreground">
                用于情感匹配，多个标签用逗号分隔
              </p>
            </div>

            <div className="space-y-2">
              <Label htmlFor="single-description">描述</Label>
              <Input
                id="single-description"
                value={file.description}
                onChange={(e) => updateFileInfo(file.id, { description: e.target.value })}
                placeholder="输入表情包描述..."
              />
            </div>

            <div className="flex items-center space-x-2">
              <Checkbox
                id="single-is-registered"
                checked={file.isRegistered}
                onCheckedChange={(checked) => updateFileInfo(file.id, { isRegistered: checked === true })}
              />
              <Label htmlFor="single-is-registered" className="cursor-pointer">
                上传后立即注册（可被麦麦使用）
              </Label>
            </div>
          </div>
        </div>

        <DialogFooter>
          <Button
            onClick={handleSubmit}
            disabled={!allFilesComplete || uploading}
          >
            {uploading ? '上传中...' : '上传'}
          </Button>
        </DialogFooter>
      </div>
    )
  }

  // 渲染多个文件编辑步骤
  const renderEditMultipleStep = () => {
    const completedCount = uploadedFiles.filter(isFileComplete).length
    const totalCount = uploadedFiles.length

    return (
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-4">
            <Button variant="ghost" size="sm" onClick={handleBack}>
              <ArrowLeft className="h-4 w-4 mr-1" />
              返回
            </Button>
            <span className="text-sm text-muted-foreground">
              编辑表情包信息（{completedCount}/{totalCount} 已完成）
            </span>
          </div>
          <Badge variant={allFilesComplete ? 'default' : 'secondary'}>
            {allFilesComplete ? (
              <><Check className="h-3 w-3 mr-1" />全部完成</>
            ) : (
              <><X className="h-3 w-3 mr-1" />未完成</>
            )}
          </Badge>
        </div>

        <div className="grid grid-cols-2 gap-4">
          {/* 左侧：文件卡片列表 */}
          <ScrollArea className="h-[350px] pr-2">
            <div className="space-y-2">
              {uploadedFiles.map((file) => {
                const complete = isFileComplete(file)
                const isSelected = selectedFileId === file.id
                return (
                  <div
                    key={file.id}
                    onClick={() => setSelectedFileId(file.id)}
                    className={`
                      flex items-center gap-3 p-3 rounded-lg border-2 cursor-pointer transition-all
                      ${isSelected ? 'ring-2 ring-primary' : ''}
                      ${complete ? 'border-green-500 bg-green-50 dark:bg-green-950/20' : 'border-border hover:border-muted-foreground/50'}
                    `}
                  >
                    <div className="w-12 h-12 rounded border overflow-hidden bg-muted flex-shrink-0 flex items-center justify-center">
                      <img
                        src={file.previewUrl}
                        alt={file.name}
                        className="max-w-full max-h-full object-contain"
                      />
                    </div>
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-medium truncate">{file.name}</p>
                      <p className="text-xs text-muted-foreground truncate">
                        {file.emotion || '未填写情感标签'}
                      </p>
                    </div>
                    {complete ? (
                      <CheckCircle2 className="h-5 w-5 text-green-500 flex-shrink-0" />
                    ) : (
                      <div className="h-5 w-5 rounded-full border-2 border-muted-foreground/30 flex-shrink-0" />
                    )}
                  </div>
                )
              })}
            </div>
          </ScrollArea>

          {/* 右侧：选中文件的编辑表单 */}
          <div className="border rounded-lg p-4">
            {selectedFile ? (
              <div className="space-y-4">
                <div className="flex items-center gap-3">
                  <div className="w-16 h-16 rounded border overflow-hidden bg-muted flex items-center justify-center">
                    <img
                      src={selectedFile.previewUrl}
                      alt={selectedFile.name}
                      className="max-w-full max-h-full object-contain"
                    />
                  </div>
                  <div className="flex-1 min-w-0">
                    <p className="font-medium truncate">{selectedFile.name}</p>
                    {isFileComplete(selectedFile) && (
                      <Badge variant="outline" className="text-green-600 border-green-600">
                        <Check className="h-3 w-3 mr-1" />
                        已完成
                      </Badge>
                    )}
                  </div>
                </div>

                <div className="space-y-2">
                  <Label htmlFor="multi-emotion">
                    情感标签 <span className="text-destructive">*</span>
                  </Label>
                  <Input
                    id="multi-emotion"
                    value={selectedFile.emotion}
                    onChange={(e) => updateFileInfo(selectedFile.id, { emotion: e.target.value })}
                    placeholder="多个标签用逗号分隔，如：开心,高兴"
                    className={!selectedFile.emotion.trim() ? 'border-destructive' : ''}
                  />
                </div>

                <div className="space-y-2">
                  <Label htmlFor="multi-description">描述</Label>
                  <Input
                    id="multi-description"
                    value={selectedFile.description}
                    onChange={(e) => updateFileInfo(selectedFile.id, { description: e.target.value })}
                    placeholder="输入表情包描述..."
                  />
                </div>

                <div className="flex items-center space-x-2">
                  <Checkbox
                    id="multi-is-registered"
                    checked={selectedFile.isRegistered}
                    onCheckedChange={(checked) => updateFileInfo(selectedFile.id, { isRegistered: checked === true })}
                  />
                  <Label htmlFor="multi-is-registered" className="cursor-pointer text-sm">
                    上传后立即注册
                  </Label>
                </div>
              </div>
            ) : (
              <div className="h-full flex items-center justify-center text-muted-foreground">
                <div className="text-center">
                  <ImageIcon className="h-12 w-12 mx-auto mb-2 opacity-50" />
                  <p>点击左侧卡片编辑</p>
                </div>
              </div>
            )}
          </div>
        </div>

        <DialogFooter>
          <Button
            onClick={handleSubmit}
            disabled={!allFilesComplete || uploading}
          >
            {uploading ? '上传中...' : `上传全部 (${totalCount})`}
          </Button>
        </DialogFooter>
      </div>
    )
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-3xl max-h-[90vh] overflow-hidden">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Upload className="h-5 w-5" />
            {step === 'select' && '上传表情包 - 选择文件'}
            {step === 'edit-single' && '上传表情包 - 填写信息'}
            {step === 'edit-multiple' && '上传表情包 - 批量编辑'}
          </DialogTitle>
          <DialogDescription>
            {step === 'select' && '支持 JPG、PNG、GIF、WebP 格式，单个文件最大 10MB，可同时上传多个文件'}
            {step === 'edit-single' && '请填写表情包的情感标签（必填）和描述'}
            {step === 'edit-multiple' && '点击左侧卡片编辑每个表情包的信息，情感标签为必填项'}
          </DialogDescription>
        </DialogHeader>

        <div className="overflow-y-auto pr-1">
          {step === 'select' && renderSelectStep()}
          {step === 'edit-single' && renderEditSingleStep()}
          {step === 'edit-multiple' && renderEditMultipleStep()}
        </div>
      </DialogContent>
    </Dialog>
  )
}