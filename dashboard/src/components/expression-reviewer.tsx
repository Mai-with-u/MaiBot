/**
 * 表达方式审核器弹窗组件
 * 
 * 功能：
 * 1. 分页显示待审核/已通过/已拒绝的表达方式
 * 2. 支持单条通过/拒绝
 * 3. 支持批量操作
 * 4. 冲突检测（防止与AI自动检查冲突）
 */

import { useState, useEffect, useCallback, useRef } from 'react'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Badge } from '@/components/ui/badge'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Checkbox } from '@/components/ui/checkbox'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import {
  Pagination,
  PaginationContent,
  PaginationEllipsis,
  PaginationItem,
  PaginationLink,
} from '@/components/ui/pagination'
import { useToast } from '@/hooks/use-toast'
import {
  CheckCircle2,
  XCircle,
  Clock,
  Search,
  RefreshCw,
  ChevronLeft,
  ChevronRight,
  Bot,
  User,
  AlertCircle,
  List,
  Zap,
  X,
  Ban,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import {
  getReviewStats,
  getReviewList,
  batchReviewExpressions,
  getChatList,
} from '@/lib/expression-api'
import type { Expression, ReviewStats, ChatInfo, BatchReviewItem } from '@/types/expression'

interface ExpressionReviewerProps {
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function ExpressionReviewer({ open, onOpenChange }: ExpressionReviewerProps) {
  // 审核模式：list（列表模式）或 quick（快速审核模式）
  const [reviewMode, setReviewMode] = useState<'list' | 'quick'>('list')
  const [stats, setStats] = useState<ReviewStats | null>(null)
  const [expressions, setExpressions] = useState<Expression[]>([])
  
  // 快速审核模式状态
  const [quickFilterType, setQuickFilterType] = useState<'unchecked' | 'passed' | 'rejected' | 'all'>('unchecked')
  const [quickExpressions, setQuickExpressions] = useState<Expression[]>([])
  const [quickCurrentIndex, setQuickCurrentIndex] = useState(0)
  const [quickLoading, setQuickLoading] = useState(false)
  const [quickTotal, setQuickTotal] = useState(0)
  const [quickPage, setQuickPage] = useState(1)
  const [swipeDirection, setSwipeDirection] = useState<'left' | 'right' | null>(null)
  const [swipeOffset, setSwipeOffset] = useState(0)
  const [isAnimating, setIsAnimating] = useState(false)
  const [conflictId, setConflictId] = useState<number | null>(null)
  const cardRef = useRef<HTMLDivElement>(null)
  const dragStartRef = useRef<{ x: number; y: number } | null>(null)
  const isDraggingRef = useRef(false)
  const [loading, setLoading] = useState(false)
  const [statsLoading, setStatsLoading] = useState(false)
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [jumpPage, setJumpPage] = useState('')
  const [filterType, setFilterType] = useState<'unchecked' | 'passed' | 'rejected' | 'all'>('unchecked')
  const [search, setSearch] = useState('')
  const [searchInput, setSearchInput] = useState('')
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set())
  const [processingIds, setProcessingIds] = useState<Set<number>>(new Set())
  const [chatNameMap, setChatNameMap] = useState<Map<string, string>>(new Map())
  const { toast } = useToast()

  // 加载统计数据
  const loadStats = useCallback(async () => {
    try {
      setStatsLoading(true)
      const data = await getReviewStats()
      setStats(data)
    } catch (error) {
      console.error('加载统计失败:', error)
    } finally {
      setStatsLoading(false)
    }
  }, [])

  // 加载列表
  const loadList = useCallback(async () => {
    try {
      setLoading(true)
      const response = await getReviewList({
        page,
        page_size: pageSize,
        filter_type: filterType,
        search: search || undefined,
      })
      setExpressions(response.data)
      setTotal(response.total)
    } catch (error) {
      toast({
        title: '加载失败',
        description: error instanceof Error ? error.message : '无法加载列表',
        variant: 'destructive',
      })
    } finally {
      setLoading(false)
    }
  }, [page, pageSize, filterType, search, toast])

  // 加载聊天名称映射
  const loadChatNames = useCallback(async () => {
    try {
      const response = await getChatList()
      if (response?.data) {
        const nameMap = new Map<string, string>()
        response.data.forEach((chat: ChatInfo) => {
          nameMap.set(chat.chat_id, chat.chat_name)
        })
        setChatNameMap(nameMap)
      }
    } catch (error) {
      console.error('加载聊天名称失败:', error)
    }
  }, [])

  // 快速审核模式 - 加载数据
  const loadQuickList = useCallback(async (resetIndex = true, append = false) => {
    try {
      setQuickLoading(true)
      const pageToLoad = append ? quickPage + 1 : quickPage
      const response = await getReviewList({
        page: pageToLoad,
        page_size: 20,
        filter_type: quickFilterType,
      })
      
      if (append) {
        // 追加模式：拼接数据
        setQuickExpressions(prev => [...prev, ...response.data])
        setQuickPage(pageToLoad)
      } else {
        // 替换模式
        setQuickExpressions(response.data)
      }
      
      setQuickTotal(response.total)
      if (resetIndex) {
        setQuickCurrentIndex(0)
      }
    } catch (error) {
      toast({
        title: '加载失败',
        description: error instanceof Error ? error.message : '无法加载列表',
        variant: 'destructive',
      })
    } finally {
      setQuickLoading(false)
    }
  }, [quickPage, quickFilterType, toast])

  // 快速审核模式 - 切换筛选时重置
  useEffect(() => {
    if (reviewMode === 'quick') {
      setQuickPage(1)
      setQuickCurrentIndex(0)
    }
  }, [quickFilterType, reviewMode])

  // 快速审核模式 - 加载数据
  useEffect(() => {
    if (open && reviewMode === 'quick') {
      loadQuickList()
      loadStats()
    }
  }, [open, reviewMode, quickPage, quickFilterType, loadQuickList, loadStats])

  // 获取当前卡片允许的滑动方向
  const getAllowedDirections = useCallback((expr: Expression | undefined) => {
    if (!expr) return { left: false, right: false }
    
    if (quickFilterType === 'unchecked') {
      // 待审核：左拒绝，右通过
      return { left: true, right: true }
    } else if (quickFilterType === 'passed') {
      // 已通过：只能左滑改为拒绝
      return { left: true, right: false }
    } else if (quickFilterType === 'rejected') {
      // 已拒绝：只能右滑改为通过
      return { left: false, right: true }
    } else {
      // 全部：智能判断
      if (!expr.checked) {
        // 未审核：双向
        return { left: true, right: true }
      } else if (expr.rejected) {
        // 已拒绝：只能右滑
        return { left: false, right: true }
      } else {
        // 已通过：只能左滑
        return { left: true, right: false }
      }
    }
  }, [quickFilterType])

  // 快速审核 - 执行审核操作
  const handleQuickReview = useCallback(async (rejected: boolean) => {
    const currentExpr = quickExpressions[quickCurrentIndex]
    if (!currentExpr || isAnimating) return

    const directions = getAllowedDirections(currentExpr)
    if ((rejected && !directions.left) || (!rejected && !directions.right)) {
      return
    }

    setIsAnimating(true)
    setSwipeDirection(rejected ? 'left' : 'right')
    setSwipeOffset(rejected ? -400 : 400)

    try {
      const response = await batchReviewExpressions([{
        id: currentExpr.id,
        rejected,
        require_unchecked: quickFilterType === 'unchecked',
      }])

      if (response.results[0]?.success) {
        toast({
          title: rejected ? '已拒绝' : '已通过',
          description: `表达方式 #${currentExpr.id} ${rejected ? '已拒绝' : '已通过'}`,
        })
        
        // 从列表中移除当前项
        setTimeout(() => {
          setQuickExpressions(prev => prev.filter((_, i) => i !== quickCurrentIndex))
          setQuickTotal(prev => prev - 1)
          
          // 如果当前索引超出范围，调整索引
          if (quickCurrentIndex >= quickExpressions.length - 1) {
            setQuickCurrentIndex(Math.max(0, quickCurrentIndex - 1))
          }
          
          // 重置状态
          setSwipeDirection(null)
          setSwipeOffset(0)
          setIsAnimating(false)
          
          // 刷新统计
          loadStats()
          
          // 如果列表为空且还有更多数据，加载下一页
          if (quickExpressions.length <= 1 && quickTotal > 1) {
            loadQuickList(false)
          }
        }, 300)
      } else {
        // 冲突处理
        setConflictId(currentExpr.id)
        toast({
          title: '数据冲突',
          description: '该条目已被后台任务处理，正在刷新数据...',
          variant: 'destructive',
        })
        
        // 播放冲突动画后刷新
        setTimeout(() => {
          setConflictId(null)
          setSwipeDirection(null)
          setSwipeOffset(0)
          setIsAnimating(false)
          loadQuickList(false) // 重新加载当前页
          loadStats()
        }, 1500)
      }
    } catch (error) {
      toast({
        title: '操作失败',
        description: error instanceof Error ? error.message : '未知错误',
        variant: 'destructive',
      })
      setSwipeDirection(null)
      setSwipeOffset(0)
      setIsAnimating(false)
    }
  }, [quickExpressions, quickCurrentIndex, isAnimating, getAllowedDirections, quickFilterType, toast, loadStats, quickTotal, loadQuickList])

  // 拖拽开始
  const handleDragStart = useCallback((clientX: number, clientY: number) => {
    if (isAnimating) return
    dragStartRef.current = { x: clientX, y: clientY }
    isDraggingRef.current = false
  }, [isAnimating])

  // 触发无效操作动画
  const triggerInvalidAnimation = useCallback((direction: 'left' | 'right') => {
    if (isAnimating) return
    setIsAnimating(true)
    // 模拟向该方向移动一点
    setSwipeOffset(direction === 'left' ? -30 : 30)
    
    setTimeout(() => {
      setSwipeOffset(0)
      setTimeout(() => setIsAnimating(false), 300)
    }, 150)
  }, [isAnimating])

  // 拖拽移动
  const handleDragMove = useCallback((clientX: number) => {
    if (!dragStartRef.current || isAnimating) return
    
    const deltaX = clientX - dragStartRef.current.x
    const currentExpr = quickExpressions[quickCurrentIndex]
    const directions = getAllowedDirections(currentExpr)
    
    // 检查方向限制
    if (deltaX < 0 && !directions.left) {
      setSwipeOffset(deltaX * 0.2) // 提供阻力反馈
      setSwipeDirection(null)
      return
    }
    if (deltaX > 0 && !directions.right) {
      setSwipeOffset(deltaX * 0.2)
      setSwipeDirection(null)
      return
    }

    isDraggingRef.current = true
    setSwipeOffset(deltaX)
    
    if (Math.abs(deltaX) > 50) {
      setSwipeDirection(deltaX > 0 ? 'right' : 'left')
    } else {
      setSwipeDirection(null)
    }
  }, [quickExpressions, quickCurrentIndex, getAllowedDirections, isAnimating])

  // 拖拽结束
  const handleDragEnd = useCallback(() => {
    if (!dragStartRef.current) return
    
    const threshold = 100
    if (Math.abs(swipeOffset) > threshold && swipeDirection) {
      handleQuickReview(swipeDirection === 'left')
    } else {
      // 回弹
      setSwipeOffset(0)
      setSwipeDirection(null)
    }
    
    dragStartRef.current = null
    isDraggingRef.current = false
  }, [swipeOffset, swipeDirection, handleQuickReview])

  // 鼠标事件处理
  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    handleDragStart(e.clientX, e.clientY)
  }, [handleDragStart])

  const handleMouseMove = useCallback((e: React.MouseEvent) => {
    if (dragStartRef.current) {
      e.preventDefault()
      handleDragMove(e.clientX)
    }
  }, [handleDragMove])

  const handleMouseUp = useCallback(() => {
    handleDragEnd()
  }, [handleDragEnd])

  const handleMouseLeave = useCallback(() => {
    if (dragStartRef.current) {
      handleDragEnd()
    }
  }, [handleDragEnd])

  // 触摸事件处理
  const handleTouchStart = useCallback((e: React.TouchEvent) => {
    const touch = e.touches[0]
    handleDragStart(touch.clientX, touch.clientY)
  }, [handleDragStart])

  const handleTouchMove = useCallback((e: React.TouchEvent) => {
    const touch = e.touches[0]
    handleDragMove(touch.clientX)
  }, [handleDragMove])

  const handleTouchEnd = useCallback(() => {
    handleDragEnd()
  }, [handleDragEnd])

  // 键盘事件处理
  useEffect(() => {
    if (!open || reviewMode !== 'quick') return

    const handleKeyDown = (e: KeyboardEvent) => {
      // 只处理方向键
      if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(e.key)) return
      
      // 阻止事件继续传播，避免被 Tabs 组件捕获
      e.preventDefault()
      e.stopPropagation()
      e.stopImmediatePropagation()
      
      if (isAnimating || quickLoading) return
      
      const currentExpr = quickExpressions[quickCurrentIndex]
      const directions = getAllowedDirections(currentExpr)

      if (e.key === 'ArrowLeft') {
        if (directions.left) {
          handleQuickReview(true) // 拒绝
        } else {
          triggerInvalidAnimation('left')
        }
      } else if (e.key === 'ArrowRight') {
        if (directions.right) {
          handleQuickReview(false) // 通过
        } else {
          triggerInvalidAnimation('right')
        }
      } else if (e.key === 'ArrowDown') {
        // 跳过当前项
        if (quickCurrentIndex < quickExpressions.length - 1) {
          setQuickCurrentIndex(prev => prev + 1)
        }
      } else if (e.key === 'ArrowUp') {
        // 返回上一项
        if (quickCurrentIndex > 0) {
          setQuickCurrentIndex(prev => prev - 1)
        }
      }
    }

    // 使用 capture 模式，在事件到达 Tabs 之前拦截
    window.addEventListener('keydown', handleKeyDown, true)
    return () => window.removeEventListener('keydown', handleKeyDown, true)
  }, [open, reviewMode, quickExpressions, quickCurrentIndex, isAnimating, quickLoading, getAllowedDirections, handleQuickReview, triggerInvalidAnimation])

  // 动态加载更多数据 - 当接近列表末尾时自动加载
  useEffect(() => {
    if (!open || reviewMode !== 'quick' || quickLoading) return
    
    // 距离末尾还有5个或更少时，且还有更多数据时，自动加载
    const remaining = quickExpressions.length - quickCurrentIndex - 1
    const hasMoreData = quickExpressions.length < quickTotal
    
    if (remaining <= 5 && hasMoreData) {
      loadQuickList(false, true) // 追加模式
    }
  }, [open, reviewMode, quickCurrentIndex, quickExpressions.length, quickTotal, quickLoading, loadQuickList])

  // 初始加载
  useEffect(() => {
    if (open) {
      loadStats()
      loadList()
      loadChatNames()
    }
  }, [open, loadStats, loadList, loadChatNames])

  // 切换筛选时重置页码
  useEffect(() => {
    setPage(1)
    setSelectedIds(new Set())
  }, [filterType, search])

  // 列表加载时清空选择
  useEffect(() => {
    setSelectedIds(new Set())
  }, [expressions])

  // 搜索处理
  const handleSearch = () => {
    setSearch(searchInput)
    setPage(1)
  }

  // 获取聊天名称
  const getChatName = (chatId: string): string => {
    return chatNameMap.get(chatId) || chatId
  }

  // 单条审核
  const handleReview = async (id: number, rejected: boolean) => {
    try {
      setProcessingIds((prev) => new Set(prev).add(id))
      
      const response = await batchReviewExpressions([
        { id, rejected, require_unchecked: filterType === 'unchecked' }
      ])
      
      if (response.results[0]?.success) {
        toast({
          title: rejected ? '已拒绝' : '已通过',
          description: `表达方式 #${id} ${rejected ? '已拒绝' : '已通过'}`,
        })
        // 刷新列表和统计
        loadList()
        loadStats()
      } else {
        toast({
          title: '操作失败',
          description: response.results[0]?.message || '未知错误',
          variant: 'destructive',
        })
      }
    } catch (error) {
      toast({
        title: '操作失败',
        description: error instanceof Error ? error.message : '未知错误',
        variant: 'destructive',
      })
    } finally {
      setProcessingIds((prev) => {
        const next = new Set(prev)
        next.delete(id)
        return next
      })
    }
  }

  // 批量审核
  const handleBatchReview = async (rejected: boolean) => {
    if (selectedIds.size === 0) {
      toast({
        title: '请选择',
        description: '请先选择要审核的表达方式',
        variant: 'destructive',
      })
      return
    }

    try {
      setLoading(true)
      
      const items: BatchReviewItem[] = Array.from(selectedIds).map((id) => ({
        id,
        rejected,
        require_unchecked: filterType === 'unchecked',
      }))
      
      const response = await batchReviewExpressions(items)
      
      toast({
        title: '批量审核完成',
        description: `成功 ${response.succeeded} 条，失败 ${response.failed} 条`,
        variant: response.failed > 0 ? 'destructive' : 'default',
      })
      
      // 清空选择并刷新
      setSelectedIds(new Set())
      loadList()
      loadStats()
    } catch (error) {
      toast({
        title: '批量审核失败',
        description: error instanceof Error ? error.message : '未知错误',
        variant: 'destructive',
      })
    } finally {
      setLoading(false)
    }
  }

  // 全选/取消全选
  const handleSelectAll = () => {
    if (selectedIds.size === expressions.length) {
      setSelectedIds(new Set())
    } else {
      setSelectedIds(new Set(expressions.map((e) => e.id)))
    }
  }

  // 切换选择
  const toggleSelect = (id: number) => {
    setSelectedIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) {
        next.delete(id)
      } else {
        next.add(id)
      }
      return next
    })
  }

  // 格式化时间
  const formatTime = (timestamp: number | null) => {
    if (!timestamp) return '-'
    return new Date(timestamp * 1000).toLocaleString('zh-CN', {
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    })
  }

  // 获取状态标签
  const getStatusBadge = (expr: Expression) => {
    if (!expr.checked) {
      return (
        <Badge variant="outline" className="gap-1">
          <Clock className="h-3 w-3" />
          待审核
        </Badge>
      )
    }
    if (expr.rejected) {
      return (
        <Badge variant="destructive" className="gap-1">
          <XCircle className="h-3 w-3" />
          已拒绝
        </Badge>
      )
    }
    return (
      <Badge variant="default" className="gap-1 bg-green-600">
        <CheckCircle2 className="h-3 w-3" />
        已通过
      </Badge>
    )
  }

  // 获取修改者标签
  const getModifierBadge = (modifier: string | null) => {
    if (!modifier) return null
    if (modifier === 'ai') {
      return (
        <Badge variant="secondary" className="gap-1 text-xs">
          <Bot className="h-3 w-3" />
          AI
        </Badge>
      )
    }
    return (
      <Badge variant="secondary" className="gap-1 text-xs">
        <User className="h-3 w-3" />
        人工
      </Badge>
    )
  }

  const totalPages = Math.ceil(total / pageSize)

  // 生成页码数组
  const getPageNumbers = () => {
    const pages: (number | 'ellipsis')[] = []
    if (totalPages <= 7) {
      // 总页数不多，全部显示
      for (let i = 1; i <= totalPages; i++) {
        pages.push(i)
      }
    } else {
      // 总是显示第一页
      pages.push(1)
      
      if (page > 3) {
        pages.push('ellipsis')
      }
      
      // 当前页附近的页码
      const start = Math.max(2, page - 1)
      const end = Math.min(totalPages - 1, page + 1)
      
      for (let i = start; i <= end; i++) {
        pages.push(i)
      }
      
      if (page < totalPages - 2) {
        pages.push('ellipsis')
      }
      
      // 总是显示最后一页
      if (totalPages > 1) {
        pages.push(totalPages)
      }
    }
    return pages
  }

  // 处理页码跳转
  const handleJumpPage = () => {
    const targetPage = parseInt(jumpPage, 10)
    if (!isNaN(targetPage) && targetPage >= 1 && targetPage <= totalPages) {
      setPage(targetPage)
      setJumpPage('')
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-5xl w-[95vw] sm:w-full h-[90vh] sm:h-[85vh] flex flex-col p-0" hideCloseButton>
        {/* 浏览器标签页风格的模式切换器 */}
        <div className="flex items-end bg-muted/30 px-2 pt-2 shrink-0">
          {/* 列表模式标签 */}
          <button
            onClick={() => setReviewMode('list')}
            className={cn(
              'group relative flex items-center gap-2 px-4 py-2 text-sm font-medium rounded-t-lg transition-all',
              'hover:bg-background/50',
              reviewMode === 'list'
                ? 'bg-background text-foreground shadow-sm border border-b-0 border-border'
                : 'text-muted-foreground hover:text-foreground'
            )}
          >
            <List className="h-4 w-4" />
            <span>列表模式</span>
            {reviewMode === 'list' && (
              <span className="absolute bottom-0 left-0 right-0 h-[2px] bg-background" />
            )}
          </button>
          
          {/* 快速审核标签 */}
          <button
            onClick={() => setReviewMode('quick')}
            className={cn(
              'group relative flex items-center gap-2 px-4 py-2 text-sm font-medium rounded-t-lg transition-all',
              'hover:bg-background/50',
              reviewMode === 'quick'
                ? 'bg-background text-foreground shadow-sm border border-b-0 border-border'
                : 'text-muted-foreground hover:text-foreground'
            )}
          >
            <Zap className="h-4 w-4" />
            <span>快速审核</span>
            <Badge variant="secondary" className="ml-1 h-5 px-1.5 text-xs">
              新
            </Badge>
            {reviewMode === 'quick' && (
              <span className="absolute bottom-0 left-0 right-0 h-[2px] bg-background" />
            )}
          </button>
          
          {/* 右侧空白区域和关闭按钮 */}
          <div className="flex-1 border-b border-border" />
          <button
            onClick={() => onOpenChange(false)}
            className="mb-[1px] p-2 text-muted-foreground hover:text-foreground hover:bg-muted rounded-lg transition-colors"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* 列表模式内容 */}
        {reviewMode === 'list' && (
          <>
        <DialogHeader className="px-4 sm:px-6 pt-4 sm:pt-6 pb-4 border-b shrink-0">
          <DialogTitle className="text-lg sm:text-xl">表达方式审核</DialogTitle>
          <DialogDescription className="text-xs sm:text-sm">
            审核麦麦学习到的表达方式。通过审核的项目才会被使用（可在配置中调整），被拒绝的项目永远不会被使用。
          </DialogDescription>
          
          {/* 统计卡片 */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 sm:gap-3 mt-4">
            <div className="rounded-lg border p-2 sm:p-3 text-center">
              <div className="text-xl sm:text-2xl font-bold text-orange-500">
                {statsLoading ? '-' : stats?.unchecked ?? 0}
              </div>
              <div className="text-xs text-muted-foreground">待审核</div>
            </div>
            <div className="rounded-lg border p-2 sm:p-3 text-center">
              <div className="text-xl sm:text-2xl font-bold text-green-500">
                {statsLoading ? '-' : stats?.passed ?? 0}
              </div>
              <div className="text-xs text-muted-foreground">已通过</div>
            </div>
            <div className="rounded-lg border p-2 sm:p-3 text-center">
              <div className="text-xl sm:text-2xl font-bold text-red-500">
                {statsLoading ? '-' : stats?.rejected ?? 0}
              </div>
              <div className="text-xs text-muted-foreground">已拒绝</div>
            </div>
            <div className="rounded-lg border p-2 sm:p-3 text-center">
              <div className="text-xl sm:text-2xl font-bold text-blue-500">
                {statsLoading ? '-' : stats?.total ?? 0}
              </div>
              <div className="text-xs text-muted-foreground">总计</div>
            </div>
          </div>
        </DialogHeader>

        {/* 筛选和操作栏 */}
        <div className="px-4 sm:px-6 py-3 border-b shrink-0 space-y-3">
          <Tabs
            value={filterType}
            onValueChange={(v) => setFilterType(v as typeof filterType)}
            className="w-full"
          >
            <TabsList className="grid w-full grid-cols-4">
              <TabsTrigger value="unchecked" className="gap-1 text-xs sm:text-sm px-1 sm:px-3">
                <Clock className="h-3 w-3 sm:h-4 sm:w-4" />
                <span className="hidden sm:inline">待审核</span>
                <span className="sm:hidden">待审</span>
                <span className="hidden sm:inline">({stats?.unchecked ?? 0})</span>
              </TabsTrigger>
              <TabsTrigger value="passed" className="gap-1 text-xs sm:text-sm px-1 sm:px-3">
                <CheckCircle2 className="h-3 w-3 sm:h-4 sm:w-4" />
                <span className="hidden sm:inline">已通过</span>
                <span className="sm:hidden">通过</span>
                <span className="hidden sm:inline">({stats?.passed ?? 0})</span>
              </TabsTrigger>
              <TabsTrigger value="rejected" className="gap-1 text-xs sm:text-sm px-1 sm:px-3">
                <XCircle className="h-3 w-3 sm:h-4 sm:w-4" />
                <span className="hidden sm:inline">已拒绝</span>
                <span className="sm:hidden">拒绝</span>
                <span className="hidden sm:inline">({stats?.rejected ?? 0})</span>
              </TabsTrigger>
              <TabsTrigger value="all" className="gap-1 text-xs sm:text-sm px-1 sm:px-3">
                <span>全部</span>
                <span className="hidden sm:inline">({stats?.total ?? 0})</span>
              </TabsTrigger>
            </TabsList>
          </Tabs>

          <div className="flex flex-col sm:flex-row items-stretch sm:items-center gap-2">
            <div className="relative flex-1">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
              <Input
                placeholder="搜索情景或风格..."
                value={searchInput}
                onChange={(e) => setSearchInput(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
                className="pl-9"
              />
            </div>
            <div className="flex items-center gap-2">
              <Button variant="outline" size="icon" onClick={handleSearch}>
                <Search className="h-4 w-4" />
              </Button>
              <Button
                variant="outline"
                size="icon"
                onClick={() => {
                  loadList()
                  loadStats()
                }}
                disabled={loading}
              >
                <RefreshCw className={cn('h-4 w-4', loading && 'animate-spin')} />
              </Button>
            </div>
            
            {/* 批量操作按钮 */}
            {selectedIds.size > 0 && (
              <div className="flex items-center gap-2 w-full sm:w-auto">
                {filterType === 'unchecked' ? (
                  // 待审核：显示批量通过和批量拒绝
                  <>
                    <Button
                      variant="default"
                      size="sm"
                      className="bg-green-600 hover:bg-green-700 flex-1 sm:flex-none"
                      onClick={() => handleBatchReview(false)}
                      disabled={loading}
                    >
                      <CheckCircle2 className="h-4 w-4 mr-1" />
                      <span className="hidden sm:inline">批量通过</span>
                      <span className="sm:hidden">通过</span>
                      ({selectedIds.size})
                    </Button>
                    <Button
                      variant="destructive"
                      size="sm"
                      className="flex-1 sm:flex-none"
                      onClick={() => handleBatchReview(true)}
                      disabled={loading}
                    >
                      <XCircle className="h-4 w-4 mr-1" />
                      <span className="hidden sm:inline">批量拒绝</span>
                      <span className="sm:hidden">拒绝</span>
                      ({selectedIds.size})
                    </Button>
                  </>
                ) : filterType === 'passed' ? (
                  // 已通过：只显示批量改为拒绝
                  <Button
                    variant="destructive"
                    size="sm"
                    className="flex-1 sm:flex-none"
                    onClick={() => handleBatchReview(true)}
                    disabled={loading}
                  >
                    <XCircle className="h-4 w-4 mr-1" />
                    <span className="hidden sm:inline">批量改为拒绝</span>
                    <span className="sm:hidden">改为拒绝</span>
                    ({selectedIds.size})
                  </Button>
                ) : filterType === 'rejected' ? (
                  // 已拒绝：只显示批量改为通过
                  <Button
                    variant="default"
                    size="sm"
                    className="bg-green-600 hover:bg-green-700 flex-1 sm:flex-none"
                    onClick={() => handleBatchReview(false)}
                    disabled={loading}
                  >
                    <CheckCircle2 className="h-4 w-4 mr-1" />
                    <span className="hidden sm:inline">批量改为通过</span>
                    <span className="sm:hidden">改为通过</span>
                    ({selectedIds.size})
                  </Button>
                ) : (
                  // 全部：显示两个按钮
                  <>
                    <Button
                      variant="default"
                      size="sm"
                      className="bg-green-600 hover:bg-green-700 flex-1 sm:flex-none"
                      onClick={() => handleBatchReview(false)}
                      disabled={loading}
                    >
                      <CheckCircle2 className="h-4 w-4 mr-1" />
                      <span className="hidden sm:inline">批量通过</span>
                      <span className="sm:hidden">通过</span>
                      ({selectedIds.size})
                    </Button>
                    <Button
                      variant="destructive"
                      size="sm"
                      className="flex-1 sm:flex-none"
                      onClick={() => handleBatchReview(true)}
                      disabled={loading}
                    >
                      <XCircle className="h-4 w-4 mr-1" />
                      <span className="hidden sm:inline">批量拒绝</span>
                      <span className="sm:hidden">拒绝</span>
                      ({selectedIds.size})
                    </Button>
                  </>
                )}
              </div>
            )}
          </div>
        </div>

        {/* 列表区域 */}
        <ScrollArea className="flex-1 px-4 sm:px-6">
          {loading && expressions.length === 0 ? (
            <div className="flex items-center justify-center h-40">
              <RefreshCw className="h-6 w-6 animate-spin text-muted-foreground" />
            </div>
          ) : expressions.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-40 text-muted-foreground">
              <AlertCircle className="h-8 w-8 mb-2" />
              <p>没有找到表达方式</p>
            </div>
          ) : (
            <div className="space-y-2 py-2">
              {/* 全选 */}
              {expressions.length > 0 && (
                <div className="flex items-center justify-between py-2 px-3 rounded-lg bg-muted/50">
                  <div className="flex items-center gap-2">
                    <Checkbox
                      checked={selectedIds.size === expressions.length && expressions.length > 0}
                      onCheckedChange={handleSelectAll}
                    />
                    <span className="text-sm text-muted-foreground">
                      {selectedIds.size === expressions.length && expressions.length > 0
                        ? `已全选当前页 (${expressions.length} 条)`
                        : `全选当前页 (${expressions.length} 条)`}
                    </span>
                  </div>
                  {selectedIds.size > 0 && (
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => setSelectedIds(new Set())}
                      className="h-7 text-xs"
                    >
                      取消选择
                    </Button>
                  )}
                </div>
              )}

              {/* 表达方式列表 */}
              {expressions.map((expr) => (
                <div
                  key={expr.id}
                  className={cn(
                    'rounded-lg border p-3 sm:p-4 space-y-2 sm:space-y-3 transition-colors',
                    selectedIds.has(expr.id) && 'bg-accent border-primary',
                    processingIds.has(expr.id) && 'opacity-50'
                  )}
                >
                  <div className="flex items-start gap-2 sm:gap-3">
                    {/* 选择框 */}
                    <Checkbox
                      checked={selectedIds.has(expr.id)}
                      onCheckedChange={() => toggleSelect(expr.id)}
                      disabled={processingIds.has(expr.id)}
                      className="mt-1"
                    />

                    {/* 内容 */}
                    <div className="flex-1 min-w-0 space-y-2">
                      {/* 情景 */}
                      <div>
                        <span className="text-xs text-muted-foreground">情景：</span>
                        <p className="text-sm font-medium break-words">{expr.situation}</p>
                      </div>
                      
                      {/* 风格 */}
                      <div>
                        <span className="text-xs text-muted-foreground">风格：</span>
                        <p className="text-sm text-muted-foreground break-words">{expr.style}</p>
                      </div>

                      {/* 元信息 */}
                      <div className="flex flex-wrap items-center gap-1 sm:gap-2 text-xs text-muted-foreground">
                        <span>#{expr.id}</span>
                        <span>·</span>
                        <span title={getChatName(expr.chat_id)} className="truncate max-w-24 sm:max-w-32">
                          {getChatName(expr.chat_id)}
                        </span>
                        <span>·</span>
                        <span>{formatTime(expr.create_date)}</span>
                        <div className="flex items-center gap-1">
                          {getStatusBadge(expr)}
                          {getModifierBadge(expr.modified_by)}
                        </div>
                      </div>
                    </div>

                    {/* 操作按钮 */}
                    <div className="flex flex-col gap-1 sm:gap-2 shrink-0">
                      {filterType === 'unchecked' ? (
                        <>
                          <Button
                            size="sm"
                            variant="outline"
                            className="text-green-600 hover:text-green-700 hover:bg-green-50 h-8 sm:h-9 px-2 sm:px-3"
                            onClick={() => handleReview(expr.id, false)}
                            disabled={processingIds.has(expr.id)}
                          >
                            <CheckCircle2 className="h-4 w-4 sm:mr-1" />
                            <span className="hidden sm:inline">通过</span>
                          </Button>
                          <Button
                            size="sm"
                            variant="outline"
                            className="text-red-600 hover:text-red-700 hover:bg-red-50 h-8 sm:h-9 px-2 sm:px-3"
                            onClick={() => handleReview(expr.id, true)}
                            disabled={processingIds.has(expr.id)}
                          >
                            <XCircle className="h-4 w-4 sm:mr-1" />
                            <span className="hidden sm:inline">拒绝</span>
                          </Button>
                        </>
                      ) : filterType === 'passed' ? (
                        <Button
                          size="sm"
                          variant="outline"
                          className="text-red-600 hover:text-red-700 hover:bg-red-50 h-8 sm:h-9 px-2 sm:px-3"
                          onClick={() => handleReview(expr.id, true)}
                          disabled={processingIds.has(expr.id)}
                        >
                          <XCircle className="h-4 w-4 sm:mr-1" />
                          <span className="hidden sm:inline">改为拒绝</span>
                        </Button>
                      ) : filterType === 'rejected' ? (
                        <Button
                          size="sm"
                          variant="outline"
                          className="text-green-600 hover:text-green-700 hover:bg-green-50 h-8 sm:h-9 px-2 sm:px-3"
                          onClick={() => handleReview(expr.id, false)}
                          disabled={processingIds.has(expr.id)}
                        >
                          <CheckCircle2 className="h-4 w-4 sm:mr-1" />
                          <span className="hidden sm:inline">改为通过</span>
                        </Button>
                      ) : (
                        // all 模式下显示两个按钮
                        <>
                          {expr.rejected ? (
                            <Button
                              size="sm"
                              variant="outline"
                              className="text-green-600 hover:text-green-700 hover:bg-green-50 h-8 sm:h-9 px-2 sm:px-3"
                              onClick={() => handleReview(expr.id, false)}
                              disabled={processingIds.has(expr.id)}
                            >
                              <CheckCircle2 className="h-4 w-4 sm:mr-1" />
                              <span className="hidden sm:inline">改为通过</span>
                            </Button>
                          ) : expr.checked ? (
                            <Button
                              size="sm"
                              variant="outline"
                              className="text-red-600 hover:text-red-700 hover:bg-red-50 h-8 sm:h-9 px-2 sm:px-3"
                              onClick={() => handleReview(expr.id, true)}
                              disabled={processingIds.has(expr.id)}
                            >
                              <XCircle className="h-4 w-4 sm:mr-1" />
                              <span className="hidden sm:inline">改为拒绝</span>
                            </Button>
                          ) : (
                            <>
                              <Button
                                size="sm"
                                variant="outline"
                                className="text-green-600 hover:text-green-700 hover:bg-green-50 h-8 sm:h-9 px-2 sm:px-3"
                                onClick={() => handleReview(expr.id, false)}
                                disabled={processingIds.has(expr.id)}
                              >
                                <CheckCircle2 className="h-4 w-4 sm:mr-1" />
                                <span className="hidden sm:inline">通过</span>
                              </Button>
                              <Button
                                size="sm"
                                variant="outline"
                                className="text-red-600 hover:text-red-700 hover:bg-red-50 h-8 sm:h-9 px-2 sm:px-3"
                                onClick={() => handleReview(expr.id, true)}
                                disabled={processingIds.has(expr.id)}
                              >
                                <XCircle className="h-4 w-4 sm:mr-1" />
                                <span className="hidden sm:inline">拒绝</span>
                              </Button>
                            </>
                          )}
                        </>
                      )}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </ScrollArea>

        {/* 分页 */}
        <div className="px-4 sm:px-6 py-3 border-t shrink-0 flex flex-col sm:flex-row items-center justify-between gap-3">
          {/* 左侧：每页显示数量 */}
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <span className="hidden sm:inline">每页</span>
            <Select
              value={pageSize.toString()}
              onValueChange={(v) => {
                setPageSize(parseInt(v, 10))
                setPage(1) // 切换每页数量时重置到第一页
              }}
            >
              <SelectTrigger className="w-[70px] h-8">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="10">10</SelectItem>
                <SelectItem value="20">20</SelectItem>
                <SelectItem value="50">50</SelectItem>
                <SelectItem value="100">100</SelectItem>
              </SelectContent>
            </Select>
            <span className="hidden sm:inline">条</span>
            <span className="text-muted-foreground">共 {total} 条</span>
          </div>

          {/* 中间：页码导航 */}
          <Pagination className="mx-0 w-auto">
            <PaginationContent>
              <PaginationItem>
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-8 w-8"
                  onClick={() => setPage((p) => Math.max(1, p - 1))}
                  disabled={page <= 1 || loading}
                >
                  <ChevronLeft className="h-4 w-4" />
                </Button>
              </PaginationItem>
              
              {getPageNumbers().map((pageNum, idx) => (
                <PaginationItem key={idx}>
                  {pageNum === 'ellipsis' ? (
                    <PaginationEllipsis />
                  ) : (
                    <PaginationLink
                      href="#"
                      isActive={pageNum === page}
                      onClick={(e) => {
                        e.preventDefault()
                        setPage(pageNum)
                      }}
                      className="h-8 w-8 cursor-pointer"
                    >
                      {pageNum}
                    </PaginationLink>
                  )}
                </PaginationItem>
              ))}
              
              <PaginationItem>
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-8 w-8"
                  onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                  disabled={page >= totalPages || loading}
                >
                  <ChevronRight className="h-4 w-4" />
                </Button>
              </PaginationItem>
            </PaginationContent>
          </Pagination>

          {/* 右侧：跳转 */}
          <div className="hidden sm:flex items-center gap-2 text-sm">
            <span className="text-muted-foreground">跳至</span>
            <Input
              type="number"
              min={1}
              max={totalPages}
              value={jumpPage}
              onChange={(e) => setJumpPage(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleJumpPage()}
              className="w-16 h-8 text-center"
              placeholder={page.toString()}
            />
            <span className="text-muted-foreground">页</span>
            <Button
              variant="outline"
              size="sm"
              className="h-8"
              onClick={handleJumpPage}
              disabled={loading}
            >
              跳转
            </Button>
          </div>
        </div>
          </>
        )}

        {/* 快速审核模式内容 */}
        {reviewMode === 'quick' && (
          <div className="flex-1 flex flex-col overflow-hidden">
            {/* 顶部筛选和统计 */}
            <div className="px-4 sm:px-6 py-3 border-b shrink-0 space-y-3">
              {/* 统计信息 */}
              <div className="flex items-center justify-between text-sm">
                <div className="flex items-center gap-4">
                  <span className="text-muted-foreground">
                    待审核: <span className="font-medium text-orange-500">{stats?.unchecked ?? 0}</span>
                  </span>
                  <span className="text-muted-foreground">
                    已通过: <span className="font-medium text-green-500">{stats?.passed ?? 0}</span>
                  </span>
                  <span className="text-muted-foreground">
                    已拒绝: <span className="font-medium text-red-500">{stats?.rejected ?? 0}</span>
                  </span>
                </div>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    loadQuickList()
                    loadStats()
                  }}
                  disabled={quickLoading}
                >
                  <RefreshCw className={cn('h-4 w-4 mr-1', quickLoading && 'animate-spin')} />
                  刷新
                </Button>
              </div>

              {/* 筛选标签 */}
              <Tabs
                value={quickFilterType}
                onValueChange={(v) => setQuickFilterType(v as typeof quickFilterType)}
                className="w-full"
              >
                <TabsList className="grid w-full grid-cols-4">
                  <TabsTrigger value="unchecked" className="gap-1 text-xs sm:text-sm">
                    <Clock className="h-3 w-3 sm:h-4 sm:w-4" />
                    <span className="hidden sm:inline">待审核</span>
                    <span className="sm:hidden">待审</span>
                  </TabsTrigger>
                  <TabsTrigger value="passed" className="gap-1 text-xs sm:text-sm">
                    <CheckCircle2 className="h-3 w-3 sm:h-4 sm:w-4" />
                    <span className="hidden sm:inline">已通过</span>
                    <span className="sm:hidden">通过</span>
                  </TabsTrigger>
                  <TabsTrigger value="rejected" className="gap-1 text-xs sm:text-sm">
                    <XCircle className="h-3 w-3 sm:h-4 sm:w-4" />
                    <span className="hidden sm:inline">已拒绝</span>
                    <span className="sm:hidden">拒绝</span>
                  </TabsTrigger>
                  <TabsTrigger value="all" className="gap-1 text-xs sm:text-sm">
                    全部
                  </TabsTrigger>
                </TabsList>
              </Tabs>
            </div>

            {/* 卡片区域 */}
            <div className="flex-1 flex flex-col items-center justify-center p-4 sm:p-8 relative overflow-hidden">
              {quickLoading && quickExpressions.length === 0 ? (
                <div className="flex flex-col items-center justify-center">
                  <RefreshCw className="h-8 w-8 animate-spin text-muted-foreground mb-4" />
                  <p className="text-muted-foreground">加载中...</p>
                </div>
              ) : quickExpressions.length === 0 ? (
                <div className="flex flex-col items-center justify-center text-center">
                  <div className="w-20 h-20 rounded-full bg-muted/50 flex items-center justify-center mb-6">
                    <CheckCircle2 className="h-10 w-10 text-green-500" />
                  </div>
                  <h3 className="text-xl font-semibold mb-2">全部审核完成！</h3>
                  <p className="text-muted-foreground">当前筛选条件下没有待处理的项目</p>
                </div>
              ) : (
                <>
                  {/* 进度提示 */}
                  <div className="absolute top-4 left-1/2 -translate-x-1/2 text-sm text-muted-foreground z-50">
                    {quickCurrentIndex + 1} / {quickExpressions.length}
                    {quickTotal > quickExpressions.length && (
                      <span className="ml-1">（共 {quickTotal} 条）</span>
                    )}
                  </div>

                  {/* 方向提示 (仅针对当前卡片) */}
                  <div className="absolute inset-x-4 top-1/2 -translate-y-1/2 flex justify-between pointer-events-none z-40">
                    {(() => {
                      const currentExpr = quickExpressions[quickCurrentIndex]
                      const directions = getAllowedDirections(currentExpr)
                      return (
                        <>
                          <div className={cn(
                            'flex items-center gap-2 px-4 py-2 rounded-lg transition-all duration-300',
                            swipeDirection === 'left' ? 'bg-red-500/20 text-red-500 scale-110' : 'bg-muted/50 text-muted-foreground opacity-0',
                            !directions.left && 'invisible'
                          )}>
                            <XCircle className="h-8 w-8" />
                            <span className="font-bold text-lg hidden sm:inline">拒绝</span>
                          </div>
                          <div className={cn(
                            'flex items-center gap-2 px-4 py-2 rounded-lg transition-all duration-300',
                            swipeDirection === 'right' ? 'bg-green-500/20 text-green-500 scale-110' : 'bg-muted/50 text-muted-foreground opacity-0',
                            !directions.right && 'invisible'
                          )}>
                            <span className="font-bold text-lg hidden sm:inline">通过</span>
                            <CheckCircle2 className="h-8 w-8" />
                          </div>
                        </>
                      )
                    })()}
                  </div>

                  {/* 堆叠卡片 */}
                  <div className="relative w-full max-w-md h-[400px] flex items-center justify-center">
                    {quickExpressions
                      .slice(quickCurrentIndex, quickCurrentIndex + 5)
                      .reverse()
                      .map((expr, reverseIndex, array) => {
                        const index = array.length - 1 - reverseIndex // 0 is current, 1 is next...
                        const isCurrent = index === 0
                        
                        // 计算样式
                        let style: React.CSSProperties = {
                          zIndex: 5 - index,
                          position: 'absolute',
                          width: '100%',
                          transition: isCurrent && !isDraggingRef.current ? 'all 0.3s cubic-bezier(0.25, 0.8, 0.25, 1)' : 'none',
                        }

                        if (isCurrent) {
                          // 当前卡片样式
                          style = {
                            ...style,
                            transform: `translateX(${swipeOffset}px) rotate(${swipeOffset * 0.05}deg)`,
                            opacity: Math.max(0, 1 - Math.abs(swipeOffset) / 500),
                            cursor: 'grab',
                          }
                        } else {
                          // 后方卡片样式
                          const progress = Math.min(Math.abs(swipeOffset) / 200, 1) // 0 to 1
                          
                          // 计算指定索引的样式属性
                          const getStyleForIndex = (i: number) => {
                            // 增加一些伪随机的错位感，让堆叠看起来不那么死板
                            const randomRotate = (i * 7) % 5 
                            const randomX = (i * 13) % 7
                            
                            return {
                              scale: 1 - i * 0.05,
                              translateY: i * 12,
                              // 错位效果：奇偶交替旋转 + 伪随机偏移
                              rotate: (i % 2 === 0 ? 1 : -1) * (i * 2) + randomRotate,
                              translateX: (i % 2 === 0 ? -1 : 1) * (i * 4) + randomX,
                            }
                          }

                          const base = getStyleForIndex(index)
                          const target = getStyleForIndex(index - 1)
                          
                          // 插值计算：所有后方卡片都会跟随第一张卡片的滑动而向前移动
                          const currentScale = base.scale + (target.scale - base.scale) * progress
                          const currentTranslateY = base.translateY + (target.translateY - base.translateY) * progress
                          const currentRotate = base.rotate + (target.rotate - base.rotate) * progress
                          const currentTranslateX = base.translateX + (target.translateX - base.translateX) * progress

                          style = {
                            ...style,
                            transform: `translate3d(${currentTranslateX}px, ${currentTranslateY}px, 0) scale(${currentScale}) rotate(${currentRotate}deg)`,
                            opacity: 1 - index * 0.15,
                            filter: `blur(${Math.max(0, index * 1 - progress)}px)`, // 模糊度也随之减小
                            pointerEvents: 'none',
                          }
                        }

                        return (
                          <div
                            key={expr.id}
                            ref={isCurrent ? cardRef : undefined}
                            className={cn(
                              'bg-card border rounded-xl shadow-xl p-6 select-none h-full flex flex-col',
                              isCurrent && 'active:cursor-grabbing shadow-2xl ring-1 ring-border/50',
                              // 冲突动效
                              isCurrent && conflictId === expr.id && 'ring-4 ring-orange-500/50 bg-orange-50/10'
                            )}
                            style={style}
                            onMouseDown={isCurrent ? handleMouseDown : undefined}
                            onMouseMove={isCurrent ? handleMouseMove : undefined}
                            onMouseUp={isCurrent ? handleMouseUp : undefined}
                            onMouseLeave={isCurrent ? handleMouseLeave : undefined}
                            onTouchStart={isCurrent ? handleTouchStart : undefined}
                            onTouchMove={isCurrent ? handleTouchMove : undefined}
                            onTouchEnd={isCurrent ? handleTouchEnd : undefined}
                          >
                            {/* 冲突提示遮罩 */}
                            {isCurrent && conflictId === expr.id && (
                              <div className="absolute inset-0 z-50 flex flex-col items-center justify-center bg-background/80 backdrop-blur-sm animate-in fade-in duration-300 rounded-xl">
                                <div className="relative">
                                  <div className="absolute inset-0 bg-orange-500/20 rounded-full animate-ping" />
                                  <RefreshCw className="relative h-16 w-16 text-orange-500 mb-4 animate-spin duration-1000" />
                                </div>
                                <h3 className="text-xl font-bold text-foreground animate-in slide-in-from-bottom-2 fade-in duration-500">数据已更新</h3>
                                <p className="text-muted-foreground mt-2 animate-in slide-in-from-bottom-3 fade-in duration-700">后台任务已处理此条目</p>
                              </div>
                            )}

                            {/* 无效操作提示 */}
                            {isCurrent && (
                              <div className={cn(
                                "absolute inset-0 flex items-center justify-center z-20 pointer-events-none transition-opacity duration-200",
                                ((swipeOffset < -10 && !getAllowedDirections(expr).left) || (swipeOffset > 10 && !getAllowedDirections(expr).right)) 
                                  ? "opacity-100" 
                                  : "opacity-0"
                              )}>
                                <div className="bg-background/80 backdrop-blur-sm p-4 rounded-full shadow-lg border border-border">
                                  <Ban className="h-12 w-12 text-muted-foreground" />
                                </div>
                              </div>
                            )}

                            <div className="space-y-4 flex-1">
                              {/* 状态和ID */}
                              <div className="flex items-center justify-between">
                                <span className="text-sm text-muted-foreground font-mono">#{expr.id}</span>
                                <div className="flex items-center gap-2">
                                  {getStatusBadge(expr)}
                                  {getModifierBadge(expr.modified_by)}
                                </div>
                              </div>

                              {/* 情景 */}
                              <div className="space-y-1.5">
                                <label className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">情景</label>
                                <div className="p-3 bg-muted/30 rounded-lg border border-border/50">
                                  <p className="text-lg font-medium leading-relaxed">{expr.situation}</p>
                                </div>
                              </div>

                              {/* 风格 */}
                              <div className="space-y-1.5">
                                <label className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">风格</label>
                                <div className="flex flex-wrap gap-2">
                                  {expr.style.split(/[,，]/).map((s, i) => (
                                    <Badge key={i} variant="secondary" className="font-normal">
                                      {s.trim()}
                                    </Badge>
                                  ))}
                                </div>
                              </div>
                            </div>

                            {/* 底部信息 */}
                            <div className="mt-auto pt-4 border-t flex items-center justify-between text-xs text-muted-foreground">
                              <div className="flex items-center gap-2">
                                <div className="w-6 h-6 rounded-full bg-primary/10 flex items-center justify-center text-primary">
                                  <User className="h-3 w-3" />
                                </div>
                                <span title={getChatName(expr.chat_id)} className="truncate max-w-[120px] font-medium">
                                  {getChatName(expr.chat_id)}
                                </span>
                              </div>
                              <span className="font-mono">{formatTime(expr.create_date)}</span>
                            </div>
                          </div>
                        )
                      })}
                  </div>

                  {/* 操作按钮（移动端） */}
                  <div className="flex items-center gap-8 mt-8 sm:hidden z-50">
                    {(() => {
                      const currentExpr = quickExpressions[quickCurrentIndex]
                      const directions = getAllowedDirections(currentExpr)
                      return (
                        <>
                          <Button
                            variant="outline"
                            size="lg"
                            className={cn(
                              'w-16 h-16 rounded-full border-2 shadow-lg transition-all active:scale-95',
                              !directions.left ? 'opacity-30 cursor-not-allowed' : 'hover:bg-red-50 hover:text-red-600 hover:border-red-200'
                            )}
                            onClick={() => directions.left && handleQuickReview(true)}
                            disabled={!directions.left || isAnimating}
                          >
                            <XCircle className="h-8 w-8" />
                          </Button>
                          <Button
                            variant="outline"
                            size="lg"
                            className={cn(
                              'w-16 h-16 rounded-full border-2 shadow-lg transition-all active:scale-95',
                              !directions.right ? 'opacity-30 cursor-not-allowed' : 'hover:bg-green-50 hover:text-green-600 hover:border-green-200'
                            )}
                            onClick={() => directions.right && handleQuickReview(false)}
                            disabled={!directions.right || isAnimating}
                          >
                            <CheckCircle2 className="h-8 w-8" />
                          </Button>
                        </>
                      )
                    })()}
                  </div>
                </>
              )}
            </div>

            {/* 底部快捷键提示（桌面端） */}
            <div className="hidden sm:flex items-center justify-center gap-6 px-6 py-3 border-t text-xs text-muted-foreground">
              <div className="flex items-center gap-1">
                <kbd className="px-2 py-1 bg-muted rounded text-xs">←</kbd>
                <span>拒绝</span>
              </div>
              <div className="flex items-center gap-1">
                <kbd className="px-2 py-1 bg-muted rounded text-xs">→</kbd>
                <span>通过</span>
              </div>
              <div className="flex items-center gap-1">
                <kbd className="px-2 py-1 bg-muted rounded text-xs">↑</kbd>
                <span>上一条</span>
              </div>
              <div className="flex items-center gap-1">
                <kbd className="px-2 py-1 bg-muted rounded text-xs">↓</kbd>
                <span>下一条</span>
              </div>
              <span className="text-muted-foreground/50">|</span>
              <span>拖拽卡片滑动审核</span>
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}
