/**
 * 黑话（俚语）相关类型定义
 */

/**
 * 黑话信息
 */
export interface Jargon {
  id: number
  content: string
  raw_content: string | null
  meaning: string | null
  chat_id: string
  stream_id: string | null  // 解析后的 stream_id，用于编辑时匹配
  chat_name: string | null  // 解析后的聊天名称，用于前端显示
  is_global: boolean
  count: number
  is_jargon: boolean | null  // null 表示未判定
  is_complete: boolean
  inference_with_context: string | null
  inference_content_only: string | null
}

/**
 * 聊天信息
 */
export interface JargonChatInfo {
  chat_id: string
  chat_name: string
  platform: string | null
  is_group: boolean
}

/**
 * 聊天列表响应
 */
export interface JargonChatListResponse {
  success: boolean
  data: JargonChatInfo[]
}

/**
 * 黑话列表响应
 */
export interface JargonListResponse {
  success: boolean
  total: number
  page: number
  page_size: number
  data: Jargon[]
}

/**
 * 黑话详情响应
 */
export interface JargonDetailResponse {
  success: boolean
  data: Jargon
}

/**
 * 黑话创建请求
 */
export interface JargonCreateRequest {
  content: string
  raw_content?: string
  meaning?: string
  chat_id: string
  is_global?: boolean
}

/**
 * 黑话更新请求
 */
export interface JargonUpdateRequest {
  content?: string
  raw_content?: string
  meaning?: string
  chat_id?: string
  is_global?: boolean
  is_jargon?: boolean | null
}

/**
 * 黑话创建响应
 */
export interface JargonCreateResponse {
  success: boolean
  message: string
  data: Jargon
}

/**
 * 黑话更新响应
 */
export interface JargonUpdateResponse {
  success: boolean
  message: string
  data?: Jargon
}

/**
 * 黑话删除响应
 */
export interface JargonDeleteResponse {
  success: boolean
  message: string
  deleted_count: number
}

/**
 * 黑话统计数据
 */
export interface JargonStats {
  total: number
  confirmed_jargon: number
  confirmed_not_jargon: number
  pending: number
  global_count: number
  complete_count: number
  chat_count: number
  top_chats: Record<string, number>
}

/**
 * 黑话统计响应
 */
export interface JargonStatsResponse {
  success: boolean
  data: JargonStats
}
