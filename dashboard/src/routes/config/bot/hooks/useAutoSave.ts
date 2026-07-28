import { useCallback, useEffect, useRef } from 'react'

import { updateBotConfigSection } from '@/lib/config-api'

/**
 * Bot 配置页自动保存配置。
 */
export interface UseAutoSaveOptions {
  /** 防抖延迟，默认 2000ms */
  debounceMs?: number
  /** 保存成功回调 */
  onSaveSuccess?: () => void
  /** 保存失败回调 */
  onSaveError?: (error: Error) => void
}

export interface UseAutoSaveReturn {
  /** 触发自动保存 */
  triggerAutoSave: (sectionName: string, sectionData: unknown) => void
  /** 立即保存 */
  saveNow: (sectionName: string, sectionData: unknown) => Promise<void>
  /** 在已开始的自动保存之后执行整份配置写入，并阻塞后来触发的分区写入 */
  runWithAutoSaveBarrier: <T>(operation: () => Promise<T>) => Promise<T>
  /** 取消尚未执行的自动保存，并等待已开始的保存结束 */
  cancelPendingAutoSave: () => Promise<void>
  /** 将当前修订标记为已同步（用于重新加载配置后丢弃旧状态） */
  resetAutoSaveState: () => void
}

interface SectionSaveState {
  revision: number
  savedRevision: number
  timer: ReturnType<typeof setTimeout> | null
  saveChain: Promise<void>
}

/**
 * Bot 配置页自动保存 hook。
 */
export function useAutoSave(
  isInitialLoad: boolean,
  setAutoSaving: (saving: boolean) => void,
  setHasUnsavedChanges: (hasChanges: boolean) => void,
  options: UseAutoSaveOptions = {}
): UseAutoSaveReturn {
  const { debounceMs = 2000, onSaveSuccess, onSaveError } = options
  const sectionStatesRef = useRef(new Map<string, SectionSaveState>())
  const activeSaveCountRef = useRef(0)
  const isMountedRef = useRef(true)
  const writeBarrierRef = useRef<Promise<void>>(Promise.resolve())

  const getSectionState = useCallback((sectionName: string): SectionSaveState => {
    const existingState = sectionStatesRef.current.get(sectionName)
    if (existingState) return existingState

    const nextState: SectionSaveState = {
      revision: 0,
      savedRevision: 0,
      timer: null,
      saveChain: Promise.resolve(),
    }
    sectionStatesRef.current.set(sectionName, nextState)
    return nextState
  }, [])

  const updateUnsavedState = useCallback(() => {
    if (!isMountedRef.current) return
    const hasUnsavedChanges = Array.from(sectionStatesRef.current.values()).some(
      ({ revision, savedRevision }) => revision > savedRevision
    )
    setHasUnsavedChanges(hasUnsavedChanges)
  }, [setHasUnsavedChanges])

  const enqueueSave = useCallback(
    (sectionName: string, sectionData: unknown, revision: number): Promise<void> => {
      const sectionState = getSectionState(sectionName)
      activeSaveCountRef.current += 1
      if (isMountedRef.current) {
        setAutoSaving(true)
      }

      // 同一分区按触发顺序串行写入，避免旧请求晚完成后覆盖较新的配置。
      // 整份配置正在写入时，后来触发的分区写入必须等待 barrier，避免被旧快照覆盖。
      const writeBarrier = writeBarrierRef.current
      const savePromise = Promise.all([
        sectionState.saveChain.catch(() => undefined),
        writeBarrier,
      ]).then(async () => {
        try {
          await updateBotConfigSection(sectionName, sectionData)
          sectionState.savedRevision = Math.max(sectionState.savedRevision, revision)
          if (isMountedRef.current) {
            updateUnsavedState()
            onSaveSuccess?.()
          }
        } catch (error) {
          console.error(`自动保存 ${sectionName} 失败:`, error)
          if (isMountedRef.current) {
            updateUnsavedState()
            onSaveError?.(error instanceof Error ? error : new Error(String(error)))
          }
        } finally {
          activeSaveCountRef.current -= 1
          if (isMountedRef.current) {
            setAutoSaving(activeSaveCountRef.current > 0)
          }
        }
      })

      sectionState.saveChain = savePromise
      return savePromise
    },
    [getSectionState, onSaveError, onSaveSuccess, setAutoSaving, updateUnsavedState]
  )

  // 每个配置分区独立防抖，一个分区的编辑不会取消其他分区的保存。
  const triggerAutoSave = useCallback(
    (sectionName: string, sectionData: unknown) => {
      if (isInitialLoad) return

      const sectionState = getSectionState(sectionName)
      sectionState.revision += 1
      const revision = sectionState.revision
      updateUnsavedState()

      if (sectionState.timer) {
        clearTimeout(sectionState.timer)
      }

      sectionState.timer = setTimeout(() => {
        sectionState.timer = null
        void enqueueSave(sectionName, sectionData, revision)
      }, debounceMs)
    },
    [debounceMs, enqueueSave, getSectionState, isInitialLoad, updateUnsavedState]
  )

  const saveNow = useCallback(
    async (sectionName: string, sectionData: unknown) => {
      const sectionState = getSectionState(sectionName)
      if (sectionState.timer) {
        clearTimeout(sectionState.timer)
        sectionState.timer = null
      }

      sectionState.revision += 1
      const revision = sectionState.revision
      updateUnsavedState()
      await enqueueSave(sectionName, sectionData, revision)
    },
    [enqueueSave, getSectionState, updateUnsavedState]
  )

  const runWithAutoSaveBarrier = useCallback(
    async <T>(operation: () => Promise<T>): Promise<T> => {
      const revisionsAtStart = new Map<string, number>()
      const activeSaveChains: Promise<void>[] = []
      for (const [sectionName, sectionState] of sectionStatesRef.current) {
        if (sectionState.timer) {
          clearTimeout(sectionState.timer)
          sectionState.timer = null
        }
        revisionsAtStart.set(sectionName, sectionState.revision)
        activeSaveChains.push(sectionState.saveChain)
      }

      const previousBarrier = writeBarrierRef.current
      activeSaveCountRef.current += 1
      if (isMountedRef.current) {
        setAutoSaving(true)
      }

      // 先等待点击前已发出的分区写入；随后执行整份配置写入。之后产生的
      // 自动保存会捕获这个 barrier，只能在整份写入结束后继续。
      const operationPromise = Promise.all([previousBarrier, ...activeSaveChains])
        .then(operation)
        .then((result) => {
          for (const [sectionName, revision] of revisionsAtStart) {
            const sectionState = sectionStatesRef.current.get(sectionName)
            if (sectionState) {
              sectionState.savedRevision = Math.max(sectionState.savedRevision, revision)
            }
          }
          updateUnsavedState()
          return result
        })
        .finally(() => {
          activeSaveCountRef.current -= 1
          if (isMountedRef.current) {
            setAutoSaving(activeSaveCountRef.current > 0)
          }
        })

      writeBarrierRef.current = operationPromise.then(
        () => undefined,
        () => undefined
      )
      return await operationPromise
    },
    [setAutoSaving, updateUnsavedState]
  )

  // 等待已入队请求后再让手动保存继续，避免较旧的分区请求覆盖整份配置。
  const cancelPendingAutoSave = useCallback(async () => {
    const activeSaveChains: Promise<void>[] = []
    for (const sectionState of sectionStatesRef.current.values()) {
      if (sectionState.timer) {
        clearTimeout(sectionState.timer)
        sectionState.timer = null
      }
      activeSaveChains.push(sectionState.saveChain)
    }

    await Promise.all([writeBarrierRef.current, ...activeSaveChains])
  }, [])

  const resetAutoSaveState = useCallback(() => {
    for (const sectionState of sectionStatesRef.current.values()) {
      if (sectionState.timer) {
        clearTimeout(sectionState.timer)
        sectionState.timer = null
      }
      sectionState.savedRevision = sectionState.revision
    }
    updateUnsavedState()
  }, [updateUnsavedState])

  useEffect(() => {
    isMountedRef.current = true
    const sectionStates = sectionStatesRef.current
    return () => {
      isMountedRef.current = false
      for (const sectionState of sectionStates.values()) {
        if (sectionState.timer) {
          clearTimeout(sectionState.timer)
          sectionState.timer = null
        }
      }
    }
  }, [])

  return {
    triggerAutoSave,
    saveNow,
    runWithAutoSaveBarrier,
    cancelPendingAutoSave,
    resetAutoSaveState,
  }
}
