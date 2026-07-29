import { useId } from 'react'
import { motion } from 'motion/react'

import { cn } from '@/lib/utils'

interface LogoAreaProps {
  sidebarOpen: boolean
}

export function LogoArea({ sidebarOpen }: LogoAreaProps) {
  const spectrumLayoutId = useId()

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
            'flex h-10 w-28 shrink-0 flex-col items-start justify-start gap-2 transition-opacity duration-100',
            !sidebarOpen && 'lg:pointer-events-none lg:opacity-0'
          )}
        >
          <span
            data-dashboard-logo-title="true"
            className="w-28 whitespace-nowrap text-xl font-bold text-primary-gradient"
          >
            MAIBOT
          </span>
          {sidebarOpen && (
            <motion.span
              layoutId={`sidebar-logo-spectrum-${spectrumLayoutId}`}
              aria-hidden="true"
              data-dashboard-logo-spectrum="true"
              className="flex h-2 w-28 max-w-full translate-y-1 items-end"
              transition={{ type: 'spring', stiffness: 360, damping: 32, mass: 0.7 }}
            >
              {Array.from({ length: 6 }, (_, index) => (
                <motion.span
                  key={index}
                  layoutId={`sidebar-logo-spectrum-band-${spectrumLayoutId}-${index}`}
                  transition={{ type: 'spring', stiffness: 360, damping: 32, mass: 0.7 }}
                />
              ))}
            </motion.span>
          )}
        </div>
      </div>
      {/* 折叠彩虹块与导航 Tab 复用相同的左边距和宽度。 */}
      {!sidebarOpen && (
        <motion.span
          layoutId={`sidebar-logo-spectrum-${spectrumLayoutId}`}
          aria-hidden="true"
          data-dashboard-logo-spectrum="true"
          data-dashboard-logo-spectrum-block="true"
          className="pointer-events-none absolute top-[1.625rem] left-[var(--layout-sidebar-nav-padding-collapsed)] flex h-7 w-[var(--layout-sidebar-nav-item-collapsed-width)] items-stretch overflow-hidden max-lg:hidden"
          transition={{ type: 'spring', stiffness: 360, damping: 32, mass: 0.7 }}
        >
          {Array.from({ length: 6 }, (_, index) => (
            <motion.span
              key={index}
              layoutId={`sidebar-logo-spectrum-band-${spectrumLayoutId}-${index}`}
              transition={{ type: 'spring', stiffness: 360, damping: 32, mass: 0.7 }}
            />
          ))}
        </motion.span>
      )}
    </div>
  )
}
