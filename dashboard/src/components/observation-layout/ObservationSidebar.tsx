import { Link, useRouterState } from '@tanstack/react-router'
import { useQuery } from '@tanstack/react-query'
import {
  BookOpen,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronsUpDown,
  MoreHorizontal,
  Moon,
  PanelLeftClose,
  PanelLeftOpen,
  Search,
  Settings,
  Sun,
} from 'lucide-react'
import { motion, useReducedMotion } from 'motion/react'
import { useContext, useEffect, useRef, useState, type ReactElement } from 'react'
import { useTranslation } from 'react-i18next'

import { SidebarUpdateButton } from '@/components/layout/SidebarUpdateButton'
import type { MenuItem } from '@/components/layout/types'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu'
import { PromptWarningDot } from '@/components/ui/prompt-warning-dot'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { toggleThemeWithTransition, useTheme } from '@/components/use-theme'
import { promptCatalogQueryOptions } from '@/lib/prompt-query'
import { TourContext } from '@/components/tour/tour-context'
import { isDesktopEmbedded, reportDesktopLayout } from '@/lib/desktop-host'
import { queryClient } from '@/lib/query'
import { isBotIndependentPath, useBotAvailable } from '@/host/state'
import '@/styles/observation-key-state.css'
import { BotSwitcher } from '@/host/BotSwitcher'

import { ObservationConversations } from './ObservationConversations'
import { SidebarSettingsMenu } from './SidebarSettingsMenu'

const SETTINGS_OPEN_STORAGE_KEY = 'maibot-observation-settings-open'
const SETTINGS_LAYOUT_STORAGE_KEY = 'maibot-observation-settings-layout'

interface SettingsLayout {
  rows: number
  order: string[]
}

function loadSettingsLayout(): SettingsLayout {
  try {
    const saved = localStorage.getItem(SETTINGS_LAYOUT_STORAGE_KEY)
    if (!saved) return { rows: 2, order: [] }
    const parsed: unknown = JSON.parse(saved)
    if (typeof parsed !== 'object' || parsed === null) return { rows: 2, order: [] }
    return {
      rows: 'rows' in parsed && (parsed.rows === 1 || parsed.rows === 2 || parsed.rows === 3) ? parsed.rows : 2,
      order: 'order' in parsed && Array.isArray(parsed.order)
        ? [...new Set(parsed.order.filter((path): path is string => typeof path === 'string'))]
        : [],
    }
  } catch {
    // 浏览器存储不可用或旧偏好损坏时，仍可在当前页面调整布局。
    return { rows: 2, order: [] }
  }
}
const LOG_PATHS = ['/logs', '/statistics', '/reasoning-process']

/** 侧栏键位空间有限，常用入口用短名；完整名称仍在悬停提示和读屏里。 */
const SHORT_LABELS: Record<string, string> = {
  '/config/bot': 'observation.keys.bot',
  '/config/model': 'observation.keys.model',
  '/adapter-management': 'observation.keys.adapter',
  '/resource/learning': 'observation.keys.learning',
  '/chat-management': 'observation.keys.mutualGroups',
  '/resource/knowledge-base': 'observation.keys.memory',
  '/plugin-config': 'observation.keys.plugin',
  '/plugins': 'observation.keys.market',
  '/config/prompts': 'observation.keys.prompt',
  '/data-transfer': 'observation.keys.data',
  '/logs': 'observation.keys.logs',
}

function isItemActive(item: MenuItem, pathname: string): boolean {
  if (item.path === '/logs')
    return LOG_PATHS.some((path) => pathname === path || pathname.startsWith(`${path}/`))
  return pathname === item.path || pathname.startsWith(`${item.path}/`)
}

function loadSettingsOpen(): boolean {
  try {
    return localStorage.getItem(SETTINGS_OPEN_STORAGE_KEY) !== 'false'
  } catch {
    return true
  }
}

/**
 * 侧栏键位的悬停提示：只在鼠标悬停或键盘聚焦时出现，鼠标离开、点击后立即收起。
 * Radix 默认会在鼠标移到提示上时保持打开，而提示正好盖在下一排键上，往下移鼠标时会一直挂着。
 */
function KeyTooltip({
  label,
  side,
  children,
}: {
  label: string
  side: 'right' | 'bottom'
  children: ReactElement
}) {
  const [open, setOpen] = useState(false)
  const triggerRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    let pointer: { x: number; y: number } | null = null
    let frame: number | null = null
    const updateAfterScroll = () => {
      if (frame !== null) cancelAnimationFrame(frame)
      // Radix 会在滚动时关闭提示；等滚动和命中区域更新后，展开鼠标实际停留的键位。
      frame = requestAnimationFrame(() => {
        frame = null
        if (!pointer) return
        const target = document.elementFromPoint(pointer.x, pointer.y)
        setOpen(target !== null && triggerRef.current?.contains(target) === true)
      })
    }
    const trackPointer = (event: PointerEvent) => {
      if (event.pointerType === 'mouse') pointer = { x: event.clientX, y: event.clientY }
    }
    const trackWheel = (event: WheelEvent) => {
      pointer = { x: event.clientX, y: event.clientY }
    }
    const handleScroll = (event: Event) => {
      if (event.target instanceof Element && event.target.contains(triggerRef.current)) updateAfterScroll()
    }
    window.addEventListener('pointermove', trackPointer, true)
    window.addEventListener('wheel', trackWheel, { capture: true, passive: true })
    window.addEventListener('scroll', handleScroll, true)
    return () => {
      window.removeEventListener('pointermove', trackPointer, true)
      window.removeEventListener('wheel', trackWheel, true)
      window.removeEventListener('scroll', handleScroll, true)
      if (frame !== null) cancelAnimationFrame(frame)
    }
  }, [])

  return (
    <Tooltip open={open} onOpenChange={setOpen} disableHoverableContent>
      <TooltipTrigger
        ref={triggerRef}
        asChild
        onPointerEnter={(event) => {
          if (event.pointerType === 'mouse') setOpen(true)
        }}
        onPointerLeave={() => setOpen(false)}
        onClick={() => setOpen(false)}
        onFocus={(event) => {
          // 点击留下的焦点不弹提示，只有键盘切换过来时才显示
          if (!event.currentTarget.matches(':focus-visible')) event.preventDefault()
        }}
      >
        {children}
      </TooltipTrigger>
      <TooltipContent side={side} className="pointer-events-none">{label}</TooltipContent>
    </Tooltip>
  )
}

interface ObservationSidebarProps {
  collapsed: boolean
  settingsItems: MenuItem[]
  /** 打开全局搜索（页面、配置项与 AI 答疑） */
  onOpenSearch: () => void
  onToggleCollapsed: () => void
}

export function ObservationSidebar({
  collapsed,
  settingsItems,
  onOpenSearch,
  onToggleCollapsed,
}: ObservationSidebarProps) {
  const { t } = useTranslation()
  const botAvailable = useBotAvailable()
  const pathname = useRouterState({ select: (state) => state.location.pathname })
  const { theme, setTheme } = useTheme()
  const [settingsOpen, setSettingsOpen] = useState(loadSettingsOpen)
  const [toolsOpen, setToolsOpen] = useState(false)
  const [settingsLayout, setSettingsLayout] = useState(loadSettingsLayout)
  const reducedMotion = useReducedMotion()
  const [settingsPage, setSettingsPage] = useState(0)
  const [settingsPageDirection, setSettingsPageDirection] = useState(1)
  const settingsSection = useRef<HTMLElement>(null)
  const { data: promptCatalog } = useQuery(
    {
      ...promptCatalogQueryOptions,
      enabled: botAvailable,
      refetchOnWindowFocus: true,
      refetchInterval: 60_000,
    },
    queryClient
  )
  const hasPromptWarnings = Object.values(promptCatalog?.files ?? {}).some((files) =>
    files.some((file) => file.validation?.valid === false)
  )
  const actualTheme =
    theme === 'system'
      ? window.matchMedia('(prefers-color-scheme: dark)').matches
        ? 'dark'
        : 'light'
      : theme
  // 折叠成图标轨时设置键总是显示，否则图标轨里就没有入口了
  const settingsVisible = settingsOpen || collapsed
  const settingsPaginated = !collapsed
  const settingsPageSize = settingsLayout.rows * 4
  const itemByPath = new Map(settingsItems.map((item) => [item.path, item]))
  const savedPaths = new Set(settingsLayout.order)
  const orderedSettingsItems = [
    ...settingsLayout.order.flatMap((path) => {
      const item = itemByPath.get(path)
      return item ? [item] : []
    }),
    ...settingsItems.filter((item) => !savedPaths.has(item.path)),
  ]
  const settingsPageCount = Math.max(1, Math.ceil(orderedSettingsItems.length / settingsPageSize))
  const currentSettingsPage = Math.min(settingsPage, settingsPageCount - 1)
  const visibleSettingsItems = settingsPaginated
    ? orderedSettingsItems.slice(currentSettingsPage * settingsPageSize, (currentSettingsPage + 1) * settingsPageSize)
    : orderedSettingsItems

  useEffect(() => {
    try {
      localStorage.setItem(SETTINGS_LAYOUT_STORAGE_KEY, JSON.stringify(settingsLayout))
    } catch (error) {
      console.warn('无法保存侧边栏布局偏好', error)
    }
  }, [settingsLayout])

  useEffect(() => {
    const section = settingsSection.current
    if (!section || !settingsVisible || !settingsPaginated || settingsPageCount <= 1) return
    let lastWheelTime = -Infinity
    let accumulatedDelta = 0
    const turnPage = (event: WheelEvent) => {
      if (event.target instanceof Node && !section.contains(event.target)) return
      if (event.ctrlKey || event.shiftKey || Math.abs(event.deltaX) >= Math.abs(event.deltaY)) return
      event.preventDefault()
      // 一次滚轮或触控板手势只翻一页，避免惯性连续跳页。
      const now = performance.now()
      const elapsed = now - lastWheelTime
      lastWheelTime = now
      if (elapsed < 180 && accumulatedDelta === 0) return
      if (elapsed >= 180) accumulatedDelta = 0
      const delta = event.deltaY * (event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? section.clientHeight : 1)
      if (Math.sign(delta) !== Math.sign(accumulatedDelta)) accumulatedDelta = 0
      accumulatedDelta += delta
      if (Math.abs(accumulatedDelta) < 24) return
      const direction = Math.sign(accumulatedDelta)
      accumulatedDelta = 0
      setSettingsPageDirection(direction)
      setSettingsPage((page) => Math.max(0, Math.min(settingsPageCount - 1, page + direction)))
    }
    section.addEventListener('wheel', turnPage, { passive: false })
    return () => section.removeEventListener('wheel', turnPage)
  }, [settingsVisible, settingsPaginated, settingsPageCount])

  // 嵌入一键包时把侧栏宽度告诉外壳，顶栏品牌列跟着对齐；
  // 教程或首次试聊进行中时锁住外壳的「回首页」，免得把引导打断
  const sidebarRef = useRef<HTMLElement>(null)
  const tourRunning = useContext(TourContext)?.state.isRunning === true
  const setupTrial = useRouterState({
    select: (state) => new URLSearchParams(state.location.searchStr).get('setupTrial') === 'true',
  })
  const homeLocked = tourRunning || setupTrial
  const layoutRef = useRef({ collapsed, homeLocked })
  const reportLayoutRef = useRef<() => void>(() => {})
  useEffect(() => {
    const sidebar = sidebarRef.current
    if (!sidebar || !isDesktopEmbedded()) return
    const report = () =>
      reportDesktopLayout({ sidebarWidth: Math.round(sidebar.getBoundingClientRect().width), ...layoutRef.current })
    reportLayoutRef.current = report
    report()
    // 折叠动画期间宽度每帧都在变，只在停稳后报一次；外壳那边自己按同一条缓动跟着走
    let timer = 0
    const observer = new ResizeObserver(() => {
      window.clearTimeout(timer)
      timer = window.setTimeout(report, 120)
    })
    observer.observe(sidebar)
    return () => {
      window.clearTimeout(timer)
      observer.disconnect()
      reportLayoutRef.current = () => {}
      // 只有侧栏真正卸载（例如回到登录页）才报宽度 0，外壳退回默认宽度
      reportDesktopLayout({ sidebarWidth: 0, collapsed: layoutRef.current.collapsed, homeLocked: true })
    }
  }, [])
  // 教程开关立即告诉外壳；折叠状态等宽度停稳后由上面的观察器报
  useEffect(() => {
    const lockChanged = layoutRef.current.homeLocked !== homeLocked
    layoutRef.current = { collapsed, homeLocked }
    if (lockChanged) reportLayoutRef.current()
  }, [collapsed, homeLocked])

  const toggleSettings = () => {
    setSettingsOpen((current) => {
      try {
        localStorage.setItem(SETTINGS_OPEN_STORAGE_KEY, String(!current))
      } catch {
        // 存储不可用时只影响展开状态的记忆
      }
      return !current
    })
  }

  const toolClassName = collapsed ? 'flex w-full items-center gap-3 rounded-sm px-3 py-2 text-sm hover:bg-accent focus-visible:outline-2' : undefined
  const toolItem = (element: ReactElement) => collapsed ? <DropdownMenuItem asChild>{element}</DropdownMenuItem> : element
  const tools = (
    <>
      <BotSwitcher submenu={collapsed}>
        <button type="button" data-observation-icon-key={!collapsed || undefined} className={toolClassName}>
          <ChevronsUpDown aria-hidden="true" />
          {collapsed && <span>{t('hostManager.multiBotManagement')}</span>}
        </button>
      </BotSwitcher>
      {toolItem(<Link
        to="/settings"
        data-observation-icon-key={!collapsed || undefined}
        data-active={pathname === '/settings' ? 'true' : 'false'}
        className={toolClassName}
        title={t('sidebar.menu.settings')}
        aria-label={t('sidebar.menu.settings')}
        onClick={() => setToolsOpen(false)}
      >
        <Settings aria-hidden="true" />
        {collapsed && <span>{t('sidebar.menu.settings')}</span>}
      </Link>)}
      {toolItem(<button
        type="button"
        data-observation-icon-key={!collapsed || undefined}
        className={toolClassName}
        title={t('header.viewDocs')}
        aria-label={t('header.viewDocs')}
        onClick={() => { setToolsOpen(false); window.open('https://docs.maibot.chat/', '_blank') }}
      >
        <BookOpen aria-hidden="true" />
        {collapsed && <span>{t('header.viewDocs')}</span>}
      </button>)}
      {toolItem(<button
        type="button"
        data-observation-icon-key={!collapsed || undefined}
        className={toolClassName}
        aria-label={actualTheme === 'dark' ? t('header.switchToLight') : t('header.switchToDark')}
        title={actualTheme === 'dark' ? t('header.switchToLight') : t('header.switchToDark')}
        onClick={(event) => toggleThemeWithTransition(actualTheme === 'dark' ? 'light' : 'dark', setTheme, event)}
      >
        {actualTheme === 'dark' ? <Sun aria-hidden="true" /> : <Moon aria-hidden="true" />}
        {collapsed && <span>{actualTheme === 'dark' ? t('header.switchToLight') : t('header.switchToDark')}</span>}
      </button>)}
      <KeyTooltip label={collapsed ? t('common.search') : `${t('common.search')} · Ctrl K`} side={collapsed ? 'right' : 'bottom'}>
        {toolItem(<button
          type="button"
          data-observation-icon-key={!collapsed || undefined}
          className={collapsed ? toolClassName : 'ml-auto'}
          aria-label={t('common.search')}
          aria-keyshortcuts="Control+K Meta+K"
          onClick={() => { setToolsOpen(false); onOpenSearch() }}
        >
          <Search aria-hidden="true" />
          {collapsed && <span>{t('common.search')}</span>}
        </button>)}
      </KeyTooltip>
    </>
  )

  return (
    <aside
      ref={sidebarRef}
      data-observation-sidebar="true"
      data-collapsed={collapsed ? 'true' : 'false'}
      aria-label={t('a11y.sidebarNav')}
    >
      <div data-observation-brand="true">
        <Link
          to="/"
          activeOptions={{ exact: true }}
          data-observation-logo="true"
          aria-label={t('sidebar.menu.home')}
          title={t('sidebar.menu.home')}
        >
          <span data-observation-logo-mark="true" aria-hidden="true">
            <span />
            <span />
            <span />
          </span>
          {!collapsed && <span data-observation-logo-plate="true">MAIBOT</span>}
        </Link>
        {!collapsed && <span data-observation-brand-vent="true" aria-hidden="true" />}
        <button
          type="button"
          data-observation-icon-key="true"
          aria-expanded={!collapsed}
          aria-label={collapsed ? t('observation.expandSidebar') : t('observation.collapseSidebar')}
          title={collapsed ? t('observation.expandSidebar') : t('observation.collapseSidebar')}
          onClick={onToggleCollapsed}
        >
          {collapsed ? <PanelLeftOpen aria-hidden="true" /> : <PanelLeftClose aria-hidden="true" />}
        </button>
      </div>

      <section ref={settingsSection} data-observation-settings="true">
        {!collapsed && (
          <div className="relative flex items-center gap-1">
            <button
              type="button"
              data-observation-section-title="true"
              className="flex-1"
              aria-expanded={settingsVisible}
              onClick={toggleSettings}
            >
              <span>{t('observation.settingsBlock')}</span>
              <ChevronDown aria-hidden="true" data-open={settingsVisible ? 'true' : 'false'} />
            </button>
            {settingsVisible && settingsPaginated && settingsPageCount > 1 && (
              <div className="absolute left-1/2 flex -translate-x-1/2 items-center gap-1" role="group" aria-label="设置分页">
                <button
                  type="button"
                  data-observation-section-title="true"
                  aria-label="上一页设置"
                  disabled={currentSettingsPage === 0}
                  onClick={() => {
                    setSettingsPageDirection(-1)
                    setSettingsPage(currentSettingsPage - 1)
                  }}
                >
                  <ChevronLeft aria-hidden="true" />
                </button>
                <span className="text-xs tabular-nums" aria-live="polite">
                  {currentSettingsPage + 1}/{settingsPageCount}
                </span>
                <button
                  type="button"
                  data-observation-section-title="true"
                  aria-label="下一页设置"
                  disabled={currentSettingsPage === settingsPageCount - 1}
                  onClick={() => {
                    setSettingsPageDirection(1)
                    setSettingsPage(currentSettingsPage + 1)
                  }}
                >
                  <ChevronRight aria-hidden="true" />
                </button>
              </div>
            )}
            <SidebarSettingsMenu
              rows={settingsLayout.rows}
              items={orderedSettingsItems}
              onRowsChange={(rows) => {
                setSettingsLayout((layout) => ({ ...layout, rows }))
                setSettingsPage(0)
              }}
              onReorder={(paths) => {
                setSettingsLayout((layout) => {
                  // 暂时不可见的插件入口保留排序位置，重新启用时仍按原偏好排列。
                  const visiblePaths = new Set(paths)
                  let index = 0
                  const order = layout.order.map((path) => visiblePaths.has(path) ? paths[index++] : path)
                  return { ...layout, order: [...order, ...paths.slice(index)] }
                })
                setSettingsPage(0)
              }}
              onResetOrder={() => {
                setSettingsLayout((layout) => ({ ...layout, order: [] }))
                setSettingsPage(0)
              }}
            />
          </div>
        )}
        {settingsVisible && (
          <ScrollArea
            scrollbars="vertical"
            className={collapsed ? 'min-h-0 flex-1' : 'min-h-0'}
          >
            <nav aria-label={t('observation.settingsBlock')}>
              <motion.ul
                key={settingsPaginated ? currentSettingsPage : 'unpaged'}
                data-observation-keys="true"
                data-paginated={settingsPaginated || undefined}
                style={settingsPaginated ? { gridTemplateRows: `repeat(${settingsLayout.rows}, 3.125rem)` } : undefined}
                initial={reducedMotion || !settingsPaginated ? false : { opacity: 0, x: settingsPageDirection * 12 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ duration: reducedMotion ? 0 : 0.18, ease: 'easeOut' }}
              >
                {visibleSettingsItems.map((item) => {
                  const Icon = item.icon
                  const label = item.literalLabel ? item.label : t(item.label)
                  const shortLabel = SHORT_LABELS[item.path] ? t(SHORT_LABELS[item.path]) : label
                  const active = isItemActive(item, pathname)
                  // Bot 还在加载或没有运行时，依赖 Bot 的键不可点；管理与 WebUI 设置照常可用
                  const disabled = !botAvailable && !isBotIndependentPath(item.path)
                  const keyContent = (
                    <>
                      <Icon className="h-5 w-5" />
                      {!collapsed && <span data-observation-key-label="true">{shortLabel}</span>}
                      {!disabled && item.path === '/config/prompts' && hasPromptWarnings && (
                        <PromptWarningDot />
                      )}
                    </>
                  )
                  return (
                    <li key={item.path}>
                      {/* 键帽里的名字放不下时截断，悬停显示完整名称 */}
                      <KeyTooltip
                        label={disabled ? t('observation.keyNeedsBot', { label }) : label}
                        side={collapsed ? 'right' : 'bottom'}
                      >
                        {disabled ? (
                          <span
                            role="link"
                            aria-disabled="true"
                            aria-label={label}
                            tabIndex={0}
                            data-observation-key="true"
                            data-disabled="true"
                            data-active="false"
                            data-tour={item.tourId}
                          >
                            {keyContent}
                          </span>
                        ) : (
                          <Link
                            to={item.path}
                            aria-label={label}
                            data-observation-key="true"
                            data-active={active ? 'true' : 'false'}
                            data-tour={item.tourId}
                            aria-current={active ? 'page' : undefined}
                          >
                            {keyContent}
                          </Link>
                        )}
                      </KeyTooltip>
                    </li>
                  )
                })}
              </motion.ul>
            </nav>
          </ScrollArea>
        )}
      </section>

      <div data-observation-rule="true" aria-hidden="true" />

      {botAvailable && <ObservationConversations collapsed={collapsed} />}

      <div data-observation-update="true" className={collapsed ? 'flex shrink-0 justify-center' : 'shrink-0'}>
        <SidebarUpdateButton collapsed={collapsed} />
      </div>
      <footer data-observation-tools="true">
        {collapsed ? (
          <div>
            <DropdownMenu modal={false} open={toolsOpen} onOpenChange={setToolsOpen}>
              <DropdownMenuTrigger asChild>
                <button type="button" data-observation-icon-key="true"
                  aria-label={t('header.moreActions')}>
                  <MoreHorizontal aria-hidden="true" />
                </button>
              </DropdownMenuTrigger>
              <DropdownMenuContent side="right" align="end" sideOffset={12}
                aria-label={t('header.moreActions')} className="w-52 max-h-[calc(100dvh-2rem)] overflow-y-auto p-2 [&_svg]:size-4 [&_svg]:shrink-0">
                {tools}
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        ) : tools}
      </footer>
    </aside>
  )
}
