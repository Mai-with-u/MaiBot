import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { MaimaiVersion, PluginLoadProgress } from '../types'

const MARKET_LIST_STORAGE_KEY = 'maibot-plugin-market-list-cache'

// 稳定的 mock：backendApi 方法与 ApiError 类在 vi.resetModules 后保持同一引用
const httpMocks = vi.hoisted(() => {
  class MockApiError extends Error {
    readonly status?: number
    readonly detail?: unknown

    constructor(message: string, options: { status?: number; detail?: unknown } = {}) {
      super(message)
      this.name = 'ApiError'
      this.status = options.status
      this.detail = options.detail
    }
  }

  return {
    ApiError: MockApiError,
    backendApi: {
      get: vi.fn(),
      post: vi.fn(),
    },
  }
})

const progressClientMocks = vi.hoisted(() => ({
  subscribe: vi.fn(),
}))

vi.mock('@/lib/http', () => ({
  ApiError: httpMocks.ApiError,
  backendApi: httpMocks.backendApi,
}))

vi.mock('@/lib/plugin-progress-client', () => ({
  pluginProgressClient: progressClientMocks,
}))

async function loadMarketplace() {
  return await import('../marketplace')
}

/** 构造一个满足必需字段的插件市场条目 */
function createMarketItem(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    id: 'market-a',
    manifest: {
      manifest_version: 1,
      id: 'plugin-a',
      name: '插件 A',
      version: '1.0.0',
      description: '测试插件',
      author: { name: '作者' },
      license: 'MIT',
      host_application: { min_version: '0.10.0' },
      keywords: ['测试'],
      plugin_type: 'chat',
      default_locale: 'zh-CN',
    },
    ...overrides,
  }
}

function mockFetchRawSuccess(items: Array<Record<string, unknown>>): void {
  httpMocks.backendApi.post.mockResolvedValue({
    success: true,
    data: JSON.stringify(items),
  })
}

describe('plugin-api/marketplace', () => {
  beforeEach(() => {
    vi.resetModules()
    localStorage.clear()
    vi.spyOn(console, 'warn').mockImplementation(() => {})
  })

  describe('fetchPluginList', () => {
    it('解析插件列表并做字段归一化', async () => {
      mockFetchRawSuccess([
        createMarketItem({
          assets: { icon_64: 'icons/a.png' },
          published_at: 1700000000000,
          updated_at: '2026-01-01T00:00:00Z',
          changelog: '  修复若干问题  ',
        }),
      ])
      const marketplace = await loadMarketplace()

      const list = await marketplace.fetchPluginList()
      expect(list).toHaveLength(1)
      expect(list[0]).toMatchObject({
        id: 'plugin-a',
        marketplace_id: 'market-a',
        marketplace_order: 0,
        stats_ids: ['plugin-a'],
        source: 'market',
        installed: false,
        changelog: '修复若干问题',
        published_at: new Date(1700000000000).toISOString(),
        updated_at: '2026-01-01T00:00:00Z',
      })
      // 相对路径资源被拼接为 GitHub raw 地址
      expect(list[0].assets).toEqual({
        icon_64: 'https://raw.githubusercontent.com/Mai-with-u/plugin-repo/main/icons/a.png',
      })
      expect(list[0].manifest.plugin_type).toBe('chat')

      expect(httpMocks.backendApi.post).toHaveBeenCalledWith('/api/webui/plugins/fetch-raw', {
        body: {
          owner: 'Mai-with-u',
          repo: 'plugin-repo',
          branch: 'main',
          file_path: 'plugin_details.json',
        },
        errorMessage: '获取插件列表失败',
      })
    })

    it('过滤缺少 manifest、ID 或必需字段的条目', async () => {
      mockFetchRawSuccess([
        { id: 'no-manifest' },
        createMarketItem({ id: undefined, manifest: { ...createMarketItem().manifest as object, id: undefined } }),
        createMarketItem({
          manifest: { ...(createMarketItem().manifest as object), name: undefined },
        }),
        createMarketItem(),
      ])
      const marketplace = await loadMarketplace()

      const list = await marketplace.fetchPluginList()
      expect(list).toHaveLength(1)
      expect(list[0].id).toBe('plugin-a')
      expect(list[0].marketplace_order).toBe(0)
    })

    it('未知插件类型归为 other，缺失时归为 extension，危险资源路径被丢弃', async () => {
      const weirdTypeItem = createMarketItem({
        id: 'market-b',
        assets: { icon_64: '../evil.png' },
      })
      ;(weirdTypeItem.manifest as Record<string, unknown>).id = 'plugin-b'
      ;(weirdTypeItem.manifest as Record<string, unknown>).plugin_type = 'weird-type'

      const noTypeItem = createMarketItem({
        id: 'market-c',
        assets: { icon_64: 'https://cdn.example.com/icon.png' },
      })
      ;(noTypeItem.manifest as Record<string, unknown>).id = 'plugin-c'
      delete (noTypeItem.manifest as Record<string, unknown>).plugin_type

      mockFetchRawSuccess([weirdTypeItem, noTypeItem])
      const marketplace = await loadMarketplace()

      const list = await marketplace.fetchPluginList()
      expect(list[0].manifest.plugin_type).toBe('other')
      expect(list[0].assets).toBeUndefined()
      expect(list[1].manifest.plugin_type).toBe('extension')
      // 绝对 URL 保持原样
      expect(list[1].assets).toEqual({ icon_64: 'https://cdn.example.com/icon.png' })
    })

    it('TTL 内复用内存缓存并写入 localStorage，forceRefresh 重新请求', async () => {
      mockFetchRawSuccess([createMarketItem()])
      const marketplace = await loadMarketplace()

      await marketplace.fetchPluginList()
      await marketplace.fetchPluginList()
      expect(httpMocks.backendApi.post).toHaveBeenCalledTimes(1)
      expect(localStorage.getItem(MARKET_LIST_STORAGE_KEY)).not.toBeNull()

      await marketplace.fetchPluginList({ forceRefresh: true })
      expect(httpMocks.backendApi.post).toHaveBeenCalledTimes(2)
    })

    it('后端返回 success=false 时抛出 ApiError 且不写缓存', async () => {
      httpMocks.backendApi.post.mockResolvedValue({ success: false, error: '仓库不可用' })
      const marketplace = await loadMarketplace()

      await expect(marketplace.fetchPluginList()).rejects.toThrow('仓库不可用')
      expect(localStorage.getItem(MARKET_LIST_STORAGE_KEY)).toBeNull()
      expect(marketplace.getCachedPluginList()).toBeNull()
    })
  })

  describe('getCachedPluginList', () => {
    it('无缓存时返回 null，localStorage 有缓存时直接返回', async () => {
      const marketplaceEmpty = await loadMarketplace()
      expect(marketplaceEmpty.getCachedPluginList()).toBeNull()

      vi.resetModules()
      localStorage.setItem(
        MARKET_LIST_STORAGE_KEY,
        JSON.stringify({ timestamp: Date.now(), data: [{ id: 'cached-plugin' }] })
      )
      const marketplace = await loadMarketplace()

      const cached = marketplace.getCachedPluginList()
      expect(cached?.[0]?.id).toBe('cached-plugin')
      expect(httpMocks.backendApi.post).not.toHaveBeenCalled()
    })
  })

  describe('checkGitStatus', () => {
    it('成功时透传后端结果', async () => {
      httpMocks.backendApi.get.mockResolvedValue({ installed: true, version: '2.39.0' })
      const marketplace = await loadMarketplace()

      await expect(marketplace.checkGitStatus()).resolves.toEqual({
        installed: true,
        version: '2.39.0',
      })
    })

    it('HTTP 错误按无法检测处理，401 与网络层失败向上抛出', async () => {
      httpMocks.backendApi.get.mockRejectedValueOnce(
        new httpMocks.ApiError('服务器错误', { status: 500 })
      )
      const marketplace = await loadMarketplace()
      await expect(marketplace.checkGitStatus()).resolves.toEqual({
        installed: false,
        error: '无法检测 Git 安装状态',
      })

      httpMocks.backendApi.get.mockRejectedValueOnce(
        new httpMocks.ApiError('未认证', { status: 401 })
      )
      await expect(marketplace.checkGitStatus()).rejects.toThrow('未认证')

      httpMocks.backendApi.get.mockRejectedValueOnce(new httpMocks.ApiError('网络失败'))
      await expect(marketplace.checkGitStatus()).rejects.toThrow('网络失败')
    })
  })

  describe('getMaimaiVersion', () => {
    it('成功时透传版本信息，HTTP 错误回退 0.0.0', async () => {
      const version: MaimaiVersion = {
        version: '0.10.5',
        version_major: 0,
        version_minor: 10,
        version_patch: 5,
      }
      httpMocks.backendApi.get.mockResolvedValueOnce(version)
      const marketplace = await loadMarketplace()
      await expect(marketplace.getMaimaiVersion()).resolves.toEqual(version)

      httpMocks.backendApi.get.mockRejectedValueOnce(
        new httpMocks.ApiError('服务器错误', { status: 500 })
      )
      await expect(marketplace.getMaimaiVersion()).resolves.toEqual({
        version: '0.0.0',
        version_major: 0,
        version_minor: 0,
        version_patch: 0,
      })
    })

    it('401 与网络层失败向上抛出', async () => {
      httpMocks.backendApi.get.mockRejectedValueOnce(
        new httpMocks.ApiError('未认证', { status: 401 })
      )
      const marketplace = await loadMarketplace()
      await expect(marketplace.getMaimaiVersion()).rejects.toThrow('未认证')

      httpMocks.backendApi.get.mockRejectedValueOnce(new httpMocks.ApiError('网络失败'))
      await expect(marketplace.getMaimaiVersion()).rejects.toThrow('网络失败')
    })
  })

  describe('isPluginCompatible', () => {
    const currentVersion: MaimaiVersion = {
      version: '0.10.5',
      version_major: 0,
      version_minor: 10,
      version_patch: 5,
    }

    it('当前版本低于最小要求时不兼容', async () => {
      const marketplace = await loadMarketplace()
      expect(marketplace.isPluginCompatible('0.11.0', undefined, currentVersion)).toBe(false)
      expect(marketplace.isPluginCompatible('0.9.0', undefined, currentVersion)).toBe(true)
    })

    it('仅修订号超过声明上限时按兼容模式放行', async () => {
      const marketplace = await loadMarketplace()
      // 同主次版本、修订号更高：允许
      expect(marketplace.isPluginCompatible('0.10.0', '0.10.2', currentVersion)).toBe(true)
      // 次版本已超过上限：拒绝
      expect(
        marketplace.isPluginCompatible('0.9.0', '0.9.9', currentVersion)
      ).toBe(false)
    })

    it('解析 snapshot 后缀与非法版本号', async () => {
      const marketplace = await loadMarketplace()
      // snapshot 后缀被剥离后按 0.10.0 处理
      expect(marketplace.isPluginCompatible('0.10.0-snapshot.3', undefined, currentVersion)).toBe(
        true
      )
      // 非法版本号解析为 0.0.0，视为无最低要求
      expect(marketplace.isPluginCompatible('abc', undefined, currentVersion)).toBe(true)
    })
  })

  describe('connectPluginProgressWebSocket', () => {
    it('订阅成功时返回底层清理函数', async () => {
      const cleanup = vi.fn(async () => {})
      progressClientMocks.subscribe.mockResolvedValue(cleanup)
      const marketplace = await loadMarketplace()
      const onProgress = vi.fn()

      const result = await marketplace.connectPluginProgressWebSocket(onProgress)
      expect(result).toBe(cleanup)
      expect(progressClientMocks.subscribe).toHaveBeenCalledWith(onProgress)
    })

    it('订阅失败时回调 onError 并返回可安全调用的空清理函数', async () => {
      const subscribeError = new Error('订阅失败')
      progressClientMocks.subscribe.mockRejectedValueOnce(subscribeError)
      const marketplace = await loadMarketplace()
      const onError = vi.fn()

      const cleanup = await marketplace.connectPluginProgressWebSocket(vi.fn(), onError)
      expect(onError).toHaveBeenCalledWith(subscribeError)
      await expect(cleanup()).resolves.toBeUndefined()
    })

    it('非 Error 拒绝原因被归一化为固定错误', async () => {
      progressClientMocks.subscribe.mockRejectedValueOnce('oops')
      const marketplace = await loadMarketplace()
      const onError = vi.fn()

      await marketplace.connectPluginProgressWebSocket(
        vi.fn<(progress: PluginLoadProgress) => void>(),
        onError
      )
      expect(onError).toHaveBeenCalledTimes(1)
      const receivedError = onError.mock.calls[0][0] as Error
      expect(receivedError.message).toBe('插件进度订阅失败')
    })
  })
})
