import { Hash, Search, Edit, Trash2, Eye, Plus, ChevronLeft, ChevronRight, ChevronsLeft, ChevronsRight, Check, X, HelpCircle, Globe, MessageCircle } from 'lucide-react'
import { useState, useEffect } from 'react'
import { cn } from '@/lib/utils'
import { MarkdownRenderer } from '@/components/markdown-renderer'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { ScrollArea } from '@/components/ui/scroll-area'
import { useToast } from '@/hooks/use-toast'
import { Textarea } from '@/components/ui/textarea'
import { Badge } from '@/components/ui/badge'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
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
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Checkbox } from '@/components/ui/checkbox'
import { Switch } from '@/components/ui/switch'
import type { Jargon, JargonCreateRequest, JargonUpdateRequest, JargonChatInfo, JargonStats } from '@/types/jargon'
import { getJargonList, getJargonDetail, createJargon, updateJargon, deleteJargon, batchDeleteJargons, getJargonStats, getJargonChatList, batchSetJargonStatus } from '@/lib/jargon-api'

export function JargonManagementPage() {
  const [jargons, setJargons] = useState<Jargon[]>([])
  const [loading, setLoading] = useState(true)
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [search, setSearch] = useState('')
  const [filterChatId, setFilterChatId] = useState<string>('all')
  const [filterIsJargon, setFilterIsJargon] = useState<string>('all')
  const [selectedJargon, setSelectedJargon] = useState<Jargon | null>(null)
  const [isDetailDialogOpen, setIsDetailDialogOpen] = useState(false)
  const [isEditDialogOpen, setIsEditDialogOpen] = useState(false)
  const [isCreateDialogOpen, setIsCreateDialogOpen] = useState(false)
  const [deleteConfirmJargon, setDeleteConfirmJargon] = useState<Jargon | null>(null)
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set())
  const [isBatchDeleteDialogOpen, setIsBatchDeleteDialogOpen] = useState(false)
  const [jumpToPage, setJumpToPage] = useState('')
  const [stats, setStats] = useState<JargonStats>({
    total: 0,
    confirmed_jargon: 0,
    confirmed_not_jargon: 0,
    pending: 0,
    global_count: 0,
    complete_count: 0,
    chat_count: 0,
    top_chats: {},
  })
  const [chatList, setChatList] = useState<JargonChatInfo[]>([])
  const { toast } = useToast()

  // 加载黑话列表
  const loadJargons = async () => {
    try {
      setLoading(true)
      const response = await getJargonList({
        page,
        page_size: pageSize,
        search: search || undefined,
        chat_id: filterChatId === 'all' ? undefined : filterChatId,
        is_jargon: filterIsJargon === 'all' ? undefined : filterIsJargon === 'true' ? true : filterIsJargon === 'false' ? false : undefined,
      })
      setJargons(response.data)
      setTotal(response.total)
    } catch (error) {
      toast({
        title: '加载失败',
        description: error instanceof Error ? error.message : '无法加载黑话列表',
        variant: 'destructive',
      })
    } finally {
      setLoading(false)
    }
  }

  // 加载统计数据
  const loadStats = async () => {
    try {
      const response = await getJargonStats()
      if (response?.data) {
        setStats(response.data)
      }
    } catch (error) {
      console.error('加载统计数据失败:', error)
    }
  }

  // 加载聊天列表
  const loadChatList = async () => {
    try {
      const response = await getJargonChatList()
      if (response?.data) {
        setChatList(response.data)
      }
    } catch (error) {
      console.error('加载聊天列表失败:', error)
    }
  }

  // 初始加载
  useEffect(() => {
    loadJargons()
    loadStats()
    loadChatList()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page, pageSize, search, filterChatId, filterIsJargon])

  // 查看详情
  const handleViewDetail = async (jargon: Jargon) => {
    try {
      const response = await getJargonDetail(jargon.id)
      setSelectedJargon(response.data)
      setIsDetailDialogOpen(true)
    } catch (error) {
      toast({
        title: '加载详情失败',
        description: error instanceof Error ? error.message : '无法加载黑话详情',
        variant: 'destructive',
      })
    }
  }

  // 编辑黑话
  const handleEdit = (jargon: Jargon) => {
    setSelectedJargon(jargon)
    setIsEditDialogOpen(true)
  }

  // 删除黑话
  const handleDelete = async (jargon: Jargon) => {
    try {
      await deleteJargon(jargon.id)
      toast({
        title: '删除成功',
        description: `已删除黑话: ${jargon.content}`,
      })
      setDeleteConfirmJargon(null)
      loadJargons()
      loadStats()
    } catch (error) {
      toast({
        title: '删除失败',
        description: error instanceof Error ? error.message : '无法删除黑话',
        variant: 'destructive',
      })
    }
  }

  // 切换单个选择
  const toggleSelect = (id: number) => {
    const newSelected = new Set(selectedIds)
    if (newSelected.has(id)) {
      newSelected.delete(id)
    } else {
      newSelected.add(id)
    }
    setSelectedIds(newSelected)
  }

  // 全选/取消全选
  const toggleSelectAll = () => {
    if (selectedIds.size === jargons.length && jargons.length > 0) {
      setSelectedIds(new Set())
    } else {
      setSelectedIds(new Set(jargons.map(j => j.id)))
    }
  }

  // 批量删除
  const handleBatchDelete = async () => {
    try {
      await batchDeleteJargons(Array.from(selectedIds))
      toast({
        title: '批量删除成功',
        description: `已删除 ${selectedIds.size} 个黑话`,
      })
      setSelectedIds(new Set())
      setIsBatchDeleteDialogOpen(false)
      loadJargons()
      loadStats()
    } catch (error) {
      toast({
        title: '批量删除失败',
        description: error instanceof Error ? error.message : '无法批量删除黑话',
        variant: 'destructive',
      })
    }
  }

  // 批量设置为黑话
  const handleBatchSetJargon = async (isJargon: boolean) => {
    try {
      await batchSetJargonStatus(Array.from(selectedIds), isJargon)
      toast({
        title: '操作成功',
        description: `已将 ${selectedIds.size} 个词条设为${isJargon ? '黑话' : '非黑话'}`,
      })
      setSelectedIds(new Set())
      loadJargons()
      loadStats()
    } catch (error) {
      toast({
        title: '操作失败',
        description: error instanceof Error ? error.message : '批量设置失败',
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

  // 渲染黑话状态徽章
  const renderJargonStatus = (isJargon: boolean | null) => {
    if (isJargon === true) {
      return <Badge variant="default" className="bg-green-600 hover:bg-green-700"><Check className="h-3 w-3 mr-1" />是黑话</Badge>
    } else if (isJargon === false) {
      return <Badge variant="secondary"><X className="h-3 w-3 mr-1" />非黑话</Badge>
    } else {
      return <Badge variant="outline"><HelpCircle className="h-3 w-3 mr-1" />未判定</Badge>
    }
  }

  return (
    <div className="h-[calc(100vh-4rem)] flex flex-col p-4 sm:p-6">
      {/* 页面标题 */}
      <div className="mb-4 sm:mb-6">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
          <div>
            <h1 className="text-2xl sm:text-3xl font-bold flex items-center gap-2">
              <MessageCircle className="h-8 w-8" strokeWidth={2} />
              黑话管理
            </h1>
            <p className="text-muted-foreground mt-1 text-sm sm:text-base">
              管理麦麦学习到的黑话和俚语
            </p>
          </div>
          <Button onClick={() => setIsCreateDialogOpen(true)} className="gap-2">
            <Plus className="h-4 w-4" />
            新增黑话
          </Button>
        </div>
      </div>

      <ScrollArea className="flex-1">
        <div className="space-y-4 sm:space-y-6 pr-4">

          {/* 统计卡片 */}
          <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-7 gap-3">
            <div className="rounded-lg border bg-card p-3 sm:p-4">
              <div className="text-xs sm:text-sm text-muted-foreground">总数量</div>
              <div className="text-xl sm:text-2xl font-bold mt-1">{stats.total}</div>
            </div>
            <div className="rounded-lg border bg-card p-3 sm:p-4">
              <div className="text-xs sm:text-sm text-muted-foreground">已确认黑话</div>
              <div className="text-xl sm:text-2xl font-bold mt-1 text-green-600">{stats.confirmed_jargon}</div>
            </div>
            <div className="rounded-lg border bg-card p-3 sm:p-4">
              <div className="text-xs sm:text-sm text-muted-foreground">确认非黑话</div>
              <div className="text-xl sm:text-2xl font-bold mt-1 text-gray-500">{stats.confirmed_not_jargon}</div>
            </div>
            <div className="rounded-lg border bg-card p-3 sm:p-4">
              <div className="text-xs sm:text-sm text-muted-foreground">待判定</div>
              <div className="text-xl sm:text-2xl font-bold mt-1 text-yellow-600">{stats.pending}</div>
            </div>
            <div className="rounded-lg border bg-card p-3 sm:p-4">
              <div className="text-xs sm:text-sm text-muted-foreground">全局黑话</div>
              <div className="text-xl sm:text-2xl font-bold mt-1 text-blue-600">{stats.global_count}</div>
            </div>
            <div className="rounded-lg border bg-card p-3 sm:p-4">
              <div className="text-xs sm:text-sm text-muted-foreground">推断完成</div>
              <div className="text-xl sm:text-2xl font-bold mt-1 text-purple-600">{stats.complete_count}</div>
            </div>
            <div className="rounded-lg border bg-card p-3 sm:p-4">
              <div className="text-xs sm:text-sm text-muted-foreground">关联聊天数</div>
              <div className="text-xl sm:text-2xl font-bold mt-1">{stats.chat_count}</div>
            </div>
          </div>

          {/* 搜索和筛选 */}
          <div className="rounded-lg border bg-card p-4">
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
              <div className="space-y-1.5">
                <Label htmlFor="search">搜索</Label>
                <div className="relative">
                  <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
                  <Input
                    id="search"
                    placeholder="搜索内容、含义..."
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                    className="pl-9"
                  />
                </div>
              </div>
              <div className="space-y-1.5">
                <Label>聊天筛选</Label>
                <Select value={filterChatId} onValueChange={setFilterChatId}>
                  <SelectTrigger>
                    <SelectValue placeholder="全部聊天" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">全部聊天</SelectItem>
                    {chatList.map((chat) => (
                      <SelectItem key={chat.chat_id} value={chat.chat_id}>
                        {chat.chat_name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label>状态筛选</Label>
                <Select value={filterIsJargon} onValueChange={setFilterIsJargon}>
                  <SelectTrigger>
                    <SelectValue placeholder="全部状态" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">全部状态</SelectItem>
                    <SelectItem value="true">是黑话</SelectItem>
                    <SelectItem value="false">非黑话</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="page-size">每页显示</Label>
                <Select
                  value={pageSize.toString()}
                  onValueChange={(value) => {
                    setPageSize(parseInt(value))
                    setPage(1)
                    setSelectedIds(new Set())
                  }}
                >
                  <SelectTrigger id="page-size">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="10">10</SelectItem>
                    <SelectItem value="20">20</SelectItem>
                    <SelectItem value="50">50</SelectItem>
                    <SelectItem value="100">100</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>

            {/* 批量操作工具栏 */}
            {selectedIds.size > 0 && (
              <div className="flex flex-wrap items-center gap-2 mt-4 pt-4 border-t">
                <span className="text-sm text-muted-foreground">已选择 {selectedIds.size} 个</span>
                <Button variant="outline" size="sm" onClick={() => handleBatchSetJargon(true)}>
                  <Check className="h-4 w-4 mr-1" />
                  标记为黑话
                </Button>
                <Button variant="outline" size="sm" onClick={() => handleBatchSetJargon(false)}>
                  <X className="h-4 w-4 mr-1" />
                  标记为非黑话
                </Button>
                <Button variant="outline" size="sm" onClick={() => setSelectedIds(new Set())}>
                  取消选择
                </Button>
                <Button variant="destructive" size="sm" onClick={() => setIsBatchDeleteDialogOpen(true)}>
                  <Trash2 className="h-4 w-4 mr-1" />
                  批量删除
                </Button>
              </div>
            )}
          </div>

          {/* 黑话列表 */}
          <div className="rounded-lg border bg-card">
            {/* 桌面端表格视图 */}
            <div className="hidden md:block">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-12">
                      <Checkbox
                        checked={selectedIds.size === jargons.length && jargons.length > 0}
                        onCheckedChange={toggleSelectAll}
                      />
                    </TableHead>
                    <TableHead>内容</TableHead>
                    <TableHead>含义</TableHead>
                    <TableHead>聊天</TableHead>
                    <TableHead>状态</TableHead>
                    <TableHead className="text-center">次数</TableHead>
                    <TableHead className="text-right">操作</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {loading ? (
                    <TableRow>
                      <TableCell colSpan={7} className="text-center py-8 text-muted-foreground">
                        加载中...
                      </TableCell>
                    </TableRow>
                  ) : jargons.length === 0 ? (
                    <TableRow>
                      <TableCell colSpan={7} className="text-center py-8 text-muted-foreground">
                        暂无数据
                      </TableCell>
                    </TableRow>
                  ) : (
                    jargons.map((jargon) => (
                      <TableRow key={jargon.id}>
                        <TableCell>
                          <Checkbox
                            checked={selectedIds.has(jargon.id)}
                            onCheckedChange={() => toggleSelect(jargon.id)}
                          />
                        </TableCell>
                        <TableCell className="font-medium max-w-[200px]">
                          <div className="flex items-center gap-2">
                            {jargon.is_global && <span title="全局黑话"><Globe className="h-4 w-4 text-blue-500 flex-shrink-0" /></span>}
                            <span className="truncate" title={jargon.content}>{jargon.content}</span>
                          </div>
                        </TableCell>
                        <TableCell className="max-w-[200px] truncate" title={jargon.meaning || ''}>
                          {jargon.meaning || <span className="text-muted-foreground">-</span>}
                        </TableCell>
                        <TableCell className="max-w-[150px] truncate" title={jargon.chat_name || jargon.chat_id}>
                          {jargon.chat_name || jargon.chat_id}
                        </TableCell>
                        <TableCell>{renderJargonStatus(jargon.is_jargon)}</TableCell>
                        <TableCell className="text-center">{jargon.count}</TableCell>
                        <TableCell className="text-right">
                          <div className="flex justify-end gap-2">
                            <Button
                              variant="default"
                              size="sm"
                              onClick={() => handleEdit(jargon)}
                            >
                              <Edit className="h-4 w-4 mr-1" />
                              编辑
                            </Button>
                            <Button
                              variant="outline"
                              size="icon"
                              className="h-8 w-8"
                              onClick={() => handleViewDetail(jargon)}
                              title="查看详情"
                            >
                              <Eye className="h-4 w-4" />
                            </Button>
                            <Button
                              size="sm"
                              onClick={() => setDeleteConfirmJargon(jargon)}
                              className="bg-red-600 hover:bg-red-700 text-white"
                            >
                              <Trash2 className="h-4 w-4 mr-1" />
                              删除
                            </Button>
                          </div>
                        </TableCell>
                      </TableRow>
                    ))
                  )}
                </TableBody>
              </Table>
            </div>

            {/* 移动端卡片视图 */}
            <div className="md:hidden space-y-3 p-4">
              {loading ? (
                <div className="text-center py-8 text-muted-foreground">加载中...</div>
              ) : jargons.length === 0 ? (
                <div className="text-center py-8 text-muted-foreground">暂无数据</div>
              ) : (
                jargons.map((jargon) => (
                  <div key={jargon.id} className="rounded-lg border bg-card p-4 space-y-3">
                    <div className="flex items-start gap-3">
                      <Checkbox
                        checked={selectedIds.has(jargon.id)}
                        onCheckedChange={() => toggleSelect(jargon.id)}
                        className="mt-1"
                      />
                      <div className="min-w-0 flex-1 space-y-2">
                        <div className="flex items-center gap-2">
                          {jargon.is_global && <Globe className="h-4 w-4 text-blue-500 flex-shrink-0" />}
                          <h3 className="font-semibold text-sm break-all">{jargon.content}</h3>
                        </div>
                        {jargon.meaning && (
                          <p className="text-sm text-muted-foreground break-all">{jargon.meaning}</p>
                        )}
                        <div className="flex flex-wrap items-center gap-2 text-xs">
                          {renderJargonStatus(jargon.is_jargon)}
                          <span className="text-muted-foreground">次数: {jargon.count}</span>
                        </div>
                        <div className="text-xs text-muted-foreground truncate">
                          聊天: {jargon.chat_name || jargon.chat_id}
                        </div>
                      </div>
                    </div>
                    <div className="flex flex-wrap gap-1 pt-2 border-t">
                      <Button variant="outline" size="sm" onClick={() => handleEdit(jargon)} className="text-xs px-2 py-1 h-auto">
                        <Edit className="h-3 w-3 mr-1" />编辑
                      </Button>
                      <Button variant="outline" size="sm" onClick={() => handleViewDetail(jargon)} className="text-xs px-2 py-1 h-auto">
                        <Eye className="h-3 w-3" />
                      </Button>
                      <Button variant="outline" size="sm" onClick={() => setDeleteConfirmJargon(jargon)} className="text-xs px-2 py-1 h-auto text-destructive hover:text-destructive">
                        <Trash2 className="h-3 w-3 mr-1" />删除
                      </Button>
                    </div>
                  </div>
                ))
              )}
            </div>

            {/* 分页 */}
            {total > 0 && (
              <div className="flex flex-col sm:flex-row items-center justify-between gap-4 px-4 py-3 border-t">
                <div className="text-sm text-muted-foreground">
                  共 {total} 条记录，第 {page} / {Math.ceil(total / pageSize)} 页
                </div>
                <div className="flex items-center gap-2">
                  <Button variant="outline" size="sm" onClick={() => setPage(1)} disabled={page === 1} className="hidden sm:flex">
                    <ChevronsLeft className="h-4 w-4" />
                  </Button>
                  <Button variant="outline" size="sm" onClick={() => setPage(page - 1)} disabled={page === 1}>
                    <ChevronLeft className="h-4 w-4 sm:mr-1" />
                    <span className="hidden sm:inline">上一页</span>
                  </Button>
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
                    <Button variant="outline" size="sm" onClick={handleJumpToPage} disabled={!jumpToPage} className="h-8">
                      跳转
                    </Button>
                  </div>
                  <Button variant="outline" size="sm" onClick={() => setPage(page + 1)} disabled={page >= Math.ceil(total / pageSize)}>
                    <span className="hidden sm:inline">下一页</span>
                    <ChevronRight className="h-4 w-4 sm:ml-1" />
                  </Button>
                  <Button variant="outline" size="sm" onClick={() => setPage(Math.ceil(total / pageSize))} disabled={page >= Math.ceil(total / pageSize)} className="hidden sm:flex">
                    <ChevronsRight className="h-4 w-4" />
                  </Button>
                </div>
              </div>
            )}
          </div>
        </div>
      </ScrollArea>

      {/* 详情对话框 */}
      <JargonDetailDialog
        jargon={selectedJargon}
        open={isDetailDialogOpen}
        onOpenChange={setIsDetailDialogOpen}
      />

      {/* 创建对话框 */}
      <JargonCreateDialog
        open={isCreateDialogOpen}
        onOpenChange={setIsCreateDialogOpen}
        chatList={chatList}
        onSuccess={() => {
          loadJargons()
          loadStats()
          setIsCreateDialogOpen(false)
        }}
      />

      {/* 编辑对话框 */}
      <JargonEditDialog
        jargon={selectedJargon}
        open={isEditDialogOpen}
        onOpenChange={setIsEditDialogOpen}
        chatList={chatList}
        onSuccess={() => {
          loadJargons()
          loadStats()
          setIsEditDialogOpen(false)
        }}
      />

      {/* 删除确认对话框 */}
      <AlertDialog open={!!deleteConfirmJargon} onOpenChange={() => setDeleteConfirmJargon(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>确认删除</AlertDialogTitle>
            <AlertDialogDescription>
              确定要删除黑话 "{deleteConfirmJargon?.content}" 吗？此操作不可撤销。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => deleteConfirmJargon && handleDelete(deleteConfirmJargon)}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              删除
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* 批量删除确认对话框 */}
      <AlertDialog open={isBatchDeleteDialogOpen} onOpenChange={setIsBatchDeleteDialogOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>确认批量删除</AlertDialogTitle>
            <AlertDialogDescription>
              您即将删除 {selectedIds.size} 个黑话，此操作无法撤销。确定要继续吗？
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction onClick={handleBatchDelete} className="bg-destructive text-destructive-foreground hover:bg-destructive/90">
              确认删除
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}

// 黑话详情对话框
function JargonDetailDialog({
  jargon,
  open,
  onOpenChange,
}: {
  jargon: Jargon | null
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  if (!jargon) return null

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl max-h-[80vh] grid grid-rows-[auto_1fr_auto] overflow-hidden">
        <DialogHeader>
          <DialogTitle>黑话详情</DialogTitle>
          <DialogDescription>查看黑话的完整信息</DialogDescription>
        </DialogHeader>

        <ScrollArea className="h-full pr-4">
          <div className="space-y-4 pb-2">
            <div className="grid grid-cols-2 gap-4">
              <InfoItem icon={Hash} label="记录ID" value={jargon.id.toString()} mono />
              <InfoItem label="使用次数" value={jargon.count.toString()} />
            </div>
            
            <div className="space-y-1">
              <Label className="text-xs text-muted-foreground">内容</Label>
              <div className="text-sm p-2 bg-muted rounded break-all whitespace-pre-wrap">{jargon.content}</div>
            </div>

            {jargon.raw_content && (
              <div className="space-y-1">
                <Label className="text-xs text-muted-foreground">原始内容</Label>
                <div className="text-sm p-2 bg-muted rounded break-all">
                  {(() => {
                    try {
                      const rawArray = JSON.parse(jargon.raw_content)
                      if (Array.isArray(rawArray)) {
                        return rawArray.map((item, index) => (
                          <div key={index}>
                            {index > 0 && <hr className="my-3 border-border" />}
                            <div className="whitespace-pre-wrap">{item}</div>
                          </div>
                        ))
                      }
                      return <div className="whitespace-pre-wrap">{jargon.raw_content}</div>
                    } catch {
                      return <div className="whitespace-pre-wrap">{jargon.raw_content}</div>
                    }
                  })()}
                </div>
              </div>
            )}

            <div className="space-y-1">
              <Label className="text-xs text-muted-foreground">含义</Label>
              <div className="text-sm p-2 bg-muted rounded break-all">
                {jargon.meaning ? (
                  <MarkdownRenderer content={jargon.meaning} />
                ) : (
                  '-'
                )}
              </div>
            </div>

            <div className="grid grid-cols-2 gap-4">
              <InfoItem label="聊天" value={jargon.chat_name || jargon.chat_id} />
              <div className="space-y-1">
                <Label className="text-xs text-muted-foreground">状态</Label>
                <div className="flex items-center gap-2">
                  {jargon.is_jargon === true && <Badge variant="default" className="bg-green-600">是黑话</Badge>}
                  {jargon.is_jargon === false && <Badge variant="secondary">非黑话</Badge>}
                  {jargon.is_jargon === null && <Badge variant="outline">未判定</Badge>}
                  {jargon.is_global && <Badge variant="outline" className="border-blue-500 text-blue-500">全局</Badge>}
                  {jargon.is_complete && <Badge variant="outline" className="border-purple-500 text-purple-500">推断完成</Badge>}
                </div>
              </div>
            </div>

            {jargon.inference_with_context && (
              <div className="space-y-1">
                <Label className="text-xs text-muted-foreground">上下文推断结果</Label>
                <div className="p-2 bg-muted rounded break-all whitespace-pre-wrap font-mono text-xs max-h-[200px] overflow-y-auto">{jargon.inference_with_context}</div>
              </div>
            )}

            {jargon.inference_content_only && (
              <div className="space-y-1">
                <Label className="text-xs text-muted-foreground">纯词条推断结果</Label>
                <div className="p-2 bg-muted rounded break-all whitespace-pre-wrap font-mono text-xs max-h-[200px] overflow-y-auto">{jargon.inference_content_only}</div>
              </div>
            )}
          </div>
        </ScrollArea>

        <DialogFooter className="flex-shrink-0">
          <Button onClick={() => onOpenChange(false)}>关闭</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

// 信息项组件
function InfoItem({
  icon: Icon,
  label,
  value,
  mono = false,
}: {
  icon?: typeof Hash
  label: string
  value: string | null | undefined
  mono?: boolean
}) {
  return (
    <div className="space-y-1">
      <Label className="text-xs text-muted-foreground flex items-center gap-1">
        {Icon && <Icon className="h-3 w-3" />}
        {label}
      </Label>
      <div className={cn('text-sm', mono && 'font-mono', !value && 'text-muted-foreground')}>
        {value || '-'}
      </div>
    </div>
  )
}

// 黑话创建对话框
function JargonCreateDialog({
  open,
  onOpenChange,
  chatList,
  onSuccess,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  chatList: JargonChatInfo[]
  onSuccess: () => void
}) {
  const [formData, setFormData] = useState<JargonCreateRequest>({
    content: '',
    meaning: '',
    chat_id: '',
    is_global: false,
  })
  const [saving, setSaving] = useState(false)
  const { toast } = useToast()

  const handleCreate = async () => {
    if (!formData.content || !formData.chat_id) {
      toast({
        title: '验证失败',
        description: '请填写必填字段：内容和聊天',
        variant: 'destructive',
      })
      return
    }

    try {
      setSaving(true)
      await createJargon(formData)
      toast({
        title: '创建成功',
        description: '黑话已创建',
      })
      setFormData({ content: '', meaning: '', chat_id: '', is_global: false })
      onSuccess()
    } catch (error) {
      toast({
        title: '创建失败',
        description: error instanceof Error ? error.message : '无法创建黑话',
        variant: 'destructive',
      })
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl max-h-[80vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>新增黑话</DialogTitle>
          <DialogDescription>创建新的黑话记录</DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="content">
              内容 <span className="text-destructive">*</span>
            </Label>
            <Input
              id="content"
              value={formData.content}
              onChange={(e) => setFormData({ ...formData, content: e.target.value })}
              placeholder="输入黑话内容"
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="meaning">含义</Label>
            <Textarea
              id="meaning"
              value={formData.meaning || ''}
              onChange={(e) => setFormData({ ...formData, meaning: e.target.value })}
              placeholder="输入黑话含义（可选）"
              rows={3}
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="chat_id">
              聊天 <span className="text-destructive">*</span>
            </Label>
            <Select
              value={formData.chat_id}
              onValueChange={(value) => setFormData({ ...formData, chat_id: value })}
            >
              <SelectTrigger>
                <SelectValue placeholder="选择关联的聊天" />
              </SelectTrigger>
              <SelectContent>
                {chatList.map((chat) => (
                  <SelectItem key={chat.chat_id} value={chat.chat_id}>
                    {chat.chat_name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="flex items-center space-x-2">
            <Switch
              id="is_global"
              checked={formData.is_global}
              onCheckedChange={(checked) => setFormData({ ...formData, is_global: checked })}
            />
            <Label htmlFor="is_global">设为全局黑话</Label>
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>取消</Button>
          <Button onClick={handleCreate} disabled={saving}>
            {saving ? '创建中...' : '创建'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

// 黑话编辑对话框
function JargonEditDialog({
  jargon,
  open,
  onOpenChange,
  chatList,
  onSuccess,
}: {
  jargon: Jargon | null
  open: boolean
  onOpenChange: (open: boolean) => void
  chatList: JargonChatInfo[]
  onSuccess: () => void
}) {
  const [formData, setFormData] = useState<JargonUpdateRequest>({})
  const [saving, setSaving] = useState(false)
  const { toast } = useToast()

  useEffect(() => {
    if (jargon) {
      setFormData({
        content: jargon.content,
        meaning: jargon.meaning || '',
        chat_id: jargon.stream_id || jargon.chat_id,  // 使用 stream_id 来匹配 chatList
        is_global: jargon.is_global,
        is_jargon: jargon.is_jargon,
      })
    }
  }, [jargon])

  const handleSave = async () => {
    if (!jargon) return

    try {
      setSaving(true)
      await updateJargon(jargon.id, formData)
      toast({
        title: '保存成功',
        description: '黑话已更新',
      })
      onSuccess()
    } catch (error) {
      toast({
        title: '保存失败',
        description: error instanceof Error ? error.message : '无法更新黑话',
        variant: 'destructive',
      })
    } finally {
      setSaving(false)
    }
  }

  if (!jargon) return null

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl max-h-[80vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>编辑黑话</DialogTitle>
          <DialogDescription>修改黑话的信息</DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="edit_content">内容</Label>
            <Input
              id="edit_content"
              value={formData.content || ''}
              onChange={(e) => setFormData({ ...formData, content: e.target.value })}
              placeholder="输入黑话内容"
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="edit_meaning">含义</Label>
            <Textarea
              id="edit_meaning"
              value={formData.meaning || ''}
              onChange={(e) => setFormData({ ...formData, meaning: e.target.value })}
              placeholder="输入黑话含义"
              rows={3}
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="edit_chat_id">聊天</Label>
            <Select
              value={formData.chat_id || ''}
              onValueChange={(value) => setFormData({ ...formData, chat_id: value })}
            >
              <SelectTrigger>
                <SelectValue placeholder="选择关联的聊天" />
              </SelectTrigger>
              <SelectContent>
                {chatList.map((chat) => (
                  <SelectItem key={chat.chat_id} value={chat.chat_id}>
                    {chat.chat_name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="space-y-2">
            <Label>黑话状态</Label>
            <Select
              value={formData.is_jargon === null ? 'null' : formData.is_jargon?.toString() || 'null'}
              onValueChange={(value) => setFormData({ ...formData, is_jargon: value === 'null' ? null : value === 'true' })}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="null">未判定</SelectItem>
                <SelectItem value="true">是黑话</SelectItem>
                <SelectItem value="false">非黑话</SelectItem>
              </SelectContent>
            </Select>
          </div>

          <div className="flex items-center space-x-2">
            <Switch
              id="edit_is_global"
              checked={formData.is_global}
              onCheckedChange={(checked) => setFormData({ ...formData, is_global: checked })}
            />
            <Label htmlFor="edit_is_global">全局黑话</Label>
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>取消</Button>
          <Button onClick={handleSave} disabled={saving}>
            {saving ? '保存中...' : '保存'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
