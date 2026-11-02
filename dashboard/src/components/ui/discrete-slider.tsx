import { motion, useMotionValue, useReducedMotion, useSpring, useTransform } from 'motion/react'
import { useEffect, useId, useLayoutEffect, useRef, useState } from 'react'

import { Slider } from '@/components/ui/slider'
import { cn } from '@/lib/utils'
import '@/styles/discrete-slider.css'

export interface DiscreteSliderOption<T extends string | number> {
  value: T
  label: string
  description?: string
}

/** 有序枚举使用位置滑动，保存时仍回传原始配置值；选项顺序即档位顺序。 */
export function DiscreteSlider<T extends string | number>({
  options, value, onValueChange, label, disabled = false, className,
}: {
  options: DiscreteSliderOption<T>[]
  value: T
  onValueChange: (value: T) => void
  label: string
  disabled?: boolean
  className?: string
}) {
  const descriptionId = useId()
  const selectedIndex = options.findIndex(option => option.value === value)
  const selected = options[selectedIndex]
  const [position, setPosition] = useState<number | null>(null)
  // 只有用户操作后才播放吸附动画；切页挂载和配置载入时滑块直接就位。
  const [interacted, setInteracted] = useState(false)
  const pointerPosition = useRef<number | null>(null)
  const previewIndex = Math.round(position ?? selectedIndex)
  const preview = options[previewIndex]
  const reduceMotion = useReducedMotion()
  const targetPosition = useMotionValue(selectedIndex)
  const springPosition = useSpring(targetPosition, { stiffness: 460, damping: 38, mass: 0.6 })
  const animatedPosition = reduceMotion ? targetPosition : springPosition
  const labelRow = useRef<HTMLDivElement>(null)
  const [labelRects, setLabelRects] = useState<Array<{ left: number; width: number }>>([])
  // 底板按档位文字的实际位置定位，档位名怎么排布都贴得上；量到之前先按等分估算。
  useLayoutEffect(() => {
    const row = labelRow.current
    if (!row) return
    const labels = [...row.querySelectorAll<HTMLElement>('[data-discrete-slider-label]')]
    const measure = () => setLabelRects(labels.map(label => ({ left: label.offsetLeft, width: label.offsetWidth })))
    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(row)
    labels.forEach(label => observer.observe(label))
    return () => observer.disconnect()
  }, [options.length])
  // 底板中心连续跟随拖杆；跨越两档时拉宽，到达刻度时收回，点选也走同一动画。
  const plateStretch = (current: number) => reduceMotion ? 0 : Math.sin(Math.PI * (current - Math.floor(current))) * 0.28
  const plateRect = (current: number) => {
    const from = labelRects[Math.floor(current)]
    const to = labelRects[Math.ceil(current)] ?? from
    if (!from || !to) return null
    const ratio = current - Math.floor(current)
    return { left: from.left + (to.left - from.left) * ratio, width: from.width + (to.width - from.width) * ratio }
  }
  const plateLeft = useTransform(animatedPosition, current => {
    const stretch = plateStretch(current)
    const rect = plateRect(current)
    return rect ? `${rect.left - rect.width * stretch / 2}px` : `calc(${(current - stretch / 2) * 100 / options.length}% + 2px)`
  })
  const plateWidth = useTransform(animatedPosition, current => {
    const stretch = plateStretch(current)
    const rect = plateRect(current)
    return rect ? `${rect.width * (1 + stretch)}px` : `calc(${(1 + stretch) * 100 / options.length}% - 4px)`
  })
  useEffect(() => {
    targetPosition.set(position ?? selectedIndex)
    if (!interacted) springPosition.jump(position ?? selectedIndex)
  }, [interacted, position, selectedIndex, springPosition, targetPosition])
  if (options.length < 2) throw new Error('DiscreteSlider requires at least two ordered options')

  // 拖动只更新本地位置，松手才提交枚举值，避免拖动途中反复保存配置。
  const finishDrag = () => {
    if (pointerPosition.current === null) return
    const index = Math.round(pointerPosition.current)
    pointerPosition.current = null
    setPosition(null)
    if (!disabled && index !== selectedIndex) onValueChange(options[index].value)
  }
  const cancelDrag = () => {
    pointerPosition.current = null
    setPosition(null)
  }

  return (
    <div data-dashboard-discrete-slider="true" data-dragging={position !== null} data-interacted={interacted} onPointerDownCapture={() => setInteracted(true)} onKeyDownCapture={() => setInteracted(true)} className={cn('min-w-0 py-2', disabled && 'opacity-50', className)}>
      {/* 轨道端点对齐首尾标签中心，避免长文字伸出字段边界。 */}
      <div className="relative py-3" style={{ marginInline: `${50 / options.length}%` }}>
        {selected ? (
          <Slider
            aria-label={label}
            aria-valuetext={options[previewIndex].label}
            aria-describedby={preview.description ? descriptionId : undefined}
            min={0} max={options.length - 1} step={0.001}
            value={[position ?? selectedIndex]} disabled={disabled}
            onPointerDownCapture={event => {
              if (disabled || event.button !== 0) return
              pointerPosition.current = selectedIndex
              setPosition(selectedIndex)
            }}
            onValueChange={([nextPosition]) => {
              if (pointerPosition.current !== null) {
                pointerPosition.current = nextPosition
                setPosition(nextPosition)
              } else {
                onValueChange(options[Math.round(nextPosition)].value)
              }
            }}
            onPointerUp={finishDrag}
            onPointerCancel={cancelDrag}
            onLostPointerCapture={cancelDrag}
            onKeyDown={event => {
              // 指针使用连续位置，键盘仍然一次调整一个完整档位。
              const moves: Record<string, number> = {
                ArrowRight: 1, ArrowUp: 1, PageUp: 1,
                ArrowLeft: -1, ArrowDown: -1, PageDown: -1,
              }
              if (event.key !== 'Home' && event.key !== 'End' && !(event.key in moves)) return
              event.preventDefault()
              if (disabled) return
              const next = event.key === 'Home' ? 0 : event.key === 'End' ? options.length - 1
                : Math.max(0, Math.min(options.length - 1, selectedIndex + moves[event.key]))
              onValueChange(options[next].value)
            }}
          />
        ) : (
          <p role="alert" className="text-sm text-destructive">未知档位：{String(value)}</p>
        )}
        <div aria-hidden="true" className="pointer-events-none absolute inset-x-0 bottom-0 flex justify-between">
          {options.map((option, index) => <span key={option.value} data-active={index === previewIndex} className="h-1.5 w-0.5 bg-foreground/40" />)}
        </div>
      </div>
      <div ref={labelRow} className="relative isolate mt-1 grid" style={{ gridTemplateColumns: `repeat(${options.length}, minmax(0, 1fr))` }}>
        {selected && <motion.div aria-hidden="true" data-discrete-slider-plate="true"
          data-disabled={disabled} className="pointer-events-none absolute inset-y-0 -z-10 rounded-[var(--surface-radius)] bg-secondary"
          style={{ left: plateLeft, width: plateWidth }} />}
        {options.map((option, index) => (
          <button key={option.value} type="button" data-discrete-slider-label="true"
            className="mx-0.5 min-h-[var(--control-height-xs)] min-w-0 cursor-pointer rounded-[var(--surface-radius)] px-1 py-1 text-xs font-medium whitespace-normal break-words leading-snug focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50"
            aria-pressed={index === previewIndex} disabled={disabled}
            title={option.description} onClick={() => onValueChange(option.value)}>
            {option.label}
          </button>
        ))}
      </div>
      {preview?.description && (
        <div data-discrete-slider-description="true">
          <div className="min-h-0 overflow-hidden">
            <p id={descriptionId} className="pt-2 text-xs leading-relaxed text-muted-foreground">{preview.description}</p>
          </div>
        </div>
      )}
    </div>
  )
}
