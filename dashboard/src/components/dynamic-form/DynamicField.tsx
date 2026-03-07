import * as React from "react"
import * as LucideIcons from "lucide-react"

import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Slider } from "@/components/ui/slider"
import { Switch } from "@/components/ui/switch"
import { Textarea } from "@/components/ui/textarea"
import type { FieldSchema } from "@/types/config-schema"

export interface DynamicFieldProps {
  schema: FieldSchema
  value: unknown
  onChange: (value: unknown) => void
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  fieldPath?: string // 用于 Hook 系统（未来使用）
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
}) => {
  /**
   * 渲染字段图标
   */
  const renderIcon = () => {
    if (!schema['x-icon']) return null
    
    const IconComponent = LucideIcons[schema['x-icon'] as keyof typeof LucideIcons] as React.ComponentType<{ className?: string }> | undefined
    if (!IconComponent) return null
    
    return <IconComponent className="h-4 w-4" />
  }

  /**
   * 根据 x-widget 或 type 选择并渲染对应的输入组件
   */
  const renderInputComponent = () => {
    const widget = schema['x-widget']
    const type = schema.type

    // x-widget 优先
    if (widget) {
      switch (widget) {
        case 'slider':
          return renderSlider()
        case 'switch':
          return renderSwitch()
        case 'textarea':
          return renderTextarea()
        case 'select':
          return renderSelect()
        case 'custom':
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
        return (
          <div className="rounded-md border border-dashed border-muted-foreground/25 bg-muted/10 p-4 text-center text-sm text-muted-foreground">
            Array fields not yet supported
          </div>
        )
      case 'object':
        return (
          <div className="rounded-md border border-dashed border-muted-foreground/25 bg-muted/10 p-4 text-center text-sm text-muted-foreground">
            Object fields not yet supported
          </div>
        )
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
   */
  const renderSwitch = () => {
    const checked = Boolean(value)
    return (
      <Switch
        checked={checked}
        onCheckedChange={(checked) => onChange(checked)}
      />
    )
  }

  /**
   * 渲染 Slider 组件（用于 number 类型 + x-widget: slider）
   */
  const renderSlider = () => {
    const numValue = typeof value === 'number' ? value : (schema.default as number ?? 0)
    const min = schema.minValue ?? 0
    const max = schema.maxValue ?? 100
    const step = schema.step ?? 1

    return (
      <div className="space-y-2">
        <Slider
          value={[numValue]}
          onValueChange={(values) => onChange(values[0])}
          min={min}
          max={max}
          step={step}
        />
        <div className="flex justify-between text-xs text-muted-foreground">
          <span>{min}</span>
          <span className="font-medium text-foreground">{numValue}</span>
          <span>{max}</span>
        </div>
      </div>
    )
  }

  /**
   * 渲染 Input[type="number"] 组件（用于 number/integer 类型）
   */
  const renderNumberInput = () => {
    const numValue = typeof value === 'number' ? value : (schema.default as number ?? 0)
    const min = schema.minValue
    const max = schema.maxValue
    const step = schema.step ?? (schema.type === 'integer' ? 1 : 0.1)

    return (
      <Input
        type="number"
        value={numValue}
        onChange={(e) => onChange(parseFloat(e.target.value) || 0)}
        min={min}
        max={max}
        step={step}
      />
    )
  }

  /**
   * 渲染 Input[type="text"] 组件（用于 string 类型）
   */
  const renderTextInput = () => {
    const strValue = typeof value === 'string' ? value : (schema.default as string ?? '')
    return (
      <Input
        type="text"
        value={strValue}
        onChange={(e) => onChange(e.target.value)}
      />
    )
  }

  /**
   * 渲染 Textarea 组件（用于 textarea 类型或 x-widget: textarea）
   */
  const renderTextarea = () => {
    const strValue = typeof value === 'string' ? value : (schema.default as string ?? '')
    return (
      <Textarea
        value={strValue}
        onChange={(e) => onChange(e.target.value)}
        rows={4}
      />
    )
  }

  /**
   * 渲染 Select 组件（用于 select 类型或 x-widget: select）
   */
  const renderSelect = () => {
    const strValue = typeof value === 'string' ? value : (schema.default as string ?? '')
    const options = schema.options ?? []

    if (options.length === 0) {
      return (
        <div className="rounded-md border border-dashed border-muted-foreground/25 bg-muted/10 p-4 text-center text-sm text-muted-foreground">
          No options available for select
        </div>
      )
    }

    return (
      <Select value={strValue} onValueChange={(val) => onChange(val)}>
        <SelectTrigger>
          <SelectValue placeholder={`Select ${schema.label}`} />
        </SelectTrigger>
        <SelectContent>
          {options.map((option) => (
            <SelectItem key={option} value={option}>
              {option}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    )
  }

  return (
    <div className="space-y-2">
      {/* Label with icon */}
      <Label className="text-sm font-medium flex items-center gap-2">
        {renderIcon()}
        {schema.label}
        {schema.required && <span className="text-destructive">*</span>}
      </Label>

      {/* Input component */}
      {renderInputComponent()}

      {/* Description */}
      {schema.description && (
        <p className="text-sm text-muted-foreground">{schema.description}</p>
      )}
    </div>
  )
}
