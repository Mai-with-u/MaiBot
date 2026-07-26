/**
 * theme/presets 单元测试：验证内置主题预设与查询工具。
 */
import { describe, expect, it } from 'vitest'

import {
  DEFAULT_PRESET_ID,
  builtInPresets,
  defaultDarkPreset,
  defaultLightPreset,
  getPresetById,
} from '../presets'
import { defaultDarkTokens, defaultLightTokens } from '../tokens'

describe('内置预设定义', () => {
  it('内置预设按亮色在前、暗色在后的顺序排列', () => {
    expect(builtInPresets.map((preset) => preset.id)).toEqual(['light', 'dark'])
  })

  it('默认亮色预设绑定亮色令牌且标记为非暗色', () => {
    expect(defaultLightPreset.id).toBe('light')
    expect(defaultLightPreset.name).toBe('默认亮色')
    expect(defaultLightPreset.isDark).toBe(false)
    expect(defaultLightPreset.tokens).toBe(defaultLightTokens)
  })

  it('默认暗色预设绑定暗色令牌且标记为暗色', () => {
    expect(defaultDarkPreset.id).toBe('dark')
    expect(defaultDarkPreset.name).toBe('默认暗色')
    expect(defaultDarkPreset.isDark).toBe(true)
    expect(defaultDarkPreset.tokens).toBe(defaultDarkTokens)
  })

  it('默认预设 ID 为亮色预设', () => {
    expect(DEFAULT_PRESET_ID).toBe('light')
  })
})

describe('getPresetById', () => {
  it('按 ID 命中时返回对应预设实例', () => {
    expect(getPresetById('light')).toBe(defaultLightPreset)
    expect(getPresetById('dark')).toBe(defaultDarkPreset)
  })

  it('未知 ID 返回 undefined', () => {
    expect(getPresetById('neon')).toBeUndefined()
  })
})
