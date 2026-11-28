import * as React from "react"
import * as SliderPrimitive from "@radix-ui/react-slider"

import { useMotion } from "@/lib/motion"
import { cn } from "@/lib/utils"

type SliderProps = React.ComponentPropsWithoutRef<typeof SliderPrimitive.Root> & {
  variant?: "value" | "default"
  valueFormat?: "fixed-1" | "fixed-2" | "fixed-3"
}

/**
 * 渲染滑块上展示的数值：两位及以上小数时，末位降为 50% 字号以压缩滑块宽度
 */
function renderSliderValue(
  value: number | undefined,
  valueFormat: SliderProps['valueFormat'],
): React.ReactNode {
  if (typeof value !== 'number' || !valueFormat) {
    return value
  }
  const decimals = valueFormat === 'fixed-3' ? 3 : valueFormat === 'fixed-2' ? 2 : 1
  const text = value.toFixed(decimals)
  // 一位小数本身已足够短，无需再用小号末位压缩
  if (decimals === 1 || !text.includes('.')) {
    return text
  }
  return (
    <>
      {text.slice(0, -1)}
      <span className="text-[0.5em]" data-dashboard-slider-value-tail="true">
        {text.slice(-1)}
      </span>
    </>
  )
}

const Slider = React.forwardRef<
  React.ElementRef<typeof SliderPrimitive.Root>,
  SliderProps
>(({
  className,
  value,
  defaultValue,
  variant = "default",
  valueFormat,
  onValueChange,
  onPointerDown,
  onPointerMove,
  onPointerUp,
  onPointerCancel,
  ...props
}, ref) => {
  const { style: motionStyle, enabled: motionEnabled } = useMotion()
  const jellyActive = motionStyle === 'modern' && motionEnabled
  const [dragging, setDragging] = React.useState(false)
  const lastXRef = React.useRef<number | null>(null)
  const hasDashboardValue = variant === 'value'
  // 非受控滑条也要同步拖块上的读数，不能一直显示初始值。
  const [internalValues, setInternalValues] = React.useState(defaultValue ?? [props.min ?? 0])
  const currentValues = value ?? internalValues
  const thumbCount = currentValues.length

  const handleValueChange = (nextValues: number[]) => {
    if (value === undefined) setInternalValues(nextValues)
    onValueChange?.(nextValues)
  }

  const resetJelly = (element: HTMLElement) => {
    lastXRef.current = null
    setDragging(false)
    element.style.removeProperty('--slider-jelly-x')
    element.style.removeProperty('--slider-jelly-y')
  }

  return (
    <SliderPrimitive.Root
      ref={ref}
      className={cn(
        "relative flex w-full touch-none select-none items-center",
        className
      )}
      value={value}
      defaultValue={defaultValue}
      onValueChange={handleValueChange}
      onPointerDown={(event) => {
        if (jellyActive && !props.disabled) {
          lastXRef.current = event.clientX
          setDragging(true)
        }
        onPointerDown?.(event)
      }}
      onPointerMove={(event) => {
        if (jellyActive && dragging && lastXRef.current !== null) {
          const delta = Math.abs(event.clientX - lastXRef.current)
          lastXRef.current = event.clientX
          const stretch = Math.min(0.18, delta * 0.012)
          event.currentTarget.style.setProperty('--slider-jelly-x', (1.08 + stretch).toFixed(3))
          event.currentTarget.style.setProperty('--slider-jelly-y', (0.94 - stretch * 0.45).toFixed(3))
        }
        onPointerMove?.(event)
      }}
      onPointerUp={(event) => {
        if (dragging) resetJelly(event.currentTarget)
        onPointerUp?.(event)
      }}
      onPointerCancel={(event) => {
        if (dragging) resetJelly(event.currentTarget)
        onPointerCancel?.(event)
      }}
      data-dashboard-slider={hasDashboardValue ? 'config' : 'default'}
      data-dashboard-slider-value-format={valueFormat}
      data-slider-dragging={jellyActive && dragging ? 'true' : undefined}
      {...props}
    >
      <SliderPrimitive.Track
        data-dashboard-slider-track="true"
        className={cn(
          "relative h-1.5 w-full grow overflow-hidden rounded-full bg-primary/20",
          hasDashboardValue && "h-3"
        )}
      >
        <SliderPrimitive.Range
          data-dashboard-slider-range="true"
          className="absolute h-full bg-primary"
        />
      </SliderPrimitive.Track>
      {Array.from({ length: Math.max(1, thumbCount) }).map((_, index) => (
        <SliderPrimitive.Thumb
          key={index}
          aria-label={props["aria-label"]}
          aria-labelledby={props["aria-labelledby"]}
          aria-valuetext={props["aria-valuetext"]}
          aria-describedby={props["aria-describedby"]}
          data-dashboard-slider-thumb="true"
          className={cn(
            "block h-4 w-4 rounded-full border border-primary/50 bg-background shadow transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50",
            hasDashboardValue &&
              "inline-flex h-7 min-w-9 items-center justify-center rounded-full border-2 border-primary bg-background px-1 text-xs font-semibold leading-none text-foreground"
          )}
        >
          {hasDashboardValue && (
            <span
              data-dashboard-slider-value="true"
              className="pointer-events-none select-none"
            >
              {renderSliderValue(currentValues[index], valueFormat)}
            </span>
          )}
        </SliderPrimitive.Thumb>
      ))}
    </SliderPrimitive.Root>
  )
})
Slider.displayName = SliderPrimitive.Root.displayName

export { Slider }
