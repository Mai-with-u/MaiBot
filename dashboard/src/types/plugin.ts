/**
 * 插件清单文件类型定义
 * 基于 MaiBot 插件系统 manifest 规范
 */

export interface PluginAuthor {
  /** 插件作者名称 */
  name: string
  /** 插件作者主页 */
  url?: string
}

export interface HostApplication {
  /** 插件适配麦麦最低版本 */
  min_version: string
  /** 插件适配麦麦最高版本（可选） */
  max_version?: string
}

export type PluginType =
  | 'adapter'
  | 'chat'
  | 'creative'
  | 'provider'
  | 'management'
  | 'search'
  | 'knowledge'
  | 'media'
  | 'game'
  | 'security'
  | 'automation'
  | 'extension'
  | 'other'

export interface PluginDisplayIcon {
  type: 'lucide' | 'emoji' | 'local'
  value: string
  fallback?: string
  background?: string
}

export interface PluginDisplay {
  icon?: PluginDisplayIcon
}

export interface PluginAssets {
  /** 插件市场缓存的 64x64 图标资源地址 */
  icon_64?: string
}

export interface PluginManifest {
  /** 清单文件版本 */
  manifest_version: number
  /** Manifest 声明的插件唯一标识 */
  id?: string
  /** 插件名称 */
  name: string
  /** 插件版本 */
  version: string
  /** 插件介绍 */
  description: string
  /** 插件作者信息 */
  author: PluginAuthor
  /** 插件许可证 */
  license: string
  /** 主应用版本要求 */
  host_application: HostApplication
  /** 插件主页（可选） */
  homepage_url?: string
  /** 插件仓库地址（可选） */
  repository_url?: string
  /** Manifest v2 URL 集合（可选） */
  urls?: {
    repository?: string
    homepage?: string
    documentation?: string
    issues?: string
  }
  /** 插件关键词 */
  keywords: string[]
  /** 插件类型 */
  plugin_type?: PluginType | string
  /** 插件展示元信息 */
  display?: PluginDisplay
  /** 更新日志地址或插件内相对路径 */
  changelog?: string
  /** 插件默认语言 */
  default_locale: string
  /** 插件语言文件夹（可选） */
  locales_path?: string
}

/**
 * 插件信息（用于市场展示）
 * 包含 manifest 信息和额外的统计数据
 */
export interface PluginInfo {
  /** 插件唯一标识 */
  id: string
  /** 插件仓库索引中的 ID，用于兼容旧统计数据 */
  marketplace_id?: string
  /** 统计服务可能使用的 ID 别名 */
  stats_ids?: string[]
  /** 插件市场清单中的原始顺序，用于在缺少发布时间时推断较新的插件 */
  marketplace_order?: number
  /** 插件清单 */
  manifest: PluginManifest
  /** 插件市场生成的派生资源 */
  assets?: PluginAssets
  /** 下载量 */
  downloads: number
  /** 评分 (0-5) */
  rating: number
  /** 评价数量 */
  review_count: number
  /** 是否已安装 */
  installed: boolean
  /** 安装的版本（如果已安装） */
  installed_version?: string
  /** 发布时间 */
  published_at: string
  /** 最后更新时间 */
  updated_at: string
  /** 详细描述（可能包含 Markdown） */
  detailed_description?: string
  /** 截图列表 */
  screenshots?: string[]
  /** 更新日志 */
  changelog?: string
  /** 插件来源：plugin-repo 市场或本地已安装插件 */
  source?: 'market' | 'local'
}

/**
 * 插件状态
 */
export const PluginStatus = {
  /** 未安装 */
  NOT_INSTALLED: 'not_installed',
  /** 已安装 */
  INSTALLED: 'installed',
  /** 可更新 */
  UPDATE_AVAILABLE: 'update_available',
  /** 安装中 */
  INSTALLING: 'installing',
  /** 卸载中 */
  UNINSTALLING: 'uninstalling',
  /** 已禁用 */
  DISABLED: 'disabled',
} as const

export type PluginStatusType = (typeof PluginStatus)[keyof typeof PluginStatus]

/**
 * 插件搜索筛选参数
 */
export interface PluginSearchParams {
  /** 搜索关键词 */
  query?: string
  /** 分类筛选 */
  category?: string
  /** 排序方式 */
  sort_by?: 'downloads' | 'rating' | 'updated' | 'name'
  /** 排序顺序 */
  order?: 'asc' | 'desc'
  /** 页码 */
  page?: number
  /** 每页数量 */
  limit?: number
}

/**
 * 已安装插件信息
 */
export interface InstalledPlugin {
  /** 插件 ID */
  id: string
  /** 插件清单 */
  manifest: PluginManifest
  /** 安装时间 */
  installed_at: string
  /** 是否启用 */
  enabled: boolean
  /** 插件状态 */
  status: PluginStatusType
  /** 插件路径 */
  path: string
}
