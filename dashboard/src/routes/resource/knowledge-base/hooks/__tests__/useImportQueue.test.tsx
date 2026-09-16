import type { ReactNode } from 'react'

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { IMPORT_CHUNK_PAGE_SIZE } from '../../constants'
import { useImportQueue } from '../useImportQueue'
import type { UseImportQueueOptions } from '../useImportQueue'

import type {
  MemoryImportChunkPayload,
  MemoryImportFilePayload,
  MemoryImportTaskPayload,
} from '@/lib/memory-api'
import type { MemoryProgressEvent } from '@/lib/memory-progress-client'

const toastMock = vi.hoisted(() => vi.fn())

const wsState = vi.hoisted(() => {
  const listeners = new Set<(connected: boolean) => void>()
  let connected = false
  return {
    listeners,
    get connected() {
      return connected
    },
    onConnectionChange(listener: (connected: boolean) => void) {
      listeners.add(listener)
      listener(connected)
      return () => {
        listeners.delete(listener)
      }
    },
    setConnected(next: boolean) {
      connected = next
      listeners.forEach((listener) => listener(next))
    },
    reset() {
      listeners.clear()
      connected = false
    },
  }
})

const progressState = vi.hoisted(() => {
  const handlers: Array<(event: MemoryProgressEvent) => void> = []
  let subscribeImpl:
    | ((
        handler: (event: MemoryProgressEvent) => void,
        topics?: string[],
      ) => Promise<() => Promise<void>>)
    | null = null

  return {
    handlers,
    lastTopics: [] as string[],
    setSubscribe(
      impl: (
        handler: (event: MemoryProgressEvent) => void,
        topics?: string[],
      ) => Promise<() => Promise<void>>,
    ) {
      subscribeImpl = impl
    },
    reset() {
      handlers.length = 0
      subscribeImpl = null
      progressState.lastTopics = []
    },
    subscribe(
      handler: (event: MemoryProgressEvent) => void,
      topics?: string[],
    ): Promise<() => Promise<void>> {
      progressState.lastTopics = topics ?? []
      if (subscribeImpl) {
        return subscribeImpl(handler, topics)
      }
      handlers.push(handler)
      return Promise.resolve(async () => {
        const index = handlers.indexOf(handler)
        if (index >= 0) {
          handlers.splice(index, 1)
        }
      })
    },
  }
})

vi.mock('@/hooks/use-toast', () => ({
  useToast: () => ({ toast: toastMock }),
}))

vi.mock('@/lib/unified-ws', () => ({
  unifiedWsClient: {
    onConnectionChange: (listener: (connected: boolean) => void) =>
      wsState.onConnectionChange(listener),
  },
}))

vi.mock('@/lib/memory-progress-client', () => ({
  memoryProgressClient: {
    subscribe: (
      handler: (event: MemoryProgressEvent) => void,
      topics?: string[],
    ) => progressState.subscribe(handler, topics),
  },
}))

vi.mock('@/lib/memory-api', () => ({
  getMemoryImportSettings: vi.fn(),
  getMemoryImportTasks: vi.fn(),
  getMemoryImportTask: vi.fn(),
  getMemoryImportTaskChunks: vi.fn(),
  cancelMemoryImportTask: vi.fn(),
  retryMemoryImportTask: vi.fn(),
}))

import * as memoryApi from '@/lib/memory-api'

function makeFile(
  overrides: Partial<MemoryImportFilePayload> = {},
): MemoryImportFilePayload {
  return {
    file_id: 'file-1',
    name: 'alpha.txt',
    source_kind: 'paste',
    input_mode: 'text',
    status: 'running',
    current_step: 'running',
    detected_strategy_type: 'auto',
    total_chunks: 10,
    done_chunks: 1,
    failed_chunks: 0,
    cancelled_chunks: 0,
    progress: 10,
    error: '',
    created_at: 1,
    updated_at: 2,
    ...overrides,
  }
}

function makeChunk(
  overrides: Partial<MemoryImportChunkPayload> = {},
): MemoryImportChunkPayload {
  return {
    chunk_id: 'chunk-1',
    index: 0,
    chunk_type: 'text',
    status: 'done',
    step: 'done',
    failed_at: '',
    retryable: false,
    error: '',
    progress: 100,
    content_preview: 'preview',
    updated_at: 1,
    ...overrides,
  }
}

function makeTask(
  overrides: Partial<MemoryImportTaskPayload> = {},
): MemoryImportTaskPayload {
  return {
    task_id: 'task-1',
    source: 'webui',
    status: 'running',
    current_step: 'running',
    total_chunks: 10,
    done_chunks: 1,
    failed_chunks: 0,
    cancelled_chunks: 0,
    progress: 10,
    error: '',
    file_count: 1,
    created_at: 1,
    updated_at: 2,
    files: [makeFile()],
    ...overrides,
  }
}

function renderQueue(options: Partial<UseImportQueueOptions> = {}) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
  return {
    queryClient,
    ...renderHook((props: UseImportQueueOptions) => useImportQueue(props), {
      wrapper,
      initialProps: {
        active: options.active ?? true,
        buildRetryOverrides: options.buildRetryOverrides,
      },
    }),
  }
}

function getTasksRefetchInterval(queryClient: QueryClient) {
  const query = queryClient.getQueryCache().find({ queryKey: ['memory-import', 'tasks'] })
  const observers = (
    query as
      | { observers?: Array<{ options: { refetchInterval?: unknown } }> }
      | undefined
  )?.observers
  return observers?.[0]?.options.refetchInterval
}

async function waitForSelectedTask(
  result: { current: { selectedImportTaskId: string } },
  taskId: string,
) {
  await waitFor(() => expect(result.current.selectedImportTaskId).toBe(taskId))
}

beforeEach(() => {
  toastMock.mockReset()
  progressState.reset()
  wsState.reset()

  vi.mocked(memoryApi.getMemoryImportSettings).mockResolvedValue({
    success: true,
    settings: {},
  })
  vi.mocked(memoryApi.getMemoryImportTasks).mockResolvedValue({
    success: true,
    items: [makeTask()],
  })
  vi.mocked(memoryApi.getMemoryImportTask).mockImplementation(async (taskId) => ({
    success: true,
    task: makeTask({ task_id: taskId }),
  }))
  vi.mocked(memoryApi.getMemoryImportTaskChunks).mockResolvedValue({
    success: true,
    items: [makeChunk()],
    total: 1,
    offset: 0,
    limit: IMPORT_CHUNK_PAGE_SIZE,
  })
  vi.mocked(memoryApi.cancelMemoryImportTask).mockResolvedValue({ success: true })
  vi.mocked(memoryApi.retryMemoryImportTask).mockResolvedValue({ success: true })
})

afterEach(() => {
  cleanup()
})

describe('useImportQueue 任务列表', () => {
  it('按状态把任务分成运行中 / 排队 / 最近完成', async () => {
    vi.mocked(memoryApi.getMemoryImportTasks).mockResolvedValue({
      success: true,
      items: [
        makeTask({ task_id: 'run-1', status: 'preparing' }),
        makeTask({ task_id: 'run-2', status: ' running ' }),
        makeTask({ task_id: 'run-3', status: 'cancel_requested' }),
        makeTask({ task_id: 'queue-1', status: 'queued' }),
        makeTask({ task_id: 'done-1', status: 'completed' }),
        makeTask({ task_id: 'fail-1', status: 'failed' }),
      ],
    })

    const { result } = renderQueue()
    await waitFor(() => expect(result.current.runningImportTasks).toHaveLength(3))

    expect(result.current.runningImportTasks.map((task) => task.task_id)).toEqual([
      'run-1',
      'run-2',
      'run-3',
    ])
    expect(result.current.queuedImportTasks.map((task) => task.task_id)).toEqual(['queue-1'])
    expect(result.current.recentImportTasks.map((task) => task.task_id)).toEqual([
      'done-1',
      'fail-1',
    ])
  })

  it('面板激活后自动选中第一项并拉取详情与分块', async () => {
    const { result } = renderQueue()
    await waitForSelectedTask(result, 'task-1')

    await waitFor(() => {
      expect(memoryApi.getMemoryImportTasks).toHaveBeenCalledWith(20)
      expect(memoryApi.getMemoryImportTask).toHaveBeenCalledWith('task-1', false)
      expect(memoryApi.getMemoryImportTaskChunks).toHaveBeenCalledWith(
        'task-1',
        'file-1',
        0,
        IMPORT_CHUNK_PAGE_SIZE,
      )
    })
    expect(result.current.selectedImportFileId).toBe('file-1')
    expect(result.current.selectedImportChunks).toEqual([makeChunk()])
  })

  it('非激活时不拉任务、不订阅进度', async () => {
    renderQueue({ active: false })
    await act(async () => {
      await Promise.resolve()
    })

    expect(memoryApi.getMemoryImportTasks).not.toHaveBeenCalled()
    expect(progressState.handlers).toHaveLength(0)
  })

  it('任务列表查询失败写入局部错误文案', async () => {
    vi.mocked(memoryApi.getMemoryImportTasks).mockRejectedValue(new Error('网络中断'))
    const { result } = renderQueue()
    await waitFor(() => expect(result.current.importErrorText).toBe('网络中断'))
  })

  it('刷新后当前任务仍在队列则保持选中，否则切到第一项', async () => {
    const itemsRef = {
      current: [makeTask({ task_id: 'task-a' }), makeTask({ task_id: 'task-b' })],
    }
    vi.mocked(memoryApi.getMemoryImportTasks).mockImplementation(async () => ({
      success: true,
      items: itemsRef.current,
    }))

    const { result } = renderQueue()
    await waitForSelectedTask(result, 'task-a')
    await act(async () => {
      await result.current.selectImportTask('task-b')
    })
    expect(result.current.selectedImportTaskId).toBe('task-b')

    itemsRef.current = [makeTask({ task_id: 'task-c' })]
    await act(async () => {
      await result.current.refreshImportQueue()
    })
    await waitForSelectedTask(result, 'task-c')
  })
})

describe('useImportQueue WebSocket 进度与断线轮询', () => {
  it('激活时订阅 import_progress；进度事件刷新列表与选中详情', async () => {
    const { result } = renderQueue()
    await waitForSelectedTask(result, 'task-1')
    await waitFor(() => expect(progressState.handlers.length).toBeGreaterThan(0))
    expect(progressState.lastTopics).toEqual(['import_progress'])

    vi.mocked(memoryApi.getMemoryImportTasks).mockClear()
    vi.mocked(memoryApi.getMemoryImportTask).mockClear()

    await act(async () => {
      progressState.handlers[0]({
        topic: 'import_progress',
        event: 'progress',
        data: { task_id: 'task-1' },
      })
    })

    await waitFor(() => expect(memoryApi.getMemoryImportTasks).toHaveBeenCalled())
    await waitFor(() =>
      expect(memoryApi.getMemoryImportTask).toHaveBeenCalledWith('task-1', false),
    )
  })

  it('其它 topic 不刷新队列；进度刷新失败时静默只写错误', async () => {
    const { result } = renderQueue()
    await waitForSelectedTask(result, 'task-1')
    await waitFor(() => expect(progressState.handlers.length).toBeGreaterThan(0))
    vi.mocked(memoryApi.getMemoryImportTasks).mockClear()
    toastMock.mockClear()

    await act(async () => {
      progressState.handlers[0]({
        topic: 'delete_progress',
        event: 'progress',
        data: {},
      })
    })
    expect(memoryApi.getMemoryImportTasks).not.toHaveBeenCalled()

    vi.mocked(memoryApi.getMemoryImportTask).mockRejectedValue(new Error('详情失败'))
    await act(async () => {
      progressState.handlers[0]({
        topic: 'import_progress',
        event: 'progress',
        data: {},
      })
    })
    await waitFor(() => expect(result.current.importErrorText).toBe('详情失败'))
    expect(toastMock).not.toHaveBeenCalled()
  })

  it('激活期间无论 WS 是否连接都保持 refetchInterval 轮询', async () => {
    vi.mocked(memoryApi.getMemoryImportSettings).mockResolvedValue({
      success: true,
      settings: { poll_interval_ms: 1500 },
    })
    const { queryClient, result } = renderQueue()
    await waitForSelectedTask(result, 'task-1')
    await waitFor(() => expect(result.current.importPollInterval).toBe(1500))
    await waitFor(() => expect(getTasksRefetchInterval(queryClient)).toBe(1500))

    await act(async () => {
      wsState.setConnected(true)
    })
    await waitFor(() => expect(getTasksRefetchInterval(queryClient)).toBe(1500))

    await act(async () => {
      wsState.setConnected(false)
    })
    await waitFor(() => expect(getTasksRefetchInterval(queryClient)).toBe(1500))
  })

  it('poll_interval_ms 低于 200 时轮询间隔钳到 200', async () => {
    vi.mocked(memoryApi.getMemoryImportSettings).mockResolvedValue({
      success: true,
      settings: { poll_interval_ms: 50 },
    })
    const { queryClient, result } = renderQueue()
    await waitFor(() => expect(result.current.importPollInterval).toBe(200))
    await waitFor(() => expect(getTasksRefetchInterval(queryClient)).toBe(200))
  })

  it('面板从激活切到非激活会退订进度', async () => {
    const { rerender } = renderQueue()
    await waitFor(() => expect(progressState.handlers.length).toBeGreaterThan(0))
    rerender({ active: false, buildRetryOverrides: undefined })
    await waitFor(() => expect(progressState.handlers).toHaveLength(0))
  })

  it('订阅失败只打 warn，不阻断队列', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined)
    progressState.setSubscribe(async () => {
      throw new Error('ws down')
    })

    const { result } = renderQueue()
    await waitForSelectedTask(result, 'task-1')
    await waitFor(() =>
      expect(warn).toHaveBeenCalledWith(
        '订阅长期记忆 WebSocket 失败，已退化到轮询兜底',
        expect.any(Error),
      ),
    )
    warn.mockRestore()
  })
})

describe('useImportQueue 取消与重试', () => {
  it('取消选中任务成功后刷新队列并提示截断后的任务号', async () => {
    vi.mocked(memoryApi.getMemoryImportTasks).mockResolvedValue({
      success: true,
      items: [makeTask({ task_id: 'task-1234567890abc' })],
    })
    const { result } = renderQueue()
    await waitForSelectedTask(result, 'task-1234567890abc')

    await act(async () => {
      await result.current.cancelSelectedImportTask()
    })

    expect(memoryApi.cancelMemoryImportTask).toHaveBeenCalledWith('task-1234567890abc')
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({
        title: '已请求取消任务',
        description: '任务 task-1234567 正在取消',
      }),
    )
  })

  it('取消失败写入局部错误并弹出失败 toast', async () => {
    vi.mocked(memoryApi.cancelMemoryImportTask).mockResolvedValue({
      success: false,
      error: '任务已结束',
    })
    const { result } = renderQueue()
    await waitForSelectedTask(result, 'task-1')

    await act(async () => {
      await result.current.cancelSelectedImportTask()
    })

    expect(result.current.importErrorText).toBe('任务已结束')
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({
        title: '取消导入任务失败',
        description: '任务已结束',
        variant: 'destructive',
      }),
    )
  })

  it('取消抛出非 Error 时使用兜底文案；未选中任务是空操作', async () => {
    vi.mocked(memoryApi.getMemoryImportTasks).mockResolvedValue({
      success: true,
      items: [],
    })
    const empty = renderQueue()
    await waitFor(() => expect(empty.result.current.selectedImportTaskId).toBe(''))
    await act(async () => {
      await empty.result.current.cancelSelectedImportTask()
      await empty.result.current.retrySelectedImportTask()
    })
    expect(memoryApi.cancelMemoryImportTask).not.toHaveBeenCalled()
    expect(memoryApi.retryMemoryImportTask).not.toHaveBeenCalled()
    empty.unmount()

    vi.mocked(memoryApi.getMemoryImportTasks).mockResolvedValue({
      success: true,
      items: [makeTask()],
    })
    vi.mocked(memoryApi.cancelMemoryImportTask).mockRejectedValue('boom')
    const { result } = renderQueue()
    await waitForSelectedTask(result, 'task-1')
    await act(async () => {
      await result.current.cancelSelectedImportTask()
    })
    expect(result.current.importErrorText).toBe('取消导入任务失败')
  })

  it('重试会带上当前表单 overrides，并切到返回的新任务', async () => {
    const buildRetryOverrides = vi.fn(() => ({ force: true, dedupe_policy: 'content_hash' }))
    const itemsRef = { current: [makeTask({ task_id: 'task-1' })] }
    vi.mocked(memoryApi.getMemoryImportTasks).mockImplementation(async () => ({
      success: true,
      items: itemsRef.current,
    }))
    vi.mocked(memoryApi.retryMemoryImportTask).mockImplementation(async () => {
      const retryTask = makeTask({ task_id: 'retry-task-1234567890' })
      itemsRef.current = [makeTask({ task_id: 'task-1' }), retryTask]
      return { success: true, task: retryTask }
    })
    const { result } = renderQueue({ buildRetryOverrides })
    await waitForSelectedTask(result, 'task-1')

    await act(async () => {
      await result.current.retrySelectedImportTask()
    })

    expect(buildRetryOverrides).toHaveBeenCalledOnce()
    expect(memoryApi.retryMemoryImportTask).toHaveBeenCalledWith('task-1', {
      overrides: { force: true, dedupe_policy: 'content_hash' },
    })
    expect(result.current.selectedImportTaskId).toBe('retry-task-1234567890')
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({
        title: '重试任务已创建',
        description: '重试任务 retry-task-1 已进入队列',
      }),
    )
  })

  it('重试未返回新任务 id 时回落到当前任务；失败写入错误', async () => {
    vi.mocked(memoryApi.retryMemoryImportTask).mockResolvedValueOnce({
      success: true,
      task: undefined,
    })
    const { result } = renderQueue()
    await waitForSelectedTask(result, 'task-1')

    await act(async () => {
      await result.current.retrySelectedImportTask()
    })
    expect(result.current.selectedImportTaskId).toBe('task-1')
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({
        title: '重试任务已创建',
        description: '失败项已提交重试',
      }),
    )

    vi.mocked(memoryApi.retryMemoryImportTask).mockResolvedValueOnce({
      success: false,
      error: '',
    })
    await act(async () => {
      await result.current.retrySelectedImportTask()
    })
    expect(result.current.importErrorText).toBe('重试失败项失败')
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({
        title: '重试失败项失败',
        description: '重试失败项失败',
        variant: 'destructive',
      }),
    )
  })

  it('afterCreated 会静默刷新队列并选中新建任务', async () => {
    const itemsRef = { current: [makeTask({ task_id: 'old-task' })] }
    vi.mocked(memoryApi.getMemoryImportTasks).mockImplementation(async () => ({
      success: true,
      items: itemsRef.current,
    }))

    const { result } = renderQueue()
    await waitForSelectedTask(result, 'old-task')

    itemsRef.current = [makeTask({ task_id: 'old-task' }), makeTask({ task_id: 'new-task' })]
    await act(async () => {
      await result.current.afterCreated('new-task')
    })

    expect(result.current.selectedImportTaskId).toBe('new-task')
    expect(memoryApi.getMemoryImportTask).toHaveBeenCalledWith('new-task', false)
  })
})
