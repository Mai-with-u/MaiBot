import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { MaimaiVersion, PluginLoadProgress } from '../types'

const MARKET_LIST_STORAGE_KEY = 'maibot-plugin-market-list-cache-v4'

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
    id: 'plugin-a', marketplace_id: 'market-a', stats: null,
    manifest: { manifest_version: 2, id: 'plugin-a', name: '插件 A', version: '1.0.0',
      description: '测试插件', author: { name: '作者' }, license: 'MIT',
      host_application: { min_version: '1.0.0' }, keywords: ['测试'],
      plugin_type: 'chat', i18n: { default_locale: 'ja', locales_path: '_locales' } },
    releases: { id: 'market-a', manifest_id: 'plugin-a', repositoryUrl: 'https://github.com/test/a',
      mode: 'releases', versions: [], recommended_version: null },
    ...overrides,
  }
}

describe('plugin-api/marketplace', () => {
  beforeEach(() => {
    vi.resetModules()
    vi.clearAllMocks()
    httpMocks.backendApi.get.mockReset().mockResolvedValue({ source: 'official', plugins: [createMarketItem()] })
    httpMocks.backendApi.post.mockReset()
    localStorage.clear()
    vi.spyOn(console, 'warn').mockImplementation(() => {})
  })

  describe('公共市场列表与文档', () => {
    it.each(['official', 'cnb', 'github'])('信息源 %s 都只调用公共列表接口', async (source) => {
      httpMocks.backendApi.get.mockResolvedValue({ source, plugins: [createMarketItem()] })
      const marketplace = await loadMarketplace()
      const plugins = await marketplace.fetchPluginList()
      expect(plugins[0]).toMatchObject({ id: 'plugin-a', marketplace_id: 'market-a', market_data_source: source })
      expect(plugins[0].manifest).toMatchObject({ default_locale: 'ja', locales_path: '_locales' })
      expect(httpMocks.backendApi.get).toHaveBeenCalledTimes(1)
      expect(httpMocks.backendApi.post).not.toHaveBeenCalled()
    })

    it('列表自带统计和发布版本，不再另拉摘要；强制刷新透传到 Host', async () => {
      const stats = { plugin_id: 'plugin-a', downloads: 12, likes: 3, rating: 4, comment_count: 2 }
      httpMocks.backendApi.get.mockResolvedValue({ source: 'official', plugins: [createMarketItem({ stats })] })
      const marketplace = await loadMarketplace()
      expect((await marketplace.fetchPluginList())[0]).toMatchObject({ downloads: 12, rating: 4, marketplace_stats: stats })
      await marketplace.fetchPluginList()
      expect(httpMocks.backendApi.get).toHaveBeenCalledTimes(1)
      await marketplace.fetchPluginList({ forceRefresh: true })
      expect(httpMocks.backendApi.get).toHaveBeenLastCalledWith('/api/webui/plugins/marketplace', {
        query: { compatible_only: false, force_refresh: true }, errorMessage: '获取插件市场数据失败',
      })
      marketplace.invalidatePluginMarketCache()
      expect(marketplace.getCachedPluginList()).toBeNull()
      expect(localStorage.getItem(MARKET_LIST_STORAGE_KEY)).toBeNull()
    })

    it('兼容与全部列表分别缓存，请求失败不会写入缓存', async () => {
      const marketplace = await loadMarketplace()
      await marketplace.fetchPluginList({ compatibleOnly: true })
      await marketplace.fetchPluginList()
      await marketplace.fetchPluginList({ compatibleOnly: true })
      expect(httpMocks.backendApi.get).toHaveBeenCalledTimes(2)
      marketplace.invalidatePluginMarketCache()
      httpMocks.backendApi.get.mockRejectedValue(new Error('offline'))
      await expect(marketplace.fetchPluginList()).rejects.toThrow('offline')
      expect(marketplace.getCachedPluginList()).toBeNull()
    })

    it('不同信息源的详情都使用公共接口', async () => {
      const marketplace = await loadMarketplace()
      const plugins = await marketplace.fetchPluginList()
      httpMocks.backendApi.get.mockResolvedValue({ plugin: createMarketItem(), releases: plugins[0].releases })
      const detail = await marketplace.fetchPluginDetail({ ...plugins[0], market_data_source: 'github' })
      expect(detail.id).toBe('plugin-a')
      expect(httpMocks.backendApi.get).toHaveBeenLastCalledWith('/api/webui/plugins/marketplace/plugin-a', {
        errorMessage: '获取插件详情失败',
      })
    })

    it('文档只传插件 ID、所选 ref 和本地优先选项，不在前端解析仓库', async () => {
      const marketplace = await loadMarketplace()
      httpMocks.backendApi.get.mockResolvedValue({ content: '# 文档' })
      const signal = new AbortController().signal
      await expect(marketplace.fetchPluginDocument('plugin/a', 'readme', {
        preferLocal: false, branch: 'commit', signal,
      })).resolves.toBe('# 文档')
      expect(httpMocks.backendApi.get).toHaveBeenCalledWith('/api/webui/plugins/marketplace/plugin%2Fa/readme', {
        query: { prefer_local: false, branch: 'commit' }, signal, errorMessage: '获取插件文档失败',
      })
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
