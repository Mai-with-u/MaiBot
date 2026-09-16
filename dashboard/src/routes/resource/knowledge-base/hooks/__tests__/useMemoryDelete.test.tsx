import type { ReactNode } from 'react'

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  DELETE_OPERATION_FETCH_LIMIT,
  DELETE_OPERATION_ITEM_PAGE_SIZE,
  DELETE_OPERATION_PAGE_SIZE,
} from '../../constants'
import { useMemoryDelete, type UseMemoryDeleteOptions } from '../useMemoryDelete'

import type { MemoryDeleteOperationPayload } from '@/lib/memory-api'
import * as memoryApi from '@/lib/memory-api'

const toastMock = vi.hoisted(() => vi.fn())

vi.mock('@/hooks/use-toast', () => ({ useToast: () => ({ toast: toastMock }) }))

vi.mock('@/lib/memory-api', () => ({
  executeMemoryDelete: vi.fn(),
  getMemoryDeleteOperation: vi.fn(),
  getMemoryDeleteOperations: vi.fn(),
  getMemorySources: vi.fn(),
  previewMemoryDelete: vi.fn(),
  restoreMemoryDelete: vi.fn(),
}))

function renderDeleteHook(options: Partial<UseMemoryDeleteOptions> = {}) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
  return {
    queryClient,
    ...renderHook(() => useMemoryDelete({ active: false, ...options }), { wrapper }),
  }
}

function makeOperation(
  overrides: Partial<MemoryDeleteOperationPayload> = {},
): MemoryDeleteOperationPayload {
  return {
    operation_id: 'op-1',
    mode: 'source',
    status: 'executed',
    reason: '清理测试批次',
    requested_by: 'alice',
    summary: {
      counts: { entities: 1, relations: 2, paragraphs: 3, sources: 4 },
      sources: ['chat:alpha'],
    },
    ...overrides,
  }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

beforeEach(() => {
  vi.mocked(memoryApi.getMemorySources).mockResolvedValue({ success: true, items: [], count: 0 })
  vi.mocked(memoryApi.getMemoryDeleteOperations).mockResolvedValue({ success: true, items: [] })
  vi.mocked(memoryApi.previewMemoryDelete).mockResolvedValue({
    success: true,
    mode: 'source',
    selector: { sources: ['source-a'] },
    counts: { sources: 1, paragraphs: 1 },
    sources: ['source-a'],
    items: [{ item_type: 'paragraph', item_hash: 'paragraph-1' }],
    item_count: 1,
    dry_run: true,
  })
  vi.mocked(memoryApi.executeMemoryDelete).mockResolvedValue({
    success: true,
    mode: 'source',
    operation_id: 'operation-1',
    counts: { sources: 1, paragraphs: 1 },
    sources: ['source-a'],
    deleted_count: 2,
    deleted_entity_count: 0,
    deleted_relation_count: 0,
    deleted_paragraph_count: 1,
    deleted_source_count: 1,
  })
  vi.mocked(memoryApi.restoreMemoryDelete).mockResolvedValue({ success: true })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('useMemoryDelete 写入后刷新', () => {
  it('删除成功后的刷新失败不会显示删除失败', async () => {
    const { queryClient, result } = renderDeleteHook()
    vi.spyOn(queryClient, 'invalidateQueries').mockRejectedValue(new Error('记录刷新超时'))

    await act(async () => {
      await result.current.openDeletePreview({
        mode: 'source',
        selector: { sources: ['source-a'] },
      })
    })
    await waitFor(() => {
      expect(result.current.deletePreview).not.toBeNull()
    })
    await act(async () => {
      await result.current.executePendingDelete()
    })

    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith(expect.objectContaining({ title: '删除成功' }))
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({
          title: '删除已完成，但数据刷新失败',
          description: '记录刷新超时',
        })
      )
    })
    expect(toastMock).not.toHaveBeenCalledWith(expect.objectContaining({ title: '删除失败' }))
  })

  it('恢复成功后的刷新失败不会显示恢复失败', async () => {
    const { queryClient, result } = renderDeleteHook()
    vi.spyOn(queryClient, 'invalidateQueries').mockRejectedValue(new Error('列表刷新超时'))

    await act(async () => {
      await result.current.restoreDeleteOperation('operation-1')
    })

    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith(expect.objectContaining({ title: '恢复成功' }))
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({
          title: '恢复已完成，但数据刷新失败',
          description: '列表刷新超时',
        })
      )
    })
    expect(toastMock).not.toHaveBeenCalledWith(expect.objectContaining({ title: '恢复失败' }))
  })
})

async function waitForOperations(
  result: { current: { deleteOperations: MemoryDeleteOperationPayload[] } },
  count: number,
) {
  await waitFor(() => expect(result.current.deleteOperations).toHaveLength(count))
}

describe('useMemoryDelete 预览失败', () => {
  it('预览抛 Error 时写入 deletePreviewError，对话框保持打开', async () => {
    vi.mocked(memoryApi.previewMemoryDelete).mockRejectedValue(new Error('预览超时'))
    const { result } = renderDeleteHook()

    await act(async () => {
      await result.current.openDeletePreview({
        mode: 'source',
        selector: { sources: ['source-a'] },
      })
    })

    expect(result.current.deleteDialogOpen).toBe(true)
    expect(result.current.deleteDialogTitle).toBe('删除预览')
    expect(result.current.deletePreview).toBeNull()
    expect(result.current.deletePreviewError).toBe('预览超时')
    expect(result.current.deletePreviewLoading).toBe(false)
  })

  it('预览抛非 Error 时使用兜底文案「删除预览失败」', async () => {
    vi.mocked(memoryApi.previewMemoryDelete).mockRejectedValue('preview-down')
    const { result } = renderDeleteHook()

    await act(async () => {
      await result.current.openDeletePreview(
        { mode: 'paragraph', selector: { hashes: ['p-1'] } },
        { title: '删除段落', description: '仅删除当前段落' },
      )
    })

    expect(result.current.deleteDialogTitle).toBe('删除段落')
    expect(result.current.deleteDialogDescription).toBe('仅删除当前段落')
    expect(result.current.deletePreviewError).toBe('删除预览失败')
    expect(result.current.deleteDialogOpen).toBe(true)
  })

  it('未选择来源时拦截预览，不请求 preview 接口', async () => {
    const { result } = renderDeleteHook()

    await act(async () => {
      await result.current.openSourceDeletePreview()
    })

    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({
        title: '请选择来源',
        description: '至少选择一个来源后再进行删除预览',
        variant: 'destructive',
      }),
    )
    expect(memoryApi.previewMemoryDelete).not.toHaveBeenCalled()
    expect(result.current.deleteDialogOpen).toBe(false)
  })
})

describe('useMemoryDelete 确认失败', () => {
  it('没有待定预览时确认删除是空操作', async () => {
    const { result } = renderDeleteHook()

    await act(async () => {
      await result.current.executePendingDelete()
    })

    expect(memoryApi.executeMemoryDelete).not.toHaveBeenCalled()
  })

  it('execute 抛 Error 时写入预览错误并弹出删除失败', async () => {
    vi.mocked(memoryApi.executeMemoryDelete).mockRejectedValue(new Error('执行超时'))
    const { result } = renderDeleteHook()

    await act(async () => {
      await result.current.openDeletePreview({
        mode: 'source',
        selector: { sources: ['source-a'] },
      })
    })
    await waitFor(() => expect(result.current.deletePreview).not.toBeNull())

    await act(async () => {
      await result.current.executePendingDelete()
    })

    expect(result.current.deletePreviewError).toBe('执行超时')
    expect(result.current.deleteExecuting).toBe(false)
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({
        title: '删除失败',
        description: '执行超时',
        variant: 'destructive',
      }),
    )
  })

  it('execute 抛非 Error 时使用兜底文案「删除失败」', async () => {
    vi.mocked(memoryApi.executeMemoryDelete).mockRejectedValue('boom')
    const { result } = renderDeleteHook()

    await act(async () => {
      await result.current.openDeletePreview({
        mode: 'source',
        selector: { sources: ['source-a'] },
      })
    })
    await waitFor(() => expect(result.current.deletePreview).not.toBeNull())

    await act(async () => {
      await result.current.executePendingDelete()
    })

    expect(result.current.deletePreviewError).toBe('删除失败')
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({
        title: '删除失败',
        description: '未知错误',
        variant: 'destructive',
      }),
    )
  })

  it('execute success=false 弹出删除失败且不刷新数据', async () => {
    vi.mocked(memoryApi.executeMemoryDelete).mockResolvedValue({
      success: false,
      mode: 'source',
      operation_id: '',
      counts: {},
      sources: [],
      deleted_count: 0,
      deleted_entity_count: 0,
      deleted_relation_count: 0,
      deleted_paragraph_count: 0,
      deleted_source_count: 0,
      error: '目标已被删除',
    })
    const { queryClient, result } = renderDeleteHook()
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries')

    await act(async () => {
      await result.current.openDeletePreview({
        mode: 'source',
        selector: { sources: ['source-a'] },
      })
    })
    await waitFor(() => expect(result.current.deletePreview).not.toBeNull())

    await act(async () => {
      await result.current.executePendingDelete()
    })

    expect(result.current.deleteResult?.success).toBe(false)
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({
        title: '删除失败',
        description: '目标已被删除',
        variant: 'destructive',
      }),
    )
    expect(invalidateSpy).not.toHaveBeenCalled()
  })

  it('execute success=false 且无 error 时使用「未能执行删除」', async () => {
    vi.mocked(memoryApi.executeMemoryDelete).mockResolvedValue({
      success: false,
      mode: 'source',
      operation_id: '',
      counts: {},
      sources: [],
      deleted_count: 0,
      deleted_entity_count: 0,
      deleted_relation_count: 0,
      deleted_paragraph_count: 0,
      deleted_source_count: 0,
    })
    const { result } = renderDeleteHook()

    await act(async () => {
      await result.current.openDeletePreview({
        mode: 'source',
        selector: { sources: ['source-a'] },
      })
    })
    await waitFor(() => expect(result.current.deletePreview).not.toBeNull())

    await act(async () => {
      await result.current.executePendingDelete()
    })

    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({
        title: '删除失败',
        description: '未能执行删除',
        variant: 'destructive',
      }),
    )
  })

  it('恢复 success=false 弹出恢复失败，空 error 走兜底文案', async () => {
    vi.mocked(memoryApi.restoreMemoryDelete).mockResolvedValueOnce({
      success: false,
      error: '禁止恢复',
    })
    const { result } = renderDeleteHook()

    await act(async () => {
      await result.current.restoreDeleteOperation('operation-1')
    })
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({
        title: '恢复失败',
        description: '禁止恢复',
        variant: 'destructive',
      }),
    )

    toastMock.mockClear()
    vi.mocked(memoryApi.restoreMemoryDelete).mockResolvedValueOnce({ success: false })
    await act(async () => {
      await result.current.restoreDeleteOperation('operation-2')
    })
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({
        title: '恢复失败',
        description: '未能恢复删除操作',
        variant: 'destructive',
      }),
    )
    expect(result.current.deleteRestoring).toBe(false)
  })

  it('恢复抛 Error / 非 Error 都弹出恢复失败', async () => {
    vi.mocked(memoryApi.restoreMemoryDelete).mockRejectedValueOnce(new Error('恢复超时'))
    const { result } = renderDeleteHook()

    await act(async () => {
      await result.current.restoreDeleteOperation('operation-1')
    })
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({
        title: '恢复失败',
        description: '恢复超时',
        variant: 'destructive',
      }),
    )

    toastMock.mockClear()
    vi.mocked(memoryApi.restoreMemoryDelete).mockRejectedValueOnce('bad')
    await act(async () => {
      await result.current.restoreDeleteOperation('operation-1')
    })
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({
        title: '恢复失败',
        description: '未知错误',
        variant: 'destructive',
      }),
    )
  })

  it('删除成功后刷新抛非 Error 仍提示刷新失败而不是删除失败', async () => {
    const { queryClient, result } = renderDeleteHook()
    vi.spyOn(queryClient, 'invalidateQueries').mockRejectedValue('refresh-down')

    await act(async () => {
      await result.current.openDeletePreview({
        mode: 'source',
        selector: { sources: ['source-a'] },
      })
    })
    await waitFor(() => expect(result.current.deletePreview).not.toBeNull())
    await act(async () => {
      await result.current.executePendingDelete()
    })

    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith(expect.objectContaining({ title: '删除成功' }))
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({
          title: '删除已完成，但数据刷新失败',
          description: '未知错误',
        }),
      )
    })
    expect(toastMock).not.toHaveBeenCalledWith(expect.objectContaining({ title: '删除失败' }))
  })
})

describe('useMemoryDelete 空 counts', () => {
  it('深链接操作缺少 summary.counts 时计数为空对象', async () => {
    vi.mocked(memoryApi.getMemoryDeleteOperation).mockResolvedValue({
      success: true,
      operation: {
        operation_id: 'ghost',
        mode: '',
        status: '',
      },
    })
    const { result } = renderDeleteHook({ active: true, initialOperationId: 'ghost' })

    await waitFor(() => expect(result.current.selectedOperationDetailLoading).toBe(false))
    expect(result.current.selectedDeleteOperation?.operation_id).toBe('ghost')
    expect(result.current.selectedOperationCounts).toEqual({})
    expect(result.current.selectedOperationSources).toEqual([])
    expect(result.current.selectedOperationItems).toEqual([])
    expect(result.current.selectedOperationDetailError).toBe('')
  })

  it('summary.counts 为空、sources 非数组、items 非数组时走空回退', async () => {
    const emptyOp = makeOperation({
      operation_id: 'op-empty',
      summary: { sources: 'not-array' },
      items: { invalid: true } as never,
    })
    vi.mocked(memoryApi.getMemoryDeleteOperations).mockResolvedValue({
      success: true,
      items: [emptyOp],
    })
    vi.mocked(memoryApi.getMemoryDeleteOperation).mockResolvedValue({
      success: true,
      operation: emptyOp,
    })
    const { result } = renderDeleteHook({ active: true })

    await waitForOperations(result, 1)
    await waitFor(() => expect(result.current.selectedOperationDetailLoading).toBe(false))
    expect(result.current.selectedOperationCounts).toEqual({})
    expect(result.current.selectedOperationSources).toEqual([])
    expect(result.current.selectedOperationItems).toEqual([])
  })

  it('详情带回 counts / sources / items 后合并到选中操作', async () => {
    const listOp = makeOperation({
      operation_id: 'op-1',
      summary: {},
      items: undefined,
    })
    const detailItems = Array.from({ length: DELETE_OPERATION_ITEM_PAGE_SIZE + 1 }, (_, index) => ({
      item_type: index === 0 ? 'entity' : 'paragraph',
      item_hash: `hash-${index}`,
      item_key: `key-${index}`,
      payload: { source: index === 0 ? 'src-entity' : 'src-para' },
    }))
    vi.mocked(memoryApi.getMemoryDeleteOperations).mockResolvedValue({
      success: true,
      items: [listOp],
    })
    vi.mocked(memoryApi.getMemoryDeleteOperation).mockResolvedValue({
      success: true,
      operation: makeOperation({
        operation_id: 'op-1',
        summary: {
          counts: { entities: 1, relations: 0, paragraphs: 8, sources: 1 },
          sources: ['chat:alpha', '', 'chat:beta'],
        },
        items: detailItems,
      }),
    })
    const { result } = renderDeleteHook({ active: true })

    await waitForOperations(result, 1)
    await waitFor(() => expect(result.current.selectedOperationItems).toHaveLength(detailItems.length))
    expect(result.current.selectedOperationCounts).toEqual({
      entities: 1,
      relations: 0,
      paragraphs: 8,
      sources: 1,
    })
    expect(result.current.selectedOperationSources).toEqual(['chat:alpha', 'chat:beta'])
    expect(result.current.selectedOperationItemPageCount).toBe(2)

    act(() => result.current.setSelectedOperationItemPage(99))
    await waitFor(() => expect(result.current.selectedOperationItemPage).toBe(2))

    act(() => result.current.setSelectedOperationItemSearch('ENTITY'))
    await waitFor(() => expect(result.current.filteredSelectedOperationItems).toHaveLength(1))
    expect(result.current.selectedOperationItemPage).toBe(1)
    expect(result.current.pagedSelectedOperationItems[0]?.item_hash).toBe('hash-0')
  })

  it('详情 success=false / 抛错分别写入错误文案', async () => {
    vi.mocked(memoryApi.getMemoryDeleteOperations).mockResolvedValue({
      success: true,
      items: [makeOperation({ operation_id: 'op-miss' })],
    })
    vi.mocked(memoryApi.getMemoryDeleteOperation).mockResolvedValue({
      success: false,
      error: '',
    })
    const missing = renderDeleteHook({ active: true })
    await waitFor(() =>
      expect(missing.result.current.selectedOperationDetailError).toBe('未能加载删除操作详情'),
    )
    missing.unmount()

    vi.mocked(memoryApi.getMemoryDeleteOperations).mockResolvedValue({
      success: true,
      items: [makeOperation({ operation_id: 'op-fail' })],
    })
    vi.mocked(memoryApi.getMemoryDeleteOperation).mockRejectedValue(new Error('详情超时'))
    const failed = renderDeleteHook({ active: true })
    await waitFor(() => expect(failed.result.current.selectedOperationDetailError).toBe('详情超时'))
    failed.unmount()

    vi.mocked(memoryApi.getMemoryDeleteOperations).mockResolvedValue({
      success: true,
      items: [makeOperation({ operation_id: 'op-bad' })],
    })
    vi.mocked(memoryApi.getMemoryDeleteOperation).mockRejectedValue('bad')
    const fallback = renderDeleteHook({ active: true })
    await waitFor(() =>
      expect(fallback.result.current.selectedOperationDetailError).toBe('未能加载删除操作详情'),
    )
  })

  it('卸载时取消进行中的详情请求，不写入过期结果', async () => {
    const pending = deferred<{
      success: boolean
      operation?: MemoryDeleteOperationPayload | null
      error?: string
    }>()
    vi.mocked(memoryApi.getMemoryDeleteOperations).mockResolvedValue({
      success: true,
      items: [makeOperation()],
    })
    vi.mocked(memoryApi.getMemoryDeleteOperation).mockReturnValue(pending.promise as never)
    const { result, unmount } = renderDeleteHook({ active: true })

    await waitFor(() => expect(memoryApi.getMemoryDeleteOperation).toHaveBeenCalledWith('op-1'))
    expect(result.current.selectedOperationDetailLoading).toBe(true)
    unmount()
    await act(async () => {
      pending.resolve({ success: true, operation: makeOperation({ items: [] }) })
    })
  })

  it('卸载时取消失败的详情请求，不写入过期错误', async () => {
    const pending = deferred<{
      success: boolean
      operation?: MemoryDeleteOperationPayload | null
      error?: string
    }>()
    vi.mocked(memoryApi.getMemoryDeleteOperations).mockResolvedValue({
      success: true,
      items: [makeOperation()],
    })
    vi.mocked(memoryApi.getMemoryDeleteOperation).mockReturnValue(pending.promise as never)
    const { unmount } = renderDeleteHook({ active: true })

    await waitFor(() => expect(memoryApi.getMemoryDeleteOperation).toHaveBeenCalledWith('op-1'))
    unmount()
    await act(async () => {
      pending.reject(new Error('过期失败'))
    })
  })
})

describe('useMemoryDelete 模式切换与校验', () => {
  it('按模式/状态/关键字筛选操作，并在筛选变化时重置页码', async () => {
    const operations = [
      makeOperation({
        operation_id: 'op-source',
        mode: 'source',
        status: 'executed',
        reason: '清理来源',
        requested_by: 'alice',
        summary: { sources: ['chat:alpha'] },
      }),
      makeOperation({
        operation_id: 'op-entity',
        mode: 'entity',
        status: 'restored',
        reason: '回滚实体',
        requested_by: 'bob',
        summary: { sources: ['chat:unique-source'] },
      }),
      ...Array.from({ length: DELETE_OPERATION_PAGE_SIZE }, (_, index) =>
        makeOperation({
          operation_id: `op-page-${index}`,
          mode: 'paragraph',
          status: 'executed',
        }),
      ),
    ]
    vi.mocked(memoryApi.getMemoryDeleteOperations).mockResolvedValue({
      success: true,
      items: operations,
    })
    vi.mocked(memoryApi.getMemoryDeleteOperation).mockImplementation(async (operationId) => ({
      success: true,
      operation: operations.find((item) => item.operation_id === operationId) ?? makeOperation({
        operation_id: operationId,
      }),
    }))
    const { result } = renderDeleteHook({ active: true })

    await waitForOperations(result, operations.length)
    expect(memoryApi.getMemoryDeleteOperations).toHaveBeenCalledWith(DELETE_OPERATION_FETCH_LIMIT)
    expect(result.current.deleteOperationPageCount).toBe(2)

    act(() => result.current.setOperationPage(2))
    await waitFor(() => expect(result.current.operationPage).toBe(2))

    act(() => result.current.setOperationPage(99))
    await waitFor(() => expect(result.current.operationPage).toBe(2))

    act(() => result.current.setOperationModeFilter('entity'))
    await waitFor(() => {
      expect(result.current.filteredDeleteOperations).toHaveLength(1)
      expect(result.current.operationPage).toBe(1)
    })
    expect(result.current.filteredDeleteOperations[0]?.operation_id).toBe('op-entity')

    act(() => {
      result.current.setOperationModeFilter('all')
      result.current.setOperationStatusFilter('restored')
    })
    await waitFor(() => expect(result.current.filteredDeleteOperations).toHaveLength(1))
    expect(result.current.filteredDeleteOperations[0]?.status).toBe('restored')

    act(() => {
      result.current.setOperationStatusFilter('all')
      result.current.setOperationSearch('unique-source')
    })
    await waitFor(() => expect(result.current.filteredDeleteOperations).toHaveLength(1))
    expect(result.current.filteredDeleteOperations[0]?.operation_id).toBe('op-entity')
  })

  it('来源搜索、勾选去重与批量删除预览走 source 模式', async () => {
    vi.mocked(memoryApi.getMemorySources).mockResolvedValue({
      success: true,
      items: [
        { source: 'chat:alpha', paragraph_count: 1 },
        { source: 'chat:beta', paragraph_count: 2 },
      ],
      count: 2,
    })
    const { result } = renderDeleteHook({ active: true })

    await waitFor(() => expect(result.current.filteredSources).toHaveLength(2))
    act(() => result.current.setSourceSearch('BETA'))
    expect(result.current.filteredSources.map((item) => item.source)).toEqual(['chat:beta'])

    act(() => {
      result.current.toggleSourceSelection('chat:alpha', true)
      result.current.toggleSourceSelection('chat:alpha', true)
      result.current.toggleSourceSelection('chat:beta', true)
    })
    expect(result.current.selectedSources).toEqual(['chat:alpha', 'chat:beta'])

    act(() => result.current.toggleSourceSelection('chat:beta', false))
    expect(result.current.selectedSources).toEqual(['chat:alpha'])

    await act(async () => {
      await result.current.openSourceDeletePreview()
    })
    expect(memoryApi.previewMemoryDelete).toHaveBeenCalledWith({
      mode: 'source',
      selector: { sources: ['chat:alpha'] },
      requested_by: 'knowledge_base',
    })
    expect(result.current.deleteDialogTitle).toBe('批量删除来源')
    expect(result.current.deleteDialogOpen).toBe(true)
  })

  it('「已执行」筛选项按状态分组匹配 completed 操作', async () => {
    const operations = [
      makeOperation({ operation_id: 'op-completed', status: 'completed' }),
      makeOperation({ operation_id: 'op-restored', status: 'restored' }),
    ]
    vi.mocked(memoryApi.getMemoryDeleteOperations).mockResolvedValue({
      success: true,
      items: operations,
    })
    vi.mocked(memoryApi.getMemoryDeleteOperation).mockImplementation(async (operationId) => ({
      success: true,
      operation:
        operations.find((item) => item.operation_id === operationId) ??
        makeOperation({ operation_id: operationId }),
    }))
    const { result } = renderDeleteHook({ active: true })

    await waitForOperations(result, operations.length)

    act(() => result.current.setOperationStatusFilter('executed'))
    await waitFor(() => expect(result.current.filteredDeleteOperations).toHaveLength(1))
    expect(result.current.filteredDeleteOperations[0]?.operation_id).toBe('op-completed')

    act(() => result.current.setOperationStatusFilter('restored'))
    await waitFor(() => expect(result.current.filteredDeleteOperations).toHaveLength(1))
    expect(result.current.filteredDeleteOperations[0]?.operation_id).toBe('op-restored')
  })

  it('来源搜索命中聊天流名称，并回填来源名称映射与自定义删除原因', async () => {
    vi.mocked(memoryApi.getMemorySources).mockResolvedValue({
      success: true,
      items: [
        {
          source: 'chat_summary:s1',
          paragraph_count: 3,
          source_kind: 'chat_summary',
          chat_id: 's1',
          chat_name: '摸鱼群',
        },
        {
          source: 'chat_summary:s2',
          paragraph_count: 1,
          source_kind: 'chat_summary',
          chat_id: 's2',
          chat_name: '测试群',
        },
        {
          source: 'chat_stream:c1',
          paragraph_count: 2,
          source_kind: 'chat_stream',
        },
        {
          // 上传/粘贴导入写的 web_import 前缀后端解析不出类别，只能靠「其他」兜住
          source: 'web_import:notes.txt',
          paragraph_count: 1,
        },
      ],
      count: 4,
    })
    const { result } = renderDeleteHook({ active: true })

    await waitFor(() => expect(result.current.filteredSources).toHaveLength(4))
    expect(result.current.sourceNameBySource).toEqual({
      'chat_summary:s1': '摸鱼群',
      'chat_summary:s2': '测试群',
    })

    act(() => result.current.setSourceSearch('摸鱼'))
    expect(result.current.filteredSources.map((item) => item.source)).toEqual(['chat_summary:s1'])

    act(() => result.current.setSourceSearch('聊天摘要'))
    expect(result.current.filteredSources.map((item) => item.source)).toEqual([
      'chat_summary:s1',
      'chat_summary:s2',
    ])

    // 类别筛选按后端回填的 source_kind 精确匹配，「全部」恢复完整列表
    act(() => result.current.setSourceSearch(''))
    act(() => result.current.setSourceKindFilter('chat_summary'))
    expect(result.current.filteredSources.map((item) => item.source)).toEqual([
      'chat_summary:s1',
      'chat_summary:s2',
    ])

    // 没有独立标签页的类别（聊天流）与解析不出类别的来源一并归入「其他」
    act(() => result.current.setSourceKindFilter('other'))
    expect(result.current.filteredSources.map((item) => item.source)).toEqual([
      'chat_stream:c1',
      'web_import:notes.txt',
    ])

    act(() => result.current.setSourceKindFilter('all'))
    expect(result.current.filteredSources).toHaveLength(4)

    act(() => result.current.toggleSourceSelection('chat_summary:s1', true))
    await act(async () => {
      await result.current.openSourceDeletePreview()
    })
    // 预览阶段不带原因，原因在对话框确认时才合并进执行请求
    expect(memoryApi.previewMemoryDelete).toHaveBeenCalledWith({
      mode: 'source',
      selector: { sources: ['chat_summary:s1'] },
      requested_by: 'knowledge_base',
    })

    await act(async () => {
      await result.current.executePendingDelete('  清理测试批次  ')
    })
    expect(memoryApi.executeMemoryDelete).toHaveBeenCalledWith({
      mode: 'source',
      selector: { sources: ['chat_summary:s1'] },
      requested_by: 'knowledge_base',
      reason: '清理测试批次',
    })
  })

  it('确认删除不填原因时回退到来源删除默认原因', async () => {
    vi.mocked(memoryApi.getMemorySources).mockResolvedValue({
      success: true,
      items: [{ source: 'chat_summary:s1', paragraph_count: 3 }],
      count: 1,
    })
    const { result } = renderDeleteHook({ active: true })

    await waitFor(() => expect(result.current.filteredSources).toHaveLength(1))
    act(() => result.current.toggleSourceSelection('chat_summary:s1', true))
    await act(async () => {
      await result.current.openSourceDeletePreview()
    })
    await act(async () => {
      await result.current.executePendingDelete('   ')
    })
    expect(memoryApi.executeMemoryDelete).toHaveBeenCalledWith({
      mode: 'source',
      selector: { sources: ['chat_summary:s1'] },
      requested_by: 'knowledge_base',
      reason: 'knowledge_base_source_delete',
    })
  })

  it('关闭删除对话框会清空预览态；再次打开只切对话框', async () => {
    const { result } = renderDeleteHook()

    await act(async () => {
      await result.current.openDeletePreview({
        mode: 'source',
        selector: { sources: ['source-a'] },
      })
    })
    await waitFor(() => expect(result.current.deletePreview).not.toBeNull())

    act(() => result.current.closeDeleteDialog(false))
    expect(result.current.deleteDialogOpen).toBe(false)
    expect(result.current.deletePreview).toBeNull()
    expect(result.current.deleteResult).toBeNull()
    expect(result.current.deletePreviewError).toBeNull()

    act(() => result.current.closeDeleteDialog(true))
    expect(result.current.deleteDialogOpen).toBe(true)
  })

  it('查询失败时 Error / 非 Error 分别写入 deleteErrorText', async () => {
    vi.mocked(memoryApi.getMemorySources).mockRejectedValue(new Error('来源接口挂了'))
    const errorView = renderDeleteHook({ active: true })
    await waitFor(() => expect(errorView.result.current.deleteErrorText).toBe('来源接口挂了'))
    errorView.unmount()

    vi.mocked(memoryApi.getMemorySources).mockResolvedValue({ success: true, items: [], count: 0 })
    vi.mocked(memoryApi.getMemoryDeleteOperations).mockRejectedValue('bad')
    const fallbackView = renderDeleteHook({ active: true })
    await waitFor(() =>
      expect(fallbackView.result.current.deleteErrorText).toBe('加载删除数据失败'),
    )
  })

  it('refreshSources 只重拉来源列表', async () => {
    const { result } = renderDeleteHook({ active: true })
    await waitFor(() => expect(memoryApi.getMemorySources).toHaveBeenCalled())
    vi.mocked(memoryApi.getMemorySources).mockClear()
    vi.mocked(memoryApi.getMemoryDeleteOperations).mockClear()

    await act(async () => {
      await result.current.refreshSources()
    })
    expect(memoryApi.getMemorySources).toHaveBeenCalled()
    expect(memoryApi.getMemoryDeleteOperations).not.toHaveBeenCalled()
  })
})

