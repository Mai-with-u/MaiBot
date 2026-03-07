import { useTranslation } from 'react-i18next'

import { ScrollArea } from '@/components/ui/scroll-area'
import { cn } from '@/lib/utils'
import { useBackground } from '@/hooks/use-background'
import { BackgroundLayer } from '@/components/background-layer'

import { LogoArea } from './LogoArea'
import { NavItem } from './NavItem'
import { menuSections } from './constants'

interface SidebarProps {
  sidebarOpen: boolean
  mobileMenuOpen: boolean
  tooltipsEnabled: boolean
  onMobileMenuClose: () => void
}

export function Sidebar({ 
  sidebarOpen, 
  mobileMenuOpen, 
  tooltipsEnabled, 
  onMobileMenuClose 
}: SidebarProps) {
  const { t } = useTranslation()
  const sidebarBg = useBackground('sidebar')

  return (
    <aside
      className={cn(
        'fixed inset-y-0 left-0 z-50 flex flex-col border-r bg-card transition-all duration-300 lg:relative lg:z-0',
        // 移动端始终显示完整宽度，桌面端根据 sidebarOpen 切换
        'w-64 lg:w-auto',
        sidebarOpen ? 'lg:w-64' : 'lg:w-16',
        mobileMenuOpen ? 'translate-x-0' : '-translate-x-full lg:translate-x-0'
      )}
    >
      <BackgroundLayer config={sidebarBg} layerId="sidebar" />
      
      {/* Logo 区域 */}
      <LogoArea sidebarOpen={sidebarOpen} />

      <ScrollArea className={cn(
        "flex-1 overflow-x-hidden",
        !sidebarOpen && "lg:w-16"
      )}>
        <nav
          aria-label={t('a11y.sidebarNav')}
          className={cn(
          "p-4",
          !sidebarOpen && "lg:p-2 lg:w-16"
        )}>
          <ul className={cn(
            // 移动端始终使用正常间距,桌面端根据 sidebarOpen 切换
            "space-y-6",
            !sidebarOpen && "lg:space-y-3 lg:w-full"
          )}>
          {menuSections.map((section, sectionIndex) => (
            <li key={section.title}>
              {/* 块标题 - 移动端始终可见，桌面端根据 sidebarOpen 切换 */}
              <div className={cn(
                "px-3 h-[1.25rem]",
                // 移动端始终显示，桌面端根据状态切换
                "mb-2",
                !sidebarOpen && "lg:mb-1 lg:invisible"
              )}>
                <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground/60 whitespace-nowrap">
                  {t(section.title)}
                </h3>
              </div>

              {/* 分割线 - 仅在桌面端折叠时显示 */}
              {!sidebarOpen && sectionIndex > 0 && (
                <div className="hidden lg:block mb-2 border-t border-border" />
              )}

              {/* 菜单项列表 */}
              <ul className="space-y-1">
                {section.items.map((item) => (
                  <NavItem
                    key={item.path}
                    item={item}
                    sidebarOpen={sidebarOpen}
                    tooltipsEnabled={tooltipsEnabled}
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
