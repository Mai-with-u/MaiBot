/**
 * useImportQueue —— 长期记忆「导入队列」领域 hook（页面逻辑下沉的样板切片）。
 *
 * 收编导入任务队列相关的服务端状态与交互：
 * - 任务列表（tasks）与导入设置（settings）走 useQuery，仅在导入面板激活时拉取（enabled: active）；
 * - 轮询 + WebSocket 融合：WS 推送优先（订阅 import_progress → invalidate），WS 未连接时由轮询兜底
 *   （refetchInterval = active && !wsConnected ? importPollInterval : false）；
 * - 选中任务详情与分块分页仍以本地 state + 命令式加载维持（依赖用户选择，不适合纯查询缓存）；
 * - 队列读失败用 importErrorText 局部呈现，取消/重试写失败仍走全局 toast。
 *
 * 与 useImportForm 共享 settings 查询（同 queryKey 由 React Query 去重），表单侧负责默认值 seed 与别名联动。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { useQuery } from '@tanstack/react-query'

import { useToast } from '@/hooks/use-toast'
import { memoryProgressClient, type MemoryProgressEvent } from '@/lib/memory-progress-client'
import { unifiedWsClient } from '@/lib/unified-ws'
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
  /** 导入面板是否激活；非激活时不拉取、不轮询、不订阅 */
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
  importAutoPolling: boolean
  setImportAutoPolling: React.Dispatch<React.SetStateAction<boolean>>
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
  /** 失效导入任务查询（WS 推送或外部变更后触发重新拉取） */
  invalidate: () => void
}

export function useImportQueue({ active, buildRetryOverrides }: UseImportQueueOptions): UseImportQueueResult {
  const { toast } = useToast()

  // WS 连接状态：连接时关闭轮询，断开时由轮询兜底
  const [wsConnected, setWsConnected] = useState(false)
  useEffect(() => {
    const unsubscribe = unifiedWsClient.onConnectionChange((connected) => {
      setWsConnected(connected)
    })
    return unsubscribe
  }, [])

  // 导入设置：仅用于派生轮询间隔；与 useImportForm 共享同一查询缓存
  const settingsQuery = useQuery({
    queryKey: ['memory-import', 'settings'],
    queryFn: () => getMemoryImportSettings(),
    enabled: active,
  })
  const importSettings: MemoryImportSettings = settingsQuery.data?.settings ?? {}
  const importPollInterval = useMemo(
    () => Math.max(200, Number(importSettings.poll_interval_ms ?? 1000)),
    [importSettings.poll_interval_ms],
  )

  const [importAutoPolling, setImportAutoPolling] = useState(true)

  // 导入任务列表：导入面板激活时拉取；WS 未连接且开启自动轮询时由 refetchInterval 兜底
  const tasksQuery = useQuery({
    queryKey: ['memory-import', 'tasks'],
    queryFn: () => getMemoryImportTasks(20),
    enabled: active,
    refetchInterval: active && importAutoPolling && !wsConnected ? importPollInterval : false,
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
  const [importChunksPayload, setImportChunksPayload] = useState<MemoryImportChunkListPayload | null>(null)
  const [importChunksLoading, setImportChunksLoading] = useState(false)

  // 任务列表查询失败时同步到局部错误文案
  const tasksError = tasksQuery.error
  useEffect(() => {
    if (tasksError) {
      setImportErrorText(tasksError instanceof Error ? tasksError.message : '刷新导入任务失败')
    }
  }, [tasksError])

  const loadImportChunks = useCallback(
    async (taskId: string, fileId: string, offset: number = 0, silent: boolean = false) => {
      if (!taskId || !fileId) {
        setImportChunksPayload(null)
        return
      }
      try {
        setImportChunksLoading(true)
        const payload = await getMemoryImportTaskChunks(taskId, fileId, offset, IMPORT_CHUNK_PAGE_SIZE)
        if (!payload.success) {
          throw new Error(payload.error || '加载分块详情失败')
        }
        setImportChunksPayload(payload)
        setImportErrorText('')
      } catch (error) {
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
        setImportChunksLoading(false)
      }
    },
    [toast],
  )

  const loadImportTaskDetail = useCallback(
    async (taskId: string, silent: boolean = false) => {
      if (!taskId) {
        setSelectedImportTask(null)
        setSelectedImportFileId('')
        setImportChunksPayload(null)
        return
      }
      try {
        if (!silent) {
          setSelectedImportTaskLoading(true)
        }
        const payload = await getMemoryImportTask(taskId, false)
        if (!payload.success || !payload.task) {
          throw new Error(payload.error || '任务不存在')
        }
        const task = payload.task
        setSelectedImportTask(task)
        setImportErrorText('')
        const files = Array.isArray(task.files) ? task.files : []
        const keepCurrentFile = files.some((file) => file.file_id === selectedImportFileId)
        const nextFileId = keepCurrentFile ? selectedImportFileId : String(files[0]?.file_id ?? '')
        const nextOffset = keepCurrentFile ? importChunkOffset : 0
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
        if (!silent) {
          setSelectedImportTaskLoading(false)
        }
      }
    },
    [importChunkOffset, loadImportChunks, selectedImportFileId, toast],
  )

  // 命令式刷新队列：触发任务列表查询重拉，并维护选中任务（沿用原 refreshImportQueue 语义）
  const refreshImportQueue = useCallback(
    async (silent: boolean = false) => {
      try {
        const result = await tasksQuery.refetch({ throwOnError: true })
        const nextTasks = result.data?.items ?? []
        setImportErrorText('')

        if (nextTasks.length <= 0) {
          setSelectedImportTaskId('')
          setSelectedImportTask(null)
          setSelectedImportFileId('')
          setImportChunksPayload(null)
          return
        }

        setSelectedImportTaskId((currentTaskId) => {
          if (!currentTaskId || !nextTasks.some((item) => item.task_id === currentTaskId)) {
            return nextTasks[0].task_id
          }
          return currentTaskId
        })
      } catch (error) {
        const message = error instanceof Error ? error.message : '刷新导入任务失败'
        setImportErrorText(message)
        if (!silent) {
          toast({
            title: '刷新导入任务失败',
            description: message,
            variant: 'destructive',
          })
        }
      }
    },
    [tasksQuery, toast],
  )

  const afterCreated = useCallback(
    async (taskId: string) => {
      await refreshImportQueue(true)
      if (taskId) {
        setSelectedImportTaskId(taskId)
        await loadImportTaskDetail(taskId, true)
      }
    },
    [loadImportTaskDetail, refreshImportQueue],
  )

  const selectImportTask = useCallback(
    async (taskId: string) => {
      setSelectedImportTaskId(taskId)
      setImportChunkOffset(0)
      await loadImportTaskDetail(taskId)
    },
    [loadImportTaskDetail],
  )

  const selectImportFile = useCallback(
    async (fileId: string) => {
      if (!selectedImportTaskId) {
        return
      }
      setSelectedImportFileId(fileId)
      setImportChunkOffset(0)
      await loadImportChunks(selectedImportTaskId, fileId, 0)
    },
    [loadImportChunks, selectedImportTaskId],
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
      setImportChunkOffset(nextOffset)
      await loadImportChunks(selectedImportTaskId, selectedImportFileId, nextOffset)
    },
    [importChunkOffset, loadImportChunks, selectedImportFileId, selectedImportTaskId],
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
      await loadImportTaskDetail(selectedImportTaskId, true)
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
  }, [loadImportTaskDetail, refreshImportQueue, selectedImportTaskId, toast])

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
      await refreshImportQueue(true)
      if (nextTaskId) {
        setSelectedImportTaskId(nextTaskId)
        await loadImportTaskDetail(nextTaskId, true)
      } else {
        await loadImportTaskDetail(selectedImportTaskId, true)
      }
      toast({
        title: '重试任务已创建',
        description: nextTaskId ? `重试任务 ${nextTaskId.slice(0, 12)} 已进入队列` : '失败项已提交重试',
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
  }, [buildRetryOverrides, loadImportTaskDetail, refreshImportQueue, selectedImportTaskId, toast])

  // 任务加载后若未选中任务则自动选第一个；选中任务被删除则切到第一个（沿用原 effect 6-7 语义）
  useEffect(() => {
    if (!active) {
      return
    }
    if (!selectedImportTaskId && importTasks.length > 0) {
      void selectImportTask(importTasks[0].task_id)
    }
  }, [active, importTasks, selectImportTask, selectedImportTaskId])

  useEffect(() => {
    if (!active) {
      return
    }
    if (!selectedImportTaskId) {
      setSelectedImportTask(null)
      setSelectedImportFileId('')
      setImportChunksPayload(null)
      return
    }
    if (!importTasks.some((task) => task.task_id === selectedImportTaskId) && importTasks.length > 0) {
      void selectImportTask(importTasks[0].task_id)
      return
    }
    void loadImportTaskDetail(selectedImportTaskId, true)
  }, [active, importTasks, loadImportTaskDetail, selectImportTask, selectedImportTaskId])

  // 统一 WebSocket 推送：作为轮询的实时增强；后端未广播时由轮询兜底
  const selectedImportTaskIdRef = useRef<string>('')
  useEffect(() => {
    selectedImportTaskIdRef.current = selectedImportTaskId
  }, [selectedImportTaskId])

  useEffect(() => {
    if (!active) {
      return
    }
    let cancelled = false
    let unsubscribe: (() => Promise<void>) | undefined
    const handleEvent = (event: MemoryProgressEvent) => {
      if (event.topic === 'import_progress') {
        invalidate()
        if (selectedImportTaskIdRef.current) {
          void loadImportTaskDetail(selectedImportTaskIdRef.current, true)
        }
      }
    }
    void memoryProgressClient
      .subscribe(handleEvent, ['import_progress'])
      .then((cleanup) => {
        if (cancelled) {
          void cleanup()
          return
        }
        unsubscribe = cleanup
      })
      .catch((error) => {
        // 订阅失败不影响轮询兜底
        console.warn('订阅长期记忆 WebSocket 失败，已退化到轮询兜底', error)
      })
    return () => {
      cancelled = true
      if (unsubscribe) {
        void unsubscribe()
      }
    }
  }, [active, invalidate, loadImportTaskDetail])

  // 派生：任务分组、选中任务的衍生信息、分块分页
  const runningImportTasks = useMemo(
    () => importTasks.filter((task) => RUNNING_IMPORT_STATUS.has(String(task.status ?? '').trim())),
    [importTasks],
  )
  const queuedImportTasks = useMemo(
    () => importTasks.filter((task) => QUEUED_IMPORT_STATUS.has(String(task.status ?? '').trim())),
    [importTasks],
  )
  const recentImportTasks = useMemo(
    () =>
      importTasks.filter((task) => {
        const status = String(task.status ?? '').trim()
        return !RUNNING_IMPORT_STATUS.has(status) && !QUEUED_IMPORT_STATUS.has(status)
      }),
    [importTasks],
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
    importAutoPolling,
    setImportAutoPolling,
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
