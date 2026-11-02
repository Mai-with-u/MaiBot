import type { InstalledPlugin } from '@/lib/plugin-api'
import type { PluginInfo } from '@/types/plugin'

export function isNapcatAdapter(plugin: PluginInfo): boolean {
  const repository = plugin.manifest.repository_url || plugin.manifest.urls?.repository
  if (!repository) return false
  try {
    const url = new URL(repository)
    // 仅匹配独立 NapCat 适配器，避免误伤支持 NapCat 的 SnowLuma。
    return ['github.com', 'cnb.cool'].includes(url.hostname.toLowerCase())
      && /^\/(?:mai-with-u|maim-with-u)\/maibot-napcat-adapter(?:\.git)?\/?$/i.test(url.pathname)
  } catch {
    return false
  }
}

/** 本地插件的展示资料，供市场列表和详情页共用。 */
export function buildLocalPluginInfo(installedPlugin: InstalledPlugin): PluginInfo {
  const urls = installedPlugin.manifest.urls as PluginInfo['manifest']['urls'] | undefined

  return {
    id: installedPlugin.id,
    manifest: {
      manifest_version: installedPlugin.manifest.manifest_version || 1,
      id: installedPlugin.manifest.id || installedPlugin.id,
      name: installedPlugin.manifest.name,
      version: installedPlugin.manifest.version,
      description: installedPlugin.manifest.description || '',
      author: installedPlugin.manifest.author,
      license: installedPlugin.manifest.license || 'Unknown',
      host_application: installedPlugin.manifest.host_application,
      homepage_url: installedPlugin.manifest.homepage_url || urls?.homepage,
      repository_url: installedPlugin.manifest.repository_url || urls?.repository,
      urls,
      keywords: installedPlugin.manifest.keywords || [],
      plugin_type: installedPlugin.manifest.plugin_type || 'extension',
      display: installedPlugin.manifest.display,
      changelog: installedPlugin.manifest.changelog,
      default_locale: (installedPlugin.manifest.default_locale as string) || 'zh-CN',
      locales_path: installedPlugin.manifest.locales_path as string | undefined,
    },
    installed: true,
    installed_version: installedPlugin.manifest.version,
    installed_release: installedPlugin.release,
    stats_ids: [installedPlugin.manifest.id].filter(Boolean) as string[],
    published_at: '',
    updated_at: '',
    source: 'local',
  }
}

/** 合并市场元数据与本地安装状态，同时补入仅在本地存在的插件。 */
export function mergeInstalledPluginInfo(
  marketPlugins: PluginInfo[],
  installed: InstalledPlugin[]
): PluginInfo[] {
  const mergedData: PluginInfo[] = marketPlugins.map(plugin => {
    const installedPlugin = installed.find(item => item.id === plugin.id || item.manifest.id === plugin.id)

    return {
      ...plugin,
      installed: Boolean(installedPlugin),
      installed_version: installedPlugin?.manifest.version,
      installed_release: installedPlugin?.release,
    }
  })

  for (const installedPlugin of installed) {
    const installedManifestId = installedPlugin.manifest?.id
    const existsInMarket = mergedData.some(
      p => p.id === installedPlugin.id || p.id === installedManifestId || p.manifest?.id === installedPlugin.id
    )
    if (!existsInMarket && installedPlugin.manifest) {
      // 添加本地安装但不在市场的插件，资料与详情页使用同一份转换。
      mergedData.push(buildLocalPluginInfo(installedPlugin))
    }
  }

  return mergedData
}
