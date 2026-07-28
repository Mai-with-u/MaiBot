import { beforeEach, describe, expect, it, vi } from 'vitest'

const SUMMARY_STORAGE_KEY = 'maibot-plugin-stats-summary-cache'

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

vi.mock('@/lib/http', () => ({
  ApiError: httpMocks.ApiError,
  backendApi: httpMocks.backendApi,
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
    // 模块内存缓存 + localStorage 缓存都要清干净
    vi.resetModules()
    localStorage.clear()
    vi.spyOn(console, 'error').mockImplementation(() => {})
    vi.spyOn(console, 'warn').mockImplementation(() => {})
  })

  describe('getPluginStats', () => {
    it('归一化 stats 包装响应并补齐缺省数值', async () => {
      httpMocks.backendApi.get.mockResolvedValue({
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
      expect(httpMocks.backendApi.get).toHaveBeenCalledWith(
        '/api/webui/plugins/stats-proxy/stats/plugin-a'
      )
    })

    it('响应不是对象或请求失败时返回 null', async () => {
      httpMocks.backendApi.get.mockResolvedValueOnce(null)
      const stats = await loadPluginStats()
      await expect(stats.getPluginStats('plugin-a')).resolves.toBeNull()

      httpMocks.backendApi.get.mockRejectedValueOnce(new Error('网络错误'))
      await expect(stats.getPluginStats('plugin-a')).resolves.toBeNull()
    })
  })

  describe('getPluginStatsSummary', () => {
    it('拉取摘要并写入内存与 localStorage 缓存，TTL 内不重复请求', async () => {
      httpMocks.backendApi.get.mockResolvedValue({
        success: true,
        stats: { 'plugin-a': { likes: 3 } },
      })
      const stats = await loadPluginStats()

      const summary = await stats.getPluginStatsSummary()
      expect(summary['plugin-a']).toMatchObject({ plugin_id: 'plugin-a', likes: 3 })

      await stats.getPluginStatsSummary()
      expect(httpMocks.backendApi.get).toHaveBeenCalledTimes(1)

      const storedRaw = localStorage.getItem(SUMMARY_STORAGE_KEY)
      expect(storedRaw).not.toBeNull()
      const stored = JSON.parse(storedRaw as string) as {
        data: Record<string, { likes: number }>
      }
      expect(stored.data['plugin-a'].likes).toBe(3)
    })

    it('forceRefresh 跳过缓存重新请求', async () => {
      httpMocks.backendApi.get.mockResolvedValue({ success: true, stats: {} })
      const stats = await loadPluginStats()

      await stats.getPluginStatsSummary()
      await stats.getPluginStatsSummary({ forceRefresh: true })
      expect(httpMocks.backendApi.get).toHaveBeenCalledTimes(2)
    })

    it('内存缓存为空时命中新鲜的 localStorage 缓存', async () => {
      seedSummaryCache({ 'plugin-b': { likes: 9 } })
      const stats = await loadPluginStats()

      const summary = await stats.getPluginStatsSummary()
      expect(summary['plugin-b']).toMatchObject({ plugin_id: 'plugin-b', likes: 9 })
      expect(httpMocks.backendApi.get).not.toHaveBeenCalled()
    })

    it('后端返回 success=false 或抛错时返回空对象', async () => {
      httpMocks.backendApi.get.mockResolvedValueOnce({ success: false, error: '服务不可用' })
      const stats = await loadPluginStats()
      await expect(stats.getPluginStatsSummary()).resolves.toEqual({})

      httpMocks.backendApi.get.mockRejectedValueOnce(new Error('boom'))
      await expect(stats.getPluginStatsSummary({ forceRefresh: true })).resolves.toEqual({})
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
      httpMocks.backendApi.post.mockResolvedValue({ likes: 2, dislikes: 0, liked: true })
      const stats = await loadPluginStats()

      const result = await stats.likePlugin('plugin-a', 'user-1')
      expect(result).toEqual({ success: true, likes: 2, dislikes: 0, liked: true })
      expect(httpMocks.backendApi.post).toHaveBeenCalledWith(
        '/api/webui/plugins/stats-proxy/stats/like',
        { body: { plugin_id: 'plugin-a', user_id: 'user-1' } }
      )

      const cached = stats.getCachedPluginStatsSummary()
      expect(cached?.['plugin-a'].likes).toBe(2)
    })

    it('429 返回固定的限频文案', async () => {
      httpMocks.backendApi.post.mockRejectedValue(new httpMocks.ApiError('限频', { status: 429 }))
      const stats = await loadPluginStats()

      await expect(stats.likePlugin('plugin-a', 'user-1')).resolves.toEqual({
        success: false,
        error: '点赞过于频繁，请稍后再试',
      })
      await expect(stats.dislikePlugin('plugin-a', 'user-1')).resolves.toEqual({
        success: false,
        error: '操作过于频繁，请稍后再试',
      })
    })

    it('HTTP 错误优先展示后端可读错误，乱码错误回落默认文案', async () => {
      httpMocks.backendApi.post.mockRejectedValueOnce(
        new httpMocks.ApiError('请求失败', { status: 400, detail: { error: '插件不存在' } })
      )
      const stats = await loadPluginStats()
      await expect(stats.likePlugin('plugin-a', 'user-1')).resolves.toEqual({
        success: false,
        error: '插件不存在',
      })

      httpMocks.backendApi.post.mockRejectedValueOnce(
        new httpMocks.ApiError('请求失败', { status: 400, detail: { error: '閹绘帊娆�' } })
      )
      await expect(stats.likePlugin('plugin-a', 'user-1')).resolves.toEqual({
        success: false,
        error: '点赞失败',
      })
    })

    it('网络层失败返回网络错误文案', async () => {
      httpMocks.backendApi.post.mockRejectedValue(new Error('offline'))
      const stats = await loadPluginStats()

      await expect(stats.likePlugin('plugin-a', 'user-1')).resolves.toEqual({
        success: false,
        error: '网络请求失败',
      })
    })
  })

  describe('ratePlugin', () => {
    it('评分与评论都缺失时直接拒绝', async () => {
      const stats = await loadPluginStats()
      await expect(stats.ratePlugin('plugin-a')).resolves.toEqual({
        success: false,
        error: '评分和评论至少需要填写一项',
      })
      expect(httpMocks.backendApi.post).not.toHaveBeenCalled()
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
      httpMocks.backendApi.post.mockResolvedValue({ rating: 4.2, rating_count: 2 })
      const stats = await loadPluginStats()

      const result = await stats.ratePlugin('plugin-a', 5, '很好用', 'user-1')
      expect(result).toEqual({ success: true, rating: 4.2, rating_count: 2 })
      expect(httpMocks.backendApi.post).toHaveBeenCalledWith(
        '/api/webui/plugins/stats-proxy/stats/rate',
        { body: { plugin_id: 'plugin-a', user_id: 'user-1', rating: 5, comment: '很好用' } }
      )

      const cached = stats.getCachedPluginStatsSummary()
      expect(cached?.['plugin-a']).toMatchObject({ rating: 4.2, rating_count: 2 })
    })

    it('仅提交评论时载荷不带 rating 字段', async () => {
      httpMocks.backendApi.post.mockResolvedValue({})
      const stats = await loadPluginStats()

      await stats.ratePlugin('plugin-a', null, '只有评论', 'user-1')
      expect(httpMocks.backendApi.post).toHaveBeenCalledWith(
        '/api/webui/plugins/stats-proxy/stats/rate',
        { body: { plugin_id: 'plugin-a', user_id: 'user-1', comment: '只有评论' } }
      )
    })

    it('429 返回每日限次文案，HTTP 错误透传后端 error 字段', async () => {
      httpMocks.backendApi.post.mockRejectedValueOnce(
        new httpMocks.ApiError('限频', { status: 429 })
      )
      const stats = await loadPluginStats()
      await expect(stats.ratePlugin('plugin-a', 4, null, 'user-1')).resolves.toEqual({
        success: false,
        error: '每天最多评分 3 次',
      })

      httpMocks.backendApi.post.mockRejectedValueOnce(
        new httpMocks.ApiError('请求失败', { status: 400, detail: { error: '评论包含敏感词' } })
      )
      await expect(stats.ratePlugin('plugin-a', 4, null, 'user-1')).resolves.toEqual({
        success: false,
        error: '评论包含敏感词',
      })
    })
  })

  describe('recordPluginDownload', () => {
    it('记录成功后更新缓存中的下载数', async () => {
      seedSummaryCache({ 'plugin-a': { downloads: 10 } })
      httpMocks.backendApi.post.mockResolvedValue({ counted: true, downloads: 11 })
      const stats = await loadPluginStats()

      const result = await stats.recordPluginDownload('plugin-a')
      expect(result).toEqual({ success: true, counted: true, downloads: 11 })

      const [, options] = httpMocks.backendApi.post.mock.calls[0] as [
        string,
        { body: Record<string, unknown> },
      ]
      expect(options.body.plugin_id).toBe('plugin-a')
      expect(typeof options.body.user_id).toBe('string')
      expect(String(options.body.fingerprint)).toMatch(/^fp_/)

      expect(stats.getCachedPluginStatsSummary()?.['plugin-a'].downloads).toBe(11)
    })

    it('429 限频不阻断下载流程，视为成功', async () => {
      httpMocks.backendApi.post.mockRejectedValue(new httpMocks.ApiError('限频', { status: 429 }))
      const stats = await loadPluginStats()

      await expect(stats.recordPluginDownload('plugin-a')).resolves.toEqual({ success: true })
    })

    it('HTTP 错误返回后端错误，网络失败返回网络文案', async () => {
      httpMocks.backendApi.post.mockRejectedValueOnce(
        new httpMocks.ApiError('请求失败', { status: 500, detail: { error: '统计服务异常' } })
      )
      const stats = await loadPluginStats()
      await expect(stats.recordPluginDownload('plugin-a')).resolves.toEqual({
        success: false,
        error: '统计服务异常',
      })

      httpMocks.backendApi.post.mockRejectedValueOnce(new Error('offline'))
      await expect(stats.recordPluginDownload('plugin-a')).resolves.toEqual({
        success: false,
        error: '网络请求失败',
      })
    })
  })

  describe('getPluginUserState', () => {
    it('归一化用户状态字段', async () => {
      httpMocks.backendApi.get.mockResolvedValue({
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
      expect(httpMocks.backendApi.get).toHaveBeenCalledWith(
        '/api/webui/plugins/stats-proxy/stats/user-state',
        { query: { plugin_id: 'plugin-a', user_id: 'user-1' } }
      )
    })

    it('success=false 或请求失败时返回 null', async () => {
      httpMocks.backendApi.get.mockResolvedValueOnce({ success: false })
      const stats = await loadPluginStats()
      await expect(stats.getPluginUserState('plugin-a', 'user-1')).resolves.toBeNull()

      httpMocks.backendApi.get.mockRejectedValueOnce(new Error('boom'))
      await expect(stats.getPluginUserState('plugin-a', 'user-1')).resolves.toBeNull()
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
