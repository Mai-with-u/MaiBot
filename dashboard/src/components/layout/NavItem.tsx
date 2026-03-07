import { Link, useMatchRoute } from '@tanstack/react-router'
import { useTranslation } from 'react-i18next'

import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { cn } from '@/lib/utils'

import type { MenuItem } from './types'

interface NavItemProps {
  item: MenuItem
  sidebarOpen: boolean
  tooltipsEnabled: boolean
  onMobileMenuClose: () => void
}

export function NavItem({ item, sidebarOpen, tooltipsEnabled, onMobileMenuClose }: NavItemProps) {
  const { t } = useTranslation()
  const matchRoute = useMatchRoute()
  const isActive = matchRoute({ to: item.path })
  const Icon = item.icon

  const menuItemContent = (
    <>
      {/* 左侧高亮条 */}
      {isActive && (
        <div className="absolute left-0 top-1/2 h-8 w-1 -translate-y-1/2 rounded-r-full bg-primary transition-opacity duration-300" />
      )}
      <div className={cn(
        'flex items-center transition-all duration-300',
        sidebarOpen ? 'gap-3' : 'gap-3 lg:gap-0'
      )}>
        <Icon
          className={cn(
            'h-5 w-5 flex-shrink-0',
            isActive && 'text-primary'
          )}
          strokeWidth={2}
          fill="none"
        />
        <span className={cn(
          'text-sm font-medium whitespace-nowrap transition-all duration-300',
          isActive && 'font-semibold',
          sidebarOpen 
            ? 'opacity-100 max-w-[200px]' 
            : 'opacity-100 max-w-[200px] lg:opacity-0 lg:max-w-0 lg:overflow-hidden'
        )}>
          {t(item.label)}
        </span>
      </div>
    </>
  )

  return (
    <li className="relative">
      <Tooltip>
        <TooltipTrigger asChild>
          <Link
            to={item.path}
            data-tour={item.tourId}
            className={cn(
              'relative flex items-center rounded-lg py-2 transition-all duration-300',
              'hover:bg-accent hover:text-accent-foreground',
              isActive
                ? 'bg-accent text-foreground'
                : 'text-muted-foreground hover:text-foreground',
              sidebarOpen ? 'px-3' : 'px-3 lg:px-0 lg:justify-center lg:w-12 lg:mx-auto'
            )}
            onClick={onMobileMenuClose}
          >
            {menuItemContent}
          </Link>
        </TooltipTrigger>
        {tooltipsEnabled && (
          <TooltipContent side="right" className="hidden lg:block">
            <p>{t(item.label)}</p>
          </TooltipContent>
        )}
      </Tooltip>
    </li>
  )
}
