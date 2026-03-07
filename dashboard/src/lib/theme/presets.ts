/**
 * Theme Presets 定义
 * 提供内置的亮色和暗色主题预设
 */

import {
	defaultDarkTokens,
	defaultLightTokens,
} from './tokens'
import type { ThemePreset } from './tokens'

// ============================================================================
// Default Light Preset
// ============================================================================

export const defaultLightPreset: ThemePreset = {
	id: 'light',
	name: '默认亮色',
	description: '默认亮色主题',
	tokens: defaultLightTokens,
	isDark: false,
}

// ============================================================================
// Default Dark Preset
// ============================================================================

export const defaultDarkPreset: ThemePreset = {
	id: 'dark',
	name: '默认暗色',
	description: '默认暗色主题',
	tokens: defaultDarkTokens,
	isDark: true,
}

// ============================================================================
// Built-in Presets Collection
// ============================================================================

export const builtInPresets: ThemePreset[] = [
	defaultLightPreset,
	defaultDarkPreset,
]

// ============================================================================
// Default Preset ID
// ============================================================================

export const DEFAULT_PRESET_ID = 'light'

// ============================================================================
// Preset Utility Functions
// ============================================================================

/**
 * 根据 ID 获取预设
 * @param id - 预设 ID
 * @returns 对应的预设，如果不存在则返回 undefined
 */
export function getPresetById(id: string): ThemePreset | undefined {
	return builtInPresets.find((preset) => preset.id === id)
}
