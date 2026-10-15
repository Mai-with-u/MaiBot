import { createContext, useContext, useEffect } from 'react'

import type { MenuSection } from './types'

export interface MobileNavContextValue {
  /** 与侧栏一致的菜单分组（含插件页面），移动端「管理」页据此生成入口 */
  menuSections: MenuSection[]
  /** 打开全局搜索 */
  openSearch: () => void
  /** 全屏子页面（如聊天对话页）请求隐藏底部标签栏 */
  setTabBarHidden: (hidden: boolean) => void
  /** 手机页头右上角的操作区，页面用 MobileHeaderActions 把按钮渲染进去 */
  headerActionsElement: HTMLElement | null
  /** 页面临时替换手机页头的大标题（如多选时的「已选择 N 项」），null 表示用菜单名 */
  setTitleOverride: (title: string | null) => void
}

export const MobileNavContext = createContext<MobileNavContextValue>({
  menuSections: [],
  openSearch: () => {},
  setTabBarHidden: () => {},
  headerActionsElement: null,
  setTitleOverride: () => {},
})

export function useMobileNav(): MobileNavContextValue {
  return useContext(MobileNavContext)
}

/** 页面处于全屏子页面时隐藏底部标签栏，离开页面后自动恢复 */
export function useHideMobileTabBar(hidden: boolean) {
  const { setTabBarHidden } = useMobileNav()
  useEffect(() => {
    setTabBarHidden(hidden)
    return () => setTabBarHidden(false)
  }, [hidden, setTabBarHidden])
}

/** 页面挂载期间用 title 替换手机页头的大标题，传 null 恢复菜单名 */
export function useMobileTitleOverride(title: string | null) {
  const { setTitleOverride } = useMobileNav()
  useEffect(() => {
    setTitleOverride(title)
    return () => setTitleOverride(null)
  }, [title, setTitleOverride])
}

export type MobileTab = 'home' | 'chat' | 'manage' | 'me'

/** 根据当前路径判断底部标签栏的选中项；不属于其它标签的页面都归到「管理」 */
export function resolveMobileTab(pathname: string): MobileTab {
  if (pathname === '/') return 'home'
  // 观察聊天流就在聊天里，单独的麦麦观察页也归到「聊天」
  if (pathname === '/chat' || pathname.startsWith('/planner-monitor')) return 'chat'
  if (pathname === '/settings') return 'me'
  return 'manage'
}
