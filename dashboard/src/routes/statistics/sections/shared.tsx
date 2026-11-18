import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'

import { Button } from '@/components/ui/button'

import { formatPercent, type SummaryDelta } from '../statistics-model'

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
}: {
  label: string
  value: T
  options: SwitchOption<T>[]
  onChange: (value: T) => void
  /** 多选模式：按下状态取自该集合，value 被忽略 */
  multiple?: Set<T>
}) {
  return (
    <div role="group" aria-label={label} data-stats-switch="true">
      {options.map((option) => (
        <Button
          key={option.value}
          type="button"
          size="sm"
          variant="outline"
          aria-pressed={multiple ? multiple.has(option.value) : option.value === value}
          aria-label={option.ariaLabel}
          title={option.ariaLabel}
          disabled={option.disabled}
          onClick={() => onChange(option.value)}
        >
          {option.label}
        </Button>
      ))}
    </div>
  )
}

/** 迷你柱状走势，只表达形状，不带坐标 */
export function SparkBars({ values, label }: { values: number[]; label: string }) {
  const maximum = Math.max(0, ...values)
  return (
    <div data-stats-spark="true" role="img" aria-label={label}>
      {values.map((value, index) => (
        <span key={index} style={{ height: maximum > 0 ? `${(value / maximum) * 100}%` : '1px' }} />
      ))}
    </div>
  )
}
