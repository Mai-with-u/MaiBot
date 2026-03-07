/**
 * 配置架构类型定义
 */

export type FieldType =
  | 'string'
  | 'number'
  | 'integer'
  | 'boolean'
  | 'select'
  | 'array'
  | 'object'
  | 'textarea'

export type XWidgetType = 'slider' | 'select' | 'textarea' | 'switch' | 'custom'

export interface FieldSchema {
  name: string
  type: FieldType
  label: string
  description: string
  required: boolean
  default?: unknown
  options?: string[]
  minValue?: number
  maxValue?: number
  items?: {
    type: string
  }
  properties?: ConfigSchema
  'x-widget'?: XWidgetType
  'x-icon'?: string
  step?: number
}

export interface ConfigSchema {
  className: string
  classDoc: string
  fields: FieldSchema[]
  nested?: Record<string, ConfigSchema>
  uiParent?: string
  uiLabel?: string
  uiIcon?: string
}

export interface ConfigSchemaResponse {
  success: boolean
  schema: ConfigSchema
}

export interface ConfigDataResponse {
  success: boolean
  config: Record<string, unknown>
}

export interface ConfigUpdateResponse {
  success: boolean
  message: string
}
