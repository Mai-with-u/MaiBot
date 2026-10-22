import { ChevronDown, ChevronUp } from 'lucide-react'

import { Button } from './button'

/** 公共排序按钮：保持固定占位，边界项禁用，不挤动旁边的显示开关。 */
export function OrderControls({
  label,
  index,
  count,
  onMove,
  upLabel,
  downLabel,
}: {
  label: string
  index: number
  count: number
  onMove: (delta: -1 | 1) => void
  upLabel: string
  downLabel: string
}) {
  return (
    <div className="flex shrink-0 gap-1">
      <Button
        type="button"
        variant="outline"
        size="icon-sm"
        disabled={index === 0}
        aria-label={`${label}: ${upLabel}`}
        title={upLabel}
        onClick={() => onMove(-1)}
      >
        <ChevronUp />
      </Button>
      <Button
        type="button"
        variant="outline"
        size="icon-sm"
        disabled={index === count - 1}
        aria-label={`${label}: ${downLabel}`}
        title={downLabel}
        onClick={() => onMove(1)}
      >
        <ChevronDown />
      </Button>
    </div>
  )
}
