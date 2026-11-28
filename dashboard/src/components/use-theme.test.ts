// @vitest-environment node
import type { MouseEvent } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { isEdgeBrowser, toggleThemeWithTransition } from './use-theme'

const setUserAgent = (userAgent: string) => {
  vi.stubGlobal('navigator', { userAgent })
}

const setStartViewTransition = (startViewTransition?: Document['startViewTransition']) => {
  Object.defineProperty(document, 'startViewTransition', {
    configurable: true,
    value: startViewTransition,
  })
}

const createViewTransition = (): ViewTransition =>
  ({
    finished: Promise.resolve(),
    ready: Promise.resolve(),
    updateCallbackDone: Promise.resolve(),
    skipTransition: vi.fn(),
  }) as unknown as ViewTransition

describe('toggleThemeWithTransition', () => {
  beforeEach(() => {
    vi.stubGlobal('innerWidth', 120)
    vi.stubGlobal('innerHeight', 90)
    vi.stubGlobal('document', {
      documentElement: {
        animate: vi.fn(),
        classList: {
          contains: vi.fn(() => false),
          remove: vi.fn(),
        },
      },
    })
    setUserAgent(
      'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/125.0.0.0 Safari/537.36'
    )
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('识别 Edge 浏览器 UA', () => {
    expect(
      isEdgeBrowser(
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/125.0.0.0 Safari/537.36 Edg/125.0.0.0'
      )
    ).toBe(true)
    expect(
      isEdgeBrowser(
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/125.0.0.0 Safari/537.36'
      )
    ).toBe(false)
  })

  it('在 Edge 中直接切换主题，不启动 View Transition', () => {
    const setTheme = vi.fn()
    const startViewTransition = vi.fn((callback: () => void | Promise<void>) => {
      callback()
      return createViewTransition()
    })

    setUserAgent(
      'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/125.0.0.0 Safari/537.36 Edg/125.0.0.0'
    )
    setStartViewTransition(startViewTransition)

    toggleThemeWithTransition('dark', setTheme, { clientX: 10, clientY: 20 } as MouseEvent)

    expect(setTheme).toHaveBeenCalledWith('dark')
    expect(startViewTransition).not.toHaveBeenCalled()
  })

  it('系统偏好减少动态效果时直接切换主题，不启动 View Transition', () => {
    const setTheme = vi.fn()
    const startViewTransition = vi.fn((callback: () => void | Promise<void>) => {
      callback()
      return createViewTransition()
    })

    vi.stubGlobal('window', {
      matchMedia: vi.fn((query: string) => ({
        matches: query === '(prefers-reduced-motion: reduce)',
      })),
    })
    setStartViewTransition(startViewTransition)

    toggleThemeWithTransition('dark', setTheme, { clientX: 10, clientY: 20 } as MouseEvent)

    expect(setTheme).toHaveBeenCalledWith('dark')
    expect(startViewTransition).not.toHaveBeenCalled()
  })

  it('在非 Edge 且支持 API 时启动 View Transition', async () => {
    const setTheme = vi.fn()
    const animate = vi.fn()
    const startViewTransition = vi.fn((callback: () => void | Promise<void>) => {
      callback()
      return createViewTransition()
    })

    setUserAgent(
      'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/125.0.0.0 Safari/537.36'
    )
    setStartViewTransition(startViewTransition)
    Object.defineProperty(document.documentElement, 'animate', {
      configurable: true,
      value: animate,
    })

    toggleThemeWithTransition('light', setTheme, { clientX: 10, clientY: 20 } as MouseEvent)
    await Promise.resolve()

    expect(startViewTransition).toHaveBeenCalledTimes(1)
    expect(setTheme).toHaveBeenCalledWith('light')
    expect(animate).toHaveBeenCalledWith(
      {
        clipPath: expect.arrayContaining([
          'circle(0px at 10px 20px)',
          expect.stringContaining('circle('),
        ]),
      },
      expect.objectContaining({
        pseudoElement: '::view-transition-new(root)',
      })
    )
  })
})
