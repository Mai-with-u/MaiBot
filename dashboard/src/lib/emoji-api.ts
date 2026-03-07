/**
 * 表情包管理 API 客户端
 */

import { fetchWithAuth } from '@/lib/fetch-with-auth'
import type {
  EmojiListResponse,
  EmojiDetailResponse,
  EmojiUpdateRequest,
  EmojiUpdateResponse,
  EmojiDeleteResponse,
  EmojiStatsResponse,
} from '@/types/emoji'

const API_BASE = '/api/webui/emoji'

/**
 * 获取表情包列表
 */
export async function getEmojiList(params: {
  page?: number
  page_size?: number
  search?: string
  is_registered?: boolean
  is_banned?: boolean
  format?: string
  sort_by?: string
  sort_order?: 'asc' | 'desc'
}): Promise<EmojiListResponse> {
  const query = new URLSearchParams()
  if (params.page) query.append('page', params.page.toString())
  if (params.page_size) query.append('page_size', params.page_size.toString())
  if (params.search) query.append('search', params.search)
  if (params.is_registered !== undefined) query.append('is_registered', params.is_registered.toString())
  if (params.is_banned !== undefined) query.append('is_banned', params.is_banned.toString())
  if (params.format) query.append('format', params.format)
  if (params.sort_by) query.append('sort_by', params.sort_by)
  if (params.sort_order) query.append('sort_order', params.sort_order)

  const response = await fetchWithAuth(`${API_BASE}/list?${query}`, {
  })

  if (!response.ok) {
    throw new Error(`获取表情包列表失败: ${response.statusText}`)
  }

  return response.json()
}

/**
 * 获取表情包详情
 */
export async function getEmojiDetail(id: number): Promise<EmojiDetailResponse> {
  const response = await fetchWithAuth(`${API_BASE}/${id}`, {
  })

  if (!response.ok) {
    throw new Error(`获取表情包详情失败: ${response.statusText}`)
  }

  return response.json()
}

/**
 * 更新表情包信息
 */
export async function updateEmoji(
  id: number,
  data: EmojiUpdateRequest
): Promise<EmojiUpdateResponse> {
  const response = await fetchWithAuth(`${API_BASE}/${id}`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  })

  if (!response.ok) {
    throw new Error(`更新表情包失败: ${response.statusText}`)
  }

  return response.json()
}

/**
 * 删除表情包
 */
export async function deleteEmoji(id: number): Promise<EmojiDeleteResponse> {
  const response = await fetchWithAuth(`${API_BASE}/${id}`, {
    method: 'DELETE',
  })

  if (!response.ok) {
    throw new Error(`删除表情包失败: ${response.statusText}`)
  }

  return response.json()
}

/**
 * 获取表情包统计数据
 */
export async function getEmojiStats(): Promise<EmojiStatsResponse> {
  const response = await fetchWithAuth(`${API_BASE}/stats/summary`, {
  })

  if (!response.ok) {
    throw new Error(`获取统计数据失败: ${response.statusText}`)
  }

  return response.json()
}

/**
 * 注册表情包
 */
export async function registerEmoji(id: number): Promise<EmojiUpdateResponse> {
  const response = await fetchWithAuth(`${API_BASE}/${id}/register`, {
    method: 'POST',
  })

  if (!response.ok) {
    throw new Error(`注册表情包失败: ${response.statusText}`)
  }

  return response.json()
}

/**
 * 封禁表情包
 */
export async function banEmoji(id: number): Promise<EmojiUpdateResponse> {
  const response = await fetchWithAuth(`${API_BASE}/${id}/ban`, {
    method: 'POST',
    
  })

  if (!response.ok) {
    throw new Error(`封禁表情包失败: ${response.statusText}`)
  }

  return response.json()
}

/**
 * 获取表情包缩略图 URL
 * 注意：使用 HttpOnly Cookie 进行认证，浏览器会自动携带
 * @param id 表情包 ID
 * @param original 是否获取原图（默认返回压缩后的缩略图）
 */
export function getEmojiThumbnailUrl(id: number, original: boolean = false): string {
  if (original) {
    return `${API_BASE}/${id}/thumbnail?original=true`
  }
  return `${API_BASE}/${id}/thumbnail`
}

/**
 * 获取表情包原图 URL
 */
export function getEmojiOriginalUrl(id: number): string {
  return `${API_BASE}/${id}/thumbnail?original=true`
}

/**
 * 批量删除表情包
 */
export async function batchDeleteEmojis(emojiIds: number[]): Promise<{
  success: boolean
  message: string
  deleted_count: number
  failed_count: number
  failed_ids: number[]
}> {
  const response = await fetchWithAuth(`${API_BASE}/batch/delete`, {
    method: 'POST',
    
    body: JSON.stringify({ emoji_ids: emojiIds }),
  })

  if (!response.ok) {
    const error = await response.json()
    throw new Error(error.detail || '批量删除失败')
  }

  return response.json()
}

/**
 * 获取表情包上传 URL（供 Uppy 使用）
 */
export function getEmojiUploadUrl(): string {
  return `${API_BASE}/upload`
}

/**
 * 获取批量上传 URL
 */
export function getEmojiBatchUploadUrl(): string {
  return `${API_BASE}/batch/upload`
}

// ==================== 缩略图缓存管理 API ====================

export interface ThumbnailCacheStatsResponse {
  success: boolean
  cache_dir: string
  total_count: number
  total_size_mb: number
  emoji_count: number
  coverage_percent: number
}

export interface ThumbnailCleanupResponse {
  success: boolean
  message: string
  cleaned_count: number
  kept_count: number
}

export interface ThumbnailPreheatResponse {
  success: boolean
  message: string
  generated_count: number
  skipped_count: number
  failed_count: number
}

/**
 * 获取缩略图缓存统计信息
 */
export async function getThumbnailCacheStats(): Promise<ThumbnailCacheStatsResponse> {
  const response = await fetchWithAuth(`${API_BASE}/thumbnail-cache/stats`, {})

  if (!response.ok) {
    throw new Error(`获取缩略图缓存统计失败: ${response.statusText}`)
  }

  return response.json()
}

/**
 * 清理孤立的缩略图缓存
 */
export async function cleanupThumbnailCache(): Promise<ThumbnailCleanupResponse> {
  const response = await fetchWithAuth(`${API_BASE}/thumbnail-cache/cleanup`, {
    method: 'POST',
  })

  if (!response.ok) {
    throw new Error(`清理缩略图缓存失败: ${response.statusText}`)
  }

  return response.json()
}

/**
 * 预热缩略图缓存
 * @param limit 最多预热数量 (1-1000)
 */
export async function preheatThumbnailCache(limit: number = 100): Promise<ThumbnailPreheatResponse> {
  const response = await fetchWithAuth(`${API_BASE}/thumbnail-cache/preheat?limit=${limit}`, {
    method: 'POST',
  })

  if (!response.ok) {
    throw new Error(`预热缩略图缓存失败: ${response.statusText}`)
  }

  return response.json()
}

/**
 * 清空所有缩略图缓存
 */
export async function clearAllThumbnailCache(): Promise<ThumbnailCleanupResponse> {
  const response = await fetchWithAuth(`${API_BASE}/thumbnail-cache/clear`, {
    method: 'DELETE',
  })

  if (!response.ok) {
    throw new Error(`清空缩略图缓存失败: ${response.statusText}`)
  }

  return response.json()
}