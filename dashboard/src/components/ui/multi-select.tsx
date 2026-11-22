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
  group?: string
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
  sortable?: boolean
  singleSelect?: boolean
}

// 可排序的标签组件
function SortableBadge({
  value,
  label,
  onRemove,
  sortable,
  compact = false,
  disabled = false,
}: {
  value: string
  label: string
  sortable: boolean
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
  } = useSortable({ id: value, disabled: disabled || !sortable })

  const style = {
    // 标签宽度随名称变化，排序时只平移，避免按目标标签尺寸缩放文字。
    transform: CSS.Translate.toString(transform),
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
        'inline-flex min-w-0 max-w-full items-center gap-1',
        isDragging && 'shadow-lg'
      )}
    >
      <Badge
        variant="secondary"
        className={cn(
          'flex min-w-0 max-w-full items-center gap-1 hover:bg-secondary/80',
          sortable && !disabled && 'cursor-move',
          disabled && 'opacity-60',
          compact && 'min-h-6 max-w-[calc(100vw-5rem)] px-1.5 py-0 text-[11px] leading-none sm:max-w-full'
        )}
      >
        {sortable && (
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
        )}
        <span className="min-w-0 truncate" title={label}>{label}</span>
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
  sortable = true,
  singleSelect = false,
}: MultiSelectProps) {
  const [open, setOpen] = React.useState(false)
  const optionGroups = React.useMemo(() => {
    const groups = new Map<string | undefined, MultiSelectOption[]>()
    for (const option of options) {
      const group = groups.get(option.group)
      if (group) group.push(option)
      else groups.set(option.group, [option])
    }
    return Array.from(groups)
  }, [options])

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
    <div className={cn('grid w-full min-w-0 gap-2', className)}>
      {selected.length > 0 && (
        <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
          <SortableContext items={selected} strategy={horizontalListSortingStrategy}>
            <div
              role="group"
              aria-label="已选项"
              className="flex max-h-40 min-w-0 flex-wrap gap-1.5 overflow-y-auto rounded-md border bg-muted/20 p-2"
            >
              {selected.map((value) => (
                <SortableBadge
                  key={value}
                  value={value}
                  label={options.find((option) => option.value === value)?.label || value}
                  onRemove={handleRemove}
                  sortable={sortable}
                  compact={compact}
                  disabled={disabled}
                />
              ))}
            </div>
          </SortableContext>
        </DndContext>
      )}
      <Popover open={!disabled && open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <Button
            type="button"
            variant="outline"
            size={compact ? 'sm' : 'default'}
            role="combobox"
            aria-expanded={open}
            disabled={disabled}
            className="w-full min-w-0 justify-between"
          >
            <span className="text-muted-foreground truncate">{placeholder}</span>
            <ChevronsUpDown className="h-4 w-4 shrink-0 opacity-50" />
          </Button>
        </PopoverTrigger>
        <PopoverContent className="w-[var(--radix-popover-trigger-width)] max-w-[var(--radix-popover-content-available-width)] p-0" align="start">
          <Command>
            <CommandInput placeholder="搜索..." className="h-9" />
            <CommandList>
              <CommandEmpty>{emptyText}</CommandEmpty>
              {optionGroups.map(([group, groupOptions]) => (
                <CommandGroup key={group ?? ''} heading={group}>
                  {groupOptions.map((option) => {
                    const isSelected = selected.includes(option.value)
                    return (
                      <CommandItem
                        key={option.value}
                        value={option.value}
                        keywords={option.group ? [option.label, option.group] : [option.label]}
                        disabled={disabled}
                        onSelect={() => handleSelect(option.value)}
                      >
                        <div
                          className={cn(
                            'mr-2 flex h-4 w-4 shrink-0 items-center justify-center rounded-sm border border-primary',
                            isSelected
                              ? 'bg-primary text-primary-foreground'
                              : 'opacity-50 [&_svg]:invisible'
                          )}
                        >
                          <Check className="h-3 w-3" strokeWidth={2} fill="none" />
                        </div>
                        <span className="min-w-0 truncate" title={option.label}>{option.label}</span>
                      </CommandItem>
                    )
                  })}
                </CommandGroup>
              ))}
            </CommandList>
          </Command>
        </PopoverContent>
      </Popover>
    </div>
  )
}
