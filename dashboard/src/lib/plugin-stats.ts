/**
 * 閹绘帊娆㈢紒鐔活吀 API 鐎广垺鍩涚粩?
 * 閻劋绨稉?Cloudflare Workers 缂佺喕顓搁張宥呭娴溿倓绨?
 */

// 闁板秶鐤嗙紒鐔活吀閺堝秴濮?API 閸︽澘娼冮敍鍫熷閺堝鏁ら幋宄板彙娴滎偆娈戞禍鎴狀伂缂佺喕顓搁張宥呭閿?
import { ApiError, backendApi } from '@/lib/http'

const STATS_API_BASE_URL = '/api/webui/plugins/stats-proxy'
const PLUGIN_STATS_SUMMARY_CACHE_TTL = 5 * 60 * 1000
const PLUGIN_STATS_SUMMARY_STORAGE_KEY = 'maibot-plugin-stats-summary-cache'

let pluginStatsSummaryCache: { timestamp: number; data: Record<string, PluginStatsData> } | null = null
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

function getReadableError(data: unknown, fallback: string): string {
  if (!data || typeof data !== 'object') {
    return fallback
  }

  const error = (data as { error?: unknown }).error
  if (typeof error !== 'string' || !error.trim()) {
    return fallback
  }

  return /[�閹缂鐠]/.test(error) ? fallback : error
}

/** 从 ApiError 携带的后端原始错误体中提取 error 字段 */
function getDetailError(error: ApiError): string | undefined {
  const detailError = (error.detail as { error?: unknown } | null | undefined)?.error
  return typeof detailError === 'string' ? detailError : undefined
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
  const currentCache = pluginStatsSummaryCache ?? readPluginStatsSummaryStorageCache()
  if (!currentCache) {
    return
  }

  const currentStats = currentCache.data[pluginId] ?? createEmptyStats(pluginId)
  const nextData = {
    ...currentCache.data,
    [pluginId]: normalizePluginStatsResponse(
      {
        ...currentStats,
        ...partialStats,
        plugin_id: pluginId,
      },
      pluginId
    ) ?? currentStats,
  }

  pluginStatsSummaryCache = { timestamp: Date.now(), data: nextData }
  writePluginStatsSummaryStorageCache(nextData)
}

export function getCachedPluginStatsSummary(): Record<string, PluginStatsData> | null {
  if (pluginStatsSummaryCache) {
    return pluginStatsSummaryCache.data
  }

  const storedCache = readPluginStatsSummaryStorageCache()
  if (!storedCache) {
    return null
  }

  pluginStatsSummaryCache = storedCache
  return storedCache.data
}

/**
 * 閼惧嘲褰囬幓鎺嶆缂佺喕顓搁弫鐗堝祦
 */
export async function getPluginStats(pluginId: string): Promise<PluginStatsData | null> {
  try {
    const data = await backendApi.get<unknown>(
      `${STATS_API_BASE_URL}/stats/${encodeURIComponent(pluginId)}`
    )
    return normalizePluginStatsResponse(data, pluginId)
  } catch (error) {
    console.error('Error fetching plugin stats:', error)
    return null
  }
}

/**
 * 閼惧嘲褰囬幓鎺嶆鐢倸婧€閻ㄥ嫯浜ら柌蹇曠埠鐠佲剝鎲崇憰渚婄礄娑撳秴瀵橀崥顐ョ槑鐠佺尨绱氶妴? */
async function fetchPluginStatsSummaryUncached(): Promise<Record<string, PluginStatsData>> {
  try {
    const data = await backendApi.get<PluginStatsSummaryResponse>(`${STATS_API_BASE_URL}/stats/summary`)
    if (!data.success || !data.stats || typeof data.stats !== 'object') {
      return {}
    }

    return Object.fromEntries(
      Object.entries(data.stats).map(([pluginId, stats]) => [
        pluginId,
        normalizePluginStatsResponse({ stats }, pluginId) ?? createEmptyStats(pluginId),
      ])
    )
  } catch (error) {
    console.error('Error fetching plugin stats summary:', error)
    return {}
  }
}

export async function getPluginUserState(
  pluginId: string,
  userId: string = getUserId()
): Promise<PluginUserState | null> {
  try {
    const data = await backendApi.get<Partial<PluginUserState> & { success?: boolean }>(
      `${STATS_API_BASE_URL}/stats/user-state`,
      {
        query: { plugin_id: pluginId, user_id: userId },
      }
    )
    if (data.success === false) {
      return null
    }

    return {
      liked: data.liked === true,
      disliked: data.disliked === true,
      rating: data.rating == null ? null : Number(data.rating),
      comment: typeof data.comment === 'string' ? data.comment : '',
    }
  } catch (error) {
    console.error('Error fetching plugin user state:', error)
    return null
  }
}

export async function getPluginStatsSummary(
  options: { forceRefresh?: boolean } = {}
): Promise<Record<string, PluginStatsData>> {
  if (
    !options.forceRefresh
    && pluginStatsSummaryCache
    && Date.now() - pluginStatsSummaryCache.timestamp < PLUGIN_STATS_SUMMARY_CACHE_TTL
  ) {
    return pluginStatsSummaryCache.data
  }

  if (!options.forceRefresh && !pluginStatsSummaryCache) {
    const storedCache = readPluginStatsSummaryStorageCache()
    if (storedCache && Date.now() - storedCache.timestamp < PLUGIN_STATS_SUMMARY_CACHE_TTL) {
      pluginStatsSummaryCache = storedCache
      return storedCache.data
    }
  }

  if (!pluginStatsSummaryRequest || options.forceRefresh) {
    pluginStatsSummaryRequest = fetchPluginStatsSummaryUncached()
      .then((data) => {
        pluginStatsSummaryCache = { timestamp: Date.now(), data }
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
 * 閻愮绂愰幓鎺嶆
 */
export async function likePlugin(pluginId: string, userId?: string): Promise<VoteStatsResponse> {
  try {
    const finalUserId = userId || getUserId()

    const data = await backendApi.post<Omit<VoteStatsResponse, 'success'>>(`${STATS_API_BASE_URL}/stats/like`, {
      body: { plugin_id: pluginId, user_id: finalUserId },
    })

    const result: VoteStatsResponse = { success: true, ...data }
    updateCachedPluginStats(pluginId, {
      likes: Number(result.likes ?? 0),
      dislikes: Number(result.dislikes ?? 0),
    })
    return result
  } catch (error) {
    if (error instanceof ApiError) {
      if (error.status === 429) {
        return { success: false, error: '点赞过于频繁，请稍后再试' }
      }
      if (error.status !== undefined) {
        return { success: false, error: getReadableError(error.detail, '点赞失败') }
      }
    }
    console.error('Error liking plugin:', error)
    return { success: false, error: '网络请求失败' }
  }
}

/**
 * 閻愮淇幓鎺嶆
 */
export async function dislikePlugin(pluginId: string, userId?: string): Promise<VoteStatsResponse> {
  try {
    const finalUserId = userId || getUserId()

    const data = await backendApi.post<Omit<VoteStatsResponse, 'success'>>(`${STATS_API_BASE_URL}/stats/dislike`, {
      body: { plugin_id: pluginId, user_id: finalUserId },
    })

    const result: VoteStatsResponse = { success: true, ...data }
    updateCachedPluginStats(pluginId, {
      likes: Number(result.likes ?? 0),
      dislikes: Number(result.dislikes ?? 0),
    })
    return result
  } catch (error) {
    if (error instanceof ApiError) {
      if (error.status === 429) {
        return { success: false, error: '操作过于频繁，请稍后再试' }
      }
      if (error.status !== undefined) {
        return { success: false, error: getReadableError(error.detail, '点踩失败') }
      }
    }
    console.error('Error disliking plugin:', error)
    return { success: false, error: '网络请求失败' }
  }
}

/**
 * 鐠囧嫬鍨庨幓鎺嶆
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

  try {
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

    const data = await backendApi.post<Omit<RatingStatsResponse, 'success'>>(`${STATS_API_BASE_URL}/stats/rate`, {
      body: payload,
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
    if (error instanceof ApiError) {
      if (error.status === 429) {
        return { success: false, error: '每天最多评分 3 次' }
      }
      if (error.status !== undefined) {
        return { success: false, error: getDetailError(error) || '鐠囧嫬鍨庢径杈Е' }
      }
    }
    console.error('Error rating plugin:', error)
    return { success: false, error: '缂冩垹绮堕柨娆掝嚖' }
  }
}

/**
 * 鐠佹澘缍嶉幓鎺嶆娑撳娴?
 */
export async function recordPluginDownload(pluginId: string): Promise<DownloadStatsResponse> {
  try {
    const userId = getUserId()
    const fingerprint = generateUserFingerprint()
    const data = await backendApi.post<Omit<DownloadStatsResponse, 'success'>>(`${STATS_API_BASE_URL}/stats/download`, {
      body: { plugin_id: pluginId, user_id: userId, fingerprint },
    })

    const result: DownloadStatsResponse = { success: true, ...data }
    if (typeof result.downloads === 'number') {
      updateCachedPluginStats(pluginId, { downloads: result.downloads })
    }
    return result
  } catch (error) {
    if (error instanceof ApiError) {
      if (error.status === 429) {
        // 娑撳娴囩紒鐔活吀鐞氼偊妾哄ù浣规闂堟瑩绮径杈Е閿涘奔绗夎ぐ鍗炴惙閻劍鍩涙担鎾荤崣
        console.warn('Download recording rate limited')
        return { success: true }
      }
      if (error.status !== undefined) {
        const detailError = getDetailError(error)
        console.error('Failed to record download:', detailError)
        return { success: false, error: detailError }
      }
    }
    console.error('Error recording download:', error)
    return { success: false, error: '缂冩垹绮堕柨娆掝嚖' }
  }
}

/**
 * 閻㈢喐鍨氶悽銊﹀煕閹稿洨姹楅敍鍫濈唨娴滃孩绁荤憴鍫濇珤閻楃懓绶涢敍?
 * 閻劋绨崷銊︽弓閻ц缍嶉弮鎯扮槕閸掝偆鏁ら幋鍑ょ礉闂冨弶顒涢柌宥咁槻閹舵洜銈?
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
  
  // 缁犫偓閸楁洖鎼辩敮灞藉毐閺?
  let hash = 0
  for (let i = 0; i < features.length; i++) {
    const char = features.charCodeAt(i)
    hash = ((hash << 5) - hash) + char
    hash = hash & hash // Convert to 32bit integer
  }
  
  return `fp_${Math.abs(hash).toString(36)}`
}

/**
 * 閻㈢喐鍨氶幋鏍箯閸欐牜鏁ら幋?UUID
 * 鐎涙ê鍋嶉崷?localStorage 娑擃厽瀵旀稊鍛
 */
export function getUserId(): string {
  const STORAGE_KEY = 'maibot_user_id'
  
  // 鐏忔繆鐦禒?localStorage 閼惧嘲褰?
  let userId = localStorage.getItem(STORAGE_KEY)
  
  if (!userId) {
    // 閻㈢喐鍨氶弬鎵畱 UUID
    const fingerprint = generateUserFingerprint()
    const timestamp = Date.now().toString(36)
    const random = Math.random().toString(36).substring(2, 15)
    
    userId = `${fingerprint}_${timestamp}_${random}`
    
    // 鐎涙ê鍋嶉崚?localStorage
    localStorage.setItem(STORAGE_KEY, userId)
  }
  
  return userId
}
