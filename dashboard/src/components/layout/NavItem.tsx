import { Link, useMatchRoute } from '@tanstack/react-router'
import { useTranslation } from 'react-i18next'

import { cn } from '@/lib/utils'

import type { MenuItem } from './types'

interface NavItemProps {
  item: MenuItem
  sidebarOpen: boolean
  onMobileMenuClose: () => void
}

export function NavItem({
  item,
  sidebarOpen,
  onMobileMenuClose,
}: NavItemProps) {
  const { t } = useTranslation()
  const matchRoute = useMatchRoute()
  const isActive = item.external ? false : matchRoute({ to: item.path })
  const Icon = item.icon
  const label = t(item.label)

  const menuItemContent = (
    <>
      <div className={cn('flex min-w-0 items-center', sidebarOpen ? 'gap-3' : 'gap-3 lg:gap-0')}>
        <Icon
          data-dashboard-nav-icon="true"
          className={cn('h-5 w-5 flex-shrink-0', isActive && 'text-primary')}
          size={20}
        />
        <span
          data-dashboard-nav-label="true"
          className={cn(
            'text-base font-medium whitespace-nowrap transition-opacity duration-150',
            sidebarOpen
              ? 'max-w-[160px] min-w-0 overflow-hidden text-ellipsis opacity-100'
              : 'max-w-[200px] opacity-100 lg:max-w-0 lg:overflow-hidden lg:opacity-0'
          )}
          title={label}
        >
          {label}
        </span>
      </div>
    </>
  )

  const linkClassName = cn(
    'relative flex h-[var(--layout-sidebar-nav-item-height)] items-center rounded-lg px-[var(--layout-sidebar-nav-item-padding-x)] py-0 transition-colors duration-150',
    'hover:bg-accent hover:text-accent-foreground',
    isActive ? 'bg-accent text-foreground' : 'text-muted-foreground hover:text-foreground',
    sidebarOpen &&
      'lg:pl-[calc(var(--layout-sidebar-nav-icon-left)-var(--layout-sidebar-nav-padding-collapsed))]',
    !sidebarOpen &&
      'lg:w-[var(--layout-sidebar-nav-item-collapsed-width)] lg:justify-center lg:px-0'
  )
  const commonLinkProps = {
    'data-tour': item.tourId,
    'data-dashboard-nav-item': 'true',
    'data-active': isActive ? 'true' : 'false',
    style: {
      height: 'var(--layout-sidebar-nav-item-height)',
      minHeight: 'var(--layout-sidebar-nav-item-height)',
    },
    className: linkClassName,
    onClick: onMobileMenuClose,
  } as const

  const link = item.external ? (
    <a href={item.path} target="_blank" rel="noopener noreferrer" {...commonLinkProps}>
      {menuItemContent}
    </a>
  ) : (
    <Link to={item.path} {...commonLinkProps}>
      {menuItemContent}
    </Link>
  )

  return <li className="relative">{link}</li>
}
