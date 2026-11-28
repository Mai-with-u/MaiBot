import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'

import { MultiSelectChips } from '@/components/ui/multi-select-chips'
import { PillNav } from '@/components/ui/pill-nav'
import { cn } from '@/lib/utils'

import { formatPercent, type SummaryDelta } from '../statistics-model'

/** 统一图表提示框外壳，确保现代主题毛玻璃表面样式一致生效 */
export function ChartTooltipBox({
  children,
  className,
}: {
  children: ReactNode
  className?: string
}) {
  return (
    <div
      data-dashboard-chart-tooltip="true"
      className={cn(
        'border-border/50 bg-background text-foreground rounded-lg border px-2.5 py-1.5 text-xs shadow-xl',
        className
      )}
    >
      {children}
    </div>
  )
}

export function EmptyState({ children }: { children?: ReactNode }) {
  const { t } = useTranslation()
  return (
    <div className="flex min-h-24 flex-1 items-center justify-center text-sm opacity-70">
      {children ?? t('statisticsPage.empty')}
    </div>
  )
}

export function Swatch({ color }: { color: string }) {
  return <span data-stats-swatch="true" aria-hidden="true" style={{ background: color }} />
}

/** 环比标记：上升且上升是坏事时用告警色；上一周期为 0 时显示「新增」 */
export function DeltaBadge({ delta }: { delta: SummaryDelta | null }) {
  const { t } = useTranslation()
  if (!delta) return null
  if (delta.value === null) {
    return (
      <span data-stats-delta="flat" title={t('statisticsPage.delta.noBaseline')}>
        {t('statisticsPage.delta.new')}
      </span>
    )
  }
  const flat = Math.abs(delta.value) < 0.005
  const tone = flat ? 'flat' : delta.value > 0 === delta.upIsBad ? 'bad' : 'good'
  const arrow = flat ? '' : delta.value > 0 ? '↑' : '↓'
  return (
    <span data-stats-delta={tone} title={t('statisticsPage.delta.vsPrevious')}>
      {arrow}
      {formatPercent(Math.abs(delta.value))}
    </span>
  )
}

export interface SwitchOption<T extends string> {
  value: T
  label: string
  /** 无障碍名称，缺省为 label */
  ariaLabel?: string
  disabled?: boolean
}

/** 分隔线右端的一组选择键，选中项保持按下 */
export function OptionSwitch<T extends string>({
  label,
  value,
  options,
  onChange,
  multiple,
  className,
}: {
  label: string
  value: T
  options: SwitchOption<T>[]
  onChange: (value: T) => void
  /** 多选模式：按下状态取自该集合，value 被忽略 */
  multiple?: Set<T>
  className?: string
}) {
  if (multiple) {
    return (
      <MultiSelectChips
        mode="chips"
        size="xs"
        ariaLabel={label}
        options={options}
        selected={multiple}
        onToggle={onChange}
        data-stats-switch="true"
        className={className}
      />
    )
  }

  return (
    <PillNav
      variant="segmented"
      size="xs"
      scrollable
      ariaLabel={label}
      value={value}
      options={options}
      onChange={onChange}
      data-stats-switch="true"
      className={className}
    />
  )
}

/** 迷你柱状走势，只表达形状，不带坐标 */
export function SparkBars({ values, label }: { values: number[]; label: string }) {
  const maximum = Math.max(0, ...values)
  return (
    <div data-stats-spark="true" role="img" aria-label={label}>
      {values.map((value, index) => (
        <span
          key={index}
          className="rounded-t-[1px] opacity-80 transition-[height,opacity] duration-200 ease-out hover:opacity-100"
          style={{ height: maximum > 0 ? `${(value / maximum) * 100}%` : '1px' }}
        />
      ))}
    </div>
  )
}
