import { createContext } from 'react'

import type { UserThemeConfig } from './theme/tokens'

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
    tokenOverrides: {},
    customCSS: '',
  },
  updateThemeConfig: () => null,
  resetTheme: () => null,
}

export const ThemeProviderContext = createContext<ThemeProviderState>(initialState)
