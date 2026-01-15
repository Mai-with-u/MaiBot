import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { ThemeProviderContext } from '@/lib/theme-context'

type Theme = 'dark' | 'light' | 'system'

type ThemeProviderProps = {
  children: ReactNode
  defaultTheme?: Theme
  storageKey?: string
}

export function ThemeProvider({
  children,
  defaultTheme = 'system',
  storageKey = 'ui-theme',
  ...props
}: ThemeProviderProps) {
  const [theme, setTheme] = useState<Theme>(
    () => (localStorage.getItem(storageKey) as Theme) || defaultTheme
  )

  useEffect(() => {
    const root = window.document.documentElement

    root.classList.remove('light', 'dark')

    if (theme === 'system') {
      const systemTheme = window.matchMedia('(prefers-color-scheme: dark)').matches
        ? 'dark'
        : 'light'

      root.classList.add(systemTheme)
      return
    }

    root.classList.add(theme)
  }, [theme])

  // 应用保存的主题色
  useEffect(() => {
    const savedAccentColor = localStorage.getItem('accent-color')
    if (savedAccentColor) {
      const root = document.documentElement
      const colors = {
        blue: { 
          hsl: '221.2 83.2% 53.3%', 
          darkHsl: '217.2 91.2% 59.8%',
          gradient: null
        },
        purple: { 
          hsl: '271 91% 65%', 
          darkHsl: '270 95% 75%',
          gradient: null
        },
        green: { 
          hsl: '142 71% 45%', 
          darkHsl: '142 76% 36%',
          gradient: null
        },
        orange: { 
          hsl: '25 95% 53%', 
          darkHsl: '20 90% 48%',
          gradient: null
        },
        pink: { 
          hsl: '330 81% 60%', 
          darkHsl: '330 85% 70%',
          gradient: null
        },
        red: { 
          hsl: '0 84% 60%', 
          darkHsl: '0 90% 70%',
          gradient: null
        },
        
        // 渐变色
        'gradient-sunset': { 
          hsl: '15 95% 60%', 
          darkHsl: '15 95% 65%',
          gradient: 'linear-gradient(135deg, hsl(25 95% 53%) 0%, hsl(330 81% 60%) 100%)'
        },
        'gradient-ocean': { 
          hsl: '200 90% 55%', 
          darkHsl: '200 90% 60%',
          gradient: 'linear-gradient(135deg, hsl(221.2 83.2% 53.3%) 0%, hsl(189 94% 43%) 100%)'
        },
        'gradient-forest': { 
          hsl: '150 70% 45%', 
          darkHsl: '150 75% 40%',
          gradient: 'linear-gradient(135deg, hsl(142 71% 45%) 0%, hsl(158 64% 52%) 100%)'
        },
        'gradient-aurora': { 
          hsl: '310 85% 65%', 
          darkHsl: '310 90% 70%',
          gradient: 'linear-gradient(135deg, hsl(271 91% 65%) 0%, hsl(330 81% 60%) 100%)'
        },
        'gradient-fire': { 
          hsl: '15 95% 55%', 
          darkHsl: '15 95% 60%',
          gradient: 'linear-gradient(135deg, hsl(0 84% 60%) 0%, hsl(25 95% 53%) 100%)'
        },
        'gradient-twilight': { 
          hsl: '250 90% 60%', 
          darkHsl: '250 95% 65%',
          gradient: 'linear-gradient(135deg, hsl(239 84% 67%) 0%, hsl(271 91% 65%) 100%)'
        },
      }

      const selectedColor = colors[savedAccentColor as keyof typeof colors]
      if (selectedColor) {
        root.style.setProperty('--primary', selectedColor.hsl)
        
        // 设置渐变（如果有）
        if (selectedColor.gradient) {
          root.style.setProperty('--primary-gradient', selectedColor.gradient)
          root.classList.add('has-gradient')
        } else {
          root.style.removeProperty('--primary-gradient')
          root.classList.remove('has-gradient')
        }
      }
    }
  }, [])

  const value = {
    theme,
    setTheme: (theme: Theme) => {
      localStorage.setItem(storageKey, theme)
      setTheme(theme)
    },
  }

  return (
    <ThemeProviderContext.Provider {...props} value={value}>
      {children}
    </ThemeProviderContext.Provider>
  )
}
