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
  | 'discrete-slider'
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
  readOnly?: boolean
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
  /** 同一 x-row 的字段在宽屏下排成几列；缺省按 2/3 列自适应 */
  'x-row-columns'?: number
  /** 相邻且同名的字段分成并排的几列，列标题由 x-column 给出 */
  'x-column-group'?: string
  'x-column'?: string
  /** 依赖同级布尔字段，该字段为 false 时本字段只读 */
  'x-depends-on'?: string
  'x-chat-settings'?: {
    scope: 'common' | 'group' | 'private'
    row: number
    quick?: boolean
    dialog?: string
  }
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
  uiSectionLabel?: string
  uiAdvanced?: boolean
  uiOrder?: number
  uiUseSubTabs?: boolean
  uiSubLabel?: string
  uiPresentation?: 'tab' | 'dialog'
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
