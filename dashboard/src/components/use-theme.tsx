import { useContext } from 'react'
import { ThemeProviderContext } from '@/lib/theme-context'

export const useTheme = () => {
  const context = useContext(ThemeProviderContext)

  if (context === undefined) throw new Error('useTheme must be used within a ThemeProvider')

  return context
}

export const toggleThemeWithTransition = (
  theme: 'dark' | 'light' | 'system',
  setTheme: (theme: 'dark' | 'light' | 'system') => void,
  event: React.MouseEvent
) => {
  // 检查是否禁用动画
  const animationsDisabled = document.documentElement.classList.contains('no-animations')
  
  // 检查浏览器是否支持 View Transitions API
  if (!document.startViewTransition || animationsDisabled) {
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
    // 始终在新内容层应用动画(z-index: 999)
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
