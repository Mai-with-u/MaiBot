import type { ComponentType } from 'react'

import type { FeatureFlags } from '@/hooks/use-feature-flags'

export type MenuIcon = ComponentType<{
  className?: string
  color?: string
  size?: number | string
}>

export interface MenuItem {
  icon: MenuIcon
  label: string
  literalLabel?: boolean
  path: string
  external?: boolean
  searchDescription?: string
  tourId?: string
  featureFlag?: keyof FeatureFlags
}

export interface MenuSection {
  title: string
  literalTitle?: boolean
  items: MenuItem[]
}
