/**
 * 千禧风格的像素字体选择：英文/数字与中文各选一种，保存在 localStorage，
 * 并通过 html[data-millennium-font]、html[data-millennium-cjk-font] 切换 CSS 字体栈。
 */

export const MILLENNIUM_FONT_STORAGE_KEY = 'maibot-theme-millennium-font'
export const MILLENNIUM_CJK_FONT_STORAGE_KEY = 'maibot-theme-millennium-cjk-font'

export const MILLENNIUM_FONTS = ['departure', 'jersey'] as const
export const MILLENNIUM_CJK_FONTS = ['default', 'ark'] as const

export type MillenniumFont = (typeof MILLENNIUM_FONTS)[number]
export type MillenniumCjkFont = (typeof MILLENNIUM_CJK_FONTS)[number]

export const DEFAULT_MILLENNIUM_FONT: MillenniumFont = 'departure'
export const DEFAULT_MILLENNIUM_CJK_FONT: MillenniumCjkFont = 'default'

function loadChoice<T extends string>(key: string, choices: readonly T[], fallback: T): T {
  try {
    const stored = localStorage.getItem(key)
    return choices.find((choice) => choice === stored) ?? fallback
  } catch {
    return fallback
  }
}

function saveChoice(key: string, value: string): void {
  try {
    localStorage.setItem(key, value)
  } catch {
    // localStorage 不可用时只在本次会话内生效
  }
}

export function loadMillenniumFont(): MillenniumFont {
  // Pixelify Sans 的数字不好认，已换成 Jersey 20；之前选过它的用户直接沿用到新字体。
  try {
    if (localStorage.getItem(MILLENNIUM_FONT_STORAGE_KEY) === 'pixelify') return 'jersey'
  } catch {
    // localStorage 不可用时走下面的默认值
  }
  return loadChoice(MILLENNIUM_FONT_STORAGE_KEY, MILLENNIUM_FONTS, DEFAULT_MILLENNIUM_FONT)
}

export function loadMillenniumCjkFont(): MillenniumCjkFont {
  return loadChoice(
    MILLENNIUM_CJK_FONT_STORAGE_KEY,
    MILLENNIUM_CJK_FONTS,
    DEFAULT_MILLENNIUM_CJK_FONT
  )
}

export function applyMillenniumFonts(font: MillenniumFont, cjkFont: MillenniumCjkFont): void {
  const root = document.documentElement
  root.dataset.millenniumFont = font
  root.dataset.millenniumCjkFont = cjkFont
}

export function saveMillenniumFont(font: MillenniumFont): void {
  saveChoice(MILLENNIUM_FONT_STORAGE_KEY, font)
  document.documentElement.dataset.millenniumFont = font
}

export function saveMillenniumCjkFont(cjkFont: MillenniumCjkFont): void {
  saveChoice(MILLENNIUM_CJK_FONT_STORAGE_KEY, cjkFont)
  document.documentElement.dataset.millenniumCjkFont = cjkFont
}
