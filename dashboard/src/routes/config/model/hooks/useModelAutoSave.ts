/**
 * 模型配置页自动保存 Hook。
 * 监听 models 和 taskConfig 变化，自动保存到服务器。
 */
import type { RefObject } from 'react'
import { useCallback, useEffect, useRef } from 'react'

import { updateModelConfigSection } from '@/lib/config-api'

import type { ModelInfo, ModelTaskConfig } from '../types'

type EnqueueConfigWrite = (operation: () => Promise<void>) => Promise<void>

interface UseModelAutoSaveOptions {
  /** 模型列表 */
  models: ModelInfo[]
  /** 任务配置 */
  taskConfig: ModelTaskConfig | null
  /** 所有模型配置写入共用的串行队列 */
  enqueueWrite: EnqueueConfigWrite
  /** 防抖延迟时间 (ms) */
  debounceMs?: number
  /** 保存状态回调 */
  onSavingChange?: (saving: boolean) => void
  /** 未保存变更回调 */
  onUnsavedChange?: (hasUnsaved: boolean) => void
}

export interface ModelSaveBarrierCheckpoint {
  modelsGeneration: number
  modelsSourceSnapshot: string
  modelsTargetSnapshot: string
  taskConfigGeneration: number
  taskConfigSourceSnapshot: string | null
  taskConfigTargetSnapshot: string | null
}

export interface ModelSaveBarrierCommit {
  applyModels: boolean
  applyTaskConfig: boolean
}

interface UseModelAutoSaveReturn {
  /** 取消尚未入队的模型与任务配置保存 */
  cancelPendingTimers: () => void
  /** 在整份配置写入入队前，同步记录它要保存的快照 */
  prepareSaveBarrier: (
    nextModels: ModelInfo[],
    nextTaskConfig: ModelTaskConfig | null
  ) => ModelSaveBarrierCheckpoint
  /** 整份配置写入成功后提交快照，并判断目标状态是否仍可安全回填到界面 */
  commitSaveBarrier: (checkpoint: ModelSaveBarrierCheckpoint) => ModelSaveBarrierCommit
  /** 初始加载状态标记引用（用于设置初始加载完成） */
  initialLoadRef: RefObject<boolean>
  resetSnapshots: (nextModels: ModelInfo[], nextTaskConfig: ModelTaskConfig | null) => void
}

type SaveDomain = 'models' | 'taskConfig'

interface DirtyDomains {
  models: boolean
  taskConfig: boolean
}

interface DomainGenerations {
  models: number
  taskConfig: number
}

/**
 * 模型配置自动保存 Hook。
 */
export function useModelAutoSave(options: UseModelAutoSaveOptions): UseModelAutoSaveReturn {
  const {
    models,
    taskConfig,
    enqueueWrite,
    debounceMs = 2000,
    onSavingChange,
    onUnsavedChange,
  } = options

  const modelsTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const taskConfigTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const initialLoadRef = useRef(true)
  const modelsSnapshotRef = useRef<string | null>(null)
  const taskConfigSnapshotRef = useRef<string | null>(null)
  const latestModelsSnapshotRef = useRef('')
  const latestTaskConfigSnapshotRef = useRef<string | null>(null)
  const dirtyDomainsRef = useRef<DirtyDomains>({ models: false, taskConfig: false })
  const generationsRef = useRef<DomainGenerations>({ models: 0, taskConfig: 0 })
  const pendingModelsSaveCountRef = useRef(0)
  const pendingTaskConfigSaveCountRef = useRef(0)
  const activeSaveCountRef = useRef(0)
  const isMountedRef = useRef(true)

  const publishUnsavedState = useCallback(() => {
    if (!isMountedRef.current) return
    onUnsavedChange?.(dirtyDomainsRef.current.models || dirtyDomainsRef.current.taskConfig)
  }, [onUnsavedChange])

  const setDomainDirty = useCallback(
    (domain: SaveDomain, dirty: boolean) => {
      dirtyDomainsRef.current[domain] = dirty
      publishUnsavedState()
    },
    [publishUnsavedState]
  )

  const updateSavingCount = useCallback(
    (delta: number) => {
      activeSaveCountRef.current += delta
      if (isMountedRef.current) {
        onSavingChange?.(activeSaveCountRef.current > 0)
      }
    },
    [onSavingChange]
  )

  const cancelPendingTimers = useCallback(() => {
    if (modelsTimerRef.current) {
      clearTimeout(modelsTimerRef.current)
      modelsTimerRef.current = null
    }
    if (taskConfigTimerRef.current) {
      clearTimeout(taskConfigTimerRef.current)
      taskConfigTimerRef.current = null
    }
  }, [])

  // 清理模型中的 null 值（TOML 不支持 null）。
  const cleanModelForSave = useCallback((model: ModelInfo): ModelInfo => {
    const cleaned: ModelInfo = {
      model_identifier: model.model_identifier,
      name: model.name,
      api_provider: model.api_provider,
      price_in: model.price_in ?? 0,
      price_out: model.price_out ?? 0,
      cache: model.cache ?? false,
      cache_price_in: model.cache_price_in ?? 0,
      visual: model.visual ?? false,
      force_stream_mode: model.force_stream_mode ?? false,
      prefill: model.prefill ?? true,
      extra_params: model.extra_params ?? {},
    }
    // 只有在有值时才添加可选字段。
    if (model.temperature != null) {
      cleaned.temperature = model.temperature
    }
    if (model.max_tokens != null) {
      cleaned.max_tokens = model.max_tokens
    }
    return cleaned
  }, [])

  const snapshotModels = useCallback(
    (nextModels: ModelInfo[]): string => JSON.stringify(nextModels.map(cleanModelForSave)),
    [cleanModelForSave]
  )

  const snapshotTaskConfig = useCallback(
    (nextTaskConfig: ModelTaskConfig | null): string | null =>
      nextTaskConfig ? JSON.stringify(nextTaskConfig) : null,
    []
  )

  // 这些 ref 每次渲染都指向最新草稿，供异步屏障完成时判断期间是否又发生编辑。
  latestModelsSnapshotRef.current = snapshotModels(models)
  latestTaskConfigSnapshotRef.current = snapshotTaskConfig(taskConfig)

  const resetSnapshots = useCallback(
    (nextModels: ModelInfo[], nextTaskConfig: ModelTaskConfig | null) => {
      const modelsSnapshot = snapshotModels(nextModels)
      const taskSnapshot = snapshotTaskConfig(nextTaskConfig)
      modelsSnapshotRef.current = modelsSnapshot
      taskConfigSnapshotRef.current = taskSnapshot
      latestModelsSnapshotRef.current = modelsSnapshot
      latestTaskConfigSnapshotRef.current = taskSnapshot
      generationsRef.current.models += 1
      generationsRef.current.taskConfig += 1
      dirtyDomainsRef.current = { models: false, taskConfig: false }
      publishUnsavedState()
    },
    [publishUnsavedState, snapshotModels, snapshotTaskConfig]
  )

  const prepareSaveBarrier = useCallback(
    (
      nextModels: ModelInfo[],
      nextTaskConfig: ModelTaskConfig | null
    ): ModelSaveBarrierCheckpoint => {
      // 只取消屏障之前尚未入队的保存；屏障之后的新编辑会创建新定时器。
      cancelPendingTimers()
      return {
        modelsGeneration: generationsRef.current.models,
        modelsSourceSnapshot: latestModelsSnapshotRef.current,
        modelsTargetSnapshot: snapshotModels(nextModels),
        taskConfigGeneration: generationsRef.current.taskConfig,
        taskConfigSourceSnapshot: latestTaskConfigSnapshotRef.current,
        taskConfigTargetSnapshot: snapshotTaskConfig(nextTaskConfig),
      }
    },
    [cancelPendingTimers, snapshotModels, snapshotTaskConfig]
  )

  const commitSaveBarrier = useCallback(
    (checkpoint: ModelSaveBarrierCheckpoint): ModelSaveBarrierCommit => {
      modelsSnapshotRef.current = checkpoint.modelsTargetSnapshot
      taskConfigSnapshotRef.current = checkpoint.taskConfigTargetSnapshot

      const applyModels =
        checkpoint.modelsGeneration === generationsRef.current.models &&
        checkpoint.modelsSourceSnapshot === latestModelsSnapshotRef.current
      const applyTaskConfig =
        checkpoint.taskConfigGeneration === generationsRef.current.taskConfig &&
        checkpoint.taskConfigSourceSnapshot === latestTaskConfigSnapshotRef.current

      if (applyModels) {
        latestModelsSnapshotRef.current = checkpoint.modelsTargetSnapshot
      }
      if (applyTaskConfig) {
        latestTaskConfigSnapshotRef.current = checkpoint.taskConfigTargetSnapshot
      }

      dirtyDomainsRef.current.models =
        latestModelsSnapshotRef.current !== checkpoint.modelsTargetSnapshot
      dirtyDomainsRef.current.taskConfig =
        latestTaskConfigSnapshotRef.current !== checkpoint.taskConfigTargetSnapshot
      publishUnsavedState()

      return { applyModels, applyTaskConfig }
    },
    [publishUnsavedState]
  )

  const queueModelsSave = useCallback(
    (newModels: ModelInfo[], snapshot: string, generation: number): Promise<void> => {
      pendingModelsSaveCountRef.current += 1
      updateSavingCount(1)

      return enqueueWrite(async () => {
        try {
          const cleanedModels = newModels.map(cleanModelForSave)
          await updateModelConfigSection('models', cleanedModels)
          if (
            generation === generationsRef.current.models &&
            snapshot === latestModelsSnapshotRef.current
          ) {
            modelsSnapshotRef.current = snapshot
            setDomainDirty('models', false)
          }
        } catch (error) {
          console.error('自动保存模型列表失败:', error)
          if (generation === generationsRef.current.models) {
            setDomainDirty('models', true)
          }
        } finally {
          pendingModelsSaveCountRef.current -= 1
          updateSavingCount(-1)
        }
      })
    },
    [cleanModelForSave, enqueueWrite, setDomainDirty, updateSavingCount]
  )

  const queueTaskConfigSave = useCallback(
    (newTaskConfig: ModelTaskConfig, snapshot: string, generation: number): Promise<void> => {
      pendingTaskConfigSaveCountRef.current += 1
      updateSavingCount(1)

      return enqueueWrite(async () => {
        try {
          await updateModelConfigSection('model_task_config', newTaskConfig)
          if (
            generation === generationsRef.current.taskConfig &&
            snapshot === latestTaskConfigSnapshotRef.current
          ) {
            taskConfigSnapshotRef.current = snapshot
            setDomainDirty('taskConfig', false)
          }
        } catch (error) {
          console.error('自动保存任务配置失败:', error)
          if (generation === generationsRef.current.taskConfig) {
            setDomainDirty('taskConfig', true)
          }
        } finally {
          pendingTaskConfigSaveCountRef.current -= 1
          updateSavingCount(-1)
        }
      })
    },
    [enqueueWrite, setDomainDirty, updateSavingCount]
  )

  // 监听 models 变化。
  useEffect(() => {
    if (initialLoadRef.current) return

    const snapshot = snapshotModels(models)
    if (modelsSnapshotRef.current === null) {
      modelsSnapshotRef.current = snapshot
      return
    }

    generationsRef.current.models += 1
    const generation = generationsRef.current.models
    // 若旧修订已入队，即使用户回退到基线也要补写一次，覆盖旧请求即将落盘的内容。
    const dirty = snapshot !== modelsSnapshotRef.current || pendingModelsSaveCountRef.current > 0
    setDomainDirty('models', dirty)
    if (!dirty) return

    modelsTimerRef.current = setTimeout(() => {
      modelsTimerRef.current = null
      void queueModelsSave(models, snapshot, generation)
    }, debounceMs)

    return () => {
      if (modelsTimerRef.current) {
        clearTimeout(modelsTimerRef.current)
        modelsTimerRef.current = null
      }
    }
  }, [models, debounceMs, queueModelsSave, setDomainDirty, snapshotModels])

  // 监听 taskConfig 变化。
  useEffect(() => {
    if (initialLoadRef.current || !taskConfig) return

    const snapshot = snapshotTaskConfig(taskConfig)
    if (taskConfigSnapshotRef.current === null) {
      taskConfigSnapshotRef.current = snapshot
      return
    }

    generationsRef.current.taskConfig += 1
    const generation = generationsRef.current.taskConfig
    const dirty =
      snapshot !== taskConfigSnapshotRef.current || pendingTaskConfigSaveCountRef.current > 0
    setDomainDirty('taskConfig', dirty)
    if (!dirty || snapshot === null) return

    taskConfigTimerRef.current = setTimeout(() => {
      taskConfigTimerRef.current = null
      void queueTaskConfigSave(taskConfig, snapshot, generation)
    }, debounceMs)

    return () => {
      if (taskConfigTimerRef.current) {
        clearTimeout(taskConfigTimerRef.current)
        taskConfigTimerRef.current = null
      }
    }
  }, [taskConfig, debounceMs, queueTaskConfigSave, setDomainDirty, snapshotTaskConfig])

  useEffect(() => {
    isMountedRef.current = true
    return () => {
      isMountedRef.current = false
      cancelPendingTimers()
    }
  }, [cancelPendingTimers])

  return {
    cancelPendingTimers,
    commitSaveBarrier,
    initialLoadRef,
    prepareSaveBarrier,
    resetSnapshots,
  }
}
