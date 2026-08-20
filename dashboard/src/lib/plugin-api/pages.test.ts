import { beforeEach, describe, expect, it, vi } from 'vitest'

import { backendApi } from '@/lib/http'

import { fetchPluginPages } from './pages'

vi.mock('@/lib/http', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/http')>()
  return {
    ...actual,
    backendApi: {
      ...actual.backendApi,
      get: vi.fn(),
    },
  }
})

describe('fetchPluginPages', () => {
  beforeEach(() => {
    vi.mocked(backendApi.get).mockReset()
  })

  it('通过 backendApi 获取并校验插件页面清单', async () => {
    vi.mocked(backendApi.get).mockResolvedValue({
      success: true,
      pages: [
        {
          plugin_id: 'example.first',
          page_id: 'hello',
          title: 'Hello',
          route: '/plugin-pages/example.first/hello',
          entry:
            '/api/webui/plugins/example.first/assets/webui/dist/index.js?v=1.0.0',
          component: 'mount',
          icon: null,
          order: 0,
          permissions: [],
          api_base: '/api/webui/plugins/example.first/pages/hello/api',
        },
      ],
      warnings: [],
    })

    await expect(fetchPluginPages()).resolves.toMatchObject({
      pages: [{ page_id: 'hello' }],
    })
    expect(backendApi.get).toHaveBeenCalledWith('/api/webui/plugins/pages', {
      signal: undefined,
    })
  })

  it('业务失败包络会被转换为 ApiError', async () => {
    vi.mocked(backendApi.get).mockResolvedValue({
      success: false,
      pages: [],
      warnings: [],
      message: '不可用',
    })

    await expect(fetchPluginPages()).rejects.toThrow('不可用')
  })
})
