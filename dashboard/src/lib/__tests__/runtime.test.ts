import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import type { ElectronAPI } from '@/types/electron'

import { getPlatform, getRuntime, isElectron, type RuntimeInfo } from '../runtime'

function electronRuntime(overrides: Partial<RuntimeInfo> = {}): RuntimeInfo {
  return {
    kind: 'electron',
    source: 'tag',
    versions: { electron: '28.0.0' },
    ...overrides,
  }
}

beforeEach(() => {
  delete globalThis.__RUNTIME__
  delete window.electronAPI
})

afterEach(() => {
  delete globalThis.__RUNTIME__
  delete window.electronAPI
})

describe('getRuntime', () => {
  it('存在 globalThis.__RUNTIME__ 时原样返回该对象', () => {
    const runtime = electronRuntime()
    globalThis.__RUNTIME__ = runtime

    expect(getRuntime()).toBe(runtime)
  })

  it('未注入 __RUNTIME__ 时回落为浏览器 runtime', () => {
    const runtime = getRuntime()

    expect(runtime).toEqual({
      kind: 'browser',
      userAgent: navigator.userAgent,
      source: 'fallback',
    })
  })
})

describe('isElectron', () => {
  it('runtime.kind 为 electron 时返回 true', () => {
    globalThis.__RUNTIME__ = electronRuntime()
    expect(isElectron()).toBe(true)
  })

  it('浏览器回落时返回 false', () => {
    expect(isElectron()).toBe(false)
  })
})

describe('getPlatform', () => {
  it('浏览器环境返回 browser', () => {
    expect(getPlatform()).toBe('browser')
  })

  it('Electron 且 electronAPI.getPlatform 可用时返回真实平台', () => {
    globalThis.__RUNTIME__ = electronRuntime()
    window.electronAPI = { getPlatform: () => 'darwin' } as unknown as ElectronAPI

    expect(getPlatform()).toBe('darwin')
  })

  it('Electron 但未注入 electronAPI 时返回 unknown', () => {
    globalThis.__RUNTIME__ = electronRuntime()
    delete window.electronAPI

    expect(getPlatform()).toBe('unknown')
  })

  it('Electron 已注入 electronAPI 但缺少 getPlatform 时返回 unknown', () => {
    globalThis.__RUNTIME__ = electronRuntime()
    window.electronAPI = {} as unknown as ElectronAPI

    expect(getPlatform()).toBe('unknown')
  })
})
