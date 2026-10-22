/**
 * 插件市场统计与用户互动客户端。
 * 统计读取与评分、点赞、下载记录统一调用公共市场接口。
 */
import { ApiError, backendApi } from '@/lib/http'
import { invalidatePluginMarketCache } from '@/lib/plugin-api/marketplace'

const MARKET_API_BASE_URL = '/api/webui/plugins/marketplace'

export interface PluginStatsData {
  plugin_id: string
  likes: number
  dislikes: number
  liked?: boolean
  disliked?: boolean
  downloads: number
  /** 全市场飙升排名，1～30；未上榜为 null，旧统计接口可能不提供。 */
  downloads_growth_rank_7d?: number | null
  likes_growth_rank_7d?: number | null
  downloads_growth_rank_30d?: number | null
  likes_growth_rank_30d?: number | null
  rating: number
  rating_count: number
  comment_count: number
  recent_ratings?: Array<{
    user_id: string
    username?: string | null
    anonymous?: boolean
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
  comment_count?: number
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
  username?: string | null
  anonymous?: boolean
}

export interface PluginReviewIdentity {
  username: string
  anonymous: boolean
}

/** 获取本地 bot 昵称；此请求不会发往插件统计服务。 */
export async function getPluginReviewIdentity(): Promise<{ username: string }> {
  return backendApi.get<{ username: string }>(`${MARKET_API_BASE_URL}/review-identity`, {
    errorMessage: '获取默认评论用户名失败',
  })
}

export interface PluginVoteState {
  liked: boolean
  disliked: boolean
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

/** 获取当前市场统计与评论；错误交由调用方显示或记录。 */
export async function getPluginStats(pluginId: string): Promise<PluginStatsData | null> {
  const result = await backendApi.get<{ stats: PluginStatsData | null }>(
    `${MARKET_API_BASE_URL}/${encodeURIComponent(pluginId)}/stats`
  )
  return result.stats
}

export async function getPluginUserState(
  pluginId: string,
  userId: string = getUserId()
): Promise<PluginUserState | null> {
  try {
    const data = await backendApi.get<Partial<PluginUserState> & { success?: boolean }>(
      `${MARKET_API_BASE_URL}/${encodeURIComponent(pluginId)}/user-state`,
      {
        query: { user_id: userId },
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
      ...(data.username !== undefined ? { username: data.username } : {}),
      ...(data.anonymous !== undefined ? { anonymous: data.anonymous } : {}),
    }
  } catch (error) {
    console.error('Error fetching plugin user state:', error)
    return null
  }
}

/**
 * 批量获取当前用户对所有插件的点赞/点踩状态，键为插件 ID
 */
export async function getPluginUserStates(
  userId: string = getUserId()
): Promise<Record<string, PluginVoteState>> {
  try {
    const data = await backendApi.get<{
      success?: boolean
      states?: Record<string, Partial<PluginVoteState>>
    }>(`${MARKET_API_BASE_URL}/user-states`, {
      query: { user_id: userId },
    })
    if (!data.success || !data.states || typeof data.states !== 'object') {
      return {}
    }

    return Object.fromEntries(
      Object.entries(data.states).map(([pluginId, state]) => [
        pluginId,
        { liked: state.liked === true, disliked: state.disliked === true },
      ])
    )
  } catch (error) {
    console.error('Error fetching plugin user states:', error)
    return {}
  }
}

export async function likePlugin(pluginId: string, userId?: string): Promise<VoteStatsResponse> {
  try {
    const finalUserId = userId || getUserId()

    const data = await backendApi.post<Omit<VoteStatsResponse, 'success'>>(
      `${MARKET_API_BASE_URL}/like`,
      {
        body: { plugin_id: pluginId, user_id: finalUserId },
      }
    )

    const result: VoteStatsResponse = { success: true, ...data }
    invalidatePluginMarketCache()
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

    const data = await backendApi.post<Omit<VoteStatsResponse, 'success'>>(
      `${MARKET_API_BASE_URL}/dislike`,
      {
        body: { plugin_id: pluginId, user_id: finalUserId },
      }
    )

    const result: VoteStatsResponse = { success: true, ...data }
    invalidatePluginMarketCache()
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
 * 提交插件评分或评论
 */
export async function ratePlugin(
  pluginId: string,
  rating?: number | null,
  comment?: string | null,
  userId?: string,
  identity?: PluginReviewIdentity
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
      username?: string
      anonymous?: boolean
    } = { plugin_id: pluginId, user_id: finalUserId }

    if (hasRating) {
      payload.rating = Number(rating)
    }
    if (hasComment) {
      payload.comment = comment
    }
    if (identity) {
      payload.anonymous = identity.anonymous
      if (!identity.anonymous) {
        const username = identity.username.trim()
        if (!username || username.length > 100) {
          return { success: false, error: '用户名需要填写 1-100 个字符' }
        }
        payload.username = username
      }
    }

    const data = await backendApi.post<Omit<RatingStatsResponse, 'success'>>(
      `${MARKET_API_BASE_URL}/rate`,
      {
        body: payload,
      }
    )

    const result: RatingStatsResponse = { success: true, ...data }
    invalidatePluginMarketCache()
    return result
  } catch (error) {
    if (error instanceof ApiError) {
      if (error.status === 429) {
        return { success: false, error: '每天最多评分 3 次' }
      }
      if (error.status !== undefined) {
        return { success: false, error: getDetailError(error) || '评分失败' }
      }
    }
    console.error('Error rating plugin:', error)
    return { success: false, error: '网络请求失败' }
  }
}

/**
 * 记录插件下载
 */
export async function recordPluginDownload(pluginId: string): Promise<DownloadStatsResponse> {
  try {
    const userId = getUserId()
    const fingerprint = generateUserFingerprint()
    const data = await backendApi.post<Omit<DownloadStatsResponse, 'success'>>(
      `${MARKET_API_BASE_URL}/download`,
      {
        body: { plugin_id: pluginId, user_id: userId, fingerprint },
      }
    )

    const result: DownloadStatsResponse = { success: true, ...data }
    if (typeof result.downloads === 'number') {
      invalidatePluginMarketCache()
    }
    return result
  } catch (error) {
    if (error instanceof ApiError) {
      if (error.status === 429) {
        // 下载计数受限不影响实际下载流程
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
    return { success: false, error: '网络请求失败' }
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
    hash = (hash << 5) - hash + char
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
