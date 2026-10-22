import { Link, useNavigate, useRouterState } from '@tanstack/react-router'
import { ChevronRight, House, LayoutGrid, MessageCircle, Search } from 'lucide-react'
import { AnimatePresence, LayoutGroup, MotionConfig, motion, useReducedMotion } from 'motion/react'
import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { LucideIcon } from 'lucide-react'

import { useAnimation } from '@/hooks/use-animation'
import { useMediaQuery } from '@/hooks/use-media-query'
import { cn } from '@/lib/utils'
import { StageStatusLabel } from '@/routes/monitor/StageStatusLabel'
import { useMaisakaMonitorOverview } from '@/routes/monitor/use-maisaka-monitor'
import type { StageStatusInfo } from '@/routes/monitor/use-maisaka-monitor'
import { MOBILE_GLASS_CLASS } from './mobile-glass'
import { resolveMobileTab } from './mobile-nav-context'
import type { MobileTab } from './mobile-nav-context'

interface MobileTabDef {
  tab: MobileTab
  to: string
  labelKey: string
  icon: LucideIcon
}

// 三个标签 + 独立的搜索键；「我的」从首页头像进入。两种风格结构相同，千禧风格只换外观（键帽）。
// 观察麦麦的聊天流就在「聊天」里，不再单独设标签。
const TABS: MobileTabDef[] = [
  { tab: 'home', to: '/', labelKey: 'mobileNav.home', icon: House },
  { tab: 'chat', to: '/chat', labelKey: 'mobileNav.chat', icon: MessageCircle },
  { tab: 'manage', to: '/manage', labelKey: 'mobileNav.manage', icon: LayoutGrid },
]

// 与 Tailwind 的 lg 断点对齐：桌面端完全不挂载标签栏，也就不会订阅麦麦实时状态。
const MOBILE_MEDIA_QUERY = '(max-width: 1023.98px)'

// 页面向下滚动超过该距离后标签栏收成胶囊
const COLLAPSE_SCROLL_TOP = 48
const SCROLL_DELTA_THRESHOLD = 6
const LOG_VIEWER_PATHS = new Set(['/logs', '/reasoning-process', '/statistics'])
// 收起 / 展开用同一条弹簧：尺寸、位置连续过渡，不跳变
const DOCK_SPRING = { type: 'spring', stiffness: 420, damping: 36, mass: 0.8 } as const

/** 底部浮层占用的高度写到根元素上，页面据此给内容留出底部空间 */
const DOCK_HEIGHT_CSS_VAR = '--mobile-dock-height'

interface MobileTabBarProps {
  hidden?: boolean
  onSearch: () => void
}

/** 移动端底部标签栏，取代侧栏与汉堡菜单；桌面端（lg 及以上）不渲染。 */
export function MobileTabBar(props: MobileTabBarProps) {
  const isMobile = useMediaQuery(MOBILE_MEDIA_QUERY)

  useEffect(() => {
    if (isMobile && !props.hidden) return
    document.documentElement.style.setProperty(DOCK_HEIGHT_CSS_VAR, '0px')
  }, [isMobile, props.hidden])

  if (!isMobile || props.hidden) return null
  return <TabBarDock onSearch={props.onSearch} />
}

/** 记录底部浮层的实际高度，供页面留白使用 */
function useDockHeight(ref: React.RefObject<HTMLElement | null>) {
  useLayoutEffect(() => {
    const element = ref.current
    if (!element) return
    let previousHeight: string | undefined
    const update = () => {
      const bottomGap = window.innerHeight - element.getBoundingClientRect().bottom
      const height = `${Math.ceil(element.offsetHeight + Math.max(0, bottomGap) + 16)}px`
      if (height === previousHeight) return
      previousHeight = height
      document.documentElement.style.setProperty(DOCK_HEIGHT_CSS_VAR, height)
    }
    update()
    const observer = new ResizeObserver(update)
    observer.observe(element)
    return () => observer.disconnect()
  }, [ref])
}

/** 向下滚动时收起、向上滚动时展开；监听所有滚动容器，兼容页面内部自带滚动区域的情况 */
function useCollapseOnScroll(pathname: string) {
  // 日志类页面默认收起，仅由用户点击展开，避免滚动时反复改变内容区高度。
  const [dockState, setDockState] = useState(() => ({
    pathname,
    collapsed: LOG_VIEWER_PATHS.has(pathname),
  }))
  if (dockState.pathname !== pathname) {
    setDockState({ pathname, collapsed: LOG_VIEWER_PATHS.has(pathname) })
  }
  const pathnameRef = useRef(pathname)
  const lastScrollTops = useRef(new WeakMap<EventTarget, number>())

  useEffect(() => {
    pathnameRef.current = pathname
  }, [pathname])

  useEffect(() => {
    const handleScroll = (event: Event) => {
      if (LOG_VIEWER_PATHS.has(pathnameRef.current)) return
      const target = event.target
      if (!(target instanceof HTMLElement)) return
      // 横向滚动条、输入框等不参与判断
      if (target.scrollHeight - target.clientHeight < COLLAPSE_SCROLL_TOP * 2) return
      const scrollTop = target.scrollTop
      const previous = lastScrollTops.current.get(target) ?? scrollTop
      lastScrollTops.current.set(target, scrollTop)
      const delta = scrollTop - previous
      if (scrollTop <= COLLAPSE_SCROLL_TOP) {
        setDockState((current) => current.pathname === pathnameRef.current && current.collapsed === false
          ? current
          : { pathname: pathnameRef.current, collapsed: false })
      } else if (delta > SCROLL_DELTA_THRESHOLD) {
        setDockState((current) => current.pathname === pathnameRef.current && current.collapsed === true
          ? current
          : { pathname: pathnameRef.current, collapsed: true })
      } else if (delta < -SCROLL_DELTA_THRESHOLD) {
        setDockState((current) => current.pathname === pathnameRef.current && current.collapsed === false
          ? current
          : { pathname: pathnameRef.current, collapsed: false })
      }
    }
    document.addEventListener('scroll', handleScroll, { capture: true, passive: true })
    return () => document.removeEventListener('scroll', handleScroll, { capture: true })
  }, [])

  const collapsed = dockState.collapsed
  const setCollapsed = (value: boolean) => setDockState({ pathname, collapsed: value })
  return [collapsed, setCollapsed] as const
}

/** 找出最近一次状态更新的聊天流，作为「麦麦实时」附件条的内容 */
function pickLatestStageStatus(stageStatuses: Map<string, StageStatusInfo>) {
  let latest: StageStatusInfo | undefined
  for (const status of stageStatuses.values()) {
    if (!latest || status.updatedAt > latest.updatedAt) latest = status
  }
  return latest
}

function TabBarDock({ onSearch }: Pick<MobileTabBarProps, 'onSearch'>) {
  const { t } = useTranslation()
  const { enableAnimations } = useAnimation()
  const prefersReducedMotion = useReducedMotion()
  const animate = enableAnimations && !prefersReducedMotion
  const dockTransition = animate ? DOCK_SPRING : { duration: 0 }
  const navigate = useNavigate()
  const pathname = useRouterState({ select: (state) => state.location.pathname })
  const activeTab = resolveMobileTab(pathname)
  const activeTabIndex = TABS.findIndex(({ tab }) => tab === activeTab)
  const dockRef = useRef<HTMLDivElement>(null)
  const [collapsed, setCollapsed] = useCollapseOnScroll(pathname)
  const { sessions, stageStatuses, setSelectedSession } = useMaisakaMonitorOverview()
  const latestStatus = pickLatestStageStatus(stageStatuses)
  const showLive = latestStatus !== undefined
  const liveSessionName = latestStatus
    ? (sessions.get(latestStatus.sessionId)?.sessionName ??
      latestStatus.sessionName ??
      latestStatus.sessionId.slice(0, 8))
    : ''

  useDockHeight(dockRef)

  const openLive = () => {
    if (!latestStatus) return
    // 直接进入聊天里这个聊天流的观察对话页
    setSelectedSession(latestStatus.sessionId)
    void navigate({ to: '/chat', search: { observe: latestStatus.sessionId } })
  }

  // 麦麦实时条：展开时在标签栏上方，收起时移进同一行；只在收起状态变化时测量布局。
  const liveStrip = latestStatus && (
    <motion.button
      layoutId="mobile-live-strip"
      layoutDependency={collapsed}
      transition={dockTransition}
      type="button"
      data-mobile-live-strip="true"
      onClick={openLive}
      aria-label={t('mobileNav.live')}
      className={cn(
        'text-foreground flex h-[52px] min-w-0 items-center gap-3 rounded-full pr-1.5 pl-2 text-left',
        collapsed ? 'flex-1' : 'w-full',
        MOBILE_GLASS_CLASS
      )}
    >
      <motion.span
        layout="position"
        layoutDependency={collapsed}
        className="bg-foreground text-background flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-sm font-semibold"
      >
        {t('mobileNav.botInitial')}
      </motion.span>
      <motion.span
        layout="position"
        layoutDependency={collapsed}
        className="flex min-w-0 flex-1 flex-col"
      >
        <span className="truncate text-sm font-semibold">
          {liveSessionName} · <StageStatusLabel status={latestStatus} />
        </span>
        {!collapsed && latestStatus.detail && (
          <span className="text-muted-foreground truncate text-xs">{latestStatus.detail}</span>
        )}
      </motion.span>
      {!collapsed && (
        <span className="flex h-10 w-10 shrink-0 items-center justify-center">
          <ChevronRight className="h-[18px] w-[18px]" strokeWidth={1.7} aria-hidden="true" />
        </span>
      )}
    </motion.button>
  )

  return (
    <MotionConfig reducedMotion={animate ? 'user' : 'always'}>
      <LayoutGroup>
        <div
          ref={dockRef}
          data-mobile-tabbar="true"
          data-collapsed={collapsed ? 'true' : 'false'}
          className="fixed inset-x-4 bottom-[calc(env(safe-area-inset-bottom)+14px)] z-40 flex flex-col gap-2.5"
        >
          {showLive && !collapsed && liveStrip}
          <nav aria-label={t('mobileNav.label')} className="flex items-center gap-2.5">
            {/* 收起时只留下当前标签，其余标签淡出，容器宽度随之收缩成圆键 */}
            <motion.div
              layout
              layoutDependency={collapsed}
              transition={dockTransition}
              data-mobile-tabbar-tabs="true"
              onClick={collapsed ? () => setCollapsed(false) : undefined}
              className={cn(
                'relative flex rounded-full p-1',
                collapsed ? 'h-[52px] w-[52px] flex-none' : 'h-[62px] flex-1',
                MOBILE_GLASS_CLASS
              )}
            >
              {/* 底板常驻，用 transform 切换标签，避免导航时跨节点测量共享布局。 */}
              {activeTabIndex >= 0 && (
                <span className="pointer-events-none absolute inset-1" aria-hidden="true">
                  <span
                    data-mobile-tab-indicator="true"
                    className="bg-foreground/[0.07] block h-full rounded-full transition-transform duration-200 ease-out motion-reduce:transition-none"
                    style={{
                      width: collapsed ? '100%' : `${100 / TABS.length}%`,
                      transform: `translateX(${collapsed ? 0 : activeTabIndex * 100}%)`,
                      transitionDuration: animate ? undefined : '0s',
                    }}
                  />
                </span>
              )}
              <AnimatePresence initial={false} mode="popLayout">
                {TABS.filter(({ tab }) => !collapsed || tab === activeTab).map(
                  ({ tab, to, labelKey, icon: Icon }) => {
                    const active = activeTab === tab
                    return (
                      <motion.div
                        key={tab}
                        layout
                        layoutDependency={collapsed}
                        transition={dockTransition}
                        initial={{ opacity: 0, scale: 0.8 }}
                        animate={{ opacity: 1, scale: 1 }}
                        exit={{ opacity: 0, scale: 0.8 }}
                        className="flex min-w-0 flex-1"
                      >
                        <Link
                          to={to}
                          data-mobile-tab="true"
                          data-active={active ? 'true' : 'false'}
                          aria-current={active ? 'page' : undefined}
                          aria-label={collapsed ? t(labelKey) : undefined}
                          onClick={collapsed ? (event) => event.preventDefault() : undefined}
                          className={cn(
                            'relative flex min-w-0 flex-1 flex-col items-center justify-center gap-0.5 rounded-full text-[10px] font-semibold transition-colors duration-300',
                            active ? 'text-primary' : 'text-foreground'
                          )}
                        >
                          <span className="relative flex">
                            <Icon
                              className="h-[22px] w-[22px]"
                              strokeWidth={active ? 2 : 1.7}
                              aria-hidden="true"
                            />
                          </span>
                          {!collapsed && (
                            <span className="relative truncate">{t(labelKey)}</span>
                          )}
                        </Link>
                      </motion.div>
                    )
                  }
                )}
              </AnimatePresence>
            </motion.div>
            {collapsed && (showLive ? liveStrip : <span className="flex-1" />)}
            <button
              type="button"
              data-mobile-tabbar-search="true"
              onClick={onSearch}
              aria-label={t('mobileNav.search')}
              className={cn(
                'text-foreground flex shrink-0 items-center justify-center rounded-full',
                collapsed ? 'h-[52px] w-[52px]' : 'h-[62px] w-[62px]',
                MOBILE_GLASS_CLASS
              )}
            >
              <Search className="h-[22px] w-[22px]" strokeWidth={1.7} aria-hidden="true" />
            </button>
          </nav>
        </div>
      </LayoutGroup>
    </MotionConfig>
  )
}
