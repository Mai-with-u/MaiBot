/**
 * theme/pipeline 单元测试：验证令牌计算、CSS 变量注入
 * 与自定义 CSS（全局/组件级）的注入与清理。
 */
import { afterEach, describe, expect, it } from 'vitest'

import { DEFAULT_ACCENT_COLOR_HSL, getReadableForeground } from '../palette'
import {
  applyThemePipeline,
  getComputedTokens,
  injectComponentCSS,
  injectCustomCSS,
  injectTokensAsCSS,
  removeAllComponentCSS,
  removeCustomCSS,
} from '../pipeline'
import {
  DEFAULT_DASHBOARD_STYLE_CONFIG,
  defaultBackgroundConfig,
  defaultLightTokens,
} from '../tokens'
import type { ThemeTokens, UserThemeConfig } from '../tokens'

/** 构造一份合法的完整主题配置，默认使用 modern 风格便于隔离复古令牌 */
const buildConfig = (overrides: Partial<UserThemeConfig> = {}): UserThemeConfig => ({
  selectedPreset: 'light',
  accentColor: DEFAULT_ACCENT_COLOR_HSL,
  styleTokenOverrides: {},
  styleCustomCSS: {},
  styleBackgroundConfig: {},
  dashboardStyle: 'modern',
  styleConfig: DEFAULT_DASHBOARD_STYLE_CONFIG,
  ...overrides,
})

afterEach(() => {
  // 清理注入到 head 的 style 标签与根节点上的 CSS 变量，避免跨用例污染
  document.querySelectorAll('style').forEach((el) => el.remove())
  document.documentElement.removeAttribute('style')
})

describe('getComputedTokens', () => {
  it('默认强调色 + modern 亮色时基于默认主色生成调色板，仅替换 accent', () => {
    const tokens = getComputedTokens(buildConfig(), false)

    expect(tokens.color.primary).toBe(defaultLightTokens.color.primary)
    expect(tokens.color.accent).toBe(DEFAULT_ACCENT_COLOR_HSL)
    expect(tokens.color['accent-foreground']).toBe(getReadableForeground(DEFAULT_ACCENT_COLOR_HSL))
    // modern 风格不合并复古排版令牌
    expect(tokens.typography).toEqual(defaultLightTokens.typography)
  })

  it('自定义强调色时整个调色板围绕它重建', () => {
    const tokens = getComputedTokens(buildConfig({ accentColor: '200 80% 50%' }), false)

    expect(tokens.color.primary).toBe('200 80% 50%')
    expect(tokens.color.ring).toBe('200 80% 50%')
  })

  it('future-retro 风格按明暗合并对应复古令牌', () => {
    const light = getComputedTokens(buildConfig({ dashboardStyle: 'future-retro' }), false)
    expect(light.color.primary).toBe('15.6 68.7% 45.1%')
    expect(light.typography['font-family-base']).toContain('MaiRetroText')

    const dark = getComputedTokens(buildConfig({ dashboardStyle: 'future-retro' }), true)
    expect(dark.color.primary).toBe('19.2 44.7% 42.5%')
  })

  it('仅当前风格的 token 覆盖最后生效', () => {
    const tokens = getComputedTokens(
      buildConfig({
        dashboardStyle: 'modern',
        styleTokenOverrides: {
          modern: { color: { primary: '1 2% 3%' } as ThemeTokens['color'] },
          'future-retro': { color: { primary: '9 9% 9%' } as ThemeTokens['color'] },
        },
      }),
      false
    )

    expect(tokens.color.primary).toBe('1 2% 3%')
    // 非当前风格的覆盖不生效
    expect(tokens.color.primary).not.toBe('9 9% 9%')
    // 未覆盖的字段仍保留调色板生成结果
    expect(tokens.color.accent).toBe(DEFAULT_ACCENT_COLOR_HSL)
  })
})

describe('injectTokensAsCSS', () => {
  it('把五类令牌写入目标元素的 CSS 变量', () => {
    const el = document.createElement('div')

    injectTokensAsCSS(defaultLightTokens, el)

    expect(el.style.getPropertyValue('--color-primary')).toBe(defaultLightTokens.color.primary)
    expect(el.style.getPropertyValue('--typography-font-weight-bold')).toBe('700')
    expect(el.style.getPropertyValue('--visual-radius-md')).toBe('0.375rem')
    expect(el.style.getPropertyValue('--layout-header-height')).toBe('3.5rem')
    expect(el.style.getPropertyValue('--animation-anim-duration-fast')).toBe('150ms')
  })
})

describe('injectCustomCSS / removeCustomCSS', () => {
  it('创建全局 style 标签并写入内容', () => {
    injectCustomCSS('body { color: red; }')

    const el = document.getElementById('maibot-custom-css')
    expect(el).not.toBeNull()
    expect(el?.tagName).toBe('STYLE')
    expect(el?.textContent).toBe('body { color: red; }')
  })

  it('重复注入时复用同一标签并更新内容', () => {
    injectCustomCSS('.a { color: red; }')
    injectCustomCSS('.b { color: blue; }')

    expect(document.querySelectorAll('style#maibot-custom-css')).toHaveLength(1)
    expect(document.getElementById('maibot-custom-css')?.textContent).toBe('.b { color: blue; }')
  })

  it('注入空白内容等价于移除既有标签', () => {
    injectCustomCSS('.a { color: red; }')
    injectCustomCSS('   ')

    expect(document.getElementById('maibot-custom-css')).toBeNull()
  })

  it('removeCustomCSS 移除既有标签', () => {
    injectCustomCSS('.a { color: red; }')
    removeCustomCSS()

    expect(document.getElementById('maibot-custom-css')).toBeNull()
  })
})

describe('injectComponentCSS / removeAllComponentCSS', () => {
  it('注入前会 sanitize，危险内容被剥离', () => {
    injectComponentCSS('.card { color: red; }\n@import "https://evil.com/x.css";', 'card')

    const el = document.getElementById('maibot-bg-css-card')
    expect(el?.textContent).toContain('.card { color: red; }')
    expect(el?.textContent).not.toContain('@import')
  })

  it('sanitize 后为空时移除既有组件标签', () => {
    injectComponentCSS('.dialog { opacity: 0.5; }', 'dialog')
    expect(document.getElementById('maibot-bg-css-dialog')).not.toBeNull()

    injectComponentCSS('@import "https://evil.com/x.css";', 'dialog')
    expect(document.getElementById('maibot-bg-css-dialog')).toBeNull()
  })

  it('removeAllComponentCSS 清理全部组件标签', () => {
    injectComponentCSS('.page { color: red; }', 'page')
    injectComponentCSS('.sidebar { color: blue; }', 'sidebar')

    removeAllComponentCSS()

    expect(document.getElementById('maibot-bg-css-page')).toBeNull()
    expect(document.getElementById('maibot-bg-css-sidebar')).toBeNull()
  })
})

describe('applyThemePipeline', () => {
  it('modern 风格：注入令牌到根节点并注入全局自定义 CSS', () => {
    applyThemePipeline(
      buildConfig({
        dashboardStyle: 'modern',
        styleCustomCSS: { modern: '.x { color: red; }' },
      }),
      false
    )

    expect(document.documentElement.style.getPropertyValue('--color-primary')).toBe(
      defaultLightTokens.color.primary
    )
    expect(document.getElementById('maibot-custom-css')?.textContent).toBe('.x { color: red; }')
  })

  it('modern 风格：自定义 CSS 全是危险内容时移除既有全局标签', () => {
    injectCustomCSS('.stale { color: blue; }')

    applyThemePipeline(
      buildConfig({
        dashboardStyle: 'modern',
        styleCustomCSS: { modern: '@import "https://evil.com/x.css";' },
      }),
      false
    )

    expect(document.getElementById('maibot-custom-css')).toBeNull()
  })

  it('future-retro 风格：忽略全局自定义 CSS 并应用复古暗色令牌', () => {
    injectCustomCSS('.stale { color: blue; }')

    applyThemePipeline(
      buildConfig({
        dashboardStyle: 'future-retro',
        styleCustomCSS: { modern: '.x { color: red; }' },
      }),
      true
    )

    expect(document.getElementById('maibot-custom-css')).toBeNull()
    expect(document.documentElement.style.getPropertyValue('--color-primary')).toBe(
      '19.2 44.7% 42.5%'
    )
  })

  it('组件背景配置：有 customCSS 的组件注入样式，无配置时全部清理', () => {
    applyThemePipeline(
      buildConfig({
        dashboardStyle: 'modern',
        styleBackgroundConfig: {
          modern: {
            card: { ...defaultBackgroundConfig, customCSS: '.card-bg { opacity: 0.5; }' },
          },
        },
      }),
      false
    )

    expect(document.getElementById('maibot-bg-css-card')?.textContent).toContain('.card-bg')
    expect(document.getElementById('maibot-bg-css-page')).toBeNull()

    // 再次应用无背景配置的主题时，之前的组件样式被清理
    applyThemePipeline(buildConfig({ dashboardStyle: 'modern' }), false)
    expect(document.getElementById('maibot-bg-css-card')).toBeNull()
  })
})
