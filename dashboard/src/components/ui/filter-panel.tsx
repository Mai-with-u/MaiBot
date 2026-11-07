import type { ReactNode } from 'react'

import { Button } from '@/components/ui/button'
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
  title,
  options,
  selected,
  onToggle,
  onSelectAll,
  onSelectNone,
  className,
  children,
}: FilterPanelProps) {
  const selectedCount = options.filter((option) => selected.has(option.id)).length
  return (
    <div id={id} data-filter-panel="true" className={cn('flex flex-col gap-2', className)}>
      <div className="flex items-center gap-2 text-xs">
        <span className="font-semibold">{title}</span>
        <span className="text-muted-foreground font-mono">
          已选 {selectedCount}/{options.length}
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
              className="max-w-full"
            >
              <span className="block max-w-40 truncate">{option.label}</span>
              {option.count !== undefined && (
                <span className="font-mono font-normal opacity-70">{option.count}</span>
              )}
            </Button>
          )
        })}
      </div>
      {children}
    </div>
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
      <span className="font-mono font-normal">
        {selectedCount === total ? `全部 ${total}` : `${selectedCount}/${total}`}
      </span>
      <svg
        aria-hidden="true"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.2"
        strokeLinecap="square"
        className={cn('transition-transform', open && 'rotate-180')}
      >
        <path d="M6 9l6 6 6-6" />
      </svg>
    </Button>
  )
}
