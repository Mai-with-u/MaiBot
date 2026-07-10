/**
 * Pack 市场页面
 * 
 * 浏览、搜索、应用模型配置 Pack
 */

import { useState, useEffect } from 'react'
import { useNavigate } from '@tanstack/react-router'
import { 
  Package, 
  Search, 
  Download, 
  Heart, 
  Clock, 
  Tag,
  ChevronDown,
  ArrowUpDown,
  RefreshCw,
  User,
  Layers,
  Server,
  ListChecks,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { ScrollArea } from '@/components/ui/scroll-area'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import {
  Pagination,
  PaginationContent,
  PaginationItem,
  PaginationLink,
  PaginationNext,
  PaginationPrevious,
} from '@/components/ui/pagination'
import { Skeleton } from '@/components/ui/skeleton'
import { useDataList } from '@/hooks/useDataList'
import { toast } from '@/hooks/use-toast'
import { 
  listPacks, 
  togglePackLike, 
  checkPackLike,
  getPackUserId,
  type PackListItem,
  type ListPacksResponse,
} from '@/lib/pack-api'

// 排序选项
const SORT_OPTIONS = [
  { value: 'created_at', label: '最新发布', icon: Clock },
  { value: 'downloads', label: '下载最多', icon: Download },
  { value: 'likes', label: '最受欢迎', icon: Heart },
] as const

type SortBy = typeof SORT_OPTIONS[number]['value']

export default function PackMarketPage() {
  const navigate = useNavigate()
  const [likedPacks, setLikedPacks] = useState<Set<string>>(new Set())
  const [likingPacks, setLikingPacks] = useState<Set<string>>(new Set())

  const userId = getPackUserId()

  // Pack 列表：分页/搜索/排序统一由 useDataList 承载（固定每页 12，无多选）
  const list = useDataList<PackListItem, { sortBy: SortBy }, string>({
    domain: 'pack-market',
    getId: (pack) => pack.id,
    initialFilters: { sortBy: 'downloads' },
    initialPageSize: 12,
    searchDebounceMs: 300,
    queryFn: async ({ page, pageSize, search, filters }) => {
      const response: ListPacksResponse = await listPacks({
        status: 'approved',
        page,
        page_size: pageSize,
        search: search || undefined,
        sort_by: filters.sortBy,
        sort_order: 'desc',
      })
      return { items: response.packs, total: response.total }
    },
  })
  const packs = list.items
  const total = list.total
  const loading = list.isPending

  // 点赞状态：随当前页 Pack 旁路加载，以 id 列表为键，避免列表重取但 id 不变时重复检查
  const packIdsKey = packs.map((pack) => pack.id).join(',')
  useEffect(() => {
    let cancelled = false
    async function loadLikes() {
      const likedSet = new Set<string>()
      for (const pack of packs) {
        const liked = await checkPackLike(pack.id, userId)
        if (liked) likedSet.add(pack.id)
      }
      if (!cancelled) {
        setLikedPacks(likedSet)
      }
    }
    loadLikes()
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [packIdsKey, userId])

  // 点赞
  const handleLike = async (packId: string) => {
    if (likingPacks.has(packId)) return
    
    setLikingPacks(prev => new Set(prev).add(packId))
    try {
      const result = await togglePackLike(packId, userId)
      
      // 更新点赞状态
      setLikedPacks(prev => {
        const newSet = new Set(prev)
        if (result.liked) {
          newSet.add(packId)
        } else {
          newSet.delete(packId)
        }
        return newSet
      })
      
      // 刷新点赞数（从服务端同步）
      list.invalidate()
    } catch (error) {
      console.error('点赞失败:', error)
      toast({ title: '点赞失败', variant: 'destructive' })
    } finally {
      setLikingPacks(prev => {
        const newSet = new Set(prev)
        newSet.delete(packId)
        return newSet
      })
    }
  }
  
  // 查看详情
  const handleViewPack = (packId: string) => {
    navigate({ to: '/config/pack-market/$packId', params: { packId } })
  }
  
  // 获取当前排序选项
  const currentSort = SORT_OPTIONS.find(o => o.value === list.filters.sortBy) || SORT_OPTIONS[0]
  
  return (
    <div className="h-[calc(100vh-4rem)] flex flex-col p-4 sm:p-6">
      {/* 页面标题 */}
      <div className="mb-4 sm:mb-6">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
          <div>
            <h1 className="text-2xl sm:text-3xl font-bold flex items-center gap-2">
              <Package className="h-8 w-8" strokeWidth={2} />
              配置模板市场
            </h1>
            <p className="text-muted-foreground mt-1 text-sm sm:text-base">
              浏览和应用社区分享的模型配置模板，快速配置你的 MaiBot
            </p>
          </div>
          <Button variant="outline" onClick={() => list.refetch()} disabled={list.isFetching} className="gap-2">
            <RefreshCw className={`h-4 w-4 ${list.isFetching ? 'animate-spin' : ''}`} />
            刷新
          </Button>
        </div>
      </div>
      
      <ScrollArea className="flex-1">
        <div className="space-y-4">
          {/* 搜索和筛选 */}
          <div className="flex gap-4 flex-wrap">
            <form onSubmit={(e) => e.preventDefault()} className="flex-1 min-w-[200px] max-w-md">
              <div className="relative">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
                <Input
                  placeholder="搜索模板名称、描述..."
                  value={list.searchInput}
                  onChange={e => list.setSearchInput(e.target.value)}
                  className="pl-10"
                />
              </div>
            </form>
            
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="outline" className="min-w-[140px] gap-2">
                  <ArrowUpDown className="w-4 h-4" />
                  {currentSort.label}
                  <ChevronDown className="w-4 h-4 ml-auto" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                {SORT_OPTIONS.map(option => (
                  <DropdownMenuItem
                    key={option.value}
                    onClick={() => list.setFilter('sortBy', option.value)}
                  >
                    <option.icon className="w-4 h-4 mr-2" />
                    {option.label}
                  </DropdownMenuItem>
                ))}
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
          
          {/* 统计信息 */}
          <div className="text-sm text-muted-foreground">
            共找到 <span className="font-medium text-foreground">{total}</span> 个模板
          </div>
          
              {/* Pack 列表 */}
          {loading ? (
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
              {Array.from({ length: 6 }).map((_, i) => (
                <Card key={i}>
                  <CardHeader>
                    <Skeleton className="h-6 w-3/4" />
                    <Skeleton className="h-4 w-full mt-2" />
                  </CardHeader>
                  <CardContent>
                    <Skeleton className="h-20 w-full" />
                  </CardContent>
                  <CardFooter>
                    <Skeleton className="h-9 w-full" />
                  </CardFooter>
                </Card>
              ))}
            </div>
          ) : list.isError ? (
            <Card className="py-12">
              <CardContent className="text-center text-muted-foreground space-y-3">
                <Package className="w-12 h-12 mx-auto opacity-50" />
                <p className="text-destructive">{list.error?.message ?? '加载 Pack 列表失败'}</p>
                <Button variant="outline" size="sm" onClick={() => list.refetch()}>重试</Button>
              </CardContent>
            </Card>
          ) : packs.length === 0 ? (
            <Card className="py-12">
              <CardContent className="text-center text-muted-foreground">
                <Package className="w-12 h-12 mx-auto mb-4 opacity-50" />
                <p className="text-lg font-medium">暂无模板</p>
                <p className="mt-1">当前还没有可用的配置模板</p>
              </CardContent>
            </Card>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
              {packs.map(pack => (
                <PackCard
                  key={pack.id}
                  pack={pack}
                  liked={likedPacks.has(pack.id)}
                  liking={likingPacks.has(pack.id)}
                  onLike={() => handleLike(pack.id)}
                  onView={() => handleViewPack(pack.id)}
                />
              ))}
            </div>
          )}
          
          {/* 分页 */}
          {list.totalPages > 1 && (
            <Pagination>
              <PaginationContent>
                <PaginationItem>
                  <PaginationPrevious
                    onClick={() => list.goToPage(list.page - 1)}
                    className={list.page === 1 ? 'pointer-events-none opacity-50' : 'cursor-pointer'}
                  />
                </PaginationItem>
                {Array.from({ length: list.totalPages }, (_, i) => i + 1)
                  .filter(p => p === 1 || p === list.totalPages || Math.abs(p - list.page) <= 1)
                  .map((p, i, arr) => {
                    const showEllipsis = i > 0 && p - arr[i - 1] > 1
                    return (
                      <PaginationItem key={p}>
                        {showEllipsis && <span className="px-2">...</span>}
                        <PaginationLink
                          onClick={() => list.goToPage(p)}
                          isActive={p === list.page}
                          className="cursor-pointer"
                        >
                          {p}
                        </PaginationLink>
                      </PaginationItem>
                    )
                  })}
                <PaginationItem>
                  <PaginationNext
                    onClick={() => list.goToPage(list.page + 1)}
                    className={list.page === list.totalPages ? 'pointer-events-none opacity-50' : 'cursor-pointer'}
                  />
                </PaginationItem>
              </PaginationContent>
            </Pagination>
          )}
        </div>
      </ScrollArea>
    </div>
  )
}

// Pack 卡片组件
function PackCard({ 
  pack, 
  liked, 
  liking,
  onLike, 
  onView,
}: { 
  pack: PackListItem
  liked: boolean
  liking: boolean
  onLike: () => void
  onView: () => void
}) {
  const formatDate = (dateStr: string) => {
    const date = new Date(dateStr)
    return date.toLocaleDateString('zh-CN', { 
      year: 'numeric', 
      month: 'short', 
      day: 'numeric' 
    })
  }
  
  return (
    <Card className="flex flex-col hover:shadow-md transition-shadow">
      <CardHeader className="pb-3">
        <div className="flex items-start justify-between">
          <CardTitle className="text-lg line-clamp-1">{pack.name}</CardTitle>
          <Badge variant="secondary" className="text-xs">v{pack.version}</Badge>
        </div>
        <CardDescription className="line-clamp-2 min-h-[40px]">
          {pack.description}
        </CardDescription>
      </CardHeader>
      
      <CardContent className="flex-1 space-y-3">
        {/* 作者和日期 */}
        <div className="flex items-center justify-between text-sm text-muted-foreground">
          <span className="flex items-center gap-1">
            <User className="w-3.5 h-3.5" />
            {pack.author}
          </span>
          <span className="flex items-center gap-1">
            <Clock className="w-3.5 h-3.5" />
            {formatDate(pack.created_at)}
          </span>
        </div>
        
        {/* 内容统计 */}
        <div className="flex gap-4 text-sm">
          <span className="flex items-center gap-1 text-muted-foreground" title="提供商数量">
            <Server className="w-3.5 h-3.5" />
            {pack.provider_count}
          </span>
          <span className="flex items-center gap-1 text-muted-foreground" title="模型数量">
            <Layers className="w-3.5 h-3.5" />
            {pack.model_count}
          </span>
          <span className="flex items-center gap-1 text-muted-foreground" title="任务配置数">
            <ListChecks className="w-3.5 h-3.5" />
            {pack.task_count}
          </span>
        </div>
        
        {/* 标签 */}
        {pack.tags && pack.tags.length > 0 && (
          <div className="flex flex-wrap gap-1">
            {pack.tags.slice(0, 3).map(tag => (
              <Badge key={tag} variant="outline" className="text-xs">
                <Tag className="w-2.5 h-2.5 mr-1" />
                {tag}
              </Badge>
            ))}
            {pack.tags.length > 3 && (
              <Badge variant="outline" className="text-xs">
                +{pack.tags.length - 3}
              </Badge>
            )}
          </div>
        )}
      </CardContent>
      
      <CardFooter className="pt-3 border-t">
        <div className="flex items-center justify-between w-full">
          {/* 统计 */}
          <div className="flex items-center gap-3 text-sm text-muted-foreground">
            <span className="flex items-center gap-1">
              <Download className="w-4 h-4" />
              {pack.downloads}
            </span>
            <button 
              onClick={e => { e.stopPropagation(); onLike() }}
              disabled={liking}
              className={`flex items-center gap-1 transition-colors ${
                liked ? 'text-red-500' : 'hover:text-red-500'
              } ${liking ? 'opacity-50' : ''}`}
            >
              <Heart className={`w-4 h-4 ${liked ? 'fill-current' : ''}`} />
              {pack.likes}
            </button>
          </div>
          
          {/* 查看按钮 */}
          <Button size="sm" onClick={onView}>
            查看详情
          </Button>
        </div>
      </CardFooter>
    </Card>
  )
}
