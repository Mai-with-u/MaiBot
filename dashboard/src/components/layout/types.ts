import type { ComponentType, ReactNode } from 'react'

export interface LayoutProps {
  children: ReactNode
}

export type WorkspaceMode = 'settings' | 'chat' | 'logs' | `plugin:${string}`

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
  featureFlag?: 'replyEffects'
}

export interface MenuSection {
  title: string
  literalTitle?: boolean
  items: MenuItem[]
}
