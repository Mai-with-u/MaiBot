import type { MouseEvent } from 'react'
import { useContext } from 'react'

import { ThemeProviderContext } from '@/lib/theme-context'

const EDGE_USER_AGENT_PATTERN = /\bEdg(?:e|A|iOS)?\//

export const useTheme = () => {
  const context = useContext(ThemeProviderContext)

  if (context === undefined) throw new Error('useTheme must be used within a ThemeProvider')

  return context
}

export const isEdgeBrowser = (userAgent = navigator.userAgent) =>
  EDGE_USER_AGENT_PATTERN.test(userAgent)

export const toggleThemeWithTransition = (
  theme: 'dark' | 'light' | 'system',
  setTheme: (theme: 'dark' | 'light' | 'system') => void,
  event: MouseEvent
) => {
  const prefersReducedMotion =
    typeof window !== 'undefined' &&
    typeof window.matchMedia === 'function' &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches

  // 禁用动画或系统偏好减少动态效果时直接切换，避免触发全局过渡效果。
  const animationsDisabled =
    document.documentElement.classList.contains('no-animations') || prefersReducedMotion

  // Edge 的 View Transitions API 在主题切换时会出现显示异常，先回退到普通切换。
  if (!document.startViewTransition || animationsDisabled || isEdgeBrowser()) {
    setTheme(theme)
    return
  }

  const x = event.clientX
  const y = event.clientY
  const endRadius = Math.hypot(Math.max(x, innerWidth - x), Math.max(y, innerHeight - y))

  const transition = document.startViewTransition(() => {
    setTheme(theme)
  })

  transition.ready.then(() => {
    // 始终在新内容层应用圆形展开动画。
    document.documentElement.animate(
      {
        clipPath: [`circle(0px at ${x}px ${y}px)`, `circle(${endRadius}px at ${x}px ${y}px)`],
      },
      {
        duration: 500,
        easing: 'ease-in-out',
        pseudoElement: '::view-transition-new(root)',
      }
    )
  })
}
