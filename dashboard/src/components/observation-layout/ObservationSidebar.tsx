import { Link, useRouterState } from '@tanstack/react-router'
import { useQuery } from '@tanstack/react-query'
import {
  BookOpen,
  ChevronDown,
  ChevronsUpDown,
  Minus,
  Moon,
  PanelLeftClose,
  PanelLeftOpen,
  Plus,
  Search,
  Settings,
  Sun,
} from 'lucide-react'
import { useEffect, useRef, useState, type ReactElement } from 'react'
import { useTranslation } from 'react-i18next'

import { SidebarUpdateButton } from '@/components/layout/SidebarUpdateButton'
import type { MenuItem } from '@/components/layout/types'
import { PromptWarningDot } from '@/components/ui/prompt-warning-dot'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { toggleThemeWithTransition, useTheme } from '@/components/use-theme'
import { promptCatalogQueryOptions } from '@/lib/prompt-query'
import { queryClient } from '@/lib/query'
import { isBotIndependentPath, useBotAvailable } from '@/host/state'
import '@/styles/observation-key-state.css'
import { BotSwitcher } from '@/host/BotSwitcher'

import { ObservationConversations } from './ObservationConversations'

const SETTINGS_OPEN_STORAGE_KEY = 'maibot-observation-settings-open'
const SETTINGS_COMPACT_STORAGE_KEY = 'maibot-observation-settings-compact'
const LOG_PATHS = ['/logs', '/statistics', '/reasoning-process']

/** 侧栏键位空间有限，常用入口用短名；完整名称仍在悬停提示和读屏里。 */
const SHORT_LABELS: Record<string, string> = {
  '/config/bot': 'observation.keys.bot',
  '/config/model': 'observation.keys.model',
  '/adapter-management': 'observation.keys.adapter',
  '/resource/learning': 'observation.keys.learning',
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
  return (
    <Tooltip open={open} onOpenChange={setOpen} disableHoverableContent>
      <TooltipTrigger
        asChild
        onPointerLeave={() => setOpen(false)}
        onClick={() => setOpen(false)}
        onFocus={(event) => {
          // 点击留下的焦点不弹提示，只有键盘切换过来时才显示
          if (!event.currentTarget.matches(':focus-visible')) event.preventDefault()
        }}
      >
        {children}
      </TooltipTrigger>
      <TooltipContent side={side}>{label}</TooltipContent>
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
  const [settingsCompact, setSettingsCompact] = useState(() => {
    try {
      return localStorage.getItem(SETTINGS_COMPACT_STORAGE_KEY) === 'true'
    } catch {
      return false
    }
  })
  const settingsViewport = useRef<HTMLDivElement>(null)
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
  const settingsSingleRow = settingsCompact && !collapsed

  useEffect(() => {
    try {
      localStorage.setItem(SETTINGS_COMPACT_STORAGE_KEY, String(settingsCompact))
    } catch {
      // 存储不可用时，布局选择仅在当前页面保留。
    }
  }, [settingsCompact])

  useEffect(() => {
    const viewport = settingsViewport.current
    if (!settingsVisible || !settingsSingleRow || !viewport) return
    const row = viewport.parentElement
    if (!row) return

    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)')
    let maxScroll = viewport.scrollWidth - viewport.clientWidth
    let wheelTarget = viewport.scrollLeft
    let wheelPosition = wheelTarget
    let lastFrameTime = 0
    let wheelFrame = 0

    const animateWheel = (frameTime: number) => {
      const remaining = wheelTarget - wheelPosition
      if (Math.abs(remaining) <= 1) {
        viewport.scrollLeft = wheelTarget
        wheelFrame = 0
        return
      }
      const elapsed = frameTime - lastFrameTime
      lastFrameTime = frameTime
      wheelPosition += remaining * (1 - Math.exp(-elapsed / 30))
      viewport.scrollLeft = wheelPosition
      wheelFrame = requestAnimationFrame(animateWheel)
    }

    const updateScrollEdges = () => {
      const position = viewport.scrollLeft
      row.toggleAttribute('data-clipped-left', position > 1)
      row.toggleAttribute('data-clipped-right', position < maxScroll - 1)
    }
    const scrollSettings = (event: WheelEvent) => {
      if (
        event.ctrlKey || event.shiftKey || event.deltaX || !event.deltaY ||
        maxScroll <= 0
      ) {
        cancelAnimationFrame(wheelFrame)
        wheelFrame = 0
        return
      }

      const unit = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? viewport.clientWidth : 1
      event.preventDefault()
      const from = wheelFrame ? wheelTarget : viewport.scrollLeft
      wheelTarget = Math.min(maxScroll, Math.max(0, from + event.deltaY * unit))
      if (reducedMotion.matches) {
        viewport.scrollLeft = wheelTarget
      } else if (!wheelFrame) {
        wheelPosition = viewport.scrollLeft
        lastFrameTime = performance.now()
        wheelFrame = requestAnimationFrame(animateWheel)
      }
    }
    const observer = new ResizeObserver(() => {
      maxScroll = viewport.scrollWidth - viewport.clientWidth
      wheelTarget = Math.min(wheelTarget, maxScroll)
      updateScrollEdges()
    })
    observer.observe(viewport)
    updateScrollEdges()
    viewport.addEventListener('scroll', updateScrollEdges, { passive: true })
    viewport.addEventListener('wheel', scrollSettings, { passive: false })
    return () => {
      cancelAnimationFrame(wheelFrame)
      observer.disconnect()
      viewport.removeEventListener('scroll', updateScrollEdges)
      viewport.removeEventListener('wheel', scrollSettings)
      row.removeAttribute('data-clipped-left')
      row.removeAttribute('data-clipped-right')
    }
  }, [settingsSingleRow, settingsVisible, settingsItems.length])

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

  return (
    <aside
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

      <section data-observation-settings="true">
        {!collapsed && (
          <div className="flex items-center gap-1">
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
            {settingsVisible && (
              <button
                type="button"
                data-observation-section-title="true"
                aria-label={t('observation.compactSettings')}
                aria-pressed={settingsSingleRow}
                title={t('observation.compactSettings')}
                onClick={() => setSettingsCompact((current) => !current)}
              >
                {settingsSingleRow ? <Plus aria-hidden="true" /> : <Minus aria-hidden="true" />}
              </button>
            )}
          </div>
        )}
        {settingsVisible && (
          <ScrollArea
            scrollbars={settingsSingleRow ? 'horizontal' : 'vertical'}
            className="min-h-0"
            data-observation-settings-row={settingsSingleRow || undefined}
            viewportRef={settingsViewport}
          >
            <nav aria-label={t('observation.settingsBlock')}>
              <ul data-observation-keys="true" data-single-row={settingsSingleRow}>
                {settingsItems.map((item) => {
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
              </ul>
            </nav>
          </ScrollArea>
        )}
      </section>

      <div data-observation-rule="true" aria-hidden="true" />

      {botAvailable && <ObservationConversations collapsed={collapsed} />}

      <div className={collapsed ? 'flex shrink-0 justify-center' : 'shrink-0'}>
        <SidebarUpdateButton collapsed={collapsed} />
      </div>
      <footer data-observation-tools="true">
        <BotSwitcher>
          <button type="button" data-observation-icon-key="true">
            <ChevronsUpDown aria-hidden="true" />
          </button>
        </BotSwitcher>
        <Link
          to="/settings"
          data-observation-icon-key="true"
          data-active={pathname === '/settings' ? 'true' : 'false'}
          title={t('sidebar.menu.settings')}
          aria-label={t('sidebar.menu.settings')}
        >
          <Settings aria-hidden="true" />
        </Link>
        <button
          type="button"
          data-observation-icon-key="true"
          title={t('header.viewDocs')}
          aria-label={t('header.viewDocs')}
          onClick={() => window.open('https://docs.mai-mai.org', '_blank')}
        >
          <BookOpen aria-hidden="true" />
        </button>
        <button
          type="button"
          data-observation-icon-key="true"
          aria-label={actualTheme === 'dark' ? t('header.switchToLight') : t('header.switchToDark')}
          title={actualTheme === 'dark' ? t('header.switchToLight') : t('header.switchToDark')}
          onClick={(event) =>
            toggleThemeWithTransition(actualTheme === 'dark' ? 'light' : 'dark', setTheme, event)
          }
        >
          {actualTheme === 'dark' ? <Sun aria-hidden="true" /> : <Moon aria-hidden="true" />}
        </button>

        <KeyTooltip label={`${t('common.search')} · Ctrl K`} side={collapsed ? 'right' : 'bottom'}>
          <button
            type="button"
            data-observation-icon-key="true"
            className={collapsed ? undefined : 'ml-auto'}
            aria-label={t('common.search')}
            aria-keyshortcuts="Control+K Meta+K"
            onClick={onOpenSearch}
          >
            <Search aria-hidden="true" />
          </button>
        </KeyTooltip>
      </footer>
    </aside>
  )
}
