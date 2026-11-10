/**
 * useImportQueue —— 长期记忆「导入队列」领域 hook（页面逻辑下沉的样板切片）。
 *
 * 收编导入任务队列相关的服务端状态与交互：
 * - 任务列表（tasks）与导入设置（settings）走 useQuery，仅在导入面板激活时拉取（enabled: active）；
 * - 轮询刷新任务进度
 *   （refetchInterval = active ? importPollInterval : false）；
 * - 选中任务详情与分块分页仍以本地 state + 命令式加载维持（依赖用户选择，不适合纯查询缓存）；
 * - 队列读失败用 importErrorText 局部呈现，取消/重试写失败仍走全局 toast。
 *
 * 与 useImportForm 共享 settings 查询（同 queryKey 由 React Query 去重），表单侧负责默认值 seed 与别名联动。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { useQuery } from '@tanstack/react-query'

import { useToast } from '@/hooks/use-toast'
import {
  cancelMemoryImportTask,
  getMemoryImportSettings,
  getMemoryImportTask,
  getMemoryImportTaskChunks,
  getMemoryImportTasks,
  retryMemoryImportTask,
  type MemoryImportChunkListPayload,
  type MemoryImportChunkPayload,
  type MemoryImportFilePayload,
  type MemoryImportRetrySummary,
  type MemoryImportSettings,
  type MemoryImportTaskPayload,
} from '@/lib/memory-api'

import { IMPORT_CHUNK_PAGE_SIZE, QUEUED_IMPORT_STATUS, RUNNING_IMPORT_STATUS } from '../constants'

export interface UseImportQueueOptions {
  /** 导入面板是否激活；非激活时不拉取、不轮询 */
  active: boolean
  /** 重试时构建 overrides 的回调，由表单 hook 提供当前公共参数（拆分前 retry 直接读表单参数） */
  buildRetryOverrides?: () => Record<string, unknown>
}

export interface UseImportQueueResult {
  refreshImportQueue: (silent?: boolean) => Promise<void>
  runningImportTasks: MemoryImportTaskPayload[]
  queuedImportTasks: MemoryImportTaskPayload[]
  recentImportTasks: MemoryImportTaskPayload[]
  selectedImportTaskId: string
  selectImportTask: (taskId: string) => Promise<void>
  importPollInterval: number
  importErrorText: string
  cancelSelectedImportTask: () => Promise<void>
  retrySelectedImportTask: () => Promise<void>
  selectedImportTaskLoading: boolean
  selectedImportTaskResolved: MemoryImportTaskPayload | null | undefined
  selectedImportRetrySummary: MemoryImportRetrySummary | null | undefined
  selectedImportTaskErrorText: string
  selectedImportFiles: MemoryImportFilePayload[]
  selectedImportFileId: string
  selectImportFile: (fileId: string) => Promise<void>
  importChunkTotal: number
  importChunkOffset: number
  moveImportChunkPage: (direction: -1 | 1) => Promise<void>
  canImportChunkPrev: boolean
  canImportChunkNext: boolean
  importChunksLoading: boolean
  selectedImportChunks: MemoryImportChunkPayload[]
  /** 供 useImportForm 在创建任务成功后调用：刷新队列并选中新任务 */
  afterCreated: (taskId: string) => Promise<void>
  /** 外部变更后重新拉取导入任务查询 */
  invalidate: () => void
}

/**
 * 管理导入任务轮询、当前详情和分块，让手动刷新与任务操作共用同一加载路径。
 *
 * 详情与分块响应只有在请求序号及当前选择仍匹配时才能写回；切换选择或卸载会使旧响应失效。
 * 即使列表内容未变，手动刷新也重新加载详情，并在当前文件仍存在时保留其分块偏移。
 */
export function useImportQueue({
  active,
  buildRetryOverrides,
}: UseImportQueueOptions): UseImportQueueResult {
  const { toast } = useToast()

  // 导入设置：仅用于派生轮询间隔；与 useImportForm 共享同一查询缓存
  const settingsQuery = useQuery({
    queryKey: ['memory-import', 'settings'],
    queryFn: () => getMemoryImportSettings(),
    enabled: active,
  })
  const importSettings: MemoryImportSettings = settingsQuery.data?.settings ?? {}
  const importPollInterval = useMemo(
    () => Math.max(200, Number(importSettings.poll_interval_ms ?? 1000)),
    [importSettings.poll_interval_ms]
  )

  // 导入任务列表：在面板激活期间轮询刷新任务进度。
  const tasksQuery = useQuery({
    queryKey: ['memory-import', 'tasks'],
    queryFn: () => getMemoryImportTasks(20),
    enabled: active,
    refetchInterval: active ? importPollInterval : false,
  })
  const importTasks = useMemo(() => tasksQuery.data?.items ?? [], [tasksQuery.data?.items])

  const invalidate = useCallback(() => {
    void tasksQuery.refetch()
  }, [tasksQuery])

  // 队列读失败：局部 importErrorText 呈现（沿用原 refreshImportQueue 的错误文案语义）
  const [importErrorText, setImportErrorText] = useState('')

  const [selectedImportTaskId, setSelectedImportTaskId] = useState('')
  const [selectedImportTask, setSelectedImportTask] = useState<MemoryImportTaskPayload | null>(null)
  const [selectedImportTaskLoading, setSelectedImportTaskLoading] = useState(false)
  const [selectedImportFileId, setSelectedImportFileId] = useState('')
  const [importChunkOffset, setImportChunkOffset] = useState(0)
  const [importChunksPayload, setImportChunksPayload] =
    useState<MemoryImportChunkListPayload | null>(null)
  const [importChunksLoading, setImportChunksLoading] = useState(false)

  // 同步记录用户选择，异步响应不能依赖发起请求时的 React 闭包。
  const selectionRef = useRef({ taskId: '', fileId: '', offset: 0 })
  const detailRequestRef = useRef(0)
  const chunksRequestRef = useRef(0)
  const refreshingQueueRef = useRef(0)
  const handledTasksUpdateRef = useRef(0)

  const changeTaskSelection = useCallback((taskId: string) => {
    if (selectionRef.current.taskId === taskId) return
    selectionRef.current = { taskId, fileId: '', offset: 0 }
    detailRequestRef.current += 1
    chunksRequestRef.current += 1
    setSelectedImportTaskId(taskId)
    setSelectedImportTask(null)
    setSelectedImportFileId('')
    setImportChunkOffset(0)
    setImportChunksPayload(null)
    setSelectedImportTaskLoading(false)
    setImportChunksLoading(false)
  }, [])

  useEffect(() => () => {
    detailRequestRef.current += 1
    chunksRequestRef.current += 1
  }, [])

  // 任务列表查询失败时同步到局部错误文案
  const tasksError = tasksQuery.error
  useEffect(() => {
    if (tasksError) {
      setImportErrorText(tasksError instanceof Error ? tasksError.message : '刷新导入任务失败')
    }
  }, [tasksError])

  /** 只写回当前任务、文件和分页的最新请求；旧响应也不能清除新请求的加载或错误状态。 */
  const loadImportChunks = useCallback(
    async (taskId: string, fileId: string, offset: number = 0, silent: boolean = false) => {
      const requestId = ++chunksRequestRef.current
      const isCurrent = () => requestId === chunksRequestRef.current
        && taskId === selectionRef.current.taskId
        && fileId === selectionRef.current.fileId
        && offset === selectionRef.current.offset
      if (!taskId || !fileId) {
        setImportChunksPayload(null)
        setImportChunksLoading(false)
        return
      }
      try {
        setImportChunksLoading(true)
        const payload = await getMemoryImportTaskChunks(
          taskId,
          fileId,
          offset,
          IMPORT_CHUNK_PAGE_SIZE
        )
        if (!isCurrent()) return
        if (!payload.success) {
          throw new Error(payload.error || '加载分块详情失败')
        }
        setImportChunksPayload(payload)
        setImportErrorText('')
      } catch (error) {
        if (!isCurrent()) return
        const message = error instanceof Error ? error.message : '加载分块详情失败'
        setImportChunksPayload(null)
        setImportErrorText(message)
        if (!silent) {
          toast({
            title: '加载分块详情失败',
            description: message,
            variant: 'destructive',
          })
        }
      } finally {
        if (isCurrent()) setImportChunksLoading(false)
      }
    },
    [toast]
  )

  /** 详情返回后按最新选择保留文件及偏移；当前请求失败时清空派生分块，并使旧分块响应失效。 */
  const loadImportTaskDetail = useCallback(
    async (taskId: string, silent: boolean = false) => {
      const requestId = ++detailRequestRef.current
      const isCurrent = () => requestId === detailRequestRef.current
        && taskId === selectionRef.current.taskId
      if (!taskId) {
        setSelectedImportTask(null)
        setSelectedImportFileId('')
        setImportChunksPayload(null)
        return
      }
      try {
        setSelectedImportTaskLoading(!silent)
        const payload = await getMemoryImportTask(taskId, false)
        if (!isCurrent()) return
        if (!payload.success || !payload.task) {
          throw new Error(payload.error || '任务不存在')
        }
        const task = payload.task
        setSelectedImportTask(task)
        setImportErrorText('')
        const files = Array.isArray(task.files) ? task.files : []
        const selection = selectionRef.current
        const keepCurrentFile = files.some((file) => file.file_id === selection.fileId)
        const nextFileId = keepCurrentFile ? selection.fileId : String(files[0]?.file_id ?? '')
        const nextOffset = keepCurrentFile ? selection.offset : 0
        selectionRef.current = { taskId, fileId: nextFileId, offset: nextOffset }
        if (!keepCurrentFile) {
          setImportChunkOffset(0)
        }
        setSelectedImportFileId(nextFileId)
        if (nextFileId) {
          await loadImportChunks(taskId, nextFileId, nextOffset, silent)
        } else {
          setImportChunksPayload(null)
        }
      } catch (error) {
        if (!isCurrent()) return
        selectionRef.current = { taskId, fileId: '', offset: 0 }
        chunksRequestRef.current += 1
        setImportChunksLoading(false)
        setImportChunkOffset(0)
        const message = error instanceof Error ? error.message : '加载导入任务详情失败'
        setSelectedImportTask(null)
        setSelectedImportFileId('')
        setImportChunksPayload(null)
        setImportErrorText(message)
        if (!silent) {
          toast({
            title: '加载导入任务详情失败',
            description: message,
            variant: 'destructive',
          })
        }
      } finally {
        if (isCurrent()) setSelectedImportTaskLoading(false)
      }
    },
    [loadImportChunks, toast]
  )

  /**
   * 刷新列表后选中指定任务或仍有效的最新选择，再加载一次详情。
   * 标记该次查询更新已处理，使轮询 effect 不重复加载同一详情；没有有效选择时回到首项。
   */
  const refreshQueue = useCallback(
    async (silent: boolean = false, preferredTaskId?: string) => {
      refreshingQueueRef.current += 1
      try {
        const result = await tasksQuery.refetch({ throwOnError: true })
        handledTasksUpdateRef.current = result.dataUpdatedAt
        const nextTasks = result.data?.items ?? []
        setImportErrorText('')
        const currentTaskId = preferredTaskId ?? selectionRef.current.taskId
        const nextTaskId = nextTasks.some((item) => item.task_id === currentTaskId)
          ? currentTaskId : nextTasks[0]?.task_id ?? ''
        changeTaskSelection(nextTaskId)
        if (nextTaskId) await loadImportTaskDetail(nextTaskId, silent)
      } catch (error) {
        const message = error instanceof Error ? error.message : '刷新导入任务失败'
        setImportErrorText(message)
        if (!silent) {
          toast({ title: '刷新导入任务失败', description: message, variant: 'destructive' })
        }
      } finally {
        refreshingQueueRef.current -= 1
      }
    },
    [changeTaskSelection, loadImportTaskDetail, tasksQuery, toast]
  )

  const refreshImportQueue = useCallback(
    (silent: boolean = false) => refreshQueue(silent), [refreshQueue]
  )

  const afterCreated = useCallback(
    (taskId: string) => refreshQueue(true, taskId || undefined), [refreshQueue]
  )

  const selectImportTask = useCallback(
    async (taskId: string) => {
      changeTaskSelection(taskId)
      selectionRef.current.offset = 0
      setImportChunkOffset(0)
      await loadImportTaskDetail(taskId)
    },
    [changeTaskSelection, loadImportTaskDetail]
  )

  const selectImportFile = useCallback(
    async (fileId: string) => {
      if (!selectedImportTaskId) {
        return
      }
      detailRequestRef.current += 1
      setSelectedImportTaskLoading(false)
      selectionRef.current = { taskId: selectedImportTaskId, fileId, offset: 0 }
      setImportChunksPayload(null)
      setSelectedImportFileId(fileId)
      setImportChunkOffset(0)
      await loadImportChunks(selectedImportTaskId, fileId, 0)
    },
    [loadImportChunks, selectedImportTaskId]
  )

  const moveImportChunkPage = useCallback(
    async (direction: -1 | 1) => {
      if (!selectedImportTaskId || !selectedImportFileId) {
        return
      }
      const nextOffset =
        direction < 0
          ? Math.max(0, importChunkOffset - IMPORT_CHUNK_PAGE_SIZE)
          : importChunkOffset + IMPORT_CHUNK_PAGE_SIZE
      if (nextOffset === importChunkOffset) {
        return
      }
      detailRequestRef.current += 1
      setSelectedImportTaskLoading(false)
      selectionRef.current.offset = nextOffset
      setImportChunksPayload(null)
      setImportChunkOffset(nextOffset)
      await loadImportChunks(selectedImportTaskId, selectedImportFileId, nextOffset)
    },
    [importChunkOffset, loadImportChunks, selectedImportFileId, selectedImportTaskId]
  )

  const cancelSelectedImportTask = useCallback(async () => {
    if (!selectedImportTaskId) {
      return
    }
    try {
      const payload = await cancelMemoryImportTask(selectedImportTaskId)
      if (!payload.success) {
        throw new Error(payload.error || '取消导入任务失败')
      }
      await refreshImportQueue(true)
      toast({
        title: '已请求取消任务',
        description: `任务 ${selectedImportTaskId.slice(0, 12)} 正在取消`,
      })
    } catch (error) {
      const message = error instanceof Error ? error.message : '取消导入任务失败'
      setImportErrorText(message)
      toast({
        title: '取消导入任务失败',
        description: message,
        variant: 'destructive',
      })
    }
  }, [refreshImportQueue, selectedImportTaskId, toast])

  const retrySelectedImportTask = useCallback(async () => {
    if (!selectedImportTaskId) {
      return
    }
    try {
      const payload = await retryMemoryImportTask(selectedImportTaskId, {
        overrides: buildRetryOverrides?.() ?? {},
      })
      if (!payload.success) {
        throw new Error(payload.error || '重试失败项失败')
      }
      const nextTaskId = String(payload.task?.task_id ?? '')
      await refreshQueue(true, nextTaskId || undefined)
      toast({
        title: '重试任务已创建',
        description: nextTaskId
          ? `重试任务 ${nextTaskId.slice(0, 12)} 已进入队列`
          : '失败项已提交重试',
      })
    } catch (error) {
      const message = error instanceof Error ? error.message : '重试失败项失败'
      setImportErrorText(message)
      toast({
        title: '重试失败项失败',
        description: message,
        variant: 'destructive',
      })
    }
  }, [buildRetryOverrides, refreshQueue, selectedImportTaskId, toast])

  // 轮询成功时刷新当前选择；手动刷新已经消费的查询更新不再重复加载。
  useEffect(() => {
    if (!active || refreshingQueueRef.current > 0 || !tasksQuery.dataUpdatedAt
      || handledTasksUpdateRef.current === tasksQuery.dataUpdatedAt) return
    handledTasksUpdateRef.current = tasksQuery.dataUpdatedAt
    const currentTaskId = selectionRef.current.taskId
    const nextTaskId = importTasks.some((task) => task.task_id === currentTaskId)
      ? currentTaskId : importTasks[0]?.task_id ?? ''
    changeTaskSelection(nextTaskId)
    if (nextTaskId) void loadImportTaskDetail(nextTaskId, nextTaskId === currentTaskId)
  }, [active, changeTaskSelection, importTasks, loadImportTaskDetail, tasksQuery.dataUpdatedAt])

  // 派生：任务分组、选中任务的衍生信息、分块分页
  const runningImportTasks = useMemo(
    () => importTasks.filter((task) => RUNNING_IMPORT_STATUS.has(String(task.status ?? '').trim())),
    [importTasks]
  )
  const queuedImportTasks = useMemo(
    () => importTasks.filter((task) => QUEUED_IMPORT_STATUS.has(String(task.status ?? '').trim())),
    [importTasks]
  )
  const recentImportTasks = useMemo(
    () =>
      importTasks.filter((task) => {
        const status = String(task.status ?? '').trim()
        return !RUNNING_IMPORT_STATUS.has(status) && !QUEUED_IMPORT_STATUS.has(status)
      }),
    [importTasks]
  )

  const selectedImportTaskSummary = useMemo(() => {
    if (!selectedImportTaskId) {
      return null
    }
    return importTasks.find((task) => task.task_id === selectedImportTaskId) ?? null
  }, [importTasks, selectedImportTaskId])

  const selectedImportFiles = useMemo(() => {
    return Array.isArray(selectedImportTask?.files) ? selectedImportTask.files : []
  }, [selectedImportTask?.files])

  const selectedImportChunks = useMemo(() => {
    return Array.isArray(importChunksPayload?.items) ? importChunksPayload.items : []
  }, [importChunksPayload?.items])

  const selectedImportTaskResolved = selectedImportTask ?? selectedImportTaskSummary
  const selectedImportTaskErrorText = String(selectedImportTaskResolved?.error ?? '').trim()
  const selectedImportRetrySummary = selectedImportTaskResolved?.retry_summary

  const importChunkTotal = Number(importChunksPayload?.total ?? 0)
  const canImportChunkPrev = importChunkOffset > 0
  const canImportChunkNext = importChunkOffset + IMPORT_CHUNK_PAGE_SIZE < importChunkTotal

  return {
    refreshImportQueue,
    runningImportTasks,
    queuedImportTasks,
    recentImportTasks,
    selectedImportTaskId,
    selectImportTask,
    importPollInterval,
    importErrorText,
    cancelSelectedImportTask,
    retrySelectedImportTask,
    selectedImportTaskLoading,
    selectedImportTaskResolved,
    selectedImportRetrySummary,
    selectedImportTaskErrorText,
    selectedImportFiles,
    selectedImportFileId,
    selectImportFile,
    importChunkTotal,
    importChunkOffset,
    moveImportChunkPage,
    canImportChunkPrev,
    canImportChunkNext,
    importChunksLoading,
    selectedImportChunks,
    afterCreated,
    invalidate,
  }
}
