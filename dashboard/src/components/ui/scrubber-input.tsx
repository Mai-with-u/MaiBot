import { useCallback, useRef, useState } from 'react'
import type { KeyboardEvent, PointerEvent, ReactNode } from 'react'

import { cn } from '@/lib/utils'

export interface ScrubberInputProps {
  label: ReactNode
  ariaLabel?: string
  value: number
  min: number
  max: number
  step?: number
  formatValue?: (value: number) => string
  onChange: (value: number) => void
  disabled?: boolean
  className?: string
}

function clampStep(value: number, min: number, max: number, step: number): number {
  const clamped = Math.min(max, Math.max(min, value))
  const steps = Math.round((clamped - min) / step)
  return Math.min(max, Math.max(min, min + steps * step))
}

/**
 * 紧凑横向可拖拽数值条（DesignBookmark / SmoothUI Scrubber 风格）。
 * 同时完整实现 WAI-ARIA slider 键盘交互（方向键 / Home / End），适配紧凑工具栏参数调节。
 */
export function ScrubberInput({
  label,
  ariaLabel,
  value,
  min,
  max,
  step = 1,
  formatValue = (v) => String(v),
  onChange,
  disabled = false,
  className,
}: ScrubberInputProps) {
  const trackRef = useRef<HTMLDivElement | null>(null)
  const [dragging, setDragging] = useState(false)
  const range = Math.max(1, max - min)
  const ratio = Math.min(1, Math.max(0, (value - min) / range))
  const formatted = formatValue(value)

  const updateFromClientX = useCallback(
    (clientX: number) => {
      const rect = trackRef.current?.getBoundingClientRect()
      if (!rect || rect.width <= 0) return
      const nextRatio = Math.min(1, Math.max(0, (clientX - rect.left) / rect.width))
      const next = clampStep(min + nextRatio * range, min, max, step)
      if (next !== value) onChange(next)
    },
    [max, min, onChange, range, step, value]
  )

  const handlePointerDown = (event: PointerEvent<HTMLDivElement>) => {
    if (disabled || event.button !== 0) return
    event.preventDefault()
    event.currentTarget.focus()
    setDragging(true)
    try {
      event.currentTarget.setPointerCapture(event.pointerId)
    } catch {
      /* JSDOM 中可能未实现 setPointerCapture */
    }
    updateFromClientX(event.clientX)
  }

  const handlePointerMove = (event: PointerEvent<HTMLDivElement>) => {
    if (!dragging || disabled) return
    updateFromClientX(event.clientX)
  }

  const handlePointerUp = () => {
    setDragging(false)
  }

  const handleKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (disabled) return
    let next: number | null = null
    if (event.key === 'ArrowRight' || event.key === 'ArrowUp') {
      next = clampStep(value + step, min, max, step)
    } else if (event.key === 'ArrowLeft' || event.key === 'ArrowDown') {
      next = clampStep(value - step, min, max, step)
    } else if (event.key === 'Home') {
      next = min
    } else if (event.key === 'End') {
      next = max
    }
    if (next !== null) {
      event.preventDefault()
      if (next !== value) onChange(next)
    }
  }

  return (
    <div
      ref={trackRef}
      role="slider"
      tabIndex={disabled ? -1 : 0}
      aria-label={ariaLabel ?? (typeof label === 'string' ? label : undefined)}
      aria-valuemin={min}
      aria-valuemax={max}
      aria-valuenow={value}
      aria-valuetext={formatted}
      aria-disabled={disabled || undefined}
      data-scrubber-input="true"
      data-dragging={dragging ? 'true' : 'false'}
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onPointerUp={handlePointerUp}
      onPointerCancel={handlePointerUp}
      onLostPointerCapture={handlePointerUp}
      onKeyDown={handleKeyDown}
      className={cn(
        'relative inline-flex h-8 min-w-[7.5rem] cursor-ew-resize items-center justify-between gap-2 overflow-hidden rounded-md border border-border/75 bg-muted/45 px-2.5 text-xs select-none transition-colors hover:border-border focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring',
        dragging && 'border-primary/60',
        disabled && 'pointer-events-none opacity-50',
        className
      )}
    >
      <span
        aria-hidden="true"
        className="pointer-events-none absolute inset-y-0 left-0 bg-primary/16 transition-[width] duration-75"
        style={{ width: `${ratio * 100}%` }}
      />
      <span
        aria-hidden="true"
        className="pointer-events-none absolute inset-y-1.5 w-[2px] rounded-full bg-primary/70 transition-[left] duration-75"
        style={{ left: `calc(${ratio * 100}% - 1px)` }}
      />
      <span className="relative z-10 text-muted-foreground">{label}</span>
      <span className="relative z-10 font-medium text-foreground tabular-nums">{formatted}</span>
    </div>
  )
}
