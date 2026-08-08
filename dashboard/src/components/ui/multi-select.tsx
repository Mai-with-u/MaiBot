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
  compact?: boolean
  disabled?: boolean
  singleSelect?: boolean
}

// 可排序的标签组件
function SortableBadge({
  value,
  label,
  onRemove,
  compact = false,
  disabled = false,
}: {
  value: string
  label: string
  onRemove: (value: string) => void
  compact?: boolean
  disabled?: boolean
}) {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: value, disabled })

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.5 : 1,
  }

  // 处理删除按钮点击，阻止事件冒泡和默认行为
  const handleRemoveClick = (e: React.MouseEvent | React.KeyboardEvent) => {
    e.preventDefault()
    e.stopPropagation()
    if (disabled) return
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
        className={cn(
          'flex items-center gap-1 hover:bg-secondary/80',
          !disabled && 'cursor-move',
          disabled && 'opacity-60',
          compact && 'min-h-6 max-w-[calc(100vw-5rem)] px-1.5 py-0 text-[11px] leading-none sm:max-w-full'
        )}
      >
        <div
          {...attributes}
          {...listeners}
          className={cn(
            'flex items-center',
            !disabled && 'cursor-grab active:cursor-grabbing',
            disabled && 'cursor-not-allowed'
          )}
        >
          <GripVertical className="h-3 w-3 text-muted-foreground" />
        </div>
        <span className={cn(compact && 'min-w-0 truncate')}>{label}</span>
        <span
          role="button"
          tabIndex={0}
          className={cn(
            'ml-1 inline-flex shrink-0 cursor-pointer items-center justify-center rounded-sm hover:bg-destructive/20 focus:outline-none focus:ring-1 focus:ring-destructive',
            compact ? 'h-4 w-4' : 'h-5 w-5'
          )}
          onClick={handleRemoveClick}
          onPointerDown={handleRemovePointerDown}
          onMouseDown={(e) => e.stopPropagation()}
          aria-disabled={disabled}
          onKeyDown={(e) => {
            if (e.key === 'Enter' || e.key === ' ') {
              e.preventDefault()
              handleRemoveClick(e)
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
  compact = false,
  disabled = false,
  singleSelect = false,
}: MultiSelectProps) {
  const [open, setOpen] = React.useState(false)

  React.useEffect(() => {
    if (disabled && open) {
      setOpen(false)
    }
  }, [disabled, open])

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
    if (disabled) return
    if (selected.includes(value)) {
      // 取消选择
      onChange(selected.filter((item) => item !== value))
    } else if (singleSelect) {
      // 单选模式仅限制 WebUI 操作，不裁剪配置文件中已有的多选值
      onChange([value])
      setOpen(false)
    } else {
      // 添加选择
      onChange([...selected, value])
    }
  }

  const handleRemove = (value: string) => {
    if (disabled) return
    onChange(selected.filter((item) => item !== value))
  }

  const handleDragEnd = (event: DragEndEvent) => {
    if (disabled) return
    const { active, over } = event

    if (over && active.id !== over.id) {
      const oldIndex = selected.indexOf(active.id as string)
      const newIndex = selected.indexOf(over.id as string)

      onChange(arrayMove(selected, oldIndex, newIndex))
    }
  }

  return (
    <Popover
      open={disabled ? false : open}
      onOpenChange={(nextOpen) => {
        if (!disabled) {
          setOpen(nextOpen)
        }
      }}
    >
      <PopoverTrigger asChild>
        <Button
          variant="outline"
          role="combobox"
          aria-expanded={open}
          disabled={disabled}
          className={cn(
            'h-auto w-full justify-between',
            compact ? 'min-h-9 px-2 py-1.5' : 'min-h-10',
            className
          )}
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
              <div className="flex flex-1 flex-wrap gap-1">
                {selected.length === 0 ? (
                  <span className={cn('text-muted-foreground', compact && 'text-sm')}>{placeholder}</span>
                ) : (
                  selected.map((value) => {
                    const option = options.find((opt) => opt.value === value)
                    return (
                      <SortableBadge
                        key={value}
                        value={value}
                        label={option?.label || value}
                        onRemove={handleRemove}
                        compact={compact}
                        disabled={disabled}
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
                    disabled={disabled}
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
