import { lazy, Suspense, useEffect, useMemo, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { useRouter, useRouterState } from '@tanstack/react-router'
import { ScrollText } from 'lucide-react'

import { BackgroundLayer } from '@/components/background-layer'
import { TitleBar } from '@/components/electron/TitleBar'
import { HttpWarningBanner } from '@/components/http-warning-banner'
import { useAnnounce } from '@/components/ui/announcer-context'
import { SkipNav } from '@/components/ui/skip-nav'
import { TooltipProvider } from '@/components/ui/tooltip'
import { useAuthGuard } from '@/hooks/use-auth'
import { useBackground } from '@/hooks/use-background'
import { useBotAvailable } from '@/host/state'
import { matchesShortcut } from '@/lib/keyboard'
import { LayoutVersionContext } from '@/lib/layout-version'
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
import type { MenuItem } from '@/components/layout/types'

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
 * 上半区是设置键盘，下半区是所有对话；页面内容沿用经典布局的同一批路由组件。
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

  const isLogsPath = LOG_PATHS.some((path) => pathname === path || pathname.startsWith(`${path}/`))
  // 首页、聊天和日志自己管理滚动区域，其余设置页按内容撑高、由 main 滚动
  const isScrollablePage = !isLogsPath && !CONVERSATION_PATHS.has(pathname) && pathname !== '/help-history'

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
    <LayoutVersionContext.Provider value="observation">
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
          <ObservationSidebar
            collapsed={collapsed}
            settingsItems={settingsItems}
            onOpenSearch={() => setSearchOpen(true)}
            onToggleCollapsed={() => setCollapsed((current) => !current)}
          />
          <div className="relative z-10 flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
            <HttpWarningBanner />
            <main
              id="main-content"
              data-dashboard-main="true"
              tabIndex={-1}
              className={cn(
                'relative isolate min-h-0 flex-1 outline-none',
                isScrollablePage
                  ? 'overflow-x-hidden overflow-y-auto overscroll-contain'
                  : 'overflow-hidden'
              )}
            >
              {/* 与经典布局一致保持 h-full：设置页自带滚动区域，需要定高才能在内部滚动 */}
              <div
                data-dashboard-workspace-content="true"
                className="relative z-10 h-full min-h-full min-w-0"
              >
                {children}
              </div>
            </main>
          </div>
        </div>
        {botAvailable && (searchOpen || searchDialogLoaded) && (
          <Suspense fallback={null}>
            <SearchDialog open={searchOpen} onOpenChange={setSearchOpen} />
          </Suspense>
        )}
        <Suspense fallback={null}>{botAvailable && <UpdateNoticeDialog />}</Suspense>
      </TooltipProvider>
    </LayoutVersionContext.Provider>
  )
}
