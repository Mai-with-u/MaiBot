import { cn } from '@/lib/utils'

/** 多段比例显示，与单值 Progress 分开；每一段保留名称供鼠标和读屏识别。 */
export function SegmentedMeter({
  segments,
  total,
  label,
  separated = false,
  className,
}: {
  segments: { label: string; value: number; className: string }[]
  total?: number
  label: string
  separated?: boolean
  className?: string
}) {
  const values = segments.map((segment) => Math.max(0, segment.value))
  const sum = values.reduce((value, next) => value + next, 0)
  const maximum = Math.max(total ?? sum, sum)
  return (
    <div
      data-dashboard-segmented-meter="true"
      data-separated={separated}
      role="img"
      aria-label={`${label}：${segments.map((segment, index) => `${segment.label} ${values[index]}`).join('，')}`}
      className={cn('bg-muted flex h-3 w-full overflow-hidden', className)}
    >
      {segments.map((segment, index) => (
        <span
          key={index}
          title={segment.label}
          className={cn('h-full min-w-0', segment.className)}
          style={{ width: `${maximum > 0 ? (values[index] / maximum) * 100 : 0}%` }}
        />
      ))}
    </div>
  )
}
