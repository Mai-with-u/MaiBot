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
  it('配置加载完成前隐藏行为学习入口，其余菜单完整', () => {
    // 返回一个永不 resolve 的 Promise，模拟配置仍在加载
    mockGetBotConfigCached.mockReturnValue(new Promise(() => {}))

    const { result } = renderHook(() => useMenuSections())
    const paths = flattenPaths(result.current)

    expect(paths).not.toContain('/resource/behavior')
    // 其它常规入口不受特性开关影响
    expect(paths).toContain('/')
    expect(paths).toContain('/config/bot')
    expect(paths).toContain('/resource/emoji')
    // 四个分组都保留（没有分组被过滤成空）
    expect(result.current).toHaveLength(4)
  })

  it('实验开关开启时显示行为学习入口', async () => {
    mockGetBotConfigCached.mockResolvedValue({
      experimental: { enable_behavior_learning: true },
    })

    const { result } = renderHook(() => useMenuSections())

    await waitFor(() => {
      expect(flattenPaths(result.current)).toContain('/resource/behavior')
    })
    // 分组标题保持完整
    expect(result.current.map((section) => section.title)).toEqual([
      'sidebar.groups.overview',
      'sidebar.groups.botConfig',
      'sidebar.groups.botResources',
      'sidebar.groups.extensionsMonitor',
    ])
  })

  it('实验开关关闭时隐藏行为学习入口', async () => {
    mockGetBotConfigCached.mockResolvedValue({
      experimental: { enable_behavior_learning: false },
    })

    const { result } = renderHook(() => useMenuSections())
    const initial = result.current

    // 配置加载后 featureFlags 状态更新，useMemo 会产出新的数组引用
    await waitFor(() => {
      expect(result.current).not.toBe(initial)
    })

    const paths = flattenPaths(result.current)
    expect(paths).not.toContain('/resource/behavior')
    // 资源分组中其余入口保持可见
    expect(paths).toContain('/resource/expression')
    expect(paths).toContain('/resource/knowledge-base')
  })

  it('配置缺少 experimental 字段时默认显示行为学习入口', async () => {
    mockGetBotConfigCached.mockResolvedValue({})

    const { result } = renderHook(() => useMenuSections())

    await waitFor(() => {
      expect(flattenPaths(result.current)).toContain('/resource/behavior')
    })
  })

  it('experimental 存在但缺少开关键时默认显示行为学习入口', async () => {
    mockGetBotConfigCached.mockResolvedValue({ experimental: {} })

    const { result } = renderHook(() => useMenuSections())

    await waitFor(() => {
      expect(flattenPaths(result.current)).toContain('/resource/behavior')
    })
  })

  it('配置拉取失败时回退为显示行为学习入口', async () => {
    mockGetBotConfigCached.mockRejectedValue(new Error('网络错误'))

    const { result } = renderHook(() => useMenuSections())

    await waitFor(() => {
      expect(flattenPaths(result.current)).toContain('/resource/behavior')
    })
  })

  it('收到配置更新事件后重新拉取并刷新菜单', async () => {
    mockGetBotConfigCached
      .mockResolvedValueOnce({ experimental: { enable_behavior_learning: false } })
      .mockResolvedValueOnce({ experimental: { enable_behavior_learning: true } })

    const { result } = renderHook(() => useMenuSections())
    const initial = result.current

    // 先等第一次拉取（开关关闭）生效
    await waitFor(() => {
      expect(result.current).not.toBe(initial)
    })
    expect(flattenPaths(result.current)).not.toContain('/resource/behavior')

    // 派发配置更新事件，触发第二次拉取（开关开启）
    act(() => {
      window.dispatchEvent(new Event(BOT_CONFIG_UPDATED_EVENT))
    })

    await waitFor(() => {
      expect(flattenPaths(result.current)).toContain('/resource/behavior')
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
