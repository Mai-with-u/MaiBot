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
  /** 动态插件页面标题是纯文本，不应当作为 i18n key 解析。 */
  labelMode?: 'i18n' | 'text'
  path: string
  external?: boolean
  searchDescription?: string
  tourId?: string
  featureFlag?: 'behaviorLearning' | 'replyEffects'
}

export interface MenuSection {
  title: string
  items: MenuItem[]
}
