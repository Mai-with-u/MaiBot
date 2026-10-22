import { Link } from '@tanstack/react-router'
import {
  BookOpen,
  ChevronLeft,
  ChevronsUp,
  Database,
  FileText,
  Moon,
  MoreHorizontal,
  Search,
  Settings,
  SlidersHorizontal,
  Sun,
} from 'lucide-react'
import { LayoutGroup, motion } from 'motion/react'
import {
  lazy,
  Suspense,
  type ComponentType,
  useContext,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from 'react'
import { useTranslation } from 'react-i18next'

import { BackgroundLayer } from '@/components/background-layer'
import { BackendManager } from '@/components/electron/BackendManager'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { toggleThemeWithTransition } from '@/components/use-theme'
import { useBackground } from '@/hooks/use-background'
import { isElectron } from '@/lib/runtime'
import { ThemeProviderContext } from '@/lib/theme-context'
import { cn } from '@/lib/utils'
import { extensionIcons, extensionPath, extensionWorkspace } from '@/lib/plugin-webui'
import type { WebUIExtension } from '@/lib/plugin-webui'
import { splitWorkspaceTabs } from '@/lib/workspace-tabs'

import { HeaderUpdateButton } from './HeaderUpdateButton'
import { TopbarMascot } from './TopbarMascot'
import type { WorkspaceMode } from './types'

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
  to: string
  icon: ComponentType<{ className?: string }>
  labelKey: string
}> = [
  { value: 'settings', to: '/', icon: SlidersHorizontal, labelKey: 'workspace.settings' },
  { value: 'logs', to: '/logs', icon: FileText, labelKey: 'workspace.logs' },
]

interface HeaderProps {
  extensions?: WebUIExtension[]
  /** 工作区切换动画期间隐藏日志视图切换，让它随页面一起淡入淡出 */
  logSwitcherHidden?: boolean
  /** 侧栏滑入/滑出期间让顶栏延伸到侧栏下方，顶栏自身保持不动 */
  sidebarUnderlay?: boolean
  sidebarOpen: boolean
  searchOpen: boolean
  actualTheme: 'light' | 'dark'
  onSidebarToggle: () => void
  onSearchOpenChange: (open: boolean) => void
  onThemeChange: (theme: 'light' | 'dark' | 'system') => void
  onTopbarToggle: () => void
  onWorkspaceNavigate: (to: string) => void
  topbarCollapsed: boolean
  workspaceMode: WorkspaceMode
}

type HeaderActionId =
  | 'update'
  | 'settings'
  | 'docs'
  | 'theme'
  | 'collapse'
  | 'extensions'

export function Header({
  extensions = [],
  logSwitcherHidden = false,
  sidebarUnderlay = false,
  sidebarOpen,
  searchOpen,
  actualTheme,
  onSidebarToggle,
  onSearchOpenChange,
  onThemeChange,
  onTopbarToggle,
  onWorkspaceNavigate,
  topbarCollapsed,
  workspaceMode,
}: HeaderProps) {
  const { t } = useTranslation()
  const [preloadMobileSearch, setPreloadMobileSearch] = useState(false)

  useEffect(() => {
    if (!window.matchMedia('(max-width: 1023.98px)').matches) return
    // 首屏显示后提前挂载关闭状态的搜索面板，首次点击不再等待下载组件。
    const timer = window.setTimeout(() => setPreloadMobileSearch(true), 300)
    return () => window.clearTimeout(timer)
  }, [])
  const pluginTabs = extensions.flatMap((extension) => {
    const page = extension.pages.find((page) => page.placement === 'workspace')
    return page
      ? [
          {
            value: extensionWorkspace(extension.plugin_id),
            to: extensionPath(extension.plugin_id, page.id),
            icon: extensionIcons[page.icon],
            labelKey: extension.workspace_title ?? extension.plugin_id,
            literal: true,
          },
        ]
      : []
  })
  // 顶栏最多直接展示两个插件工作区，其余收进“更多”；当前工作区保持可见。
  const activePluginTab = pluginTabs.find((tab) => tab.value === workspaceMode)
  const { visible: visiblePluginTabs, overflow: overflowTabs } = splitWorkspaceTabs(pluginTabs, activePluginTab)
  const workspaceTabs = [
    ...WORKSPACE_TABS.map((tab) => ({ ...tab, literal: false })),
    ...visiblePluginTabs,
  ]
  const workspaceTabsKey = workspaceTabs.map((tab) => `${tab.value}:${tab.labelKey}`).join('|')
  const { themeConfig } = useContext(ThemeProviderContext)
  // 千禧风格的顶栏要放得下键帽，比其它风格高一截；高度由动画驱动，所以在这里按风格取值。
  const expandedTopbarHeight = themeConfig.dashboardStyle === 'millennium' ? 52 : 42
  // 原版风格和千禧一样用顶栏里的按钮收起顶栏，不再显示顶栏下沿的滑条。
  const isModernStyle = themeConfig.dashboardStyle === 'modern'
  const { config: headerBg, inheritedFrom } = useBackground('header')
  const inheritsPageBackground = inheritedFrom === 'page'
  const [searchQuery, setSearchQuery] = useState('')
  const [aiSearchRequestId, setAISearchRequestId] = useState(0)
  const [backendManagerOpen, setBackendManagerOpen] = useState(false)
  const [activeBackendName, setActiveBackendName] = useState<string>('')
  const [workspaceTabsCompact, setWorkspaceTabsCompact] = useState(false)
  const [hoveredWorkspace, setHoveredWorkspace] = useState<WorkspaceMode | null>(null)
  const [workspaceHoverLocked, setWorkspaceHoverLocked] = useState(false)
  const [hoveredHeaderAction, setHoveredHeaderAction] = useState<HeaderActionId | null>(null)
  const logSwitcherShown = workspaceMode === 'logs' && !logSwitcherHidden
  const [logSwitcherSettled, setLogSwitcherSettled] = useState(logSwitcherShown)
  // 桌面端侧栏当前占据的布局宽度（px）；小屏侧栏不占布局，探针不显示，取 0。
  const [sidebarInset, setSidebarInset] = useState(0)
  const sidebarInsetProbeRef = useRef<HTMLSpanElement | null>(null)
  const [topbarRowGap, setTopbarRowGap] = useState(0)
  // 只有千禧顶栏的日志槽位参与正常布局，其它风格是绝对定位，不需要让位。
  // 槽位自身会多带一段行间距，让位宽度里要扣掉，工作区键才正好停在原位。
  const logSwitcherInset =
    themeConfig.dashboardStyle === 'millennium' ? Math.max(0, sidebarInset - topbarRowGap) : 0
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

  // 首帧就要量到侧栏宽度：侧栏顶部此时已让出底色，顶栏晚一帧垫过去会闪一下页面底色。
  useLayoutEffect(() => {
    const probe = sidebarInsetProbeRef.current
    if (!probe) return
    setSidebarInset(probe.offsetWidth)
    const resizeObserver = new ResizeObserver(() => {
      setSidebarInset(probe.offsetWidth)
      const row = document.getElementById('log-viewer-topbar-tabs')?.parentElement
      setTopbarRowGap(row ? parseFloat(window.getComputedStyle(row).columnGap) || 0 : 0)
    })
    resizeObserver.observe(probe)
    return () => resizeObserver.disconnect()
  }, [])

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

  if (workspaceHoverLocked && hoveredWorkspace === workspaceMode) {
    setHoveredWorkspace(null)
    setWorkspaceHoverLocked(false)
  }


  useEffect(() => {
    if (workspaceMode !== 'logs' || themeConfig.dashboardStyle === 'millennium') {
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
  }, [workspaceMode, workspaceTabsKey, themeConfig.dashboardStyle])

  const highlightedHeaderAction = hoveredWorkspace === null ? hoveredHeaderAction : null

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
        data-dashboard-topbar-pill="true"
        className="bg-primary pointer-events-none absolute inset-x-1 inset-y-0 -z-10 rounded-sm shadow-sm"
        transition={{ type: 'spring', stiffness: 480, damping: 38, mass: 0.6 }}
      />
    ) : null

  return (
    <motion.header
      data-dashboard-header="true"
      data-dashboard-header-collapsed={topbarCollapsed ? 'true' : undefined}
      initial={false}
      animate={{ height: topbarCollapsed ? 16 : expandedTopbarHeight, marginBottom: 0 }}
      transition={{ duration: 0.18, ease: [0.22, 1, 0.36, 1] }}
      style={
        sidebarUnderlay && sidebarInset > 0 && !topbarCollapsed
          ? // 向左延伸到侧栏下方并用等量内边距抵消，内容位置不变；侧栏滑走后露出的仍是顶栏。
            { marginLeft: -sidebarInset, paddingLeft: `calc(1rem + ${sidebarInset}px)` }
          : undefined
      }
      className={cn(
        'sticky top-0 isolate z-30 min-w-0 overflow-visible',
        topbarCollapsed ? 'h-4' : 'flex h-[42px] flex-col border-b px-3 backdrop-blur-md sm:px-4',
        // 手机上不显示顶栏：页面自带大标题，搜索与设置分别由标签栏和首页头像承担
        'max-lg:hidden',
        topbarCollapsed || inheritsPageBackground ? 'bg-transparent' : 'bg-background'
      )}
    >
      <span
        ref={sidebarInsetProbeRef}
        aria-hidden="true"
        className={cn(
          'pointer-events-none invisible absolute top-0 left-0 hidden h-0 lg:block',
          sidebarOpen
            ? 'w-[var(--layout-sidebar-width)]'
            : 'w-[var(--layout-sidebar-collapsed-width)]'
        )}
      />
      {topbarCollapsed && (
        <div
          data-dashboard-header-strip="true"
          className={cn(
            'relative z-30 flex h-4 min-w-0 items-center justify-end px-3 backdrop-blur-md sm:px-15',
            inheritsPageBackground ? 'bg-transparent' : 'bg-background'
          )}
        >
          {!inheritsPageBackground && <BackgroundLayer config={headerBg} layerId="header" />}
          {sidebarOpen && !isModernStyle && (
            <button
              type="button"
              data-dashboard-sidebar-mode-switch="true"
              onClick={onSidebarToggle}
              aria-label={t('header.switchSidebarToHover')}
              aria-expanded="true"
              title={t('header.switchSidebarToHover')}
              className={cn(
                'group absolute top-1/2 left-0 z-20 hidden h-5 w-7 -translate-y-1/2 items-center justify-center focus-visible:ring-ring focus-visible:ring-offset-background focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:outline-none lg:flex',
                workspaceMode === 'logs' && 'lg:hidden'
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
            data-topbar-mascot-toggle="true"
            onClick={onTopbarToggle}
            aria-label={t('header.expandTopbar')}
            aria-expanded={!topbarCollapsed}
            title={t('header.expandTopbar')}
            className="bg-foreground/70 relative z-10 h-2 w-24 transition-shadow"
          >
            <TopbarMascot />
            <span className="sr-only">{t('header.expandTopbar')}</span>
          </button>
        </div>
      )}

      {!topbarCollapsed && !inheritsPageBackground && (
        <BackgroundLayer config={headerBg} layerId="header" />
      )}
      <div className={cn(topbarCollapsed ? 'hidden' : 'contents')}>
        <div className="relative z-10 flex h-full min-h-0 items-center justify-between gap-2">
          <div className="flex min-w-0 shrink-0 items-center gap-2 sm:gap-4">

            {/* 固定模式显示左折线；悬浮模式展开后由侧栏内的右折线切回固定模式。
                原版风格的这枚开关放在侧栏 Logo 行右侧，这里不再渲染。 */}
            {sidebarOpen && !isModernStyle && (
              <button
                type="button"
                data-dashboard-sidebar-mode-switch="true"
                onClick={onSidebarToggle}
                aria-label={t('header.switchSidebarToHover')}
                aria-expanded="true"
                title={t('header.switchSidebarToHover')}
                className={cn(
                  'group absolute top-1/2 left-0 z-20 hidden h-14 w-7 -translate-y-1/2 items-center justify-center focus-visible:ring-ring focus-visible:ring-offset-background focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:outline-none lg:flex',
                  workspaceMode === 'logs' && 'lg:hidden'
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
                {workspaceTabs.map(({ value, icon: Icon, labelKey, literal }) => (
                  <div
                    key={value}
                    className="inline-flex h-7 items-center justify-center gap-1.5 rounded-md px-2 text-sm font-medium whitespace-nowrap"
                  >
                    <Icon className="h-3.5 w-3.5" />
                    <span className="font-sans text-base font-semibold tracking-wider uppercase">
                      {literal ? labelKey : t(labelKey)}
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
                  {workspaceTabs.map(({ value, to, icon: Icon, labelKey, literal }) => (
                    <TabsTrigger
                      size="sm"
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
                        workspaceTabsCompact ? 'px-1.5' : 'px-2'
                      )}
                    >
                      <Link
                        to={to}
                        title={literal ? labelKey : t(labelKey)}
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
                            data-dashboard-topbar-pill="true"
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
                          {literal ? labelKey : t(labelKey)}
                        </span>
                      </Link>
                    </TabsTrigger>
                  ))}
                </TabsList>
              </Tabs>
              {overflowTabs.length > 0 && (
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button
                      variant="ghost"
                      size="icon"
                      title={t('pluginWebUI.more')}
                      aria-label={t('pluginWebUI.more')}
                      data-dashboard-header-action="true"
                      data-dashboard-workspace-more="true"
                      data-header-action-highlighted={
                        highlightedHeaderAction === 'extensions' ? 'true' : 'false'
                      }
                      onPointerEnter={() => handleHeaderActionEnter('extensions')}
                      onPointerLeave={handleHeaderActionLeave}
                      className="relative isolate shrink-0 border-0 bg-transparent shadow-none"
                    >
                      {renderHeaderActionPill('extensions')}
                      <MoreHorizontal className="h-4 w-4" />
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="end">
                    {overflowTabs.map((tab) => (
                      <DropdownMenuItem
                        key={tab.value}
                        onSelect={() => onWorkspaceNavigate(tab.to)}
                      >
                        <tab.icon className="mr-2 h-4 w-4" />
                        {tab.labelKey}
                      </DropdownMenuItem>
                    ))}
                  </DropdownMenuContent>
                </DropdownMenu>
              )}
              {/* 顶栏搜索：提交问题后打开搜索窗口并自动执行 AI 搜索。 */}
              <form
                role="search"
                data-dashboard-header-search="true"
                onSubmit={(event) => {
                  event.preventDefault()
                  if (searchQuery.trim()) {
                    setAISearchRequestId((current) => current + 1)
                  }
                  onSearchOpenChange(true)
                }}
                className={cn(
                  'relative ml-2 flex min-w-0 flex-1 items-center sm:ml-3 sm:max-w-72',
                  // 原版风格在手机上由底部标签栏的搜索按钮负责搜索，顶栏不再重复放搜索框
                  themeConfig.dashboardStyle !== 'millennium' && 'max-lg:hidden',
                  workspaceMode === 'logs' && themeConfig.dashboardStyle !== 'millennium'
                    ? 'mr-auto sm:mr-0 sm:w-48 sm:flex-none'
                    : 'mr-auto'
                )}
              >
                <Input
                  type="search"
                  value={searchQuery}
                  onChange={(event) => setSearchQuery(event.target.value)}
                  placeholder={t('header.searchPlaceholder')}
                  aria-label={t('header.searchPlaceholder')}
                  className="h-8 min-w-0 pr-9 text-sm"
                />
                <button
                  type="submit"
                  aria-label={t('header.searchPlaceholder')}
                  className="text-muted-foreground hover:text-foreground absolute right-1 flex h-7 w-7 items-center justify-center rounded-sm"
                >
                  <Search className="h-4 w-4" />
                </button>
              </form>
              {/* 千禧风格下日志切换槽位放在搜索框右侧，并参与正常布局；其他风格保留左侧定位。
                  宽度从 0 展开，入场/退场只做一次单向移动。 */}
              <motion.div
                id="log-viewer-topbar-tabs"
                className={cn(
                  'absolute top-1/2 left-0 hidden min-w-0 shrink-0 -translate-y-1/2 items-center [&>*]:shrink-0',
                  workspaceMode === 'logs' && 'sm:flex',
                  // 展开/收起过程中裁掉尚未露出的部分；静止后放开，避免裁掉键帽阴影。
                  !(logSwitcherShown && logSwitcherSettled) && 'pointer-events-none overflow-x-clip'
                )}
                initial={false}
                animate={
                  logSwitcherShown
                    ? { opacity: 1, width: 'auto', marginLeft: 0 }
                    : { opacity: 0, width: 0, marginLeft: themeConfig.dashboardStyle === 'millennium' ? 0 : logSwitcherInset }
                }
                transition={
                  logSwitcherShown
                    ? // 宽度动画跑在主线程：等日志页挂载、上滑完成后再展开，避开最重的那几帧
                      { duration: 0.36, delay: 0.28, ease: [0.22, 1, 0.36, 1] }
                    : { duration: 0.28, ease: [0.22, 1, 0.36, 1] }
                }
                onAnimationComplete={() => setLogSwitcherSettled(logSwitcherShown)}
              />

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
            {/* 搜索对话框 */}
            {(searchOpen || searchDialogLoaded || preloadMobileSearch) && (
              <Suspense fallback={null}>
                <SearchDialog
                  open={searchOpen}
                  onOpenChange={onSearchOpenChange}
                  query={searchQuery}
                  onQueryChange={setSearchQuery}
                  aiSearchRequestId={aiSearchRequestId}
                />
              </Suspense>
            )}

            <HeaderUpdateButton
              highlighted={highlightedHeaderAction === 'update'}
              onPointerEnter={() => handleHeaderActionEnter('update')}
              onPointerLeave={handleHeaderActionLeave}
            >
              {renderHeaderActionPill('update')}
            </HeaderUpdateButton>

            {/* WebUI 设置 */}
            <span data-dashboard-header-vent="true" aria-hidden="true" className="hidden" />
            <Button
              variant="ghost"
              size="icon"
              asChild
              className="relative isolate hidden border-0 bg-transparent shadow-none sm:inline-flex"
            >
              <Link
                to="/settings"
                title={t('sidebar.menu.settings')}
                aria-label={t('sidebar.menu.settings')}
                data-dashboard-header-action="true"
                data-header-action-highlighted={
                  highlightedHeaderAction === 'settings' ? 'true' : 'false'
                }
                onPointerEnter={() => handleHeaderActionEnter('settings')}
                onPointerLeave={handleHeaderActionLeave}
              >
                {renderHeaderActionPill('settings')}
                <Settings className="h-4 w-4" />
              </Link>
            </Button>

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

            {/* 千禧风格用一颗键帽收起顶栏，取代顶栏下沿的滑条；原版风格同样改为顶栏按钮 */}
            {(themeConfig.dashboardStyle === 'millennium' || isModernStyle) && (
              <Button
                variant="ghost"
                size="icon"
                onClick={onTopbarToggle}
                title={t('header.collapseTopbar')}
                aria-label={t('header.collapseTopbar')}
                aria-expanded={!topbarCollapsed}
                data-dashboard-header-action="true"
                data-dashboard-topbar-collapse-key="true"
                data-header-action-highlighted={
                  isModernStyle && highlightedHeaderAction === 'collapse' ? 'true' : 'false'
                }
                onPointerEnter={
                  isModernStyle ? () => handleHeaderActionEnter('collapse') : undefined
                }
                onPointerLeave={isModernStyle ? handleHeaderActionLeave : undefined}
                className="relative isolate hidden border-0 bg-transparent shadow-none sm:inline-flex"
              >
                {isModernStyle && renderHeaderActionPill('collapse')}
                <ChevronsUp className="h-5 w-5" />
              </Button>
            )}

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
                <DropdownMenuItem asChild>
                  <Link to="/settings" className="cursor-pointer gap-2">
                    <Settings className="h-4 w-4" />
                    {t('sidebar.menu.settings')}
                  </Link>
                </DropdownMenuItem>
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
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        </div>

        {/* 顶栏收起拉片只服务桌面布局，手机上不显示；原版风格改用顶栏里的收起按钮 */}
        {!isModernStyle && (
          <div className="absolute right-5 bottom-[-7px] z-10 flex h-0 shrink-0 items-center justify-end max-lg:hidden sm:right-15.5">
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
        )}
      </div>
    </motion.header>
  )
}
