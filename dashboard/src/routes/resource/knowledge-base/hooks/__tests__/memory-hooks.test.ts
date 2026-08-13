/**
 * 长期记忆导入队列 / 记忆修正 hook 行为测试。
 * 覆盖队列分组与选中流转、取消/重试/分块分页、修正预览/执行/回滚载荷。
 */
import { createElement, type ReactNode } from 'react'

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type {
  MemoryCorrectionPlanPayload,
  MemoryImportChunkPayload,
  MemoryImportFilePayload,
  MemoryImportTaskPayload,
  MemoryRuntimeConfigPayload,
} from '@/lib/memory-api'
import type { MemoryProgressEvent } from '@/lib/memory-progress-client'

import {
  IMPORT_CHUNK_PAGE_SIZE,
  MEMORY_CORRECTION_FETCH_LIMIT,
  MEMORY_CORRECTION_PAGE_SIZE,
} from '../../constants'
import { useImportQueue } from '../useImportQueue'
import { useMemoryCorrection } from '../useMemoryCorrection'

const toastMock = vi.hoisted(() => vi.fn())

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
    },
    subscribe(
      handler: (event: MemoryProgressEvent) => void,
      topics?: string[],
    ): Promise<() => Promise<void>> {
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
    onConnectionChange: (listener: (connected: boolean) => void) => {
      listener(false)
      return () => undefined
    },
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
  getMemoryImportChatTargets: vi.fn(),
  getMemoryCorrectionPlans: vi.fn(),
  getMemoryCorrectionPlan: vi.fn(),
  previewMemoryCorrection: vi.fn(),
  executeMemoryCorrection: vi.fn(),
  rollbackMemoryCorrectionPlan: vi.fn(),
}))

import * as memoryApi from '@/lib/memory-api'

function makeWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return ({ children }: { children: ReactNode }) =>
    createElement(QueryClientProvider, { client: queryClient }, children)
}

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

function makePlan(
  overrides: Partial<MemoryCorrectionPlanPayload> = {},
): MemoryCorrectionPlanPayload {
  return {
    plan_id: 'plan-1',
    request_text: '把常住城市改为杭州',
    scope: 'person_profile',
    target_person_id: 'person-1',
    target_chat_id: 'chat-1',
    status: 'awaiting_confirmation',
    confidence: 0.91,
    plan: {
      scope: 'person_profile',
      request_text: '把常住城市改为杭州',
      person_id: 'person-1',
      chat_id: 'chat-1',
      confidence: 0.91,
      risk_level: 'medium',
      reason: 'plan-reason',
      operations: [],
    },
    preview: {
      request_text: '把常住城市改为杭州',
      scope: 'person_profile',
      person_id: 'person-1',
      person_keyword: '测试用户',
      chat_id: 'chat-1',
      candidates: [],
      operations: [],
      requires_confirmation: true,
      confirm_threshold: 0.75,
      reason: 'preview-reason',
    },
    execution: {},
    created_at: 1,
    updated_at: 2,
    requested_by: 'knowledge_base',
    reason: 'top-reason',
    ...overrides,
  }
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

function renderQueue(
  options: {
    active?: boolean
    buildRetryOverrides?: () => Record<string, unknown>
  } = {},
) {
  return renderHook(
    () =>
      useImportQueue({
        active: options.active ?? true,
        buildRetryOverrides: options.buildRetryOverrides,
      }),
    { wrapper: makeWrapper() },
  )
}

function renderCorrection(
  options: Parameters<typeof useMemoryCorrection>[0] = { active: true },
) {
  return renderHook((props: Parameters<typeof useMemoryCorrection>[0]) => useMemoryCorrection(props), {
    wrapper: makeWrapper(),
    initialProps: { active: true, ...options },
  })
}

async function waitForSelectedTask(result: { current: { selectedImportTaskId: string } }, taskId: string) {
  await waitFor(() => expect(result.current.selectedImportTaskId).toBe(taskId))
}

async function waitForSelectedPlan(
  result: {
    current: {
      selectedPlanId: string
      selectedPlanLoading: boolean
    }
  },
  planId: string,
) {
  await waitFor(() => {
    expect(result.current.selectedPlanId).toBe(planId)
    expect(result.current.selectedPlanLoading).toBe(false)
  })
}

beforeEach(() => {
  toastMock.mockReset()
  progressState.reset()

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
  vi.mocked(memoryApi.getMemoryImportChatTargets).mockResolvedValue({
    success: true,
    data: [],
  })
  vi.mocked(memoryApi.getMemoryCorrectionPlans).mockResolvedValue({
    success: true,
    items: [makePlan()],
    count: 1,
  })
  vi.mocked(memoryApi.getMemoryCorrectionPlan).mockImplementation(async (planId) => ({
    success: true,
    plan: makePlan({ plan_id: planId }),
  }))
  vi.mocked(memoryApi.previewMemoryCorrection).mockResolvedValue({
    success: true,
    plan_id: 'plan-preview',
    preview: makePlan({ plan_id: 'plan-preview' }).preview,
  })
  vi.mocked(memoryApi.executeMemoryCorrection).mockResolvedValue({
    success: true,
    plan: makePlan({ status: 'executed' }),
  })
  vi.mocked(memoryApi.rollbackMemoryCorrectionPlan).mockResolvedValue({
    success: true,
    plan: makePlan({ status: 'rolled_back' }),
  })
})

afterEach(() => {
  cleanup()
})

describe('useImportQueue', () => {
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

  it('面板激活后自动选中队列第一项并拉取详情与分块', async () => {
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

  it('刷新时空队列会清空选中任务与分块', async () => {
    const itemsRef = { current: [makeTask()] }
    vi.mocked(memoryApi.getMemoryImportTasks).mockImplementation(async () => ({
      success: true,
      items: itemsRef.current,
    }))

    const { result } = renderQueue()
    await waitForSelectedTask(result, 'task-1')

    itemsRef.current = []
    await act(async () => {
      await result.current.refreshImportQueue()
    })

    expect(result.current.selectedImportTaskId).toBe('')
    expect(result.current.selectedImportTaskResolved).toBeNull()
    expect(result.current.selectedImportChunks).toEqual([])
    expect(result.current.importErrorText).toBe('')
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

    await act(async () => {
      await result.current.refreshImportQueue()
    })
    expect(result.current.selectedImportTaskId).toBe('task-b')

    itemsRef.current = [makeTask({ task_id: 'task-c' })]
    await act(async () => {
      await result.current.refreshImportQueue()
    })
    await waitForSelectedTask(result, 'task-c')
  })

  it('afterCreated 会静默刷新队列并选中新建任务', async () => {
    const itemsRef = { current: [makeTask({ task_id: 'old-task' })] }
    vi.mocked(memoryApi.getMemoryImportTasks).mockImplementation(async () => ({
      success: true,
      items: itemsRef.current,
    }))

    const { result } = renderQueue()
    await waitForSelectedTask(result, 'old-task')

    itemsRef.current = [
      makeTask({ task_id: 'old-task' }),
      makeTask({ task_id: 'new-task' }),
    ]
    await act(async () => {
      await result.current.afterCreated('new-task')
    })

    expect(result.current.selectedImportTaskId).toBe('new-task')
    expect(memoryApi.getMemoryImportTask).toHaveBeenCalledWith('new-task', false)
  })

  it('取消选中任务成功后刷新队列并提示截断后的任务号', async () => {
    const { result } = renderQueue()
    await waitForSelectedTask(result, 'task-1')

    await act(async () => {
      await result.current.cancelSelectedImportTask()
    })

    expect(memoryApi.cancelMemoryImportTask).toHaveBeenCalledWith('task-1')
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({
        title: '已请求取消任务',
        description: '任务 task-1 正在取消',
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

  it('未选中任务时取消和重试都是空操作', async () => {
    vi.mocked(memoryApi.getMemoryImportTasks).mockResolvedValue({
      success: true,
      items: [],
    })
    const { result } = renderQueue()
    await waitFor(() => expect(result.current.selectedImportTaskId).toBe(''))

    await act(async () => {
      await result.current.cancelSelectedImportTask()
      await result.current.retrySelectedImportTask()
    })

    expect(memoryApi.cancelMemoryImportTask).not.toHaveBeenCalled()
    expect(memoryApi.retryMemoryImportTask).not.toHaveBeenCalled()
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

  it('重试未返回新任务 id 时回落到当前任务并使用兜底文案', async () => {
    vi.mocked(memoryApi.retryMemoryImportTask).mockResolvedValue({
      success: true,
      task: undefined,
    })
    const { result } = renderQueue()
    await waitForSelectedTask(result, 'task-1')
    const detailCallsBefore = vi.mocked(memoryApi.getMemoryImportTask).mock.calls.length

    await act(async () => {
      await result.current.retrySelectedImportTask()
    })

    expect(result.current.selectedImportTaskId).toBe('task-1')
    expect(vi.mocked(memoryApi.getMemoryImportTask).mock.calls.length).toBeGreaterThan(
      detailCallsBefore,
    )
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({
        title: '重试任务已创建',
        description: '失败项已提交重试',
      }),
    )
  })

  it('重试失败使用非 Error 兜底文案', async () => {
    vi.mocked(memoryApi.retryMemoryImportTask).mockRejectedValue('boom')
    const { result } = renderQueue()
    await waitForSelectedTask(result, 'task-1')

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

  it('切换文件会重置分块偏移并重新拉第一页', async () => {
    vi.mocked(memoryApi.getMemoryImportTask).mockResolvedValue({
      success: true,
      task: makeTask({
        files: [makeFile({ file_id: 'file-a' }), makeFile({ file_id: 'file-b' })],
      }),
    })
    const { result } = renderQueue()
    await waitForSelectedTask(result, 'task-1')
    await waitFor(() => expect(result.current.selectedImportFileId).toBe('file-a'))

    await act(async () => {
      await result.current.selectImportFile('file-b')
    })

    expect(result.current.selectedImportFileId).toBe('file-b')
    expect(result.current.importChunkOffset).toBe(0)
    expect(memoryApi.getMemoryImportTaskChunks).toHaveBeenCalledWith(
      'task-1',
      'file-b',
      0,
      IMPORT_CHUNK_PAGE_SIZE,
    )
  })

  it('分块下一页推进偏移，上一页在起点是空操作', async () => {
    vi.mocked(memoryApi.getMemoryImportTaskChunks).mockImplementation(
      async (_taskId, _fileId, offset) => ({
        success: true,
        items: [makeChunk({ chunk_id: `chunk-${offset}` })],
        total: IMPORT_CHUNK_PAGE_SIZE + 5,
        offset,
        limit: IMPORT_CHUNK_PAGE_SIZE,
      }),
    )
    const { result } = renderQueue()
    await waitForSelectedTask(result, 'task-1')
    await waitFor(() => expect(result.current.canImportChunkNext).toBe(true))
    expect(result.current.canImportChunkPrev).toBe(false)

    await act(async () => {
      await result.current.moveImportChunkPage(-1)
    })
    expect(result.current.importChunkOffset).toBe(0)

    await act(async () => {
      await result.current.moveImportChunkPage(1)
    })
    expect(result.current.importChunkOffset).toBe(IMPORT_CHUNK_PAGE_SIZE)
    expect(result.current.canImportChunkPrev).toBe(true)
    expect(result.current.canImportChunkNext).toBe(false)
    expect(memoryApi.getMemoryImportTaskChunks).toHaveBeenCalledWith(
      'task-1',
      'file-1',
      IMPORT_CHUNK_PAGE_SIZE,
      IMPORT_CHUNK_PAGE_SIZE,
    )
  })

  it('详情刷新若仍选中同一文件会保留当前分块偏移', async () => {
    vi.mocked(memoryApi.getMemoryImportTaskChunks).mockImplementation(
      async (_taskId, _fileId, offset) => ({
        success: true,
        items: [makeChunk({ chunk_id: `chunk-${offset}` })],
        total: IMPORT_CHUNK_PAGE_SIZE + 2,
        offset,
        limit: IMPORT_CHUNK_PAGE_SIZE,
      }),
    )
    const { result } = renderQueue()
    await waitForSelectedTask(result, 'task-1')
    await act(async () => {
      await result.current.moveImportChunkPage(1)
    })
    expect(result.current.importChunkOffset).toBe(IMPORT_CHUNK_PAGE_SIZE)
    vi.mocked(memoryApi.getMemoryImportTaskChunks).mockClear()

    await waitFor(() => expect(progressState.handlers.length).toBeGreaterThan(0))
    await act(async () => {
      progressState.handlers[0]({
        topic: 'import_progress',
        event: 'progress',
        data: { task_id: 'task-1' },
      })
    })

    await waitFor(() =>
      expect(memoryApi.getMemoryImportTaskChunks).toHaveBeenCalledWith(
        'task-1',
        'file-1',
        IMPORT_CHUNK_PAGE_SIZE,
        IMPORT_CHUNK_PAGE_SIZE,
      ),
    )
  })

  it('import_progress 会失效任务列表；其它 topic 不触发刷新', async () => {
    const { result } = renderQueue()
    await waitForSelectedTask(result, 'task-1')
    await waitFor(() => expect(progressState.handlers.length).toBeGreaterThan(0))
    vi.mocked(memoryApi.getMemoryImportTasks).mockClear()

    await act(async () => {
      progressState.handlers[0]({
        topic: 'delete_progress',
        event: 'progress',
        data: {},
      })
    })
    expect(memoryApi.getMemoryImportTasks).not.toHaveBeenCalled()

    result.current.invalidate()
    await waitFor(() => expect(memoryApi.getMemoryImportTasks).toHaveBeenCalled())
  })

  it('轮询间隔取 settings.poll_interval_ms，且不低于 200ms', async () => {
    vi.mocked(memoryApi.getMemoryImportSettings).mockResolvedValue({
      success: true,
      settings: { poll_interval_ms: 50 },
    })
    const low = renderQueue()
    await waitFor(() => expect(low.result.current.importPollInterval).toBe(200))
    low.unmount()

    vi.mocked(memoryApi.getMemoryImportSettings).mockResolvedValue({
      success: true,
      settings: { poll_interval_ms: 1500 },
    })
    const high = renderQueue()
    await waitFor(() => expect(high.result.current.importPollInterval).toBe(1500))
  })

  it('任务列表查询失败写入局部错误文案，非 Error 走兜底句', async () => {
    vi.mocked(memoryApi.getMemoryImportTasks).mockRejectedValue(new Error('网络中断'))
    const errorView = renderQueue()
    await waitFor(() => expect(errorView.result.current.importErrorText).toBe('网络中断'))
    errorView.unmount()

    vi.mocked(memoryApi.getMemoryImportTasks).mockRejectedValue('bad')
    const fallbackView = renderQueue()
    await waitFor(() =>
      expect(fallbackView.result.current.importErrorText).toBe('刷新导入任务失败'),
    )
  })

  it('刷新队列失败时非静默弹 toast，静默只写错误文案', async () => {
    const { result } = renderQueue()
    await waitForSelectedTask(result, 'task-1')

    vi.mocked(memoryApi.getMemoryImportTasks).mockRejectedValue(new Error('二次失败'))
    toastMock.mockClear()
    await act(async () => {
      await result.current.refreshImportQueue()
    })
    expect(result.current.importErrorText).toBe('二次失败')
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({
        title: '刷新导入任务失败',
        description: '二次失败',
        variant: 'destructive',
      }),
    )

    toastMock.mockClear()
    vi.mocked(memoryApi.getMemoryImportTasks).mockRejectedValue('x')
    await act(async () => {
      await result.current.refreshImportQueue(true)
    })
    expect(result.current.importErrorText).toBe('刷新导入任务失败')
    expect(toastMock).not.toHaveBeenCalled()
  })

  it('任务详情失败时回落到列表摘要上的 error / retry_summary', async () => {
    vi.mocked(memoryApi.getMemoryImportTasks).mockResolvedValue({
      success: true,
      items: [
        makeTask({
          error: '  列表错误  ',
          retry_summary: { parent_task_id: 'parent-1', chunk_retry_chunks: 3 },
        }),
      ],
    })
    vi.mocked(memoryApi.getMemoryImportTask).mockResolvedValue({
      success: false,
      error: '任务不存在了',
    })

    const { result } = renderQueue()
    await waitFor(() => expect(result.current.importErrorText).toBe('任务不存在了'))
    expect(result.current.selectedImportTaskErrorText).toBe('列表错误')
    expect(result.current.selectedImportRetrySummary).toEqual({
      parent_task_id: 'parent-1',
      chunk_retry_chunks: 3,
    })
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({
        title: '加载导入任务详情失败',
        description: '任务不存在了',
      }),
    )
  })

  it('任务没有文件时不拉分块；队列非空时清空选中会被自动切回第一项', async () => {
    vi.mocked(memoryApi.getMemoryImportTask).mockResolvedValue({
      success: true,
      task: makeTask({ files: [] }),
    })
    const { result } = renderQueue()
    await waitForSelectedTask(result, 'task-1')
    await waitFor(() => expect(result.current.selectedImportFiles).toEqual([]))
    expect(result.current.selectedImportChunks).toEqual([])

    await act(async () => {
      await result.current.selectImportTask('')
    })
    await waitForSelectedTask(result, 'task-1')
  })

  it('分块接口 success=false 时清空分块并弹 toast', async () => {
    vi.mocked(memoryApi.getMemoryImportTaskChunks).mockResolvedValue({
      success: false,
      error: '',
    })
    const { result } = renderQueue()
    await waitFor(() => expect(result.current.importErrorText).toBe('加载分块详情失败'))
    expect(result.current.selectedImportChunks).toEqual([])
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({
        title: '加载分块详情失败',
        description: '加载分块详情失败',
      }),
    )
  })

  it('卸载时若订阅尚未完成会立刻执行 cleanup', async () => {
    let resolveSubscribe: ((cleanup: () => Promise<void>) => void) | undefined
    const cleanupFn = vi.fn(async () => undefined)
    progressState.setSubscribe(
      () =>
        new Promise((resolve) => {
          resolveSubscribe = resolve
        }),
    )

    const { unmount } = renderQueue()
    await waitFor(() => expect(resolveSubscribe).toBeTypeOf('function'))
    unmount()
    await act(async () => {
      resolveSubscribe?.(cleanupFn)
      await Promise.resolve()
    })
    expect(cleanupFn).toHaveBeenCalledOnce()
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

describe('useMemoryCorrection', () => {
  it('丢掉没有 plan_id 的条目，并按搜索/状态/范围过滤', async () => {
    vi.mocked(memoryApi.getMemoryCorrectionPlans).mockResolvedValue({
      success: true,
      items: [
        makePlan({ plan_id: 'keep-person', status: 'executed', scope: 'person_profile' }),
        makePlan({
          plan_id: 'keep-memory',
          status: 'awaiting_confirmation',
          scope: 'memory',
          request_text: '忘掉旧住址',
          reason: 'memory-only',
        }),
        { ...makePlan(), plan_id: '' },
      ],
      count: 3,
    })

    const { result } = renderCorrection()
    await waitFor(() => expect(result.current.plans).toHaveLength(2))

    act(() => result.current.setPlanStatusFilter('executed'))
    expect(result.current.filteredPlans.map((plan) => plan.plan_id)).toEqual(['keep-person'])

    act(() => {
      result.current.setPlanStatusFilter('all')
      result.current.setPlanScopeFilter('memory')
    })
    expect(result.current.filteredPlans.map((plan) => plan.plan_id)).toEqual(['keep-memory'])

    act(() => {
      result.current.setPlanScopeFilter('all')
      result.current.setPlanSearch('MEMORY-ONLY')
    })
    expect(result.current.filteredPlans.map((plan) => plan.plan_id)).toEqual(['keep-memory'])
  })

  it('筛选变化重置到第 1 页，并在页数收缩时回夹当前页', async () => {
    const items = Array.from({ length: MEMORY_CORRECTION_PAGE_SIZE + 2 }, (_, index) =>
      makePlan({
        plan_id: `plan-${index + 1}`,
        request_text: index === MEMORY_CORRECTION_PAGE_SIZE ? '唯一关键词杭州' : `条目 ${index}`,
        status: index === 0 ? 'executed' : 'awaiting_confirmation',
      }),
    )
    vi.mocked(memoryApi.getMemoryCorrectionPlans).mockResolvedValue({
      success: true,
      items,
      count: items.length,
    })

    const { result } = renderCorrection()
    await waitFor(() => expect(result.current.plans).toHaveLength(items.length))
    expect(result.current.planPageCount).toBe(2)
    expect(result.current.pagedPlans).toHaveLength(MEMORY_CORRECTION_PAGE_SIZE)

    act(() => result.current.setPlanPage(2))
    expect(result.current.pagedPlans).toHaveLength(2)

    act(() => result.current.setPlanSearch('唯一关键词杭州'))
    await waitFor(() => expect(result.current.planPage).toBe(1))
    expect(result.current.filteredPlans).toHaveLength(1)
  })

  it('列表到达后自动选中第一项并拉取详情', async () => {
    const { result } = renderCorrection()
    await waitForSelectedPlan(result, 'plan-1')

    expect(memoryApi.getMemoryCorrectionPlans).toHaveBeenCalledWith({
      limit: MEMORY_CORRECTION_FETCH_LIMIT,
    })
    expect(memoryApi.getMemoryCorrectionPlan).toHaveBeenCalledWith('plan-1')
    expect(result.current.selectedPlan?.plan_id).toBe('plan-1')
    expect(result.current.selectedPreview?.reason).toBe('preview-reason')
  })

  it('详情 success=false 与抛错分别写入错误文案', async () => {
    vi.mocked(memoryApi.getMemoryCorrectionPlan).mockResolvedValue({
      success: false,
      error: '',
    })
    const missing = renderCorrection()
    await waitFor(() =>
      expect(missing.result.current.selectedPlanError).toBe('未能加载记忆修正计划详情'),
    )
    missing.unmount()

    vi.mocked(memoryApi.getMemoryCorrectionPlan).mockRejectedValue(new Error('详情超时'))
    const failed = renderCorrection()
    await waitFor(() => expect(failed.result.current.selectedPlanError).toBe('详情超时'))
    failed.unmount()

    vi.mocked(memoryApi.getMemoryCorrectionPlan).mockRejectedValue('bad')
    const fallback = renderCorrection()
    await waitFor(() =>
      expect(fallback.result.current.selectedPlanError).toBe('未能加载记忆修正计划详情'),
    )
  })

  it('计划列表/聊天流查询失败分别写入局部错误文案', async () => {
    vi.mocked(memoryApi.getMemoryCorrectionPlans).mockRejectedValue(new Error('计划接口挂了'))
    vi.mocked(memoryApi.getMemoryImportChatTargets).mockRejectedValue('bad')
    const { result } = renderCorrection()

    await waitFor(() => expect(result.current.correctionErrorText).toBe('计划接口挂了'))
    await waitFor(() => expect(result.current.chatTargetsErrorText).toBe('加载聊天流列表失败'))
  })

  it('缺少修正内容或人物定位时不发预览请求', async () => {
    const { result } = renderCorrection()
    await waitForSelectedPlan(result, 'plan-1')

    await act(async () => {
      await result.current.submitPreview()
    })
    expect(memoryApi.previewMemoryCorrection).not.toHaveBeenCalled()
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({ title: '缺少修正内容' }),
    )

    toastMock.mockClear()
    act(() => result.current.setRequestText('  改城市  '))
    await act(async () => {
      await result.current.submitPreview()
    })
    expect(memoryApi.previewMemoryCorrection).not.toHaveBeenCalled()
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({ title: '缺少人物定位信息' }),
    )
  })

  it('人物关键词也可通过定位校验，并按配置上限裁剪 candidate limit', async () => {
    const { result } = renderCorrection({
      runtimeConfig: makeRuntime({ fuzzy_modify_candidate_limit: 12 }),
    })
    await waitFor(() => expect(result.current.candidateLimitMax).toBe(12))

    act(() => {
      result.current.setRequestText('  改成杭州  ')
      result.current.setPersonKeyword(' 测试用户 ')
      result.current.setChatId(' chat-9 ')
      result.current.setCorrectionReason(' 人工确认 ')
      result.current.setCandidateLimit('99')
    })
    await act(async () => {
      await result.current.submitPreview()
    })

    expect(memoryApi.previewMemoryCorrection).toHaveBeenCalledWith({
      request_text: '改成杭州',
      scope: 'person_profile',
      person_id: undefined,
      person_keyword: '测试用户',
      chat_id: 'chat-9',
      limit: 12,
      requested_by: 'knowledge_base',
      reason: '人工确认',
    })
  })

  it('空/非法 limit 回落到配置值；无配置且为空则不传 limit', async () => {
    const configured = renderCorrection({
      runtimeConfig: makeRuntime({ fuzzy_modify_candidate_limit: 8 }),
    })
    await waitFor(() => expect(configured.result.current.candidateLimit).toBe('8'))
    act(() => {
      configured.result.current.setRequestText('改')
      configured.result.current.setPersonId('p1')
      configured.result.current.setScope('memory')
      configured.result.current.setCandidateLimit('   ')
    })
    await act(async () => {
      await configured.result.current.submitPreview()
    })
    expect(memoryApi.previewMemoryCorrection).toHaveBeenLastCalledWith(
      expect.objectContaining({ limit: 8, scope: 'memory', person_id: 'p1' }),
    )

    act(() => configured.result.current.setCandidateLimit('not-a-number'))
    await act(async () => {
      await configured.result.current.submitPreview()
    })
    expect(memoryApi.previewMemoryCorrection).toHaveBeenLastCalledWith(
      expect.objectContaining({ limit: 8 }),
    )
    configured.unmount()

    const unconfigured = renderCorrection({ runtimeConfig: makeRuntime() })
    await waitFor(() => expect(unconfigured.result.current.candidateLimit).toBe(''))
    act(() => {
      unconfigured.result.current.setRequestText('改')
      unconfigured.result.current.setPersonId('p1')
    })
    await act(async () => {
      await unconfigured.result.current.submitPreview()
    })
    expect(memoryApi.previewMemoryCorrection).toHaveBeenLastCalledWith(
      expect.objectContaining({ limit: undefined }),
    )

    act(() => unconfigured.result.current.setCandidateLimit('3.8'))
    await act(async () => {
      await unconfigured.result.current.submitPreview()
    })
    expect(memoryApi.previewMemoryCorrection).toHaveBeenLastCalledWith(
      expect.objectContaining({ limit: 3 }),
    )
  })

  it('候选上限优先读顶层，其次 integration / a_memorix.integration；用户改过不再被配置覆盖', async () => {
    const { result, rerender } = renderCorrection({
      runtimeConfig: makeRuntime({
        fuzzy_modify_candidate_limit: 0,
        config: { integration: { fuzzy_modify_candidate_limit: 9 } },
      }),
    })
    await waitFor(() => expect(result.current.candidateLimitMax).toBe(9))
    expect(result.current.candidateLimit).toBe('9')

    rerender({
      active: true,
      runtimeConfig: makeRuntime({
        config: {
          a_memorix: { integration: { fuzzy_modify_candidate_limit: 4 } },
        },
      }),
    })
    await waitFor(() => expect(result.current.candidateLimit).toBe('4'))

    act(() => {
      result.current.setCandidateLimit((current) => `${current}0`)
    })
    expect(result.current.candidateLimit).toBe('40')
    rerender({
      active: true,
      runtimeConfig: makeRuntime({ fuzzy_modify_candidate_limit: 2 }),
    })
    expect(result.current.candidateLimit).toBe('40')
    expect(result.current.candidateLimitMax).toBe(2)
  })

  it('预览成功后选中新计划，优先用 payload.plan_id，并刷新详情', async () => {
    const itemsRef = { current: [makePlan({ plan_id: 'plan-1' })] }
    vi.mocked(memoryApi.getMemoryCorrectionPlans).mockImplementation(async () => ({
      success: true,
      items: itemsRef.current,
      count: itemsRef.current.length,
    }))
    vi.mocked(memoryApi.previewMemoryCorrection).mockImplementation(async () => {
      itemsRef.current = [makePlan({ plan_id: 'plan-new' })]
      return {
        success: true,
        plan_id: 'plan-new',
        preview: {
          ...makePlan().preview,
          reason: '新预览',
        },
      }
    })
    vi.mocked(memoryApi.getMemoryCorrectionPlan).mockImplementation(async (planId) => ({
      success: true,
      plan: makePlan({
        plan_id: planId,
        preview: { ...makePlan().preview, reason: '详情预览' },
      }),
    }))

    const { result } = renderCorrection()
    await waitForSelectedPlan(result, 'plan-1')
    act(() => {
      result.current.setRequestText('改成杭州')
      result.current.setPersonId('person-1')
    })
    await act(async () => {
      await result.current.submitPreview()
    })

    await waitFor(() => expect(result.current.selectedPlanId).toBe('plan-new'))
    expect(result.current.planSearch).toBe('plan-new')
    expect(result.current.previewPayload?.plan_id).toBe('plan-new')
    expect(result.current.selectedPreview?.reason).toBe('新预览')
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({
        title: '已生成记忆修正预览',
        description: '计划 plan-new 等待确认',
      }),
    )
    expect(memoryApi.getMemoryCorrectionPlan).toHaveBeenCalledWith('plan-new')
  })

  it('预览只带 plan 对象时回退取其 plan_id；两边都没有则用检查文案', async () => {
    const itemsRef = { current: [makePlan({ plan_id: 'plan-1' })] }
    vi.mocked(memoryApi.getMemoryCorrectionPlans).mockImplementation(async () => ({
      success: true,
      items: itemsRef.current,
      count: itemsRef.current.length,
    }))
    vi.mocked(memoryApi.previewMemoryCorrection).mockImplementation(async () => {
      itemsRef.current = [makePlan({ plan_id: 'from-plan-object' })]
      return {
        success: true,
        plan: makePlan({ plan_id: 'from-plan-object' }),
      }
    })
    const first = renderCorrection()
    await waitForSelectedPlan(first.result, 'plan-1')
    act(() => {
      first.result.current.setRequestText('改')
      first.result.current.setPersonId('p')
    })
    await act(async () => {
      await first.result.current.submitPreview()
    })
    await waitFor(() => expect(first.result.current.selectedPlanId).toBe('from-plan-object'))
    first.unmount()

    vi.mocked(memoryApi.getMemoryCorrectionPlans).mockResolvedValue({
      success: true,
      items: [makePlan()],
      count: 1,
    })
    vi.mocked(memoryApi.previewMemoryCorrection).mockResolvedValue({
      success: true,
    })
    const second = renderCorrection()
    await waitForSelectedPlan(second.result, 'plan-1')
    act(() => {
      second.result.current.setRequestText('改')
      second.result.current.setPersonId('p')
    })
    await act(async () => {
      await second.result.current.submitPreview()
    })
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({
        description: '请检查预览结果后再确认执行',
      }),
    )
  })

  it('预览 success=false 仍写入 previewPayload，并弹出失败 toast', async () => {
    vi.mocked(memoryApi.previewMemoryCorrection).mockResolvedValue({
      success: false,
      error: '',
    })
    const { result } = renderCorrection()
    await waitForSelectedPlan(result, 'plan-1')
    act(() => {
      result.current.setRequestText('改')
      result.current.setPersonId('p')
    })
    await act(async () => {
      await result.current.submitPreview()
    })

    expect(result.current.previewPayload).toEqual({ success: false, error: '' })
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({
        title: '生成记忆修正预览失败',
        description: '生成记忆修正预览失败',
        variant: 'destructive',
      }),
    )
    expect(result.current.previewing).toBe(false)
  })

  it('预览已成功但详情同步失败时弹出同步告警', async () => {
    const { result } = renderCorrection()
    await waitForSelectedPlan(result, 'plan-1')
    vi.mocked(memoryApi.getMemoryCorrectionPlan).mockRejectedValue(new Error('同步超时'))
    act(() => {
      result.current.setRequestText('改')
      result.current.setPersonId('p')
    })
    await act(async () => {
      await result.current.submitPreview()
    })

    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({
        title: '预览已生成，但界面同步失败',
        description: '同步超时',
        variant: 'destructive',
      }),
    )
  })

  it('executePlan 空 id 直接返回', async () => {
    vi.mocked(memoryApi.getMemoryCorrectionPlans).mockResolvedValue({
      success: true,
      items: [],
    })
    const { result } = renderCorrection({ initialPlanId: '' })
    await waitFor(() => expect(result.current.selectedPlan).toBeNull())
    await act(async () => {
      await result.current.executePlan('   ')
    })
    expect(memoryApi.executeMemoryCorrection).not.toHaveBeenCalled()
  })

  it('executePlan 成功时带上确认载荷并用返回的 plan 更新详情', async () => {
    const onSourcesChanged = vi.fn()
    const onRuntimeChanged = vi.fn()
    const { result } = renderCorrection({ onSourcesChanged, onRuntimeChanged })
    await waitForSelectedPlan(result, 'plan-1')
    act(() => result.current.setCorrectionReason(' 执行原因 '))

    await act(async () => {
      await result.current.executePlan()
    })

    expect(memoryApi.executeMemoryCorrection).toHaveBeenCalledWith({
      plan_id: 'plan-1',
      confirmed: true,
      requested_by: 'knowledge_base',
      reason: '执行原因',
    })
    expect(result.current.selectedPlan?.status).toBe('executed')
    expect(onSourcesChanged).toHaveBeenCalled()
    expect(onRuntimeChanged).toHaveBeenCalled()
    expect(result.current.executingPlanId).toBe('')
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({
        title: '记忆修正已执行',
        description: '计划 plan-1 已写入执行结果',
      }),
    )
  })

  it('执行未带回 plan 时补拉详情，详情同步失败会告警', async () => {
    vi.mocked(memoryApi.executeMemoryCorrection).mockResolvedValue({
      success: true,
      plan: null,
    })
    const { result } = renderCorrection()
    await waitForSelectedPlan(result, 'plan-1')
    vi.mocked(memoryApi.getMemoryCorrectionPlan).mockRejectedValue(new Error('详情丢了'))
    await act(async () => {
      await result.current.executePlan('plan-1')
    })

    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({
        title: '执行已完成，但详情同步失败',
        description: '详情丢了',
      }),
    )
  })

  it('执行后界面同步部分失败、以及执行接口失败都有对应 toast', async () => {
    const onSourcesChanged = vi.fn().mockRejectedValue(new Error('来源刷新失败'))
    const ok = renderCorrection({ onSourcesChanged })
    await waitForSelectedPlan(ok.result, 'plan-1')
    await act(async () => {
      await ok.result.current.executePlan('plan-1')
    })
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({
        title: '执行已完成，但界面同步未完全成功',
        description: '来源刷新失败',
      }),
    )
    ok.unmount()

    vi.mocked(memoryApi.executeMemoryCorrection).mockResolvedValue({
      success: false,
      error: '',
    })
    const failed = renderCorrection()
    await waitForSelectedPlan(failed.result, 'plan-1')
    toastMock.mockClear()
    await act(async () => {
      await failed.result.current.executePlan('plan-1')
    })
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({
        title: '执行记忆修正失败',
        description: '执行记忆修正失败',
      }),
    )
  })

  it('rollbackPlan 成功时写入回滚载荷并刷新来源/运行时', async () => {
    const onSourcesChanged = vi.fn()
    const onRuntimeChanged = vi.fn()
    const rolled = makePlan({
      plan_id: 'plan-1',
      status: 'rolled_back',
      reason: '回滚完成',
    })
    vi.mocked(memoryApi.rollbackMemoryCorrectionPlan).mockResolvedValue({
      success: true,
      plan: rolled,
    })

    const { result } = renderCorrection({ onSourcesChanged, onRuntimeChanged })
    await waitForSelectedPlan(result, 'plan-1')
    act(() => result.current.setCorrectionReason(' 回滚原因 '))

    await act(async () => {
      await result.current.rollbackPlan()
    })

    expect(memoryApi.rollbackMemoryCorrectionPlan).toHaveBeenCalledWith('plan-1', {
      requested_by: 'knowledge_base',
      reason: '回滚原因',
    })
    expect(result.current.selectedPlan?.status).toBe('rolled_back')
    expect(onSourcesChanged).toHaveBeenCalled()
    expect(onRuntimeChanged).toHaveBeenCalled()
    expect(result.current.rollingBackPlanId).toBe('')
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({
        title: '记忆修正已回滚',
        description: '计划 plan-1 的回滚结果已写入日志',
      }),
    )
  })

  it('回滚未带回 plan 时补拉详情；详情失败弹出同步告警', async () => {
    vi.mocked(memoryApi.rollbackMemoryCorrectionPlan).mockResolvedValue({
      success: true,
      plan: null,
    })
    const { result } = renderCorrection()
    await waitForSelectedPlan(result, 'plan-1')
    vi.mocked(memoryApi.getMemoryCorrectionPlan).mockRejectedValue('sync-bad')
    await act(async () => {
      await result.current.rollbackPlan('plan-1')
    })

    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({
        title: '回滚已完成，但详情同步失败',
        description: '请手动刷新后确认最新状态',
      }),
    )
  })

  it('rollbackPlan 空白 id 早退', async () => {
    const { result } = renderCorrection()
    await waitForSelectedPlan(result, 'plan-1')
    await act(async () => {
      await result.current.rollbackPlan('   ')
    })
    expect(memoryApi.rollbackMemoryCorrectionPlan).not.toHaveBeenCalled()
  })

  it('回滚后界面同步失败、回滚接口失败分别弹对应 toast', async () => {
    const onRuntimeChanged = vi.fn().mockRejectedValue(new Error('运行时刷新失败'))
    const syncFail = renderCorrection({ onRuntimeChanged })
    await waitForSelectedPlan(syncFail.result, 'plan-1')
    await act(async () => {
      await syncFail.result.current.rollbackPlan('plan-1')
    })
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({
        title: '回滚已完成，但界面同步未完全成功',
        description: '运行时刷新失败',
      }),
    )
    syncFail.unmount()

    vi.mocked(memoryApi.rollbackMemoryCorrectionPlan).mockResolvedValue({
      success: false,
      error: '',
    })
    const apiFail = renderCorrection()
    await waitForSelectedPlan(apiFail.result, 'plan-1')
    toastMock.mockClear()
    await act(async () => {
      await apiFail.result.current.rollbackPlan('plan-1')
    })
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({
        title: '回滚记忆修正失败',
        description: '回滚记忆修正失败',
      }),
    )

    vi.mocked(memoryApi.rollbackMemoryCorrectionPlan).mockRejectedValue('x')
    toastMock.mockClear()
    await act(async () => {
      await apiFail.result.current.rollbackPlan('plan-1')
    })
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({
        title: '回滚记忆修正失败',
        description: '未知错误',
      }),
    )
  })

  it('initialPlanId 不在列表里时仍保留并尝试拉详情；refreshPlans 会重拉列表', async () => {
    const { result } = renderCorrection({ initialPlanId: 'deep-link-plan' })
    await waitFor(() =>
      expect(memoryApi.getMemoryCorrectionPlan).toHaveBeenCalledWith('deep-link-plan'),
    )
    expect(result.current.selectedPlanId).toBe('deep-link-plan')
    expect(result.current.planSearch).toBe('deep-link-plan')

    vi.mocked(memoryApi.getMemoryCorrectionPlans).mockClear()
    await act(async () => {
      await result.current.refreshPlans()
    })
    expect(memoryApi.getMemoryCorrectionPlans).toHaveBeenCalled()
  })

  it('非激活时不拉计划列表', async () => {
    renderCorrection({ active: false })
    await act(async () => {
      await Promise.resolve()
    })
    expect(memoryApi.getMemoryCorrectionPlans).not.toHaveBeenCalled()
  })
})
