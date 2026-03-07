import { useCallback, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'

import { ThemeProviderContext } from '@/lib/theme-context'
import type { UserThemeConfig } from '@/lib/theme/tokens'
import {
  THEME_STORAGE_KEYS,
  loadThemeConfig,
  migrateOldKeys,
  resetThemeToDefault,
  saveThemePartial,
} from '@/lib/theme/storage'
import { applyThemePipeline, removeCustomCSS } from '@/lib/theme/pipeline'

type Theme = 'dark' | 'light' | 'system'

type ThemeProviderProps = {
  children: ReactNode
  defaultTheme?: Theme
  storageKey?: string
}

export function ThemeProvider({
  children,
  defaultTheme = 'system',
  storageKey: _storageKey,
}: ThemeProviderProps) {
  const [themeMode, setThemeMode] = useState<Theme>(() => {
    const saved = localStorage.getItem(THEME_STORAGE_KEYS.MODE) as Theme | null
    return saved || defaultTheme
  })
  const [themeConfig, setThemeConfig] = useState<UserThemeConfig>(() => loadThemeConfig())
  const [systemThemeTick, setSystemThemeTick] = useState(0)

  const resolvedTheme = useMemo<'dark' | 'light'>(() => {
    if (themeMode !== 'system') return themeMode
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
  }, [themeMode, systemThemeTick])

  useEffect(() => {
    migrateOldKeys()
  }, [])

  useEffect(() => {
    const mediaQuery = window.matchMedia('(prefers-color-scheme: dark)')
    const handleChange = () => {
      if (themeMode === 'system') {
        setSystemThemeTick((prev) => prev + 1)
      }
    }
    mediaQuery.addEventListener('change', handleChange)
    return () => mediaQuery.removeEventListener('change', handleChange)
  }, [themeMode])

  useEffect(() => {
    const root = document.documentElement
    root.classList.remove('light', 'dark')
    root.classList.add(resolvedTheme)

    const isDark = resolvedTheme === 'dark'
    applyThemePipeline(themeConfig, isDark)
  }, [resolvedTheme, themeConfig])

  const setTheme = useCallback((mode: Theme) => {
    localStorage.setItem(THEME_STORAGE_KEYS.MODE, mode)
    setThemeMode(mode)
  }, [])

  const updateThemeConfig = useCallback((partial: Partial<UserThemeConfig>) => {
    saveThemePartial(partial)
    setThemeConfig((prev) => ({ ...prev, ...partial }))
  }, [])

  const resetTheme = useCallback(() => {
    resetThemeToDefault()
    removeCustomCSS()
    setThemeConfig(loadThemeConfig())
  }, [])

  const value = useMemo(
    () => ({
      theme: themeMode,
      resolvedTheme,
      setTheme,
      themeConfig,
      updateThemeConfig,
      resetTheme,
    }),
    [themeMode, resolvedTheme, setTheme, themeConfig, updateThemeConfig, resetTheme],
  )

  return (
    <ThemeProviderContext value={value}>
      {children}
    </ThemeProviderContext>
  )
}
