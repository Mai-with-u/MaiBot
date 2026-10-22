import { motion, useMotionValue, useReducedMotion, useSpring, useTransform } from 'motion/react'
import { useEffect, useId, useRef, useState } from 'react'

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
  const pointerPosition = useRef<number | null>(null)
  const previewIndex = Math.round(position ?? selectedIndex)
  const preview = options[previewIndex]
  const reduceMotion = useReducedMotion()
  const targetPosition = useMotionValue(selectedIndex)
  const springPosition = useSpring(targetPosition, { stiffness: 460, damping: 38, mass: 0.6 })
  const animatedPosition = reduceMotion ? targetPosition : springPosition
  // 底板中心连续跟随拖杆；跨越两档时拉宽，到达刻度时收回，点选也走同一动画。
  const plateLeft = useTransform(animatedPosition, current => {
    const stretch = reduceMotion ? 0 : Math.sin(Math.PI * (current - Math.floor(current))) * 0.28
    return `calc(${(current - stretch / 2) * 100 / options.length}% + 2px)`
  })
  const plateWidth = useTransform(animatedPosition, current => {
    const stretch = reduceMotion ? 0 : Math.sin(Math.PI * (current - Math.floor(current))) * 0.28
    return `calc(${(1 + stretch) * 100 / options.length}% - 4px)`
  })
  useEffect(() => {
    targetPosition.set(position ?? selectedIndex)
  }, [position, selectedIndex, targetPosition])
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
    <div data-dashboard-discrete-slider="true" data-dragging={position !== null} className={cn('min-w-0 py-2', className)}>
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
      <div className="relative isolate mt-1 grid" style={{ gridTemplateColumns: `repeat(${options.length}, minmax(0, 1fr))` }}>
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
      {preview?.description && <p id={descriptionId} className="mt-2 text-xs leading-relaxed text-muted-foreground">{preview.description}</p>}
    </div>
  )
}
