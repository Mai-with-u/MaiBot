/** 插件生命周期变更事件，供菜单和页面宿主同步刷新 Host 页面清单。 */
export const PLUGIN_PAGES_UPDATED_EVENT = 'maibot:plugin-pages-updated'

export function notifyPluginPagesUpdated(): void {
  if (typeof window === 'undefined') {
    return
  }

  window.dispatchEvent(new Event(PLUGIN_PAGES_UPDATED_EVENT))
}
