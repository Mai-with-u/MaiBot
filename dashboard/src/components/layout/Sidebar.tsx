import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { ScrollArea } from '@/components/ui/scroll-area'
import { cn } from '@/lib/utils'
import { useBackground } from '@/hooks/use-background'
import { BackgroundLayer } from '@/components/background-layer'

import { LogoArea } from './LogoArea'
import { NavItem } from './NavItem'
import { useMenuSections } from './use-menu-sections'

interface SidebarProps {
  sidebarOpen: boolean
  mobileMenuOpen: boolean
  onMobileMenuClose: () => void
}

const SIDEBAR_HOVER_EXPAND_DELAY_MS = 180

export function Sidebar({ sidebarOpen, mobileMenuOpen, onMobileMenuClose }: SidebarProps) {
  const { t } = useTranslation()
  const { config: sidebarBg, inheritedFrom } = useBackground('sidebar')
  const inheritsPageBackground = inheritedFrom === 'page'
  const menuSections = useMenuSections()
  const [hoverExpanded, setHoverExpanded] = useState(false)
  const hoverExpandTimerRef = useRef<number | null>(null)
  const visuallyOpen = sidebarOpen || hoverExpanded

  const cancelHoverExpand = useCallback(() => {
    if (hoverExpandTimerRef.current !== null) {
      window.clearTimeout(hoverExpandTimerRef.current)
      hoverExpandTimerRef.current = null
    }
  }, [])

  useEffect(() => {
    if (sidebarOpen) {
      cancelHoverExpand()
      setHoverExpanded(false)
    }
    return cancelHoverExpand
  }, [cancelHoverExpand, sidebarOpen])

  return (
    <aside
      data-dashboard-sidebar="true"
      data-dashboard-sidebar-hover-expanded={hoverExpanded ? 'true' : undefined}
      onPointerEnter={(event) => {
        if (!sidebarOpen && event.pointerType === 'mouse') {
          cancelHoverExpand()
          hoverExpandTimerRef.current = window.setTimeout(() => {
            hoverExpandTimerRef.current = null
            setHoverExpanded(true)
          }, SIDEBAR_HOVER_EXPAND_DELAY_MS)
        }
      }}
      onPointerLeave={() => {
        cancelHoverExpand()
        setHoverExpanded(false)
      }}
      className={cn(
        'fixed inset-y-0 left-0 isolate z-50 flex flex-col border-r transition-transform duration-300 lg:relative lg:z-0 lg:h-full lg:transition-[width] lg:duration-[220ms] lg:ease-[cubic-bezier(0.22,1,0.36,1)]',
        inheritsPageBackground ? 'bg-transparent' : 'bg-card',
        // 移动端始终显示完整宽度；桌面端折叠后可通过悬停临时覆盖展开。
        'w-[var(--layout-sidebar-width)]',
        visuallyOpen
          ? 'lg:w-[var(--layout-sidebar-width)]'
          : 'lg:w-[var(--layout-sidebar-collapsed-width)]',
        mobileMenuOpen ? 'translate-x-0' : '-translate-x-full lg:translate-x-0'
      )}
    >
      {!inheritsPageBackground && <BackgroundLayer config={sidebarBg} layerId="sidebar" />}

      {/* Logo 区域 */}
      <div className="relative z-10">
        <LogoArea sidebarOpen={visuallyOpen} />
      </div>

      <ScrollArea
        className={cn(
          'relative z-10',
          'min-h-0 flex-1 overflow-x-hidden',
          !visuallyOpen && 'lg:w-[var(--layout-sidebar-collapsed-width)]'
        )}
        viewportClassName="[&>div]:!block"
      >
        <nav
          aria-label={t('a11y.sidebarNav')}
          className={cn(
            'p-[var(--layout-sidebar-nav-padding)]',
            !visuallyOpen && 'lg:w-[var(--layout-sidebar-collapsed-width)]',
            !sidebarOpen && 'lg:p-[var(--layout-sidebar-nav-padding-collapsed)]'
          )}
        >
          <ul
            className={cn(
              // 移动端始终使用正常间距,桌面端根据 sidebarOpen 切换
              'flex flex-col gap-[var(--layout-sidebar-section-gap)]',
              !visuallyOpen && 'lg:w-full'
            )}
          >
            {menuSections.map((section, sectionIndex) => (
              <li key={section.title}>
                {/* 块标题 - 移动端始终可见，桌面端根据 sidebarOpen 切换 */}
                <div
                  className={cn(
                    'h-[var(--layout-sidebar-section-title-height)] px-[var(--layout-sidebar-nav-item-padding-x)]',
                    section.title === 'sidebar.groups.overview' && 'hidden',
                    // 移动端始终显示，桌面端根据状态切换
                    'mb-[var(--layout-sidebar-section-title-margin-bottom)]',
                    !visuallyOpen && 'lg:invisible',
                    !sidebarOpen &&
                      'lg:mb-[var(--layout-sidebar-section-title-margin-bottom-collapsed)]'
                  )}
                >
                  <h3
                    data-dashboard-sidebar-section-title="true"
                    className="text-muted-foreground/60 text-sm font-semibold tracking-wider whitespace-nowrap uppercase"
                  >
                    {t(section.title)}
                  </h3>
                </div>

                {/* 分割线 - 仅在桌面端折叠时显示 */}
                {!sidebarOpen && sectionIndex > 0 && (
                  <div className="border-border mb-2 hidden border-t lg:block" />
                )}

                {/* 菜单项列表 */}
                <ul className="flex flex-col gap-[var(--layout-sidebar-nav-item-gap)]">
                  {section.items.map((item) => (
                    <NavItem
                      key={item.path}
                      item={item}
                      sidebarOpen={visuallyOpen}
                      temporarilyExpanded={!sidebarOpen && hoverExpanded}
                      onMobileMenuClose={onMobileMenuClose}
                    />
                  ))}
                </ul>
              </li>
            ))}
          </ul>
        </nav>
      </ScrollArea>
    </aside>
  )
}
