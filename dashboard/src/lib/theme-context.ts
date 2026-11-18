import { createContext } from 'react'

import { DEFAULT_DASHBOARD_STYLE, DEFAULT_DASHBOARD_STYLE_CONFIG } from './theme/tokens'
import type { DashboardStyle, UserThemeConfig } from './theme/tokens'

type Theme = 'dark' | 'light' | 'system'

export type ThemeProviderState = {
  theme: Theme
  resolvedTheme: 'dark' | 'light'
  setTheme: (theme: Theme) => void
  themeConfig: UserThemeConfig
  updateThemeConfig: (partial: Partial<UserThemeConfig>) => void
  resetTheme: () => void
}

const initialState: ThemeProviderState = {
  theme: 'system',
  resolvedTheme: 'light',
  setTheme: () => null,
  themeConfig: {
    selectedPreset: 'light',
    accentColor: '',
    styleTokenOverrides: {},
    styleCustomCSS: {},
    dashboardStyle: DEFAULT_DASHBOARD_STYLE,
    styleBackgroundConfig: {},
    styleConfig: DEFAULT_DASHBOARD_STYLE_CONFIG,
  },
  updateThemeConfig: () => null,
  resetTheme: () => null,
}

export const ThemeProviderContext = createContext<ThemeProviderState>(initialState)
export const DashboardStylePreviewContext = createContext<(style: DashboardStyle | null) => void>(() => {})
