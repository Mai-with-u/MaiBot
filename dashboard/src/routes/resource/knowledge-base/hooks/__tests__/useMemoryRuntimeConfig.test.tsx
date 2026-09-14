import type { ReactNode } from 'react'

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { MemoryRuntimeConfigPayload } from '@/lib/memory-api'

import { useMemoryRuntimeConfig } from '../useMemoryRuntimeConfig'

const toastMock = vi.hoisted(() => vi.fn())

vi.mock('@/hooks/use-toast', () => ({ useToast: () => ({ toast: toastMock }) }))

vi.mock('@/lib/memory-api', () => ({
  getMemoryRuntimeConfig: vi.fn(),
  rebuildMemoryRuntimeVectors: vi.fn(),
  refreshMemoryRuntimeSelfCheck: vi.fn(),
}))

import * as memoryApi from '@/lib/memory-api'

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

function makeRuntime(
  overrides: Partial<MemoryRuntimeConfigPayload> = {},
): MemoryRuntimeConfigPayload {
  return {
    success: true,
    config: {},
    data_dir: 'data',
    embedding_dimension: 8,
    auto_save: true,
    relation_vectors_enabled: false,
    runtime_ready: true,
    embedding_degraded: false,
    embedding_degraded_reason: '',
    paragraph_vector_backfill_pending: 0,
    paragraph_vector_backfill_running: 0,
    paragraph_vector_backfill_failed: 0,
    paragraph_vector_backfill_done: 0,
    ...overrides,
  }
}

function renderRuntimeHook() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
  return {
    queryClient,
    ...renderHook(() => useMemoryRuntimeConfig(), { wrapper }),
  }
}

beforeEach(() => {
  toastMock.mockReset()
  vi.mocked(memoryApi.getMemoryRuntimeConfig).mockResolvedValue(makeRuntime())
  vi.mocked(memoryApi.refreshMemoryRuntimeSelfCheck).mockResolvedValue({
    success: true,
    report: { ok: true },
  })
  vi.mocked(memoryApi.rebuildMemoryRuntimeVectors).mockResolvedValue({
    success: true,
    dry_run: true,
    counts: { paragraphs: 2 },
    done: 2,
    failed: 0,
  })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('useMemoryRuntimeConfig', () => {
  it('默认拉取运行时配置，loading 结束后写入结果', async () => {
    const { result } = renderRuntimeHook()
    expect(result.current.runtimeLoading).toBe(true)
    await waitFor(() => expect(result.current.runtimeLoading).toBe(false))
    expect(result.current.runtimeConfig?.data_dir).toBe('data')
    expect(result.current.runtimeErrorText).toBe('')
    expect(memoryApi.getMemoryRuntimeConfig).toHaveBeenCalled()
  })

  it('读取失败时 Error / 非 Error 分别写入局部错误文案', async () => {
    vi.mocked(memoryApi.getMemoryRuntimeConfig).mockRejectedValue(new Error('配置接口挂了'))
    const errorView = renderRuntimeHook()
    await waitFor(() => expect(errorView.result.current.runtimeErrorText).toBe('配置接口挂了'))
    expect(errorView.result.current.runtimeConfig).toBeNull()
    errorView.unmount()

    vi.mocked(memoryApi.getMemoryRuntimeConfig).mockRejectedValue('bad')
    const fallbackView = renderRuntimeHook()
    await waitFor(() =>
      expect(fallbackView.result.current.runtimeErrorText).toBe('加载长期记忆运行状态失败'),
    )
  })

  it('refreshRuntimeConfig 会再次请求运行时配置', async () => {
    const { result } = renderRuntimeHook()
    await waitFor(() => expect(result.current.runtimeConfig).not.toBeNull())
    vi.mocked(memoryApi.getMemoryRuntimeConfig).mockResolvedValue(
      makeRuntime({ data_dir: 'data-refreshed' }),
    )
    await act(async () => {
      await result.current.refreshRuntimeConfig()
    })
    await waitFor(() => expect(result.current.runtimeConfig?.data_dir).toBe('data-refreshed'))
  })

  it('自检通过后写入最新运行时并弹出成功 toast', async () => {
    const { result } = renderRuntimeHook()
    await waitFor(() => expect(result.current.runtimeConfig).not.toBeNull())
    vi.mocked(memoryApi.getMemoryRuntimeConfig).mockResolvedValue(
      makeRuntime({ data_dir: 'after-check' }),
    )

    await act(async () => {
      await result.current.refreshSelfCheck()
    })

    expect(memoryApi.refreshMemoryRuntimeSelfCheck).toHaveBeenCalledOnce()
    expect(result.current.runtimeConfig?.data_dir).toBe('after-check')
    expect(result.current.refreshingCheck).toBe(false)
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({
        title: '自检通过',
        description: '运行时状态正常',
        variant: 'default',
      }),
    )
  })

  it('自检未通过走破坏性 toast，过程中 refreshingCheck 为 true', async () => {
    const pending = deferred<{ success: boolean }>()
    vi.mocked(memoryApi.refreshMemoryRuntimeSelfCheck).mockReturnValue(pending.promise)
    const { result } = renderRuntimeHook()
    await waitFor(() => expect(result.current.runtimeConfig).not.toBeNull())

    let finished: Promise<void> | undefined
    act(() => {
      finished = result.current.refreshSelfCheck()
    })
    await waitFor(() => expect(result.current.refreshingCheck).toBe(true))

    await act(async () => {
      pending.resolve({ success: false })
      await finished
    })

    expect(result.current.refreshingCheck).toBe(false)
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({
        title: '自检未通过',
        description: '请检查 embedding 配置和外部服务连通性',
        variant: 'destructive',
      }),
    )
  })

  it('自检抛错时 Error / 非 Error 都弹出运行时自检失败', async () => {
    const { result } = renderRuntimeHook()
    await waitFor(() => expect(result.current.runtimeConfig).not.toBeNull())

    vi.mocked(memoryApi.refreshMemoryRuntimeSelfCheck).mockRejectedValue(new Error('自检超时'))
    await act(async () => {
      await result.current.refreshSelfCheck()
    })
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({
        title: '运行时自检失败',
        description: '自检超时',
        variant: 'destructive',
      }),
    )

    toastMock.mockClear()
    vi.mocked(memoryApi.refreshMemoryRuntimeSelfCheck).mockRejectedValue('boom')
    await act(async () => {
      await result.current.refreshSelfCheck()
    })
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({
        title: '运行时自检失败',
        description: '未知错误',
        variant: 'destructive',
      }),
    )
    expect(result.current.refreshingCheck).toBe(false)
  })

  it('打开向量重建对话框会拉 dry-run 预览并暂存待定操作', async () => {
    const { result } = renderRuntimeHook()
    await waitFor(() => expect(result.current.runtimeConfig).not.toBeNull())

    await act(async () => {
      await result.current.openVectorRebuildDialog()
    })

    expect(memoryApi.rebuildMemoryRuntimeVectors).toHaveBeenCalledWith({ dry_run: true })
    expect(result.current.vectorRebuildDialogOpen).toBe(true)
    expect(result.current.vectorRebuildPreview).toEqual({ paragraphs: 2 })
  })

  it('预览没有 counts 时 preview 为 null', async () => {
    vi.mocked(memoryApi.rebuildMemoryRuntimeVectors).mockResolvedValue({
      success: true,
      dry_run: true,
    })
    const { result } = renderRuntimeHook()
    await waitFor(() => expect(result.current.runtimeConfig).not.toBeNull())

    await act(async () => {
      await result.current.openVectorRebuildDialog()
    })

    expect(result.current.vectorRebuildDialogOpen).toBe(true)
    expect(result.current.vectorRebuildPreview).toBeNull()
  })

  it('读取重建预览失败时 Error / 非 Error 都弹 toast，对话框仍打开', async () => {
    const { result } = renderRuntimeHook()
    await waitFor(() => expect(result.current.runtimeConfig).not.toBeNull())

    vi.mocked(memoryApi.rebuildMemoryRuntimeVectors).mockRejectedValue(new Error('预览超时'))
    await act(async () => {
      await result.current.openVectorRebuildDialog()
    })
    expect(result.current.vectorRebuildDialogOpen).toBe(true)
    expect(result.current.vectorRebuildPreview).toBeNull()
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({
        title: '读取向量重建预览失败',
        description: '预览超时',
        variant: 'destructive',
      }),
    )

    toastMock.mockClear()
    vi.mocked(memoryApi.rebuildMemoryRuntimeVectors).mockRejectedValue('x')
    await act(async () => {
      await result.current.openVectorRebuildDialog()
    })
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({
        title: '读取向量重建预览失败',
        description: '未知错误',
        variant: 'destructive',
      }),
    )
  })

  it('确认重建成功后关闭对话框并刷新运行时', async () => {
    const { result } = renderRuntimeHook()
    await waitFor(() => expect(result.current.runtimeConfig).not.toBeNull())
    await act(async () => {
      await result.current.openVectorRebuildDialog()
    })

    vi.mocked(memoryApi.rebuildMemoryRuntimeVectors).mockResolvedValue({
      success: true,
      dry_run: false,
      done: 4,
      failed: 1,
    })
    vi.mocked(memoryApi.getMemoryRuntimeConfig).mockResolvedValue(
      makeRuntime({ data_dir: 'after-rebuild' }),
    )

    await act(async () => {
      await result.current.confirmVectorRebuild()
    })

    expect(memoryApi.rebuildMemoryRuntimeVectors).toHaveBeenCalledWith({ dry_run: false })
    expect(result.current.vectorRebuildDialogOpen).toBe(false)
    expect(result.current.vectorRebuilding).toBe(false)
    expect(result.current.runtimeConfig?.data_dir).toBe('after-rebuild')
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({
        title: '向量重建完成',
        description: '已处理 4 条，失败 1 条',
        variant: 'default',
      }),
    )
  })

  it('重建未完全成功且缺省计数时走破坏性 toast 与 0 条兜底', async () => {
    const { result } = renderRuntimeHook()
    await waitFor(() => expect(result.current.runtimeConfig).not.toBeNull())
    await act(async () => {
      await result.current.openVectorRebuildDialog()
    })

    vi.mocked(memoryApi.rebuildMemoryRuntimeVectors).mockResolvedValue({
      success: false,
      dry_run: false,
    })

    await act(async () => {
      await result.current.confirmVectorRebuild()
    })

    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({
        title: '向量重建未完全成功',
        description: '已处理 0 条，失败 0 条',
        variant: 'destructive',
      }),
    )
  })

  it('向量重建抛错时 Error / 非 Error 都弹出失败 toast，对话框不自动关闭', async () => {
    const pending = deferred<{ success: boolean; dry_run?: boolean; done?: number; failed?: number }>()
    const { result } = renderRuntimeHook()
    await waitFor(() => expect(result.current.runtimeConfig).not.toBeNull())
    await act(async () => {
      await result.current.openVectorRebuildDialog()
    })

    vi.mocked(memoryApi.rebuildMemoryRuntimeVectors).mockReturnValue(pending.promise)
    let finished: Promise<void> | undefined
    act(() => {
      finished = result.current.confirmVectorRebuild()
    })
    await waitFor(() => expect(result.current.vectorRebuilding).toBe(true))

    await act(async () => {
      pending.reject(new Error('重建中断'))
      await finished
    })
    expect(result.current.vectorRebuilding).toBe(false)
    expect(result.current.vectorRebuildDialogOpen).toBe(true)
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({
        title: '向量重建失败',
        description: '重建中断',
        variant: 'destructive',
      }),
    )

    vi.mocked(memoryApi.rebuildMemoryRuntimeVectors).mockResolvedValue({
      success: true,
      dry_run: true,
      counts: { paragraphs: 2 },
    })
    await act(async () => {
      await result.current.openVectorRebuildDialog()
    })
    toastMock.mockClear()
    vi.mocked(memoryApi.rebuildMemoryRuntimeVectors).mockRejectedValue('boom')
    await act(async () => {
      await result.current.confirmVectorRebuild()
    })
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({
        title: '向量重建失败',
        description: '未知错误',
        variant: 'destructive',
      }),
    )
  })

  it('关闭对话框会放弃待定操作并清空预览，未打开时确认是空操作', async () => {
    const { result } = renderRuntimeHook()
    await waitFor(() => expect(result.current.runtimeConfig).not.toBeNull())

    await act(async () => {
      await result.current.confirmVectorRebuild()
    })
    expect(memoryApi.rebuildMemoryRuntimeVectors).not.toHaveBeenCalledWith({ dry_run: false })

    await act(async () => {
      await result.current.openVectorRebuildDialog()
    })
    expect(result.current.vectorRebuildPreview).toEqual({ paragraphs: 2 })

    act(() => {
      result.current.setVectorRebuildDialogOpen(true)
    })
    expect(result.current.vectorRebuildDialogOpen).toBe(true)

    act(() => {
      result.current.setVectorRebuildDialogOpen(false)
    })
    expect(result.current.vectorRebuildDialogOpen).toBe(false)
    expect(result.current.vectorRebuildPreview).toBeNull()

    vi.mocked(memoryApi.rebuildMemoryRuntimeVectors).mockClear()
    await act(async () => {
      await result.current.confirmVectorRebuild()
    })
    expect(memoryApi.rebuildMemoryRuntimeVectors).not.toHaveBeenCalled()
  })
})
