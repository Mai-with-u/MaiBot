/**
 * useEmbeddingWarning —— 嵌入模型「更换警告」领域 hook。
 *
 * 基于通用 usePendingOperation 封装 embedding 换模型的「检测变化 → 弹警告 → 确认应用 / 取消还原」流程：
 * - previousEmbeddingModelsRef 记录上一次的 embedding model_list，供变化检测；
 * - detectChange 在 updateTaskConfig 检测到 embedding model_list 变化且非首次时调用，
 *   有变化则 submit 待定更新（驱动警告对话框），返回 true 表示「拦截」原始更新；
 * - confirm 应用待定更新（由调用方注入的 applyUpdate 写回 taskConfig）、更新 previous ref、弹 toast；
 * - cancel 放弃待定更新；
 * - setPrevious 用于 loadConfig 初始化与正常更新后同步 previous ref。
 *
 * 仅负责待定操作的生命周期与 previous ref；taskConfig 本体仍由核心 hook 持有。
 */
import { useCallback, useRef } from 'react'

import type { TaskConfig } from '../types'
import { usePendingOperation } from '@/hooks/usePendingOperation'
import { useToast } from '@/hooks/use-toast'

/** 待定的 embedding 更新载荷 */
export interface PendingEmbeddingUpdate {
  field: keyof TaskConfig
  value: string[] | number
}

export interface UseEmbeddingWarningOptions {
  /** 确认更换时执行的应用逻辑：将待定更新写回 taskConfig（核心 hook 提供） */
  applyUpdate: (update: PendingEmbeddingUpdate) => void
}

export interface UseEmbeddingWarningResult {
  /** 是否处于等待确认态，驱动警告对话框开关 */
  isOpen: boolean
  /** 警告对话框 onOpenChange 处理：关闭时放弃待定 */
  setOpen: (open: boolean) => void
  /**
   * 检测 embedding model_list 变化。
   * @returns 有变化并已 submit 待定更新（应拦截原始更新）时返回 true，否则返回 false。
   */
  detectChange: (field: keyof TaskConfig, value: string[]) => boolean
  /** 确认更换（应用待定更新 + 更新 previous ref + toast） */
  confirm: () => Promise<void>
  /** 取消更换（放弃待定更新） */
  cancel: () => void
  /** 设置 previous embedding 模型列表（loadConfig 初始化 / 正常更新后同步） */
  setPrevious: (models: string[]) => void
}

export function useEmbeddingWarning(
  options: UseEmbeddingWarningOptions,
): UseEmbeddingWarningResult {
  const { applyUpdate } = options
  const { toast } = useToast()

  // 上一次的 embedding 模型列表，用于变化检测
  const previousEmbeddingModelsRef = useRef<string[]>([])

  const setPrevious = useCallback((models: string[]) => {
    previousEmbeddingModelsRef.current = [...models]
  }, [])

  const pending = usePendingOperation<PendingEmbeddingUpdate>({
    onConfirm: (update) => {
      // 应用待定更新写回 taskConfig
      applyUpdate(update)
      // 更新 previous ref
      if (update.field === 'model_list' && Array.isArray(update.value)) {
        previousEmbeddingModelsRef.current = [...update.value]
      }
      toast({
        title: '嵌入模型已更新',
        description: '建议重新生成知识库向量以确保最佳匹配精度',
      })
    },
  })

  const detectChange = useCallback((field: keyof TaskConfig, value: string[]): boolean => {
    const previousModels = previousEmbeddingModelsRef.current
    const newModels = value

    const hasChanges =
      previousModels.length !== newModels.length ||
      previousModels.some((model) => !newModels.includes(model)) ||
      newModels.some((model) => !previousModels.includes(model))

    if (hasChanges && previousModels.length > 0) {
      pending.submit({ field, value })
      return true
    }
    return false
  }, [pending])

  const setOpen = useCallback((open: boolean) => {
    // 对话框关闭即放弃待定更新（与原 setEmbeddingWarningOpen(false) 语义一致）
    if (!open) {
      pending.cancel()
    }
  }, [pending])

  return {
    isOpen: pending.isWaiting,
    setOpen,
    detectChange,
    confirm: pending.confirm,
    cancel: pending.cancel,
    setPrevious,
  }
}
