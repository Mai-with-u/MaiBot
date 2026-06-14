/**
 * useDataList —— 对服务端集合的「分页 + 搜索 + 筛选 + 多选」视图（数据列表，DataList）。
 *
 * 把列表页历来各自重复的那组状态（分页三件套、搜索防抖、筛选、多选）连同列表查询一并收编：
 * - 内部包一个列表查询，queryKey 从分页/搜索/筛选状态派生，参数变化自动重新拉取；
 * - 筛选 / 搜索 / 翻页 / 改页大小时自动重置页码并清空选中集（避免跨页残留导致误删）；
 * - 搜索可配置防抖（受控输入 + 内部防抖值驱动查询）；
 * - 对话框与具体渲染（表格 / 卡片）留在各页面，不进本 hook。
 *
 * queryFn 由页面在调用处适配各自的 API 形状，统一返回 { items, total }。
 * 失败沿用请求客户端的 throw 契约（ApiError），由 useQuery 暴露为 error。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { keepPreviousData, useQuery, useQueryClient } from '@tanstack/react-query'

export interface DataListQueryParams<TFilters> {
  page: number
  pageSize: number
  /** 已防抖的搜索值（searchDebounceMs 为 0 时即为输入框当前值） */
  search: string
  filters: TFilters
}

export interface UseDataListConfig<TItem, TFilters, TId> {
  /** queryKey 前缀，也是 invalidate 的范围（覆盖同领域的兄弟查询，如统计） */
  domain: string
  /** 从行取 id，用于多选 */
  getId: (item: TItem) => TId
  /** 筛选初始值；setFilter 在此形状上增量更新 */
  initialFilters: TFilters
  /** 每页条数初始值，默认 20 */
  initialPageSize?: number
  /** 搜索防抖毫秒，默认 0（不防抖、输入即生效） */
  searchDebounceMs?: number
  /** 列表请求；页面在此把各自 API 形状适配为 { items, total } */
  queryFn: (params: DataListQueryParams<TFilters>) => Promise<{ items: TItem[]; total: number }>
  /** 透传给底层 useQuery 的部分选项（如条件列表的 enabled、覆盖 staleTime） */
  queryOptions?: { enabled?: boolean; staleTime?: number }
}

export interface UseDataListResult<TItem, TFilters, TId> {
  // 数据（来自内部 useQuery）
  items: TItem[]
  total: number
  totalPages: number
  isPending: boolean
  isFetching: boolean
  isError: boolean
  error: Error | null
  refetch: () => void
  /** 失效 [domain] 前缀，覆盖列表与同领域的兄弟查询 */
  invalidate: () => void
  // 分页（goToPage / setPage 钳到 [1, totalPages]；改 pageSize 重置页码并清空选中）
  page: number
  setPage: (n: number) => void
  goToPage: (n: number) => void
  pageSize: number
  setPageSize: (n: number) => void
  // 搜索（受控输入；内部防抖值驱动查询，变更重置页码并清空选中）
  searchInput: string
  setSearchInput: (value: string) => void
  // 筛选（泛型袋；setFilter 自动重置页码并清空选中）
  filters: TFilters
  setFilter: <K extends keyof TFilters>(key: K, value: TFilters[K]) => void
  resetFilters: () => void
  // 多选（参数变即清空；toggleAll 只针对当前页可见项）
  selectedIds: Set<TId>
  toggle: (id: TId) => void
  toggleAll: () => void
  isSelected: (id: TId) => boolean
  clearSelection: () => void
  selectedCount: number
}

export function useDataList<TItem, TFilters, TId = string>(
  config: UseDataListConfig<TItem, TFilters, TId>,
): UseDataListResult<TItem, TFilters, TId> {
  const { domain, initialPageSize = 20, searchDebounceMs = 0 } = config

  const [page, setPageRaw] = useState(1)
  const [pageSize, setPageSizeRaw] = useState(initialPageSize)
  const [searchInput, setSearchInputRaw] = useState('')
  const [debouncedSearch, setDebouncedSearch] = useState('')
  const [filters, setFilters] = useState<TFilters>(config.initialFilters)
  const [selectedIds, setSelectedIds] = useState<Set<TId>>(() => new Set())

  // 初始筛选只取首次值，避免页面传入内联对象导致的重置抖动
  const initialFiltersRef = useRef(config.initialFilters)

  // 搜索防抖：仅在 searchDebounceMs > 0 时启用定时器；定时器回调里的 setState 是异步的，不触发渲染期约束
  useEffect(() => {
    if (searchDebounceMs <= 0) {
      return
    }
    const timer = setTimeout(() => setDebouncedSearch(searchInput), searchDebounceMs)
    return () => clearTimeout(timer)
  }, [searchInput, searchDebounceMs])
  const search = searchDebounceMs > 0 ? debouncedSearch : searchInput

  // 参数变化时清空选中：仅在非空时新建 Set，避免空集反复重建触发渲染
  const clearSelection = useCallback(() => {
    setSelectedIds((prev) => (prev.size === 0 ? prev : new Set()))
  }, [])

  const query = useQuery({
    queryKey: [domain, 'list', { page, pageSize, search, filters }],
    queryFn: () => config.queryFn({ page, pageSize, search, filters }),
    // 翻页/改参时保留上一页数据（含 total）直到新页落定：稳住分页钳制、避免骨架屏闪烁
    placeholderData: keepPreviousData,
    ...config.queryOptions,
  })

  // useMemo 稳定空数组引用，避免 query 无数据时 items 每次渲染换引用、波及 toggleAll 依赖
  const items = useMemo(() => query.data?.items ?? [], [query.data?.items])
  const total = query.data?.total ?? 0
  const totalPages = Math.max(1, Math.ceil(total / pageSize))

  const goToPage = useCallback(
    (n: number) => {
      setPageRaw(Math.min(Math.max(1, Math.trunc(n)), totalPages))
      clearSelection()
    },
    [totalPages, clearSelection],
  )

  const setSearchInput = useCallback(
    (value: string) => {
      setSearchInputRaw(value)
      setPageRaw(1)
      clearSelection()
    },
    [clearSelection],
  )

  const setPageSize = useCallback(
    (n: number) => {
      setPageSizeRaw(n)
      setPageRaw(1)
      clearSelection()
    },
    [clearSelection],
  )

  const setFilter = useCallback(
    <K extends keyof TFilters>(key: K, value: TFilters[K]) => {
      setFilters((prev) => ({ ...prev, [key]: value }))
      setPageRaw(1)
      clearSelection()
    },
    [clearSelection],
  )

  const resetFilters = useCallback(() => {
    setFilters(initialFiltersRef.current)
    setPageRaw(1)
    clearSelection()
  }, [clearSelection])

  // 多选：toggleAll 针对当前页可见项
  const { getId } = config

  const toggle = useCallback((id: TId) => {
    setSelectedIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) {
        next.delete(id)
      } else {
        next.add(id)
      }
      return next
    })
  }, [])

  const toggleAll = useCallback(() => {
    const pageIds = items.map((item) => getId(item))
    setSelectedIds((prev) => {
      const allSelected = pageIds.length > 0 && pageIds.every((id) => prev.has(id))
      const next = new Set(prev)
      if (allSelected) {
        pageIds.forEach((id) => next.delete(id))
      } else {
        pageIds.forEach((id) => next.add(id))
      }
      return next
    })
  }, [items, getId])

  const isSelected = useCallback((id: TId) => selectedIds.has(id), [selectedIds])

  const queryClient = useQueryClient()
  const invalidate = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: [domain] })
  }, [queryClient, domain])

  return {
    items,
    total,
    totalPages,
    isPending: query.isPending,
    isFetching: query.isFetching,
    isError: query.isError,
    error: query.error,
    refetch: query.refetch,
    invalidate,
    page,
    setPage: goToPage,
    goToPage,
    pageSize,
    setPageSize,
    searchInput,
    setSearchInput,
    filters,
    setFilter,
    resetFilters,
    selectedIds,
    toggle,
    toggleAll,
    isSelected,
    clearSelection,
    selectedCount: selectedIds.size,
  }
}
