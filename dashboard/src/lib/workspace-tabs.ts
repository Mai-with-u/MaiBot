export const MAX_VISIBLE_PLUGIN_TABS = 2

/** 顶栏与设置预览共用分组规则；当前打开的插件始终留在顶栏。 */
export function splitWorkspaceTabs<T>(tabs: readonly T[], active?: T) {
  const visible = tabs.slice(0, MAX_VISIBLE_PLUGIN_TABS)
  if (active !== undefined && tabs.includes(active) && !visible.includes(active)) {
    visible[MAX_VISIBLE_PLUGIN_TABS - 1] = active
  }
  return { visible, overflow: tabs.filter((tab) => !visible.includes(tab)) }
}
