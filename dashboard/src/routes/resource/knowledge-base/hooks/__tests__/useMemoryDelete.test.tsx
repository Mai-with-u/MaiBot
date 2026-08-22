import type { ReactNode } from 'react'

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useMemoryDelete } from '../useMemoryDelete'
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

function renderDeleteHook() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
  return {
    queryClient,
    ...renderHook(() => useMemoryDelete({ active: false }), { wrapper }),
  }
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
