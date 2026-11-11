import { Link } from '@tanstack/react-router'
import { useTranslation } from 'react-i18next'

import { useTheme } from '@/components/use-theme'
import { cn } from '@/lib/utils'

interface LogoAreaProps {
  sidebarOpen: boolean
  onMobileMenuClose?: () => void
}

function SpectrumBands() {
  return (
    <>
      {Array.from({ length: 6 }, (_, index) => (
        <span key={index} />
      ))}
    </>
  )
}

export function LogoArea({ sidebarOpen, onMobileMenuClose }: LogoAreaProps) {
  const { t } = useTranslation()
  const { themeConfig } = useTheme()
  const homeLabel = t('sidebar.menu.home')
  // 原版风格的 Logo 行与顶栏同高：色块在前、字标在后，字标用大小写混排。
  const isModernStyle = themeConfig.dashboardStyle === 'modern'

  return (
    <div
      data-dashboard-logo-area="true"
      className="relative flex h-[var(--layout-sidebar-logo-height)] items-center border-b px-[var(--layout-sidebar-logo-padding-x)]"
    >
      <div
        className="relative flex w-full items-center justify-center overflow-visible lg:w-[calc(var(--layout-sidebar-width)-var(--layout-sidebar-logo-padding-x)-var(--layout-sidebar-logo-padding-x))] lg:flex-none"
      >
        {/* 移动端始终显示完整 Logo，桌面端根据 sidebarOpen 切换 */}
        <div
          className={cn(
            'flex h-10 w-28 shrink-0 flex-col items-start justify-start gap-2 transition-opacity duration-[220ms] motion-reduce:transition-none',
            !sidebarOpen && 'lg:invisible lg:opacity-0'
          )}
        >
          <Link
            to="/"
            activeOptions={{ exact: true }}
            aria-label={homeLabel}
            title={homeLabel}
            onClick={onMobileMenuClose}
            data-dashboard-logo-title="true"
            className="w-28 whitespace-nowrap text-left text-xl font-bold text-primary-gradient"
          >
            {isModernStyle ? 'MaiBot' : 'MAIBOT'}
          </Link>
          {sidebarOpen && (
            <span
              aria-hidden="true"
              data-dashboard-logo-spectrum="true"
              className="flex h-2 w-28 max-w-full translate-y-1 items-end"
            >
              <SpectrumBands />
            </span>
          )}
        </div>
      </div>
      {/* 折叠彩虹块与导航 Tab 复用相同的左边距和宽度。 */}
      {!sidebarOpen && (
        <Link
          to="/"
          activeOptions={{ exact: true }}
          aria-label={homeLabel}
          title={homeLabel}
          onClick={onMobileMenuClose}
          data-dashboard-logo-spectrum="true"
          data-dashboard-logo-spectrum-block="true"
          className="absolute top-[1.625rem] left-[var(--layout-sidebar-nav-padding-collapsed)] flex h-7 w-[var(--layout-sidebar-nav-item-collapsed-width)] items-stretch overflow-hidden max-lg:hidden"
        >
          <SpectrumBands />
        </Link>
      )}
    </div>
  )
}
