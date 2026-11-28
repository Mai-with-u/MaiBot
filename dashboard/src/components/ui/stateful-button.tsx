import { Check, Copy, Loader2, RotateCcw, X } from 'lucide-react'
import { AnimatePresence, motion } from 'motion/react'
import { useCallback, useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'

import { MotionIconSwap, MotionRollText } from '@/components/motion'
import { Button } from '@/components/ui/button'
import type { ButtonProps } from '@/components/ui/button'
import { useMotion } from '@/lib/motion'
import { cn } from '@/lib/utils'

export interface UndoButtonProps {
  /** 执行主操作；返回 false 可中止进入撤销态 */
  onAction: () => boolean | void
  /** 点击撤销或按下 ⌘Z / Ctrl+Z 时触发 */
  onUndo: () => void
  /** 倒计时结束正式提交时可选回调 */
  onCommit?: () => void
  label?: ReactNode
  ariaLabel?: string
  title?: string
  icon?: ReactNode
  doneLabel?: ReactNode
  undoLabel?: ReactNode
  durationMs?: number
  disabled?: boolean
  variant?: ButtonProps['variant']
  size?: ButtonProps['size']
  className?: string
  enableShortcut?: boolean
}

/**
 * 撤销倒计时按钮（DesignBookmark / Kokonut UI Undo Button 风格）。
 * 点击后就地平滑扩展为带环形进度条与倒计时数字的撤销丸，支持 ⌘Z / Ctrl+Z 一键撤回。
 */
export function UndoButton({
  onAction,
  onUndo,
  onCommit,
  label,
  ariaLabel,
  title,
  icon,
  doneLabel = '已清空',
  undoLabel = '撤销',
  durationMs = 5000,
  disabled = false,
  variant = 'outline',
  size = 'sm',
  className,
  enableShortcut = true,
}: UndoButtonProps) {
  const { enabled, transitions } = useMotion()
  const [undoing, setUndoing] = useState(false)
  const [remainingSeconds, setRemainingSeconds] = useState(Math.ceil(durationMs / 1000))
  const commitTimerRef = useRef<number | null>(null)
  const tickTimerRef = useRef<number | null>(null)

  const clearTimers = useCallback(() => {
    if (commitTimerRef.current !== null) {
      window.clearTimeout(commitTimerRef.current)
      commitTimerRef.current = null
    }
    if (tickTimerRef.current !== null) {
      window.clearInterval(tickTimerRef.current)
      tickTimerRef.current = null
    }
  }, [])

  useEffect(() => () => clearTimers(), [clearTimers])

  const handleUndo = useCallback(() => {
    clearTimers()
    setUndoing(false)
    onUndo()
  }, [clearTimers, onUndo])

  useEffect(() => {
    if (!undoing || !enableShortcut) return
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'z' && !event.shiftKey) {
        event.preventDefault()
        handleUndo()
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [enableShortcut, handleUndo, undoing])

  const handleTrigger = () => {
    if (disabled || undoing) return
    const result = onAction()
    if (result === false) return
    const totalSeconds = Math.max(1, Math.ceil(durationMs / 1000))
    const deadline = Date.now() + durationMs
    setRemainingSeconds(totalSeconds)
    setUndoing(true)
    clearTimers()
    tickTimerRef.current = window.setInterval(() => {
      const left = Math.max(1, Math.ceil((deadline - Date.now()) / 1000))
      setRemainingSeconds(left)
    }, 250)
    commitTimerRef.current = window.setTimeout(() => {
      clearTimers()
      setUndoing(false)
      onCommit?.()
    }, durationMs)
  }

  return (
    <motion.div
      layout={enabled}
      transition={transitions.snappy}
      className="inline-flex shrink-0 items-center"
    >
      <AnimatePresence mode="popLayout" initial={false}>
        {undoing ? (
          <motion.button
            key="undo"
            type="button"
            data-undo-active="true"
            aria-label={typeof undoLabel === 'string' ? undoLabel : '撤销'}
            onClick={handleUndo}
            initial={enabled ? { opacity: 0, scale: 0.94, filter: 'blur(3px)' } : false}
            animate={{ opacity: 1, scale: 1, filter: 'blur(0px)' }}
            exit={enabled ? { opacity: 0, scale: 0.94, filter: 'blur(3px)' } : undefined}
            transition={transitions.snappy}
            className={cn(
              'inline-flex h-8 cursor-pointer items-center gap-2 rounded-full border border-primary/40 bg-primary/12 px-3 text-xs font-medium text-foreground shadow-xs transition-colors hover:bg-primary/18 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring',
              className
            )}
          >
            <span className="text-muted-foreground">{doneLabel}</span>
            <span aria-hidden="true" className="h-3 w-px bg-border/80" />
            <span className="inline-flex items-center gap-1 text-primary">
              <RotateCcw className="size-3.5" aria-hidden="true" />
              <span>{undoLabel}</span>
            </span>
            <span className="relative inline-flex size-4 items-center justify-center tabular-nums text-xs text-primary">
              <svg className="absolute inset-0 -rotate-90" viewBox="0 0 16 16" aria-hidden="true">
                <circle
                  cx="8"
                  cy="8"
                  r="6.5"
                  fill="none"
                  stroke="currentColor"
                  strokeOpacity="0.22"
                  strokeWidth="1.5"
                />
                <motion.circle
                  cx="8"
                  cy="8"
                  r="6.5"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.5"
                  strokeLinecap="round"
                  strokeDasharray={2 * Math.PI * 6.5}
                  initial={{ strokeDashoffset: 0 }}
                  animate={{ strokeDashoffset: 2 * Math.PI * 6.5 }}
                  transition={{ duration: durationMs / 1000, ease: 'linear' }}
                />
              </svg>
              <span className="sr-only">{remainingSeconds}s</span>
            </span>
          </motion.button>
        ) : (
          <motion.div
            key="idle"
            initial={enabled ? { opacity: 0, scale: 0.95 } : false}
            animate={{ opacity: 1, scale: 1 }}
            exit={enabled ? { opacity: 0, scale: 0.95 } : undefined}
            transition={transitions.fast}
          >
            <Button
              type="button"
              variant={variant}
              size={size}
              disabled={disabled}
              aria-label={ariaLabel}
              title={title}
              onClick={handleTrigger}
              className={className}
            >
              {icon}
              {label}
            </Button>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  )
}

export interface CopyButtonProps extends Omit<ButtonProps, 'onClick' | 'children'> {
  text?: string
  onCopy?: () => Promise<boolean | void> | boolean | void
  label?: string
  copiedLabel?: string
  copiedDurationMs?: number
  iconOnly?: boolean
  iconClassName?: string
}

/**
 * 形态翻转复制按钮（DesignBookmark Copy Button 风格）。
 * 使用 MotionIconSwap + MotionRollText 平滑切换 Copy ↔ Check 与文案。
 */
export function CopyButton({
  text,
  onCopy,
  label = '复制',
  copiedLabel = '已复制',
  copiedDurationMs = 1800,
  iconOnly = false,
  iconClassName = 'size-3.5',
  className,
  'aria-label': ariaLabel,
  title,
  ...props
}: CopyButtonProps) {
  const [copied, setCopied] = useState(false)
  const timerRef = useRef<number | null>(null)

  useEffect(
    () => () => {
      if (timerRef.current !== null) window.clearTimeout(timerRef.current)
    },
    []
  )

  const handleClick = async () => {
    try {
      if (onCopy) {
        const result = await onCopy()
        if (result === false) return
      } else if (text !== undefined) {
        await navigator.clipboard.writeText(text)
      }
      setCopied(true)
      if (timerRef.current !== null) window.clearTimeout(timerRef.current)
      timerRef.current = window.setTimeout(() => setCopied(false), copiedDurationMs)
    } catch {
      /* 剪贴板不可用时保持原状，由调用方按需处理错误提示 */
    }
  }

  return (
    <Button
      type="button"
      onClick={() => void handleClick()}
      aria-label={ariaLabel ?? (copied ? copiedLabel : label)}
      title={title ?? (copied ? copiedLabel : label)}
      className={cn(iconOnly ? undefined : 'gap-1.5', className)}
      {...props}
    >
      <MotionIconSwap
        iconKey={copied ? 'copied' : 'copy'}
        className="inline-flex shrink-0 items-center justify-center"
      >
        {copied ? (
          <Check className={cn(iconClassName, 'text-emerald-600 dark:text-emerald-400')} />
        ) : (
          <Copy className={iconClassName} />
        )}
      </MotionIconSwap>
      {!iconOnly && (
        <MotionRollText textKey={copied ? 'copied' : 'copy'}>
          {copied ? copiedLabel : label}
        </MotionRollText>
      )}
    </Button>
  )
}

export type StatefulButtonStatus = 'idle' | 'loading' | 'success' | 'error'

export interface StatefulButtonProps extends Omit<ButtonProps, 'children'> {
  status: StatefulButtonStatus
  idleLabel: string
  loadingLabel?: string
  successLabel?: string
  errorLabel?: string
  idleIcon?: ReactNode
  successIcon?: ReactNode
  errorIcon?: ReactNode
}

/**
 * 态变按钮（DesignBookmark Loading to Success 风格）。
 * 在 idle / loading / success / error 之间通过图标翻转与文案滚动平滑过渡。
 */
export function StatefulButton({
  status,
  idleLabel,
  loadingLabel = '处理中…',
  successLabel = '已完成',
  errorLabel = '失败',
  idleIcon,
  successIcon,
  errorIcon,
  disabled,
  className,
  ...props
}: StatefulButtonProps) {
  const text =
    status === 'loading'
      ? loadingLabel
      : status === 'success'
        ? successLabel
        : status === 'error'
          ? errorLabel
          : idleLabel

  const icon =
    status === 'loading' ? (
      <Loader2 className="size-3.5 animate-spin" />
    ) : status === 'success' ? (
      (successIcon ?? <Check className="size-3.5 text-emerald-600 dark:text-emerald-400" />)
    ) : status === 'error' ? (
      (errorIcon ?? <X className="size-3.5 text-destructive" />)
    ) : (
      idleIcon
    )

  return (
    <Button
      disabled={disabled || status === 'loading'}
      data-button-status={status}
      className={cn('gap-1.5', className)}
      {...props}
    >
      {(icon || status !== 'idle') && (
        <MotionIconSwap iconKey={status} className="inline-flex shrink-0 items-center">
          {icon}
        </MotionIconSwap>
      )}
      <MotionRollText textKey={status}>{text}</MotionRollText>
    </Button>
  )
}
