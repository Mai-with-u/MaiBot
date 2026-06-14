/**
 * 表达方式审核器弹窗组件
 *
 * 功能：
 * 1. 分页显示待审核/已通过的表达方式
 * 2. 支持单条通过/拒绝
 * 3. 支持批量操作
 * 4. 冲突检测（防止与并发审核操作冲突）
 */

import { animated, useSpring } from '@react-spring/web'

const AnimatedDiv = animated('div')
import { useCallback, useEffect, useRef, useState } from 'react'
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
import { ShortcutKbd } from '@/components/ui/kbd'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Checkbox } from '@/components/ui/checkbox'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { ThinkingIllustration } from '@/components/ui/thinking-illustration'
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
  open?: boolean
  onOpenChange?: (open: boolean) => void
  embedded?: boolean
  className?: string
  mode?: 'list' | 'quick'
  onReviewed?: () => void
}

export function ExpressionReviewer({
  open = true,
  onOpenChange = () => undefined,
  embedded = false,
  className,
  mode,
  onReviewed,
}: ExpressionReviewerProps) {
  // 审核模式：list（列表模式）或 quick（快速审核模式）
  const [internalReviewMode, setInternalReviewMode] = useState<'list' | 'quick'>('list')
  const reviewMode = mode ?? internalReviewMode
  const showModeSwitcher = mode === undefined
  const [stats, setStats] = useState<ReviewStats | null>(null)
  const [expressions, setExpressions] = useState<Expression[]>([])

  // 快速审核模式状态
  const [quickFilterType, setQuickFilterType] = useState<'unchecked' | 'passed' | 'all'>(
    'unchecked'
  )
  const [quickExpressions, setQuickExpressions] = useState<Expression[]>([])
  const quickExpressionsRef = useRef<Expression[]>([])
  const quickExcludedIdsRef = useRef<Set<number>>(new Set())
  const [quickCurrentIndex, setQuickCurrentIndex] = useState(0)
  const [quickLoading, setQuickLoading] = useState(false)
  const quickLoadingRef = useRef(false)
  const [quickTotal, setQuickTotal] = useState(0)
  const [quickHasMore, setQuickHasMore] = useState(true)
  const swipeDirectionRef = useRef<'left' | 'right' | null>(null)
  const isAnimatingRef = useRef(false)
  const [cardSpring, cardApi] = useSpring(() => ({
    x: 0,
    opacity: 1,
    rotate: 0,
    config: { tension: 300, friction: 30 },
  }))
  const swipeOffsetRef = useRef(0)
  const [conflictId, setConflictId] = useState<number | null>(null)
  const cardRef = useRef<HTMLDivElement>(null)
  const dragStartRef = useRef<{ x: number; y: number } | null>(null)
  const isDraggingRef = useRef(false)

  useEffect(() => {
    quickExpressionsRef.current = quickExpressions
  }, [quickExpressions])
  const [loading, setLoading] = useState(false)
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [jumpPage, setJumpPage] = useState('')
  const [filterType, setFilterType] = useState<'unchecked' | 'passed' | 'all'>('unchecked')
  const [search, setSearch] = useState('')
  const [searchInput, setSearchInput] = useState('')
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set())
  const [processingIds, setProcessingIds] = useState<Set<number>>(new Set())
  const [chatNameMap, setChatNameMap] = useState<Map<string, string>>(new Map())
  const { toast } = useToast()

  // 加载统计数据
  const loadStats = useCallback(async () => {
    try {
      const result = await getReviewStats()
      setStats(result)
    } catch (error) {
      console.error('加载统计失败:', error)
    }
  }, [])

  // 加载列表
  const loadList = useCallback(async () => {
    try {
      setLoading(true)
      const result = await getReviewList({
        page,
        page_size: pageSize,
        filter_type: filterType,
        search: search || undefined,
      })
      setExpressions(result.data)
      setTotal(result.total)
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

  // 加载聚天名称映射
  const loadChatNames = useCallback(async () => {
    try {
      const result = await getChatList()
      const nameMap = new Map<string, string>()
      result.forEach((chat: ChatInfo) => {
        nameMap.set(chat.chat_id, chat.chat_name)
      })
      setChatNameMap(nameMap)
    } catch (error) {
      console.error('加载聚天名称失败:', error)
    }
  }, [])

  // 快速审核模式 - 加载数据
  const loadQuickList = useCallback(
    async (resetIndex = true, append = false) => {
      if (quickLoadingRef.current) return

      try {
        quickLoadingRef.current = true
        setQuickLoading(true)
        const excludedIds = append ? Array.from(quickExcludedIdsRef.current) : []
        const result = await getReviewList({
          page: 1,
          page_size: 20,
          filter_type: quickFilterType,
          order: quickFilterType === 'unchecked' ? 'random' : 'latest',
          exclude_ids: excludedIds.length > 0 ? excludedIds : undefined,
        })

        if (append) {
          const loadedCount = quickExpressionsRef.current.length
          const existingIds = new Set(quickExpressionsRef.current.map((e) => e.id))
          const newItems = result.data.filter((e: Expression) => !existingIds.has(e.id))
          newItems.forEach((expr: Expression) => quickExcludedIdsRef.current.add(expr.id))

          setQuickExpressions((prev) => [...prev, ...newItems])
          setQuickTotal(loadedCount + result.total)
          setQuickHasMore(newItems.length > 0 && result.total > newItems.length)
        } else {
          quickExcludedIdsRef.current = new Set(
            result.data.map((expr: Expression) => expr.id)
          )
          setQuickExpressions(result.data)
          setQuickTotal(result.total)
          setQuickHasMore(result.total > result.data.length)
        }

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
        quickLoadingRef.current = false
        setQuickLoading(false)
      }
    },
    [quickFilterType, toast]
  )

  // 快速审核模式 - 切换筛选时重置
  useEffect(() => {
    if (reviewMode === 'quick') {
      quickExcludedIdsRef.current = new Set()
      setQuickCurrentIndex(0)
      setQuickHasMore(true)
      setQuickExpressions([])
    }
  }, [quickFilterType, reviewMode])

  // 快速审核模式 - 加载数据
  useEffect(() => {
    if (open && reviewMode === 'quick') {
      loadQuickList()
      loadStats()
    }
  }, [open, reviewMode, quickFilterType, loadQuickList, loadStats])

  // 获取当前卡片允许的滑动方向
  const getAllowedDirections = useCallback(
    (expr: Expression | undefined) => {
      if (!expr) return { left: false, right: false }

      if (quickFilterType === 'unchecked') {
        // 待审核：左拒绝，右通过
        return { left: true, right: true }
      } else if (quickFilterType === 'passed') {
        // 已通过：只能左滑改为拒绝
        return { left: true, right: false }
      } else {
        // 全部：智能判断
        if (!expr.checked) {
          // 未审核：双向
          return { left: true, right: true }
        } else {
          // 已通过：只能左滑
          return { left: true, right: false }
        }
      }
    },
    [quickFilterType]
  )

  // 快速审核 - 执行审核操作
  const handleQuickReview = useCallback(
    async (approved: boolean) => {
      const currentExpr = quickExpressions[quickCurrentIndex]
      if (!currentExpr || isAnimatingRef.current) return

      const directions = getAllowedDirections(currentExpr)
      if ((!approved && !directions.left) || (approved && !directions.right)) {
        return
      }

      isAnimatingRef.current = true
      swipeDirectionRef.current = approved ? 'right' : 'left'
      cardApi.start({ x: approved ? 400 : -400, rotate: approved ? 20 : -20, opacity: 0 })

      try {
        const result = await batchReviewExpressions([
          {
            id: currentExpr.id,
            approved,
            require_unchecked: quickFilterType === 'unchecked',
          },
        ])

        if (result.results[0]?.success) {
          toast({
            title: approved ? '已通过' : '已删除',
            description: `表达方式 #${currentExpr.id} ${approved ? '已通过' : '已删除'}`,
          })

          // 从列表中移除当前项
          setTimeout(() => {
            swipeDirectionRef.current = null
            swipeOffsetRef.current = 0
            cardApi.set({ x: 0, opacity: 1, rotate: 0 })
            isAnimatingRef.current = false

            const stillExists = quickExpressionsRef.current.some((e) => e.id === currentExpr.id)
            if (!stillExists) return

            quickExcludedIdsRef.current.add(currentExpr.id)
            setQuickExpressions((prev) => prev.filter((e) => e.id !== currentExpr.id))
            setQuickTotal((prev) => Math.max(0, prev - 1))

            // 刷新统计
            loadStats()
            onReviewed?.()
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
            swipeDirectionRef.current = null
            swipeOffsetRef.current = 0
            cardApi.set({ x: 0, opacity: 1, rotate: 0 })
            isAnimatingRef.current = false
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
        swipeDirectionRef.current = null
        swipeOffsetRef.current = 0
        cardApi.set({ x: 0, opacity: 1, rotate: 0 })
        isAnimatingRef.current = false
      }
    },
    [
      quickExpressions,
      quickCurrentIndex,
      isAnimatingRef,
      getAllowedDirections,
      quickFilterType,
      toast,
      loadStats,
      onReviewed,
    ]
  )

  // 拖拽开始
  const handleDragStart = useCallback(
    (clientX: number, clientY: number) => {
      if (isAnimatingRef.current) return
      dragStartRef.current = { x: clientX, y: clientY }
      isDraggingRef.current = false
    },
    [isAnimatingRef]
  )

  // 触发无效操作动画
  const triggerInvalidAnimation = useCallback(
    (direction: 'left' | 'right') => {
      if (isAnimatingRef.current) return
      isAnimatingRef.current = true
      // 模拟向该方向移动一点
      cardApi.start({ x: direction === 'left' ? -30 : 30, immediate: true })

      setTimeout(() => {
        cardApi.start({ x: 0 })
        setTimeout(() => {
          isAnimatingRef.current = false
        }, 300)
      }, 150)
    },
    [cardApi]
  )

  // 拖拽移动
  const handleDragMove = useCallback(
    (clientX: number) => {
      if (!dragStartRef.current || isAnimatingRef.current) return

      const deltaX = clientX - dragStartRef.current.x
      const currentExpr = quickExpressions[quickCurrentIndex]
      const directions = getAllowedDirections(currentExpr)

      // 检查方向限制
      if (deltaX < 0 && !directions.left) {
        cardApi.start({ x: deltaX * 0.2, immediate: true }) // 提供阻力反馈
        swipeOffsetRef.current = deltaX * 0.2
        swipeDirectionRef.current = null
        return
      }
      if (deltaX > 0 && !directions.right) {
        cardApi.start({ x: deltaX * 0.2, immediate: true })
        swipeOffsetRef.current = deltaX * 0.2
        swipeDirectionRef.current = null
        return
      }

      isDraggingRef.current = true
      swipeOffsetRef.current = deltaX
      cardApi.start({
        x: deltaX,
        rotate: deltaX * 0.05,
        opacity: Math.max(0, 1 - Math.abs(deltaX) / 500),
        immediate: true,
      })
      cardApi.start({
        x: deltaX,
        rotate: deltaX * 0.05,
        opacity: Math.max(0, 1 - Math.abs(deltaX) / 500),
        immediate: true,
      })

      if (Math.abs(deltaX) > 50) {
        swipeDirectionRef.current = deltaX > 0 ? 'right' : 'left'
      } else {
        swipeDirectionRef.current = null
      }
    },
    [quickExpressions, quickCurrentIndex, getAllowedDirections, cardApi]
  )

  // 拖拽结束
  const handleDragEnd = useCallback(() => {
    if (!dragStartRef.current) return

    const threshold = 100
    const currentX = cardSpring.x.get()
    if (Math.abs(currentX) > threshold && swipeDirectionRef.current) {
      handleQuickReview(swipeDirectionRef.current === 'right')
    } else {
      // 回弹
      cardApi.start({ x: 0, rotate: 0, opacity: 1 })
      swipeOffsetRef.current = 0
      swipeDirectionRef.current = null
    }

    dragStartRef.current = null
    isDraggingRef.current = false
  }, [cardSpring.x, handleQuickReview, cardApi])

  // 鼠标事件处理
  const handleMouseDown = useCallback(
    (e: React.MouseEvent) => {
      handleDragStart(e.clientX, e.clientY)
    },
    [handleDragStart]
  )

  const handleMouseMove = useCallback(
    (e: React.MouseEvent) => {
      if (dragStartRef.current) {
        e.preventDefault()
        handleDragMove(e.clientX)
      }
    },
    [handleDragMove]
  )

  const handleMouseUp = useCallback(() => {
    handleDragEnd()
  }, [handleDragEnd])

  const handleMouseLeave = useCallback(() => {
    if (dragStartRef.current) {
      handleDragEnd()
    }
  }, [handleDragEnd])

  // 触摸事件处理
  const handleTouchStart = useCallback(
    (e: React.TouchEvent) => {
      const touch = e.touches[0]
      handleDragStart(touch.clientX, touch.clientY)
    },
    [handleDragStart]
  )

  const handleTouchMove = useCallback(
    (e: React.TouchEvent) => {
      const touch = e.touches[0]
      handleDragMove(touch.clientX)
    },
    [handleDragMove]
  )

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

      if (isAnimatingRef.current || quickLoading) return

      const currentExpr = quickExpressions[quickCurrentIndex]
      const directions = getAllowedDirections(currentExpr)

      if (e.key === 'ArrowLeft') {
        if (directions.left) {
          handleQuickReview(false) // 拒绝
        } else {
          triggerInvalidAnimation('left')
        }
      } else if (e.key === 'ArrowRight') {
        if (directions.right) {
          handleQuickReview(true) // 通过
        } else {
          triggerInvalidAnimation('right')
        }
      } else if (e.key === 'ArrowDown') {
        // 跳过当前项
        if (quickCurrentIndex < quickExpressions.length - 1) {
          setQuickCurrentIndex((prev) => prev + 1)
        }
      } else if (e.key === 'ArrowUp') {
        // 返回上一项
        if (quickCurrentIndex > 0) {
          setQuickCurrentIndex((prev) => prev - 1)
        }
      }
    }

    // 使用 capture 模式，在事件到达 Tabs 之前拦截
    window.addEventListener('keydown', handleKeyDown, true)
    return () => window.removeEventListener('keydown', handleKeyDown, true)
  }, [
    open,
    reviewMode,
    quickExpressions,
    quickCurrentIndex,
    isAnimatingRef,
    quickLoading,
    getAllowedDirections,
    handleQuickReview,
    triggerInvalidAnimation,
  ])

  // 动态加载更多数据 - 当接近列表末尾时自动加载
  useEffect(() => {
    if (!open || reviewMode !== 'quick' || quickLoading) return

    // 距离末尾还有5个或更少时，且还有更多数据时，自动加载
    const remaining = quickExpressions.length - quickCurrentIndex - 1

    if (remaining <= 5 && quickHasMore) {
      loadQuickList(false, true) // 追加模式
    }
  }, [
    open,
    reviewMode,
    quickCurrentIndex,
    quickExpressions.length,
    quickHasMore,
    quickLoading,
    loadQuickList,
  ])

  // 修正删除最后一张卡片后的索引
  useEffect(() => {
    if (quickLoading) return

    const maxIndex = Math.max(0, quickExpressions.length - 1)
    if (quickCurrentIndex > maxIndex) {
      setQuickCurrentIndex(maxIndex)
    }
  }, [quickExpressions.length, quickCurrentIndex, quickLoading])

  // 初始加载
  useEffect(() => {
    if (!open) {
      return
    }

    loadStats()
    if (reviewMode === 'list') {
      loadList()
      loadChatNames()
    }
  }, [open, reviewMode, loadStats, loadList, loadChatNames])

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
  const getChatName = (expression: Expression): string => {
    return expression.chat_name || chatNameMap.get(expression.chat_id) || expression.chat_id
  }

  // 单条审核
  const handleReview = async (id: number, approved: boolean) => {
    try {
      setProcessingIds((prev) => new Set(prev).add(id))

      const result = await batchReviewExpressions([
        { id, approved, require_unchecked: filterType === 'unchecked' },
      ])

      if (result.results[0]?.success) {
        toast({
          title: approved ? '已通过' : '已删除',
          description: `表达方式 #${id} ${approved ? '已通过' : '已删除'}`,
        })
        // 刷新列表和统计
        loadList()
        loadStats()
        onReviewed?.()
      } else {
        toast({
          title: '操作失败',
          description: result.results[0]?.message || '未知错误',
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
  const handleBatchReview = async (approved: boolean) => {
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
        approved,
        require_unchecked: filterType === 'unchecked',
      }))

      const result = await batchReviewExpressions(items)

      toast({
        title: '批量审核完成',
        description: `成功 ${result.succeeded} 条，失败 ${result.failed} 条`,
        variant: result.failed > 0 ? 'destructive' : 'default',
      })

      // 清空选择并刷新
      setSelectedIds(new Set())
      loadList()
      loadStats()
      onReviewed?.()
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

  // 获取审核状态标签
  const getReviewBadge = (expr: Expression) => {
    const modifier = expr.modified_by?.toLowerCase()

    if (expr.checked && modifier === 'user') {
      return (
        <Badge variant="default" className="gap-1 bg-green-600 whitespace-nowrap">
          <User className="h-3 w-3" />
          人工通过
        </Badge>
      )
    }
    return null
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

  const renderListHeaderTitle = () => {
    if (embedded) {
      return null
    }
    return <DialogTitle className="text-lg sm:text-xl">表达方式审核</DialogTitle>
  }

  const renderListHeaderDescription = () => {
    const description =
      '审核麦麦学习到的表达方式。通过人工审核的项目才会被使用（可在配置中调整），不通过的项目会被直接删除。'
    if (embedded) {
      return null
    }
    return <DialogDescription className="text-xs sm:text-sm">{description}</DialogDescription>
  }

  const handleReviewModeChange = (nextMode: 'list' | 'quick') => {
    if (mode === undefined) {
      setInternalReviewMode(nextMode)
    }
  }

  const reviewerContent = (
    <div
      className={cn(
        'flex min-h-0 flex-col overflow-hidden',
        embedded ? 'bg-card h-full rounded-lg border' : 'h-full',
        className
      )}
    >
      {/* 浏览器标签页风格的模式切换器 */}
      {showModeSwitcher && (
        <div className="bg-muted/30 flex shrink-0 items-end px-2 pt-2">
          {/* 列表模式标签 */}
          <button
            onClick={() => handleReviewModeChange('list')}
            className={cn(
              'group relative flex items-center gap-2 rounded-t-lg px-4 py-2 text-sm font-medium transition-all',
              'hover:bg-background/50',
              reviewMode === 'list'
                ? 'bg-background text-foreground border-border border border-b-0 shadow-sm'
                : 'text-muted-foreground hover:text-foreground'
            )}
          >
            <List className="h-4 w-4" />
            <span>列表模式</span>
            {reviewMode === 'list' && (
              <span className="bg-background absolute right-0 bottom-0 left-0 h-[2px]" />
            )}
          </button>

          {/* 快速审核标签 */}
          <button
            onClick={() => handleReviewModeChange('quick')}
            className={cn(
              'group relative flex items-center gap-2 rounded-t-lg px-4 py-2 text-sm font-medium transition-all',
              'hover:bg-background/50',
              reviewMode === 'quick'
                ? 'bg-background text-foreground border-border border border-b-0 shadow-sm'
                : 'text-muted-foreground hover:text-foreground'
            )}
          >
            <Zap className="h-4 w-4" />
            <span>快速审核</span>
            <Badge variant="secondary" className="ml-1 h-5 px-1.5 text-xs">
              新
            </Badge>
            {reviewMode === 'quick' && (
              <span className="bg-background absolute right-0 bottom-0 left-0 h-[2px]" />
            )}
          </button>

          {/* 右侧空白区域和关闭按钮 */}
          <div className="border-border flex-1 border-b" />
          {!embedded && (
            <button
              onClick={() => onOpenChange(false)}
              className="text-muted-foreground hover:text-foreground hover:bg-muted mb-[1px] rounded-lg p-2 transition-colors"
            >
              <X className="h-4 w-4" />
            </button>
          )}
        </div>
      )}

      {/* 列表模式内容 */}
      {reviewMode === 'list' && (
        <>
          <DialogHeader
            className={cn(
              'shrink-0 border-b px-4 sm:px-6',
              embedded ? 'py-2' : 'pt-4 pb-3 sm:pt-6'
            )}
          >
            {renderListHeaderTitle()}
            {renderListHeaderDescription()}
          </DialogHeader>

          {/* 筛选和操作栏 */}
          <div className="shrink-0 border-b px-4 py-3 sm:px-6">
            <div className="flex flex-col gap-2 md:flex-row md:items-center">
              <Tabs
                value={filterType}
                onValueChange={(v) => setFilterType(v as typeof filterType)}
                className="w-full md:w-[24rem] md:shrink-0"
              >
                <TabsList className="grid w-full grid-cols-3">
                  <TabsTrigger value="unchecked" className="gap-1 px-1 text-xs sm:px-3 sm:text-sm">
                    <Clock className="h-3 w-3 sm:h-4 sm:w-4" />
                    <span className="hidden sm:inline">待审核</span>
                    <span className="sm:hidden">待审</span>
                    <span className="hidden sm:inline">({stats?.unchecked ?? 0})</span>
                  </TabsTrigger>
                  <TabsTrigger value="passed" className="gap-1 px-1 text-xs sm:px-3 sm:text-sm">
                    <CheckCircle2 className="h-3 w-3 sm:h-4 sm:w-4" />
                    <span className="hidden sm:inline">已通过</span>
                    <span className="sm:hidden">通过</span>
                    <span className="hidden sm:inline">({stats?.passed ?? 0})</span>
                  </TabsTrigger>
                  <TabsTrigger value="all" className="gap-1 px-1 text-xs sm:px-3 sm:text-sm">
                    <span>全部</span>
                    <span className="hidden sm:inline">({stats?.total ?? 0})</span>
                  </TabsTrigger>
                </TabsList>
              </Tabs>

              <div className="flex min-w-0 flex-col items-stretch gap-2 sm:flex-row sm:items-center md:flex-1">
                <div className="relative flex-1">
                  <Search className="text-muted-foreground absolute top-1/2 left-3 h-4 w-4 -translate-y-1/2" />
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
                  <div className="flex w-full items-center gap-2 sm:w-auto">
                    {filterType === 'unchecked' ? (
                      // 待审核：显示批量通过和批量拒绝
                      <>
                        <Button
                          variant="default"
                          size="sm"
                          className="flex-1 bg-green-600 hover:bg-green-700 sm:flex-none"
                          onClick={() => handleBatchReview(true)}
                          disabled={loading}
                        >
                          <CheckCircle2 className="mr-1 h-4 w-4" />
                          <span className="hidden sm:inline">批量通过</span>
                          <span className="sm:hidden">通过</span>({selectedIds.size})
                        </Button>
                        <Button
                          variant="destructive"
                          size="sm"
                          className="flex-1 sm:flex-none"
                          onClick={() => handleBatchReview(false)}
                          disabled={loading}
                        >
                          <XCircle className="mr-1 h-4 w-4" />
                          <span className="hidden sm:inline">批量拒绝</span>
                          <span className="sm:hidden">拒绝</span>({selectedIds.size})
                        </Button>
                      </>
                    ) : filterType === 'passed' ? (
                      // 已通过：只显示批量改为拒绝
                      <Button
                        variant="destructive"
                        size="sm"
                        className="flex-1 sm:flex-none"
                        onClick={() => handleBatchReview(false)}
                        disabled={loading}
                      >
                        <XCircle className="mr-1 h-4 w-4" />
                        <span className="hidden sm:inline">批量改为拒绝</span>
                        <span className="sm:hidden">改为拒绝</span>({selectedIds.size})
                      </Button>
                    ) : (
                      // 全部：显示两个按钮
                      <>
                        <Button
                          variant="default"
                          size="sm"
                          className="flex-1 bg-green-600 hover:bg-green-700 sm:flex-none"
                          onClick={() => handleBatchReview(true)}
                          disabled={loading}
                        >
                          <CheckCircle2 className="mr-1 h-4 w-4" />
                          <span className="hidden sm:inline">批量通过</span>
                          <span className="sm:hidden">通过</span>({selectedIds.size})
                        </Button>
                        <Button
                          variant="destructive"
                          size="sm"
                          className="flex-1 sm:flex-none"
                          onClick={() => handleBatchReview(false)}
                          disabled={loading}
                        >
                          <XCircle className="mr-1 h-4 w-4" />
                          <span className="hidden sm:inline">批量拒绝</span>
                          <span className="sm:hidden">拒绝</span>({selectedIds.size})
                        </Button>
                      </>
                    )}
                  </div>
                )}
              </div>
            </div>
          </div>

          {/* 列表区域 */}
          <ScrollArea className="flex-1 px-4 sm:px-6">
            {loading && expressions.length === 0 ? (
              <div className="flex h-40 items-center justify-center">
                <RefreshCw className="text-muted-foreground h-6 w-6 animate-spin" />
              </div>
            ) : expressions.length === 0 ? (
              <div className="text-muted-foreground flex h-40 flex-col items-center justify-center">
                <AlertCircle className="mb-2 h-8 w-8" />
                <p>没有找到表达方式</p>
              </div>
            ) : (
              <div className="space-y-2 py-2">
                {/* 全选 */}
                {expressions.length > 0 && (
                  <div className="bg-muted/50 flex items-center justify-between rounded-lg px-3 py-2">
                    <div className="flex items-center gap-2">
                      <Checkbox
                        checked={selectedIds.size === expressions.length && expressions.length > 0}
                        onCheckedChange={handleSelectAll}
                      />
                      <span className="text-muted-foreground text-sm">
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
                      'rounded-lg border p-2.5 transition-colors sm:p-3',
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
                      <div className="min-w-0 flex-1 space-y-1.5">
                        <div className="grid gap-2 sm:grid-cols-2 sm:items-start">
                          {/* 情景 */}
                          <div className="min-w-0">
                            <span className="text-muted-foreground text-xs">情景：</span>
                            <p className="text-sm font-medium break-words">{expr.situation}</p>
                          </div>

                          {/* 风格 */}
                          <div className="min-w-0">
                            <span className="text-muted-foreground text-xs">风格：</span>
                            <p className="text-muted-foreground text-sm break-words">
                              {expr.style}
                            </p>
                          </div>
                        </div>

                        {/* 元信息 */}
                        <div className="text-muted-foreground flex flex-wrap items-center gap-1 text-xs sm:gap-2">
                          <span>#{expr.id}</span>
                          <span>·</span>
                          <span title={getChatName(expr)} className="max-w-24 truncate sm:max-w-32">
                            {getChatName(expr)}
                          </span>
                          <span>·</span>
                          <span>{formatTime(expr.create_date)}</span>
                          <div className="flex items-center gap-1">{getReviewBadge(expr)}</div>
                        </div>
                      </div>

                      {/* 操作按钮 */}
                      <div className="flex shrink-0 items-center gap-1">
                        {filterType === 'unchecked' ? (
                          <>
                            <Button
                              size="sm"
                              variant="outline"
                              className="h-8 w-8 border-green-600 p-0 text-green-600 hover:bg-green-50 hover:text-green-700"
                              onClick={() => handleReview(expr.id, true)}
                              disabled={processingIds.has(expr.id)}
                              title="通过"
                            >
                              <CheckCircle2 className="h-4 w-4" />
                            </Button>
                            <Button
                              size="sm"
                              variant="outline"
                              className="h-8 w-8 border-red-600 p-0 text-red-600 hover:bg-red-50 hover:text-red-700"
                              onClick={() => handleReview(expr.id, false)}
                              disabled={processingIds.has(expr.id)}
                              title="拒绝"
                            >
                              <XCircle className="h-4 w-4" />
                            </Button>
                          </>
                        ) : filterType === 'passed' ? (
                          <Button
                            size="sm"
                            variant="outline"
                            className="h-8 w-8 border-red-600 p-0 text-red-600 hover:bg-red-50 hover:text-red-700"
                            onClick={() => handleReview(expr.id, false)}
                            disabled={processingIds.has(expr.id)}
                            title="改为拒绝"
                          >
                            <XCircle className="h-4 w-4" />
                          </Button>
                        ) : (
                          // all 模式下显示两个按钮
                          <>
                            {expr.checked ? (
                              <Button
                                size="sm"
                                variant="outline"
                                className="h-8 w-8 border-red-600 p-0 text-red-600 hover:bg-red-50 hover:text-red-700"
                                onClick={() => handleReview(expr.id, false)}
                                disabled={processingIds.has(expr.id)}
                                title="改为拒绝"
                              >
                                <XCircle className="h-4 w-4" />
                              </Button>
                            ) : (
                              <>
                                <Button
                                  size="sm"
                                  variant="outline"
                                  className="h-8 w-8 border-green-600 p-0 text-green-600 hover:bg-green-50 hover:text-green-700"
                                  onClick={() => handleReview(expr.id, true)}
                                  disabled={processingIds.has(expr.id)}
                                  title="通过"
                                >
                                  <CheckCircle2 className="h-4 w-4" />
                                </Button>
                                <Button
                                  size="sm"
                                  variant="outline"
                                  className="h-8 w-8 border-red-600 p-0 text-red-600 hover:bg-red-50 hover:text-red-700"
                                  onClick={() => handleReview(expr.id, false)}
                                  disabled={processingIds.has(expr.id)}
                                  title="拒绝"
                                >
                                  <XCircle className="h-4 w-4" />
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
          <div className="flex shrink-0 flex-col items-center justify-between gap-3 border-t px-4 py-3 sm:flex-row sm:px-6">
            {/* 左侧：每页显示数量 */}
            <div className="text-muted-foreground flex items-center gap-2 text-sm">
              <span className="hidden sm:inline">每页</span>
              <Select
                value={pageSize.toString()}
                onValueChange={(v) => {
                  setPageSize(parseInt(v, 10))
                  setPage(1) // 切换每页数量时重置到第一页
                }}
              >
                <SelectTrigger className="h-8 w-[70px]">
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
            <div className="hidden items-center gap-2 text-sm sm:flex">
              <span className="text-muted-foreground">跳至</span>
              <Input
                type="number"
                min={1}
                max={totalPages}
                value={jumpPage}
                onChange={(e) => setJumpPage(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && handleJumpPage()}
                className="h-8 w-16 text-center"
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
        <div className="flex flex-1 flex-col overflow-hidden">
          {/* 顶部筛选 */}
          <div className="shrink-0 border-b px-4 py-3 sm:px-6">
            <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
              <Tabs
                value={quickFilterType}
                onValueChange={(v) => setQuickFilterType(v as typeof quickFilterType)}
                className="w-full sm:flex-1"
              >
                <TabsList className="grid w-full grid-cols-3">
                  <TabsTrigger value="unchecked" className="gap-1 text-xs sm:text-sm">
                    <Clock className="h-3 w-3 sm:h-4 sm:w-4" />
                    <span className="hidden sm:inline">待审核</span>
                    <span className="sm:hidden">待审</span>
                    <span className="hidden sm:inline">({stats?.unchecked ?? 0})</span>
                  </TabsTrigger>
                  <TabsTrigger value="passed" className="gap-1 text-xs sm:text-sm">
                    <CheckCircle2 className="h-3 w-3 sm:h-4 sm:w-4" />
                    <span className="hidden sm:inline">已通过</span>
                    <span className="sm:hidden">通过</span>
                    <span className="hidden sm:inline">({stats?.passed ?? 0})</span>
                  </TabsTrigger>
                  <TabsTrigger value="all" className="gap-1 text-xs sm:text-sm">
                    <span>全部</span>
                    <span className="hidden sm:inline">({stats?.total ?? 0})</span>
                  </TabsTrigger>
                </TabsList>
              </Tabs>
              <Button
                variant="outline"
                size="sm"
                className="w-full sm:w-auto"
                onClick={() => {
                  loadQuickList()
                  loadStats()
                }}
                disabled={quickLoading}
              >
                <RefreshCw className={cn('mr-1 h-4 w-4', quickLoading && 'animate-spin')} />
                刷新
              </Button>
            </div>
          </div>

          {/* 卡片区域 */}
          <div className="relative flex min-h-0 flex-1 flex-col items-center justify-center gap-3 overflow-hidden p-3 sm:p-4">
            {quickLoading && quickExpressions.length === 0 ? (
              <div className="flex flex-col items-center justify-center">
                <ThinkingIllustration size="lg" />
              </div>
            ) : quickExpressions.length === 0 ? (
              <div className="flex flex-col items-center justify-center text-center">
                <div className="bg-muted/50 mb-6 flex h-20 w-20 items-center justify-center rounded-full">
                  <CheckCircle2 className="h-10 w-10 text-green-500" />
                </div>
                <h3 className="mb-2 text-xl font-semibold">全部审核完成！</h3>
                <p className="text-muted-foreground">当前筛选条件下没有待处理的项目</p>
              </div>
            ) : (
              <>
                {/* 进度提示 */}
                <div className="text-muted-foreground shrink-0 text-sm font-medium">
                  {Math.min(quickCurrentIndex + 1, Math.max(quickTotal, quickExpressions.length))} /{' '}
                  {Math.max(quickTotal, quickExpressions.length)}
                </div>

                {/* 方向提示 (仅针对当前卡片) */}
                <div className="pointer-events-none absolute inset-x-4 top-1/2 z-40 flex -translate-y-1/2 justify-between">
                  {(() => {
                    const currentExpr = quickExpressions[quickCurrentIndex]
                    const directions = getAllowedDirections(currentExpr)
                    return (
                      <>
                        <div
                          className={cn(
                            'flex items-center gap-2 rounded-lg px-4 py-2 transition-all duration-300',
                            swipeDirectionRef.current === 'left'
                              ? 'scale-110 bg-red-500/20 text-red-500'
                              : 'bg-muted/50 text-muted-foreground opacity-0',
                            !directions.left && 'invisible'
                          )}
                        >
                          <XCircle className="h-8 w-8" />
                          <span className="hidden text-lg font-bold sm:inline">拒绝</span>
                        </div>
                        <div
                          className={cn(
                            'flex items-center gap-2 rounded-lg px-4 py-2 transition-all duration-300',
                            swipeDirectionRef.current === 'right'
                              ? 'scale-110 bg-green-500/20 text-green-500'
                              : 'bg-muted/50 text-muted-foreground opacity-0',
                            !directions.right && 'invisible'
                          )}
                        >
                          <span className="hidden text-lg font-bold sm:inline">通过</span>
                          <CheckCircle2 className="h-8 w-8" />
                        </div>
                      </>
                    )
                  })()}
                </div>

                {/* 堆叠卡片 */}
                <div
                  className="relative flex max-h-[560px] min-h-[320px] w-full max-w-md flex-1 items-center justify-center"
                  role="listbox"
                  tabIndex={0}
                  aria-label="待审核的表达方式"
                  aria-activedescendant={
                    quickExpressions[quickCurrentIndex]
                      ? `quick-expr-${quickExpressions[quickCurrentIndex].id}`
                      : undefined
                  }
                >
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
                        transition:
                          isCurrent && !isDraggingRef.current
                            ? 'all 0.3s cubic-bezier(0.25, 0.8, 0.25, 1)'
                            : 'none',
                      }

                      if (isCurrent) {
                        // 当前卡片：样式由 useSpring 控制，通过 animated.div 渲染
                        // style 仅保留非动画属性
                        style = {
                          ...style,
                          cursor: 'grab',
                        }
                      } else {
                        // 后方卡片样式
                        const progress = Math.min(Math.abs(swipeOffsetRef.current) / 200, 1) // 0 to 1

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
                        const currentTranslateY =
                          base.translateY + (target.translateY - base.translateY) * progress
                        const currentRotate = base.rotate + (target.rotate - base.rotate) * progress
                        const currentTranslateX =
                          base.translateX + (target.translateX - base.translateX) * progress

                        style = {
                          ...style,
                          transform: `translate3d(${currentTranslateX}px, ${currentTranslateY}px, 0) scale(${currentScale}) rotate(${currentRotate}deg)`,
                          opacity: 1 - index * 0.15,
                          filter: `blur(${Math.max(0, index * 1.5 - progress)}px)`, // 模糊度也随之减小
                          pointerEvents: 'none',
                        }
                      }

                      return isCurrent ? (
                        <AnimatedDiv
                          key={expr.id}
                          ref={cardRef}
                          role="option"
                          id={`quick-expr-${expr.id}`}
                          aria-selected={true}
                          className={cn(
                            'bg-card flex h-full min-h-0 flex-col overflow-hidden rounded-xl border p-5 shadow-xl select-none sm:p-6',
                            'ring-border/50 shadow-2xl ring-1 active:cursor-grabbing',
                            // 冲突动效
                            conflictId === expr.id && 'bg-orange-50/10 ring-4 ring-orange-500/50'
                          )}
                          style={{ ...style, ...cardSpring }}
                          onMouseDown={handleMouseDown}
                          onMouseMove={handleMouseMove}
                          onMouseUp={handleMouseUp}
                          onMouseLeave={handleMouseLeave}
                          onTouchStart={handleTouchStart}
                          onTouchMove={handleTouchMove}
                          onTouchEnd={handleTouchEnd}
                        >
                          {/* 冲突提示遮罩 */}
                          {conflictId === expr.id && (
                            <div className="bg-background/80 animate-in fade-in absolute inset-0 z-50 flex flex-col items-center justify-center rounded-xl backdrop-blur-sm duration-300">
                              <div className="relative">
                                <div className="absolute inset-0 animate-ping rounded-full bg-orange-500/20" />
                                <RefreshCw className="relative mb-4 h-16 w-16 animate-spin text-orange-500 duration-1000" />
                              </div>
                              <h3 className="text-foreground animate-in slide-in-from-bottom-2 fade-in text-xl font-bold duration-500">
                                数据已更新
                              </h3>
                              <p className="text-muted-foreground animate-in slide-in-from-bottom-3 fade-in mt-2 duration-700">
                                后台任务已处理此条目
                              </p>
                            </div>
                          )}
                          {/* 无效操作提示 */}
                          <div
                            className={cn(
                              'pointer-events-none absolute inset-0 z-20 flex items-center justify-center transition-opacity duration-200',
                              (swipeOffsetRef.current < -10 && !getAllowedDirections(expr).left) ||
                                (swipeOffsetRef.current > 10 && !getAllowedDirections(expr).right)
                                ? 'opacity-100'
                                : 'opacity-0'
                            )}
                          >
                            <div className="bg-background/80 border-border rounded-full border p-4 shadow-lg backdrop-blur-sm">
                              <Ban className="text-muted-foreground h-12 w-12" />
                            </div>
                          </div>
                          {/* 内容区 */}
                          <div className="min-h-0 flex-1 space-y-3 overflow-hidden">
                            {/* 状态和ID */}
                            <div className="flex items-center justify-between">
                              <span className="text-muted-foreground font-mono text-sm">
                                #{expr.id}
                              </span>
                              <div className="flex items-center gap-2">{getReviewBadge(expr)}</div>
                            </div>
                            {/* 情景 */}
                            <div className="space-y-1.5">
                              <div className="text-muted-foreground text-xs font-semibold tracking-wider uppercase">
                                情景
                              </div>
                              <div className="bg-muted/30 border-border/50 rounded-lg border p-3">
                                <p className="line-clamp-3 text-base leading-relaxed font-medium break-words sm:text-lg">
                                  {expr.situation}
                                </p>
                              </div>
                            </div>
                            {/* 风格 */}
                            <div className="min-h-0 space-y-1.5">
                              <div className="text-muted-foreground text-xs font-semibold tracking-wider uppercase">
                                风格
                              </div>
                              <div className="flex max-h-24 flex-wrap gap-2 overflow-y-auto pr-1">
                                {expr.style.split(/[,，]/).map((s, i) => (
                                  <Badge key={i} variant="secondary" className="font-normal">
                                    {s.trim()}
                                  </Badge>
                                ))}
                              </div>
                            </div>
                          </div>
                          {/* 底部信息 */}
                          <div className="text-muted-foreground mt-3 flex shrink-0 items-center justify-between gap-3 border-t pt-3 text-xs">
                            <div className="flex min-w-0 items-center gap-2">
                              <div className="bg-primary/10 text-primary flex h-6 w-6 items-center justify-center rounded-full">
                                <User className="h-3 w-3" />
                              </div>
                              <span title={getChatName(expr)} className="truncate font-medium">
                                {getChatName(expr)}
                              </span>
                            </div>
                            <span className="shrink-0 font-mono">
                              {formatTime(expr.create_date)}
                            </span>
                          </div>
                        </AnimatedDiv>
                      ) : (
                        <div
                          key={expr.id}
                          role="option"
                          id={`quick-expr-${expr.id}`}
                          aria-selected={false}
                          className={cn(
                            'bg-card flex h-full min-h-0 flex-col overflow-hidden rounded-xl border p-5 shadow-xl select-none sm:p-6'
                          )}
                          style={style}
                        >
                          {/* 内容区 */}
                          <div className="min-h-0 flex-1 space-y-3 overflow-hidden">
                            {/* 状态和ID */}
                            <div className="flex items-center justify-between">
                              <span className="text-muted-foreground font-mono text-sm">
                                #{expr.id}
                              </span>
                              <div className="flex items-center gap-2">{getReviewBadge(expr)}</div>
                            </div>
                            {/* 情景 */}
                            <div className="space-y-1.5">
                              <div className="text-muted-foreground text-xs font-semibold tracking-wider uppercase">
                                情景
                              </div>
                              <div className="bg-muted/30 border-border/50 rounded-lg border p-3">
                                <p className="line-clamp-3 text-base leading-relaxed font-medium break-words sm:text-lg">
                                  {expr.situation}
                                </p>
                              </div>
                            </div>
                            {/* 风格 */}
                            <div className="min-h-0 space-y-1.5">
                              <div className="text-muted-foreground text-xs font-semibold tracking-wider uppercase">
                                风格
                              </div>
                              <div className="flex max-h-24 flex-wrap gap-2 overflow-y-auto pr-1">
                                {expr.style.split(/[,，]/).map((s, i) => (
                                  <Badge key={i} variant="secondary" className="font-normal">
                                    {s.trim()}
                                  </Badge>
                                ))}
                              </div>
                            </div>
                          </div>
                          {/* 底部信息 */}
                          <div className="text-muted-foreground mt-3 flex shrink-0 items-center justify-between gap-3 border-t pt-3 text-xs">
                            <div className="flex min-w-0 items-center gap-2">
                              <div className="bg-primary/10 text-primary flex h-6 w-6 items-center justify-center rounded-full">
                                <User className="h-3 w-3" />
                              </div>
                              <span title={getChatName(expr)} className="truncate font-medium">
                                {getChatName(expr)}
                              </span>
                            </div>
                            <span className="shrink-0 font-mono">
                              {formatTime(expr.create_date)}
                            </span>
                          </div>
                        </div>
                      )
                    })}
                </div>

                {/* 操作按钮（移动端） */}
                <div className="z-50 flex shrink-0 items-center gap-8 sm:hidden">
                  {(() => {
                    const currentExpr = quickExpressions[quickCurrentIndex]
                    const directions = getAllowedDirections(currentExpr)
                    return (
                      <>
                        <Button
                          variant="outline"
                          size="lg"
                          className={cn(
                            'h-16 w-16 rounded-full border-2 shadow-lg transition-all active:scale-95',
                            !directions.left
                              ? 'cursor-not-allowed opacity-30'
                              : 'hover:border-red-200 hover:bg-red-50 hover:text-red-600'
                          )}
                          onClick={() => directions.left && handleQuickReview(false)}
                          disabled={!directions.left || isAnimatingRef.current}
                        >
                          <XCircle className="h-8 w-8" />
                        </Button>
                        <Button
                          variant="outline"
                          size="lg"
                          className={cn(
                            'h-16 w-16 rounded-full border-2 shadow-lg transition-all active:scale-95',
                            !directions.right
                              ? 'cursor-not-allowed opacity-30'
                              : 'hover:border-green-200 hover:bg-green-50 hover:text-green-600'
                          )}
                          onClick={() => directions.right && handleQuickReview(true)}
                          disabled={!directions.right || isAnimatingRef.current}
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
          <div className="text-muted-foreground hidden shrink-0 flex-wrap items-center justify-center gap-x-5 gap-y-2 border-t px-4 py-2 text-xs sm:flex">
            <div className="flex items-center gap-1">
              <ShortcutKbd size="sm" keys={['left']} />
              <span>拒绝</span>
            </div>
            <div className="flex items-center gap-1">
              <ShortcutKbd size="sm" keys={['right']} />
              <span>通过</span>
            </div>
            <div className="flex items-center gap-1">
              <ShortcutKbd size="sm" keys={['up']} />
              <span>上一条</span>
            </div>
            <div className="flex items-center gap-1">
              <ShortcutKbd size="sm" keys={['down']} />
              <span>下一条</span>
            </div>
            <span className="text-muted-foreground/50">|</span>
            <span>拖拽卡片滑动审核</span>
          </div>
        </div>
      )}
    </div>
  )

  if (embedded) {
    return reviewerContent
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="flex h-[90vh] w-[95vw] max-w-5xl flex-col p-0 sm:h-[85vh] sm:w-full"
        hideCloseButton
      >
        {reviewerContent}
      </DialogContent>
    </Dialog>
  )
}
