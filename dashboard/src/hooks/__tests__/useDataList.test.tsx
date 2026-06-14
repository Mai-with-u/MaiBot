import type { ReactNode } from 'react'

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useDataList } from '../useDataList'

interface Row {
  id: number
  name: string
}

interface RowFilters {
  status?: string
  flagged?: boolean
}

function makeWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
}

/** 构造一个返回固定页数据的 queryFn 间谍 */
function makeQueryFn(total = 95) {
  return vi.fn(async (params: { page: number; pageSize: number }) => {
    const items: Row[] = Array.from({ length: params.pageSize }, (_, i) => ({
      id: (params.page - 1) * params.pageSize + i + 1,
      name: `row-${i}`,
    }))
    return { items, total }
  })
}

function renderDataList(
  overrides: Partial<Parameters<typeof useDataList<Row, RowFilters, number>>[0]> = {},
) {
  const queryFn = overrides.queryFn ?? makeQueryFn()
  const result = renderHook(
    () =>
      useDataList<Row, RowFilters, number>({
        domain: 'rows',
        getId: (row) => row.id,
        initialFilters: { status: undefined, flagged: undefined },
        initialPageSize: 20,
        queryFn,
        ...overrides,
      }),
    { wrapper: makeWrapper() },
  )
  return { ...result, queryFn }
}

afterEach(() => {
  vi.useRealTimers()
})

describe('useDataList', () => {
  describe('初始状态与数据派生', () => {
    it('初始页码为 1、采用配置的页大小、选中集为空', () => {
      const { result } = renderDataList()
      expect(result.current.page).toBe(1)
      expect(result.current.pageSize).toBe(20)
      expect(result.current.searchInput).toBe('')
      expect(result.current.selectedCount).toBe(0)
      expect(result.current.isPending).toBe(true)
    })

    it('加载完成后派生 items / total / totalPages', async () => {
      const { result } = renderDataList({ queryFn: makeQueryFn(95) })
      await waitFor(() => expect(result.current.isPending).toBe(false))
      expect(result.current.items).toHaveLength(20)
      expect(result.current.total).toBe(95)
      expect(result.current.totalPages).toBe(5) // ceil(95 / 20)
    })

    it('queryFn 收到当前分页/搜索/筛选参数', async () => {
      const { result, queryFn } = renderDataList()
      await waitFor(() => expect(result.current.isPending).toBe(false))
      expect(queryFn).toHaveBeenCalledWith({
        page: 1,
        pageSize: 20,
        search: '',
        filters: { status: undefined, flagged: undefined },
      })
    })
  })

  describe('分页', () => {
    it('goToPage 钳制到 [1, totalPages]', async () => {
      const { result } = renderDataList({ queryFn: makeQueryFn(95) }) // 5 页
      await waitFor(() => expect(result.current.totalPages).toBe(5))

      act(() => result.current.goToPage(3))
      expect(result.current.page).toBe(3)

      act(() => result.current.goToPage(99))
      expect(result.current.page).toBe(5) // 上钳到 totalPages

      act(() => result.current.goToPage(0))
      expect(result.current.page).toBe(1) // 下钳到 1
    })

    it('改页大小重置页码到 1', async () => {
      const { result } = renderDataList({ queryFn: makeQueryFn(95) })
      await waitFor(() => expect(result.current.totalPages).toBe(5))
      act(() => result.current.goToPage(4))
      expect(result.current.page).toBe(4)

      act(() => result.current.setPageSize(50))
      expect(result.current.pageSize).toBe(50)
      expect(result.current.page).toBe(1)
    })
  })

  describe('参数变化重置页码', () => {
    it('改筛选时重置页码到 1 并更新筛选值', async () => {
      const { result } = renderDataList({ queryFn: makeQueryFn(95) })
      await waitFor(() => expect(result.current.totalPages).toBe(5))
      act(() => result.current.goToPage(3))

      act(() => result.current.setFilter('status', 'active'))
      expect(result.current.page).toBe(1)
      expect(result.current.filters.status).toBe('active')
    })

    it('改搜索输入时重置页码到 1', async () => {
      const { result } = renderDataList({ queryFn: makeQueryFn(95) })
      await waitFor(() => expect(result.current.totalPages).toBe(5))
      act(() => result.current.goToPage(3))

      act(() => result.current.setSearchInput('hello'))
      expect(result.current.page).toBe(1)
      expect(result.current.searchInput).toBe('hello')
    })

    it('resetFilters 回到初始筛选并重置页码', async () => {
      const { result } = renderDataList({ queryFn: makeQueryFn(95) })
      await waitFor(() => expect(result.current.totalPages).toBe(5))
      act(() => result.current.setFilter('status', 'active'))
      act(() => result.current.goToPage(1))

      act(() => result.current.resetFilters())
      expect(result.current.filters).toEqual({ status: undefined, flagged: undefined })
    })
  })

  describe('多选', () => {
    it('toggle 增删单个 id，selectedCount 与 isSelected 同步', async () => {
      const { result } = renderDataList()
      await waitFor(() => expect(result.current.isPending).toBe(false))

      act(() => result.current.toggle(3))
      expect(result.current.isSelected(3)).toBe(true)
      expect(result.current.selectedCount).toBe(1)

      act(() => result.current.toggle(3))
      expect(result.current.isSelected(3)).toBe(false)
      expect(result.current.selectedCount).toBe(0)
    })

    it('toggleAll 全选当前页可见项，再次调用全部取消', async () => {
      const { result } = renderDataList({ queryFn: makeQueryFn(95) })
      await waitFor(() => expect(result.current.items).toHaveLength(20))

      act(() => result.current.toggleAll())
      expect(result.current.selectedCount).toBe(20)

      act(() => result.current.toggleAll())
      expect(result.current.selectedCount).toBe(0)
    })

    it('翻页时清空选中（避免跨页残留）', async () => {
      const { result } = renderDataList({ queryFn: makeQueryFn(95) })
      await waitFor(() => expect(result.current.items).toHaveLength(20))
      act(() => result.current.toggle(1))
      expect(result.current.selectedCount).toBe(1)

      act(() => result.current.goToPage(2))
      expect(result.current.selectedCount).toBe(0)
    })

    it('改筛选/改搜索时清空选中', async () => {
      const { result } = renderDataList({ queryFn: makeQueryFn(95) })
      await waitFor(() => expect(result.current.items).toHaveLength(20))

      act(() => result.current.toggle(1))
      act(() => result.current.setFilter('flagged', true))
      expect(result.current.selectedCount).toBe(0)

      act(() => result.current.toggle(2))
      act(() => result.current.setSearchInput('x'))
      expect(result.current.selectedCount).toBe(0)
    })
  })

  describe('搜索防抖', () => {
    beforeEach(() => vi.useFakeTimers())

    it('searchDebounceMs > 0 时，防抖值在延迟后才驱动 queryFn', async () => {
      const queryFn = makeQueryFn()
      const { result } = renderHook(
        () =>
          useDataList<Row, RowFilters, number>({
            domain: 'rows',
            getId: (row) => row.id,
            initialFilters: {},
            searchDebounceMs: 300,
            queryFn,
          }),
        { wrapper: makeWrapper() },
      )

      // 连续输入：searchInput 立即变化，但防抖后的 search 尚未更新
      act(() => result.current.setSearchInput('a'))
      act(() => result.current.setSearchInput('ab'))
      expect(result.current.searchInput).toBe('ab')

      // 推进定时器，防抖值落定，queryFn 以最终值再次被调用
      await act(async () => {
        vi.advanceTimersByTime(300)
      })
      await vi.waitFor(() =>
        expect(queryFn).toHaveBeenLastCalledWith(
          expect.objectContaining({ search: 'ab', page: 1 }),
        ),
      )
    })
  })
})
