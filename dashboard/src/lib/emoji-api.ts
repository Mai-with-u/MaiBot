/**
 * 表情包管理 API 客户端
 *
 * 请求样板（认证、解析、错误格式化）由 @/lib/http 的请求客户端承担；
 * 本文件只声明 endpoint 与业务错误文案。请求失败时抛出 ApiError（throw 契约）。
 */
import { backendApi } from '@/lib/http'
import type {
  EmojiDeleteResponse,
  EmojiDetailResponse,
  EmojiListResponse,
  EmojiStatsResponse,
  EmojiStatus,
  EmojiUpdateRequest,
  EmojiUpdateResponse,
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
  status?: EmojiStatus
  format?: string
  sort_by?: string
  sort_order?: 'asc' | 'desc'
}): Promise<EmojiListResponse> {
  return backendApi.get<EmojiListResponse>(`${API_BASE}/list`, {
    query: {
      page: params.page || undefined,
      page_size: params.page_size || undefined,
      search: params.search || undefined,
      is_registered: params.is_registered,
      is_banned: params.is_banned,
      status: params.status || undefined,
      format: params.format || undefined,
      sort_by: params.sort_by || undefined,
      sort_order: params.sort_order || undefined,
    },
    errorMessage: '获取表情包列表失败',
  })
}

/**
 * 获取表情包详情
 */
export async function getEmojiDetail(id: number): Promise<EmojiDetailResponse> {
  return backendApi.get<EmojiDetailResponse>(`${API_BASE}/${id}`, {
    errorMessage: '获取表情包详情失败',
  })
}

/**
 * 更新表情包信息
 */
export async function updateEmoji(
  id: number,
  data: EmojiUpdateRequest
): Promise<EmojiUpdateResponse> {
  return backendApi.patch<EmojiUpdateResponse>(`${API_BASE}/${id}`, {
    body: data,
    errorMessage: '更新表情包失败',
  })
}

/**
 * 删除表情包
 */
export async function deleteEmoji(id: number): Promise<EmojiDeleteResponse> {
  return backendApi.delete<EmojiDeleteResponse>(`${API_BASE}/${id}`, {
    errorMessage: '删除表情包失败',
  })
}

/**
 * 获取表情包统计数据
 */
export async function getEmojiStats(): Promise<EmojiStatsResponse> {
  return backendApi.get<EmojiStatsResponse>(`${API_BASE}/stats/summary`, {
    errorMessage: '获取统计数据失败',
  })
}

/**
 * 注册表情包
 */
export async function registerEmoji(id: number): Promise<EmojiUpdateResponse> {
  return backendApi.post<EmojiUpdateResponse>(`${API_BASE}/${id}/register`, {
    errorMessage: '注册表情包失败',
  })
}

/**
 * 封禁表情包
 */
export async function banEmoji(id: number): Promise<EmojiUpdateResponse> {
  return backendApi.post<EmojiUpdateResponse>(`${API_BASE}/${id}/ban`, {
    errorMessage: '封禁表情包失败',
  })
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
  return backendApi.post(`${API_BASE}/batch/delete`, {
    body: { emoji_ids: emojiIds },
    errorMessage: '批量删除失败',
  })
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
  return backendApi.get<ThumbnailCacheStatsResponse>(`${API_BASE}/thumbnail-cache/stats`, {
    errorMessage: '获取缩略图缓存统计失败',
  })
}

/**
 * 清理孤立的缩略图缓存
 */
export async function cleanupThumbnailCache(): Promise<ThumbnailCleanupResponse> {
  return backendApi.post<ThumbnailCleanupResponse>(`${API_BASE}/thumbnail-cache/cleanup`, {
    errorMessage: '清理缩略图缓存失败',
  })
}

/**
 * 预热缩略图缓存
 * @param limit 最多预热数量 (1-1000)
 */
export async function preheatThumbnailCache(limit: number = 100): Promise<ThumbnailPreheatResponse> {
  return backendApi.post<ThumbnailPreheatResponse>(`${API_BASE}/thumbnail-cache/preheat`, {
    query: { limit },
    errorMessage: '预热缩略图缓存失败',
  })
}

/**
 * 清空所有缩略图缓存
 */
export async function clearAllThumbnailCache(): Promise<ThumbnailCleanupResponse> {
  return backendApi.delete<ThumbnailCleanupResponse>(`${API_BASE}/thumbnail-cache/clear`, {
    errorMessage: '清空缩略图缓存失败',
  })
}
