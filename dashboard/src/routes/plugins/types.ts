import type { PluginInfo } from '@/types/plugin'
import type { PluginType } from '@/types/plugin'
import type { GitStatus, MaimaiVersion, PluginLoadProgress } from '@/lib/plugin-api'
import type { PluginStatsData } from '@/lib/plugin-stats'

export const PLUGIN_TYPE_LABELS: Record<PluginType, string> = {
  adapter: '适配器',
  chat: '聊天',
  creative: '创作',
  provider: '服务提供方',
  management: '管理',
  search: '检索搜索',
  knowledge: '知识',
  media: '媒体',
  game: '游戏娱乐',
  security: '安全防护',
  automation: '自动化',
  extension: '通用扩展',
  other: '其他',
}

export const PLUGIN_TYPE_OPTIONS: Array<{ value: PluginType; label: string }> = [
  { value: 'adapter', label: PLUGIN_TYPE_LABELS.adapter },
  { value: 'chat', label: PLUGIN_TYPE_LABELS.chat },
  { value: 'creative', label: PLUGIN_TYPE_LABELS.creative },
  { value: 'provider', label: PLUGIN_TYPE_LABELS.provider },
  { value: 'management', label: PLUGIN_TYPE_LABELS.management },
  { value: 'search', label: PLUGIN_TYPE_LABELS.search },
  { value: 'knowledge', label: PLUGIN_TYPE_LABELS.knowledge },
  { value: 'media', label: PLUGIN_TYPE_LABELS.media },
  { value: 'game', label: PLUGIN_TYPE_LABELS.game },
  { value: 'security', label: PLUGIN_TYPE_LABELS.security },
  { value: 'automation', label: PLUGIN_TYPE_LABELS.automation },
  { value: 'extension', label: PLUGIN_TYPE_LABELS.extension },
  { value: 'other', label: PLUGIN_TYPE_LABELS.other },
]

export function getPluginType(plugin: { manifest?: { plugin_type?: PluginType | string } }): PluginType {
  const pluginType = plugin.manifest?.plugin_type
  if (!pluginType?.trim()) {
    return 'extension'
  }

  return pluginType in PLUGIN_TYPE_LABELS ? (pluginType as PluginType) : 'other'
}

export function getPluginTypeLabel(plugin: { manifest?: { plugin_type?: PluginType | string } }): string {
  return PLUGIN_TYPE_LABELS[getPluginType(plugin)]
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

// 导出类型
export type MarketplaceSortKey = 'default' | 'downloads' | 'likes' | 'rating' | 'latest'

export type { PluginInfo, PluginType, GitStatus, MaimaiVersion, PluginLoadProgress, PluginStatsData }
