/**
 * Git 安装状态
 */
export interface GitStatus {
  installed: boolean
  version?: string
  path?: string
  error?: string
}

/**
 * 麦麦版本信息
 */
export interface MaimaiVersion {
  version: string
  version_major: number
  version_minor: number
  version_patch: number
}

/**
 * 已安装插件信息
 */
export interface InstalledPlugin {
  id: string
  manifest: {
    manifest_version: number
    name: string
    version: string
    description: string
    author: {
      name: string
      url?: string
    }
    license: string
    host_application: {
      min_version: string
      max_version?: string
    }
    homepage_url?: string
    repository_url?: string
    keywords?: string[]
    categories?: string[]
    [key: string]: unknown  // 允许其他字段
  }
  path: string
}
/**
 * 旧版本插件格式(直接包含 version 字段)
 */
export interface LegacyInstalledPlugin {
  id: string
  version: string
  path: string
}

/**
 * 插件加载进度
 */
export interface PluginLoadProgress {
  operation: 'idle' | 'fetch' | 'install' | 'uninstall' | 'update'
  stage: 'idle' | 'loading' | 'success' | 'error'
  progress: number  // 0-100
  message: string
  error?: string
  plugin_id?: string
  total_plugins: number
  loaded_plugins: number
}

/**
 * 列表项字段定义（用于 object 类型的数组项）
 */
export interface ItemFieldDefinition {
  type: string
  label?: string
  placeholder?: string
  default?: unknown
}

/**
 * 配置字段定义
 */
export interface ConfigFieldSchema {
  name: string
  type: string
  default: unknown
  description: string
  example?: string
  required: boolean
  choices?: unknown[]
  min?: number
  max?: number
  step?: number
  pattern?: string
  max_length?: number
  label: string
  placeholder?: string
  hint?: string
  icon?: string
  hidden: boolean
  disabled: boolean
  order: number
  input_type?: string
  ui_type: string
  rows?: number
  group?: string
  depends_on?: string
  depends_value?: unknown
  // 列表类型专用
  item_type?: string  // "string" | "number" | "object"
  item_fields?: Record<string, ItemFieldDefinition>
  min_items?: number
  max_items?: number
}

/**
 * 配置节定义
 */
export interface ConfigSectionSchema {
  name: string
  title: string
  description?: string
  icon?: string
  collapsed: boolean
  order: number
  fields: Record<string, ConfigFieldSchema>
}

/**
 * 配置标签页定义
 */
export interface ConfigTabSchema {
  id: string
  title: string
  sections: string[]
  icon?: string
  order: number
  badge?: string
}

/**
 * 配置布局定义
 */
export interface ConfigLayoutSchema {
  type: 'auto' | 'tabs' | 'pages'
  tabs: ConfigTabSchema[]
}

/**
 * 插件配置 Schema
 */
export interface PluginConfigSchema {
  plugin_id: string
  plugin_info: {
    name: string
    version: string
    description: string
    author: string
  }
  sections: Record<string, ConfigSectionSchema>
  layout: ConfigLayoutSchema
  _note?: string
}
