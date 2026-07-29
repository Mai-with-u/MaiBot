/**
 * 主题配置的 localStorage 存储管理模块。
 */

import { DEFAULT_ACCENT_COLOR_HSL, normalizeAccentColor } from './palette'
import {
  DEFAULT_DASHBOARD_STYLE,
  DEFAULT_DASHBOARD_STYLE_CONFIG,
  DEFAULT_FUTURE_RETRO_STYLE_CONFIG,
} from './tokens'
import type {
  BackgroundConfigMap,
  DashboardStyle,
  DashboardStyleConfig,
  StyleBackgroundConfigMap,
  StyleCustomCSS,
  StyleTokenOverrides,
  UserThemeConfig,
} from './tokens'

export const THEME_STORAGE_KEYS = {
  MODE: 'maibot-theme-mode',
  PRESET: 'maibot-theme-preset',
  ACCENT: 'maibot-theme-accent',
  STYLE_OVERRIDES: 'maibot-theme-style-overrides',
  STYLE_CUSTOM_CSS: 'maibot-theme-style-custom-css',
  STYLE_BACKGROUND_CONFIG: 'maibot-theme-style-background',
  DASHBOARD_STYLE: 'maibot-theme-dashboard-style',
  STYLE_CONFIG: 'maibot-theme-style-config',
} as const

const DEFAULT_THEME_CONFIG: UserThemeConfig = {
  selectedPreset: 'light',
  accentColor: DEFAULT_ACCENT_COLOR_HSL,
  styleTokenOverrides: {},
  styleCustomCSS: {},
  styleBackgroundConfig: {},
  dashboardStyle: DEFAULT_DASHBOARD_STYLE,
  styleConfig: DEFAULT_DASHBOARD_STYLE_CONFIG,
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null

type ImportThemeConfigRecord = Record<string, unknown> &
  Pick<UserThemeConfig, 'selectedPreset' | 'accentColor'>

function hasRequiredImportThemeFields(
  config: Record<string, unknown>,
  errors: string[]
): config is ImportThemeConfigRecord {
  let valid = true

  if (typeof config.selectedPreset !== 'string') {
    errors.push('selectedPreset must be a string')
    valid = false
  }
  if (typeof config.accentColor !== 'string') {
    errors.push('accentColor must be a string')
    valid = false
  }

  return valid
}

function normalizeDashboardStyle(value: unknown): DashboardStyle {
  if (value === 'modern' || value === 'future-retro') {
    return value
  }

  return DEFAULT_DASHBOARD_STYLE
}

function normalizeStyleTokenOverrides(value: unknown): StyleTokenOverrides {
  if (!isRecord(value)) {
    return {}
  }

  const nextOverrides: StyleTokenOverrides = {}
  for (const style of ['modern', 'future-retro'] as const) {
    if (isRecord(value[style])) {
      nextOverrides[style] = value[style] as StyleTokenOverrides[typeof style]
    }
  }

  return nextOverrides
}

function normalizeStyleCustomCSS(value: unknown): StyleCustomCSS {
  if (!isRecord(value)) {
    return {}
  }

  const nextCustomCSS: StyleCustomCSS = {}
  if (typeof value.modern === 'string') {
    nextCustomCSS.modern = value.modern
  }

  return nextCustomCSS
}

function normalizeStyleBackgroundConfig(value: unknown): StyleBackgroundConfigMap {
  if (!isRecord(value)) {
    return {}
  }

  const nextBackgroundConfig: StyleBackgroundConfigMap = {}
  for (const style of ['modern', 'future-retro'] as const) {
    if (isRecord(value[style])) {
      nextBackgroundConfig[style] = value[style] as BackgroundConfigMap
    }
  }

  return nextBackgroundConfig
}

function normalizeStyleConfig(value: unknown): DashboardStyleConfig {
  const config = isRecord(value) ? value : {}
  const futureRetro = isRecord(config.futureRetro) ? config.futureRetro : {}
  const variant =
    futureRetro.variant === 'classic-signal' || futureRetro.variant === 'paper-console'
      ? futureRetro.variant
      : DEFAULT_FUTURE_RETRO_STYLE_CONFIG.variant

  return {
    futureRetro: {
      focusHighlight:
        typeof futureRetro.focusHighlight === 'boolean'
          ? futureRetro.focusHighlight
          : DEFAULT_FUTURE_RETRO_STYLE_CONFIG.focusHighlight,
      paperTexture:
        typeof futureRetro.paperTexture === 'boolean'
          ? futureRetro.paperTexture
          : DEFAULT_FUTURE_RETRO_STYLE_CONFIG.paperTexture,
      variant,
    },
  }
}

function parseJSONStorage(key: string): unknown {
  const value = localStorage.getItem(key)
  if (!value) {
    return undefined
  }

  return JSON.parse(value)
}

export function loadThemeConfig(): UserThemeConfig {
  const preset = localStorage.getItem(THEME_STORAGE_KEYS.PRESET)
  const accent = localStorage.getItem(THEME_STORAGE_KEYS.ACCENT)
  const dashboardStyle = localStorage.getItem(THEME_STORAGE_KEYS.DASHBOARD_STYLE)

  let styleTokenOverrides: StyleTokenOverrides = {}
  let styleCustomCSS: StyleCustomCSS = {}
  let styleBackgroundConfig: StyleBackgroundConfigMap = {}
  let styleConfig: DashboardStyleConfig = DEFAULT_THEME_CONFIG.styleConfig

  try {
    styleTokenOverrides = normalizeStyleTokenOverrides(
      parseJSONStorage(THEME_STORAGE_KEYS.STYLE_OVERRIDES)
    )
  } catch {
    styleTokenOverrides = {}
  }

  try {
    styleCustomCSS = normalizeStyleCustomCSS(parseJSONStorage(THEME_STORAGE_KEYS.STYLE_CUSTOM_CSS))
  } catch {
    styleCustomCSS = {}
  }

  try {
    styleBackgroundConfig = normalizeStyleBackgroundConfig(
      parseJSONStorage(THEME_STORAGE_KEYS.STYLE_BACKGROUND_CONFIG)
    )
  } catch {
    styleBackgroundConfig = {}
  }

  try {
    styleConfig = normalizeStyleConfig(parseJSONStorage(THEME_STORAGE_KEYS.STYLE_CONFIG))
  } catch {
    styleConfig = DEFAULT_THEME_CONFIG.styleConfig
  }

  return {
    selectedPreset: preset || DEFAULT_THEME_CONFIG.selectedPreset,
    accentColor: normalizeAccentColor(accent),
    styleTokenOverrides,
    styleCustomCSS,
    styleBackgroundConfig,
    dashboardStyle: normalizeDashboardStyle(dashboardStyle),
    styleConfig,
  }
}

export function saveThemeConfig(config: UserThemeConfig): void {
  localStorage.setItem(THEME_STORAGE_KEYS.PRESET, config.selectedPreset)
  localStorage.setItem(THEME_STORAGE_KEYS.ACCENT, normalizeAccentColor(config.accentColor))
  localStorage.setItem(
    THEME_STORAGE_KEYS.STYLE_OVERRIDES,
    JSON.stringify(normalizeStyleTokenOverrides(config.styleTokenOverrides))
  )
  localStorage.setItem(
    THEME_STORAGE_KEYS.STYLE_CUSTOM_CSS,
    JSON.stringify(normalizeStyleCustomCSS(config.styleCustomCSS))
  )
  localStorage.setItem(
    THEME_STORAGE_KEYS.STYLE_BACKGROUND_CONFIG,
    JSON.stringify(normalizeStyleBackgroundConfig(config.styleBackgroundConfig))
  )
  localStorage.setItem(
    THEME_STORAGE_KEYS.DASHBOARD_STYLE,
    normalizeDashboardStyle(config.dashboardStyle)
  )
  localStorage.setItem(
    THEME_STORAGE_KEYS.STYLE_CONFIG,
    JSON.stringify(normalizeStyleConfig(config.styleConfig))
  )
}

export function saveThemePartial(partial: Partial<UserThemeConfig>): void {
  const current = loadThemeConfig()
  saveThemeConfig({
    ...current,
    ...partial,
  })
}

export function exportThemeJSON(): string {
  const config = loadThemeConfig()
  return JSON.stringify(config, null, 2)
}

export function importThemeJSON(json: string): { success: boolean; errors: string[] } {
  const errors: string[] = []

  let config: unknown
  try {
    config = JSON.parse(json)
  } catch (error) {
    return {
      success: false,
      errors: [`Invalid JSON format: ${error instanceof Error ? error.message : 'Unknown error'}`],
    }
  }

  if (!isRecord(config)) {
    return {
      success: false,
      errors: ['Configuration must be a JSON object'],
    }
  }

  const hasRequiredFields = hasRequiredImportThemeFields(config, errors)
  if (
    config.styleTokenOverrides !== undefined &&
    (typeof config.styleTokenOverrides !== 'object' || config.styleTokenOverrides === null)
  ) {
    errors.push('styleTokenOverrides must be an object')
  }
  if (
    config.styleCustomCSS !== undefined &&
    (typeof config.styleCustomCSS !== 'object' || config.styleCustomCSS === null)
  ) {
    errors.push('styleCustomCSS must be an object')
  }
  if (
    config.styleBackgroundConfig !== undefined &&
    (typeof config.styleBackgroundConfig !== 'object' || config.styleBackgroundConfig === null)
  ) {
    errors.push('styleBackgroundConfig must be an object')
  }
  if (config.dashboardStyle !== undefined && typeof config.dashboardStyle !== 'string') {
    errors.push('dashboardStyle must be a string')
  }
  if (
    config.styleConfig !== undefined &&
    (typeof config.styleConfig !== 'object' || config.styleConfig === null)
  ) {
    errors.push('styleConfig must be an object')
  }

  if (errors.length > 0) {
    return { success: false, errors }
  }
  const selectedPreset = config.selectedPreset
  const accentColor = config.accentColor
  if (!hasRequiredFields || typeof selectedPreset !== 'string' || typeof accentColor !== 'string') {
    throw new Error('Theme import required fields were not narrowed after validation')
  }

  saveThemeConfig({
    selectedPreset,
    accentColor,
    styleTokenOverrides: normalizeStyleTokenOverrides(config.styleTokenOverrides),
    styleCustomCSS: normalizeStyleCustomCSS(config.styleCustomCSS),
    styleBackgroundConfig: normalizeStyleBackgroundConfig(config.styleBackgroundConfig),
    dashboardStyle: normalizeDashboardStyle(config.dashboardStyle),
    styleConfig: normalizeStyleConfig(config.styleConfig),
  })

  return { success: true, errors: [] }
}

export function resetThemeToDefault(): void {
  Object.values(THEME_STORAGE_KEYS).forEach((key) => {
    localStorage.removeItem(key)
  })
}
