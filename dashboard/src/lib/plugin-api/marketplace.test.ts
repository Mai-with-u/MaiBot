import { describe, expect, it } from 'vitest'

import { isPluginCompatible } from './marketplace'
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
  it('允许仅修订号高于插件声明的最大版本', () => {
    expect(isPluginCompatible('1.0.0', '1.4.0', maimaiVersion('1.4.2', 1, 4, 2))).toBe(true)
  })

  it('允许同主版本内比上限高一个次版本的兼容模式', () => {
    expect(isPluginCompatible('1.0.0', '1.4.9', maimaiVersion('1.5.0', 1, 5, 0))).toBe(true)
  })

  it('拒绝比上限高两个次版本', () => {
    expect(isPluginCompatible('1.0.0', '1.4.9', maimaiVersion('1.6.0', 1, 6, 0))).toBe(false)
  })

  it('拒绝跨主版本高于插件声明的最大版本', () => {
    expect(isPluginCompatible('1.0.0', '1.9.9', maimaiVersion('2.0.0', 2, 0, 0))).toBe(false)
  })

  it('仍严格检查插件声明的最小版本', () => {
    expect(isPluginCompatible('1.5.0', '1.9.9', maimaiVersion('1.4.9', 1, 4, 9))).toBe(false)
  })
})
