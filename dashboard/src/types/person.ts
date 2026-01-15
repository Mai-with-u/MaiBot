/**
 * 人物信息相关类型定义
 */

/**
 * 群昵称信息
 */
export interface GroupNickName {
  group_id: string
  group_nick_name: string
}

/**
 * 人物信息
 */
export interface PersonInfo {
  id: number
  is_known: boolean
  person_id: string
  person_name: string | null
  name_reason: string | null
  platform: string
  user_id: string
  nickname: string | null
  group_nick_name: GroupNickName[] | null
  memory_points: string | null
  know_times: number | null
  know_since: number | null
  last_know: number | null
}

/**
 * 人物列表响应
 */
export interface PersonListResponse {
  success: boolean
  total: number
  page: number
  page_size: number
  data: PersonInfo[]
}

/**
 * 人物详情响应
 */
export interface PersonDetailResponse {
  success: boolean
  data: PersonInfo
}

/**
 * 人物更新请求
 */
export interface PersonUpdateRequest {
  person_name?: string
  name_reason?: string
  nickname?: string
  memory_points?: string
  is_known?: boolean
}

/**
 * 人物更新响应
 */
export interface PersonUpdateResponse {
  success: boolean
  message: string
  data?: PersonInfo
}

/**
 * 人物删除响应
 */
export interface PersonDeleteResponse {
  success: boolean
  message: string
}

/**
 * 人物统计数据
 */
export interface PersonStats {
  total: number
  known: number
  unknown: number
  platforms: Record<string, number>
}

/**
 * 人物统计响应
 */
export interface PersonStatsResponse {
  success: boolean
  data: PersonStats
}
