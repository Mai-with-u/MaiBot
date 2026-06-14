/**
 * usePendingOperation —— 「submit 待定 → 对话框确认 → 执行」同构流程的通用缓冲。
 *
 * 取代散落各页的 `pendingXxxRef + dialogOpen` 双 state：
 * 预览/校验完成后 submit(operation) 暂存待定操作并进入等待态（驱动确认对话框），
 * confirm() 执行注入的 onConfirm（执行成功后清空待定、关闭对话框），cancel() 放弃。
 *
 * 仅负责待定操作的生命周期；预览数据、执行结果等业务态仍由调用方（领域 hook）持有。
 */
import { useCallback, useState } from 'react'

export interface UsePendingOperationOptions<T> {
  /** 确认时执行的操作；自行处理成功/失败提示。抛错则保留待定态（对话框不关闭）。 */
  onConfirm: (operation: T) => void | Promise<void>
}

export interface UsePendingOperationResult<T> {
  /** 当前待定操作，无则为 null */
  pending: T | null
  /** 是否处于等待确认态（pending 非空），驱动确认对话框开关 */
  isWaiting: boolean
  /** onConfirm 是否执行中 */
  isConfirming: boolean
  /** 暂存一个待定操作并进入等待态 */
  submit: (operation: T) => void
  /** 确认执行；成功后清空待定，失败保留待定态 */
  confirm: () => Promise<void>
  /** 放弃待定操作 */
  cancel: () => void
}

export function usePendingOperation<T>(
  options: UsePendingOperationOptions<T>,
): UsePendingOperationResult<T> {
  const { onConfirm } = options
  const [pending, setPending] = useState<T | null>(null)
  const [isConfirming, setIsConfirming] = useState(false)

  const submit = useCallback((operation: T) => {
    setPending(operation)
  }, [])

  const cancel = useCallback(() => {
    setPending(null)
  }, [])

  const confirm = useCallback(async () => {
    if (pending === null) {
      return
    }
    setIsConfirming(true)
    try {
      await onConfirm(pending)
      // 执行成功才清空待定、关闭对话框；失败时保留以便重试
      setPending(null)
    } finally {
      setIsConfirming(false)
    }
  }, [onConfirm, pending])

  return {
    pending,
    isWaiting: pending !== null,
    isConfirming,
    submit,
    confirm,
    cancel,
  }
}
