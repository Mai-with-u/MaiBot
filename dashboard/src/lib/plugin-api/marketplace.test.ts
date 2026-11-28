import { describe, expect, it } from 'vitest'

import { isPluginCompatibilityMode, isPluginCompatible } from './marketplace'
import type { MaimaiVersion } from './types'

function maimaiVersion(
  version: string,
  major: number,
  minor: number,
  patch: number
): MaimaiVersion {
  return {
    version,
    version_major: major,
    version_minor: minor,
    version_patch: patch,
  }
}

describe('isPluginCompatible', () => {
  it('允许仅修订号高于插件声明的最大版本，并标记为兼容模式', () => {
    const current = maimaiVersion('1.4.2', 1, 4, 2)
    expect(isPluginCompatible('1.0.0', '1.4.0', current)).toBe(true)
    expect(isPluginCompatibilityMode('1.0.0', '1.4.0', current)).toBe(true)
  })

  it('允许同主版本下次版本高 1 进入兼容模式', () => {
    const current = maimaiVersion('1.5.0', 1, 5, 0)
    expect(isPluginCompatible('1.0.0', '1.4.9', current)).toBe(true)
    expect(isPluginCompatibilityMode('1.0.0', '1.4.9', current)).toBe(true)
  })

  it('拒绝同主版本下次版本超出兼容窗口（高 2 及以上）', () => {
    const current = maimaiVersion('1.6.0', 1, 6, 0)
    expect(isPluginCompatible('1.0.0', '1.4.9', current)).toBe(false)
    expect(isPluginCompatibilityMode('1.0.0', '1.4.9', current)).toBe(false)
  })

  it('拒绝跨主版本高于插件声明的最大版本', () => {
    expect(isPluginCompatible('1.0.0', '1.9.9', maimaiVersion('2.0.0', 2, 0, 0))).toBe(false)
  })

  it('仍严格检查插件声明的最小版本', () => {
    expect(isPluginCompatible('1.5.0', '1.9.9', maimaiVersion('1.4.9', 1, 4, 9))).toBe(false)
  })

  it('在声明最大版本以内时不处于兼容模式', () => {
    const current = maimaiVersion('1.4.0', 1, 4, 0)
    expect(isPluginCompatible('1.0.0', '1.4.0', current)).toBe(true)
    expect(isPluginCompatibilityMode('1.0.0', '1.4.0', current)).toBe(false)
  })
})
