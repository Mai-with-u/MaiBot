/**
 * 插件统计 API
 *
 * 与 Cloudflare Workers 上的统计服务（statsApi 实例，无 Cookie 认证）交互。
 * 请求样板（解析、错误格式化）由 @/lib/http 的请求客户端承担；
 * 本文件只声明 endpoint、业务错误文案与统计响应的归一化规则。
 */
import { ApiError, statsApi } from '@/lib/http'

const PLUGIN_STATS_SUMMARY_CACHE_TTL = 5 * 60 * 1000
const PLUGIN_STATS_SUMMARY_STORAGE_KEY = 'maibot-plugin-stats-summary-cache'

let pluginStatsSummaryRequest: Promise<Record<string, PluginStatsData>> | null = null

export interface PluginStatsData {
  plugin_id: string
  likes: number
  dislikes: number
  liked?: boolean
  disliked?: boolean
  downloads: number
  rating: number
  rating_count: number
  recent_ratings?: Array<{
    user_id: string
    rating?: number | null
    comment?: string
    created_at: string
  }>
}

export interface StatsResponse {
  success: boolean
  error?: string
  remaining?: number
  [key: string]: unknown
}

export interface VoteStatsResponse extends StatsResponse {
  liked?: boolean
  disliked?: boolean
  likes?: number
  dislikes?: number
}

export interface RatingStatsResponse extends StatsResponse {
  user_rating?: number | null
  user_comment?: string | null
  comment?: string | null
  rating?: number
  rating_count?: number
}

export interface DownloadStatsResponse extends StatsResponse {
  counted?: boolean
  downloads?: number
}

export interface PluginUserState {
  liked: boolean
  disliked: boolean
  rating: number | null
  comment: string
}

interface PluginStatsSummaryResponse {
  success?: boolean
  stats?: Record<string, Partial<PluginStatsData>>
  error?: string
}

interface PluginStatsSummaryStorageCache {
  timestamp: number
  data: Record<string, PluginStatsData>
}

function createEmptyStats(pluginId: string): PluginStatsData {
  return {
    plugin_id: pluginId,
    likes: 0,
    dislikes: 0,
    downloads: 0,
    rating: 0,
    rating_count: 0,
  }
}

function normalizePluginStatsResponse(data: unknown, pluginId: string): PluginStatsData | null {
  if (!data || typeof data !== 'object') {
    return null
  }

  const response = data as Partial<PluginStatsData> & {
    stats?: Partial<PluginStatsData>
  }
  const stats = response.stats ?? response

  return {
    ...createEmptyStats(pluginId),
    ...stats,
    plugin_id: String(stats.plugin_id ?? pluginId),
    likes: Number(stats.likes ?? 0),
    dislikes: Number(stats.dislikes ?? 0),
    downloads: Number(stats.downloads ?? 0),
    rating: Number(stats.rating ?? 0),
    rating_count: Number(stats.rating_count ?? 0),
    recent_ratings: Array.isArray(stats.recent_ratings) ? stats.recent_ratings : undefined,
  }
}

function readPluginStatsSummaryStorageCache(): PluginStatsSummaryStorageCache | null {
  if (typeof localStorage === 'undefined') {
    return null
  }

  try {
    const rawCache = localStorage.getItem(PLUGIN_STATS_SUMMARY_STORAGE_KEY)
    if (!rawCache) {
      return null
    }

    const cache = JSON.parse(rawCache) as Partial<PluginStatsSummaryStorageCache>
    if (!cache.timestamp || !cache.data || typeof cache.data !== 'object') {
      return null
    }

    return {
      timestamp: Number(cache.timestamp),
      data: Object.fromEntries(
        Object.entries(cache.data).map(([pluginId, stats]) => [
          pluginId,
          normalizePluginStatsResponse(stats, pluginId) ?? createEmptyStats(pluginId),
        ])
      ),
    }
  } catch (error) {
    console.warn('读取插件统计缓存失败:', error)
    return null
  }
}

function writePluginStatsSummaryStorageCache(data: Record<string, PluginStatsData>): void {
  if (typeof localStorage === 'undefined') {
    return
  }

  try {
    localStorage.setItem(
      PLUGIN_STATS_SUMMARY_STORAGE_KEY,
      JSON.stringify({
        timestamp: Date.now(),
        data,
      })
    )
  } catch (error) {
    console.warn('写入插件统计缓存失败:', error)
  }
}

function updateCachedPluginStats(pluginId: string, partialStats: Partial<PluginStatsData>): void {
  const currentCache = readPluginStatsSummaryStorageCache()
  if (!currentCache) {
    return
  }

  const currentStats = currentCache.data[pluginId] ?? createEmptyStats(pluginId)
  const nextData = {
    ...currentCache.data,
    [pluginId]:
      normalizePluginStatsResponse(
        {
          ...currentStats,
          ...partialStats,
          plugin_id: pluginId,
        },
        pluginId
      ) ?? currentStats,
  }

  writePluginStatsSummaryStorageCache(nextData)
}

function remapRateLimitError(error: unknown, message: string): never {
  if (error instanceof ApiError && error.status === 429) {
    throw new ApiError(message, { status: 429, detail: error.detail })
  }
  throw error
}

export function getCachedPluginStatsSummary(): Record<string, PluginStatsData> | null {
  const storedCache = readPluginStatsSummaryStorageCache()
  return storedCache?.data ?? null
}

/**
 * 获取单个插件的统计数据
 */
export async function getPluginStats(pluginId: string): Promise<PluginStatsData> {
  const data = await statsApi.get<unknown>(`/stats/${encodeURIComponent(pluginId)}`, {
    errorMessage: '获取插件统计失败',
  })
  const stats = normalizePluginStatsResponse(data, pluginId)
  if (!stats) {
    throw new ApiError('插件统计响应格式无效', { detail: data })
  }
  return stats
}

/**
 * 拉取插件统计摘要（绕过本地短缓存）
 */
async function fetchPluginStatsSummaryUncached(): Promise<Record<string, PluginStatsData>> {
  const data = await statsApi.get<PluginStatsSummaryResponse>('/stats/summary', {
    errorMessage: '获取插件统计摘要失败',
  })
  if (!data.success || !data.stats || typeof data.stats !== 'object') {
    throw new ApiError(
      typeof data.error === 'string' && data.error.trim() ? data.error : '获取插件统计摘要失败',
      { detail: data }
    )
  }

  return Object.fromEntries(
    Object.entries(data.stats).map(([pluginId, stats]) => [
      pluginId,
      normalizePluginStatsResponse({ stats }, pluginId) ?? createEmptyStats(pluginId),
    ])
  )
}

export async function getPluginUserState(
  pluginId: string,
  userId: string = getUserId()
): Promise<PluginUserState> {
  const data = await statsApi.get<Partial<PluginUserState> & { success?: boolean }>(
    '/stats/user-state',
    {
      query: { plugin_id: pluginId, user_id: userId },
      errorMessage: '获取插件用户状态失败',
    }
  )
  if (data.success === false) {
    throw new ApiError('获取插件用户状态失败', { detail: data })
  }

  return {
    liked: data.liked === true,
    disliked: data.disliked === true,
    rating: data.rating == null ? null : Number(data.rating),
    comment: typeof data.comment === 'string' ? data.comment : '',
  }
}

export async function getPluginStatsSummary(
  options: { forceRefresh?: boolean } = {}
): Promise<Record<string, PluginStatsData>> {
  if (!options.forceRefresh) {
    const storedCache = readPluginStatsSummaryStorageCache()
    if (storedCache && Date.now() - storedCache.timestamp < PLUGIN_STATS_SUMMARY_CACHE_TTL) {
      return storedCache.data
    }
  }

  if (!pluginStatsSummaryRequest || options.forceRefresh) {
    pluginStatsSummaryRequest = fetchPluginStatsSummaryUncached()
      .then((data) => {
        writePluginStatsSummaryStorageCache(data)
        return data
      })
      .finally(() => {
        pluginStatsSummaryRequest = null
      })
  }

  return pluginStatsSummaryRequest
}

/**
 * 点赞插件
 */
export async function likePlugin(pluginId: string, userId?: string): Promise<VoteStatsResponse> {
  const finalUserId = userId || getUserId()

  try {
    const data = await statsApi.post<Omit<VoteStatsResponse, 'success'>>('/stats/like', {
      body: { plugin_id: pluginId, user_id: finalUserId },
      errorMessage: '点赞失败',
    })

    const result: VoteStatsResponse = { success: true, ...data }
    updateCachedPluginStats(pluginId, {
      likes: Number(result.likes ?? 0),
      dislikes: Number(result.dislikes ?? 0),
    })
    return result
  } catch (error) {
    remapRateLimitError(error, '点赞过于频繁，请稍后再试')
  }
}

/**
 * 点踩插件
 */
export async function dislikePlugin(pluginId: string, userId?: string): Promise<VoteStatsResponse> {
  const finalUserId = userId || getUserId()

  try {
    const data = await statsApi.post<Omit<VoteStatsResponse, 'success'>>('/stats/dislike', {
      body: { plugin_id: pluginId, user_id: finalUserId },
      errorMessage: '点踩失败',
    })

    const result: VoteStatsResponse = { success: true, ...data }
    updateCachedPluginStats(pluginId, {
      likes: Number(result.likes ?? 0),
      dislikes: Number(result.dislikes ?? 0),
    })
    return result
  } catch (error) {
    remapRateLimitError(error, '操作过于频繁，请稍后再试')
  }
}

/**
 * 提交插件评分或评论
 */
export async function ratePlugin(
  pluginId: string,
  rating?: number | null,
  comment?: string | null,
  userId?: string
): Promise<RatingStatsResponse> {
  const hasRating = rating !== undefined && rating !== null
  const hasComment = comment !== undefined

  if (!hasRating && !hasComment) {
    return { success: false, error: '评分和评论至少需要填写一项' }
  }

  if (hasRating && (rating < 1 || rating > 5)) {
    return { success: false, error: '评分必须在 1-5 之间' }
  }

  const finalUserId = userId || getUserId()
  const payload: {
    plugin_id: string
    user_id: string
    rating?: number
    comment?: string | null
  } = { plugin_id: pluginId, user_id: finalUserId }

  if (hasRating) {
    payload.rating = Number(rating)
  }
  if (hasComment) {
    payload.comment = comment
  }

  try {
    const data = await statsApi.post<Omit<RatingStatsResponse, 'success'>>('/stats/rate', {
      body: payload,
      errorMessage: '评分失败',
    })

    const result: RatingStatsResponse = { success: true, ...data }
    const updatedStats: Partial<PluginStatsData> = {}
    if (result.rating !== undefined) {
      updatedStats.rating = Number(result.rating)
    }
    if (result.rating_count !== undefined) {
      updatedStats.rating_count = Number(result.rating_count)
    }
    updateCachedPluginStats(pluginId, updatedStats)
    return result
  } catch (error) {
    remapRateLimitError(error, '每天最多评分 3 次')
  }
}

/**
 * 记录插件下载
 */
export async function recordPluginDownload(pluginId: string): Promise<DownloadStatsResponse> {
  const userId = getUserId()
  const fingerprint = generateUserFingerprint()
  const data = await statsApi.post<Omit<DownloadStatsResponse, 'success'>>('/stats/download', {
    body: { plugin_id: pluginId, user_id: userId, fingerprint },
    errorMessage: '记录插件下载失败',
  })

  const result: DownloadStatsResponse = { success: true, ...data }
  if (typeof result.downloads === 'number') {
    updateCachedPluginStats(pluginId, { downloads: result.downloads })
  }
  return result
}

/**
 * 根据浏览器环境生成稳定的用户指纹。
 * 用于匿名统计，不依赖登录态。
 */
export function generateUserFingerprint(): string {
  const nav = navigator as Navigator & { deviceMemory?: number }
  const features = [
    navigator.userAgent,
    navigator.language,
    navigator.languages?.join(',') || '',
    navigator.platform,
    navigator.hardwareConcurrency || 0,
    screen.width,
    screen.height,
    screen.colorDepth,
    screen.pixelDepth,
    new Date().getTimezoneOffset(),
    Intl.DateTimeFormat().resolvedOptions().timeZone,
    navigator.maxTouchPoints || 0,
    nav.deviceMemory || 0,
  ].join('|')

  let hash = 0
  for (let i = 0; i < features.length; i++) {
    const char = features.charCodeAt(i)
    hash = (hash << 5) - hash + char
    hash = hash & hash // Convert to 32bit integer
  }

  return `fp_${Math.abs(hash).toString(36)}`
}

/**
 * 生成或读取持久化用户 ID。
 * 首次调用写入 localStorage，后续复用。
 */
export function getUserId(): string {
  const STORAGE_KEY = 'maibot_user_id'

  let userId = localStorage.getItem(STORAGE_KEY)

  if (!userId) {
    const fingerprint = generateUserFingerprint()
    const timestamp = Date.now().toString(36)
    const random = Math.random().toString(36).substring(2, 15)

    userId = `${fingerprint}_${timestamp}_${random}`
    localStorage.setItem(STORAGE_KEY, userId)
  }

  return userId
}
