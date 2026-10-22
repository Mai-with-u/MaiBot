import { beforeEach, describe, expect, it, vi } from 'vitest'

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

const invalidateMarket = vi.hoisted(() => vi.fn())
vi.mock('@/lib/plugin-api/marketplace', () => ({ invalidatePluginMarketCache: invalidateMarket }))

describe('公共市场统计与互动', () => {
  beforeEach(() => {
    vi.resetModules()
    vi.clearAllMocks()
    httpMocks.backendApi.get.mockReset()
    httpMocks.backendApi.post.mockReset()
    localStorage.clear()
    vi.spyOn(console, 'error').mockImplementation(() => {})
  })

  it('读取公共统计，保留缺失状态并直接暴露请求失败', async () => {
    const stats = await loadPluginStats()
    const value = { plugin_id: 'a', likes: 2, recent_ratings: [] }
    httpMocks.backendApi.get.mockResolvedValueOnce({ stats: value }).mockResolvedValueOnce({ stats: null })
    expect(await stats.getPluginStats('a')).toEqual(value)
    expect(await stats.getPluginStats('a')).toBeNull()
    expect(httpMocks.backendApi.get).toHaveBeenCalledWith('/api/webui/plugins/marketplace/a/stats')
    httpMocks.backendApi.get.mockRejectedValueOnce(new Error('offline'))
    await expect(stats.getPluginStats('a')).rejects.toThrow('offline')
  })

  it('点赞成功后失效唯一的市场列表缓存', async () => {
    const stats = await loadPluginStats()
    httpMocks.backendApi.post.mockResolvedValue({ likes: 3, liked: true })
    expect(await stats.likePlugin('a', 'user')).toMatchObject({ success: true, likes: 3 })
    expect(httpMocks.backendApi.post).toHaveBeenCalledWith('/api/webui/plugins/marketplace/like', {
      body: { plugin_id: 'a', user_id: 'user' },
    })
    expect(invalidateMarket).toHaveBeenCalledTimes(1)
  })

  it('评分与匿名评论使用公共互动接口', async () => {
    const stats = await loadPluginStats()
    httpMocks.backendApi.post.mockResolvedValue({ rating: 4 })
    expect(await stats.ratePlugin('a', 5, '好用', 'user', { username: '不应发送', anonymous: true }))
      .toMatchObject({ success: true, rating: 4 })
    expect(httpMocks.backendApi.post).toHaveBeenCalledWith('/api/webui/plugins/marketplace/rate', {
      body: { plugin_id: 'a', user_id: 'user', rating: 5, comment: '好用', anonymous: true },
    })
    expect(invalidateMarket).toHaveBeenCalledTimes(1)
    expect((await stats.ratePlugin('a', 8, undefined, 'user')).success).toBe(false)
  })

  it('用户状态与默认身份也从公共市场入口读取', async () => {
    const stats = await loadPluginStats()
    httpMocks.backendApi.get.mockResolvedValue({ liked: true, rating: 5, comment: '好用' })
    expect(await stats.getPluginUserState('a', 'user')).toMatchObject({ liked: true, rating: 5 })
    expect(httpMocks.backendApi.get).toHaveBeenLastCalledWith('/api/webui/plugins/marketplace/a/user-state', {
      query: { user_id: 'user' },
    })
    httpMocks.backendApi.get.mockResolvedValue({ username: '麦麦' })
    expect(await stats.getPluginReviewIdentity()).toEqual({ username: '麦麦' })
  })

  it('下载限流保留成功语义，其他上游错误保留错误信息', async () => {
    const stats = await loadPluginStats()
    httpMocks.backendApi.post.mockRejectedValueOnce(new httpMocks.ApiError('limited', { status: 429 }))
    expect(await stats.recordPluginDownload('a')).toEqual({ success: true })
    httpMocks.backendApi.post.mockRejectedValueOnce(new httpMocks.ApiError('bad', {
      status: 503, detail: { error: 'upstream unavailable' },
    }))
    expect(await stats.likePlugin('a', 'user')).toMatchObject({ success: false, error: 'upstream unavailable' })
  })
})
