import { closestCenter, DndContext, KeyboardSensor, PointerSensor, useSensor, useSensors } from '@dnd-kit/core'
import { arrayMove, rectSortingStrategy, SortableContext, sortableKeyboardCoordinates, useSortable } from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import type { ReactNode } from 'react'

import { cn } from '@/lib/utils'

interface SortableCardItem {
  id: string
  label: string
  icon?: ReactNode
}

function SortableCard({ item }: { item: SortableCardItem }) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id: item.id })
  return (
    <button
      ref={setNodeRef}
      type="button"
      style={{ transform: CSS.Translate.toString(transform), transition }}
      className={cn(
        'relative flex min-h-20 touch-none flex-col items-center justify-center gap-2 rounded-md border bg-card p-2 text-sm text-card-foreground cursor-grab active:cursor-grabbing focus-visible:outline-2 focus-visible:outline-ring',
        isDragging && 'z-10 shadow-lg ring-2 ring-ring'
      )}
      {...attributes}
      {...listeners}
    >
      {item.icon}
      <span className="w-full truncate text-center" title={item.label}>{item.label}</span>
    </button>
  )
}

/** 可用鼠标、触屏或键盘排序的卡片网格，顺序按从左到右、从上到下排列。 */
export function SortableCardGrid({ items, onReorder, label }: {
  items: SortableCardItem[]
  onReorder: (ids: string[]) => void
  label: string
}) {
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates })
  )
  return (
    <DndContext
      sensors={sensors}
      collisionDetection={closestCenter}
      onDragEnd={({ active, over }) => {
        if (!over || active.id === over.id) return
        const ids = items.map((item) => item.id)
        onReorder(arrayMove(ids, ids.indexOf(String(active.id)), ids.indexOf(String(over.id))))
      }}
    >
      <SortableContext items={items.map((item) => item.id)} strategy={rectSortingStrategy}>
        <div className="grid grid-cols-4 gap-2 p-1" role="group" aria-label={label}>
          {items.map((item) => <SortableCard key={item.id} item={item} />)}
        </div>
      </SortableContext>
    </DndContext>
  )
}
