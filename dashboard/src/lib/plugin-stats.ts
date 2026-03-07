/**
 * 插件统计 API 客户端
 * 用于与 Cloudflare Workers 统计服务交互
 */

// 配置统计服务 API 地址（所有用户共享的云端统计服务）
const STATS_API_BASE_URL = 'https://maibot-plugin-stats.maibot-webui.workers.dev'

export interface PluginStatsData {
  plugin_id: string
  likes: number
  dislikes: number
  downloads: number
  rating: number
  rating_count: number
  recent_ratings?: Array<{
    user_id: string
    rating: number
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

/**
 * 获取插件统计数据
 */
export async function getPluginStats(pluginId: string): Promise<PluginStatsData | null> {
  try {
    const response = await fetch(`${STATS_API_BASE_URL}/stats/${pluginId}`)
    
    if (!response.ok) {
      console.error('Failed to fetch plugin stats:', response.statusText)
      return null
    }
    
    return await response.json()
  } catch (error) {
    console.error('Error fetching plugin stats:', error)
    return null
  }
}

/**
 * 点赞插件
 */
export async function likePlugin(pluginId: string, userId?: string): Promise<StatsResponse> {
  try {
    const finalUserId = userId || getUserId()
    
    const response = await fetch(`${STATS_API_BASE_URL}/stats/like`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ plugin_id: pluginId, user_id: finalUserId }),
    })
    
    const data = await response.json()
    
    if (response.status === 429) {
      return { success: false, error: '操作过于频繁，请稍后再试' }
    }
    
    if (!response.ok) {
      return { success: false, error: data.error || '点赞失败' }
    }
    
    return { success: true, ...data }
  } catch (error) {
    console.error('Error liking plugin:', error)
    return { success: false, error: '网络错误' }
  }
}

/**
 * 点踩插件
 */
export async function dislikePlugin(pluginId: string, userId?: string): Promise<StatsResponse> {
  try {
    const finalUserId = userId || getUserId()
    
    const response = await fetch(`${STATS_API_BASE_URL}/stats/dislike`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ plugin_id: pluginId, user_id: finalUserId }),
    })
    
    const data = await response.json()
    
    if (response.status === 429) {
      return { success: false, error: '操作过于频繁，请稍后再试' }
    }
    
    if (!response.ok) {
      return { success: false, error: data.error || '点踩失败' }
    }
    
    return { success: true, ...data }
  } catch (error) {
    console.error('Error disliking plugin:', error)
    return { success: false, error: '网络错误' }
  }
}

/**
 * 评分插件
 */
export async function ratePlugin(
  pluginId: string,
  rating: number,
  comment?: string,
  userId?: string
): Promise<StatsResponse> {
  if (rating < 1 || rating > 5) {
    return { success: false, error: '评分必须在 1-5 之间' }
  }
  
  try {
    const finalUserId = userId || getUserId()
    
    const response = await fetch(`${STATS_API_BASE_URL}/stats/rate`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ plugin_id: pluginId, rating, comment, user_id: finalUserId }),
    })
    
    const data = await response.json()
    
    if (response.status === 429) {
      return { success: false, error: '每天最多评分 3 次' }
    }
    
    if (!response.ok) {
      return { success: false, error: data.error || '评分失败' }
    }
    
    return { success: true, ...data }
  } catch (error) {
    console.error('Error rating plugin:', error)
    return { success: false, error: '网络错误' }
  }
}

/**
 * 记录插件下载
 */
export async function recordPluginDownload(pluginId: string): Promise<StatsResponse> {
  try {
    const response = await fetch(`${STATS_API_BASE_URL}/stats/download`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ plugin_id: pluginId }),
    })
    
    const data = await response.json()
    
    if (response.status === 429) {
      // 下载统计被限流时静默失败，不影响用户体验
      console.warn('Download recording rate limited')
      return { success: true }
    }
    
    if (!response.ok) {
      console.error('Failed to record download:', data.error)
      return { success: false, error: data.error }
    }
    
    return { success: true, ...data }
  } catch (error) {
    console.error('Error recording download:', error)
    return { success: false, error: '网络错误' }
  }
}

/**
 * 生成用户指纹（基于浏览器特征）
 * 用于在未登录时识别用户，防止重复投票
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
  
  // 简单哈希函数
  let hash = 0
  for (let i = 0; i < features.length; i++) {
    const char = features.charCodeAt(i)
    hash = ((hash << 5) - hash) + char
    hash = hash & hash // Convert to 32bit integer
  }
  
  return `fp_${Math.abs(hash).toString(36)}`
}

/**
 * 生成或获取用户 UUID
 * 存储在 localStorage 中持久化
 */
export function getUserId(): string {
  const STORAGE_KEY = 'maibot_user_id'
  
  // 尝试从 localStorage 获取
  let userId = localStorage.getItem(STORAGE_KEY)
  
  if (!userId) {
    // 生成新的 UUID
    const fingerprint = generateUserFingerprint()
    const timestamp = Date.now().toString(36)
    const random = Math.random().toString(36).substring(2, 15)
    
    userId = `${fingerprint}_${timestamp}_${random}`
    
    // 存储到 localStorage
    localStorage.setItem(STORAGE_KEY, userId)
  }
  
  return userId
}
