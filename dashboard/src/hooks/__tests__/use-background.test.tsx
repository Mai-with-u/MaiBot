import type { ReactNode } from 'react'

import { renderHook } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import type { ThemeProviderState } from '@/lib/theme-context'
import { ThemeProviderContext } from '@/lib/theme-context'
import type { BackgroundConfig, UserThemeConfig } from '@/lib/theme/tokens'
import {
  DEFAULT_DASHBOARD_STYLE_CONFIG,
  defaultBackgroundConfig,
  defaultBackgroundEffects,
} from '@/lib/theme/tokens'

import { useBackground } from '../use-background'

/** 构造一个带有可辨识 assetId 的背景配置 */
function makeBackgroundConfig(overrides: Partial<BackgroundConfig> = {}): BackgroundConfig {
  return {
    type: 'image',
    assetId: 'asset-default',
    effects: defaultBackgroundEffects,
    customCSS: '',
    ...overrides,
  }
}

/** 构造完整的用户主题配置，默认风格为 future-retro */
function makeThemeConfig(overrides: Partial<UserThemeConfig> = {}): UserThemeConfig {
  return {
    selectedPreset: 'light',
    accentColor: '',
    styleTokenOverrides: {},
    styleCustomCSS: {},
    dashboardStyle: 'future-retro',
    styleConfig: DEFAULT_DASHBOARD_STYLE_CONFIG,
    ...overrides,
  }
}

/** 用真实的 ThemeProviderContext 包裹，注入受控的主题配置 */
function makeWrapper(themeConfig: UserThemeConfig) {
  const state: ThemeProviderState = {
    theme: 'light',
    resolvedTheme: 'light',
    setTheme: () => {},
    themeConfig,
    updateThemeConfig: () => {},
    resetTheme: () => {},
  }
  return ({ children }: { children: ReactNode }) => (
    <ThemeProviderContext.Provider value={state}>{children}</ThemeProviderContext.Provider>
  )
}

describe('useBackground', () => {
  it('未配置任何背景时返回默认配置且不标记继承', () => {
    const { result } = renderHook(() => useBackground('page'), {
      wrapper: makeWrapper(makeThemeConfig()),
    })

    expect(result.current).toEqual({
      config: defaultBackgroundConfig,
      inheritEnabled: false,
      inheritedFrom: null,
    })
  })

  it('page 层返回当前风格下的自身配置', () => {
    const pageConfig = makeBackgroundConfig({ assetId: 'page-bg' })
    const { result } = renderHook(() => useBackground('page'), {
      wrapper: makeWrapper(
        makeThemeConfig({
          styleBackgroundConfig: { 'future-retro': { page: pageConfig } },
        })
      ),
    })

    expect(result.current.config).toBe(pageConfig)
    expect(result.current.inheritEnabled).toBe(false)
    expect(result.current.inheritedFrom).toBeNull()
  })

  it('读取的是 dashboardStyle 对应风格下的配置', () => {
    const modernPage = makeBackgroundConfig({ assetId: 'modern-page' })
    const retroPage = makeBackgroundConfig({ assetId: 'retro-page' })
    const { result } = renderHook(() => useBackground('page'), {
      wrapper: makeWrapper(
        makeThemeConfig({
          dashboardStyle: 'modern',
          styleBackgroundConfig: {
            modern: { page: modernPage },
            'future-retro': { page: retroPage },
          },
        })
      ),
    })

    expect(result.current.config).toBe(modernPage)
  })

  it('非 page 层开启 inherit 时返回 page 配置并标记继承来源', () => {
    const pageConfig = makeBackgroundConfig({ assetId: 'page-bg' })
    const cardConfig = makeBackgroundConfig({ assetId: 'card-bg', inherit: true })
    const { result } = renderHook(() => useBackground('card'), {
      wrapper: makeWrapper(
        makeThemeConfig({
          styleBackgroundConfig: { 'future-retro': { page: pageConfig, card: cardConfig } },
        })
      ),
    })

    expect(result.current.config).toBe(pageConfig)
    expect(result.current.inheritEnabled).toBe(true)
    expect(result.current.inheritedFrom).toBe('page')
  })

  it('非 page 层开启 inherit 但缺少 page 配置时回落到默认配置', () => {
    const sidebarConfig = makeBackgroundConfig({ assetId: 'sidebar-bg', inherit: true })
    const { result } = renderHook(() => useBackground('sidebar'), {
      wrapper: makeWrapper(
        makeThemeConfig({
          styleBackgroundConfig: { 'future-retro': { sidebar: sidebarConfig } },
        })
      ),
    })

    expect(result.current.config).toBe(defaultBackgroundConfig)
    expect(result.current.inheritEnabled).toBe(true)
    expect(result.current.inheritedFrom).toBe('page')
  })

  it('非 page 层未开启 inherit 时返回自身配置', () => {
    const headerConfig = makeBackgroundConfig({ assetId: 'header-bg' })
    const { result } = renderHook(() => useBackground('header'), {
      wrapper: makeWrapper(
        makeThemeConfig({
          styleBackgroundConfig: {
            'future-retro': {
              page: makeBackgroundConfig({ assetId: 'page-bg' }),
              header: headerConfig,
            },
          },
        })
      ),
    })

    expect(result.current.config).toBe(headerConfig)
    expect(result.current.inheritEnabled).toBe(false)
    expect(result.current.inheritedFrom).toBeNull()
  })

  it('page 层即使误开 inherit 也不发生继承', () => {
    const pageConfig = makeBackgroundConfig({ assetId: 'page-bg', inherit: true })
    const { result } = renderHook(() => useBackground('page'), {
      wrapper: makeWrapper(
        makeThemeConfig({
          styleBackgroundConfig: { 'future-retro': { page: pageConfig } },
        })
      ),
    })

    // page 是继承链顶端：仍返回自身配置，inheritedFrom 保持 null
    expect(result.current.config).toBe(pageConfig)
    expect(result.current.inheritEnabled).toBe(true)
    expect(result.current.inheritedFrom).toBeNull()
  })
})
