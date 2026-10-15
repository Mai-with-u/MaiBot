import { act, renderHook, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { BOT_CONFIG_UPDATED_EVENT, getBotConfigCached } from '@/lib/config-api'

import type { MenuSection } from './types'
import { useMenuSections } from './use-menu-sections'

// mock 配置 API：避免测试中发起真实请求，同时保持事件名常量与源码一致
vi.mock('@/lib/config-api', () => ({
  BOT_CONFIG_UPDATED_EVENT: 'maibot:bot-config-updated',
  getBotConfigCached: vi.fn(),
}))

const mockGetBotConfigCached = vi.mocked(getBotConfigCached)

/** 把菜单分组拍平成路径列表，便于断言某个入口是否可见 */
function flattenPaths(sections: MenuSection[]): string[] {
  return sections.flatMap((section) => section.items.map((item) => item.path))
}

describe('useMenuSections', () => {
  it('配置加载完成前隐藏受开关控制的入口', () => {
    // 返回一个永不 resolve 的 Promise，模拟配置仍在加载
    mockGetBotConfigCached.mockReturnValue(new Promise(() => {}))

    const { result } = renderHook(() => useMenuSections())
    const paths = flattenPaths(result.current)

    expect(paths).not.toContain('/reply-effects')
    // 其它常规入口不受特性开关影响
    expect(paths).toContain('/')
    expect(paths).toContain('/config/bot')
    expect(paths).toContain('/resource/emoji')
    // 五个分组都保留（没有分组被过滤成空）
    expect(result.current).toHaveLength(5)
  })


  it('回复评分调试开关开启时显示回复效果入口', async () => {
    mockGetBotConfigCached.mockResolvedValue({
      debug: { enable_reply_effect_tracking: true },
    })

    const { result } = renderHook(() => useMenuSections())

    await waitFor(() => {
      expect(flattenPaths(result.current)).toContain('/reply-effects')
    })
  })

  it('回复评分调试开关关闭或缺失时隐藏回复效果入口', async () => {
    mockGetBotConfigCached.mockResolvedValue({
      debug: { enable_reply_effect_tracking: false },
    })

    const { result } = renderHook(() => useMenuSections())

    await waitFor(() => {
      expect(mockGetBotConfigCached).toHaveBeenCalled()
    })
    expect(flattenPaths(result.current)).not.toContain('/reply-effects')
  })

  it('收到配置更新事件后重新拉取并刷新菜单', async () => {
    mockGetBotConfigCached
      .mockResolvedValueOnce({ debug: { enable_reply_effect_tracking: false } })
      .mockResolvedValueOnce({ debug: { enable_reply_effect_tracking: true } })

    const { result } = renderHook(() => useMenuSections())
    const initial = result.current

    // 先等第一次拉取（开关关闭）生效
    await waitFor(() => {
      expect(result.current).not.toBe(initial)
    })
    expect(flattenPaths(result.current)).not.toContain('/reply-effects')

    // 派发配置更新事件，触发第二次拉取（开关开启）
    act(() => {
      window.dispatchEvent(new Event(BOT_CONFIG_UPDATED_EVENT))
    })

    await waitFor(() => {
      expect(flattenPaths(result.current)).toContain('/reply-effects')
    })
    expect(mockGetBotConfigCached).toHaveBeenCalledTimes(2)
  })

  it('卸载后不再响应配置更新事件', async () => {
    mockGetBotConfigCached.mockResolvedValue({})

    const { unmount } = renderHook(() => useMenuSections())

    await waitFor(() => {
      expect(mockGetBotConfigCached).toHaveBeenCalledTimes(1)
    })

    unmount()
    act(() => {
      window.dispatchEvent(new Event(BOT_CONFIG_UPDATED_EVENT))
    })

    expect(mockGetBotConfigCached).toHaveBeenCalledTimes(1)
  })
})
