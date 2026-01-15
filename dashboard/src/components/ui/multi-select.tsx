/**
 * 多选下拉框组件
 * 支持搜索、单击选择、标签展示、拖动排序
 */

import * as React from 'react'
import { X, Check, ChevronsUpDown, GripVertical } from 'lucide-react'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from '@/components/ui/command'
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover'
import { Badge } from '@/components/ui/badge'
import {
  DndContext,
  closestCenter,
  KeyboardSensor,
  PointerSensor,
  useSensor,
  useSensors,
} from '@dnd-kit/core'
import type { DragEndEvent } from '@dnd-kit/core'
import {
  arrayMove,
  SortableContext,
  sortableKeyboardCoordinates,
  useSortable,
  horizontalListSortingStrategy,
} from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'

export interface MultiSelectOption {
  label: string
  value: string
}

interface MultiSelectProps {
  options: MultiSelectOption[]
  selected: string[]
  onChange: (values: string[]) => void
  placeholder?: string
  emptyText?: string
  className?: string
}

// 可排序的标签组件
function SortableBadge({
  value,
  label,
  onRemove,
}: {
  value: string
  label: string
  onRemove: (value: string) => void
}) {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: value })

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.5 : 1,
  }

  // 处理删除按钮点击，阻止事件冒泡和默认行为
  const handleRemoveClick = (e: React.MouseEvent) => {
    e.preventDefault()
    e.stopPropagation()
    onRemove(value)
  }

  // 阻止删除按钮上的指针事件被 DndContext 捕获
  const handleRemovePointerDown = (e: React.PointerEvent) => {
    e.stopPropagation()
  }

  return (
    <div
      ref={setNodeRef}
      style={style}
      className={cn(
        'inline-flex items-center gap-1',
        isDragging && 'shadow-lg'
      )}
    >
      <Badge
        variant="secondary"
        className="cursor-move hover:bg-secondary/80 flex items-center gap-1"
      >
        <div
          {...attributes}
          {...listeners}
          className="cursor-grab active:cursor-grabbing flex items-center"
        >
          <GripVertical className="h-3 w-3 text-muted-foreground" />
        </div>
        <span>{label}</span>
        <span
          role="button"
          tabIndex={0}
          className="ml-1 rounded-sm hover:bg-destructive/20 focus:outline-none focus:ring-1 focus:ring-destructive cursor-pointer"
          onClick={handleRemoveClick}
          onPointerDown={handleRemovePointerDown}
          onMouseDown={(e) => e.stopPropagation()}
          onKeyDown={(e) => {
            if (e.key === 'Enter' || e.key === ' ') {
              e.preventDefault()
              handleRemoveClick(e as any)
            }
          }}
        >
          <X
            className="h-3 w-3 hover:text-destructive"
            strokeWidth={2}
            fill="none"
          />
        </span>
      </Badge>
    </div>
  )
}

export function MultiSelect({
  options,
  selected,
  onChange,
  placeholder = '选择选项...',
  emptyText = '未找到选项',
  className,
}: MultiSelectProps) {
  const [open, setOpen] = React.useState(false)

  const sensors = useSensors(
    useSensor(PointerSensor, {
      activationConstraint: {
        distance: 8, // 拖动至少8px才触发，避免与点击冲突
      },
    }),
    useSensor(KeyboardSensor, {
      coordinateGetter: sortableKeyboardCoordinates,
    })
  )

  const handleSelect = (value: string) => {
    if (selected.includes(value)) {
      // 取消选择
      onChange(selected.filter((item) => item !== value))
    } else {
      // 添加选择
      onChange([...selected, value])
    }
  }

  const handleRemove = (value: string) => {
    onChange(selected.filter((item) => item !== value))
  }

  const handleDragEnd = (event: DragEndEvent) => {
    const { active, over } = event

    if (over && active.id !== over.id) {
      const oldIndex = selected.indexOf(active.id as string)
      const newIndex = selected.indexOf(over.id as string)

      onChange(arrayMove(selected, oldIndex, newIndex))
    }
  }

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          variant="outline"
          role="combobox"
          aria-expanded={open}
          className={cn('w-full justify-between min-h-10 h-auto', className)}
        >
          <DndContext
            sensors={sensors}
            collisionDetection={closestCenter}
            onDragEnd={handleDragEnd}
          >
            <SortableContext
              items={selected}
              strategy={horizontalListSortingStrategy}
            >
              <div className="flex gap-1 flex-wrap flex-1">
                {selected.length === 0 ? (
                  <span className="text-muted-foreground">{placeholder}</span>
                ) : (
                  selected.map((value) => {
                    const option = options.find((opt) => opt.value === value)
                    return (
                      <SortableBadge
                        key={value}
                        value={value}
                        label={option?.label || value}
                        onRemove={handleRemove}
                      />
                    )
                  })
                )}
              </div>
            </SortableContext>
          </DndContext>
          <ChevronsUpDown className="ml-2 h-4 w-4 shrink-0 opacity-50" strokeWidth={2} fill="none" />
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-full p-0" align="start">
        <Command>
          <CommandInput placeholder="搜索..." className="h-9" />
          <CommandList>
            <CommandEmpty>{emptyText}</CommandEmpty>
            <CommandGroup>
              {options.map((option) => {
                const isSelected = selected.includes(option.value)
                return (
                  <CommandItem
                    key={option.value}
                    value={option.value}
                    onSelect={() => handleSelect(option.value)}
                  >
                    <div
                      className={cn(
                        'mr-2 flex h-4 w-4 items-center justify-center rounded-sm border border-primary',
                        isSelected
                          ? 'bg-primary text-primary-foreground'
                          : 'opacity-50 [&_svg]:invisible'
                      )}
                    >
                      <Check className="h-3 w-3" strokeWidth={2} fill="none" />
                    </div>
                    <span>{option.label}</span>
                  </CommandItem>
                )
              })}
            </CommandGroup>
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  )
}
