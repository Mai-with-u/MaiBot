export const PLUGIN_MARKET_VIEW_STATE_KEY = 'plugins-market-view-state'
export const PLUGIN_MARKET_SHOW_UPDATES_EVENT = 'maibot-plugin-market-show-updates'

/** 让从兼容性提醒进入插件管理时直接显示已安装插件及其更新按钮。 */
export function preparePluginUpdateManagementView(): void {
  sessionStorage.setItem(
    PLUGIN_MARKET_VIEW_STATE_KEY,
    JSON.stringify({
      searchQuery: '',
      pluginTypeFilter: 'all',
      marketplaceSortBy: 'default',
      showInstalledPlugins: true,
    })
  )
  window.dispatchEvent(new Event(PLUGIN_MARKET_SHOW_UPDATES_EVENT))
}
