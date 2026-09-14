import { afterEach, describe, expect, it, vi } from 'vitest'

import { APP_FULL_NAME, APP_NAME, APP_VERSION, formatVersion, getVersionInfo } from '../version'

afterEach(() => {
  vi.unstubAllEnvs()
})

describe('version', () => {
  it('导出应用名与由 __APP_VERSION__ 拼出的全名', () => {
    expect(APP_NAME).toBe('MaiBot Dashboard')
    expect(APP_VERSION).toEqual(expect.any(String))
    expect(APP_VERSION.length).toBeGreaterThan(0)
    expect(APP_FULL_NAME).toBe(`${APP_NAME} ${APP_VERSION}`)
  })

  it('getVersionInfo 在设置了 VITE_BUILD_DATE 时使用该日期', () => {
    vi.stubEnv('VITE_BUILD_DATE', '2026-03-01')

    expect(getVersionInfo()).toEqual({
      version: APP_VERSION,
      name: APP_NAME,
      fullName: APP_FULL_NAME,
      buildDate: '2026-03-01',
      buildEnv: import.meta.env.MODE,
    })
  })

  it('getVersionInfo 在未设置 VITE_BUILD_DATE 时回落到当天日期', () => {
    vi.stubEnv('VITE_BUILD_DATE', '')

    expect(getVersionInfo()).toEqual({
      version: APP_VERSION,
      name: APP_NAME,
      fullName: APP_FULL_NAME,
      buildDate: new Date().toISOString().split('T')[0],
      buildEnv: import.meta.env.MODE,
    })
  })

  it('formatVersion 默认前缀为 v，也可传入自定义前缀', () => {
    expect(formatVersion()).toBe(`v${APP_VERSION}`)
    expect(formatVersion('')).toBe(APP_VERSION)
    expect(formatVersion('版本 ')).toBe(`版本 ${APP_VERSION}`)
  })
})
