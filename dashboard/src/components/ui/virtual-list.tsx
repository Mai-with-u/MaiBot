import { useVirtualizer } from '@tanstack/react-virtual'
import { useCallback, useRef, type ReactNode } from 'react'

import { cn } from '@/lib/utils'

/** 长列表只挂载可视区域附近的行，测量实际行高以适配不同主题和字号。 */
export function VirtualList<T>({
  items,
  getKey,
  estimateSize,
  renderItem,
  className,
}: {
  items: T[]
  getKey: (item: T) => string
  estimateSize: (item: T) => number
  renderItem: (item: T) => ReactNode
  className?: string
}) {
  const viewport = useRef<HTMLDivElement>(null)
  const itemKey = useCallback((index: number) => getKey(items[index]), [getKey, items])
  const itemSize = useCallback((index: number) => estimateSize(items[index]), [estimateSize, items])
  const virtualizer = useVirtualizer<HTMLDivElement, HTMLDivElement>({
    count: items.length,
    getScrollElement: () => viewport.current,
    getItemKey: itemKey,
    estimateSize: itemSize,
    overscan: 5,
  })
  return (
    <div ref={viewport} className={cn('min-h-0 flex-1 overflow-y-auto overscroll-contain', className)}>
      <div className="relative w-full" style={{ height: virtualizer.getTotalSize() }}>
        {virtualizer.getVirtualItems().map((row) => (
          <div
            key={row.key}
            data-index={row.index}
            ref={virtualizer.measureElement}
            className="absolute top-0 left-0 w-full"
            style={{ transform: `translateY(${row.start}px)` }}
          >
            {renderItem(items[row.index])}
          </div>
        ))}
      </div>
    </div>
  )
}
