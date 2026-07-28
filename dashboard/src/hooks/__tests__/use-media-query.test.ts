import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { useIsMobile, useMediaQuery } from '../use-media-query'

type ChangeListener = (event: MediaQueryListEvent) => void

/**
 * 可手动派发 change 事件的 matchMedia 桩。
 * 全局 setup 里的 matchMedia 桩恒返回 matches=false 且不支持触发事件，
 * 这里换成可控实现以覆盖订阅 / 退订 / 状态变更路径。
 */
class FakeMediaQueryList {
  matches: boolean
  readonly media: string
  private readonly listeners = new Set<ChangeListener>()

  constructor(media: string, matches: boolean) {
    this.media = media
    this.matches = matches
  }

  addEventListener(type: string, listener: ChangeListener): void {
    if (type === 'change') this.listeners.add(listener)
  }

  removeEventListener(type: string, listener: ChangeListener): void {
    if (type === 'change') this.listeners.delete(listener)
  }

  /** 模拟媒体查询匹配状态变化并通知所有监听者 */
  emitChange(matches: boolean): void {
    this.matches = matches
    for (const listener of this.listeners) {
      listener({ matches, media: this.media } as MediaQueryListEvent)
    }
  }

  /** 当前 change 监听者数量，用于断言订阅 / 退订行为 */
  get listenerCount(): number {
    return this.listeners.size
  }
}

// 按 query 缓存的 FakeMediaQueryList 注册表，测试可以借它拿到桩实例触发事件
const mqlRegistry = new Map<string, FakeMediaQueryList>()
// 每个 query 首次创建时的初始匹配状态，测试在 renderHook 前设置
const initialMatchesByQuery = new Map<string, boolean>()

const originalMatchMedia = window.matchMedia

/** 取出指定 query 对应的桩实例（不存在则视为测试编写错误直接抛出） */
function getMql(query: string): FakeMediaQueryList {
  const mql = mqlRegistry.get(query)
  if (!mql) throw new Error(`测试桩中不存在 query: ${query}`)
  return mql
}

beforeEach(() => {
  mqlRegistry.clear()
  initialMatchesByQuery.clear()
  window.matchMedia = ((query: string) => {
    let mql = mqlRegistry.get(query)
    if (!mql) {
      mql = new FakeMediaQueryList(query, initialMatchesByQuery.get(query) ?? false)
      mqlRegistry.set(query, mql)
    }
    return mql as unknown as MediaQueryList
  }) as typeof window.matchMedia
})

afterEach(() => {
  window.matchMedia = originalMatchMedia
})

describe('useMediaQuery', () => {
  it('初始匹配状态读取自 matchMedia', () => {
    initialMatchesByQuery.set('(min-width: 1024px)', true)
    const { result } = renderHook(() => useMediaQuery('(min-width: 1024px)'))
    expect(result.current).toBe(true)
  })

  it('初始不匹配时返回 false', () => {
    const { result } = renderHook(() => useMediaQuery('(prefers-reduced-motion: reduce)'))
    expect(result.current).toBe(false)
  })

  it('change 事件触发后同步更新匹配状态', () => {
    const query = '(max-width: 600px)'
    const { result } = renderHook(() => useMediaQuery(query))
    expect(result.current).toBe(false)

    act(() => {
      getMql(query).emitChange(true)
    })
    expect(result.current).toBe(true)

    act(() => {
      getMql(query).emitChange(false)
    })
    expect(result.current).toBe(false)
  })

  it('卸载时移除 change 监听', () => {
    const query = '(orientation: landscape)'
    const { unmount } = renderHook(() => useMediaQuery(query))
    expect(getMql(query).listenerCount).toBe(1)

    unmount()
    expect(getMql(query).listenerCount).toBe(0)
  })

  it('query 变化时退订旧查询并订阅新查询', () => {
    const queryA = '(min-width: 640px)'
    const queryB = '(min-width: 1280px)'
    initialMatchesByQuery.set(queryB, true)

    const { result, rerender } = renderHook(({ query }) => useMediaQuery(query), {
      initialProps: { query: queryA },
    })
    expect(result.current).toBe(false)
    expect(getMql(queryA).listenerCount).toBe(1)

    rerender({ query: queryB })
    // 旧查询退订、新查询订阅，且状态切换为新查询的匹配结果
    expect(getMql(queryA).listenerCount).toBe(0)
    expect(getMql(queryB).listenerCount).toBe(1)
    expect(result.current).toBe(true)
  })
})

describe('useIsMobile', () => {
  it('基于 (max-width: 768px) 查询判定移动端', () => {
    initialMatchesByQuery.set('(max-width: 768px)', true)
    const { result } = renderHook(() => useIsMobile())
    // 确认使用的是约定的移动端断点查询
    expect(mqlRegistry.has('(max-width: 768px)')).toBe(true)
    expect(result.current).toBe(true)
  })

  it('宽屏环境下返回 false 并响应断点变化', () => {
    const { result } = renderHook(() => useIsMobile())
    expect(result.current).toBe(false)

    act(() => {
      getMql('(max-width: 768px)').emitChange(true)
    })
    expect(result.current).toBe(true)
  })
})
