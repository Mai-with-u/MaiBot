import { MessageSquare, Search, Edit, Trash2, Eye, Plus, Clock, Hash, ChevronLeft, ChevronRight, ChevronsLeft, ChevronsRight, Info, CheckCircle2, XCircle, Circle, ClipboardCheck } from 'lucide-react'
import { useState, useEffect } from 'react'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { ScrollArea } from '@/components/ui/scroll-area'
import { useToast } from '@/hooks/use-toast'
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
import { Alert, AlertDescription } from '@/components/ui/alert'
import type { Expression, ExpressionCreateRequest, ExpressionUpdateRequest, ChatInfo } from '@/types/expression'
import { getExpressionList, getExpressionDetail, createExpression, updateExpression, deleteExpression, batchDeleteExpressions, getExpressionStats, getChatList, getReviewStats } from '@/lib/expression-api'
import { ExpressionReviewer } from '@/components/expression-reviewer'

export function ExpressionManagementPage() {
  const [expressions, setExpressions] = useState<Expression[]>([])
  const [loading, setLoading] = useState(true)
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [search, setSearch] = useState('')
  const [selectedExpression, setSelectedExpression] = useState<Expression | null>(null)
  const [isDetailDialogOpen, setIsDetailDialogOpen] = useState(false)
  const [isEditDialogOpen, setIsEditDialogOpen] = useState(false)
  const [isCreateDialogOpen, setIsCreateDialogOpen] = useState(false)
  const [deleteConfirmExpression, setDeleteConfirmExpression] = useState<Expression | null>(null)
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set())
  const [isBatchDeleteDialogOpen, setIsBatchDeleteDialogOpen] = useState(false)
  const [jumpToPage, setJumpToPage] = useState('')
  const [stats, setStats] = useState({ total: 0, recent_7days: 0, chat_count: 0, top_chats: {} as Record<string, number> })
  const [chatList, setChatList] = useState<ChatInfo[]>([])
  const [chatNameMap, setChatNameMap] = useState<Map<string, string>>(new Map())
  const [isReviewerOpen, setIsReviewerOpen] = useState(false)
  const [uncheckedCount, setUncheckedCount] = useState(0)
  const { toast } = useToast()

  // 加载表达方式列表
  const loadExpressions = async () => {
    try {
      setLoading(true)
      const response = await getExpressionList({
        page,
        page_size: pageSize,
        search: search || undefined,
      })
      setExpressions(response.data)
      setTotal(response.total)
    } catch (error) {
      toast({
        title: '加载失败',
        description: error instanceof Error ? error.message : '无法加载表达方式',
        variant: 'destructive',
      })
    } finally {
      setLoading(false)
    }
  }

  // 加载统计数据
  const loadStats = async () => {
    try {
      const response = await getExpressionStats()
      if (response?.data) {
        setStats(response.data)
      }
    } catch (error) {
      console.error('加载统计数据失败:', error)
    }
  }

  // 加载审核统计
  const loadReviewStats = async () => {
    try {
      const data = await getReviewStats()
      setUncheckedCount(data.unchecked)
    } catch (error) {
      console.error('加载审核统计失败:', error)
    }
  }

  // 加载聊天列表
  const loadChatList = async () => {
    try {
      const response = await getChatList()
      if (response?.data) {
        setChatList(response.data)
        // 构建聊天ID到名称的映射
        const nameMap = new Map<string, string>()
        response.data.forEach((chat) => {
          nameMap.set(chat.chat_id, chat.chat_name)
        })
        setChatNameMap(nameMap)
      }
    } catch (error) {
      console.error('加载聊天列表失败:', error)
    }
  }

  // 获取聊天名称（支持Unicode字符完整显示）
  const getChatName = (chatId: string): string => {
    return chatNameMap.get(chatId) || chatId
  }

  // 初始加载
  useEffect(() => {
    loadExpressions()
    loadReviewStats()
    loadStats()
    loadChatList()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page, pageSize, search])

  // 查看详情
  const handleViewDetail = async (expression: Expression) => {
    try {
      const response = await getExpressionDetail(expression.id)
      setSelectedExpression(response.data)
      setIsDetailDialogOpen(true)
    } catch (error) {
      toast({
        title: '加载详情失败',
        description: error instanceof Error ? error.message : '无法加载表达方式详情',
        variant: 'destructive',
      })
    }
  }

  // 编辑表达方式
  const handleEdit = (expression: Expression) => {
    setSelectedExpression(expression)
    setIsEditDialogOpen(true)
  }

  // 删除表达方式
  const handleDelete = async (expression: Expression) => {
    try {
      await deleteExpression(expression.id)
      toast({
        title: '删除成功',
        description: `已删除表达方式: ${expression.situation}`,
      })
      setDeleteConfirmExpression(null)
      loadExpressions()
      loadStats()
    } catch (error) {
      toast({
        title: '删除失败',
        description: error instanceof Error ? error.message : '无法删除表达方式',
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
    if (selectedIds.size === expressions.length && expressions.length > 0) {
      setSelectedIds(new Set())
    } else {
      setSelectedIds(new Set(expressions.map(e => e.id)))
    }
  }

  // 批量删除
  const handleBatchDelete = async () => {
    try {
      await batchDeleteExpressions(Array.from(selectedIds))
      toast({
        title: '批量删除成功',
        description: `已删除 ${selectedIds.size} 个表达方式`,
      })
      setSelectedIds(new Set())
      setIsBatchDeleteDialogOpen(false)
      loadExpressions()
      loadStats()
    } catch (error) {
      toast({
        title: '批量删除失败',
        description: error instanceof Error ? error.message : '无法批量删除表达方式',
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

  return (
    <div className="h-[calc(100vh-4rem)] flex flex-col p-4 sm:p-6">
      {/* 页面标题 */}
      <div className="mb-4 sm:mb-6">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
          <div>
            <h1 className="text-2xl sm:text-3xl font-bold flex items-center gap-2">
              <MessageSquare className="h-8 w-8" strokeWidth={2} />
              表达方式管理
            </h1>
            <p className="text-muted-foreground mt-1 text-sm sm:text-base">
              管理麦麦的表达方式和话术模板
            </p>
          </div>
          <div className="flex items-center gap-2">
            <Button 
              variant="outline" 
              onClick={() => setIsReviewerOpen(true)} 
              className="gap-2"
            >
              <ClipboardCheck className="h-4 w-4" />
              人工审核
              {uncheckedCount > 0 && (
                <span className="ml-1 px-1.5 py-0.5 text-xs rounded-full bg-orange-500 text-white">
                  {uncheckedCount > 99 ? '99+' : uncheckedCount}
                </span>
              )}
            </Button>
            <Button onClick={() => setIsCreateDialogOpen(true)} className="gap-2">
              <Plus className="h-4 w-4" />
              新增表达方式
            </Button>
          </div>
        </div>
      </div>

      <ScrollArea className="flex-1">
        <div className="space-y-4 sm:space-y-6 pr-4">

      {/* 统计卡片 */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <div className="rounded-lg border bg-card p-4">
          <div className="text-sm text-muted-foreground">总数量</div>
          <div className="text-2xl font-bold mt-1">{stats.total}</div>
        </div>
        <div className="rounded-lg border bg-card p-4">
          <div className="text-sm text-muted-foreground">近7天新增</div>
          <div className="text-2xl font-bold mt-1 text-green-600">{stats.recent_7days}</div>
        </div>
        <div className="rounded-lg border bg-card p-4">
          <div className="text-sm text-muted-foreground">关联聊天数</div>
          <div className="text-2xl font-bold mt-1 text-blue-600">{stats.chat_count}</div>
        </div>
      </div>

      {/* 搜索和批量操作 */}
      <div className="rounded-lg border bg-card p-4">
        <Label htmlFor="search">搜索</Label>
        <div className="flex flex-col sm:flex-row gap-2 mt-1.5">
          <div className="flex-1 relative">
            <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
            <Input
              id="search"
              placeholder="搜索情境、风格或上下文..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="pl-9"
            />
          </div>
        </div>

        {/* 批量操作工具栏 */}
        <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 mt-4 pt-4 border-t">
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            {selectedIds.size > 0 && (
              <span>已选择 {selectedIds.size} 个表达方式</span>
            )}
          </div>
          <div className="flex items-center gap-2">
            <Label htmlFor="page-size" className="text-sm whitespace-nowrap">每页显示</Label>
            <Select
              value={pageSize.toString()}
              onValueChange={(value) => {
                setPageSize(parseInt(value))
                setPage(1)
                setSelectedIds(new Set())
              }}
            >
              <SelectTrigger id="page-size" className="w-20">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="10">10</SelectItem>
                <SelectItem value="20">20</SelectItem>
                <SelectItem value="50">50</SelectItem>
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
                  onClick={() => setIsBatchDeleteDialogOpen(true)}
                >
                  <Trash2 className="h-4 w-4 mr-1" />
                  批量删除
                </Button>
              </>
            )}
          </div>
        </div>
      </div>

      {/* 表达方式列表 */}
      <div className="rounded-lg border bg-card">
        {/* 桌面端表格视图 */}
        <div className="hidden md:block">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-12">
                  <Checkbox
                    checked={selectedIds.size === expressions.length && expressions.length > 0}
                    onCheckedChange={toggleSelectAll}
                  />
                </TableHead>
                <TableHead>情境</TableHead>
                <TableHead>风格</TableHead>
                <TableHead>聊天</TableHead>
                <TableHead className="text-right">操作</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {loading ? (
                <TableRow>
                  <TableCell colSpan={5} className="text-center py-8 text-muted-foreground">
                    加载中...
                  </TableCell>
                </TableRow>
              ) : expressions.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={5} className="text-center py-8 text-muted-foreground">
                    暂无数据
                  </TableCell>
                </TableRow>
              ) : (
                expressions.map((expression) => (
                  <TableRow key={expression.id}>
                    <TableCell>
                      <Checkbox
                        checked={selectedIds.has(expression.id)}
                        onCheckedChange={() => toggleSelect(expression.id)}
                      />
                    </TableCell>
                    <TableCell className="font-medium max-w-xs truncate">
                      {expression.situation}
                    </TableCell>
                    <TableCell className="max-w-xs truncate">{expression.style}</TableCell>
                    <TableCell 
                      className="max-w-[200px] truncate" 
                      title={getChatName(expression.chat_id)}
                      style={{ wordBreak: 'keep-all' }}
                    >
                      <span className="whitespace-nowrap overflow-hidden text-ellipsis block">
                        {getChatName(expression.chat_id)}
                      </span>
                    </TableCell>
                    <TableCell className="text-right">
                      <div className="flex justify-end gap-2">
                        <Button
                          variant="default"
                          size="sm"
                          onClick={() => handleEdit(expression)}
                        >
                          <Edit className="h-4 w-4 mr-1" />
                          编辑
                        </Button>
                        <Button
                          variant="outline"
                          size="icon"
                          className="h-8 w-8"
                          onClick={() => handleViewDetail(expression)}
                          title="查看详情"
                        >
                          <Eye className="h-4 w-4" />
                        </Button>
                        <Button
                          size="sm"
                          onClick={() => setDeleteConfirmExpression(expression)}
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
            <div className="text-center py-8 text-muted-foreground">
              加载中...
            </div>
          ) : expressions.length === 0 ? (
            <div className="text-center py-8 text-muted-foreground">
              暂无数据
            </div>
          ) : (
            expressions.map((expression) => (
                  <div key={expression.id} className="rounded-lg border bg-card p-4 space-y-3 overflow-hidden">
                    {/* 复选框和情境 */}
                    <div className="flex items-start gap-3">
                      <Checkbox
                        checked={selectedIds.has(expression.id)}
                        onCheckedChange={() => toggleSelect(expression.id)}
                        className="mt-1"
                      />
                      <div className="min-w-0 flex-1 overflow-hidden space-y-2">
                        <div>
                          <div className="text-xs text-muted-foreground mb-1">情境</div>
                          <h3 className="font-semibold text-sm line-clamp-2 w-full break-all" title={expression.situation}>
                            {expression.situation}
                          </h3>
                        </div>
                        <div>
                          <div className="text-xs text-muted-foreground mb-1">风格</div>
                          <p className="text-sm line-clamp-2 w-full break-all" title={expression.style}>
                            {expression.style}
                          </p>
                        </div>
                      </div>
                    </div>

                    {/* 聊天名称 */}
                    <div className="text-sm">
                      <div className="text-xs text-muted-foreground mb-1">聊天</div>
                      <p 
                        className="text-sm truncate" 
                        title={getChatName(expression.chat_id)}
                        style={{ wordBreak: 'keep-all' }}
                      >
                        {getChatName(expression.chat_id)}
                      </p>
                    </div>

                    {/* 操作按钮 */}
                    <div className="flex flex-wrap gap-1 pt-2 border-t overflow-hidden">
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => handleEdit(expression)}
                        className="text-xs px-2 py-1 h-auto flex-shrink-0"
                      >
                        <Edit className="h-3 w-3 mr-1" />
                        编辑
                      </Button>
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => handleViewDetail(expression)}
                        className="text-xs px-2 py-1 h-auto flex-shrink-0"
                      >
                        <Eye className="h-3 w-3" />
                      </Button>
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => setDeleteConfirmExpression(expression)}
                        className="text-xs px-2 py-1 h-auto flex-shrink-0 text-destructive hover:text-destructive"
                      >
                        <Trash2 className="h-3 w-3 mr-1" />
                        删除
                      </Button>
                    </div>
                  </div>
                ))
              )}
        </div>

        {/* 分页 - 增强版 */}
        {total > 0 && (
          <div className="flex flex-col sm:flex-row items-center justify-between gap-4 px-4 py-3 border-t">
            <div className="text-sm text-muted-foreground">
              共 {total} 条记录，第 {page} / {Math.ceil(total / pageSize)} 页
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
                onClick={() => setPage(page - 1)}
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
                onClick={() => setPage(page + 1)}
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
      </div>

        </div>
      </ScrollArea>

      {/* 详情对话框 */}
      <ExpressionDetailDialog
        expression={selectedExpression}
        open={isDetailDialogOpen}
        onOpenChange={setIsDetailDialogOpen}
        chatNameMap={chatNameMap}
      />

      {/* 创建对话框 */}
      <ExpressionCreateDialog
        open={isCreateDialogOpen}
        onOpenChange={setIsCreateDialogOpen}
        chatList={chatList}
        onSuccess={() => {
          loadExpressions()
          loadStats()
          setIsCreateDialogOpen(false)
        }}
      />

      {/* 编辑对话框 */}
      <ExpressionEditDialog
        expression={selectedExpression}
        open={isEditDialogOpen}
        onOpenChange={setIsEditDialogOpen}
        chatList={chatList}
        onSuccess={() => {
          loadExpressions()
          loadStats()
          setIsEditDialogOpen(false)
        }}
      />

      {/* 删除确认对话框 */}
      <AlertDialog
        open={!!deleteConfirmExpression}
        onOpenChange={() => setDeleteConfirmExpression(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>确认删除</AlertDialogTitle>
            <AlertDialogDescription>
              确定要删除表达方式 "{deleteConfirmExpression?.situation}" 吗？
              此操作不可撤销。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => deleteConfirmExpression && handleDelete(deleteConfirmExpression)}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              删除
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* 批量删除确认对话框 */}
      <BatchDeleteConfirmDialog
        open={isBatchDeleteDialogOpen}
        onOpenChange={setIsBatchDeleteDialogOpen}
        onConfirm={handleBatchDelete}
        count={selectedIds.size}
      />

      {/* 表达方式审核器 */}
      <ExpressionReviewer
        open={isReviewerOpen}
        onOpenChange={(open) => {
          setIsReviewerOpen(open)
          if (!open) {
            // 关闭审核器时刷新列表和统计
            loadExpressions()
            loadStats()
            loadReviewStats()
          }
        }}
      />
    </div>
  )
}

// 表达方式详情对话框
function ExpressionDetailDialog({
  expression,
  open,
  onOpenChange,
  chatNameMap,
}: {
  expression: Expression | null
  open: boolean
  onOpenChange: (open: boolean) => void
  chatNameMap: Map<string, string>
}) {
  if (!expression) return null

  const formatTime = (timestamp: number | null) => {
    if (!timestamp) return '-'
    return new Date(timestamp * 1000).toLocaleString('zh-CN')
  }

  const getChatName = (chatId: string): string => {
    return chatNameMap.get(chatId) || chatId
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl max-h-[80vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>表达方式详情</DialogTitle>
          <DialogDescription>
            查看表达方式的完整信息
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <InfoItem label="情境" value={expression.situation} />
            <InfoItem label="风格" value={expression.style} />
            <InfoItem 
              label="聊天" 
              value={getChatName(expression.chat_id)} 
            />
            <InfoItem icon={Hash} label="记录ID" value={expression.id.toString()} mono />
          </div>

          <div className="grid grid-cols-2 gap-4">
            <InfoItem icon={Clock} label="创建时间" value={formatTime(expression.create_date)} />
          </div>

          {/* 状态标记 */}
          <div className="rounded-lg border bg-muted/50 p-4">
            <Label className="text-xs text-muted-foreground mb-3 block">状态标记</Label>
            <div className="grid grid-cols-2 gap-4">
              <div className="flex items-center gap-2">
                <div className={cn(
                  "flex h-8 w-8 items-center justify-center rounded-full",
                  expression.checked ? "bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400" : "bg-gray-100 text-gray-400 dark:bg-gray-800 dark:text-gray-600"
                )}>
                  {expression.checked ? (
                    <CheckCircle2 className="h-5 w-5" />
                  ) : (
                    <Circle className="h-5 w-5" />
                  )}
                </div>
                <div>
                  <p className="text-sm font-medium">已检查</p>
                  <p className="text-xs text-muted-foreground">
                    {expression.checked ? "已通过审核" : "未审核"}
                  </p>
                </div>
              </div>
              <div className="flex items-center gap-2">
                <div className={cn(
                  "flex h-8 w-8 items-center justify-center rounded-full",
                  expression.rejected ? "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400" : "bg-gray-100 text-gray-400 dark:bg-gray-800 dark:text-gray-600"
                )}>
                  {expression.rejected ? (
                    <XCircle className="h-5 w-5" />
                  ) : (
                    <Circle className="h-5 w-5" />
                  )}
                </div>
                <div>
                  <p className="text-sm font-medium">已拒绝</p>
                  <p className="text-xs text-muted-foreground">
                    {expression.rejected ? "不会被使用" : "正常"}
                  </p>
                </div>
              </div>
            </div>
          </div>
        </div>

        <DialogFooter>
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

// 表达方式创建对话框
function ExpressionCreateDialog({
  open,
  onOpenChange,
  chatList,
  onSuccess,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  chatList: ChatInfo[]
  onSuccess: () => void
}) {
  const [formData, setFormData] = useState<ExpressionCreateRequest>({
    situation: '',
    style: '',
    chat_id: '',
  })
  const [saving, setSaving] = useState(false)
  const { toast } = useToast()

  const handleCreate = async () => {
    if (!formData.situation || !formData.style || !formData.chat_id) {
      toast({
        title: '验证失败',
        description: '请填写必填字段：情境、风格和聊天',
        variant: 'destructive',
      })
      return
    }

    try {
      setSaving(true)
      await createExpression(formData)
      toast({
        title: '创建成功',
        description: '表达方式已创建',
      })
      // 重置表单
      setFormData({
        situation: '',
        style: '',
        chat_id: '',
      })
      onSuccess()
    } catch (error) {
      toast({
        title: '创建失败',
        description: error instanceof Error ? error.message : '无法创建表达方式',
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
          <DialogTitle>新增表达方式</DialogTitle>
          <DialogDescription>
            创建新的表达方式记录
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label htmlFor="situation">
                情境 <span className="text-destructive">*</span>
              </Label>
              <Input
                id="situation"
                value={formData.situation}
                onChange={(e) => setFormData({ ...formData, situation: e.target.value })}
                placeholder="描述使用场景"
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="style">
                风格 <span className="text-destructive">*</span>
              </Label>
              <Input
                id="style"
                value={formData.style}
                onChange={(e) => setFormData({ ...formData, style: e.target.value })}
                placeholder="描述表达风格"
              />
            </div>
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
                    <span className="truncate" style={{ wordBreak: 'keep-all' }}>
                      {chat.chat_name}
                      {chat.is_group && <span className="text-muted-foreground ml-1">(群聊)</span>}
                    </span>
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button onClick={handleCreate} disabled={saving}>
            {saving ? '创建中...' : '创建'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

// 表达方式编辑对话框
function ExpressionEditDialog({
  expression,
  open,
  onOpenChange,
  chatList,
  onSuccess,
}: {
  expression: Expression | null
  open: boolean
  onOpenChange: (open: boolean) => void
  chatList: ChatInfo[]
  onSuccess: () => void
}) {
  const [formData, setFormData] = useState<ExpressionUpdateRequest>({})
  const [saving, setSaving] = useState(false)
  const { toast } = useToast()

  useEffect(() => {
    if (expression) {
      setFormData({
        situation: expression.situation,
        style: expression.style,
        chat_id: expression.chat_id,
        checked: expression.checked,
        rejected: expression.rejected,
      })
    }
  }, [expression])

  const handleSave = async () => {
    if (!expression) return

    try {
      setSaving(true)
      await updateExpression(expression.id, formData)
      toast({
        title: '保存成功',
        description: '表达方式已更新',
      })
      onSuccess()
    } catch (error) {
      toast({
        title: '保存失败',
        description: error instanceof Error ? error.message : '无法更新表达方式',
        variant: 'destructive',
      })
    } finally {
      setSaving(false)
    }
  }

  if (!expression) return null

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl max-h-[80vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>编辑表达方式</DialogTitle>
          <DialogDescription>
            修改表达方式的信息
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label htmlFor="edit_situation">情境</Label>
              <Input
                id="edit_situation"
                value={formData.situation || ''}
                onChange={(e) => setFormData({ ...formData, situation: e.target.value })}
                placeholder="描述使用场景"
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="edit_style">风格</Label>
              <Input
                id="edit_style"
                value={formData.style || ''}
                onChange={(e) => setFormData({ ...formData, style: e.target.value })}
                placeholder="描述表达风格"
              />
            </div>
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
                    <span className="truncate" style={{ wordBreak: 'keep-all' }}>
                      {chat.chat_name}
                      {chat.is_group && <span className="text-muted-foreground ml-1">(群聊)</span>}
                    </span>
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {/* 状态标记 */}
          <Alert>
            <Info className="h-4 w-4" />
            <AlertDescription className="text-xs">
              <div className="space-y-1">
                <p><strong>状态标记说明：</strong></p>
                <p>• 已检查：表示该表达方式已通过审核（可由AI自动检查或人工审核）</p>
                <p>• 已拒绝：表示该表达方式被标记为不合适，将永远不会被使用</p>
                <p className="text-muted-foreground mt-2">
                  根据配置中"仅使用已审核通过的表达方式"设置：<br/>
                  • 开启时：只有通过审核（已检查）的项目会被使用<br/>
                  • 关闭时：未审核的项目也会被使用
                </p>
              </div>
            </AlertDescription>
          </Alert>

          <div className="grid grid-cols-2 gap-4">
            <div className="flex items-center justify-between space-x-2 rounded-lg border p-4">
              <div className="space-y-0.5">
                <Label htmlFor="edit_checked" className="text-sm font-medium">
                  已检查
                </Label>
                <p className="text-xs text-muted-foreground">
                  已通过审核
                </p>
              </div>
              <Switch
                id="edit_checked"
                checked={formData.checked ?? false}
                onCheckedChange={(checked) => setFormData({ ...formData, checked })}
              />
            </div>

            <div className="flex items-center justify-between space-x-2 rounded-lg border p-4">
              <div className="space-y-0.5">
                <Label htmlFor="edit_rejected" className="text-sm font-medium">
                  已拒绝
                </Label>
                <p className="text-xs text-muted-foreground">
                  不会被使用
                </p>
              </div>
              <Switch
                id="edit_rejected"
                checked={formData.rejected ?? false}
                onCheckedChange={(rejected) => setFormData({ ...formData, rejected })}
              />
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

// 批量删除确认对话框
function BatchDeleteConfirmDialog({
  open,
  onOpenChange,
  onConfirm,
  count,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  onConfirm: () => void
  count: number
}) {
  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>确认批量删除</AlertDialogTitle>
          <AlertDialogDescription>
            您即将删除 {count} 个表达方式，此操作无法撤销。确定要继续吗？
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>取消</AlertDialogCancel>
          <AlertDialogAction onClick={onConfirm} className="bg-destructive text-destructive-foreground hover:bg-destructive/90">
            确认删除
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
