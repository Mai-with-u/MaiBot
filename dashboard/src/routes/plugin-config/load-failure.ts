import type { InstalledPlugin } from '@/lib/plugin-api'
import type { PluginUpdateState } from './hooks/usePluginList'

/** 插件和适配器共用故障判断与说明，避免两份界面各自解释同一运行状态。 */
export function getPluginLoadFailureInfo(
  plugin: InstalledPlugin,
  update: PluginUpdateState,
  versionIncompatible: boolean,
  checkingUpdates: boolean,
  resourceLabel = '插件'
) {
  const needsUpdate = versionIncompatible && update.hasUpdate
  const reason = plugin.load_error?.trim() || '运行时未返回具体失败原因'
  const title = needsUpdate
    ? `当前${resourceLabel}版本需要更新`
    : versionIncompatible
      ? `当前${resourceLabel}版本已不兼容`
      : `${resourceLabel}加载失败`
  const description = versionIncompatible
    ? checkingUpdates
      ? `已安装 v${plugin.manifest.version} 与当前麦麦版本不兼容，正在检查插件市场更新…`
      : needsUpdate
        ? `已安装 v${plugin.manifest.version}，插件市场已有 v${update.latestVersion}，请更新后重试。`
        : `已安装 v${plugin.manifest.version} 与当前麦麦版本不兼容，请前往插件市场查看兼容版本。`
    : `失败原因：${reason}`
  return { needsUpdate, reason, title, description }
}
