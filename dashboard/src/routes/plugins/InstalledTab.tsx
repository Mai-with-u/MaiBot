import type { GitStatus, MaimaiVersion, PluginInfo, PluginLoadProgress, PluginStatsData } from './types'
import { PluginCard } from './PluginCard'

interface InstalledTabProps {
  plugins: PluginInfo[]
  searchQuery: string
  categoryFilter: string
  showCompatibleOnly: boolean
  gitStatus: GitStatus | null
  maimaiVersion: MaimaiVersion | null
  pluginStats: Record<string, PluginStatsData>
  loadProgress: PluginLoadProgress | null
  onInstall: (plugin: PluginInfo) => void
  onUpdate: (plugin: PluginInfo) => void
  onUninstall: (plugin: PluginInfo) => void
  checkPluginCompatibility: (plugin: PluginInfo) => boolean
  needsUpdate: (plugin: PluginInfo) => boolean
  getStatusBadge: (plugin: PluginInfo) => React.JSX.Element | null
}

export function InstalledTab({
  plugins,
  searchQuery,
  categoryFilter,
  showCompatibleOnly,
  gitStatus,
  maimaiVersion,
  pluginStats,
  loadProgress,
  onInstall,
  onUpdate,
  onUninstall,
  checkPluginCompatibility,
  needsUpdate,
  getStatusBadge,
}: InstalledTabProps) {
  // 过滤已安装插件
  const filteredPlugins = plugins.filter(plugin => {
    // 跳过没有 manifest 的插件
    if (!plugin.manifest) {
      return false
    }
    
    // 只显示已安装
    if (!plugin.installed) {
      return false
    }
    
    // 搜索过滤
    const matchesSearch = searchQuery === '' ||
      plugin.manifest.name?.toLowerCase().includes(searchQuery.toLowerCase()) ||
      plugin.manifest.description?.toLowerCase().includes(searchQuery.toLowerCase()) ||
      (plugin.manifest.keywords && plugin.manifest.keywords.some(k => k.toLowerCase().includes(searchQuery.toLowerCase())))
    
    // 分类过滤
    const matchesCategory = categoryFilter === 'all' ||
      (plugin.manifest.categories && plugin.manifest.categories.includes(categoryFilter))
    
    // 兼容性过滤
    const matchesCompatibility = !showCompatibleOnly || 
      !maimaiVersion || 
      checkPluginCompatibility(plugin)
    
    return matchesSearch && matchesCategory && matchesCompatibility
  })

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
      {filteredPlugins.map((plugin) => (
        <PluginCard
          key={plugin.id}
          plugin={plugin}
          gitStatus={gitStatus}
          maimaiVersion={maimaiVersion}
          pluginStats={pluginStats}
          loadProgress={loadProgress}
          onInstall={onInstall}
          onUpdate={onUpdate}
          onUninstall={onUninstall}
          checkPluginCompatibility={checkPluginCompatibility}
          needsUpdate={needsUpdate}
          getStatusBadge={getStatusBadge}
        />
      ))}
    </div>
  )
}
