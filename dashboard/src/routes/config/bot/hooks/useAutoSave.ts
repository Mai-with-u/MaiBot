import { useEffect, useRef, useCallback } from 'react'
import { updateBotConfigSection } from '@/lib/config-api'
import type { ConfigSectionName } from '../types'

export interface UseAutoSaveOptions {
  /** 防抖延迟时间（毫秒），默认 2000ms */
  debounceMs?: number
  /** 保存成功回调 */
  onSaveSuccess?: () => void
  /** 保存失败回调 */
  onSaveError?: (error: Error) => void
}

export interface UseAutoSaveReturn {
  /** 触发自动保存 */
  triggerAutoSave: (sectionName: ConfigSectionName, sectionData: unknown) => void
  /** 立即保存（不防抖） */
  saveNow: (sectionName: ConfigSectionName, sectionData: unknown) => Promise<void>
  /** 取消待处理的自动保存 */
  cancelPendingAutoSave: () => void
}

export interface AutoSaveState {
  /** 是否正在保存中 */
  isAutoSaving: boolean
  /** 是否有未保存的更改 */
  hasUnsavedChanges: boolean
}

/**
 * 自动保存 hook
 * 
 * 用于监听配置变化并自动防抖保存到后端
 * 
 * @example
 * ```tsx
 * const { triggerAutoSave } = useAutoSave({
 *   isInitialLoad,
 *   setAutoSaving,
 *   setHasUnsavedChanges,
 * })
 * 
 * // 配置变化时触发
 * useEffect(() => {
 *   if (config) triggerAutoSave('bot', config)
 * }, [config])
 * ```
 */
export function useAutoSave(
  isInitialLoad: boolean,
  setAutoSaving: (saving: boolean) => void,
  setHasUnsavedChanges: (hasChanges: boolean) => void,
  options: UseAutoSaveOptions = {}
): UseAutoSaveReturn {
  const { debounceMs = 2000, onSaveSuccess, onSaveError } = options
  const autoSaveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // 执行保存操作
  const saveSection = useCallback(
    async (sectionName: ConfigSectionName, sectionData: unknown) => {
      try {
        setAutoSaving(true)
        await updateBotConfigSection(sectionName, sectionData)
        setHasUnsavedChanges(false)
        onSaveSuccess?.()
      } catch (error) {
        console.error(`自动保存 ${sectionName} 失败:`, error)
        setHasUnsavedChanges(true)
        onSaveError?.(error instanceof Error ? error : new Error(String(error)))
      } finally {
        setAutoSaving(false)
      }
    },
    [setAutoSaving, setHasUnsavedChanges, onSaveSuccess, onSaveError]
  )

  // 触发自动保存（带防抖）
  const triggerAutoSave = useCallback(
    (sectionName: ConfigSectionName, sectionData: unknown) => {
      if (isInitialLoad) return

      setHasUnsavedChanges(true)

      if (autoSaveTimerRef.current) {
        clearTimeout(autoSaveTimerRef.current)
      }

      autoSaveTimerRef.current = setTimeout(() => {
        saveSection(sectionName, sectionData)
      }, debounceMs)
    },
    [isInitialLoad, setHasUnsavedChanges, saveSection, debounceMs]
  )

  // 立即保存（不防抖）
  const saveNow = useCallback(
    async (sectionName: ConfigSectionName, sectionData: unknown) => {
      if (autoSaveTimerRef.current) {
        clearTimeout(autoSaveTimerRef.current)
        autoSaveTimerRef.current = null
      }
      await saveSection(sectionName, sectionData)
    },
    [saveSection]
  )

  // 取消待处理的自动保存
  const cancelPendingAutoSave = useCallback(() => {
    if (autoSaveTimerRef.current) {
      clearTimeout(autoSaveTimerRef.current)
      autoSaveTimerRef.current = null
    }
  }, [])

  // 组件卸载时清理定时器
  useEffect(() => {
    return () => {
      if (autoSaveTimerRef.current) {
        clearTimeout(autoSaveTimerRef.current)
      }
    }
  }, [])

  return {
    triggerAutoSave,
    saveNow,
    cancelPendingAutoSave,
  }
}

/**
 * 创建配置自动保存 effect
 * 
 * 这是一个工厂函数，用于创建监听特定配置变化并触发自动保存的 effect
 * 简化重复的 useEffect 代码
 * 
 * @example
 * ```tsx
 * // 使用方式 1: 直接在组件中调用
 * useConfigAutoSave(botConfig, 'bot', isInitialLoad, triggerAutoSave)
 * useConfigAutoSave(chatConfig, 'chat', isInitialLoad, triggerAutoSave)
 * 
 * // 使用方式 2: 批量配置
 * const configs = [
 *   { config: botConfig, section: 'bot' },
 *   { config: chatConfig, section: 'chat' },
 * ] as const
 * 
 * configs.forEach(({ config, section }) => {
 *   useConfigAutoSave(config, section, isInitialLoad, triggerAutoSave)
 * })
 * ```
 */
export function useConfigAutoSave<T>(
  config: T | null,
  sectionName: ConfigSectionName,
  isInitialLoad: boolean,
  triggerAutoSave: (sectionName: ConfigSectionName, data: unknown) => void
): void {
  useEffect(() => {
    if (config && !isInitialLoad) {
      triggerAutoSave(sectionName, config)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [config])
}
