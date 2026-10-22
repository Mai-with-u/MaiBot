import { useEffect, useLayoutEffect, useRef, type PointerEvent } from 'react'
import { cn } from '@/lib/utils'
import {
  DAY_MINUTES,
  TIMELINE_TICKS,
  TIMELINE_DRAG_STEP_MINUTES,
  formatTimelineMinute,
  timelineMinuteAtPosition,
  type TimelineRange,
} from '@/lib/time-range'

export function TimeRangeTicks() {
  return (
    <div className="text-muted-foreground relative h-4 text-[10px]">
      {TIMELINE_TICKS.map((hour) => (
        <span
          key={hour}
          className="absolute -translate-x-1/2"
          style={{ left: `${(hour / 24) * 100}%` }}
        >
          {String(hour).padStart(2, '0')}
        </span>
      ))}
    </div>
  )
}

/** 展示、指针拖动与键盘调整共用一套刻度，业务只接收新的时间范围。 */
export function TimeRangeTrack({
  range,
  segments,
  colorClassName,
  label = '',
  disabled = false,
  onChange,
}: {
  range: TimelineRange | null
  segments: { left: number; width: number }[]
  colorClassName: string
  label?: string
  disabled?: boolean
  onChange: (range: TimelineRange) => void
}) {
  const trackRef = useRef<HTMLDivElement>(null)
  const frameRef = useRef<number | null>(null)
  const pendingRef = useRef<TimelineRange | null>(null)
  const callbackRef = useRef(onChange)
  useLayoutEffect(() => {
    callbackRef.current = onChange
  }, [onChange])
  useEffect(
    () => () => {
      if (frameRef.current !== null) cancelAnimationFrame(frameRef.current)
    },
    []
  )
  const flush = () => {
    if (frameRef.current !== null) cancelAnimationFrame(frameRef.current)
    frameRef.current = null
    const next = pendingRef.current
    pendingRef.current = null
    if (next) callbackRef.current(next)
  }
  const update = (event: PointerEvent<HTMLButtonElement>, edge: 'start' | 'end') => {
    if (!range || !trackRef.current) return
    const rect = trackRef.current.getBoundingClientRect()
    pendingRef.current = {
      ...range,
      [edge]: timelineMinuteAtPosition(event.clientX, rect.left, rect.width),
    }
    if (frameRef.current === null) frameRef.current = requestAnimationFrame(flush)
  }
  return (
    <div
      ref={trackRef}
      data-dashboard-time-range="true"
      className="bg-background relative h-8 rounded-md border"
    >
      {TIMELINE_TICKS.slice(1, -1).map((hour) => (
        <span
          key={hour}
          className="border-muted-foreground/20 absolute top-0 h-full border-l border-dashed"
          style={{ left: `${(hour / 24) * 100}%` }}
        />
      ))}
      {segments.map((segment, index) => (
        <span
          key={index}
          className={cn('absolute top-1/2 h-4 -translate-y-1/2 rounded-sm', colorClassName)}
          style={{ left: `${segment.left}%`, width: `${segment.width}%` }}
        />
      ))}
      {range &&
        !disabled &&
        (['start', 'end'] as const).map((edge) => (
          <button
            key={edge}
            type="button"
            role="slider"
            data-time-range-handle="true"
            aria-label={`调整${label}${edge === 'start' ? '开始' : '结束'}时间`}
            aria-valuemin={0}
            aria-valuemax={DAY_MINUTES - 1}
            aria-valuenow={range[edge]}
            aria-valuetext={formatTimelineMinute(range[edge])}
            className="border-background bg-foreground/80 focus-visible:ring-ring absolute top-1/2 h-7 w-2 -translate-x-1/2 -translate-y-1/2 cursor-ew-resize touch-none rounded-sm border focus-visible:ring-2 focus-visible:outline-none"
            style={{ left: `${((range[edge] + (edge === 'end' ? 1 : 0)) / DAY_MINUTES) * 100}%` }}
            onPointerDown={(event) => {
              event.preventDefault()
              event.currentTarget.setPointerCapture(event.pointerId)
              update(event, edge)
            }}
            onPointerMove={(event) => {
              if (event.currentTarget.hasPointerCapture(event.pointerId)) update(event, edge)
            }}
            onPointerUp={(event) => {
              if (!event.currentTarget.hasPointerCapture(event.pointerId)) return
              update(event, edge)
              flush()
              event.currentTarget.releasePointerCapture(event.pointerId)
            }}
            onLostPointerCapture={flush}
            onPointerCancel={flush}
            onKeyDown={(event) => {
              const delta = ['ArrowRight', 'ArrowUp'].includes(event.key)
                ? TIMELINE_DRAG_STEP_MINUTES
                : ['ArrowLeft', 'ArrowDown'].includes(event.key)
                  ? -TIMELINE_DRAG_STEP_MINUTES
                  : 0
              if (!delta && event.key !== 'Home' && event.key !== 'End') return
              event.preventDefault()
              const minute =
                event.key === 'Home'
                  ? 0
                  : event.key === 'End'
                    ? DAY_MINUTES - 1
                    : range[edge] + delta
              onChange({ ...range, [edge]: Math.max(0, Math.min(DAY_MINUTES - 1, minute)) })
            }}
          />
        ))}
    </div>
  )
}
