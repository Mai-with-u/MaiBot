/**
 * 人物信息相关类型定义
 */

/**
 * 群昵称信息
 */
export interface GroupNickName {
  group_id: string
  group_cardname: string
}

/**
 * 人物信息
 */
export interface PersonInfo {
  id: number
  is_known: boolean
  person_id: string
  individual_id: string
  source_person_name: string | null
  person_name: string | null
  platform: string
  user_id: string
  nickname: string | null
  group_nick_name: GroupNickName[] | null
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
  expected_individual_id?: string
  expected_profile?: IndividualProfile
  person_name?: string
  nickname?: string
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
  individuals: number
  linked_individuals: number
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

/** 跨平台统一个体，成员保留各自的账号资料。 */
export interface IndividualProfile {
  display_name: string
  is_known: boolean
}

export interface ProfileOrigin extends IndividualProfile {
  individual_id: string
  person_id: string
  platform: string
  user_id: string
}

export interface Individual extends IndividualProfile {
  individual_id: string
  display_name: string
  members: PersonInfo[]
  profile_sources: ProfileOrigin[]
}

export interface IndividualDirectoryEntry extends Individual {
  member_count: number
}

export interface IndividualDirectoryResponse {
  data: IndividualDirectoryEntry[]
  total: number
  page: number
  page_size: number
}

export type ProfileField = keyof IndividualProfile

export interface IndividualMergePreview {
  target_individual_id: string
  revision: string
  profile: IndividualProfile
  conflicts: ProfileField[]
  sources: (IndividualProfile & { individual_id: string; origins: ProfileOrigin[] })[]
  members: Record<string, string>
}
