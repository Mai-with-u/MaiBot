import { useCallback, useEffect, useRef, useState } from 'react'

import { updateBotConfigSection } from '@/lib/config-api'
import type { ConfigSectionName } from '../types'

/**
 * Self-contained auto-save hook configuration
 * @template T The type of data being saved
 */
export interface UseAutoSaveConfig<T> {
  /** Function to save data, should return a promise */
  saveFn: (data: T) => Promise<void>
  /** Debounce delay in milliseconds, default 2000ms */
  debounceMs?: number
  /** Callback when save succeeds */
  onSaveSuccess?: () => void
  /** Callback when save fails */
  onSaveError?: (error: Error) => void
}

/**
 * Self-contained auto-save hook return type (generic)
 */
export interface UseAutoSaveReturnGeneric<T> {
  /** Trigger auto-save (debounced) */
  save: (data: T) => void
  /** Save immediately without debounce */
  saveNow: (data: T) => Promise<void>
  /** Cancel pending auto-save */
  cancel: () => void
  /** Whether currently saving */
  isSaving: boolean
  /** Error from last save attempt, or null */
  error: Error | null
}

/**
 * Self-contained generic auto-save hook
 *
 * Manages debouncing, pending state, and error handling internally.
 * No external state dependencies required.
 *
 * @example
 * ```tsx
 * const { save, isSaving } = useAutoSaveGeneric<MyConfig>({
 *   saveFn: async (config) => {
 *     await updateMyConfig(config)
 *   },
 *   debounceMs: 2000,
 * })
 *
 * useEffect(() => {
 *   if (config) {
 *     save(config)
 *   }
 * }, [config, save])
 * ```
 */
export function useAutoSaveGeneric<T>(
  config: UseAutoSaveConfig<T>
): UseAutoSaveReturnGeneric<T> {
  const { saveFn, debounceMs = 2000, onSaveSuccess, onSaveError } = config

  // Internal state management
  const [isSaving, setIsSaving] = useState(false)
  const [error, setError] = useState<Error | null>(null)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Perform the actual save
  const performSave = useCallback(
    async (data: T) => {
      try {
        setIsSaving(true)
        setError(null)
        await saveFn(data)
        onSaveSuccess?.()
      } catch (err) {
        const error = err instanceof Error ? err : new Error(String(err))
        setError(error)
        console.error('Auto-save failed:', error)
        onSaveError?.(error)
      } finally {
        setIsSaving(false)
      }
    },
    [saveFn, onSaveSuccess, onSaveError]
  )

  // Debounced save
  const save = useCallback(
    (data: T) => {
      // Clear existing timer
      if (timerRef.current) {
        clearTimeout(timerRef.current)
      }
      // Set new timer
      timerRef.current = setTimeout(() => {
        performSave(data)
      }, debounceMs)
    },
    [performSave, debounceMs]
  )

  // Save immediately
  const saveNow = useCallback(
    async (data: T) => {
      if (timerRef.current) {
        clearTimeout(timerRef.current)
        timerRef.current = null
      }
      await performSave(data)
    },
    [performSave]
  )

  // Cancel pending save
  const cancel = useCallback(() => {
    if (timerRef.current) {
      clearTimeout(timerRef.current)
      timerRef.current = null
    }
  }, [])

  // Cleanup timer on unmount
  useEffect(() => {
    return () => {
      if (timerRef.current) {
        clearTimeout(timerRef.current)
      }
    }
  }, [])

  return {
    save,
    saveNow,
    cancel,
    isSaving,
    error,
  }
}

/**
 * Legacy wrapper for backward compatibility with old API
 * Maintains external state for existing code
 */
export interface UseAutoSaveOptions {
  /** Debounce delay in milliseconds, default 2000ms */
  debounceMs?: number
  /** Save success callback */
  onSaveSuccess?: () => void
  /** Save error callback */
  onSaveError?: (error: Error) => void
}

export interface UseAutoSaveReturn {
  /** Trigger auto-save */
  triggerAutoSave: (sectionName: ConfigSectionName, sectionData: unknown) => void
  /** Save immediately */
  saveNow: (sectionName: ConfigSectionName, sectionData: unknown) => Promise<void>
  /** Cancel pending auto-save */
  cancelPendingAutoSave: () => void
}

export interface AutoSaveState {
  /** Whether currently saving */
  isAutoSaving: boolean
  /** Whether has unsaved changes */
  hasUnsavedChanges: boolean
}

/**
 * Legacy auto-save hook for bot config
 * Maintains backward compatibility with external state management
 *
 * @deprecated Use the generic useAutoSaveGeneric<T> instead
 */
export function useAutoSave(
  isInitialLoad: boolean,
  setAutoSaving: (saving: boolean) => void,
  setHasUnsavedChanges: (hasChanges: boolean) => void,
  options: UseAutoSaveOptions = {}
): UseAutoSaveReturn {
  const { debounceMs = 2000, onSaveSuccess, onSaveError } = options
  const autoSaveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Execute save operation
  const saveSection = useCallback(
    async (sectionName: ConfigSectionName, sectionData: unknown) => {
      try {
        setAutoSaving(true)
        const result = await updateBotConfigSection(sectionName, sectionData)
        if (!result.success) {
          throw new Error(result.error)
        }
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

  // Trigger auto-save (with debounce)
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

  // Save immediately (no debounce)
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

  // Cancel pending auto-save
  const cancelPendingAutoSave = useCallback(() => {
    if (autoSaveTimerRef.current) {
      clearTimeout(autoSaveTimerRef.current)
      autoSaveTimerRef.current = null
    }
  }, [])

  // Cleanup timer on unmount
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
