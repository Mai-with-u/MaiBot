import { beforeEach, describe, expect, it, vi } from 'vitest'

const SUMMARY_STORAGE_KEY = 'maibot-plugin-stats-summary-cache'

// 稳定的 mock：statsApi 方法与 ApiError 类在 vi.resetModules 后保持同一引用
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
    statsApi: {
      get: vi.fn(),
      post: vi.fn(),
    },
  }
})

vi.mock('@/lib/http', () => ({
  ApiError: httpMocks.ApiError,
  statsApi: httpMocks.statsApi,
}))

async function loadPluginStats() {
  return await import('../plugin-stats')
}

/** 向 localStorage 写入一份新鲜的统计摘要缓存 */
function seedSummaryCache(data: Record<string, unknown>): void {
  localStorage.setItem(SUMMARY_STORAGE_KEY, JSON.stringify({ timestamp: Date.now(), data }))
}

describe('plugin-stats', () => {
  beforeEach(() => {
    vi.resetModules()
    localStorage.clear()
    httpMocks.statsApi.get.mockReset()
    httpMocks.statsApi.post.mockReset()
    vi.spyOn(console, 'error').mockImplementation(() => {})
    vi.spyOn(console, 'warn').mockImplementation(() => {})
  })

  describe('getPluginStats', () => {
    it('归一化 stats 包装响应并补齐缺省数值', async () => {
      httpMocks.statsApi.get.mockResolvedValue({
        stats: { plugin_id: 'plugin-a', likes: 7, rating: 4.5 },
      })
      const stats = await loadPluginStats()

      await expect(stats.getPluginStats('plugin-a')).resolves.toEqual({
        plugin_id: 'plugin-a',
        likes: 7,
        dislikes: 0,
        downloads: 0,
        rating: 4.5,
        rating_count: 0,
        recent_ratings: undefined,
      })
      expect(httpMocks.statsApi.get).toHaveBeenCalledWith('/stats/plugin-a', {
        errorMessage: '获取插件统计失败',
      })
    })

    it('响应不是对象或请求失败时抛出', async () => {
      httpMocks.statsApi.get.mockResolvedValueOnce(null)
      const stats = await loadPluginStats()
      await expect(stats.getPluginStats('plugin-a')).rejects.toMatchObject({
        name: 'ApiError',
        message: '插件统计响应格式无效',
      })

      const networkError = new Error('网络错误')
      httpMocks.statsApi.get.mockRejectedValueOnce(networkError)
      await expect(stats.getPluginStats('plugin-a')).rejects.toBe(networkError)
    })
  })

  describe('getPluginStatsSummary', () => {
    it('拉取摘要并写入 localStorage 缓存，TTL 内不重复请求', async () => {
      httpMocks.statsApi.get.mockResolvedValue({
        success: true,
        stats: { 'plugin-a': { likes: 3 } },
      })
      const stats = await loadPluginStats()

      const summary = await stats.getPluginStatsSummary()
      expect(summary['plugin-a']).toMatchObject({ plugin_id: 'plugin-a', likes: 3 })

      await stats.getPluginStatsSummary()
      expect(httpMocks.statsApi.get).toHaveBeenCalledTimes(1)
      expect(httpMocks.statsApi.get).toHaveBeenCalledWith('/stats/summary', {
        errorMessage: '获取插件统计摘要失败',
      })

      const storedRaw = localStorage.getItem(SUMMARY_STORAGE_KEY)
      expect(storedRaw).not.toBeNull()
      const stored = JSON.parse(storedRaw as string) as {
        data: Record<string, { likes: number }>
      }
      expect(stored.data['plugin-a'].likes).toBe(3)
    })

    it('forceRefresh 跳过缓存重新请求', async () => {
      httpMocks.statsApi.get.mockResolvedValue({ success: true, stats: {} })
      const stats = await loadPluginStats()

      await stats.getPluginStatsSummary()
      await stats.getPluginStatsSummary({ forceRefresh: true })
      expect(httpMocks.statsApi.get).toHaveBeenCalledTimes(2)
    })

    it('命中新鲜的 localStorage 缓存时不发请求', async () => {
      seedSummaryCache({ 'plugin-b': { likes: 9 } })
      const stats = await loadPluginStats()

      const summary = await stats.getPluginStatsSummary()
      expect(summary['plugin-b']).toMatchObject({ plugin_id: 'plugin-b', likes: 9 })
      expect(httpMocks.statsApi.get).not.toHaveBeenCalled()
    })

    it('后端返回 success=false 或抛错时向上抛出，不写入空缓存', async () => {
      httpMocks.statsApi.get.mockResolvedValueOnce({ success: false, error: '服务不可用' })
      const stats = await loadPluginStats()
      await expect(stats.getPluginStatsSummary()).rejects.toMatchObject({
        name: 'ApiError',
        message: '服务不可用',
      })
      expect(localStorage.getItem(SUMMARY_STORAGE_KEY)).toBeNull()

      httpMocks.statsApi.get.mockRejectedValueOnce(new Error('boom'))
      await expect(stats.getPluginStatsSummary({ forceRefresh: true })).rejects.toThrow('boom')
      expect(localStorage.getItem(SUMMARY_STORAGE_KEY)).toBeNull()
    })
  })

  describe('getCachedPluginStatsSummary', () => {
    it('无任何缓存时返回 null，有 localStorage 缓存时归一化返回', async () => {
      const statsEmpty = await loadPluginStats()
      expect(statsEmpty.getCachedPluginStatsSummary()).toBeNull()

      vi.resetModules()
      seedSummaryCache({ 'plugin-c': { likes: 1, downloads: 5 } })
      const stats = await loadPluginStats()
      expect(stats.getCachedPluginStatsSummary()).toEqual({
        'plugin-c': {
          plugin_id: 'plugin-c',
          likes: 1,
          dislikes: 0,
          downloads: 5,
          rating: 0,
          rating_count: 0,
          recent_ratings: undefined,
        },
      })
    })
  })

  describe('likePlugin / dislikePlugin', () => {
    it('点赞成功后同步更新本地统计缓存', async () => {
      seedSummaryCache({ 'plugin-a': { likes: 1, dislikes: 0 } })
      httpMocks.statsApi.post.mockResolvedValue({ likes: 2, dislikes: 0, liked: true })
      const stats = await loadPluginStats()

      const result = await stats.likePlugin('plugin-a', 'user-1')
      expect(result).toEqual({ success: true, likes: 2, dislikes: 0, liked: true })
      expect(httpMocks.statsApi.post).toHaveBeenCalledWith('/stats/like', {
        body: { plugin_id: 'plugin-a', user_id: 'user-1' },
        errorMessage: '点赞失败',
      })

      const cached = stats.getCachedPluginStatsSummary()
      expect(cached?.['plugin-a'].likes).toBe(2)
    })

    it('429 抛出固定的限频文案', async () => {
      httpMocks.statsApi.post.mockRejectedValue(new httpMocks.ApiError('限频', { status: 429 }))
      const stats = await loadPluginStats()

      await expect(stats.likePlugin('plugin-a', 'user-1')).rejects.toMatchObject({
        name: 'ApiError',
        message: '点赞过于频繁，请稍后再试',
        status: 429,
      })
      await expect(stats.dislikePlugin('plugin-a', 'user-1')).rejects.toMatchObject({
        name: 'ApiError',
        message: '操作过于频繁，请稍后再试',
        status: 429,
      })
    })

    it('HTTP 错误直接抛出 ApiError', async () => {
      const error = new httpMocks.ApiError('插件不存在', {
        status: 400,
        detail: { error: '插件不存在' },
      })
      httpMocks.statsApi.post.mockRejectedValueOnce(error)
      const stats = await loadPluginStats()
      await expect(stats.likePlugin('plugin-a', 'user-1')).rejects.toBe(error)
    })

    it('网络层失败向上抛出', async () => {
      const networkError = new Error('offline')
      httpMocks.statsApi.post.mockRejectedValue(networkError)
      const stats = await loadPluginStats()

      await expect(stats.likePlugin('plugin-a', 'user-1')).rejects.toBe(networkError)
    })
  })

  describe('ratePlugin', () => {
    it('评分与评论都缺失时直接拒绝', async () => {
      const stats = await loadPluginStats()
      await expect(stats.ratePlugin('plugin-a')).resolves.toEqual({
        success: false,
        error: '评分和评论至少需要填写一项',
      })
      expect(httpMocks.statsApi.post).not.toHaveBeenCalled()
    })

    it('评分超出 1-5 范围时拒绝', async () => {
      const stats = await loadPluginStats()
      await expect(stats.ratePlugin('plugin-a', 0)).resolves.toEqual({
        success: false,
        error: '评分必须在 1-5 之间',
      })
      await expect(stats.ratePlugin('plugin-a', 6)).resolves.toEqual({
        success: false,
        error: '评分必须在 1-5 之间',
      })
    })

    it('提交评分与评论并用响应更新缓存', async () => {
      seedSummaryCache({ 'plugin-a': { rating: 3, rating_count: 1 } })
      httpMocks.statsApi.post.mockResolvedValue({ rating: 4.2, rating_count: 2 })
      const stats = await loadPluginStats()

      const result = await stats.ratePlugin('plugin-a', 5, '很好用', 'user-1')
      expect(result).toEqual({ success: true, rating: 4.2, rating_count: 2 })
      expect(httpMocks.statsApi.post).toHaveBeenCalledWith('/stats/rate', {
        body: { plugin_id: 'plugin-a', user_id: 'user-1', rating: 5, comment: '很好用' },
        errorMessage: '评分失败',
      })

      const cached = stats.getCachedPluginStatsSummary()
      expect(cached?.['plugin-a']).toMatchObject({ rating: 4.2, rating_count: 2 })
    })

    it('仅提交评论时载荷不带 rating 字段', async () => {
      httpMocks.statsApi.post.mockResolvedValue({})
      const stats = await loadPluginStats()

      await stats.ratePlugin('plugin-a', null, '只有评论', 'user-1')
      expect(httpMocks.statsApi.post).toHaveBeenCalledWith('/stats/rate', {
        body: { plugin_id: 'plugin-a', user_id: 'user-1', comment: '只有评论' },
        errorMessage: '评分失败',
      })
    })

    it('429 抛出每日限次文案，其它 HTTP 错误原样抛出', async () => {
      httpMocks.statsApi.post.mockRejectedValueOnce(new httpMocks.ApiError('限频', { status: 429 }))
      const stats = await loadPluginStats()
      await expect(stats.ratePlugin('plugin-a', 4, null, 'user-1')).rejects.toMatchObject({
        name: 'ApiError',
        message: '每天最多评分 3 次',
        status: 429,
      })

      const error = new httpMocks.ApiError('评论包含敏感词', {
        status: 400,
        detail: { error: '评论包含敏感词' },
      })
      httpMocks.statsApi.post.mockRejectedValueOnce(error)
      await expect(stats.ratePlugin('plugin-a', 4, null, 'user-1')).rejects.toBe(error)
    })
  })

  describe('recordPluginDownload', () => {
    it('记录成功后更新缓存中的下载数', async () => {
      seedSummaryCache({ 'plugin-a': { downloads: 10 } })
      httpMocks.statsApi.post.mockResolvedValue({ counted: true, downloads: 11 })
      const stats = await loadPluginStats()

      const result = await stats.recordPluginDownload('plugin-a')
      expect(result).toEqual({ success: true, counted: true, downloads: 11 })

      const [, options] = httpMocks.statsApi.post.mock.calls[0] as [
        string,
        { body: Record<string, unknown> },
      ]
      expect(httpMocks.statsApi.post.mock.calls[0][0]).toBe('/stats/download')
      expect(options.body.plugin_id).toBe('plugin-a')
      expect(typeof options.body.user_id).toBe('string')
      expect(String(options.body.fingerprint)).toMatch(/^fp_/)

      expect(stats.getCachedPluginStatsSummary()?.['plugin-a'].downloads).toBe(11)
    })

    it('HTTP 错误与网络失败均向上抛出', async () => {
      const httpError = new httpMocks.ApiError('统计服务异常', {
        status: 500,
        detail: { error: '统计服务异常' },
      })
      httpMocks.statsApi.post.mockRejectedValueOnce(httpError)
      const stats = await loadPluginStats()
      await expect(stats.recordPluginDownload('plugin-a')).rejects.toBe(httpError)

      const networkError = new Error('offline')
      httpMocks.statsApi.post.mockRejectedValueOnce(networkError)
      await expect(stats.recordPluginDownload('plugin-a')).rejects.toBe(networkError)
    })
  })

  describe('getPluginUserState', () => {
    it('归一化用户状态字段', async () => {
      httpMocks.statsApi.get.mockResolvedValue({
        success: true,
        liked: true,
        disliked: false,
        rating: 4,
      })
      const stats = await loadPluginStats()

      await expect(stats.getPluginUserState('plugin-a', 'user-1')).resolves.toEqual({
        liked: true,
        disliked: false,
        rating: 4,
        comment: '',
      })
      expect(httpMocks.statsApi.get).toHaveBeenCalledWith('/stats/user-state', {
        query: { plugin_id: 'plugin-a', user_id: 'user-1' },
        errorMessage: '获取插件用户状态失败',
      })
    })

    it('success=false 或请求失败时抛出', async () => {
      httpMocks.statsApi.get.mockResolvedValueOnce({ success: false })
      const stats = await loadPluginStats()
      await expect(stats.getPluginUserState('plugin-a', 'user-1')).rejects.toMatchObject({
        name: 'ApiError',
        message: '获取插件用户状态失败',
      })

      const boom = new Error('boom')
      httpMocks.statsApi.get.mockRejectedValueOnce(boom)
      await expect(stats.getPluginUserState('plugin-a', 'user-1')).rejects.toBe(boom)
    })
  })

  describe('用户标识', () => {
    it('getUserId 首次生成并持久化，后续调用保持一致', async () => {
      const stats = await loadPluginStats()

      const first = stats.getUserId()
      expect(first).toMatch(/^fp_/)
      expect(localStorage.getItem('maibot_user_id')).toBe(first)
      expect(stats.getUserId()).toBe(first)
    })

    it('generateUserFingerprint 生成稳定的 fp_ 前缀指纹', async () => {
      const stats = await loadPluginStats()

      const fingerprint = stats.generateUserFingerprint()
      expect(fingerprint).toMatch(/^fp_[0-9a-z]+$/)
      expect(stats.generateUserFingerprint()).toBe(fingerprint)
    })
  })
})
