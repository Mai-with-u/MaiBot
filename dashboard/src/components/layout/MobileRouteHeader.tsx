import { useNavigate, useRouterState } from '@tanstack/react-router'
import { useTranslation } from 'react-i18next'

import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'

import { MOBILE_EXTRA_MANAGE_SECTIONS } from './mobile-manage-sections'
import { MobilePageHeader } from './MobilePageHeader'
import { resolveMobileTab } from './mobile-nav-context'
import type { MenuSection } from './types'

/** 这些页面自己画手机页头（首页、聊天、管理），布局不再重复 */
const SELF_HEADED_PATHS = new Set(['/', '/chat', '/manage'])

/** 在菜单里找当前路径对应的入口名称：取最长的路径前缀匹配 */
function findMenuLabel(
  menuSections: MenuSection[],
  pathname: string,
  translate: (key: string) => string
): string | null {
  let best: { length: number; label: string } | null = null
  for (const section of menuSections) {
    for (const item of section.items) {
      if (item.external) continue
      const matches = pathname === item.path || pathname.startsWith(`${item.path}/`)
      if (matches && item.path !== '/' && (!best || item.path.length > best.length)) {
        best = {
          length: item.path.length,
          label: item.literalLabel ? item.label : translate(item.label),
        }
      }
    }
  }
  return best?.label ?? null
}

interface MobileRouteHeaderProps {
  menuSections: MenuSection[]
  /** 右上角操作区的挂载点，页面通过 MobileHeaderActions 渲染进来 */
  actionsRef: (element: HTMLElement | null) => void
  /** 页面临时替换的大标题 */
  titleOverride?: string | null
}

/**
 * 手机端各子页面统一的页头：大标题取自菜单名称，管理类页面左上角返回「管理」，
 * WebUI 设置返回首页（从首页头像进入）。
 */
export function MobileRouteHeader({
  menuSections,
  actionsRef,
  titleOverride = null,
}: MobileRouteHeaderProps) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const pathname = useRouterState({ select: (state) => state.location.pathname })
  if (SELF_HEADED_PATHS.has(pathname)) return null

  const tab = resolveMobileTab(pathname)

  if (tab === 'me') {
    // 「我的」从首页头像进入，返回首页
    return (
      <MobilePageHeader
        title={t('sidebar.menu.settings')}
        back={{ to: '/', label: t('mobileNav.backHome') }}
        className="px-4 pt-3 pb-2"
      />
    )
  }

  // 单独的麦麦观察页归到「聊天」，返回聊天；其余子页面返回「管理」
  const fromChat = tab === 'chat'
  const fromExtension = pathname.startsWith('/extensions/')
  const extensionPages = fromExtension
    ? menuSections.find((section) => section.items.some((item) => item.path === pathname))?.items ?? []
    : []
  const title =
    titleOverride ??
    findMenuLabel([...menuSections, ...MOBILE_EXTRA_MANAGE_SECTIONS], pathname, t) ??
    (fromChat ? t('sidebar.menu.maisakaMonitor') : fromExtension ? t('sidebar.menu.pluginConfig') : null)
  if (!title) return null
  return (
    <MobilePageHeader
      title={fromExtension ? undefined : title}
      back={
        fromChat
          ? { to: '/chat', label: t('chat.mobile.back') }
          : { to: '/manage', label: t('mobileNav.backManage') }
      }
      backTrailing={
        <div className="flex min-w-0 flex-1 items-center justify-end gap-2.5">
          {extensionPages.length > 1 && (
            <Select value={pathname} onValueChange={(path) => void navigate({ to: path })}>
              <SelectTrigger className="min-w-0 max-w-64 flex-1" aria-label="切换插件页面">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {extensionPages.map((item) => (
                  <SelectItem key={item.path} value={item.path}>
                    {item.literalLabel ? item.label : t(item.label)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
          <div ref={actionsRef} data-mobile-header-actions="true" className="flex shrink-0 gap-2.5" />
        </div>
      }
      className="px-4 pt-3 pb-2"
    />
  )
}
