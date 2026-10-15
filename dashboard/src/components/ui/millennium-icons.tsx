import { MILLENNIUM_ICON_STROKE_WIDTH } from './millennium-icon-set'
import type { MillenniumIconDef } from './millennium-icon-set'

interface MillenniumIconProps {
  icon: MillenniumIconDef
  className?: string
  color?: string
  size?: number | string
}

/** 按 millennium-icon-set.ts 里登记的画法直接画出一个千禧图标。 */
export function MillenniumIcon({ icon, className, color, size = 20 }: MillenniumIconProps) {
  const paint = icon.filled
    ? { fill: color ?? 'currentColor' }
    : {
        fill: 'none',
        stroke: color ?? 'currentColor',
        strokeWidth: icon.strokeWidth ?? MILLENNIUM_ICON_STROKE_WIDTH,
        strokeLinecap: 'square' as const,
        strokeLinejoin: 'miter' as const,
      }

  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 24 24"
      width={size}
      height={size}
      aria-hidden="true"
      data-millennium-icon="true"
      className={className}
      {...paint}
    >
      <path d={icon.path} fillRule={icon.evenOdd ? 'evenodd' : undefined} />
    </svg>
  )
}
