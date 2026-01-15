/**
 * 表达方式相关类型定义
 */

/**
 * 表达方式信息
 */
export interface Expression {
  id: number
  situation: string
  style: string
  last_active_time: number
  chat_id: string
  create_date: number | null
  checked: boolean
  rejected: boolean
  modified_by: 'ai' | 'user' | null  // 最后修改来源
}

/**
 * 聊天信息
 */
export interface ChatInfo {
  chat_id: string
  chat_name: string
  platform: string | null
  is_group: boolean
}

/**
 * 聊天列表响应
 */
export interface ChatListResponse {
  success: boolean
  data: ChatInfo[]
}

/**
 * 表达方式列表响应
 */
export interface ExpressionListResponse {
  success: boolean
  total: number
  page: number
  page_size: number
  data: Expression[]
}

/**
 * 表达方式详情响应
 */
export interface ExpressionDetailResponse {
  success: boolean
  data: Expression
}

/**
 * 表达方式创建请求
 */
export interface ExpressionCreateRequest {
  situation: string
  style: string
  chat_id: string
}

/**
 * 表达方式更新请求
 */
export interface ExpressionUpdateRequest {
  situation?: string
  style?: string
  chat_id?: string
  checked?: boolean
  rejected?: boolean
  require_unchecked?: boolean  // 用于人工审核时的冲突检测
}

/**
 * 表达方式创建响应
 */
export interface ExpressionCreateResponse {
  success: boolean
  message: string
  data: Expression
}

/**
 * 表达方式更新响应
 */
export interface ExpressionUpdateResponse {
  success: boolean
  message: string
  data?: Expression
}

/**
 * 表达方式删除响应
 */
export interface ExpressionDeleteResponse {
  success: boolean
  message: string
}

/**
 * 表达方式统计数据
 */
export interface ExpressionStats {
  total: number
  recent_7days: number
  chat_count: number
  top_chats: Record<string, number>
}

/**
 * 表达方式统计响应
 */
export interface ExpressionStatsResponse {
  success: boolean
  data: ExpressionStats
}

// ============ 审核相关类型 ============

/**
 * 审核统计数据
 */
export interface ReviewStats {
  total: number
  unchecked: number
  passed: number
  rejected: number
  ai_checked: number
  user_checked: number
}

/**
 * 审核列表响应
 */
export interface ReviewListResponse {
  success: boolean
  total: number
  page: number
  page_size: number
  data: Expression[]
}

/**
 * 批量审核项
 */
export interface BatchReviewItem {
  id: number
  rejected: boolean
  require_unchecked?: boolean
}

/**
 * 批量审核结果项
 */
export interface BatchReviewResultItem {
  id: number
  success: boolean
  message: string
}

/**
 * 批量审核响应
 */
export interface BatchReviewResponse {
  success: boolean
  total: number
  succeeded: number
  failed: number
  results: BatchReviewResultItem[]
}
