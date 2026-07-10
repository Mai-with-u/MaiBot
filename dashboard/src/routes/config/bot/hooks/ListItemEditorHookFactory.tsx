import { useCallback, useEffect, useMemo, useState, type CSSProperties, type ReactNode } from 'react'
import * as LucideIcons from 'lucide-react'
import { ChevronDown, ChevronUp, Plus, Trash2 } from 'lucide-react'

import { Button } from '@/components/ui/button'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip'
import { DynamicConfigForm } from '@/components/dynamic-form/DynamicConfigForm'
import { fieldTitleClassName } from '@/components/dynamic-form/fieldStyle'
import { resolveLocalizedText } from '@/lib/config-label'
import type { FieldHookComponent } from '@/lib/field-hooks'
import type { ConfigSchema, FieldSchema } from '@/types/config-schema'

/**
 * createListItemEditorHook
 *
 * 通过 nestedSchema 渲染列表项式的富 UI 编辑器，替换原来直接展示 JSON 文本的 fallback。
 * 适用于 `List[ConfigBase]` 类型字段（schema.nested 中存在对应子配置类）。
 */
export interface ListItemEditorOptions {
  /** 用于生成每个 item 的标题，如 `${index+1} · ${item.platform}` */
  itemTitle?: (item: Record<string, unknown>, index: number) => string
  /** 添加按钮文案 */
  addLabel?: string
  /** 顶部辅助说明 */
  helperText?: string
  /** 标题旁信息图标说明 */
  infoText?: string
  /** 列表为空时的占位说明 */
  emptyText?: string
  /** 顶部图标（覆盖 schema 自带的 x-icon） */
  iconName?: string
  /** 紧凑布局：把指定字段放在同一行展示 */
  fieldRows?: string[][]
  /** Hook-local field UI metadata overrides */
  fieldSchemaOverrides?: Record<string, Partial<FieldSchema>>
  /** 只在列表项编辑器中展示这些字段；未展示字段由后端默认值或 normalizeItems 处理 */
  visibleFields?: string[]
  /** 后端 schema 暂时缺失时用于维持富编辑器可用的本地子项 schema */
  fallbackNestedSchema?: ConfigSchema
  /** 添加按钮位置 */
  addButtonPlacement?: 'top' | 'bottom' | 'none'
  /** 根据同级配置决定是否默认折叠 */
  collapseWhen?: (context: { parentValues?: Record<string, unknown> }) => boolean
  collapsedText?: string
  expandLabel?: string
  collapseLabel?: string
  collapseButtonDisplay?: 'text' | 'icon'
  normalizeItems?: (
    items: Record<string, unknown>[],
    context?: { addedIndex?: number; changedIndex?: number },
  ) => Record<string, unknown>[]
  renderOverview?: (context: {
    items: Record<string, unknown>[]
    onAddItem: (item?: Record<string, unknown>) => void
    onItemFieldChange: (index: number, fieldName: string, fieldValue: unknown) => void
    onItemsChange: (
      items: Record<string, unknown>[],
      context?: { addedIndex?: number; changedIndex?: number },
    ) => void
    onRemoveItem: (index: number) => void
  }) => ReactNode
  renderItems?: (context: {
    emptyText: string
    items: Record<string, unknown>[]
    onAddItem: (item?: Record<string, unknown>) => void
    onItemFieldChange: (index: number, fieldName: string, fieldValue: unknown) => void
    onItemsChange: (
      items: Record<string, unknown>[],
      context?: { addedIndex?: number; changedIndex?: number },
    ) => void
    onRemoveItem: (index: number) => void
    renderItemEditor: (item: Record<string, unknown>, index: number) => ReactNode
  }) => ReactNode
}

function resolveLabel(schema?: ConfigSchema | FieldSchema, fieldPath?: string): string {
  if (!schema) {
    return fieldPath?.split('.').at(-1) ?? '列表配置'
  }
  if ('label' in schema && schema.label) {
    return resolveLocalizedText(schema.label, undefined, fieldPath?.split('.').at(-1) ?? '列表配置')
  }
  if ('uiLabel' in schema && schema.uiLabel) {
    return schema.uiLabel
  }
  if ('classDoc' in schema && schema.classDoc) {
    return schema.classDoc
  }
  if ('className' in schema && schema.className) {
    return schema.className
  }
  return fieldPath?.split('.').at(-1) ?? '列表配置'
}

function resolveDescription(schema?: ConfigSchema | FieldSchema): string {
  if (!schema) return ''
  if ('description' in schema && schema.description) return schema.description
  if ('classDoc' in schema && schema.classDoc) return schema.classDoc
  return ''
}

function resolveIconName(
  iconOverride: string | undefined,
  schema?: ConfigSchema | FieldSchema,
): string | undefined {
  if (iconOverride) return iconOverride
  if (schema && 'x-icon' in schema && schema['x-icon']) return schema['x-icon']
  return undefined
}

function renderLucideIcon(iconName: string | undefined, className: string) {
  if (!iconName) return null
  const Icon = LucideIcons[iconName as keyof typeof LucideIcons] as
    | React.ComponentType<{ className?: string }>
    | undefined
  if (!Icon) return null
  return <Icon className={className} />
}

/** 根据 itemSchema 字段默认值构造一个新 item */
function buildDefaultItem(itemSchema: ConfigSchema | undefined): Record<string, unknown> {
  if (!itemSchema?.fields) return {}
  const next: Record<string, unknown> = {}
  for (const field of itemSchema.fields) {
    if ('default' in field && field.default !== undefined) {
      // 数组/对象需要做一次浅拷贝，避免多个 item 共享同一引用
      if (Array.isArray(field.default)) {
        next[field.name] = [...field.default]
      } else if (
        field.default !== null &&
        typeof field.default === 'object'
      ) {
        next[field.name] = { ...(field.default as Record<string, unknown>) }
      } else {
        next[field.name] = field.default
      }
      continue
    }
    switch (field.type) {
      case 'boolean':
        next[field.name] = false
        break
      case 'integer':
      case 'number':
        next[field.name] = 0
        break
      case 'array':
        next[field.name] = []
        break
      case 'object':
        next[field.name] = {}
        break
      case 'select':
        next[field.name] = field.options?.[0] ?? ''
        break
      default:
        next[field.name] = ''
    }
  }
  return next
}

/**
 * 把 dotted-path 写入 item 对象（兼容 DynamicConfigForm 的 onChange）
 */
function setNested(target: Record<string, unknown>, path: string, value: unknown) {
  const keys = path.split('.')
  if (keys.length === 1) {
    target[keys[0]] = value
    return
  }
  let cursor: Record<string, unknown> = target
  for (let i = 0; i < keys.length - 1; i++) {
    const key = keys[i]
    const existing = cursor[key]
    if (existing && typeof existing === 'object' && !Array.isArray(existing)) {
      cursor[key] = { ...(existing as Record<string, unknown>) }
    } else {
      cursor[key] = {}
    }
    cursor = cursor[key] as Record<string, unknown>
  }
  cursor[keys[keys.length - 1]] = value
}

export function createListItemEditorHook(
  options: ListItemEditorOptions = {},
): FieldHookComponent {
  const ListItemEditorHook: FieldHookComponent = ({
    fieldPath,
    onChange,
    schema,
    nestedSchema,
    parentValues,
    value,
  }) => {
    const effectiveNestedSchema = nestedSchema ?? options.fallbackNestedSchema
    const items = useMemo<Record<string, unknown>[]>(() => {
      if (!Array.isArray(value)) return []
      return value.map((item) =>
        item && typeof item === 'object' && !Array.isArray(item)
          ? (item as Record<string, unknown>)
          : {},
      )
    }, [value])

    const emitItems = useCallback(
      (nextItems: Record<string, unknown>[], context?: { addedIndex?: number; changedIndex?: number }) => {
        onChange?.(options.normalizeItems?.(nextItems, context) ?? nextItems)
      },
      [onChange],
    )

    const handleAdd = useCallback(() => {
      const next = [...items, buildDefaultItem(effectiveNestedSchema)]
      emitItems(next, { addedIndex: next.length - 1 })
    }, [effectiveNestedSchema, emitItems, items])

    const handleAddItem = useCallback(
      (item: Record<string, unknown> = {}) => {
        const next = [...items, { ...buildDefaultItem(effectiveNestedSchema), ...item }]
        emitItems(next, { addedIndex: next.length - 1 })
      },
      [effectiveNestedSchema, emitItems, items],
    )

    const handleRemove = useCallback(
      (index: number) => {
        const next = items.filter((_, idx) => idx !== index)
        emitItems(next)
      },
      [emitItems, items],
    )

    const handleItemFieldChange = useCallback(
      (index: number, fieldName: string, fieldValue: unknown) => {
        const next = items.map((item, idx) => {
          if (idx !== index) return item
          const cloned = { ...item }
          setNested(cloned, fieldName, fieldValue)
          return cloned
        })
        emitItems(next, { changedIndex: index })
      },
      [emitItems, items],
    )

    const effectiveEditorSchema = useMemo<ConfigSchema | undefined>(() => {
      if (!effectiveNestedSchema || !options.visibleFields?.length) {
        return effectiveNestedSchema
      }

      const visibleFieldSet = new Set(options.visibleFields)
      return {
        ...effectiveNestedSchema,
        fields: effectiveNestedSchema.fields.filter((field) => visibleFieldSet.has(field.name)),
      }
    }, [effectiveNestedSchema])

    const renderItemEditor = (item: Record<string, unknown>, index: number) => {
      if (!effectiveEditorSchema) {
        return null
      }

      if (!options.fieldRows?.length) {
        return (
          <DynamicConfigForm
            schema={effectiveEditorSchema}
            values={item}
            onChange={(field, fieldValue) =>
              handleItemFieldChange(index, field, fieldValue)
            }
            basePath=""
            level={1}
          />
        )
      }

      const applyFieldOverride = (field: FieldSchema): FieldSchema => ({
        ...field,
        ...(options.fieldSchemaOverrides?.[field.name] ?? {}),
      })
      const fieldMap = new Map(
        effectiveEditorSchema.fields.map((field) => [field.name, applyFieldOverride(field)]),
      )
      const rowFieldNames = new Set(options.fieldRows.flat())
      const remainingFields = effectiveEditorSchema.fields
        .filter((field) => !rowFieldNames.has(field.name))
        .map(applyFieldOverride)
      const buildRowSchema = (fields: FieldSchema[]): ConfigSchema => ({
        ...effectiveEditorSchema,
        fields,
        nested: undefined,
      })

      return (
        <div className="space-y-3">
          {options.fieldRows.map((row, rowIndex) => {
            const fields = row
              .map((fieldName) => fieldMap.get(fieldName))
              .filter((field): field is FieldSchema => Boolean(field))

            if (fields.length === 0) {
              return null
            }

            return (
              <div
                key={rowIndex}
                className="grid gap-3 md:grid-cols-[repeat(var(--field-count),minmax(0,1fr))]"
                style={{ '--field-count': fields.length } as CSSProperties}
              >
                {fields.map((field) => (
                  <DynamicConfigForm
                    key={field.name}
                    schema={buildRowSchema([field])}
                    values={item}
                    onChange={(fieldName, fieldValue) =>
                      handleItemFieldChange(index, fieldName, fieldValue)
                    }
                    basePath=""
                    level={1}
                  />
                ))}
              </div>
            )
          })}
          {remainingFields.length > 0 && (
            <DynamicConfigForm
              schema={buildRowSchema(remainingFields)}
              values={item}
              onChange={(field, fieldValue) =>
                handleItemFieldChange(index, field, fieldValue)
              }
              basePath=""
              level={1}
            />
          )}
        </div>
      )
    }

    const label = resolveLabel(schema, fieldPath)
    const description = resolveDescription(schema)
    const iconName = resolveIconName(options.iconName, schema)
    const addButtonPlacement = options.addButtonPlacement ?? 'bottom'
    const shouldCollapse = options.collapseWhen?.({ parentValues }) ?? false
    const [manuallyExpanded, setManuallyExpanded] = useState(false)
    const collapsed = shouldCollapse && !manuallyExpanded
    const collapseButtonLabel = collapsed
      ? (options.expandLabel ?? '灞曞紑')
      : (options.collapseLabel ?? '鎶樺彔')

    useEffect(() => {
      if (!shouldCollapse) {
        setManuallyExpanded(false)
      }
    }, [shouldCollapse])

    const addButton = (
      <Button
        type="button"
        variant="outline"
        size="sm"
        onClick={handleAdd}
        className="w-full"
      >
        <Plus className="mr-1 h-4 w-4" />
        {options.addLabel ?? '添加一项'}
      </Button>
    )

    if (!effectiveNestedSchema) {
      return (
        <Card>
          <CardHeader>
            <CardTitle className={fieldTitleClassName(schema, 'text-base')}>{label}</CardTitle>
            <CardDescription>未获取到子配置 schema，无法渲染富编辑器。</CardDescription>
          </CardHeader>
        </Card>
      )
    }

    return (
      <Card>
        <CardHeader className="space-y-2 pb-4">
          <div className="flex items-start justify-between gap-3">
            <div className="flex min-w-0 items-center gap-2">
              {renderLucideIcon(iconName, 'h-5 w-5 flex-shrink-0 text-muted-foreground')}
              <CardTitle className={fieldTitleClassName(schema, 'truncate text-base')}>{label}</CardTitle>
              {options.infoText && (
                <TooltipProvider delayDuration={150}>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <button
                        type="button"
                        aria-label={`${label} 说明`}
                        className="inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                      >
                        <LucideIcons.CircleAlert className="h-4 w-4" />
                      </button>
                    </TooltipTrigger>
                    <TooltipContent
                      side="right"
                      align="center"
                      className="max-w-80 whitespace-pre-line bg-popover text-popover-foreground"
                    >
                      {options.infoText}
                    </TooltipContent>
                  </Tooltip>
                </TooltipProvider>
              )}
            </div>
            {shouldCollapse && options.collapseButtonDisplay === 'icon' && (
              <Button
                type="button"
                variant="outline"
                size="icon"
                onClick={() => setManuallyExpanded((current) => !current)}
                aria-label={collapseButtonLabel}
                title={collapseButtonLabel}
                className="inline-flex items-center justify-center"
              >
                {collapsed ? (
                  <ChevronDown className="h-4 w-4" />
                ) : (
                  <ChevronUp className="h-4 w-4" />
                )}
              </Button>
            )}
            {shouldCollapse && options.collapseButtonDisplay !== 'icon' && (
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => setManuallyExpanded((current) => !current)}
                aria-label={collapseButtonLabel}
                title={collapseButtonLabel}
              >
                {collapsed
                  ? (options.expandLabel ?? '展开')
                  : (options.collapseLabel ?? '折叠')}
              </Button>
            )}
          </div>
          {description && (
            <CardDescription className="whitespace-pre-line">{description}</CardDescription>
          )}
          {options.helperText && (
            <p className="text-xs text-muted-foreground">{options.helperText}</p>
          )}
        </CardHeader>
        <CardContent className="space-y-3">
          {collapsed ? (
            <div className="rounded-md border border-dashed border-muted-foreground/25 bg-muted/10 p-4 text-sm text-muted-foreground">
              {options.collapsedText ?? '当前配置已折叠，可手动展开查看或编辑。'}
            </div>
          ) : (
            <>
          {options.renderOverview?.({
            items,
            onAddItem: handleAddItem,
            onItemFieldChange: handleItemFieldChange,
            onItemsChange: emitItems,
            onRemoveItem: handleRemove,
          })}
          {addButtonPlacement === 'top' && addButton}
          {options.renderItems ? (
            options.renderItems({
              emptyText: options.emptyText ?? '尚未添加任何条目，点击下方按钮新增。',
              items,
              onAddItem: handleAddItem,
              onItemFieldChange: handleItemFieldChange,
              onItemsChange: emitItems,
              onRemoveItem: handleRemove,
              renderItemEditor,
            })
          ) : items.length === 0 ? (
            <div className="rounded-md border border-dashed border-muted-foreground/25 bg-muted/10 p-6 text-center text-sm text-muted-foreground">
              {options.emptyText ?? '尚未添加任何条目，点击下方按钮新增。'}
            </div>
          ) : (
            items.map((item, index) => {
              const title =
                options.itemTitle?.(item, index) ?? `条目 ${index + 1}`
              return (
                <div
                  key={index}
                  className="space-y-3 rounded-lg border bg-card/40 p-4"
                >
                  <div className="flex items-center justify-between gap-2">
                    <div className="flex items-center gap-2 text-sm font-semibold">
                      <span className="truncate">{title}</span>
                    </div>
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      className="text-destructive hover:text-destructive"
                      aria-label={`删除${title}`}
                      title={`删除${title}`}
                      onClick={() => handleRemove(index)}
                    >
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </div>
                  {renderItemEditor(item, index)}
                </div>
              )
            })
          )}
          {addButtonPlacement === 'bottom' && addButton}
            </>
          )}
        </CardContent>
      </Card>
    )
  }

  return ListItemEditorHook
}
