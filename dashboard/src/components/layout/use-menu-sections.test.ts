import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { BOT_CONFIG_UPDATED_EVENT, getBotConfigCached } from '@/lib/config-api'
import { fetchPluginPages } from '@/lib/plugin-api/pages'
import { PLUGIN_PAGES_UPDATED_EVENT } from '@/lib/plugin-api/plugin-pages-events'

import type { MenuSection } from './types'
import { appendPluginPages, resolvePluginPageIcon, useMenuSections } from './use-menu-sections'

// mock 配置 API：避免测试中发起真实请求，同时保持事件名常量与源码一致
vi.mock('@/lib/config-api', () => ({
  BOT_CONFIG_UPDATED_EVENT: 'maibot:bot-config-updated',
  getBotConfigCached: vi.fn(),
}))

vi.mock('@/lib/plugin-api/pages', () => ({
  fetchPluginPages: vi.fn(),
}))

const mockGetBotConfigCached = vi.mocked(getBotConfigCached)
const mockFetchPluginPages = vi.mocked(fetchPluginPages)

beforeEach(() => {
  mockGetBotConfigCached.mockResolvedValue({})
  mockFetchPluginPages.mockResolvedValue({ success: true, pages: [], warnings: [] })
})

/** 把菜单分组拍平成路径列表，便于断言某个入口是否可见 */
function flattenPaths(sections: MenuSection[]): string[] {
  return sections.flatMap((section) => section.items.map((item) => item.path))
}

describe('useMenuSections', () => {
  it('使用受控 Lucide 图标清单解析插件页面图标，未知值回退 Puzzle', () => {
    expect(resolvePluginPageIcon('bar-chart-3')).not.toBe(resolvePluginPageIcon('unknown'))
    expect(resolvePluginPageIcon(null)).toBe(resolvePluginPageIcon('puzzle'))
  })

  it('追加页面时使用 Manifest icon', () => {
    const sections = appendPluginPages(
      [
        {
          title: 'sidebar.groups.extensionsMonitor',
          items: [],
        },
      ],
      [
        {
          plugin_id: 'example.first',
          page_id: 'hello',
          title: '插件你好',
          route: '/plugin-pages/example.first/hello',
          entry: '/api/webui/plugins/example.first/assets/webui/dist/index.js',
          component: 'mount',
          icon: 'bar-chart-3',
          order: 0,
          permissions: [],
          api_base: '/api/webui/plugins/example.first/pages/hello/api',
        },
      ]
    )

    expect(sections[0].items[0].icon).toBe(resolvePluginPageIcon('bar-chart-3'))
  })

  it('收到插件生命周期事件后重新拉取页面清单', async () => {
    mockFetchPluginPages
      .mockResolvedValueOnce({ success: true, pages: [], warnings: [] })
      .mockResolvedValueOnce({
        success: true,
        pages: [
          {
            plugin_id: 'example.first',
            page_id: 'hello',
            title: '插件你好',
            route: '/plugin-pages/example.first/hello',
            entry: '/api/webui/plugins/example.first/assets/webui/dist/index.js',
            component: 'mount',
            icon: null,
            order: 0,
            permissions: [],
            api_base: '/api/webui/plugins/example.first/pages/hello/api',
          },
        ],
        warnings: [],
      })

    const { result } = renderHook(() => useMenuSections())
    await waitFor(() => expect(mockFetchPluginPages).toHaveBeenCalledTimes(1))

    act(() => {
      window.dispatchEvent(new Event(PLUGIN_PAGES_UPDATED_EVENT))
    })

    await waitFor(() => {
      expect(mockFetchPluginPages).toHaveBeenCalledTimes(2)
      expect(flattenPaths(result.current)).toContain('/plugin-pages/example.first/hello')
    })
  })

  it('菜单卸载后不再响应插件生命周期事件', async () => {
    const { unmount } = renderHook(() => useMenuSections())

    await waitFor(() => expect(mockFetchPluginPages).toHaveBeenCalledTimes(1))
    unmount()

    act(() => {
      window.dispatchEvent(new Event(PLUGIN_PAGES_UPDATED_EVENT))
    })

    expect(mockFetchPluginPages).toHaveBeenCalledTimes(1)
  })

  it('把插件页面追加到扩展与集成分组并保留 Host 路由', async () => {
    mockFetchPluginPages.mockResolvedValue({
      success: true,
      pages: [
        {
          plugin_id: 'example.first',
          page_id: 'hello',
          title: '插件你好',
          route: '/plugin-pages/example.first/hello',
          entry: '/api/webui/plugins/example.first/assets/webui/dist/index.js',
          component: 'mount',
          icon: null,
          order: 10,
          permissions: [],
          api_base: '/api/webui/plugins/example.first/pages/hello/api',
        },
      ],
      warnings: [],
    })

    const { result } = renderHook(() => useMenuSections())

    await waitFor(() => {
      expect(flattenPaths(result.current)).toContain('/plugin-pages/example.first/hello')
    })

    const extensionsSection = result.current.find(
      (section) => section.title === 'sidebar.groups.extensionsMonitor'
    )
    expect(extensionsSection?.items).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          label: '插件你好',
          labelMode: 'text',
          icon: expect.anything(),
        }),
      ])
    )
  })

  it('插件页面清单请求失败时保留静态扩展入口', async () => {
    mockFetchPluginPages.mockRejectedValue(new Error('页面服务不可用'))
    vi.spyOn(console, 'error').mockImplementation(() => {})

    const { result } = renderHook(() => useMenuSections())

    await waitFor(() => {
      expect(mockFetchPluginPages).toHaveBeenCalled()
    })
    const paths = flattenPaths(result.current)
    expect(paths).toEqual(expect.arrayContaining(['/plugin-config', '/plugins', '/mcp-settings']))
    expect(paths.some((path) => path.startsWith('/plugin-pages/'))).toBe(false)
  })

  it('配置加载完成前隐藏受开关控制的入口', () => {
    // 返回一个永不 resolve 的 Promise，模拟配置仍在加载
    mockGetBotConfigCached.mockReturnValue(new Promise(() => {}))
    mockFetchPluginPages.mockReturnValue(new Promise(() => {}))

    const { result } = renderHook(() => useMenuSections())
    const paths = flattenPaths(result.current)

    expect(paths).not.toContain('/resource/behavior')
    expect(paths).not.toContain('/reply-effects')
    // 其它常规入口不受特性开关影响
    expect(paths).toContain('/')
    expect(paths).toContain('/config/bot')
    expect(paths).toContain('/resource/emoji')
    // 五个分组都保留（没有分组被过滤成空）
    expect(result.current).toHaveLength(5)
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
      'sidebar.groups.advancedTools',
    ])
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
    expect(flattenPaths(result.current)).not.toContain('/reply-effects')
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
    expect(flattenPaths(result.current)).not.toContain('/reply-effects')
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
