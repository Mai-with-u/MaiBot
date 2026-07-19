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
  chat_name?: string | null
  create_date: number | null
  checked: boolean
  modified_by: 'ai' | 'user' | null  // 最后修改来源
}

/**
 * 聊天信息
 */
export interface ChatInfo {
  chat_id: string
  chat_name: string
  platform: string | null
  account_id?: string | null
  is_group: boolean
  use_expression: boolean
  enable_learning: boolean
}

/**
 * 聊天列表响应
 */
export interface ChatListResponse {
  success: boolean
  data: ChatInfo[]
}

export interface ExpressionGroupInfo {
  index: number
  name: string
  chat_ids: string[]
  members: ChatInfo[]
  is_global: boolean
}

export interface ExpressionGroupListResponse {
  success: boolean
  data: ExpressionGroupInfo[]
}

export interface ExpressionClusterMember {
  id: number
  situation: string
  style: string
  count: number
  chat_id?: string | null
  chat_name?: string | null
  checked: boolean
  modified_by: 'ai' | 'user' | null
}

export interface ExpressionClusterSummary {
  embedding_profile_marker: string
  cluster_id: number
  size: number
  members: ExpressionClusterMember[]
}

export interface ExpressionClusterListResponse {
  success: boolean
  index_exists: boolean
  index_path: string
  generated_at: string | null
  updated_at: string | null
  embedding_model: string | null
  embedding_dimension: number | null
  sample_count: number
  clusters: ExpressionClusterSummary[]
}

export interface ExpressionClusterMemberListResponse {
  success: boolean
  cluster: ExpressionClusterSummary | null
  data: ExpressionClusterMember[]
}

export interface ExpressionExportItem {
  situation: string
  style: string
  content_list: string
  count: number
  last_active_time: string | null
  create_time: string | null
  checked: boolean
  modified_by: 'ai' | 'user' | null
}

export interface ExpressionExportResponse {
  success: boolean
  version: number
  type: 'maibot.expression.export'
  exported_at: string
  source_chat_name: string
  count: number
  expressions: ExpressionExportItem[]
}

export interface ExpressionImportResponse {
  success: boolean
  message: string
  imported_count: number
  skipped_count: number
  failed_count: number
}

export interface ExpressionClearResponse {
  success: boolean
  message: string
  deleted_count: number
}

export interface LegacyExpressionGroupPreview {
  old_chat_id: string
  expression_count: number
  platform: string | null
  target_id: string | null
  chat_type: 'group' | 'private' | null
  matched_session_id: string | null
  matched_chat_name: string | null
  matched: boolean
  matched_sessions: LegacyExpressionMatchOption[]
}

export interface LegacyExpressionMatchOption {
  session_id: string
  chat_name: string
  account_id?: string | null
}

export interface LegacyExpressionImportPreviewResponse {
  success: boolean
  db_path: string
  total_count: number
  matched_count: number
  unmatched_count: number
  groups: LegacyExpressionGroupPreview[]
}

export interface LegacyExpressionImportResponse {
  success: boolean
  message: string
  imported_count: number
  skipped_count: number
  failed_count: number
  ignored_group_count: number
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
  approved: boolean
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

export interface ExpressionReviewLogEntry {
  id: string
  created_at: number
  expression_id: number | null
  session_id: string
  chat_name?: string | null
  passed: boolean
  reason: string
  situation: string
  style: string
  source: string
  error?: string | null
  rescued: boolean
  rescued_expression_id: number | null
  rescued_at: number | null
}

export interface ExpressionReviewLogListResponse {
  success: boolean
  total: number
  data: ExpressionReviewLogEntry[]
}

export interface ExpressionReviewLogApproveResponse {
  success: boolean
  message: string
  data: Expression
}
