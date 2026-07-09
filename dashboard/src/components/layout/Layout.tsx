import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useRouter, useRouterState } from '@tanstack/react-router'
import { AnimatePresence, motion } from 'motion/react'

import { BackgroundLayer } from '@/components/background-layer'
import { BackToTop } from '@/components/back-to-top'
import { HttpWarningBanner } from '@/components/http-warning-banner'
import { UpdateNoticeDialog } from '@/components/update-notice-dialog'
import { SkipNav } from '@/components/ui/skip-nav'
import { useAnnounce } from '@/components/ui/announcer'
import { TooltipProvider } from '@/components/ui/tooltip'
import { useTheme } from '@/components/use-theme'
import { useAuthGuard } from '@/hooks/use-auth'
import { useBackground } from '@/hooks/use-background'

import { TitleBar } from '@/components/electron/TitleBar'
import { matchesShortcut } from '@/lib/keyboard'
import { isElectron } from '@/lib/runtime'
import { cn } from '@/lib/utils'
import { Header } from './Header'
import { Sidebar } from './Sidebar'
import type { LayoutProps, WorkspaceMode } from './types'
import { useMenuSections } from './use-menu-sections'

const SIDEBAR_OPEN_STORAGE_KEY = 'maibot-layout-sidebar-open'
const TOPBAR_COLLAPSED_STORAGE_KEY = 'maibot-layout-topbar-collapsed'
const LAYOUT_IMMERSIVE_EVENT = 'maibot-layout-immersive-change'

function loadStoredBoolean(key: string, fallback: boolean): boolean {
  if (typeof window === 'undefined') {
    return fallback
  }

  const stored = localStorage.getItem(key)
  if (stored === 'true') return true
  if (stored === 'false') return false
  return fallback
}

export function Layout({ children }: LayoutProps) {
  const { t } = useTranslation()
  const { checking } = useAuthGuard() // 检查认证状态
  const router = useRouter()
  const pathname = useRouterState({ select: (state) => state.location.pathname })
  const announce = useAnnounce()
  const isLogsPath = pathname === '/logs' || pathname.startsWith('/reasoning-process')
  const workspaceMode = pathname === '/chat' ? 'chat' : isLogsPath ? 'logs' : 'settings'
  const isSettingsWorkspace = workspaceMode === 'settings'
  const isChatWorkspace = workspaceMode === 'chat'
  const showBackToTop = isSettingsWorkspace && pathname !== '/planner-monitor'

  const [sidebarOpen, setSidebarOpen] = useState(() => loadStoredBoolean(SIDEBAR_OPEN_STORAGE_KEY, true))
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false)
  const [searchOpen, setSearchOpen] = useState(false)
  const [topbarCollapsed, setTopbarCollapsed] = useState(() => loadStoredBoolean(TOPBAR_COLLAPSED_STORAGE_KEY, false))
  const [visibleWorkspaceMode, setVisibleWorkspaceMode] = useState<WorkspaceMode>(workspaceMode)
  const [visibleChildren, setVisibleChildren] = useState<LayoutProps['children']>(children)
  const [pendingWorkspace, setPendingWorkspace] = useState<{
    children: LayoutProps['children']
    mode: WorkspaceMode
  } | null>(null)
  const shellStateRef = useRef({ sidebarOpen, topbarCollapsed })
  const immersiveRestoreRef = useRef<{ sidebarOpen: boolean; topbarCollapsed: boolean } | null>(null)
  const { theme, setTheme } = useTheme()
  const menuSections = useMenuSections()

  useEffect(() => {
    shellStateRef.current = { sidebarOpen, topbarCollapsed }
  }, [sidebarOpen, topbarCollapsed])

  useEffect(() => {
    const handleImmersiveChange = (event: Event) => {
      const detail = (event as CustomEvent<{ immersive?: boolean }>).detail
      const immersive = detail?.immersive === true

      if (immersive) {
        immersiveRestoreRef.current ??= shellStateRef.current
        setSidebarOpen(false)
        setTopbarCollapsed(true)
        setMobileMenuOpen(false)
        return
      }

      if (immersiveRestoreRef.current) {
        setSidebarOpen(immersiveRestoreRef.current.sidebarOpen)
        setTopbarCollapsed(immersiveRestoreRef.current.topbarCollapsed)
        immersiveRestoreRef.current = null
      }
    }

    window.addEventListener(LAYOUT_IMMERSIVE_EVENT, handleImmersiveChange)
    return () => window.removeEventListener(LAYOUT_IMMERSIVE_EVENT, handleImmersiveChange)
  }, [])

  useEffect(() => {
    localStorage.setItem(SIDEBAR_OPEN_STORAGE_KEY, String(sidebarOpen))
  }, [sidebarOpen])

  useEffect(() => {
    localStorage.setItem(TOPBAR_COLLAPSED_STORAGE_KEY, String(topbarCollapsed))
  }, [topbarCollapsed])

  // 搜索快捷键监听（Cmd/Ctrl + K）
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (matchesShortcut(e, ['mod', 'k'])) {
        e.preventDefault()
        setSearchOpen(true)
      }
    }

    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [])

  useEffect(() => {
    if (workspaceMode === visibleWorkspaceMode) {
      setVisibleChildren(children)
      setPendingWorkspace(null)
      return
    }

    setPendingWorkspace({ children, mode: workspaceMode })
  }, [children, visibleWorkspaceMode, workspaceMode])
  // 路由变更：焦点管理 + 屏幕阅读器播报 + document.title 更新
  useEffect(() => {
    // 构建 路径 -> 页面标题 的映射表（以当前语言 t() 翻译）
    const pathToLabel: Record<string, string> = {}
    for (const section of menuSections) {
      for (const item of section.items) {
        pathToLabel[item.path] = t(item.label)
      }
    }
    pathToLabel['/chat'] = t('workspace.chat')
    pathToLabel['/focus'] = t('sidebar.menu.focusCompanion')
    pathToLabel['/logs'] = t('workspace.logs')
    pathToLabel['/reasoning-process'] = t('sidebar.menu.reasoningProcess')

    return router.subscribe('onResolved', () => {
      const pageTitle = pathToLabel[router.state.location.pathname] ?? 'MaiBot Dashboard'
      const fullTitle =
        pageTitle === 'MaiBot Dashboard' ? 'MaiBot Dashboard' : `${pageTitle} — MaiBot Dashboard`

      // 更新 document.title
      document.title = fullTitle

      // 屏幕阅读器朗读导航结果
      announce(t('a11y.navigatedTo', { page: pageTitle }), 'polite')

      // 将焦点移到主内容区（仅当焦点不在其内部时）
      const mainEl = document.getElementById('main-content')
      if (mainEl && !mainEl.contains(document.activeElement)) {
        // requestAnimationFrame 确保 DOM 已渲染完成
        requestAnimationFrame(() => {
          mainEl.focus({ preventScroll: true })
        })
      }
    })
  }, [router, announce, t, menuSections])

  // 获取实际应用的主题（处理 system 情况）
  const getActualTheme = () => {
    if (theme === 'system') {
      return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
    }
    return theme
  }

  const actualTheme = getActualTheme()
  const { config: pageBg } = useBackground('page')
  const isWorkspaceTransitioning = pendingWorkspace !== null
  const visibleIsChatWorkspace = visibleWorkspaceMode === 'chat'
  const visibleIsSettingsWorkspace = visibleWorkspaceMode === 'settings'

  // 认证检查中，显示加载状态
  if (checking) {
    return (
      <div className="bg-background flex h-screen items-center justify-center">
        <div className="text-muted-foreground">{t('layout.verifyingLogin')}</div>
      </div>
    )
  }

  return (
    <TooltipProvider delayDuration={300}>
      <SkipNav />
      {isElectron() && <TitleBar />}
      <div
        data-dashboard-shell="true"
        className={cn('relative isolate flex h-screen overflow-hidden overscroll-none', isElectron() && 'pt-8')}
      >
        <BackgroundLayer config={pageBg} layerId="page" />
        <div className="relative z-10 flex h-full min-h-0 w-full overflow-hidden">
          {/* Sidebar：仅在设置工作区显示，伴随滑入/滑出动画 */}
          <AnimatePresence initial={false}>
            {isSettingsWorkspace && (
              <motion.div
                key="settings-sidebar"
                className="relative z-40 hidden shrink-0 overflow-hidden transition-[width] duration-150 ease-out motion-reduce:transition-none lg:block"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                transition={{
                  type: 'spring',
                  stiffness: 320,
                  damping: 36,
                  mass: 0.7,
                  opacity: { duration: 0.2 },
                }}
                style={{
                  width: sidebarOpen
                    ? 'var(--layout-sidebar-width)'
                    : 'var(--layout-sidebar-collapsed-width)',
                }}
              >
                <Sidebar
                  sidebarOpen={sidebarOpen}
                  mobileMenuOpen={mobileMenuOpen}
                  onMobileMenuClose={() => setMobileMenuOpen(false)}
                />
              </motion.div>
            )}
          </AnimatePresence>

          {/* 移动端 Sidebar 走自己的 fixed 定位，通过 mobileMenuOpen 控制显隐 */}
          {isSettingsWorkspace && (
            <div className="lg:hidden">
              <Sidebar
                sidebarOpen={sidebarOpen}
                mobileMenuOpen={mobileMenuOpen}
                onMobileMenuClose={() => setMobileMenuOpen(false)}
              />
            </div>
          )}

          {/* Mobile overlay */}
          <AnimatePresence>
            {isSettingsWorkspace && mobileMenuOpen && (
              <motion.div
                aria-hidden="true"
                className="fixed inset-0 z-40 bg-black/50 lg:hidden"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                transition={{ duration: 0.18 }}
                onClick={() => setMobileMenuOpen(false)}
              />
            )}
          </AnimatePresence>
          {/* Main content */}
          <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
            {/* HTTP 安全警告横幅 */}
            <HttpWarningBanner />

            {/* Topbar */}
            <Header
              sidebarOpen={sidebarOpen}
              mobileMenuOpen={mobileMenuOpen}
              searchOpen={searchOpen}
              actualTheme={actualTheme}
              onSidebarToggle={() => setSidebarOpen(!sidebarOpen)}
              onMobileMenuToggle={() => setMobileMenuOpen(!mobileMenuOpen)}
              onSearchOpenChange={setSearchOpen}
              onThemeChange={setTheme}
              onTopbarToggle={() => setTopbarCollapsed(!topbarCollapsed)}
              topbarCollapsed={topbarCollapsed}
              workspaceMode={workspaceMode}
            />

            {/* Page content */}
            <main
              id="main-content"
              data-dashboard-main="true"
              tabIndex={-1}
              className={cn(
                'relative isolate min-h-0 flex-1 outline-none',
                isSettingsWorkspace ? 'overflow-y-auto overflow-x-hidden overscroll-contain' : 'overflow-hidden',
                isChatWorkspace
                  ? 'bg-transparent'
                  : pageBg.type === 'none'
                    ? 'bg-background'
                    : 'bg-transparent'
              )}
            >
              <AnimatePresence
                mode="wait"
                initial={false}
                onExitComplete={() => {
                  if (!pendingWorkspace) {
                    return
                  }

                  setVisibleWorkspaceMode(pendingWorkspace.mode)
                  setVisibleChildren(pendingWorkspace.children)
                  setPendingWorkspace(null)
                }}
              >
                {!isWorkspaceTransitioning && (
                  <motion.div
                    key={visibleWorkspaceMode}
                    className={cn('relative z-10 min-w-0', visibleIsSettingsWorkspace ? 'h-full min-h-full' : 'h-full')}
                    initial={{ opacity: 0, x: visibleIsChatWorkspace ? 32 : -32, filter: 'blur(6px)' }}
                    animate={{ opacity: 1, x: 0, filter: 'blur(0px)' }}
                    exit={{ opacity: 0, x: visibleIsChatWorkspace ? -32 : 32, filter: 'blur(6px)' }}
                    transition={{
                      type: 'spring',
                      stiffness: 320,
                      damping: 34,
                      mass: 0.7,
                      opacity: { duration: 0.18 },
                      filter: { duration: 0.22 },
                    }}
                  >
                    {visibleChildren}
                  </motion.div>
                )}
              </AnimatePresence>
            </main>

            {/* Back to Top Button */}
            {showBackToTop && <BackToTop />}
          </div>
        </div>
      </div>
      <UpdateNoticeDialog />
    </TooltipProvider>
  )
}
