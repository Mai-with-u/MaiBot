import { Check } from 'lucide-react'
import { AnimatePresence, motion } from 'motion/react'
import type { ReactNode } from 'react'

import { MotionNumber } from '@/components/motion'
import { Button } from '@/components/ui/button'
import { useMotion } from '@/lib/motion'
import { cn } from '@/lib/utils'
import '@/styles/filter-panel.css'

export interface FilterPanelOption {
  id: string
  label: ReactNode
  /** 选项旁的数量读数，可选 */
  count?: number
  title?: string
}

interface FilterPanelProps {
  id?: string
  /** 控制展开/收起以播放平滑退场动画；未传入时默认展开。 */
  open?: boolean
  title: ReactNode
  options: FilterPanelOption[]
  /** 当前勾选的选项 id */
  selected: ReadonlySet<string>
  onToggle: (id: string) => void
  onSelectAll: () => void
  onSelectNone: () => void
  className?: string
  children?: ReactNode
}

/** 工具行下方展开的多选筛选面板：铺在外壳上，上沿一道刻线，不另加底色。 */
export function FilterPanel({
  id,
  open = true,
  title,
  options,
  selected,
  onToggle,
  onSelectAll,
  onSelectNone,
  className,
  children,
}: FilterPanelProps) {
  const { enabled, profile, transitions } = useMotion()
  const selectedCount = options.filter((option) => selected.has(option.id)).length
  return (
    <AnimatePresence>
      {open && (
        <motion.div
          key={id ?? 'filter-panel'}
          id={id}
          data-filter-panel="true"
          className={cn('flex flex-col gap-2', className)}
          initial={enabled ? { opacity: 0, y: -profile.distances.overlayY } : false}
          animate={{ opacity: 1, y: 0 }}
          exit={
            enabled
              ? { opacity: 0, y: -profile.distances.overlayY, transition: transitions.exit }
              : undefined
          }
          transition={transitions.fast}
        >
          <div className="flex items-center gap-2 text-xs">
            <span className="font-semibold">{title}</span>
            <span className="text-muted-foreground tabular-nums">
              已选 <MotionNumber value={selectedCount} />/{options.length}
            </span>
            <span className="flex-1" />
            <Button type="button" variant="outline" size="xs" onClick={onSelectAll}>
              全选
            </Button>
            <Button type="button" variant="outline" size="xs" onClick={onSelectNone}>
              全不选
            </Button>
          </div>
          <div className="flex flex-wrap gap-1.5">
            {options.map((option) => {
              const on = selected.has(option.id)
              return (
                <Button
                  key={option.id}
                  type="button"
                  variant="outline"
                  size="xs"
                  aria-pressed={on}
                  title={option.title}
                  onClick={() => onToggle(option.id)}
                  className="max-w-full gap-1.5 rounded-full"
                >
                  <AnimatePresence initial={false}>
                    {on && (
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
                  <span className="block max-w-40 truncate">{option.label}</span>
                  {option.count !== undefined && (
                    <span className="font-normal tabular-nums opacity-70">{option.count}</span>
                  )}
                </Button>
              )
            })}
          </div>
          {children}
        </motion.div>
      )}
    </AnimatePresence>
  )
}

/** 工具行里展开筛选面板的切换键，显示已选数量。 */
export function FilterPanelToggle({
  label,
  open,
  selectedCount,
  total,
  controls,
  onOpenChange,
}: {
  label: ReactNode
  open: boolean
  selectedCount: number
  total: number
  controls?: string
  onOpenChange: (open: boolean) => void
}) {
  return (
    <Button
      type="button"
      variant="outline"
      size="sm"
      aria-expanded={open}
      aria-pressed={open}
      aria-controls={controls}
      onClick={() => onOpenChange(!open)}
      data-filter-panel-toggle="true"
    >
      {label}
      <span className="font-normal tabular-nums">
        {selectedCount === total ? `全部 ${total}` : `${selectedCount}/${total}`}
      </span>
      <svg
        aria-hidden="true"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.2"
        strokeLinecap="square"
        className={cn(
          'transition-transform [transition-duration:var(--motion-duration-fast)] [transition-timing-function:var(--motion-ease)] motion-reduce:transition-none',
          open && 'rotate-180'
        )}
      >
        <path d="M6 9l6 6 6-6" />
      </svg>
    </Button>
  )
}
