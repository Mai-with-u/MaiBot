import { act, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { backendApi } from '@/lib/http'
import { fetchPluginPages } from '@/lib/plugin-api/pages'
import { PLUGIN_PAGES_UPDATED_EVENT } from '@/lib/plugin-api/plugin-pages-events'

import { PluginPageHost } from './PluginPageHost'
import { loadPluginPageModule } from './loader'

const paramsMock = vi.hoisted(() => ({
  pluginId: 'example.first',
  pageId: 'hello',
}))

vi.mock('@tanstack/react-router', () => ({
  useParams: () => paramsMock,
}))

vi.mock('@/lib/http', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/http')>()
  return {
    ...actual,
    backendApi: {
      ...actual.backendApi,
      post: vi.fn(),
    },
  }
})

vi.mock('@/lib/plugin-api/pages', () => ({
  fetchPluginPages: vi.fn(),
}))

vi.mock('./loader', () => ({
  loadPluginPageModule: vi.fn(),
}))

const page = {
  plugin_id: 'example.first',
  page_id: 'hello',
  title: 'Hello',
  route: '/plugin-pages/example.first/hello',
  entry: '/api/webui/plugins/example.first/assets/webui/dist/index.js?v=1.0.0',
  component: 'mount',
  icon: null,
  order: 0,
  permissions: [],
  api_base: '/api/webui/plugins/example.first/pages/hello/api',
}

describe('PluginPageHost', () => {
  beforeEach(() => {
    vi.mocked(fetchPluginPages).mockReset()
    vi.mocked(loadPluginPageModule).mockReset()
    vi.mocked(backendApi.post).mockReset()
    vi.mocked(fetchPluginPages).mockResolvedValue({ success: true, pages: [page], warnings: [] })
  })

  it('加载入口并调用 mount，离开时调用清理函数', async () => {
    const cleanup = vi.fn()
    const mount = vi.fn((_: HTMLElement, __: Record<string, unknown>) => cleanup)
    vi.mocked(loadPluginPageModule).mockResolvedValue({ mount })

    const { unmount } = render(<PluginPageHost />)

    await waitFor(() => {
      expect(mount).toHaveBeenCalledWith(
        expect.any(HTMLElement),
        expect.objectContaining({
          pluginId: 'example.first',
          pageId: 'hello',
          apiBase: page.api_base,
          assetsBase: '/api/webui/plugins/example.first/assets/',
        })
      )
    })
    const context = mount.mock.calls[0]?.[1]
    expect(context).not.toHaveProperty('client')
    unmount()
    expect(cleanup).toHaveBeenCalledOnce()
  })

  it('context.request 解包 Host API 成功响应中的 data', async () => {
    const mount = vi.fn()
    vi.mocked(loadPluginPageModule).mockResolvedValue({ mount })
    vi.mocked(backendApi.post).mockResolvedValue({
      success: true,
      data: { message: '来自插件 API' },
      request_id: 'request-123',
    })

    render(<PluginPageHost />)

    await waitFor(() => {
      expect(mount).toHaveBeenCalledOnce()
    })
    const context = mount.mock.calls[0]?.[1] as {
      request<T>(
        operation: string,
        options?: { debug?: boolean }
      ): Promise<T | { data: T; request_id: string }>
    }
    const result = await context.request<{ message: string }>('greet', { debug: true })

    expect(result).toEqual({
      data: { message: '来自插件 API' },
      request_id: 'request-123',
    })
    expect(backendApi.post).toHaveBeenCalledWith(
      `${page.api_base}/greet`,
      expect.objectContaining({ query: { debug: true } })
    )
  })

  it('入口加载失败时显示可诊断错误而不是空白页面', async () => {
    vi.mocked(loadPluginPageModule).mockRejectedValue(new Error('入口损坏'))

    render(<PluginPageHost />)

    expect(screen.getByRole('status')).toHaveTextContent('正在加载插件页面')
    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent('入口损坏')
    })
  })

  it('拒绝非 Host 同源插件资源入口', async () => {
    vi.mocked(fetchPluginPages).mockResolvedValue({
      success: true,
      pages: [{ ...page, entry: 'https://evil.example/plugin.js' }],
      warnings: [],
    })

    render(<PluginPageHost />)

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent('插件页面入口必须使用 Host 同源资源')
    })
    expect(loadPluginPageModule).not.toHaveBeenCalled()
  })

  it('插件重载后重新发现并重新挂载，旧页面 cleanup 被执行', async () => {
    const cleanup = vi.fn()
    const mount = vi.fn((_: HTMLElement, __: Record<string, unknown>) => cleanup)
    vi.mocked(loadPluginPageModule).mockResolvedValue({ mount })
    vi.mocked(fetchPluginPages).mockResolvedValueOnce({ success: true, pages: [page], warnings: [] })
    vi.mocked(fetchPluginPages).mockResolvedValueOnce({ success: true, pages: [page], warnings: [] })

    render(<PluginPageHost />)

    await waitFor(() => {
      expect(mount).toHaveBeenCalledOnce()
    })

    act(() => {
      window.dispatchEvent(new Event(PLUGIN_PAGES_UPDATED_EVENT))
    })

    await waitFor(() => {
      expect(mount).toHaveBeenCalledTimes(2)
    })
    expect(cleanup).toHaveBeenCalledOnce()
  })

  it('插件页面被删除后重新拉取清单并进入错误状态', async () => {
    const mount = vi.fn()
    vi.mocked(loadPluginPageModule).mockResolvedValue({ mount })
    vi.mocked(fetchPluginPages).mockResolvedValueOnce({ success: true, pages: [page], warnings: [] })
    vi.mocked(fetchPluginPages).mockResolvedValueOnce({ success: true, pages: [], warnings: [] })

    render(<PluginPageHost />)

    await waitFor(() => {
      expect(mount).toHaveBeenCalledOnce()
    })

    act(() => {
      window.dispatchEvent(new Event(PLUGIN_PAGES_UPDATED_EVENT))
    })

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent('插件页面不存在')
    })
  })

  it('拒绝非 Host 同源插件 API 基址', async () => {
    vi.mocked(fetchPluginPages).mockResolvedValue({
      success: true,
      pages: [{ ...page, api_base: 'https://evil.example/api' }],
      warnings: [],
    })
    vi.mocked(loadPluginPageModule).mockResolvedValue({ mount: vi.fn() })

    render(<PluginPageHost />)

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent('插件页面 API 必须使用 Host 同源资源')
    })
    expect(loadPluginPageModule).not.toHaveBeenCalled()
  })
  it('id 与 route 不同时按 route slug 匹配并挂载', async () => {
    paramsMock.pageId = 'custom-slug'
    const mount = vi.fn()
    vi.mocked(loadPluginPageModule).mockResolvedValue({ mount })
    vi.mocked(fetchPluginPages).mockResolvedValue({
      success: true,
      pages: [
        {
          ...page,
          page_id: 'hello',
          route: '/plugin-pages/example.first/custom-slug',
          api_base: '/api/webui/plugins/example.first/pages/hello/api',
        },
      ],
      warnings: [],
    })

    try {
      render(<PluginPageHost />)

      await waitFor(() => {
        expect(mount).toHaveBeenCalledOnce()
      })
      expect(mount).toHaveBeenCalledWith(
        expect.any(HTMLElement),
        expect.objectContaining({ pluginId: 'example.first', pageId: 'hello' })
      )
      expect(screen.queryByRole('alert')).toBeNull()
    } finally {
      paramsMock.pageId = 'hello'
    }
  })
})
