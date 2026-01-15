/**
 * ListFieldEditor - 动态数组字段编辑器
 *
 * 支持功能：
 * - 字符串数组 (string[])
 * - 数字数组 (number[])
 * - 对象数组 (object[]) - 根据 item_fields 定义渲染
 * - 拖拽排序
 * - 动态增删项
 */

import { useState, useCallback, useMemo } from 'react'
import {
  DndContext,
  closestCenter,
  KeyboardSensor,
  PointerSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
} from '@dnd-kit/core'
import {
  arrayMove,
  SortableContext,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
} from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Card } from '@/components/ui/card'
import { Switch } from '@/components/ui/switch'
import { Slider } from '@/components/ui/slider'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { GripVertical, Plus, Trash2, AlertCircle } from 'lucide-react'
import { cn } from '@/lib/utils'

// ============ 类型定义 ============

export interface ItemFieldDefinition {
  /** 字段类型: "string" | "number" | "boolean" | "select" */
  type: string
  label?: string
  placeholder?: string
  default?: unknown
  /** select 类型的选项 */
  choices?: unknown[]
  /** slider 类型的最小值 */
  min?: number
  /** slider 类型的最大值 */
  max?: number
  /** slider 类型的步进 */
  step?: number
}

export interface ListFieldEditorProps {
  /** 当前值 */
  value: unknown[] | unknown
  /** 值变化回调 */
  onChange: (value: unknown[]) => void
  /** 数组元素类型: "string" | "number" | "object" */
  itemType?: string
  /** 当 itemType="object" 时的字段定义 */
  itemFields?: Record<string, ItemFieldDefinition>
  /** 最小元素数量 */
  minItems?: number
  /** 最大元素数量 */
  maxItems?: number
  /** 是否禁用 */
  disabled?: boolean
  /** 新项的占位符文字 */
  placeholder?: string
}

// ============ 可排序项组件 ============

interface SortableItemProps {
  id: string
  index: number
  itemType: string
  itemFields?: Record<string, ItemFieldDefinition>
  value: unknown
  onChange: (value: unknown) => void
  onRemove: () => void
  disabled?: boolean
  canRemove: boolean
  placeholder?: string
}

function SortableItem({
  id,
  index,
  itemType,
  itemFields,
  value,
  onChange,
  onRemove,
  disabled,
  canRemove,
  placeholder,
}: SortableItemProps) {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id, disabled })

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
  }

  return (
    <div
      ref={setNodeRef}
      style={style}
      className={cn(
        'flex items-start gap-2 group',
        isDragging && 'opacity-50 z-50'
      )}
    >
      {/* 拖拽手柄 */}
      <button
        type="button"
        className={cn(
          'flex-shrink-0 p-2 cursor-grab active:cursor-grabbing',
          'text-muted-foreground hover:text-foreground transition-colors',
          'opacity-0 group-hover:opacity-100 focus:opacity-100',
          disabled && 'cursor-not-allowed opacity-30'
        )}
        {...attributes}
        {...listeners}
      >
        <GripVertical className="h-4 w-4" />
      </button>

      {/* 内容区域 */}
      <div className="flex-1 min-w-0">
        {itemType === 'object' && itemFields ? (
          <ObjectItemEditor
            value={value as Record<string, unknown>}
            onChange={onChange}
            fields={itemFields}
            disabled={disabled}
          />
        ) : itemType === 'number' ? (
          <Input
            type="number"
            value={value as number ?? ''}
            onChange={(e) => onChange(parseFloat(e.target.value) || 0)}
            placeholder={placeholder ?? `第 ${index + 1} 项`}
            disabled={disabled}
            className="font-mono"
          />
        ) : (
          <Input
            type="text"
            value={value as string ?? ''}
            onChange={(e) => onChange(e.target.value)}
            placeholder={placeholder ?? `第 ${index + 1} 项`}
            disabled={disabled}
          />
        )}
      </div>

      {/* 删除按钮 */}
      <Button
        type="button"
        variant="ghost"
        size="icon"
        onClick={onRemove}
        disabled={disabled || !canRemove}
        className={cn(
          'flex-shrink-0 text-muted-foreground hover:text-destructive',
          'opacity-0 group-hover:opacity-100 focus:opacity-100 transition-opacity'
        )}
      >
        <Trash2 className="h-4 w-4" />
      </Button>
    </div>
  )
}

// ============ 对象项编辑器 ============

interface ObjectItemEditorProps {
  value: Record<string, unknown>
  onChange: (value: Record<string, unknown>) => void
  fields: Record<string, ItemFieldDefinition>
  disabled?: boolean
}

function ObjectItemEditor({
  value,
  onChange,
  fields,
  disabled,
}: ObjectItemEditorProps) {
  const handleFieldChange = useCallback(
    (fieldName: string, fieldValue: unknown) => {
      onChange({
        ...value,
        [fieldName]: fieldValue,
      })
    },
    [value, onChange]
  )

  const renderField = (fieldName: string, fieldDef: ItemFieldDefinition) => {
    const fieldValue = value?.[fieldName]

    // boolean / switch
    if (fieldDef.type === 'boolean' || fieldDef.type === 'switch') {
      return (
        <div className="flex items-center justify-between py-1">
          <Label className="text-xs text-muted-foreground">
            {fieldDef.label ?? fieldName}
          </Label>
          <Switch
            checked={Boolean(fieldValue ?? fieldDef.default)}
            onCheckedChange={(checked) => handleFieldChange(fieldName, checked)}
            disabled={disabled}
          />
        </div>
      )
    }

    // slider (number with min/max)
    if (fieldDef.type === 'slider' || (fieldDef.type === 'number' && fieldDef.min != null && fieldDef.max != null)) {
      const numValue = (fieldValue as number) ?? (fieldDef.default as number) ?? fieldDef.min ?? 0
      return (
        <div className="space-y-1">
          <div className="flex items-center justify-between">
            <Label className="text-xs text-muted-foreground">
              {fieldDef.label ?? fieldName}
            </Label>
            <span className="text-xs text-muted-foreground">{numValue}</span>
          </div>
          <Slider
            value={[numValue]}
            onValueChange={(v) => handleFieldChange(fieldName, v[0])}
            min={fieldDef.min ?? 0}
            max={fieldDef.max ?? 100}
            step={fieldDef.step ?? 1}
            disabled={disabled}
            className="py-1"
          />
        </div>
      )
    }

    // select
    if (fieldDef.type === 'select' && fieldDef.choices) {
      return (
        <div className="space-y-1">
          <Label className="text-xs text-muted-foreground">
            {fieldDef.label ?? fieldName}
          </Label>
          <Select
            value={String(fieldValue ?? fieldDef.default ?? '')}
            onValueChange={(v) => handleFieldChange(fieldName, v)}
            disabled={disabled}
          >
            <SelectTrigger className="h-8 text-sm">
              <SelectValue placeholder={fieldDef.placeholder ?? '请选择'} />
            </SelectTrigger>
            <SelectContent>
              {fieldDef.choices.map((choice) => (
                <SelectItem key={String(choice)} value={String(choice)}>
                  {String(choice)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      )
    }

    // number
    if (fieldDef.type === 'number') {
      return (
        <div className="space-y-1">
          <Label className="text-xs text-muted-foreground">
            {fieldDef.label ?? fieldName}
          </Label>
          <Input
            type="number"
            value={(fieldValue as number) ?? fieldDef.default ?? ''}
            onChange={(e) =>
              handleFieldChange(fieldName, parseFloat(e.target.value) || 0)
            }
            placeholder={fieldDef.placeholder}
            disabled={disabled}
            className="h-8 text-sm"
          />
        </div>
      )
    }

    // string (default)
    return (
      <div className="space-y-1">
        <Label className="text-xs text-muted-foreground">
          {fieldDef.label ?? fieldName}
        </Label>
        <Input
          type="text"
          value={(fieldValue as string) ?? fieldDef.default ?? ''}
          onChange={(e) => handleFieldChange(fieldName, e.target.value)}
          placeholder={fieldDef.placeholder}
          disabled={disabled}
          className="h-8 text-sm"
        />
      </div>
    )
  }

  return (
    <Card className="p-3 space-y-2 bg-muted/30">
      {Object.entries(fields).map(([fieldName, fieldDef]) => (
        <div key={fieldName}>
          {renderField(fieldName, fieldDef)}
        </div>
      ))}
    </Card>
  )
}

// ============ 主组件 ============

export function ListFieldEditor({
  value,
  onChange,
  itemType = 'string',
  itemFields,
  minItems,
  maxItems,
  disabled,
  placeholder,
}: ListFieldEditorProps) {
  // 确保 value 是数组
  const items: unknown[] = useMemo(() => {
    if (Array.isArray(value)) return value
    if (typeof value === 'string' && value.trim()) {
      // 尝试解析逗号分隔的字符串
      return value.split(',').map((s: string) => s.trim())
    }
    return []
  }, [value])

  // 为每个项生成稳定的 ID
  const [itemIds] = useState(() => new Map<number, string>())
  const getItemId = useCallback(
    (index: number) => {
      if (!itemIds.has(index)) {
        itemIds.set(index, `item-${Date.now()}-${index}-${Math.random().toString(36).slice(2)}`)
      }
      return itemIds.get(index)!
    },
    [itemIds]
  )

  // 同步 itemIds
  const sortableIds = useMemo(() => {
    // 清理多余的 ID
    const newIds: string[] = []
    for (let i = 0; i < items.length; i++) {
      newIds.push(getItemId(i))
    }
    return newIds
  }, [items.length, getItemId])

  // DnD 传感器配置
  const sensors = useSensors(
    useSensor(PointerSensor, {
      activationConstraint: {
        distance: 8,
      },
    }),
    useSensor(KeyboardSensor, {
      coordinateGetter: sortableKeyboardCoordinates,
    })
  )

  // 拖拽结束处理
  const handleDragEnd = useCallback(
    (event: DragEndEvent) => {
      const { active, over } = event
      if (over && active.id !== over.id) {
        const oldIndex = sortableIds.indexOf(active.id as string)
        const newIndex = sortableIds.indexOf(over.id as string)
        const newItems = arrayMove(items, oldIndex, newIndex)
        onChange(newItems)
      }
    },
    [items, sortableIds, onChange]
  )

  // 添加新项
  const handleAddItem = useCallback(() => {
    if (maxItems != null && items.length >= maxItems) return

    let newItem: unknown
    if (itemType === 'object' && itemFields) {
      // 创建包含默认值的对象
      newItem = Object.fromEntries(
        Object.entries(itemFields).map(([k, v]) => [k, v.default ?? ''])
      )
    } else if (itemType === 'number') {
      newItem = 0
    } else {
      newItem = ''
    }

    onChange([...items, newItem])
  }, [items, maxItems, itemType, itemFields, onChange])

  // 修改项
  const handleItemChange = useCallback(
    (index: number, newValue: unknown) => {
      const newItems = [...items]
      newItems[index] = newValue
      onChange(newItems)
    },
    [items, onChange]
  )

  // 删除项
  const handleRemoveItem = useCallback(
    (index: number) => {
      if (minItems != null && items.length <= minItems) return
      const newItems = items.filter((_: unknown, i: number) => i !== index)
      // 清理 itemIds 映射
      itemIds.delete(index)
      onChange(newItems)
    },
    [items, minItems, itemIds, onChange]
  )

  const canAdd = maxItems == null || items.length < maxItems
  const canRemove = minItems == null || items.length > minItems

  return (
    <div className="space-y-2">
      {/* 列表项 */}
      {items.length === 0 ? (
        <div className="flex items-center gap-2 text-sm text-muted-foreground py-4 justify-center border border-dashed rounded-md">
          <AlertCircle className="h-4 w-4" />
          <span>暂无数据，点击下方按钮添加</span>
        </div>
      ) : (
        <DndContext
          sensors={sensors}
          collisionDetection={closestCenter}
          onDragEnd={handleDragEnd}
        >
          <SortableContext
            items={sortableIds}
            strategy={verticalListSortingStrategy}
          >
            <div className="space-y-2">
              {items.map((item: unknown, index: number) => (
                <SortableItem
                  key={sortableIds[index]}
                  id={sortableIds[index]}
                  index={index}
                  itemType={itemType}
                  itemFields={itemFields}
                  value={item}
                  onChange={(newValue) => handleItemChange(index, newValue)}
                  onRemove={() => handleRemoveItem(index)}
                  disabled={disabled}
                  canRemove={canRemove}
                  placeholder={placeholder}
                />
              ))}
            </div>
          </SortableContext>
        </DndContext>
      )}

      {/* 添加按钮 */}
      <Button
        type="button"
        variant="outline"
        size="sm"
        onClick={handleAddItem}
        disabled={disabled || !canAdd}
        className="w-full"
      >
        <Plus className="h-4 w-4 mr-1" />
        添加项目
        {maxItems !== undefined && (
          <span className="ml-2 text-xs text-muted-foreground">
            ({items.length}/{maxItems})
          </span>
        )}
      </Button>

      {/* 限制提示 */}
      {(minItems != null || maxItems != null) && (minItems !== null || maxItems !== null) && (
        <p className="text-xs text-muted-foreground text-center">
          {minItems != null && maxItems != null
            ? `允许 ${minItems} - ${maxItems} 项`
            : minItems != null
              ? `至少 ${minItems} 项`
              : `最多 ${maxItems} 项`}
        </p>
      )}
    </div>
  )
}

export default ListFieldEditor
