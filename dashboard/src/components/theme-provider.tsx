import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'

import { ThemeProviderContext } from '@/lib/theme-context'
import { getBotConfig, updateBotConfigSection } from '@/lib/config-api'
import { DEFAULT_DASHBOARD_STYLE, DEFAULT_FUTURE_RETRO_STYLE_CONFIG } from '@/lib/theme/tokens'
import type { DashboardStyle, UserThemeConfig } from '@/lib/theme/tokens'
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

function dashboardStyleToConfigValue(style: DashboardStyle): 0 | 1 {
  return style === 'future-retro' ? 1 : 0
}

function configValueToDashboardStyle(value: unknown): DashboardStyle {
  return Number(value) === 1 ? 'future-retro' : 'modern'
}

function shouldSyncRemoteWebUIStyle(): boolean {
  return !window.location.pathname.startsWith('/auth')
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
  const pendingWebUIStyleRef = useRef<0 | 1 | null>(null)

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
    const dashboardStyle = themeConfig.dashboardStyle ?? DEFAULT_DASHBOARD_STYLE
    const futureRetroConfig = {
      ...DEFAULT_FUTURE_RETRO_STYLE_CONFIG,
      ...themeConfig.styleConfig?.futureRetro,
    }

    root.dataset.dashboardStyle = dashboardStyle
    root.dataset.retroPaperTexture = futureRetroConfig.paperTexture ? 'true' : 'false'
    root.dataset.retroStrongBorders = futureRetroConfig.strongBorders ? 'true' : 'false'

    applyThemePipeline(themeConfig, isDark)
  }, [resolvedTheme, themeConfig])

  const applyRemoteWebUIStyle = useCallback((styleValue: unknown) => {
    const nextDashboardStyle = configValueToDashboardStyle(styleValue)

    setThemeConfig((prev) => {
      if (prev.dashboardStyle === nextDashboardStyle) {
        return prev
      }

      saveThemePartial({ dashboardStyle: nextDashboardStyle })
      return { ...prev, dashboardStyle: nextDashboardStyle }
    })
  }, [])

  const loadRemoteWebUIStyle = useCallback(async () => {
    if (!shouldSyncRemoteWebUIStyle() || pendingWebUIStyleRef.current !== null) {
      return
    }

    try {
      const result = await getBotConfig()
      const webuiConfig = result.webui as Record<string, unknown> | undefined
      if (!webuiConfig || !('webui_style' in webuiConfig)) {
        return
      }

      applyRemoteWebUIStyle(webuiConfig.webui_style)
    } catch (error) {
      console.debug('同步 WebUI 风格配置失败:', error)
    }
  }, [applyRemoteWebUIStyle])

  const persistRemoteWebUIStyle = useCallback(async (style: DashboardStyle) => {
    if (!shouldSyncRemoteWebUIStyle()) {
      return
    }

    const webuiStyle = dashboardStyleToConfigValue(style)
    pendingWebUIStyleRef.current = webuiStyle

    try {
      await updateBotConfigSection('webui', { webui_style: webuiStyle })
    } catch (error) {
      console.warn('保存 WebUI 风格配置失败:', error)
    } finally {
      pendingWebUIStyleRef.current = null
    }
  }, [])

  useEffect(() => {
    void loadRemoteWebUIStyle()

    const handleFocus = () => {
      void loadRemoteWebUIStyle()
    }
    const handleVisibilityChange = () => {
      if (!document.hidden) {
        void loadRemoteWebUIStyle()
      }
    }

    window.addEventListener('focus', handleFocus)
    document.addEventListener('visibilitychange', handleVisibilityChange)

    return () => {
      window.removeEventListener('focus', handleFocus)
      document.removeEventListener('visibilitychange', handleVisibilityChange)
    }
  }, [loadRemoteWebUIStyle])

  const setTheme = useCallback((mode: Theme) => {
    localStorage.setItem(THEME_STORAGE_KEYS.MODE, mode)
    setThemeMode(mode)
  }, [])

  const updateThemeConfig = useCallback(
    (partial: Partial<UserThemeConfig>) => {
      saveThemePartial(partial)
      setThemeConfig((prev) => ({ ...prev, ...partial }))

      if (partial.dashboardStyle) {
        void persistRemoteWebUIStyle(partial.dashboardStyle)
      }
    },
    [persistRemoteWebUIStyle]
  )

  const resetTheme = useCallback(() => {
    resetThemeToDefault()
    removeCustomCSS()
    const defaultThemeConfig = loadThemeConfig()
    setThemeConfig(defaultThemeConfig)
    void persistRemoteWebUIStyle(defaultThemeConfig.dashboardStyle)
  }, [persistRemoteWebUIStyle])

  const value = useMemo(
    () => ({
      theme: themeMode,
      resolvedTheme,
      setTheme,
      themeConfig,
      updateThemeConfig,
      resetTheme,
    }),
    [themeMode, resolvedTheme, setTheme, themeConfig, updateThemeConfig, resetTheme]
  )

  return <ThemeProviderContext value={value}>{children}</ThemeProviderContext>
}
