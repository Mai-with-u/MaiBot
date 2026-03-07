import { ClipboardCheck, MessageSquare, Plus, Search, Trash2 } from 'lucide-react'
import { useEffect, useState } from 'react'

import { Button } from '@/components/ui/button'
import { ExpressionReviewer } from '@/components/expression-reviewer'
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
import { useToast } from '@/hooks/use-toast'

import {
  batchDeleteExpressions,
  deleteExpression,
  getChatList,
  getExpressionDetail,
  getExpressionList,
  getExpressionStats,
  getReviewStats,
} from '@/lib/expression-api'

import {
  BatchDeleteConfirmDialog,
  DeleteConfirmDialog,
  ExpressionCreateDialog,
  ExpressionDetailDialog,
  ExpressionEditDialog,
} from './ExpressionDialogs'
import { ExpressionList } from './ExpressionList'

import type { ChatInfo, Expression } from '@/types/expression'
import type { StatsData } from './types'

/**
 * 表达方式管理主页面
 */
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
  const [stats, setStats] = useState<StatsData>({ total: 0, recent_7days: 0, chat_count: 0, top_chats: {} })
  const [chatList, setChatList] = useState<ChatInfo[]>([])
  const [chatNameMap, setChatNameMap] = useState<Map<string, string>>(new Map())
  const [isReviewerOpen, setIsReviewerOpen] = useState(false)
  const [uncheckedCount, setUncheckedCount] = useState(0)
  const { toast } = useToast()

  // 加载表达方式列表
  const loadExpressions = async () => {
    try {
      setLoading(true)
      const result = await getExpressionList({
        page,
        page_size: pageSize,
        search: search || undefined,
      })
      if (result.success) {
        setExpressions(result.data.data)
        setTotal(result.data.total)
      } else {
        toast({
          title: '加载失败',
          description: result.error,
          variant: 'destructive',
        })
      }
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
      const result = await getExpressionStats()
      if (result.success) {
        setStats(result.data)
      } else {
        console.error('加载统计数据失败:', result.error)
      }
    } catch (error) {
      console.error('加载统计数据失败:', error)
    }
  }

  // 加载审核统计
  const loadReviewStats = async () => {
    try {
      const result = await getReviewStats()
      if (result.success) {
        setUncheckedCount(result.data.unchecked)
      }
    } catch (error) {
      console.error('加载审核统计失败:', error)
    }
  }

  // 加载聚天列表
  const loadChatList = async () => {
    try {
      const result = await getChatList()
      if (result.success) {
        setChatList(result.data)
        const nameMap = new Map<string, string>()
        result.data.forEach((chat: ChatInfo) => {
          nameMap.set(chat.chat_id, chat.chat_name)
        })
        setChatNameMap(nameMap)
      }
    } catch (error) {
      console.error('加载聚天列表失败:', error)
    }
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
      const result = await getExpressionDetail(expression.id)
      if (result.success) {
        setSelectedExpression(result.data)
        setIsDetailDialogOpen(true)
      } else {
        toast({
          title: '加载详情失败',
          description: result.error,
          variant: 'destructive',
        })
      }
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
  const handleDelete = async () => {
    if (!deleteConfirmExpression) return
    try {
      const result = await deleteExpression(deleteConfirmExpression.id)
      if (result.success) {
        toast({
          title: '删除成功',
          description: `已删除表达方式: ${deleteConfirmExpression.situation}`,
        })
        setDeleteConfirmExpression(null)
        loadExpressions()
        loadStats()
      } else {
        toast({
          title: '删除失败',
          description: result.error,
          variant: 'destructive',
        })
      }
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
      const result = await batchDeleteExpressions(Array.from(selectedIds))
      if (result.success) {
        toast({
          title: '批量删除成功',
          description: `已删除 ${selectedIds.size} 个表达方式`,
        })
        setSelectedIds(new Set())
        setIsBatchDeleteDialogOpen(false)
        loadExpressions()
        loadStats()
      } else {
        toast({
          title: '批量删除失败',
          description: result.error,
          variant: 'destructive',
        })
      }
    } catch (error) {
      toast({
        title: '批量删除失败',
        description: error instanceof Error ? error.message : '无法批量删除表达方式',
        variant: 'destructive',
      })
    }
  }

  // 页面跳转
  const handleJumpToPage = (jumpToPage: string) => {
    const targetPage = parseInt(jumpToPage)
    const totalPages = Math.ceil(total / pageSize)
    if (targetPage >= 1 && targetPage <= totalPages) {
      setPage(targetPage)
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
      <ExpressionList
        expressions={expressions}
        loading={loading}
        total={total}
        page={page}
        pageSize={pageSize}
        selectedIds={selectedIds}
        chatNameMap={chatNameMap}
        onEdit={handleEdit}
        onViewDetail={handleViewDetail}
        onDelete={(expression) => setDeleteConfirmExpression(expression)}
        onToggleSelect={toggleSelect}
        onToggleSelectAll={toggleSelectAll}
        onPageChange={setPage}
        onJumpToPage={handleJumpToPage}
      />

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
      <DeleteConfirmDialog
        expression={deleteConfirmExpression}
        open={!!deleteConfirmExpression}
        onOpenChange={() => setDeleteConfirmExpression(null)}
        onConfirm={handleDelete}
      />

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
            loadExpressions()
            loadStats()
            loadReviewStats()
          }
        }}
      />
    </div>
  )
}
