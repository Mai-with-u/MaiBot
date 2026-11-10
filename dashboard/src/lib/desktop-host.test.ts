import { afterEach, describe, expect, it, vi } from 'vitest'

import { applyDesktopEmbedding, isDesktopEmbedded, reportDesktopLayout, subscribeDesktopSidebar } from './desktop-host'

afterEach(() => {
  delete window.maibotDesktopHost
  delete document.documentElement.dataset.desktopEmbedded
  delete document.documentElement.dataset.desktopSidebarControl
})

describe('desktop-host', () => {
  it('浏览器里直接打开时不进入嵌入模式', () => {
    applyDesktopEmbedding()
    expect(isDesktopEmbedded()).toBe(false)
    expect(document.documentElement.dataset.desktopEmbedded).toBeUndefined()
    expect(() => reportDesktopLayout({ sidebarWidth: 300, collapsed: false, homeLocked: false })).not.toThrow()
  })

  it('旧版一键包只提供 openMessagePlatform 时照常显示', () => {
    window.maibotDesktopHost = { openMessagePlatform: vi.fn() }
    applyDesktopEmbedding()
    expect(document.documentElement.dataset.desktopEmbedded).toBeUndefined()
  })

  it('嵌入时打标记并把侧栏宽度交给外壳', () => {
    const reportLayout = vi.fn()
    window.maibotDesktopHost = { openMessagePlatform: vi.fn(), embedded: true, reportLayout }
    applyDesktopEmbedding()
    expect(document.documentElement.dataset.desktopEmbedded).toBe('true')
    reportDesktopLayout({ sidebarWidth: 312, collapsed: false, homeLocked: true })
    expect(reportLayout).toHaveBeenCalledWith({ sidebarWidth: 312, collapsed: false, homeLocked: true })
  })

  it('外壳通道抛错时不影响 WebUI', () => {
    window.maibotDesktopHost = {
      openMessagePlatform: vi.fn(),
      embedded: true,
      reportLayout: () => {
        throw new Error('closed')
      },
    }
    expect(() => reportDesktopLayout({ sidebarWidth: 0, collapsed: true, homeLocked: true })).not.toThrow()
  })

  it('外壳代管折叠键时收起铭牌行，并把折叠指令交给侧栏', () => {
    let send: ((command: { collapsed: boolean }) => void) | undefined
    const off = vi.fn()
    window.maibotDesktopHost = {
      openMessagePlatform: vi.fn(),
      embedded: true,
      onSidebarCommand: (listener) => {
        send = listener
        return off
      },
    }
    applyDesktopEmbedding()
    expect(document.documentElement.dataset.desktopSidebarControl).toBe('true')
    const listener = vi.fn()
    const unsubscribe = subscribeDesktopSidebar(listener)
    send?.({ collapsed: true })
    expect(listener).toHaveBeenCalledWith(true)
    unsubscribe()
    expect(off).toHaveBeenCalled()
  })

  it('旧版外壳没有折叠指令时保留铭牌行', () => {
    window.maibotDesktopHost = { openMessagePlatform: vi.fn(), embedded: true }
    applyDesktopEmbedding()
    expect(document.documentElement.dataset.desktopSidebarControl).toBeUndefined()
    expect(subscribeDesktopSidebar(vi.fn())).toBeTypeOf('function')
  })
})
