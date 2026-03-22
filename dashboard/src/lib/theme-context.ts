/**
 * 主题上下文
 */

import { createContext } from 'react'

type Theme = 'dark' | 'light' | 'system'

interface ThemeProviderContextValue {
  theme: Theme
  setTheme: (theme: Theme) => void
}

export const ThemeProviderContext = createContext<ThemeProviderContextValue>({
  theme: 'system',
  setTheme: () => {},
})
