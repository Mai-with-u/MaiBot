import type { PluginInfo } from '@/types/plugin'
import type { GitStatus, MaimaiVersion, PluginLoadProgress } from '@/lib/plugin-api'
import type { PluginStatsData } from '@/lib/plugin-stats'

// 分类名称映射
export const CATEGORY_NAMES: Record<string, string> = {
  'Group Management': '群组管理',
  'Entertainment & Interaction': '娱乐互动',
  'Utility Tools': '实用工具',
  'Content Generation': '内容生成',
  'Multimedia': '多媒体',
  'External Integration': '外部集成',
  'Data Analysis & Insights': '数据分析与洞察',
  'Other': '其他',
}

// 导出类型
export type { PluginInfo, GitStatus, MaimaiVersion, PluginLoadProgress, PluginStatsData }
