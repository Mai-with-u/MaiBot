import { describe, expect, it, vi } from 'vitest'

import { ApiError, backendApi } from '@/lib/http'

import { getPluginHomeCards } from './home-cards'
import type { PluginHomeCard } from './home-cards'

vi.mock('@/lib/http', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/http')>()
  return {
    ...actual,
    backendApi: {
      request: vi.fn(),
      get: vi.fn(),
      post: vi.fn(),
      put: vi.fn(),
      patch: vi.fn(),
      delete: vi.fn(),
    },
  }
})

const getMock = vi.mocked(backendApi.get)

/** 构造一个最小可用的插件首页卡片 */
function makeHomeCard(id: string): PluginHomeCard {
  return {
    id,
    name: '状态卡片',
    plugin_id: 'demo-plugin',
    title: '运行状态',
    show_title: true,
    description: '展示插件运行状态',
    content: '一切正常',
    link_url: 'https://example.com',
    link_label: '查看详情',
    icon: 'activity',
    width: 'medium',
    order: 1,
    enabled: true,
  }
}

describe('getPluginHomeCards', () => {
  it('成功时请求首页卡片接口并返回卡片列表', async () => {
    const cards = [makeHomeCard('card-1'), makeHomeCard('card-2')]
    getMock.mockResolvedValue({ success: true, cards })

    await expect(getPluginHomeCards()).resolves.toBe(cards)
    expect(getMock).toHaveBeenCalledWith('/api/webui/plugins/runtime/home-cards', {
      errorMessage: '加载插件首页卡片失败',
    })
  })

  it('响应中 cards 缺省时返回空数组', async () => {
    getMock.mockResolvedValue({ success: true })

    await expect(getPluginHomeCards()).resolves.toEqual([])
  })

  it('后端返回错误时向上抛出 ApiError', async () => {
    getMock.mockRejectedValue(new ApiError('加载插件首页卡片失败', { status: 500 }))

    await expect(getPluginHomeCards()).rejects.toBeInstanceOf(ApiError)
    getMock.mockRejectedValue(new ApiError('加载插件首页卡片失败', { status: 500 }))
    await expect(getPluginHomeCards()).rejects.toMatchObject({ status: 500 })
  })
})
