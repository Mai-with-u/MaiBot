import { Check, ChevronDown, X } from 'lucide-react'
import { AnimatePresence, motion } from 'motion/react'
import { useRef, useState } from 'react'
import type { ReactNode } from 'react'

import { ModernGlideHighlight, MotionNumber } from '@/components/motion'
import { Button } from '@/components/ui/button'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { useMotion } from '@/lib/motion'
import { cn } from '@/lib/utils'

export interface MultiSelectChipOption<T extends string = string> {
  value: T
  label: ReactNode
  count?: number
  swatchColor?: string
  ariaLabel?: string
  title?: string
  disabled?: boolean
}

export interface MultiSelectChipsProps<T extends string = string> {
  options: MultiSelectChipOption<T>[]
  selected: ReadonlySet<T>
  onToggle: (value: T) => void
  onSelectAll?: () => void
  onSelectNone?: () => void
  ariaLabel?: string
  mode?: 'chips' | 'popover'
  triggerLabel?: ReactNode
  size?: 'xs' | 'sm'
  className?: string
  'data-stats-switch'?: string
}

/**
 * 公共多选芯片控件（DesignBookmark Tag Input / Checkbox List 风格）。
 * - chips 模式：内联勾选胶囊，切换时以弹簧动画弹出/收起勾选图标，告别平铺无反馈按钮。
 * - popover 模式：紧凑计数器触发器 + 可删除已选标签 + 滑轨高亮多选弹层。
 */
export function MultiSelectChips<T extends string = string>({
  options,
  selected,
  onToggle,
  onSelectAll,
  onSelectNone,
  ariaLabel,
  mode = 'chips',
  triggerLabel,
  size = 'sm',
  className,
  'data-stats-switch': dataStatsSwitch,
}: MultiSelectChipsProps<T>) {
  const { enabled, transitions } = useMotion()
  const [open, setOpen] = useState(false)
  const listRef = useRef<HTMLDivElement | null>(null)
  const selectedOptions = options.filter((item) => selected.has(item.value))

  if (mode === 'popover') {
    return (
      <div
        role="group"
        aria-label={ariaLabel}
        data-multi-select-chips="popover"
        className={cn('inline-flex flex-wrap items-center gap-1.5', className)}
      >
        <Popover open={open} onOpenChange={setOpen}>
          <PopoverTrigger asChild>
            <Button
              type="button"
              variant="outline"
              size={size === 'xs' ? 'xs' : 'sm'}
              aria-expanded={open}
              className="group gap-1.5"
            >
              <span>{triggerLabel ?? ariaLabel ?? '已选'}</span>
              <span className="inline-flex items-center rounded-full bg-primary/12 px-1.5 py-0.5 text-xs font-medium text-primary tabular-nums">
                <MotionNumber value={selectedOptions.length} />/{options.length}
              </span>
              <ChevronDown
                strokeWidth={1.75}
                className={cn(
                  'size-3.5 shrink-0 text-muted-foreground/75 transition-transform duration-150',
                  open && 'rotate-180'
                )}
              />
            </Button>
          </PopoverTrigger>
          <PopoverContent align="start" className="w-64 p-1.5">
            {(onSelectAll || onSelectNone) && (
              <div className="mb-1 flex items-center justify-between border-b border-border/60 px-2 pb-1.5 text-xs">
                <span className="text-muted-foreground tabular-nums">
                  已选 <MotionNumber value={selectedOptions.length} />/{options.length}
                </span>
                <div className="flex items-center gap-1">
                  {onSelectAll && (
                    <Button type="button" variant="ghost" size="xs" onClick={onSelectAll}>
                      全选
                    </Button>
                  )}
                  {onSelectNone && (
                    <Button type="button" variant="ghost" size="xs" onClick={onSelectNone}>
                      全不选
                    </Button>
                  )}
                </div>
              </div>
            )}
            <div ref={listRef} className="relative max-h-64 space-y-0.5 overflow-y-auto">
              <ModernGlideHighlight
                containerRef={listRef}
                itemSelector="[data-multi-chip-row='true']"
              />
              {options.map((option) => {
                const active = selected.has(option.value)
                return (
                  <button
                    key={option.value}
                    type="button"
                    data-multi-chip-row="true"
                    aria-pressed={active}
                    disabled={option.disabled}
                    onClick={() => onToggle(option.value)}
                    className={cn(
                      'relative z-10 flex w-full cursor-pointer items-center gap-2 rounded-sm px-2 py-1.5 text-left text-xs transition-colors hover:bg-muted/60 disabled:pointer-events-none disabled:opacity-45',
                      active ? 'font-medium text-foreground' : 'text-muted-foreground'
                    )}
                  >
                    <span
                      aria-hidden="true"
                      className={cn(
                        'flex size-4 shrink-0 items-center justify-center rounded-[4px] border transition-colors',
                        active
                          ? 'border-primary bg-primary text-primary-foreground'
                          : 'border-input bg-background'
                      )}
                    >
                      <AnimatePresence initial={false}>
                        {active && (
                          <motion.span
                            key="check"
                            initial={enabled ? { scale: 0.4, opacity: 0 } : false}
                            animate={{ scale: 1, opacity: 1 }}
                            exit={enabled ? { scale: 0.4, opacity: 0 } : undefined}
                            transition={transitions.snappy}
                          >
                            <Check className="size-3" strokeWidth={2.5} />
                          </motion.span>
                        )}
                      </AnimatePresence>
                    </span>
                    {option.swatchColor && (
                      <span
                        aria-hidden="true"
                        className="size-2.5 shrink-0 rounded-full"
                        style={{ backgroundColor: option.swatchColor }}
                      />
                    )}
                    <span className="min-w-0 flex-1 truncate">{option.label}</span>
                    {option.count !== undefined && (
                      <span className="shrink-0 text-muted-foreground tabular-nums">
                        {option.count}
                      </span>
                    )}
                  </button>
                )
              })}
            </div>
          </PopoverContent>
        </Popover>
        <AnimatePresence initial={false}>
          {selectedOptions.length > 0 && selectedOptions.length <= 4 && (
            <div className="inline-flex flex-wrap items-center gap-1">
              {selectedOptions.map((option) => (
                <motion.span
                  key={option.value}
                  layout={enabled}
                  initial={enabled ? { opacity: 0, scale: 0.85 } : false}
                  animate={{ opacity: 1, scale: 1 }}
                  exit={enabled ? { opacity: 0, scale: 0.85 } : undefined}
                  transition={transitions.fast}
                  className="inline-flex h-7 items-center gap-1 rounded-full border border-primary/25 bg-primary/10 pl-2.5 pr-1 text-xs font-medium text-foreground"
                >
                  <span className="max-w-28 truncate">{option.label}</span>
                  <button
                    type="button"
                    aria-label={`移除 ${typeof option.label === 'string' ? option.label : option.value}`}
                    onClick={() => onToggle(option.value)}
                    className="inline-flex size-4 cursor-pointer items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-primary/20 hover:text-foreground"
                  >
                    <X className="size-3" />
                  </button>
                </motion.span>
              ))}
            </div>
          )}
        </AnimatePresence>
      </div>
    )
  }

  return (
    <div
      role="group"
      aria-label={ariaLabel}
      data-multi-select-chips="chips"
      data-stats-switch={dataStatsSwitch}
      className={cn('inline-flex flex-wrap items-center gap-1.5', className)}
    >
      {options.map((option) => {
        const active = selected.has(option.value)
        return (
          <motion.button
            key={option.value}
            type="button"
            layout={enabled ? 'position' : false}
            aria-pressed={active}
            aria-label={option.ariaLabel}
            title={option.title ?? option.ariaLabel}
            disabled={option.disabled}
            data-active={active ? 'true' : 'false'}
            onClick={() => onToggle(option.value)}
            whileTap={enabled ? { scale: 0.97 } : undefined}
            transition={transitions.fast}
            className={cn(
              'inline-flex cursor-pointer items-center whitespace-nowrap rounded-full border font-medium transition-colors duration-150 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-45',
              size === 'xs' ? 'h-7 gap-1 px-2.5 text-xs' : 'h-7.5 gap-1.5 px-3 text-xs',
              active
                ? 'border-primary/40 bg-primary/12 text-foreground'
                : 'border-border/70 bg-background/80 text-muted-foreground hover:border-border hover:text-foreground'
            )}
          >
            {option.swatchColor ? (
              <span
                aria-hidden="true"
                className={cn(
                  'size-2 shrink-0 rounded-full transition-opacity',
                  !active && 'opacity-35'
                )}
                style={{ backgroundColor: option.swatchColor }}
              />
            ) : (
              <AnimatePresence initial={false}>
                {active && (
                  <motion.span
                    key="check"
                    aria-hidden="true"
                    initial={enabled ? { width: 0, opacity: 0, scale: 0.5 } : false}
                    animate={{ width: 12, opacity: 1, scale: 1 }}
                    exit={enabled ? { width: 0, opacity: 0, scale: 0.5 } : undefined}
                    transition={transitions.snappy}
                    className="inline-flex shrink-0 items-center justify-center overflow-hidden text-primary"
                  >
                    <Check className="size-3" strokeWidth={2.4} />
                  </motion.span>
                )}
              </AnimatePresence>
            )}
            <span className={cn(!active && option.swatchColor && 'line-through opacity-65')}>
              {option.label}
            </span>
            {option.count !== undefined && (
              <span className="tabular-nums opacity-70">{option.count}</span>
            )}
          </motion.button>
        )
      })}
    </div>
  )
}
