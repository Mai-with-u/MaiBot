/**
 * 表情包相关类型定义
 */

/**
 * 表情包信息
 */
export interface Emoji {
  id: number
  full_path: string
  format: string
  emoji_hash: string
  description: string
  query_count: number
  is_registered: boolean
  is_banned: boolean
  emotion: string | null
  record_time: number
  register_time: number | null
  usage_count: number
  last_used_time: number | null
}

/**
 * 表情包列表响应
 */
export interface EmojiListResponse {
  success: boolean
  total: number
  page: number
  page_size: number
  data: Emoji[]
}

/**
 * 表情包详情响应
 */
export interface EmojiDetailResponse {
  success: boolean
  data: Emoji
}

/**
 * 表情包更新请求
 */
export interface EmojiUpdateRequest {
  description?: string
  is_registered?: boolean
  is_banned?: boolean
  emotion?: string
}

/**
 * 表情包更新响应
 */
export interface EmojiUpdateResponse {
  success: boolean
  message: string
  data?: Emoji
}

/**
 * 表情包删除响应
 */
export interface EmojiDeleteResponse {
  success: boolean
  message: string
}

/**
 * 表情包统计数据
 */
export interface EmojiStats {
  total: number
  registered: number
  banned: number
  unregistered: number
  formats: Record<string, number>
  top_used: Array<{
    id: number
    emoji_hash: string
    description: string
    usage_count: number
  }>
}

/**
 * 表情包统计响应
 */
export interface EmojiStatsResponse {
  success: boolean
  data: EmojiStats
}
