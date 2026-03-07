/**
 * 主题配置的 localStorage 存储管理模块
 * 统一处理主题相关的存储操作，包括加载、保存、导出、导入和迁移旧 key
 */

import type { BackgroundConfigMap, UserThemeConfig } from './tokens'

/**
 * 主题存储 key 定义
 * 统一使用 'maibot-theme-*' 前缀，替代现有的 'ui-theme'、'maibot-ui-theme' 和 'accent-color'
 */
export const THEME_STORAGE_KEYS = {
  MODE: 'maibot-theme-mode',
  PRESET: 'maibot-theme-preset',
  ACCENT: 'maibot-theme-accent',
  OVERRIDES: 'maibot-theme-overrides',
  CUSTOM_CSS: 'maibot-theme-custom-css',
  BACKGROUND_CONFIG: 'maibot-theme-background',
} as const

/**
 * 默认主题配置
 */
const DEFAULT_THEME_CONFIG: UserThemeConfig = {
  selectedPreset: 'light',
  accentColor: 'blue',
  tokenOverrides: {},
  customCSS: '',
  backgroundConfig: {} as BackgroundConfigMap,
}

/**
 * 从 localStorage 加载完整主题配置
 * 缺失值使用合理默认值
 *
 * @returns 加载的主题配置对象
 */
export function loadThemeConfig(): UserThemeConfig {
  const preset = localStorage.getItem(THEME_STORAGE_KEYS.PRESET)
  const accent = localStorage.getItem(THEME_STORAGE_KEYS.ACCENT)
  const overridesStr = localStorage.getItem(THEME_STORAGE_KEYS.OVERRIDES)
  const customCSS = localStorage.getItem(THEME_STORAGE_KEYS.CUSTOM_CSS)

  // 解析 tokenOverrides JSON
  let tokenOverrides = {}
  if (overridesStr) {
    try {
      tokenOverrides = JSON.parse(overridesStr)
    } catch {
      // JSON 解析失败，使用空对象
      tokenOverrides = {}
    }
  }

  // 加载 backgroundConfig
  const backgroundConfigStr = localStorage.getItem(THEME_STORAGE_KEYS.BACKGROUND_CONFIG)
  let backgroundConfig: BackgroundConfigMap = {}
  if (backgroundConfigStr) {
    try {
      backgroundConfig = JSON.parse(backgroundConfigStr)
    } catch {
      backgroundConfig = {}
    }
  }

  return {
    selectedPreset: preset || DEFAULT_THEME_CONFIG.selectedPreset,
    accentColor: accent || DEFAULT_THEME_CONFIG.accentColor,
    tokenOverrides,
    customCSS: customCSS || DEFAULT_THEME_CONFIG.customCSS,
    backgroundConfig,
  }
}

/**
 * 保存完整主题配置到 localStorage
 *
 * @param config - 要保存的主题配置
 */
export function saveThemeConfig(config: UserThemeConfig): void {
  localStorage.setItem(THEME_STORAGE_KEYS.PRESET, config.selectedPreset)
  localStorage.setItem(THEME_STORAGE_KEYS.ACCENT, config.accentColor)
  localStorage.setItem(THEME_STORAGE_KEYS.OVERRIDES, JSON.stringify(config.tokenOverrides))
  localStorage.setItem(THEME_STORAGE_KEYS.CUSTOM_CSS, config.customCSS)
  if (config.backgroundConfig) {
    localStorage.setItem(THEME_STORAGE_KEYS.BACKGROUND_CONFIG, JSON.stringify(config.backgroundConfig))
  } else {
    localStorage.removeItem(THEME_STORAGE_KEYS.BACKGROUND_CONFIG)
  }
}

/**
 * 部分更新主题配置
 * 先加载现有配置，合并部分更新，再保存
 *
 * @param partial - 部分主题配置更新
 */
export function saveThemePartial(partial: Partial<UserThemeConfig>): void {
  const current = loadThemeConfig()
  const updated: UserThemeConfig = {
    ...current,
    ...partial,
  }
  saveThemeConfig(updated)
}

/**
 * 导出主题配置为美化格式的 JSON 字符串
 *
 * @returns 格式化的 JSON 字符串
 */
export function exportThemeJSON(): string {
  const config = loadThemeConfig()
  return JSON.stringify(config, null, 2)
}

/**
 * 从 JSON 字符串导入主题配置
 * 包含基础的格式和字段校验
 *
 * @param json - JSON 字符串
 * @returns 导入结果，包含成功状态和错误列表
 */
export function importThemeJSON(
  json: string,
): { success: boolean; errors: string[] } {
  const errors: string[] = []

  // JSON 格式校验
  let config: unknown
  try {
    config = JSON.parse(json)
  } catch (error) {
    return {
      success: false,
      errors: [`Invalid JSON format: ${error instanceof Error ? error.message : 'Unknown error'}`],
    }
  }

  // 基本对象类型校验
  if (typeof config !== 'object' || config === null) {
    return {
      success: false,
      errors: ['Configuration must be a JSON object'],
    }
  }

  const configObj = config as Record<string, unknown>

  // 必要字段存在性校验
  if (typeof configObj.selectedPreset !== 'string') {
    errors.push('selectedPreset must be a string')
  }
  if (typeof configObj.accentColor !== 'string') {
    errors.push('accentColor must be a string')
  }
  if (typeof configObj.customCSS !== 'string') {
    errors.push('customCSS must be a string')
  }
  if (configObj.tokenOverrides !== undefined && typeof configObj.tokenOverrides !== 'object') {
    errors.push('tokenOverrides must be an object')
  }

  if (errors.length > 0) {
    return { success: false, errors }
  }

  // 校验通过，保存配置
  const validConfig: UserThemeConfig = {
    selectedPreset: configObj.selectedPreset as string,
    accentColor: configObj.accentColor as string,
    tokenOverrides: (configObj.tokenOverrides as Partial<any>) || {},
    customCSS: configObj.customCSS as string,
    backgroundConfig: (configObj.backgroundConfig as BackgroundConfigMap) ?? {},
  }

  saveThemeConfig(validConfig)
  return { success: true, errors: [] }
}

/**
 * 重置主题配置为默认值
 * 删除所有 THEME_STORAGE_KEYS 对应的 localStorage 项
 */
export function resetThemeToDefault(): void {
  Object.values(THEME_STORAGE_KEYS).forEach((key) => {
    localStorage.removeItem(key)
  })
}

/**
 * 迁移旧的 localStorage key 到新 key
 * 处理：
 * - 'ui-theme' 或 'maibot-ui-theme' → 'maibot-theme-mode'
 * - 'accent-color' → 'maibot-theme-accent'
 * 迁移完成后删除旧 key，避免重复迁移
 */
export function migrateOldKeys(): void {
  // 迁移主题模式
  // 优先使用 'ui-theme'（因为 ThemeProvider 默认使用它）
  const uiTheme = localStorage.getItem('ui-theme')
  const maiTheme = localStorage.getItem('maibot-ui-theme')
  const newMode = localStorage.getItem(THEME_STORAGE_KEYS.MODE)

  if (!newMode) {
    if (uiTheme) {
      localStorage.setItem(THEME_STORAGE_KEYS.MODE, uiTheme)
    } else if (maiTheme) {
      localStorage.setItem(THEME_STORAGE_KEYS.MODE, maiTheme)
    }
  }

  // 迁移强调色
  const accentColor = localStorage.getItem('accent-color')
  const newAccent = localStorage.getItem(THEME_STORAGE_KEYS.ACCENT)

  if (accentColor && !newAccent) {
    localStorage.setItem(THEME_STORAGE_KEYS.ACCENT, accentColor)
  }

  // 删除旧 key
  localStorage.removeItem('ui-theme')
  localStorage.removeItem('maibot-ui-theme')
  localStorage.removeItem('accent-color')
}
