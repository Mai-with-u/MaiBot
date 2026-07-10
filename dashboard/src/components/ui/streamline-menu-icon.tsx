import { Icon } from '@iconify/react'
import { createElement } from 'react'

import { useTheme } from '@/components/use-theme'
import { getStreamlineIcon } from './streamline-icons'

import type { MenuIcon } from '@/components/layout/types'

export function createStreamlineIcon(name: string, fallback?: MenuIcon): MenuIcon {
  return function StreamlineGeneratedIcon({ className, color, size = 20 }) {
    const { themeConfig } = useTheme()
    const icon = getStreamlineIcon('streamline-sharp', name)

    if ((themeConfig.dashboardStyle !== 'future-retro' || !icon) && fallback) {
      return createElement(fallback, { className, color, size })
    }

    return createElement(Icon, {
      icon: icon ?? `streamline-sharp:${name}`,
      className,
      color,
      width: size,
      height: size,
    })
  }
}
