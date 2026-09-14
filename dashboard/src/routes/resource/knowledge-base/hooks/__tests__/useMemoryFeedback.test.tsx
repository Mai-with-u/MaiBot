import type { ReactNode } from 'react'

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type {
  MemoryFeedbackCorrectionDetailPayload,
  MemoryFeedbackCorrectionDetailTaskPayload,
  MemoryFeedbackCorrectionSummaryPayload,
} from '@/lib/memory-api'

import {
  FEEDBACK_ACTION_LOG_PAGE_SIZE,
  FEEDBACK_CORRECTION_FETCH_LIMIT,
  FEEDBACK_CORRECTION_PAGE_SIZE,
} from '../../constants'
import { useMemoryFeedback, type UseMemoryFeedbackOptions } from '../useMemoryFeedback'

const toastMock = vi.hoisted(() => vi.fn())

vi.mock('@/hooks/use-toast', () => ({ useToast: () => ({ toast: toastMock }) }))

vi.mock('@/lib/memory-api', () => ({
  getMemoryFeedbackCorrection: vi.fn(),
  getMemoryFeedbackCorrections: vi.fn(),
  rollbackMemoryFeedbackCorrection: vi.fn(),
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

function makeSummary(
  overrides: Partial<MemoryFeedbackCorrectionSummaryPayload> = {},
): MemoryFeedbackCorrectionSummaryPayload {
  return {
    task_id: 1,
    query_tool_id: 'qt-color',
    session_id: 'session-alpha',
    query_text: '最喜欢的颜色是什么',
    task_status: 'applied',
    decision: 'correct',
    decision_confidence: 0.9,
    feedback_message_count: 1,
    rollback_status: 'none',
    affected_counts: {},
    ...overrides,
  }
}

function makeDetail(
  overrides: Partial<MemoryFeedbackCorrectionDetailTaskPayload> = {},
): MemoryFeedbackCorrectionDetailTaskPayload {
  return {
    ...makeSummary(),
    query_snapshot: { query: '最喜欢的颜色是什么' },
    decision_payload: { decision: 'correct' },
    action_logs: [],
    ...overrides,
  }
}

function renderFeedback(options: Partial<UseMemoryFeedbackOptions> = {}) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
  return {
    queryClient,
    ...renderHook((props: UseMemoryFeedbackOptions) => useMemoryFeedback(props), {
      wrapper,
      initialProps: { active: true, ...options },
    }),
  }
}

async function waitForSelectedTask(
  result: { current: { selectedFeedbackCorrection: { task_id: number } | null } },
  taskId: number,
) {
  await waitFor(() => expect(result.current.selectedFeedbackCorrection?.task_id).toBe(taskId))
}

beforeEach(() => {
  toastMock.mockReset()
  vi.mocked(memoryApi.getMemoryFeedbackCorrections).mockResolvedValue({
    success: true,
    items: [makeSummary()],
    count: 1,
  })
  vi.mocked(memoryApi.getMemoryFeedbackCorrection).mockImplementation(async (taskId) => ({
    success: true,
    task: makeDetail({ task_id: taskId }),
  }))
  vi.mocked(memoryApi.rollbackMemoryFeedbackCorrection).mockResolvedValue({
    success: true,
    already_rolled_back: false,
  })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('useMemoryFeedback', () => {
  it('非激活时不拉列表、不加载任务详情', async () => {
    renderFeedback({ active: false })
    await act(async () => {
      await Promise.resolve()
    })
    expect(memoryApi.getMemoryFeedbackCorrections).not.toHaveBeenCalled()
    expect(memoryApi.getMemoryFeedbackCorrection).not.toHaveBeenCalled()
  })

  it('激活后自动选中第一项并拉取详情', async () => {
    const { result } = renderFeedback()
    await waitForSelectedTask(result, 1)
    await waitFor(() => expect(result.current.selectedFeedbackTaskLoading).toBe(false))

    expect(memoryApi.getMemoryFeedbackCorrections).toHaveBeenCalledWith({
      limit: FEEDBACK_CORRECTION_FETCH_LIMIT,
    })
    expect(memoryApi.getMemoryFeedbackCorrection).toHaveBeenCalledWith(1)
    expect(result.current.selectedFeedbackResolved?.query_snapshot).toEqual({
      query: '最喜欢的颜色是什么',
    })
    expect(result.current.feedbackErrorText).toBe('')
  })

  it('列表查询失败时 Error / 非 Error 分别写入局部错误文案', async () => {
    vi.mocked(memoryApi.getMemoryFeedbackCorrections).mockRejectedValue(new Error('纠错接口挂了'))
    const errorView = renderFeedback()
    await waitFor(() => expect(errorView.result.current.feedbackErrorText).toBe('纠错接口挂了'))
    errorView.unmount()

    vi.mocked(memoryApi.getMemoryFeedbackCorrections).mockRejectedValue('bad')
    const fallbackView = renderFeedback()
    await waitFor(() =>
      expect(fallbackView.result.current.feedbackErrorText).toBe('加载纠错历史失败'),
    )
  })

  it('按搜索、任务状态和回退状态过滤', async () => {
    vi.mocked(memoryApi.getMemoryFeedbackCorrections).mockResolvedValue({
      success: true,
      items: [
        makeSummary({ task_id: 1, query_tool_id: 'qt-color', session_id: 'session-alpha' }),
        makeSummary({
          task_id: 2,
          query_tool_id: 'qt-city',
          session_id: 'session-beta',
          query_text: '常住城市',
          task_status: 'Skipped',
          decision: 'reject',
          rollback_status: 'Rolled_Back',
        }),
        makeSummary({
          task_id: 3,
          session_id: 'session-gamma',
          query_text: '天气',
          task_status: 'error',
          rollback_status: 'error',
        }),
      ],
    })
    const { result } = renderFeedback()
    await waitFor(() => expect(result.current.feedbackCorrections).toHaveLength(3))

    act(() => result.current.setFeedbackStatusFilter('skipped'))
    expect(result.current.filteredFeedbackCorrections.map((item) => item.task_id)).toEqual([2])

    act(() => {
      result.current.setFeedbackStatusFilter('all')
      result.current.setFeedbackRollbackFilter('error')
    })
    expect(result.current.filteredFeedbackCorrections.map((item) => item.task_id)).toEqual([3])

    act(() => {
      result.current.setFeedbackRollbackFilter('all')
      result.current.setFeedbackSearch('  QT-CITY  ')
    })
    expect(result.current.filteredFeedbackCorrections.map((item) => item.task_id)).toEqual([2])

    act(() => result.current.setFeedbackSearch('session-alpha'))
    expect(result.current.filteredFeedbackCorrections.map((item) => item.task_id)).toEqual([1])
  })

  it('筛选变化重置到第 1 页，并在页数收缩时回夹当前页', async () => {
    const items = Array.from({ length: FEEDBACK_CORRECTION_PAGE_SIZE + 2 }, (_, index) =>
      makeSummary({
        task_id: index + 1,
        query_text: index === FEEDBACK_CORRECTION_PAGE_SIZE ? '唯一关键词杭州' : `条目 ${index}`,
        task_status: index === 0 ? 'applied' : 'skipped',
      }),
    )
    vi.mocked(memoryApi.getMemoryFeedbackCorrections).mockResolvedValue({
      success: true,
      items,
      count: items.length,
    })
    const { result } = renderFeedback()
    await waitFor(() => expect(result.current.feedbackCorrections).toHaveLength(items.length))
    expect(result.current.feedbackPageCount).toBe(2)
    expect(result.current.pagedFeedbackCorrections).toHaveLength(FEEDBACK_CORRECTION_PAGE_SIZE)

    act(() => result.current.setFeedbackPage(2))
    expect(result.current.pagedFeedbackCorrections).toHaveLength(2)

    act(() => result.current.setFeedbackSearch('唯一关键词杭州'))
    await waitFor(() => expect(result.current.feedbackPage).toBe(1))
    expect(result.current.filteredFeedbackCorrections).toHaveLength(1)
  })

  it('深链接 taskId 不在列表中时用占位摘要继续拉详情', async () => {
    const { result } = renderFeedback({ initialSearch: 'deep-link', initialTaskId: 99 })
    await waitFor(() => expect(result.current.feedbackSearch).toBe('deep-link'))
    await waitForSelectedTask(result, 99)
    await waitFor(() => expect(memoryApi.getMemoryFeedbackCorrection).toHaveBeenCalledWith(99))
    expect(result.current.selectedFeedbackCorrection?.query_tool_id).toBe('')
  })

  it('空列表且未指定任务时清空选中', async () => {
    vi.mocked(memoryApi.getMemoryFeedbackCorrections).mockResolvedValue({
      success: true,
      items: [],
    })
    const { result } = renderFeedback()
    await waitFor(() => expect(result.current.feedbackCorrections).toEqual([]))
    expect(result.current.selectedFeedbackCorrection).toBeNull()
    expect(result.current.selectedFeedbackResolved).toBeNull()
    expect(memoryApi.getMemoryFeedbackCorrection).not.toHaveBeenCalled()
  })

  it('详情 success=false 与抛错分别写入错误文案', async () => {
    vi.mocked(memoryApi.getMemoryFeedbackCorrection).mockResolvedValue({
      success: false,
      error: '',
    })
    const missing = renderFeedback()
    await waitFor(() =>
      expect(missing.result.current.selectedFeedbackTaskError).toBe('未能加载纠错任务详情'),
    )
    missing.unmount()

    vi.mocked(memoryApi.getMemoryFeedbackCorrection).mockResolvedValue({
      success: false,
      error: '任务不存在',
    })
    const failedPayload = renderFeedback()
    await waitFor(() =>
      expect(failedPayload.result.current.selectedFeedbackTaskError).toBe('任务不存在'),
    )
    failedPayload.unmount()

    vi.mocked(memoryApi.getMemoryFeedbackCorrection).mockRejectedValue(new Error('详情超时'))
    const failed = renderFeedback()
    await waitFor(() => expect(failed.result.current.selectedFeedbackTaskError).toBe('详情超时'))
    failed.unmount()

    vi.mocked(memoryApi.getMemoryFeedbackCorrection).mockRejectedValue('bad')
    const fallback = renderFeedback()
    await waitFor(() =>
      expect(fallback.result.current.selectedFeedbackTaskError).toBe('未能加载纠错任务详情'),
    )
  })

  it('切换任务时丢弃过期详情响应', async () => {
    const first = deferred<MemoryFeedbackCorrectionDetailPayload>()
    const second = deferred<MemoryFeedbackCorrectionDetailPayload>()
    vi.mocked(memoryApi.getMemoryFeedbackCorrections).mockResolvedValue({
      success: true,
      items: [makeSummary({ task_id: 1 }), makeSummary({ task_id: 2, query_text: '第二' })],
    })
    vi.mocked(memoryApi.getMemoryFeedbackCorrection).mockImplementation(async (taskId) => {
      if (taskId === 1) {
        return first.promise
      }
      return second.promise
    })

    const { result } = renderFeedback()
    await waitForSelectedTask(result, 1)

    act(() => result.current.setSelectedFeedbackTaskId(2))
    await waitFor(() => expect(memoryApi.getMemoryFeedbackCorrection).toHaveBeenCalledWith(2))

    await act(async () => {
      first.resolve({
        success: true,
        task: makeDetail({ task_id: 1, query_text: '过期详情' }),
      })
    })
    await act(async () => {
      second.resolve({
        success: true,
        task: makeDetail({ task_id: 2, query_text: '第二详情' }),
      })
    })

    await waitFor(() => expect(result.current.selectedFeedbackResolved?.query_text).toBe('第二详情'))
    expect(result.current.selectedFeedbackTaskError).toBe('')
  })

  it('切换任务时丢弃过期详情错误', async () => {
    const first = deferred<MemoryFeedbackCorrectionDetailPayload>()
    const second = deferred<MemoryFeedbackCorrectionDetailPayload>()
    vi.mocked(memoryApi.getMemoryFeedbackCorrections).mockResolvedValue({
      success: true,
      items: [makeSummary({ task_id: 1 }), makeSummary({ task_id: 2, query_text: '第二' })],
    })
    vi.mocked(memoryApi.getMemoryFeedbackCorrection).mockImplementation(async (taskId) => {
      if (taskId === 1) {
        return first.promise
      }
      return second.promise
    })

    const { result } = renderFeedback()
    await waitForSelectedTask(result, 1)
    act(() => result.current.setSelectedFeedbackTaskId(2))
    await waitFor(() => expect(memoryApi.getMemoryFeedbackCorrection).toHaveBeenCalledWith(2))

    await act(async () => {
      first.reject(new Error('过期失败'))
    })
    await act(async () => {
      second.resolve({
        success: true,
        task: makeDetail({ task_id: 2, query_text: '第二详情' }),
      })
    })

    await waitFor(() => expect(result.current.selectedFeedbackResolved?.query_text).toBe('第二详情'))
    expect(result.current.selectedFeedbackTaskError).toBe('')
  })

  it('动作日志按关键词过滤，页数收缩时回夹，非数组视为空', async () => {
    const logs = Array.from({ length: FEEDBACK_ACTION_LOG_PAGE_SIZE + 1 }, (_, index) => ({
      id: index + 1,
      task_id: 1,
      query_tool_id: 'qt-color',
      action_type: index === FEEDBACK_ACTION_LOG_PAGE_SIZE ? 'write_correction' : 'skip',
      target_hash: index === FEEDBACK_ACTION_LOG_PAGE_SIZE ? 'hash-unique' : `hash-${index}`,
      reason: index === 0 ? '置信度不足' : '',
      before_payload: { hash: `before-${index}` },
      after_payload: { hash: `after-${index}` },
    }))
    vi.mocked(memoryApi.getMemoryFeedbackCorrection).mockResolvedValue({
      success: true,
      task: makeDetail({ action_logs: logs }),
    })
    const { result } = renderFeedback()
    await waitFor(() =>
      expect(result.current.selectedFeedbackActionLogs).toHaveLength(logs.length),
    )
    expect(result.current.feedbackActionLogPageCount).toBe(2)

    act(() => result.current.setFeedbackActionLogPage(2))
    expect(result.current.pagedFeedbackActionLogs).toHaveLength(1)

    act(() => result.current.setFeedbackActionLogSearch('HASH-UNIQUE'))
    await waitFor(() => expect(result.current.feedbackActionLogPage).toBe(1))
    expect(result.current.pagedFeedbackActionLogs).toHaveLength(1)
  })

  it('action_logs 非数组时视为空列表', async () => {
    vi.mocked(memoryApi.getMemoryFeedbackCorrection).mockResolvedValue({
      success: true,
      task: makeDetail({
        action_logs: { not: 'array' } as unknown as MemoryFeedbackCorrectionDetailTaskPayload['action_logs'],
      }),
    })
    const { result } = renderFeedback()
    await waitForSelectedTask(result, 1)
    await waitFor(() => expect(result.current.selectedFeedbackTaskLoading).toBe(false))
    expect(result.current.selectedFeedbackActionLogs).toEqual([])
  })

  it('打开回退对话框会清空原因', async () => {
    const { result } = renderFeedback()
    await waitForSelectedTask(result, 1)
    act(() => result.current.setFeedbackRollbackReason('旧原因'))
    act(() => result.current.openFeedbackRollbackDialog())
    expect(result.current.feedbackRollbackReason).toBe('')
    expect(result.current.feedbackRollbackDialogOpen).toBe(true)
  })

  it('没有选中任务时回退是空操作', async () => {
    vi.mocked(memoryApi.getMemoryFeedbackCorrections).mockResolvedValue({
      success: true,
      items: [],
    })
    const { result } = renderFeedback()
    await waitFor(() => expect(result.current.selectedFeedbackResolved).toBeNull())
    await act(async () => {
      await result.current.executeFeedbackRollback()
    })
    expect(memoryApi.rollbackMemoryFeedbackCorrection).not.toHaveBeenCalled()
  })

  it('回退成功后刷新列表与详情，并通知运行时/来源', async () => {
    const onRuntimeChanged = vi.fn()
    const onSourcesChanged = vi.fn()
    const { result } = renderFeedback({ onRuntimeChanged, onSourcesChanged })
    await waitForSelectedTask(result, 1)
    await waitFor(() => expect(result.current.selectedFeedbackTaskLoading).toBe(false))

    act(() => result.current.setFeedbackRollbackReason('  人工确认  '))
    vi.mocked(memoryApi.getMemoryFeedbackCorrections).mockResolvedValue({
      success: true,
      items: [makeSummary({ rollback_status: 'rolled_back' })],
    })
    vi.mocked(memoryApi.getMemoryFeedbackCorrection).mockResolvedValue({
      success: true,
      task: makeDetail({ rollback_status: 'rolled_back' }),
    })

    await act(async () => {
      await result.current.executeFeedbackRollback()
    })

    expect(memoryApi.rollbackMemoryFeedbackCorrection).toHaveBeenCalledWith(1, {
      requested_by: 'knowledge_base',
      reason: '人工确认',
    })
    expect(result.current.feedbackRollbackDialogOpen).toBe(false)
    expect(result.current.feedbackRollingBack).toBe(false)
    expect(result.current.selectedFeedbackResolved?.rollback_status).toBe('rolled_back')
    expect(onRuntimeChanged).toHaveBeenCalledOnce()
    expect(onSourcesChanged).toHaveBeenCalledOnce()
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({
        title: '纠错回退成功',
        description: '任务 1 的回退结果已写入日志',
      }),
    )
  })

  it('已经回退过的任务走已回退 toast，详情缺失时清空详情', async () => {
    const { result } = renderFeedback()
    await waitForSelectedTask(result, 1)
    await waitFor(() => expect(result.current.selectedFeedbackTaskLoading).toBe(false))

    vi.mocked(memoryApi.rollbackMemoryFeedbackCorrection).mockResolvedValue({
      success: true,
      already_rolled_back: true,
    })
    vi.mocked(memoryApi.getMemoryFeedbackCorrection).mockResolvedValue({
      success: true,
      task: null,
    })

    await act(async () => {
      await result.current.executeFeedbackRollback()
    })

    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({ title: '该纠错已回退' }),
    )
    expect(result.current.feedbackRollbackDialogOpen).toBe(false)
    expect(result.current.selectedFeedbackResolved?.query_snapshot).toBeUndefined()
  })

  it('回退 success=false 有/无 error，以及抛错都弹出失败 toast', async () => {
    const { result } = renderFeedback()
    await waitForSelectedTask(result, 1)
    await waitFor(() => expect(result.current.selectedFeedbackTaskLoading).toBe(false))

    vi.mocked(memoryApi.rollbackMemoryFeedbackCorrection).mockResolvedValue({
      success: false,
      error: '不允许回退',
    })
    await act(async () => {
      await result.current.executeFeedbackRollback()
    })
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({
        title: '纠错回退失败',
        description: '不允许回退',
        variant: 'destructive',
      }),
    )

    toastMock.mockClear()
    vi.mocked(memoryApi.rollbackMemoryFeedbackCorrection).mockResolvedValue({
      success: false,
    })
    await act(async () => {
      await result.current.executeFeedbackRollback()
    })
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({
        title: '纠错回退失败',
        description: '回退失败',
        variant: 'destructive',
      }),
    )

    toastMock.mockClear()
    vi.mocked(memoryApi.rollbackMemoryFeedbackCorrection).mockRejectedValue(new Error('网络中断'))
    await act(async () => {
      await result.current.executeFeedbackRollback()
    })
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({
        title: '纠错回退失败',
        description: '网络中断',
        variant: 'destructive',
      }),
    )

    toastMock.mockClear()
    vi.mocked(memoryApi.rollbackMemoryFeedbackCorrection).mockRejectedValue('x')
    await act(async () => {
      await result.current.executeFeedbackRollback()
    })
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({
        title: '纠错回退失败',
        description: '未知错误',
        variant: 'destructive',
      }),
    )
    expect(result.current.feedbackRollingBack).toBe(false)
  })
})
