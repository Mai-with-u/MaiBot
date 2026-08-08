import { lazy, Suspense, useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useRouter, useRouterState } from '@tanstack/react-router'
import { AnimatePresence, motion } from 'motion/react'

import { BackgroundLayer } from '@/components/background-layer'
import { BackToTop } from '@/components/back-to-top'
import { HttpWarningBanner } from '@/components/http-warning-banner'
import { SkipNav } from '@/components/ui/skip-nav'
import { useAnnounce } from '@/components/ui/announcer-context'
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
const PAGE_TRANSITION_DURATION_MS = 280
const SIDEBAR_TRANSITION_DURATION_MS = 180
const UpdateNoticeDialog = lazy(() =>
  import('@/components/update-notice-dialog').then((module) => ({
    default: module.UpdateNoticeDialog,
  }))
)

type WorkspaceTransitionStage = 'idle' | 'page-exit' | 'sidebar-exit' | 'sidebar-enter' | 'page-enter'

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
  const routeStatus = useRouterState({ select: (state) => state.status })
  const announce = useAnnounce()
  const isLogsPath =
    pathname === '/logs' || pathname === '/statistics' || pathname.startsWith('/reasoning-process')
  const workspaceMode = pathname === '/chat' ? 'chat' : isLogsPath ? 'logs' : 'settings'
  const isSettingsWorkspace = workspaceMode === 'settings'
  const showBackToTop = isSettingsWorkspace && pathname !== '/planner-monitor'

  const [sidebarOpen, setSidebarOpen] = useState(() => loadStoredBoolean(SIDEBAR_OPEN_STORAGE_KEY, true))
  const [skipSidebarResizeAnimation, setSkipSidebarResizeAnimation] = useState(false)
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false)
  const [searchOpen, setSearchOpen] = useState(false)
  const [topbarCollapsed, setTopbarCollapsed] = useState(() => loadStoredBoolean(TOPBAR_COLLAPSED_STORAGE_KEY, false))
  const [workspaceTransitionStage, setWorkspaceTransitionStage] = useState<WorkspaceTransitionStage>('idle')
  const [workspaceTransitionTarget, setWorkspaceTransitionTarget] = useState<WorkspaceMode | null>(null)
  const workspaceTransitionTimerRef = useRef<number | null>(null)
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
        setSkipSidebarResizeAnimation(false)
        setSidebarOpen(false)
        setTopbarCollapsed(true)
        setMobileMenuOpen(false)
        return
      }

      if (immersiveRestoreRef.current) {
        setSkipSidebarResizeAnimation(false)
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
    return () => {
      if (workspaceTransitionTimerRef.current !== null) {
        window.clearTimeout(workspaceTransitionTimerRef.current)
      }
    }
  }, [])

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

  const scheduleWorkspaceTransition = useCallback((callback: () => void, duration: number) => {
    workspaceTransitionTimerRef.current = window.setTimeout(() => {
      workspaceTransitionTimerRef.current = null
      callback()
    }, duration)
  }, [])

  useEffect(() => {
    if (
      !workspaceTransitionTarget ||
      workspaceMode !== workspaceTransitionTarget ||
      routeStatus !== 'idle'
    ) {
      return
    }

    // pathname 与 Outlet 由路由器分别传播。等新 workspace 完成一次提交后再进入，
    // 避免目标 wrapper 已切换、children 仍短暂保留旧首页时把旧内容重新显示出来。
    const frameId = window.requestAnimationFrame(() => {
      if (workspaceTransitionTarget === 'settings') {
        setWorkspaceTransitionStage('sidebar-enter')
        scheduleWorkspaceTransition(() => {
          setWorkspaceTransitionStage('page-enter')
          scheduleWorkspaceTransition(() => {
            setWorkspaceTransitionStage('idle')
            setWorkspaceTransitionTarget(null)
          }, PAGE_TRANSITION_DURATION_MS)
        }, SIDEBAR_TRANSITION_DURATION_MS)
        return
      }

      setWorkspaceTransitionStage('page-enter')
      scheduleWorkspaceTransition(() => {
        setWorkspaceTransitionStage('idle')
        setWorkspaceTransitionTarget(null)
      }, PAGE_TRANSITION_DURATION_MS)
    })

    return () => window.cancelAnimationFrame(frameId)
  }, [routeStatus, scheduleWorkspaceTransition, workspaceMode, workspaceTransitionTarget])

  const handleWorkspaceNavigate = (to: '/' | '/chat' | '/logs') => {
    if (workspaceTransitionStage !== 'idle') {
      return
    }

    setMobileMenuOpen(false)
    setSkipSidebarResizeAnimation(false)
    setWorkspaceTransitionTarget(to === '/chat' ? 'chat' : to === '/logs' ? 'logs' : 'settings')

    const enterWorkspace = () => {
      void router.navigate({ to }).catch(() => {
        setWorkspaceTransitionTarget(null)
        setWorkspaceTransitionStage('idle')
      })
    }

    setWorkspaceTransitionStage('page-exit')
    scheduleWorkspaceTransition(() => {
      if (workspaceMode === 'settings') {
        setWorkspaceTransitionStage('sidebar-exit')
        scheduleWorkspaceTransition(enterWorkspace, SIDEBAR_TRANSITION_DURATION_MS)
        return
      }

      enterWorkspace()
    }, PAGE_TRANSITION_DURATION_MS)
  }

  const pageHidden =
    workspaceTransitionStage === 'page-exit' ||
    workspaceTransitionStage === 'sidebar-exit' ||
    workspaceTransitionStage === 'sidebar-enter'
  const targetWorkspaceWaiting =
    workspaceTransitionTarget === workspaceMode &&
    workspaceTransitionStage !== 'idle' &&
    workspaceTransitionStage !== 'page-enter'
  const sidebarExiting = workspaceTransitionStage === 'sidebar-exit'
  const handleSidebarFix = () => {
    // 悬浮展开已处于完整宽度；固定时跳过占位宽度过渡，避免已经展开的侧栏出现二次动画。
    setSkipSidebarResizeAnimation(true)
    setSidebarOpen(true)
  }
  const handleSidebarModeToggle = () => {
    setSkipSidebarResizeAnimation(false)
    setSidebarOpen((currentSidebarOpen) => !currentSidebarOpen)
  }

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
        className={cn(
          'relative isolate flex h-[100dvh] overflow-hidden overscroll-none',
          isElectron() && 'pt-8'
        )}
      >
        <BackgroundLayer config={pageBg} layerId="page" />
        <div className="relative z-10 flex h-full min-h-0 w-full overflow-hidden">
          {/* Sidebar：离开设置工作区时向左收起，并同步释放布局宽度 */}
          {isSettingsWorkspace && (
            <motion.div
              key="settings-sidebar"
              data-dashboard-sidebar-layout="true"
              layout={false}
              className={cn(
                'relative z-40 hidden shrink-0 transition-[width] duration-[220ms] ease-[cubic-bezier(0.22,1,0.36,1)] motion-reduce:transition-none lg:block',
                sidebarExiting ? 'overflow-hidden' : 'overflow-visible',
                skipSidebarResizeAnimation && 'transition-none'
              )}
              initial={false}
              style={{
                width: sidebarExiting
                  ? 0
                  : sidebarOpen
                    ? 'var(--layout-sidebar-width)'
                    : 'var(--layout-sidebar-collapsed-width)',
              }}
            >
              <motion.div
                className="h-full w-full will-change-transform"
                initial={{ opacity: 0, x: '-100%' }}
                animate={sidebarExiting ? { opacity: 0, x: '-100%' } : { opacity: 1, x: 0 }}
                transition={{ duration: 0.18, ease: [0.22, 1, 0.36, 1] }}
              >
                <Sidebar
                  sidebarOpen={sidebarOpen}
                  mobileMenuOpen={mobileMenuOpen}
                  onMobileMenuClose={() => setMobileMenuOpen(false)}
                  onSidebarFix={handleSidebarFix}
                />
              </motion.div>
            </motion.div>
          )}

          {/* 移动端 Sidebar 走自己的 fixed 定位，通过 mobileMenuOpen 控制显隐 */}
          {isSettingsWorkspace && (
            <div className="lg:hidden">
              <Sidebar
                sidebarOpen={sidebarOpen}
                mobileMenuOpen={mobileMenuOpen}
                onMobileMenuClose={() => setMobileMenuOpen(false)}
                onSidebarFix={handleSidebarFix}
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
          <motion.div
            layout={false}
            className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden"
          >
            {/* HTTP 安全警告横幅 */}
            <HttpWarningBanner />

            {/* Topbar */}
            <Header
              sidebarOpen={sidebarOpen}
              mobileMenuOpen={mobileMenuOpen}
              searchOpen={searchOpen}
              actualTheme={actualTheme}
              onSidebarToggle={handleSidebarModeToggle}
              onMobileMenuToggle={() => setMobileMenuOpen(!mobileMenuOpen)}
              onSearchOpenChange={setSearchOpen}
              onThemeChange={setTheme}
              onTopbarToggle={() => setTopbarCollapsed(!topbarCollapsed)}
              onWorkspaceNavigate={handleWorkspaceNavigate}
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
                workspaceTransitionStage !== 'idle'
                  ? 'overflow-hidden'
                  : isSettingsWorkspace
                    ? 'overflow-y-auto overflow-x-hidden overscroll-contain'
                    : 'overflow-hidden',
                workspaceMode === 'chat'
                  ? 'bg-transparent'
                  : pageBg.type === 'none'
                    ? 'bg-background'
                    : 'bg-transparent'
              )}
            >
              <motion.div
                key={workspaceMode}
                data-dashboard-workspace-content="true"
                className={cn(
                  'relative z-10 h-full min-w-0 origin-bottom will-change-transform',
                  isSettingsWorkspace && 'min-h-full',
                  targetWorkspaceWaiting && 'invisible'
                )}
                variants={
                  workspaceMode === 'chat'
                    ? {
                        initial: { opacity: 1 },
                        animate: { opacity: 1 },
                        exit: { opacity: 1 },
                      }
                    : {
                        initial: { y: '100%' },
                        animate: { y: 0 },
                        exit: { y: '100%' },
                      }
                }
                initial="initial"
                animate={pageHidden ? 'exit' : 'animate'}
                transition={{ duration: 0.28, ease: [0.22, 1, 0.36, 1] }}
              >
                {children}
              </motion.div>
            </main>

            {/* Back to Top Button */}
            {showBackToTop && <BackToTop />}
          </motion.div>
        </div>
      </div>
      <Suspense fallback={null}>
        <UpdateNoticeDialog />
      </Suspense>
    </TooltipProvider>
  )
}
