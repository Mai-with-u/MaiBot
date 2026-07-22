import * as React from "react"
import * as LucideIcons from "lucide-react"
import { useTranslation } from "react-i18next"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { DraftNumberInput } from "@/components/ui/draft-number-input"
import { KeyValueEditor } from "@/components/ui/key-value-editor"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Slider } from "@/components/ui/slider"
import { Switch } from "@/components/ui/switch"
import { Textarea } from "@/components/ui/textarea"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { cn } from "@/lib/utils"
import { resolveFieldLabel } from "@/lib/config-label"
import type { FieldSchema } from "@/types/config-schema"

import { fieldTitleClassName } from "./fieldStyle"

const ARRAY_DRAFT_LINE_PATTERN = /\r\n|\n|\r/
const TAG_DRAFT_SPLIT_PATTERN = /[\r\n,，;；]+/

export interface DynamicFieldProps {
  schema: FieldSchema
  value: unknown
  onChange: (value: unknown) => void
  fieldPath?: string // 用于 Hook 系统（未来使用）
}

const resolvePrimitiveArrayValue = (value: unknown, defaultValue: unknown): unknown[] => {
  if (Array.isArray(value)) {
    return value
  }

  if (Array.isArray(defaultValue)) {
    return defaultValue
  }

  return []
}

const formatPrimitiveArrayDraft = (items: unknown[]) => {
  return items.map((item) => String(item ?? '')).join('\n')
}

const parsePrimitiveArrayDraft = (draftValue: string, itemType: string) => {
  return draftValue
    .split(ARRAY_DRAFT_LINE_PATTERN)
    .map((line) => line.trim())
    .filter((line) => line.length > 0)
    .map((line) => {
      if (itemType === 'integer') {
        return parseInt(line, 10) || 0
      }
      if (itemType === 'number') {
        return parseFloat(line) || 0
      }
      if (itemType === 'boolean') {
        return line === 'true'
      }
      return line
    })
}

const formatNumericValue = (value: number) => String(value)

function PrimitiveArrayEditor({
  onChange,
  schema,
  value,
}: Pick<DynamicFieldProps, 'onChange' | 'schema' | 'value'>) {
  const itemType = schema.items?.type ?? 'string'
  const arrayValue = React.useMemo(
    () => resolvePrimitiveArrayValue(value, schema.default),
    [schema.default, value],
  )
  const formattedValue = React.useMemo(
    () => formatPrimitiveArrayDraft(arrayValue),
    [arrayValue],
  )
  const [draftValue, setDraftValue] = React.useState(formattedValue)
  const isFocusedRef = React.useRef(false)

  React.useEffect(() => {
    if (!isFocusedRef.current) {
      setDraftValue(formattedValue)
    }
  }, [formattedValue])

  const commitDraft = (nextDraftValue: string) => {
    onChange(parsePrimitiveArrayDraft(nextDraftValue, itemType))
  }

  const canonicalizeDraft = () => {
    const nextItems = parsePrimitiveArrayDraft(draftValue, itemType)
    onChange(nextItems)
    setDraftValue(formatPrimitiveArrayDraft(nextItems))
  }

  const draftRows = draftValue ? draftValue.split(ARRAY_DRAFT_LINE_PATTERN).length : 0

  return (
    <Textarea
      value={draftValue}
      onBlur={() => {
        isFocusedRef.current = false
        canonicalizeDraft()
      }}
      onChange={(e) => {
        const nextDraftValue = e.target.value
        setDraftValue(nextDraftValue)
        commitDraft(nextDraftValue)
      }}
      onFocus={() => {
        isFocusedRef.current = true
      }}
      rows={Math.max(4, draftRows, arrayValue.length || 4)}
    />
  )
}

type TokenListEditorMode = 'array' | 'comma-string'

function TokenListEditor({
  mode,
  onChange,
  schema,
  value,
}: Pick<DynamicFieldProps, 'onChange' | 'schema' | 'value'> & { mode: TokenListEditorMode }) {
  const items = React.useMemo(
    () => {
      if (mode === 'array') {
        return resolvePrimitiveArrayValue(value, schema.default).map((item) => String(item ?? ''))
      }

      const stringValue = typeof value === 'string' ? value : String(schema.default ?? '')
      return stringValue
        .split(/[,，]/)
        .map((item) => item.trim())
        .filter((item) => item.length > 0)
    },
    [mode, schema.default, value],
  )
  const [draftValue, setDraftValue] = React.useState('')
  const fieldLabel = resolveFieldLabel(schema)
  const placeholder = schema['x-placeholder'] ?? '输入后按回车添加'

  const commitItems = (nextItems: string[]) => {
    const cleanItems = nextItems.filter((item) => item.trim().length > 0)
    onChange(mode === 'array' ? cleanItems : cleanItems.join(','))
  }

  const addDraftItems = () => {
    const draftItems = draftValue
      .split(mode === 'array' ? TAG_DRAFT_SPLIT_PATTERN : /[,，]/)
      .map((item) => item.trim())
      .filter((item) => item.length > 0)

    if (draftItems.length === 0) {
      return
    }

    commitItems(Array.from(new Set([...items, ...draftItems])))
    setDraftValue('')
  }

  const removeItem = (targetIndex: number) => {
    commitItems(items.filter((_, index) => index !== targetIndex))
  }

  return (
    <div className="space-y-2">
      <div className="grid min-w-0 grid-cols-[minmax(0,1fr)_2.25rem] gap-2">
        <Input
          value={draftValue}
          placeholder={placeholder}
          onChange={(event) => setDraftValue(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') {
              event.preventDefault()
              addDraftItems()
            }
          }}
        />
        <Button
          type="button"
          size="icon"
          variant="outline"
          className="h-9 w-9"
          aria-label={`添加${fieldLabel}`}
          title={`添加${fieldLabel}`}
          onClick={addDraftItems}
        >
          <LucideIcons.Plus className="h-4 w-4" />
        </Button>
      </div>
      {items.length > 0 && (
        <div className="space-y-1.5">
          {items.map((item, index) => (
            <div
              key={`${item}-${index}`}
              className="grid min-w-0 grid-cols-[minmax(0,1fr)_2rem] items-center gap-2 rounded-md border bg-muted/20 px-2.5 py-2"
            >
              <span className="min-w-0 truncate font-mono text-sm">{item}</span>
              <Button
                type="button"
                size="icon"
                variant="ghost"
                className="h-8 w-8 text-destructive hover:text-destructive"
                aria-label={`删除${item}`}
                title={`删除${item}`}
                onClick={() => removeItem(index)}
              >
                <LucideIcons.Trash2 className="h-4 w-4" />
              </Button>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

/**
 * DynamicField - 根据字段类型和 x-widget 渲染对应的 shadcn/ui 组件
 * 
 * 渲染逻辑：
 * 1. x-widget 优先：如果 schema 有 x-widget，使用对应组件
 * 2. type 回退：如果没有 x-widget，根据 type 选择默认组件
 */
export const DynamicField: React.FC<DynamicFieldProps> = ({
  schema,
  value,
  onChange,
  fieldPath,
}) => {
  const { i18n } = useTranslation()
  const fieldLabel = resolveFieldLabel(schema, i18n.language)
  const isNumericField = schema.type === 'integer' || schema.type === 'number'

  const parseNumericValue = (rawValue: unknown, fallbackValue: unknown = 0) => {
    if (typeof rawValue === 'number' && Number.isFinite(rawValue)) {
      return rawValue
    }

    if (typeof rawValue === 'string') {
      const parsedValue = parseFloat(rawValue)
      if (Number.isFinite(parsedValue)) {
        return schema.type === 'integer' ? Math.trunc(parsedValue) : parsedValue
      }
    }

    if (fallbackValue !== rawValue) {
      return parseNumericValue(fallbackValue, 0)
    }

    return 0
  }

  const normalizeNumericValue = (nextValue: number, minValue?: number, maxValue?: number) => {
    let normalizedValue = schema.type === 'integer' ? Math.trunc(nextValue) : nextValue

    if (typeof minValue === 'number' && Number.isFinite(minValue)) {
      normalizedValue = Math.max(minValue, normalizedValue)
    }
    if (typeof maxValue === 'number' && Number.isFinite(maxValue)) {
      normalizedValue = Math.min(maxValue, normalizedValue)
    }

    return normalizedValue
  }

  const numericValue = parseNumericValue(value, schema.default)
  const [sliderDraftValue, setSliderDraftValue] = React.useState(() => formatNumericValue(numericValue))
  const sliderInputFocusedRef = React.useRef(false)

  React.useEffect(() => {
    if (!sliderInputFocusedRef.current) {
      setSliderDraftValue(formatNumericValue(numericValue))
    }
  }, [numericValue])

  const renderPrimitiveArrayEditor = () => {
    return <PrimitiveArrayEditor schema={schema} value={value} onChange={onChange} />
  }

  const renderObjectEditor = () => {
    const objectValue =
      value && typeof value === 'object' && !Array.isArray(value)
        ? (value as Record<string, unknown>)
        : {}

    return (
      <KeyValueEditor
        value={objectValue}
        onChange={onChange}
      />
    )
  }

  /**
   * 渲染字段图标
   */
  const renderIcon = () => {
    if (!schema['x-icon']) return null
    
    const IconComponent = LucideIcons[schema['x-icon'] as keyof typeof LucideIcons] as React.ComponentType<{ className?: string }> | undefined
    if (!IconComponent) return null
    
    return <IconComponent className="h-4 w-4" />
  }

  const optionDescriptions = schema['x-option-descriptions'] ?? {}
  const optionLabels = schema['x-option-labels'] ?? {}
  const hasOptionDescriptions = Object.keys(optionDescriptions).length > 0
  const descriptionDisplay = schema['x-description-display'] ?? 'label-hover'
  const fieldDescription = schema.description
  const inlineDescription = descriptionDisplay === 'inline' && !hasOptionDescriptions ? fieldDescription : ''

  const renderDescriptionTooltip = (trigger: React.ReactElement, side: 'top' | 'right' = 'top') => {
    if (!fieldDescription) return trigger

    return (
      <TooltipProvider delayDuration={150}>
        <Tooltip>
          <TooltipTrigger asChild>
            {trigger}
          </TooltipTrigger>
          <TooltipContent
            side={side}
            align="start"
            className="max-w-80 whitespace-pre-line bg-popover text-popover-foreground"
          >
            {fieldDescription}
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>
    )
  }

  const renderFieldHeader = () => (
    <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
      {(() => {
        const label = (
          <Label
            className={cn(
              fieldTitleClassName(
                schema,
                "inline-flex min-w-0 items-center gap-1.5 text-[15px] leading-6",
              ),
              descriptionDisplay === 'label-hover' && fieldDescription && "cursor-help",
            )}
          >
            {renderIcon()}
            <span className="break-words">{fieldLabel}</span>
            {schema.required && <span className="text-destructive">*</span>}
          </Label>
        )

        return descriptionDisplay === 'label-hover'
          ? renderDescriptionTooltip(label)
          : label
      })()}
      {descriptionDisplay === 'icon' && fieldDescription && (
        renderDescriptionTooltip(
          <button
            type="button"
            aria-label={`${fieldLabel} 说明`}
            className="inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <LucideIcons.CircleAlert className="h-4 w-4" />
          </button>,
          'right',
        )
      )}
      {inlineDescription && (
        <span className="text-[13px] leading-6 text-muted-foreground whitespace-pre-line">
          {inlineDescription}
        </span>
      )}
    </div>
  )

  /**
   * 根据 x-widget 或 type 选择并渲染对应的输入组件
   */
  const renderInputComponent = () => {
    const widget = schema['x-widget']
    const type = schema.type
    const resolvedWidget =
      isNumericField && (widget === 'input' || widget === 'number' || !widget)
        ? 'number'
        : widget

    // x-widget 优先
    if (resolvedWidget) {
      switch (resolvedWidget) {
        case 'slider':
          return renderSlider()
        case 'input':
          return renderTextInput()
        case 'number':
          return renderNumberInput()
        case 'password':
          return renderTextInput('password')
        case 'switch':
          return renderSwitch()
        case 'tags':
          if (type === 'array' && schema.items?.type === 'string') {
            return <TokenListEditor mode="array" schema={schema} value={value} onChange={onChange} />
          }
          return renderPrimitiveArrayEditor()
        case 'comma-list':
          if (type === 'string') {
            return <TokenListEditor mode="comma-string" schema={schema} value={value} onChange={onChange} />
          }
          return renderTextInput()
        case 'talk-time':
          return renderTalkTimeInput()
        case 'textarea':
          return renderTextarea()
        case 'select':
          return renderSelect()
        case 'custom':
          if (type === 'array' && schema.items && schema.items.type !== 'object') {
            return renderPrimitiveArrayEditor()
          }
          if (type === 'object') {
            return renderObjectEditor()
          }
          return (
            <div className="rounded-md border border-dashed border-muted-foreground/25 bg-muted/10 p-4 text-center text-sm text-muted-foreground">
              Custom field requires Hook
            </div>
          )
        default:
          // 未知的 x-widget，回退到 type
          break
      }
    }

    // type 回退
    switch (type) {
      case 'boolean':
        return renderSwitch()
      case 'number':
      case 'integer':
        return renderNumberInput()
      case 'string':
        return renderTextInput()
      case 'select':
        return renderSelect()
      case 'array':
        if (!schema.items || schema.items.type === 'object') {
          return (
            <div className="rounded-md border border-dashed border-muted-foreground/25 bg-muted/10 p-4 text-center text-sm text-muted-foreground">
              Complex array requires Hook
            </div>
          )
        }
        return renderPrimitiveArrayEditor()
      case 'object':
        return renderObjectEditor()
      case 'textarea':
        return renderTextarea()
      default:
        return (
          <div className="rounded-md border border-dashed border-muted-foreground/25 bg-muted/10 p-4 text-center text-sm text-muted-foreground">
            Unknown field type: {type}
          </div>
        )
    }
  }

  /**
   * 渲染 Switch 组件（用于 boolean 类型）
   * 使用水平布局：标签+描述在左，开关在右
   */
  const renderSwitch = () => {
    const checked = Boolean(value)
    return (
      <div
        data-dynamic-field={fieldPath ?? schema.name}
        data-dynamic-field-widget="switch"
        className="grid min-h-10 min-w-0 grid-cols-[minmax(0,1fr)_auto] items-center gap-3 py-1.5"
      >
        <div className="min-w-0">
          {renderFieldHeader()}
        </div>
        <Switch
          className="shrink-0 justify-self-end"
          checked={checked}
          onCheckedChange={(checked) => onChange(checked)}
        />
      </div>
    )
  }

  /**
   * 渲染 Slider 组件（用于 number 类型 + x-widget: slider）
   */
  const renderSlider = () => {
    const numValue = numericValue
    const min = schema.minValue ?? 0
    const max = schema.maxValue ?? 100
    const step = schema.step ?? 1

    const commitSliderDraftValue = (nextDraftValue: string) => {
      setSliderDraftValue(nextDraftValue)

      if (!nextDraftValue.trim()) {
        return
      }

      const parsedValue = Number(nextDraftValue)
      if (!Number.isFinite(parsedValue)) {
        return
      }

      onChange(normalizeNumericValue(parsedValue, min, max))
    }

    const canonicalizeSliderDraftValue = () => {
      sliderInputFocusedRef.current = false

      const parsedValue = Number(sliderDraftValue)
      if (!Number.isFinite(parsedValue)) {
        setSliderDraftValue(formatNumericValue(numValue))
        return
      }

      const nextValue = normalizeNumericValue(parsedValue, min, max)
      onChange(nextValue)
      setSliderDraftValue(formatNumericValue(nextValue))
    }

    return (
      <div className="min-w-0 space-y-2">
        <div className="flex min-w-0 items-center gap-3">
          <Slider
            value={[numValue]}
            onValueChange={(values) => onChange(values[0])}
            min={min}
            max={max}
            step={step}
            className="min-w-0 flex-1"
            data-dashboard-slider="config"
          />
          <Input
            aria-label={`${fieldLabel} 数值`}
            type="number"
            value={sliderDraftValue}
            onBlur={canonicalizeSliderDraftValue}
            onChange={(event) => commitSliderDraftValue(event.target.value)}
            onFocus={() => {
              sliderInputFocusedRef.current = true
            }}
            min={min}
            max={max}
            step={step}
            className="h-8 w-24 shrink-0 text-right"
          />
        </div>
      </div>
    )
  }

  /**
   * 渲染 Input[type="number"] 组件（用于 number/integer 类型）
   */
  const renderNumberInput = () => {
    const min = schema.minValue
    const max = schema.maxValue
    const step = schema.step ?? (schema.type === 'integer' ? 1 : 0.1)

    return (
      <DraftNumberInput
        className={inlineRightInputClassName}
        value={value}
        defaultValue={schema.default}
        integer={schema.type === 'integer'}
        onValueChange={(nextValue) => onChange(nextValue)}
        min={min}
        max={max}
        step={step}
        style={inlineRightInputStyle}
      />
    )
  }

  /**
   * 渲染 Input[type="text"] 组件（用于 string 类型）
   */
  const renderTextInput = (type: 'password' | 'text' = 'text') => {
    const strValue =
      typeof value === 'string'
        ? value
        : value === null || value === undefined
          ? String(schema.default ?? '')
          : String(value)
    return (
      <Input
        className={inlineRightInputClassName}
        type={type}
        value={strValue}
        onChange={(e) => onChange(e.target.value)}
        style={inlineRightInputStyle}
      />
    )
  }

  const renderTalkTimeInput = () => {
    const strValue =
      typeof value === 'string'
        ? value
        : value === null || value === undefined
          ? String(schema.default ?? '')
          : String(value)
    const trimmedValue = strValue.trim()
    const mode =
      trimmedValue === ''
        ? 'fallback'
        : trimmedValue === '*'
          ? 'always'
          : 'range'
    const rangeValue = mode === 'range' ? strValue : ''

    const selectFallback = () => onChange('')
    const selectRange = () => onChange(mode === 'range' ? strValue : '00:00-23:59')
    const selectAlways = () => onChange('*')

    return (
      <div className="space-y-2">
        <div className="grid grid-cols-3 gap-2">
          <Button
            type="button"
            size="sm"
            variant={mode === 'fallback' ? 'default' : 'outline'}
            onClick={selectFallback}
          >
            兜底
          </Button>
          <Button
            type="button"
            size="sm"
            variant={mode === 'range' ? 'default' : 'outline'}
            onClick={selectRange}
          >
            时间段
          </Button>
          <Button
            type="button"
            size="sm"
            variant={mode === 'always' ? 'default' : 'outline'}
            onClick={selectAlways}
          >
            *
          </Button>
        </div>
        <Input
          value={rangeValue}
          disabled={mode !== 'range'}
          placeholder="HH:MM-HH:MM"
          onChange={(event) => onChange(event.target.value)}
        />
      </div>
    )
  }

  /**
   * 渲染 Textarea 组件（用于 textarea 类型或 x-widget: textarea）
   */
  const renderTextarea = () => {
    const strValue = typeof value === 'string' ? value : (schema.default as string ?? '')
    const minHeight = typeof schema['x-textarea-min-height'] === 'number'
      ? schema['x-textarea-min-height']
      : undefined
    const rows = typeof schema['x-textarea-rows'] === 'number'
      ? schema['x-textarea-rows']
      : 4

    return (
      <Textarea
        value={strValue}
        onChange={(e) => onChange(e.target.value)}
        rows={rows}
        minHeight={minHeight}
      />
    )
  }

  /**
   * 渲染 Select 组件（用于 select 类型或 x-widget: select）
   */
  const renderSelect = () => {
    const strValue = typeof value === 'string' ? value : (schema.default as string ?? '')
    const options = schema.options ?? []
    const renderOptionLabel = (option: string) => optionLabels[option] ?? option

    if (options.length === 0) {
      return (
        <div className="rounded-md border border-dashed border-muted-foreground/25 bg-muted/10 p-4 text-center text-sm text-muted-foreground">
          No options available for select
        </div>
      )
    }

    return (
      <Select value={strValue} onValueChange={(val) => onChange(val)}>
        <SelectTrigger
          className={inlineRightInputClassName}
          style={inlineRightInputStyle}
        >
          <SelectValue placeholder={`Select ${fieldLabel}`} />
        </SelectTrigger>
        <SelectContent>
          {hasOptionDescriptions ? (
            <TooltipProvider delayDuration={150}>
              {options.map((option) => {
                const description = optionDescriptions[option]
                return description ? (
                  <Tooltip key={option}>
                    <TooltipTrigger asChild>
                      <SelectItem value={option} title={description}>
                        {renderOptionLabel(option)}
                      </SelectItem>
                    </TooltipTrigger>
                    <TooltipContent
                      side="right"
                      align="center"
                      className="max-w-72 bg-popover text-popover-foreground"
                    >
                      {description}
                    </TooltipContent>
                  </Tooltip>
                ) : (
                  <SelectItem key={option} value={option}>
                    {renderOptionLabel(option)}
                  </SelectItem>
                )
              })}
            </TooltipProvider>
          ) : (
            options.map((option) => (
              <SelectItem key={option} value={option}>
                {renderOptionLabel(option)}
              </SelectItem>
            ))
          )}
        </SelectContent>
      </Select>
    )
  }

  // 判断当前字段是否为 Switch/Boolean 类型（独立处理布局）
  const isBoolean =
    schema['x-widget'] === 'switch' ||
    (!schema['x-widget'] && schema.type === 'boolean')
  const supportsInlineRight =
    schema['x-layout'] === 'inline-right' &&
    ['input', 'number', 'password', 'select', undefined].includes(schema['x-widget']) &&
    ['string', 'number', 'integer', 'select'].includes(schema.type)
  const defaultInlineRightInputWidth = isNumericField ? '7.5rem' : '12rem'
  const schemaInputWidth = schema['x-input-width']
  const inlineRightInputWidth =
    isNumericField && (!schemaInputWidth || schemaInputWidth === '12rem')
      ? defaultInlineRightInputWidth
      : schemaInputWidth ?? defaultInlineRightInputWidth
  const inlineRightInputStyle = supportsInlineRight ? { width: inlineRightInputWidth } : undefined
  const inlineRightInputClassName = supportsInlineRight ? '!w-[var(--field-input-width)]' : undefined

  // Switch/Boolean 字段自带完整布局，直接返回
  if (isBoolean) {
    return renderInputComponent()
  }

  if (supportsInlineRight) {
    return (
      <div
        data-dynamic-field={fieldPath ?? schema.name}
        data-dynamic-field-widget={schema['x-widget'] ?? schema.type}
        className="grid min-h-10 min-w-0 grid-cols-1 items-center gap-1.5 py-1.5 sm:grid-cols-[minmax(0,1fr)_auto] sm:gap-3"
        style={{ '--field-input-width': inlineRightInputWidth } as React.CSSProperties}
      >
        <div className="min-w-0">
          {renderFieldHeader()}
        </div>
        <div className="min-w-20 justify-self-start sm:justify-self-end" style={{ width: inlineRightInputWidth }}>
          {renderInputComponent()}
        </div>
      </div>
    )
  }

  return (
    <div
      data-dynamic-field={fieldPath ?? schema.name}
      data-dynamic-field-widget={schema['x-widget'] ?? schema.type}
      className="min-w-0 space-y-1.5"
    >
      {renderFieldHeader()}

      {/* Input component */}
      {renderInputComponent()}
    </div>
  )
}
