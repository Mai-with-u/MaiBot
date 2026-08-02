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

export type XWidgetType =
  | 'custom'
  | 'input'
  | 'number'
  | 'password'
  | 'select'
  | 'slider'
  | 'switch'
  | 'tags'
  | 'talk-time'
  | 'textarea'
  | 'comma-list'

export type LocalizedText = string | Record<string, string>
export type DescriptionDisplayMode = 'inline' | 'icon' | 'label-hover'

export interface FieldSchema {
  name: string
  type: FieldType
  label: LocalizedText
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
  'x-layout'?: 'inline-right'
  'x-input-width'?: string
  'x-description-display'?: DescriptionDisplayMode
  'x-collapsed-by-default'?: boolean
  'x-option-labels'?: Record<string, string>
  'x-option-descriptions'?: Record<string, string>
  'x-placeholder'?: string
  'x-row'?: string
  'x-display-as-section'?: boolean
  'x-textarea-min-height'?: number
  'x-textarea-rows'?: number
  advanced?: boolean
  step?: number
}

export interface ConfigSchema {
  className: string
  classDoc: string
  fields: FieldSchema[]
  nested?: Record<string, ConfigSchema>
  uiParent?: string
  uiLabel?: string
  uiAdvanced?: boolean
  uiOrder?: number
  uiUseSubTabs?: boolean
  uiSubLabel?: string
  uiRootSubLabel?: string
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
