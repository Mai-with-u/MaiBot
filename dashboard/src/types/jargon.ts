/**
 * 黑话（俚语）相关类型定义
 */

/**
 * 黑话信息
 */
export type JargonCreatedBy = 'AI' | 'MANUAL'

export interface Jargon {
  id: number
  content: string
  meaning: string | null
  session_id: string
  session_ids: string[]
  chat_name: string | null // 解析后的聊天名称，用于前端显示
  chat_names: string[]
  is_global: boolean
  count: number
  is_jargon: boolean
  is_legacy_empty_meaning: boolean
  is_complete: boolean
  created_by: JargonCreatedBy
  created_timestamp: string
  updated_timestamp: string
}

/**
 * 聊天信息
 */
export interface JargonChatInfo {
  session_id: string
  chat_name: string
  platform: string | null
  account_id?: string | null
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
 * 黑话导出的聊天目标信息
 */
export interface JargonTargetInfo {
  platform: string
  id: string
  type: 'group' | 'private'
  account_id?: string | null
  scope?: string | null
  count: number
}

/**
 * 黑话导出条目
 */
export interface JargonExportItem {
  content: string
  meaning: string
  count: number
  is_jargon: boolean
  is_complete: boolean
  is_global: boolean
  created_by: JargonCreatedBy
  targets?: JargonTargetInfo[] | null
}

/**
 * 黑话导出响应
 */
export interface JargonExportResponse {
  success: boolean
  version: number
  type: 'maibot.jargon.export'
  exported_at: string
  include_chat_info: boolean
  count: number
  jargons: JargonExportItem[]
}

/**
 * 黑话导入请求
 */
export interface JargonImportRequest {
  target_session_ids: string[]
  jargons: JargonExportItem[]
  conflict_strategy?: 'skip' | 'overwrite'
}

/**
 * 黑话导入响应
 */
export interface JargonImportResponse {
  success: boolean
  message: string
  imported_count: number
  skipped_count: number
  failed_count: number
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
  meaning?: string
  session_id?: string
  session_ids?: string[]
  is_global?: boolean
}

/**
 * 黑话更新请求
 */
export interface JargonUpdateRequest {
  content?: string
  meaning?: string
  session_id?: string
  session_ids?: string[]
  is_global?: boolean
  is_jargon?: boolean
  created_by?: JargonCreatedBy
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
  manual_jargon: number
  pending?: number
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
