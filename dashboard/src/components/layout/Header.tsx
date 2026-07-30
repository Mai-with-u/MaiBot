import { Link, useRouterState } from '@tanstack/react-router'
import {
  BookOpen,
  Check,
  ChevronLeft,
  Database,
  FileText,
  Globe,
  LogOut,
  Menu,
  MessageSquare,
  Moon,
  MoreHorizontal,
  Search,
  Settings,
  SlidersHorizontal,
  Sun,
  TimerReset,
} from 'lucide-react'
import { LayoutGroup, motion } from 'motion/react'
import { lazy, Suspense, type ComponentType, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { BackgroundLayer } from '@/components/background-layer'
import { BackendManager } from '@/components/electron/BackendManager'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { toggleThemeWithTransition } from '@/components/use-theme'
import { useBackground } from '@/hooks/use-background'
import { logout } from '@/lib/auth'
import { isElectron } from '@/lib/runtime'
import { DEFAULT_SETTINGS, getSetting } from '@/lib/settings-manager'
import { cn } from '@/lib/utils'

import type { WorkspaceMode } from './types'

const LANGUAGE_CODES = ['zh', 'en', 'ja', 'ko'] as const
const LANGUAGE_NAMES: Record<(typeof LANGUAGE_CODES)[number], string> = {
  zh: '中文',
  en: 'English',
  ja: '日本語',
  ko: '한국어',
}
const LOG_WORKSPACE_COMPACT_GAP = 12
const LOG_WORKSPACE_EXPAND_GAP = 96
const WORKSPACE_HOVER_LEAVE_DELAY_MS = 600
let searchDialogLoaded = false
const SearchDialog = lazy(() =>
  import('@/components/search-dialog').then((module) => {
    searchDialogLoaded = true
    return { default: module.SearchDialog }
  })
)

const WORKSPACE_TABS: Array<{
  value: WorkspaceMode
  to: '/' | '/chat' | '/logs'
  icon: ComponentType<{ className?: string }>
  labelKey: string
}> = [
  { value: 'settings', to: '/', icon: SlidersHorizontal, labelKey: 'workspace.settings' },
  { value: 'chat', to: '/chat', icon: MessageSquare, labelKey: 'workspace.chat' },
  { value: 'logs', to: '/logs', icon: FileText, labelKey: 'workspace.logs' },
]

interface HeaderProps {
  sidebarOpen: boolean
  mobileMenuOpen: boolean
  searchOpen: boolean
  actualTheme: 'light' | 'dark'
  onSidebarToggle: () => void
  onMobileMenuToggle: () => void
  onSearchOpenChange: (open: boolean) => void
  onThemeChange: (theme: 'light' | 'dark' | 'system') => void
  onTopbarToggle: () => void
  onWorkspaceNavigate: (to: '/' | '/chat' | '/logs') => void
  topbarCollapsed: boolean
  workspaceMode: WorkspaceMode
}

type HeaderActionId = 'settings' | 'search' | 'docs' | 'language' | 'theme' | 'logout'

export function Header({
  sidebarOpen,
  mobileMenuOpen,
  searchOpen,
  actualTheme,
  onSidebarToggle,
  onMobileMenuToggle,
  onSearchOpenChange,
  onThemeChange,
  onTopbarToggle,
  onWorkspaceNavigate,
  topbarCollapsed,
  workspaceMode,
}: HeaderProps) {
  const { t, i18n: i18nInstance } = useTranslation()
  const currentLang = i18nInstance.language || 'zh'
  const { config: headerBg, inheritedFrom } = useBackground('header')
  const inheritsPageBackground = inheritedFrom === 'page'
  const pathname = useRouterState({ select: (state) => state.location.pathname })
  const [backendManagerOpen, setBackendManagerOpen] = useState(false)
  const [activeBackendName, setActiveBackendName] = useState<string>('')
  const [focusCompanionEnabled, setFocusCompanionEnabled] = useState(() => getSetting('enableFocusCompanion'))
  const [workspaceTabsCompact, setWorkspaceTabsCompact] = useState(false)
  const [hoveredWorkspace, setHoveredWorkspace] = useState<WorkspaceMode | null>(null)
  const [workspaceHoverLocked, setWorkspaceHoverLocked] = useState(false)
  const [hoveredHeaderAction, setHoveredHeaderAction] = useState<HeaderActionId | null>(null)
  const [languageMenuOpen, setLanguageMenuOpen] = useState(false)
  const workspaceTabsCompactRef = useRef(false)
  const workspaceHoverTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const headerActionTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const workspaceTabsRef = useRef<HTMLDivElement | null>(null)
  const workspaceTabsMeasureRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    if (!isElectron()) return
    window.electronAPI!.getActiveBackend().then((b) => {
      setActiveBackendName(b?.name ?? t('header.notConnected'))
    })
  }, [t])

  useEffect(() => {
    workspaceTabsCompactRef.current = workspaceTabsCompact
  }, [workspaceTabsCompact])

  useEffect(
    () => () => {
      if (workspaceHoverTimerRef.current !== null) {
        clearTimeout(workspaceHoverTimerRef.current)
      }
      if (headerActionTimerRef.current !== null) {
        clearTimeout(headerActionTimerRef.current)
      }
    },
    []
  )

  useEffect(() => {
    if (!workspaceHoverLocked || hoveredWorkspace !== workspaceMode) return
    setHoveredWorkspace(null)
    setWorkspaceHoverLocked(false)
  }, [hoveredWorkspace, workspaceHoverLocked, workspaceMode])

  useEffect(() => {
    const handleSettingsChange = (event: Event) => {
      const detail = (event as CustomEvent<{ key?: string; value?: unknown }>).detail
      if (detail?.key === 'enableFocusCompanion') {
        setFocusCompanionEnabled(Boolean(detail.value))
      }
    }

    const handleSettingsReset = () => {
      setFocusCompanionEnabled(DEFAULT_SETTINGS.enableFocusCompanion)
    }

    window.addEventListener('maibot-settings-change', handleSettingsChange)
    window.addEventListener('maibot-settings-reset', handleSettingsReset)
    return () => {
      window.removeEventListener('maibot-settings-change', handleSettingsChange)
      window.removeEventListener('maibot-settings-reset', handleSettingsReset)
    }
  }, [])

  useEffect(() => {
    if (workspaceMode !== 'logs') {
      const resetFrameId = requestAnimationFrame(() => setWorkspaceTabsCompact(false))
      return () => cancelAnimationFrame(resetFrameId)
    }

    let frameId = 0
    const updateCompactState = () => {
      cancelAnimationFrame(frameId)
      frameId = requestAnimationFrame(() => {
        const logSwitcher = document.querySelector('[data-log-viewer-switcher="true"]')
        const workspaceTabs = workspaceTabsRef.current
        const workspaceTabsMeasure = workspaceTabsMeasureRef.current
        if (
          !(logSwitcher instanceof HTMLElement) ||
          !workspaceTabs ||
          !workspaceTabsMeasure
        ) {
          setWorkspaceTabsCompact(false)
          return
        }

        const logSwitcherVisible = window.getComputedStyle(logSwitcher).display !== 'none'
        const logSwitcherCompact = logSwitcher.dataset.logViewerSwitcherCompact === 'true'
        if (!logSwitcherVisible || !logSwitcherCompact) {
          setWorkspaceTabsCompact(false)
          return
        }

        const logSwitcherRect = logSwitcher.getBoundingClientRect()
        const workspaceTabsRect = workspaceTabs.getBoundingClientRect()
        const workspaceTabsMeasureRect = workspaceTabsMeasure.getBoundingClientRect()
        const fullWorkspaceTabsLeft = workspaceTabsRect.right - workspaceTabsMeasureRect.width
        const gap = fullWorkspaceTabsLeft - logSwitcherRect.right
        const threshold = workspaceTabsCompactRef.current
          ? LOG_WORKSPACE_EXPAND_GAP
          : LOG_WORKSPACE_COMPACT_GAP
        setWorkspaceTabsCompact(gap < threshold)
      })
    }

    updateCompactState()
    window.addEventListener('resize', updateCompactState)

    const resizeObserver = new ResizeObserver(updateCompactState)
    resizeObserver.observe(document.body)
    if (workspaceTabsRef.current) {
      resizeObserver.observe(workspaceTabsRef.current)
    }
    if (workspaceTabsMeasureRef.current) {
      resizeObserver.observe(workspaceTabsMeasureRef.current)
    }
    const logSwitcher = document.querySelector('[data-log-viewer-switcher="true"]')
    if (logSwitcher instanceof HTMLElement) {
      resizeObserver.observe(logSwitcher)
    }

    return () => {
      cancelAnimationFrame(frameId)
      window.removeEventListener('resize', updateCompactState)
      resizeObserver.disconnect()
    }
  }, [workspaceMode])

  const handleLogout = async () => {
    await logout()
  }

  const activeHeaderAction: HeaderActionId | null = languageMenuOpen
    ? 'language'
    : searchOpen
      ? 'search'
      : pathname === '/settings'
        ? 'settings'
        : null
  const highlightedHeaderAction =
    hoveredWorkspace === null ? (hoveredHeaderAction ?? activeHeaderAction) : null

  const handleHeaderActionEnter = (action: HeaderActionId) => {
    if (headerActionTimerRef.current !== null) {
      clearTimeout(headerActionTimerRef.current)
      headerActionTimerRef.current = null
    }
    setHoveredWorkspace(null)
    setHoveredHeaderAction(action)
  }

  const handleHeaderActionLeave = () => {
    if (headerActionTimerRef.current !== null) {
      clearTimeout(headerActionTimerRef.current)
    }
    headerActionTimerRef.current = setTimeout(() => {
      setHoveredHeaderAction(null)
      headerActionTimerRef.current = null
    }, WORKSPACE_HOVER_LEAVE_DELAY_MS)
  }

  const renderHeaderActionPill = (action: HeaderActionId) =>
    highlightedHeaderAction === action ? (
      <motion.span
        layoutId="topbar-selection-pill"
        className="bg-primary pointer-events-none absolute inset-x-1 inset-y-0 -z-10 rounded-sm shadow-sm"
        transition={{ type: 'spring', stiffness: 480, damping: 38, mass: 0.6 }}
      />
    ) : null

  return (
    <motion.header
      data-dashboard-header="true"
      data-dashboard-header-collapsed={topbarCollapsed ? 'true' : undefined}
      initial={false}
      animate={{ height: topbarCollapsed ? 16 : 48, marginBottom: 0 }}
      transition={{ duration: 0.18, ease: [0.22, 1, 0.36, 1] }}
      className={cn(
        'sticky top-0 isolate z-30 min-w-0 overflow-visible',
        topbarCollapsed ? 'h-4' : 'flex h-12 flex-col border-b px-3 backdrop-blur-md sm:px-4',
        topbarCollapsed || inheritsPageBackground ? 'bg-transparent' : 'bg-background'
      )}
    >
      {topbarCollapsed && (
        <div
          data-dashboard-header-strip="true"
          className={cn(
            'relative z-30 flex h-4 min-w-0 items-center justify-end px-3 backdrop-blur-md sm:px-15',
            inheritsPageBackground ? 'bg-transparent' : 'bg-background'
          )}
        >
          {!inheritsPageBackground && <BackgroundLayer config={headerBg} layerId="header" />}
          {sidebarOpen && (
            <button
              type="button"
              data-dashboard-sidebar-mode-switch="true"
              onClick={onSidebarToggle}
              aria-label={t('header.switchSidebarToHover')}
              aria-expanded="true"
              title={t('header.switchSidebarToHover')}
              className={cn(
                'group absolute top-1/2 left-0 z-20 hidden h-5 w-7 -translate-y-1/2 items-center justify-center focus-visible:ring-ring focus-visible:ring-offset-background focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:outline-none lg:flex',
                workspaceMode !== 'settings' && 'lg:hidden'
              )}
            >
              <ChevronLeft
                aria-hidden="true"
                className="text-muted-foreground/55 group-hover:text-primary h-5 w-5 transition-colors"
                strokeWidth={2.25}
              />
              <span className="sr-only">{t('header.switchSidebarToHover')}</span>
            </button>
          )}
          <button
            type="button"
            data-dashboard-topbar-toggle="true"
            onClick={onTopbarToggle}
            aria-label={t('header.expandTopbar')}
            aria-expanded={!topbarCollapsed}
            title={t('header.expandTopbar')}
            className="bg-foreground/70 relative z-10 h-2 w-24 transition-shadow"
          >
            <span className="sr-only">{t('header.expandTopbar')}</span>
          </button>
        </div>
      )}

      {!topbarCollapsed && !inheritsPageBackground && (
        <BackgroundLayer config={headerBg} layerId="header" />
      )}
      <div className={cn(topbarCollapsed ? 'hidden' : 'contents')}>
        <div className="relative z-10 flex h-full min-h-0 items-center justify-between gap-2">
          <div
            id="log-viewer-topbar-tabs"
            className={cn(
              'absolute top-1/2 left-0 hidden min-w-0 shrink-0 -translate-y-1/2 items-center',
              workspaceMode === 'logs' && 'sm:flex'
            )}
          />

          <div className="flex min-w-0 shrink-0 items-center gap-2 sm:gap-4">
            {/* 移动端菜单按钮 */}
            <button
              onClick={onMobileMenuToggle}
              aria-label={t('a11y.closeMenu')}
              aria-expanded={mobileMenuOpen}
              className={cn(
                'hover:bg-accent rounded-lg p-2 lg:hidden',
                workspaceMode !== 'settings' && 'hidden'
              )}
            >
              <Menu className="h-5 w-5" />
            </button>

            {/* 固定模式显示左折线；悬浮模式展开后由侧栏内的右折线切回固定模式。 */}
            {sidebarOpen && (
              <button
                type="button"
                data-dashboard-sidebar-mode-switch="true"
                onClick={onSidebarToggle}
                aria-label={t('header.switchSidebarToHover')}
                aria-expanded="true"
                title={t('header.switchSidebarToHover')}
                className={cn(
                  'group absolute top-1/2 left-0 z-20 hidden h-14 w-7 -translate-y-1/2 items-center justify-center focus-visible:ring-ring focus-visible:ring-offset-background focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:outline-none lg:flex',
                  workspaceMode !== 'settings' && 'lg:hidden'
                )}
              >
                <ChevronLeft
                  aria-hidden="true"
                  className="text-muted-foreground/55 group-hover:text-primary h-5 w-5 transition-colors"
                  strokeWidth={2.25}
                />
                <span className="sr-only">{t('header.switchSidebarToHover')}</span>
              </button>
            )}
          </div>

          <div className="flex min-w-0 flex-1 items-center justify-end gap-0.5 sm:gap-1">
            {/* 工作区切换：复用 Tabs 组件 + Motion 动画指示器 */}
            <LayoutGroup id="workspace-switcher">
              <div
                ref={workspaceTabsMeasureRef}
                data-dashboard-workspace-tabs-measure="true"
                aria-hidden="true"
                className="pointer-events-none invisible absolute top-0 left-0 inline-flex h-9 items-center justify-center gap-0.5 rounded-lg p-1"
              >
                {WORKSPACE_TABS.map(({ value, icon: Icon, labelKey }) => (
                  <div
                    key={value}
                    className="inline-flex h-7 items-center justify-center gap-1.5 rounded-md px-2.5 text-sm font-medium whitespace-nowrap"
                  >
                    <Icon className="h-3.5 w-3.5" />
                    <span className="font-sans text-base font-semibold tracking-wider uppercase">
                      {t(labelKey)}
                    </span>
                  </div>
                ))}
              </div>
              <Tabs value={workspaceMode} aria-label={t('workspace.switcherLabel')}>
                <TabsList
                  ref={workspaceTabsRef}
                  data-dashboard-workspace-tabs="true"
                  className="relative h-9 gap-0.5 border-0 bg-transparent p-1 shadow-none"
                  onPointerLeave={() => {
                    if (workspaceHoverTimerRef.current !== null) {
                      clearTimeout(workspaceHoverTimerRef.current)
                      workspaceHoverTimerRef.current = null
                    }
                    if (workspaceHoverLocked && hoveredWorkspace !== workspaceMode) {
                      return
                    }
                    workspaceHoverTimerRef.current = setTimeout(() => {
                      setHoveredWorkspace(null)
                      setWorkspaceHoverLocked(false)
                      workspaceHoverTimerRef.current = null
                    }, WORKSPACE_HOVER_LEAVE_DELAY_MS)
                  }}
                >
                  {WORKSPACE_TABS.map(({ value, to, icon: Icon, labelKey }) => (
                    <TabsTrigger
                      key={value}
                      asChild
                      value={value}
                      data-workspace-highlighted={
                        highlightedHeaderAction === null &&
                        (hoveredWorkspace ?? workspaceMode) === value
                          ? 'true'
                          : 'false'
                      }
                      className={cn(
                        'relative h-7 gap-1.5 bg-transparent text-sm font-medium data-[state=active]:bg-transparent data-[state=active]:shadow-none',
                        highlightedHeaderAction === null &&
                          (hoveredWorkspace ?? workspaceMode) === value
                          ? 'text-primary-foreground'
                          : 'text-muted-foreground',
                        workspaceTabsCompact ? 'px-2' : 'px-2.5'
                      )}
                    >
                      <Link
                        to={to}
                        onPointerEnter={() => {
                          if (!workspaceHoverLocked) {
                            if (workspaceHoverTimerRef.current !== null) {
                              clearTimeout(workspaceHoverTimerRef.current)
                              workspaceHoverTimerRef.current = null
                            }
                            setHoveredWorkspace(value)
                          }
                        }}
                        onClick={(event) => {
                          if (workspaceHoverTimerRef.current !== null) {
                            clearTimeout(workspaceHoverTimerRef.current)
                            workspaceHoverTimerRef.current = null
                          }
                          const isPlainPrimaryClick =
                            event.button === 0 &&
                            !event.metaKey &&
                            !event.ctrlKey &&
                            !event.shiftKey &&
                            !event.altKey
                          if (isPlainPrimaryClick) {
                            setHoveredWorkspace(value)
                            setWorkspaceHoverLocked(true)
                          }
                          if (
                            workspaceMode === value ||
                            !isPlainPrimaryClick
                          ) {
                            return
                          }
                          event.preventDefault()
                          onWorkspaceNavigate(to)
                        }}
                      >
                        {highlightedHeaderAction === null &&
                          (hoveredWorkspace ?? workspaceMode) === value && (
                          <motion.span
                            layoutId="topbar-selection-pill"
                            className="bg-primary absolute inset-x-1 inset-y-0.5 -z-10 rounded-sm shadow-sm"
                            transition={{ type: 'spring', stiffness: 480, damping: 38, mass: 0.6 }}
                          />
                        )}
                        <Icon className="h-3.5 w-3.5" />
                        <span
                          className={cn(
                            'hidden font-sans text-base font-semibold tracking-wider uppercase',
                            !workspaceTabsCompact && 'sm:inline'
                          )}
                        >
                          {t(labelKey)}
                        </span>
                      </Link>
                    </TabsTrigger>
                  ))}
                </TabsList>
              </Tabs>
            {focusCompanionEnabled && (
              <>
                <div className="bg-border hidden h-6 w-px sm:block" />
                <Button
                  asChild
                  variant="ghost"
                  size="icon"
                  className={cn(pathname === '/focus' && 'bg-accent text-accent-foreground')}
                  title={t('sidebar.menu.focusCompanion')}
                  aria-label={t('sidebar.menu.focusCompanion')}
                >
                  <Link to="/focus">
                    <TimerReset className="h-4 w-4" />
                  </Link>
                </Button>
              </>
            )}
            <Button
              asChild
              variant="ghost"
              size="icon"
              data-dashboard-header-action="true"
              data-header-action-highlighted={
                highlightedHeaderAction === 'settings' ? 'true' : 'false'
              }
              className="relative isolate border-0 bg-transparent shadow-none"
              title={t('sidebar.menu.settings')}
              aria-label={t('sidebar.menu.settings')}
            >
              <Link
                to="/settings"
                onPointerEnter={() => handleHeaderActionEnter('settings')}
                onPointerLeave={handleHeaderActionLeave}
                onClick={() => setHoveredHeaderAction('settings')}
              >
                {renderHeaderActionPill('settings')}
                <Settings className="h-4 w-4" />
              </Link>
            </Button>
            {/* 后端切换按钮（仅 Electron） */}
            {isElectron() && (
              <>
                <Button
                  variant="ghost"
                  size="sm"
                  className="gap-2"
                  onClick={() => setBackendManagerOpen(true)}
                  title={t('header.toggleConnection')}
                >
                  <Database className="h-4 w-4" />
                  <span className="text-muted-foreground hidden max-w-25 truncate text-xs sm:inline">
                    {activeBackendName}
                  </span>
                </Button>
                <BackendManager open={backendManagerOpen} onOpenChange={setBackendManagerOpen} />
                <div className="bg-border h-6 w-px" />
              </>
            )}
            {/* 搜索框 */}
            <Button
              variant="ghost"
              size="icon"
              onClick={() => {
                setHoveredHeaderAction('search')
                onSearchOpenChange(!searchOpen)
              }}
              aria-label={t('header.searchPlaceholder')}
              title={t('header.searchPlaceholder')}
              data-dashboard-header-action="true"
              data-header-action-highlighted={
                highlightedHeaderAction === 'search' ? 'true' : 'false'
              }
              onPointerEnter={() => handleHeaderActionEnter('search')}
              onPointerLeave={handleHeaderActionLeave}
              className="relative isolate hidden border-0 bg-transparent shadow-none md:inline-flex"
            >
              {renderHeaderActionPill('search')}
              <Search className="h-4 w-4" />
            </Button>

            {/* 搜索对话框 */}
            {(searchOpen || searchDialogLoaded) && (
              <Suspense fallback={null}>
                <SearchDialog open={searchOpen} onOpenChange={onSearchOpenChange} />
              </Suspense>
            )}

            {/* 麦麦文档链接 */}
            <Button
              variant="ghost"
              size="icon"
              onClick={() => {
                setHoveredHeaderAction('docs')
                window.open('https://docs.mai-mai.org', '_blank')
              }}
              className="relative isolate hidden border-0 bg-transparent shadow-none sm:inline-flex"
              title={t('header.viewDocs')}
              aria-label={t('header.viewDocs')}
              data-dashboard-header-action="true"
              data-header-action-highlighted={
                highlightedHeaderAction === 'docs' ? 'true' : 'false'
              }
              onPointerEnter={() => handleHeaderActionEnter('docs')}
              onPointerLeave={handleHeaderActionLeave}
            >
              {renderHeaderActionPill('docs')}
              <BookOpen className="h-4 w-4" />
            </Button>

            {/* 语言切换 */}
            <div className="hidden sm:block">
              <DropdownMenu open={languageMenuOpen} onOpenChange={setLanguageMenuOpen}>
                <DropdownMenuTrigger asChild>
                  <Button
                    variant="ghost"
                    size="icon"
                    title={t('header.switchLanguage')}
                    aria-label={t('header.switchLanguage')}
                    data-dashboard-header-action="true"
                    data-header-action-highlighted={
                      highlightedHeaderAction === 'language' ? 'true' : 'false'
                    }
                    onPointerEnter={() => handleHeaderActionEnter('language')}
                    onPointerLeave={handleHeaderActionLeave}
                    onClick={() => setHoveredHeaderAction('language')}
                    className="relative isolate border-0 bg-transparent shadow-none"
                  >
                    {renderHeaderActionPill('language')}
                    <Globe className="h-4 w-4" />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end">
                  {LANGUAGE_CODES.map((code) => (
                    <DropdownMenuItem
                      key={code}
                      onClick={() => i18nInstance.changeLanguage(code)}
                      className={cn(
                        'cursor-pointer',
                        currentLang.split('-')[0] === code && 'text-primary font-semibold'
                      )}
                    >
                      {currentLang.split('-')[0] === code && <Check className="mr-2 h-3 w-3" />}
                      {LANGUAGE_NAMES[code]}
                    </DropdownMenuItem>
                  ))}
                </DropdownMenuContent>
              </DropdownMenu>
            </div>

            {/* 主题切换按钮 */}
            <Button
              variant="ghost"
              size="icon"
              onClick={(e) => {
                setHoveredHeaderAction('theme')
                const newTheme = actualTheme === 'dark' ? 'light' : 'dark'
                toggleThemeWithTransition(newTheme, onThemeChange, e)
              }}
              onPointerEnter={() => handleHeaderActionEnter('theme')}
              onPointerLeave={handleHeaderActionLeave}
              data-dashboard-header-action="true"
              data-header-action-highlighted={
                highlightedHeaderAction === 'theme' ? 'true' : 'false'
              }
              aria-label={
                actualTheme === 'dark' ? t('header.switchToLight') : t('header.switchToDark')
              }
              className="relative isolate hidden border-0 bg-transparent shadow-none sm:inline-flex"
            >
              {renderHeaderActionPill('theme')}
              {actualTheme === 'dark' ? (
                <Sun className="h-5 w-5" />
              ) : (
                <Moon className="h-5 w-5" />
              )}
            </Button>

            {/* 分隔线 */}
            <div className="bg-border hidden h-6 w-px sm:block" />

            {/* 登出按钮 */}
            <Button
              variant="ghost"
              size="icon"
              onClick={() => {
                setHoveredHeaderAction('logout')
                void handleLogout()
              }}
              title={t('header.logout')}
              aria-label={t('header.logout')}
              data-dashboard-header-action="true"
              data-header-action-highlighted={
                highlightedHeaderAction === 'logout' ? 'true' : 'false'
              }
              onPointerEnter={() => handleHeaderActionEnter('logout')}
              onPointerLeave={handleHeaderActionLeave}
              className="relative isolate hidden border-0 bg-transparent shadow-none sm:inline-flex"
            >
              {renderHeaderActionPill('logout')}
              <LogOut className="h-4 w-4" />
            </Button>
            </LayoutGroup>

            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button
                  variant="ghost"
                  size="icon"
                  className="sm:hidden"
                  title={t('header.moreActions')}
                  aria-label={t('header.moreActions')}
                >
                  <MoreHorizontal className="h-4 w-4" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-48">
                <DropdownMenuItem
                  onClick={(event) => {
                    const newTheme = actualTheme === 'dark' ? 'light' : 'dark'
                    toggleThemeWithTransition(newTheme, onThemeChange, event)
                  }}
                  className="cursor-pointer gap-2"
                >
                  {actualTheme === 'dark' ? (
                    <Sun className="h-4 w-4" />
                  ) : (
                    <Moon className="h-4 w-4" />
                  )}
                  {actualTheme === 'dark' ? t('header.switchToLight') : t('header.switchToDark')}
                </DropdownMenuItem>
                <DropdownMenuSub>
                  <DropdownMenuSubTrigger className="cursor-pointer gap-2">
                    <Globe className="h-4 w-4" />
                    {t('header.switchLanguage')}
                  </DropdownMenuSubTrigger>
                  <DropdownMenuSubContent alignOffset={-4}>
                    {LANGUAGE_CODES.map((code) => (
                      <DropdownMenuItem
                        key={code}
                        onClick={() => i18nInstance.changeLanguage(code)}
                        className={cn(
                          'cursor-pointer',
                          currentLang.split('-')[0] === code && 'text-primary font-semibold'
                        )}
                      >
                        {currentLang.split('-')[0] === code && <Check className="mr-2 h-3 w-3" />}
                        {LANGUAGE_NAMES[code]}
                      </DropdownMenuItem>
                    ))}
                  </DropdownMenuSubContent>
                </DropdownMenuSub>
                {focusCompanionEnabled && (
                  <>
                    <DropdownMenuSeparator />
                    <DropdownMenuItem asChild className="cursor-pointer gap-2">
                      <Link to="/focus">
                        <TimerReset className="h-4 w-4" />
                        {t('sidebar.menu.focusCompanion')}
                      </Link>
                    </DropdownMenuItem>
                  </>
                )}
                <DropdownMenuItem onClick={handleLogout} className="cursor-pointer gap-2">
                  <LogOut className="h-4 w-4" />
                  {t('header.logoutLabel')}
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        </div>

        <div className="absolute right-5 bottom-[-7px] z-10 flex h-0 shrink-0 items-center justify-end sm:right-15.5">
          <button
            type="button"
            data-dashboard-topbar-toggle="true"
            onClick={onTopbarToggle}
            aria-label={topbarCollapsed ? t('header.expandTopbar') : t('header.collapseTopbar')}
            aria-expanded={!topbarCollapsed}
            title={topbarCollapsed ? t('header.expandTopbar') : t('header.collapseTopbar')}
            className="bg-foreground/70 flex h-3 w-24 shrink-0 items-center justify-center transition-shadow"
          >
            <span className="sr-only">
              {topbarCollapsed ? t('header.expandTopbar') : t('header.collapseTopbar')}
            </span>
          </button>
        </div>
      </div>
    </motion.header>
  )
}
