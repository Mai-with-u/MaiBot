import { getPluginType, type PluginInfo } from './types'

export interface MarketplaceFilters {
  searchQuery: string
  pluginTypeFilter: string
  showCompatibleOnly: boolean
}

/** 数量与列表共用同一份筛选结果；兼容性规则由页面提供。 */
export function filterMarketplacePlugins(
  plugins: PluginInfo[],
  { searchQuery, pluginTypeFilter, showCompatibleOnly }: MarketplaceFilters,
  checkPluginCompatibility: (plugin: PluginInfo) => boolean,
): PluginInfo[] {
  const query = searchQuery.toLowerCase()
  return plugins.filter((plugin) => {
    const { manifest } = plugin
    // 本地独有插件不属于市场；缺少清单的条目不能展示。
    if (!manifest || plugin.source === 'local') return false
    if (pluginTypeFilter !== 'all' && getPluginType(plugin) !== pluginTypeFilter) return false
    if (showCompatibleOnly && !checkPluginCompatibility(plugin)) return false
    return query === '' || [plugin.id, manifest.id, manifest.name, manifest.author?.name, manifest.description, ...manifest.keywords]
      .some((value) => value?.toLowerCase().includes(query))
  })
}
