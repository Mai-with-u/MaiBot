import { createPortal } from 'react-dom'
import type { ReactNode } from 'react'

import { useMobileNav } from './mobile-nav-context'

/** 把页面自己的操作按钮放进手机页头右上角；桌面端页头不显示，因此也不会出现 */
export function MobileHeaderActions({ children }: { children: ReactNode }) {
  const { headerActionsElement } = useMobileNav()
  if (!headerActionsElement) return null
  return createPortal(children, headerActionsElement)
}
