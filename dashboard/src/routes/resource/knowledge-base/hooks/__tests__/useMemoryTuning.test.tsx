import type { ReactNode } from 'react'

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useMemoryTuning, type UseMemoryTuningOptions } from '../useMemoryTuning'

const toastMock = vi.hoisted(() => vi.fn())

vi.mock('@/hooks/use-toast', () => ({ useToast: () => ({ toast: toastMock }) }))

vi.mock('@/lib/memory-api', () => ({
  applyBestMemoryTuningProfile: vi.fn(),
  createMemoryTuningTask: vi.fn(),
  getMemoryTuningProfile: vi.fn(),
  getMemoryTuningTasks: vi.fn(),
}))

import * as memoryApi from '@/lib/memory-api'

function renderTuning(options: Partial<UseMemoryTuningOptions> = {}) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
  return {
    queryClient,
    ...renderHook((props: UseMemoryTuningOptions) => useMemoryTuning(props), {
      wrapper,
      initialProps: { active: true, ...options },
    }),
  }
}

beforeEach(() => {
  toastMock.mockReset()
  vi.mocked(memoryApi.getMemoryTuningProfile).mockResolvedValue({
    success: true,
    runtime_profile: { recall: 0.8 },
    persistable_profile: { persist: true },
    toml: 'recall = 0.8',
  })
  vi.mocked(memoryApi.getMemoryTuningTasks).mockResolvedValue({
    success: true,
    items: [{ task_id: 'task-1', status: 'done' }],
  })
  vi.mocked(memoryApi.createMemoryTuningTask).mockResolvedValue({
    success: true,
    task: { task_id: 'task-new' },
  })
  vi.mocked(memoryApi.applyBestMemoryTuningProfile).mockResolvedValue({
    success: true,
    persisted: false,
  })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('useMemoryTuning', () => {
  it('非激活时不拉 profile / tasks', async () => {
    renderTuning({ active: false })
    await act(async () => {
      await Promise.resolve()
    })
    expect(memoryApi.getMemoryTuningProfile).not.toHaveBeenCalled()
    expect(memoryApi.getMemoryTuningTasks).not.toHaveBeenCalled()
  })

  it('激活后读取 profile / tasks，缺省字段回退到 profile 或空对象', async () => {
    const { result, unmount } = renderTuning()
    await waitFor(() => expect(result.current.tuningTasks).toHaveLength(1))
    expect(memoryApi.getMemoryTuningTasks).toHaveBeenCalledWith(20)
    expect(result.current.tuningProfile).toEqual({
      runtime: { recall: 0.8 },
      persistable: { persist: true },
    })
    expect(result.current.tuningProfileToml).toBe('recall = 0.8')
    unmount()

    vi.mocked(memoryApi.getMemoryTuningProfile).mockResolvedValue({
      success: true,
      profile: { shared: 1 },
    })
    const fallback = renderTuning()
    await waitFor(() => expect(fallback.result.current.tuningProfile.runtime).toEqual({ shared: 1 }))
    expect(fallback.result.current.tuningProfile.persistable).toEqual({ shared: 1 })
    expect(fallback.result.current.tuningProfileToml).toBe('')
    fallback.unmount()

    vi.mocked(memoryApi.getMemoryTuningProfile).mockResolvedValue({ success: true })
    vi.mocked(memoryApi.getMemoryTuningTasks).mockResolvedValue({ success: true } as never)
    const empty = renderTuning()
    await waitFor(() => expect(empty.result.current.tuningErrorText).toBe(''))
    expect(empty.result.current.tuningProfile).toEqual({ runtime: {}, persistable: {} })
    expect(empty.result.current.tuningTasks).toEqual([])
  })

  it('读取失败时 Error / 非 Error 分别写入局部错误文案', async () => {
    vi.mocked(memoryApi.getMemoryTuningProfile).mockRejectedValue(new Error('画像接口挂了'))
    const profileError = renderTuning()
    await waitFor(() => expect(profileError.result.current.tuningErrorText).toBe('画像接口挂了'))
    profileError.unmount()

    vi.mocked(memoryApi.getMemoryTuningProfile).mockResolvedValue({ success: true, profile: {} })
    vi.mocked(memoryApi.getMemoryTuningTasks).mockRejectedValue(new Error('任务列表失败'))
    const tasksError = renderTuning()
    await waitFor(() => expect(tasksError.result.current.tuningErrorText).toBe('任务列表失败'))
    tasksError.unmount()

    vi.mocked(memoryApi.getMemoryTuningProfile).mockRejectedValue('bad')
    vi.mocked(memoryApi.getMemoryTuningTasks).mockResolvedValue({ success: true, items: [] })
    const fallback = renderTuning()
    await waitFor(() =>
      expect(fallback.result.current.tuningErrorText).toBe('加载调优数据失败'),
    )
  })

  it('使用当前参数创建调优任务并刷新列表', async () => {
    const { result } = renderTuning()
    await waitFor(() => expect(result.current.tuningTasks).toHaveLength(1))

    act(() => {
      result.current.setTuningObjective('recall_priority')
      result.current.setTuningIntensity('aggressive')
      result.current.setTuningSampleSize('48')
      result.current.setTuningTopKEval('12')
    })
    vi.mocked(memoryApi.getMemoryTuningTasks).mockResolvedValue({
      success: true,
      items: [{ task_id: 'task-1' }, { task_id: 'task-new' }],
    })

    await act(async () => {
      await result.current.submitTuningTask()
    })

    expect(memoryApi.createMemoryTuningTask).toHaveBeenCalledWith({
      objective: 'recall_priority',
      intensity: 'aggressive',
      sample_size: 48,
      top_k_eval: 12,
    })
    expect(result.current.creatingTuning).toBe(false)
    await waitFor(() => expect(result.current.tuningTasks).toHaveLength(2))
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({
        title: '调优任务已创建',
        description: '新的检索调优任务已经进入队列',
      }),
    )
  })

  it('创建失败时优先 error、其次 message、最后兜底句', async () => {
    const { result } = renderTuning()
    await waitFor(() => expect(result.current.tuningTasks).toHaveLength(1))

    vi.mocked(memoryApi.createMemoryTuningTask).mockResolvedValue({
      success: false,
      error: '创建被拒绝',
    })
    await act(async () => {
      await result.current.submitTuningTask()
    })
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({
        title: '创建调优任务失败',
        description: '创建被拒绝',
        variant: 'destructive',
      }),
    )

    toastMock.mockClear()
    vi.mocked(memoryApi.createMemoryTuningTask).mockResolvedValue({
      success: false,
      message: '队列已满',
    })
    await act(async () => {
      await result.current.submitTuningTask()
    })
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({ description: '队列已满' }),
    )

    toastMock.mockClear()
    vi.mocked(memoryApi.createMemoryTuningTask).mockResolvedValue({ success: false })
    await act(async () => {
      await result.current.submitTuningTask()
    })
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({ description: '服务端未能创建调优任务' }),
    )
  })

  it('创建抛错时 Error / 非 Error 都弹出失败 toast', async () => {
    const { result } = renderTuning()
    await waitFor(() => expect(result.current.tuningTasks).toHaveLength(1))

    vi.mocked(memoryApi.createMemoryTuningTask).mockRejectedValue(new Error('创建超时'))
    await act(async () => {
      await result.current.submitTuningTask()
    })
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({
        title: '创建调优任务失败',
        description: '创建超时',
        variant: 'destructive',
      }),
    )

    toastMock.mockClear()
    vi.mocked(memoryApi.createMemoryTuningTask).mockRejectedValue('x')
    await act(async () => {
      await result.current.submitTuningTask()
    })
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({
        title: '创建调优任务失败',
        description: '未知错误',
        variant: 'destructive',
      }),
    )
    expect(result.current.creatingTuning).toBe(false)
  })

  it('应用最佳参数时按 persist 切换文案，并通知运行时刷新', async () => {
    const onRuntimeChanged = vi.fn()
    const { result } = renderTuning({ onRuntimeChanged })
    await waitFor(() => expect(result.current.tuningTasks).toHaveLength(1))

    await act(async () => {
      await result.current.applyBestTask('task-1')
    })
    expect(memoryApi.applyBestMemoryTuningProfile).toHaveBeenCalledWith('task-1', {
      persist: false,
      validate: true,
    })
    expect(onRuntimeChanged).toHaveBeenCalledOnce()
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({
        title: '最佳参数已应用',
        description: '任务 task-1 的最佳轮次已经写入运行时',
      }),
    )

    act(() => result.current.setPersistBestProfile(true))
    vi.mocked(memoryApi.applyBestMemoryTuningProfile).mockResolvedValue({
      success: true,
      persisted: true,
    })
    toastMock.mockClear()
    await act(async () => {
      await result.current.applyBestTask('task-9')
    })
    expect(memoryApi.applyBestMemoryTuningProfile).toHaveBeenCalledWith('task-9', {
      persist: true,
      validate: true,
    })
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({
        description: '任务 task-9 的最佳轮次已经写入运行时和配置文件',
      }),
    )
  })

  it('应用失败时优先 error、其次 message、最后兜底句，抛错走未知错误', async () => {
    const { result } = renderTuning()
    await waitFor(() => expect(result.current.tuningTasks).toHaveLength(1))

    vi.mocked(memoryApi.applyBestMemoryTuningProfile).mockResolvedValue({
      success: false,
      error: '校验未通过',
    })
    await act(async () => {
      await result.current.applyBestTask('task-1')
    })
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({
        title: '应用最佳参数失败',
        description: '校验未通过',
        variant: 'destructive',
      }),
    )

    toastMock.mockClear()
    vi.mocked(memoryApi.applyBestMemoryTuningProfile).mockResolvedValue({
      success: false,
      message: '没有最佳轮次',
    })
    await act(async () => {
      await result.current.applyBestTask('task-1')
    })
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({ description: '没有最佳轮次' }),
    )

    toastMock.mockClear()
    vi.mocked(memoryApi.applyBestMemoryTuningProfile).mockResolvedValue({ success: false })
    await act(async () => {
      await result.current.applyBestTask('task-1')
    })
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({ description: '服务端未能应用最佳参数' }),
    )

    toastMock.mockClear()
    vi.mocked(memoryApi.applyBestMemoryTuningProfile).mockRejectedValue(new Error('应用超时'))
    await act(async () => {
      await result.current.applyBestTask('task-1')
    })
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({ description: '应用超时' }),
    )

    toastMock.mockClear()
    vi.mocked(memoryApi.applyBestMemoryTuningProfile).mockRejectedValue('x')
    await act(async () => {
      await result.current.applyBestTask('task-1')
    })
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({
        title: '应用最佳参数失败',
        description: '未知错误',
        variant: 'destructive',
      }),
    )
  })
})
