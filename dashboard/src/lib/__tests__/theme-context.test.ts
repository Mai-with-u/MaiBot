import { renderHook } from '@testing-library/react'
import { useContext } from 'react'
import { describe, expect, it } from 'vitest'

import { ThemeProviderContext } from '../theme-context'
import { DEFAULT_DASHBOARD_STYLE, DEFAULT_DASHBOARD_STYLE_CONFIG } from '../theme/tokens'

describe('ThemeProviderContext', () => {
  it('无 Provider 时暴露默认初始值，空实现可调用且不抛错', () => {
    const { result } = renderHook(() => useContext(ThemeProviderContext))
    const state = result.current

    expect(state.theme).toBe('system')
    expect(state.resolvedTheme).toBe('light')
    expect(state.themeConfig).toEqual({
      selectedPreset: 'light',
      accentColor: '',
      styleTokenOverrides: {},
      styleCustomCSS: {},
      dashboardStyle: DEFAULT_DASHBOARD_STYLE,
      styleBackgroundConfig: {},
      styleConfig: DEFAULT_DASHBOARD_STYLE_CONFIG,
    })

    expect(state.setTheme('dark')).toBeNull()
    expect(state.updateThemeConfig({ accentColor: '#fff' })).toBeNull()
    expect(state.resetTheme()).toBeNull()
  })
})
