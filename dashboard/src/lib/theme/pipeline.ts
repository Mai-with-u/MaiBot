import type { ThemeTokens, UserThemeConfig } from './tokens'

import { generatePalette } from './palette'
import { getPresetById } from './presets'
import { sanitizeCSS } from './sanitizer'
import { defaultDarkTokens, defaultLightTokens, tokenToCSSVarName } from './tokens'

const CUSTOM_CSS_ID = 'maibot-custom-css'
const COMPONENT_CSS_ID_PREFIX = 'maibot-bg-css-'
const COMPONENT_IDS = ['page', 'sidebar', 'header', 'card', 'dialog'] as const

const mergeTokens = (base: ThemeTokens, overrides: Partial<ThemeTokens>): ThemeTokens => {
  return {
    color: {
      ...base.color,
      ...(overrides.color ?? {}),
    },
    typography: {
      ...base.typography,
      ...(overrides.typography ?? {}),
    },
    visual: {
      ...base.visual,
      ...(overrides.visual ?? {}),
    },
    layout: {
      ...base.layout,
      ...(overrides.layout ?? {}),
    },
    animation: {
      ...base.animation,
      ...(overrides.animation ?? {}),
    },
  }
}

const buildTokens = (config: UserThemeConfig, isDark: boolean): ThemeTokens => {
  const baseTokens = isDark ? defaultDarkTokens : defaultLightTokens
  let mergedTokens = mergeTokens(baseTokens, {})

  if (config.accentColor) {
    const paletteTokens = generatePalette(config.accentColor, isDark)
    mergedTokens = mergeTokens(mergedTokens, { color: paletteTokens })
  }

  if (config.selectedPreset && config.selectedPreset !== 'light' && config.selectedPreset !== 'dark') {
    const preset = getPresetById(config.selectedPreset)
    if (preset?.tokens) {
      mergedTokens = mergeTokens(mergedTokens, preset.tokens)
    }
  }

  if (config.tokenOverrides) {
    mergedTokens = mergeTokens(mergedTokens, config.tokenOverrides)
  }

  return mergedTokens
}

export function getComputedTokens(config: UserThemeConfig, isDark: boolean): ThemeTokens {
  return buildTokens(config, isDark)
}

export function injectTokensAsCSS(tokens: ThemeTokens, target: HTMLElement): void {
  Object.entries(tokens.color).forEach(([key, value]) => {
    target.style.setProperty(tokenToCSSVarName('color', key), String(value))
  })

  Object.entries(tokens.typography).forEach(([key, value]) => {
    target.style.setProperty(tokenToCSSVarName('typography', key), String(value))
  })

  Object.entries(tokens.visual).forEach(([key, value]) => {
    target.style.setProperty(tokenToCSSVarName('visual', key), String(value))
  })

  Object.entries(tokens.layout).forEach(([key, value]) => {
    target.style.setProperty(tokenToCSSVarName('layout', key), String(value))
  })

  Object.entries(tokens.animation).forEach(([key, value]) => {
    target.style.setProperty(tokenToCSSVarName('animation', key), String(value))
  })
}

export function injectCustomCSS(css: string): void {
  if (css.trim().length === 0) {
    removeCustomCSS()
    return
  }

  const existing = document.getElementById(CUSTOM_CSS_ID)
  if (existing) {
    existing.textContent = css
    return
  }

  const style = document.createElement('style')
  style.id = CUSTOM_CSS_ID
  style.textContent = css
  document.head.appendChild(style)
}

export function removeCustomCSS(): void {
  const existing = document.getElementById(CUSTOM_CSS_ID)
  if (existing) {
    existing.remove()
  }
}

/**
 * 为指定组件注入自定义 CSS
 * 使用独立的 style 标签,CSS 经过 sanitize 处理
 * @param css - 要注入的 CSS 字符串
 * @param componentId - 组件标识符 (page/sidebar/header/card/dialog)
 */
export function injectComponentCSS(css: string, componentId: string): void {
  const styleId = `${COMPONENT_CSS_ID_PREFIX}${componentId}`

  if (css.trim().length === 0) {
    removeComponentCSS(componentId)
    return
  }

  const sanitized = sanitizeCSS(css)
  const sanitizedCss = sanitized.css

  if (sanitizedCss.trim().length === 0) {
    removeComponentCSS(componentId)
    return
  }

  const existing = document.getElementById(styleId)
  if (existing) {
    existing.textContent = sanitizedCss
    return
  }

  const style = document.createElement('style')
  style.id = styleId
  style.textContent = sanitizedCss
  document.head.appendChild(style)
}

/**
 * 移除指定组件的自定义 CSS
 */
export function removeComponentCSS(componentId: string): void {
  const styleId = `${COMPONENT_CSS_ID_PREFIX}${componentId}`
  document.getElementById(styleId)?.remove()
}

/**
 * 移除所有组件的自定义 CSS
 */
export function removeAllComponentCSS(): void {
  COMPONENT_IDS.forEach(removeComponentCSS)
}

export function applyThemePipeline(config: UserThemeConfig, isDark: boolean): void {
  const root = document.documentElement
  const tokens = buildTokens(config, isDark)
  injectTokensAsCSS(tokens, root)
  if (config.customCSS) {
    const sanitized = sanitizeCSS(config.customCSS)
    if (sanitized.css.trim().length > 0) {
      injectCustomCSS(sanitized.css)
    } else {
      removeCustomCSS()
    }
  } else {
    removeCustomCSS()
  }

  // 应用组件级 CSS(注入顺序在全局 CSS 之后)
  if (config.backgroundConfig) {
    const { page, sidebar, header, card, dialog } = config.backgroundConfig
    ;[
      ['page', page],
      ['sidebar', sidebar],
      ['header', header],
      ['card', card],
      ['dialog', dialog],
    ].forEach(([id, cfg]) => {
      if (cfg && typeof cfg === 'object' && 'customCSS' in cfg && cfg.customCSS) {
        injectComponentCSS(cfg.customCSS, id as string)
      } else {
        removeComponentCSS(id as string)
      }
    })
  } else {
    removeAllComponentCSS()
  }
}
