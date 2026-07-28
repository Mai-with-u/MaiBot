import type { ComponentType, ReactNode } from 'react'

export interface LayoutProps {
  children: ReactNode
}

export type WorkspaceMode = 'settings' | 'chat' | 'logs'

export type MenuIcon = ComponentType<{
  className?: string
  color?: string
  size?: number | string
}>

export interface MenuItem {
  icon: MenuIcon
  label: string
  path: string
  external?: boolean
  searchDescription?: string
  tourId?: string
  featureFlag?: 'behaviorLearning'
}

export interface MenuSection {
  title: string
  items: MenuItem[]
}
