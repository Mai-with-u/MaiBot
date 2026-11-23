import { lazy, Suspense, useEffect, useMemo, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useRouter, useRouterState } from '@tanstack/react-router'
import { ScrollText, Settings } from 'lucide-react'

import { BackgroundLayer } from '@/components/background-layer'
import { BackToTop } from '@/components/back-to-top'
import { TitleBar } from '@/components/electron/TitleBar'
import { HttpWarningBanner } from '@/components/http-warning-banner'
import { useAnnounce } from '@/components/ui/announcer-context'
import { SkipNav } from '@/components/ui/skip-nav'
import { TooltipProvider } from '@/components/ui/tooltip'
import { useAuthGuard } from '@/hooks/use-auth'
import { useBackground } from '@/hooks/use-background'
import { useMediaQuery } from '@/hooks/use-media-query'
import { useBotAvailable } from '@/host/state'
import { matchesShortcut } from '@/lib/keyboard'
import {
  extensionIcons,
  extensionPath,
  usePluginWebUI,
  visibleExtensions,
} from '@/lib/plugin-webui'
import { subscribeDesktopSidebar } from '@/lib/desktop-host'
import { isElectron } from '@/lib/runtime'
import { cn } from '@/lib/utils'
import { useMenuSections } from '@/components/layout/use-menu-sections'
import type { MenuItem, MenuSection } from '@/components/layout/types'
import { MobileNavContext } from '@/components/layout/mobile-nav-context'
import { MobileRouteHeader } from '@/components/layout/MobileRouteHeader'
import { MobilePageHeader } from '@/components/layout/MobilePageHeader'
import { MobileTabBar } from '@/components/layout/MobileTabBar'

import { ObservationSidebar } from './ObservationSidebar'
import '@/styles/observation.css'

const SIDEBAR_COLLAPSED_STORAGE_KEY = 'maibot-observation-sidebar-collapsed'
const LOG_PATHS = ['/logs', '/statistics', '/reasoning-process']
// 首页由品牌链接打开，聊天由侧栏下半区的对话列表打开。
const CONVERSATION_PATHS = new Set(['/', '/chat'])
const UpdateNoticeDialog = lazy(() =>
  import('@/components/update-notice-dialog').then((module) => ({
    default: module.UpdateNoticeDialog,
  }))
)
let searchDialogLoaded = false
const SearchDialog = lazy(() =>
  import('@/components/search-dialog').then((module) => {
    searchDialogLoaded = true
    return { default: module.SearchDialog }
  })
)

function loadCollapsed(): boolean {
  try {
    return localStorage.getItem(SIDEBAR_COLLAPSED_STORAGE_KEY) === 'true'
  } catch {
    return false
  }
}

/**
 * 麦麦观察布局：没有顶栏，一列侧栏承担全部导航。
 * 桌面侧栏上半区是设置键盘，下半区是所有对话；窄屏使用移动端页头和底部导航。
 */
export function ObservationLayout({ children }: { children: ReactNode }) {
  const { t } = useTranslation()
  const { checking } = useAuthGuard()
  const botAvailable = useBotAvailable()
  const router = useRouter()
  const announce = useAnnounce()
  const pathname = useRouterState({ select: (state) => state.location.pathname })
  const extensionRegistry = usePluginWebUI(!checking && botAvailable)
  const extensions = useMemo(() => visibleExtensions(extensionRegistry), [extensionRegistry])
  const builtInMenuSections = useMenuSections()
  const { config: pageBg } = useBackground('page')
  const [collapsed, setCollapsed] = useState(loadCollapsed)
  const [searchOpen, setSearchOpen] = useState(false)
  const isDesktop = useMediaQuery('(min-width: 1024px)')
  const [mobileTabBarHidden, setMobileTabBarHidden] = useState(false)
  const [mobileHeaderActionsElement, setMobileHeaderActionsElement] = useState<HTMLElement | null>(null)
  const [mobileTitleOverride, setMobileTitleOverride] = useState<string | null>(null)

  const isLogsPath = LOG_PATHS.some((path) => pathname === path || pathname.startsWith(`${path}/`))
  // 首页、聊天和日志自己管理滚动区域，其余设置页按内容撑高、由 main 滚动
  const isScrollablePage = !isLogsPath && !CONVERSATION_PATHS.has(pathname) && pathname !== '/help-history' && pathname !== '/resource/person' && pathname !== '/chat-management'

  const settingsItems = useMemo<MenuItem[]>(
    () => [
      ...builtInMenuSections.flatMap((section) =>
        section.items.filter((item) => !CONVERSATION_PATHS.has(item.path))
      ),
      // 日志、推理过程与详细统计在新布局里共用一颗键位
      { icon: ScrollText, label: 'workspace.logs', path: '/logs' },
      ...extensions.flatMap((extension) =>
        extension.pages.map((page) => ({
          icon: extensionIcons[page.icon],
          label: page.title,
          literalLabel: true,
          path: extensionPath(extension.plugin_id, page.id),
        }))
      ),
    ],
    [builtInMenuSections, extensions]
  )

  // 移动端管理页按插件分别分组，保留每个插件的全部页面入口。
  const mobileMenuSections = useMemo<MenuSection[]>(() => [
    ...builtInMenuSections,
    ...extensions.filter((extension) => extension.pages.length > 0).map((extension) => ({
      title: extension.workspace_title ?? extension.plugin_id,
      literalTitle: true,
      items: extension.pages.map((page) => ({
        icon: extensionIcons[page.icon],
        label: page.title,
        literalLabel: true,
        path: extensionPath(extension.plugin_id, page.id),
      })),
    })),
  ], [builtInMenuSections, extensions])
  const mobileNavValue = useMemo(() => ({
    menuSections: mobileMenuSections,
    openSearch: () => setSearchOpen(true),
    setTabBarHidden: setMobileTabBarHidden,
    headerActionsElement: mobileHeaderActionsElement,
    setTitleOverride: setMobileTitleOverride,
  }), [mobileMenuSections, mobileHeaderActionsElement])

  useEffect(() => {
    try {
      localStorage.setItem(SIDEBAR_COLLAPSED_STORAGE_KEY, String(collapsed))
    } catch {
      // 存储不可用时只影响折叠状态的记忆
    }
  }, [collapsed])

  // 嵌入一键包时折叠键在外壳顶栏上
  useEffect(() => subscribeDesktopSidebar(setCollapsed), [])

  // 搜索快捷键监听（Cmd/Ctrl + K）
  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (matchesShortcut(event, ['mod', 'k'])) {
        event.preventDefault()
        setSearchOpen(true)
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [])

  // 路由变更：document.title 与屏幕阅读器播报
  useEffect(() => {
    const pathToLabel: Record<string, string> = {}
    for (const item of settingsItems) {
      pathToLabel[item.path] = item.literalLabel ? item.label : t(item.label)
    }
    pathToLabel['/'] = t('sidebar.menu.maisakaMonitor')
    pathToLabel['/chat'] = t('workspace.chat')
    pathToLabel['/help-history'] = t('observation.helpHistory')
    pathToLabel['/reasoning-process'] = t('sidebar.menu.reasoningProcess')

    return router.subscribe('onResolved', () => {
      const pageTitle = pathToLabel[router.state.location.pathname] ?? 'MaiBot Dashboard'
      document.title =
        pageTitle === 'MaiBot Dashboard' ? 'MaiBot Dashboard' : `${pageTitle} — MaiBot Dashboard`
      announce(t('a11y.navigatedTo', { page: pageTitle }), 'polite')
    })
  }, [announce, router, settingsItems, t])

  if (checking) {
    return (
      <div className="bg-background flex h-screen items-center justify-center">
        <div className="text-muted-foreground">{t('layout.verifyingLogin')}</div>
      </div>
    )
  }

  return (
    <MobileNavContext.Provider value={mobileNavValue}>
      <TooltipProvider delayDuration={300}>
        <SkipNav />
        {isElectron() && <TitleBar />}
        <div
          data-dashboard-shell="true"
          data-observation-shell="true"
          data-dashboard-page-background={pageBg.type}
          className={cn(
            'relative isolate flex h-[100dvh] overflow-hidden',
            isElectron() && 'pt-8'
          )}
        >
          <BackgroundLayer config={pageBg} layerId="page" />
          {isDesktop && <ObservationSidebar
            collapsed={collapsed}
            settingsItems={settingsItems}
            onOpenSearch={() => setSearchOpen(true)}
            onToggleCollapsed={() => setCollapsed((current) => !current)}
          />}
          <div className="relative z-10 flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
            <HttpWarningBanner />
            <main
              id="main-content"
              data-dashboard-main="true"
              tabIndex={-1}
              className={cn(
                'relative isolate min-h-0 flex-1 outline-none max-lg:pb-[var(--mobile-dock-height,0px)] max-lg:pt-[env(safe-area-inset-top)]',
                isScrollablePage
                  ? 'overflow-x-hidden overflow-y-auto overscroll-contain'
                  : 'overflow-hidden'
              )}
            >
              {/* 设置页自带滚动区域，桌面保持定高；窄屏的页头与页面上下排列。 */}
              <div
                data-dashboard-workspace-content="true"
                className={cn(
                  'relative z-10 h-full min-h-full min-w-0 max-lg:flex max-lg:flex-col',
                  isScrollablePage && 'max-lg:not-has-[[data-plugin-viewport]]:h-auto'
                )}
              >
                {!isDesktop && pathname === '/' && <MobilePageHeader
                  title={t('home.mobile.title')}
                  trailing={<Link to="/settings" aria-label={t('home.mobile.openSettings')}
                    className="bg-muted text-foreground flex h-11 w-11 shrink-0 items-center justify-center rounded-full">
                    <Settings aria-hidden="true" className="h-[22px] w-[22px]" />
                  </Link>}
                  className="px-4 pt-3 pb-2"
                />}
                {!isDesktop && <MobileRouteHeader
                  menuSections={mobileMenuSections}
                  actionsRef={setMobileHeaderActionsElement}
                  titleOverride={mobileTitleOverride}
                />}
                <div
                  className={cn(
                    'lg:contents',
                    isScrollablePage
                      ? 'not-has-[[data-plugin-viewport]]:flex-[1_0_auto] has-[[data-plugin-viewport]]:min-h-0 has-[[data-plugin-viewport]]:flex-1'
                      : 'min-h-0 flex-1'
                  )}
                >
                  {children}
                </div>
              </div>
            </main>
            <MobileTabBar hidden={mobileTabBarHidden} onSearch={() => setSearchOpen(true)} />
            {isScrollablePage && <BackToTop />}
          </div>
        </div>
        {botAvailable && (searchOpen || searchDialogLoaded) && (
          <Suspense fallback={null}>
            <SearchDialog open={searchOpen} onOpenChange={setSearchOpen} />
          </Suspense>
        )}
        <Suspense fallback={null}>{botAvailable && <UpdateNoticeDialog />}</Suspense>
      </TooltipProvider>
    </MobileNavContext.Provider>
  )
}
