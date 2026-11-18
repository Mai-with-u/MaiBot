import type { PluginInfo } from '@/types/plugin'
import type { PluginType } from '@/types/plugin'
import type { GitStatus, MaimaiVersion, PluginLoadProgress } from '@/lib/plugin-api'
import type { PluginStatsData } from '@/lib/plugin-stats'

export const PLUGIN_TYPE_LABELS: Record<PluginType, string> = {
  adapter: '适配器',
  tool: '工具',
  provider: '服务提供方',
  management: '管理',
  data: '数据与知识',
  media: '媒体',
  game: '游戏娱乐',
  integration: '外部集成',
  other: '其他',
  chat: '聊天',
  creative: '创作',
  automation: '自动化',
  deskpet: '桌宠',
}

export const PLUGIN_TYPE_OPTIONS: Array<{ value: PluginType; label: string }> = (
  Object.keys(PLUGIN_TYPE_LABELS) as PluginType[]
).map(value => ({ value, label: PLUGIN_TYPE_LABELS[value] }))

// 历史类型统一映射到当前分类；未声明类型不参与分类筛选。
export function getPluginType(plugin: {
  manifest?: { plugin_type?: PluginType | string }
}): PluginType | null {
  const value = plugin.manifest?.plugin_type?.trim().toLowerCase()
  if (!value || value === 'extension') return null
  if (value === 'knowledge') return 'data'
  if (value === 'entertainment') return 'game'
  if (value === 'search') return 'integration'
  return Object.hasOwn(PLUGIN_TYPE_LABELS, value) ? value as PluginType : 'other'
}

export function getPluginTypeLabel(plugin: {
  manifest?: { plugin_type?: PluginType | string }
}): string {
  const type = getPluginType(plugin)
  return type ? PLUGIN_TYPE_LABELS[type] : '无类别'
}

export function getPluginProgressDetail(progress: PluginLoadProgress): string | null {
  const parts: string[] = []
  const hasMirrorProgress = progress.mirror_index && progress.total_mirrors

  if (progress.mirror_name) {
    parts.push(
      hasMirrorProgress
        ? `镜像源 ${progress.mirror_index}/${progress.total_mirrors}：${progress.mirror_name}`
        : `镜像源：${progress.mirror_name}`
    )
  } else if (hasMirrorProgress) {
    parts.push(`镜像源 ${progress.mirror_index}/${progress.total_mirrors}`)
  }

  if (progress.attempt && progress.max_attempts) {
    parts.push(`尝试 ${progress.attempt}/${progress.max_attempts}`)
  }

  return parts.length > 0 ? parts.join(' · ') : null
}

export type PluginProgressById = Record<string, PluginLoadProgress>

export function mergePluginProgress(
  currentProgress: PluginProgressById,
  progress: PluginLoadProgress
): PluginProgressById {
  if (!progress.plugin_id) {
    return currentProgress
  }

  return {
    ...currentProgress,
    [progress.plugin_id]: progress,
  }
}

export function clearPluginProgress(
  currentProgress: PluginProgressById,
  pluginId: string,
  completedProgress: PluginLoadProgress
): PluginProgressById {
  if (currentProgress[pluginId] !== completedProgress) {
    return currentProgress
  }

  const nextProgress = { ...currentProgress }
  delete nextProgress[pluginId]
  return nextProgress
}

// 导出类型
export type MarketplaceSortKey = 'default' | 'downloads' | 'likes' | 'rating' | 'latest'

export type {
  PluginInfo,
  PluginType,
  GitStatus,
  MaimaiVersion,
  PluginLoadProgress,
  PluginStatsData,
}
