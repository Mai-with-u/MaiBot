/**
 * Model 配置页面自动保存 Hook
 * 监听 models 和 taskConfig 变化，自动保存到服务器
 */
import { useRef, useEffect, useCallback } from 'react'
import { updateModelConfigSection } from '@/lib/config-api'
import type { ModelInfo, ModelTaskConfig } from '../types'

interface UseModelAutoSaveOptions {
  /** 模型列表 */
  models: ModelInfo[]
  /** 任务配置 */
  taskConfig: ModelTaskConfig | null
  /** 防抖延迟时间 (ms) */
  debounceMs?: number
  /** 保存状态回调 */
  onSavingChange?: (saving: boolean) => void
  /** 未保存变更回调 */
  onUnsavedChange?: (hasUnsaved: boolean) => void
}

interface UseModelAutoSaveReturn {
  /** 清除所有待执行的保存定时器 */
  clearTimers: () => void
  /** 初始加载状态标记引用 (用于设置初始加载完成) */
  initialLoadRef: React.MutableRefObject<boolean>
}

/**
 * 模型配置自动保存 Hook
 */
export function useModelAutoSave(
  options: UseModelAutoSaveOptions
): UseModelAutoSaveReturn {
  const {
    models,
    taskConfig,
    debounceMs = 2000,
    onSavingChange,
    onUnsavedChange,
  } = options

  // 防抖定时器
  const modelsTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const taskConfigTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const initialLoadRef = useRef(true)

  // 清除定时器
  const clearTimers = useCallback(() => {
    if (modelsTimerRef.current) {
      clearTimeout(modelsTimerRef.current)
      modelsTimerRef.current = null
    }
    if (taskConfigTimerRef.current) {
      clearTimeout(taskConfigTimerRef.current)
      taskConfigTimerRef.current = null
    }
  }, [])

  // 清理模型中的 null 值（TOML 不支持 null）
  const cleanModelForSave = useCallback((model: ModelInfo): ModelInfo => {
    const cleaned: ModelInfo = {
      model_identifier: model.model_identifier,
      name: model.name,
      api_provider: model.api_provider,
      price_in: model.price_in ?? 0,
      price_out: model.price_out ?? 0,
      force_stream_mode: model.force_stream_mode ?? false,
      extra_params: model.extra_params ?? {},
    }
    // 只有在有值时才添加可选字段
    if (model.temperature != null) {
      cleaned.temperature = model.temperature
    }
    if (model.max_tokens != null) {
      cleaned.max_tokens = model.max_tokens
    }
    return cleaned
  }, [])

  // 自动保存模型列表
  const autoSaveModels = useCallback(async (newModels: ModelInfo[]) => {
    try {
      onSavingChange?.(true)
      // 清理每个模型中的 null 值
      const cleanedModels = newModels.map(cleanModelForSave)
      await updateModelConfigSection('models', cleanedModels)
      onUnsavedChange?.(false)
    } catch (error) {
      console.error('自动保存模型列表失败:', error)
      onUnsavedChange?.(true)
    } finally {
      onSavingChange?.(false)
    }
  }, [onSavingChange, onUnsavedChange, cleanModelForSave])

  // 自动保存任务配置
  const autoSaveTaskConfig = useCallback(async (newTaskConfig: ModelTaskConfig) => {
    try {
      onSavingChange?.(true)
      await updateModelConfigSection('model_task_config', newTaskConfig)
      onUnsavedChange?.(false)
    } catch (error) {
      console.error('自动保存任务配置失败:', error)
      onUnsavedChange?.(true)
    } finally {
      onSavingChange?.(false)
    }
  }, [onSavingChange, onUnsavedChange])

  // 监听 models 变化
  useEffect(() => {
    if (initialLoadRef.current) return

    onUnsavedChange?.(true)

    if (modelsTimerRef.current) {
      clearTimeout(modelsTimerRef.current)
    }

    modelsTimerRef.current = setTimeout(() => {
      autoSaveModels(models)
    }, debounceMs)

    return () => {
      if (modelsTimerRef.current) {
        clearTimeout(modelsTimerRef.current)
      }
    }
  }, [models, autoSaveModels, debounceMs, onUnsavedChange])

  // 监听 taskConfig 变化
  useEffect(() => {
    if (initialLoadRef.current || !taskConfig) return

    onUnsavedChange?.(true)

    if (taskConfigTimerRef.current) {
      clearTimeout(taskConfigTimerRef.current)
    }

    taskConfigTimerRef.current = setTimeout(() => {
      autoSaveTaskConfig(taskConfig)
    }, debounceMs)

    return () => {
      if (taskConfigTimerRef.current) {
        clearTimeout(taskConfigTimerRef.current)
      }
    }
  }, [taskConfig, autoSaveTaskConfig, debounceMs, onUnsavedChange])

  // 组件卸载时清除定时器
  useEffect(() => {
    return () => {
      clearTimers()
    }
  }, [clearTimers])

  return {
    clearTimers,
    initialLoadRef,
  }
}
