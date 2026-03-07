import { fetchWithAuth } from './fetch-with-auth'

// ========== 新的优化接口 ==========

export interface ChatSummary {
  chat_id: string
  plan_count: number
  latest_timestamp: number
  latest_filename: string
}

export interface PlannerOverview {
  total_chats: number
  total_plans: number
  chats: ChatSummary[]
}

export interface PlanLogSummary {
  chat_id: string
  timestamp: number
  filename: string
  action_count: number
  action_types: string[]  // 动作类型列表
  total_plan_ms: number
  llm_duration_ms: number
  reasoning_preview: string
}

export interface PlanLogDetail {
  type: string
  chat_id: string
  timestamp: number
  prompt: string
  reasoning: string
  raw_output: string
  actions: any[]
  timing: {
    prompt_build_ms: number
    llm_duration_ms: number
    total_plan_ms: number
    loop_start_time: number
  }
  extra: any
}

export interface PaginatedChatLogs {
  data: PlanLogSummary[]
  total: number
  page: number
  page_size: number
  chat_id: string
}

/**
 * 获取规划器总览 - 轻量级，只统计文件数量
 */
export async function getPlannerOverview(): Promise<PlannerOverview> {
  const response = await fetchWithAuth('/api/planner/overview')
  return response.json()
}

/**
 * 获取指定聊天的规划日志列表（分页）
 */
export async function getChatLogs(chatId: string, page = 1, pageSize = 20, search?: string): Promise<PaginatedChatLogs> {
  const params = new URLSearchParams({
    page: page.toString(),
    page_size: pageSize.toString()
  })
  if (search) {
    params.append('search', search)
  }
  const response = await fetchWithAuth(`/api/planner/chat/${chatId}/logs?${params}`)
  return response.json()
}

/**
 * 获取规划日志详情 - 按需加载
 */
export async function getLogDetail(chatId: string, filename: string): Promise<PlanLogDetail> {
  const response = await fetchWithAuth(`/api/planner/log/${chatId}/${filename}`)
  return response.json()
}

// ========== 兼容旧接口 ==========

export interface PlannerStats {
  total_chats: number
  total_plans: number
  avg_plan_time_ms: number
  avg_llm_time_ms: number
  recent_plans: PlanLogSummary[]
}

export interface PaginatedPlanLogs {
  data: PlanLogSummary[]
  total: number
  page: number
  page_size: number
}

export async function getPlannerStats(): Promise<PlannerStats> {
  const response = await fetchWithAuth('/api/planner/stats')
  return response.json()
}

export async function getAllLogs(page = 1, pageSize = 20): Promise<PaginatedPlanLogs> {
  const response = await fetchWithAuth(`/api/planner/all-logs?page=${page}&page_size=${pageSize}`)
  return response.json()
}

export async function getChatList(): Promise<string[]> {
  const response = await fetchWithAuth('/api/planner/chats')
  return response.json()
}

// ========== 回复器接口 ==========

export interface ReplierChatSummary {
  chat_id: string
  reply_count: number
  latest_timestamp: number
  latest_filename: string
}

export interface ReplierOverview {
  total_chats: number
  total_replies: number
  chats: ReplierChatSummary[]
}

export interface ReplyLogSummary {
  chat_id: string
  timestamp: number
  filename: string
  model: string
  success: boolean
  llm_ms: number
  overall_ms: number
  output_preview: string
}

export interface ReplyLogDetail {
  type: string
  chat_id: string
  timestamp: number
  prompt: string
  output: string
  processed_output: string[]
  model: string
  reasoning: string
  think_level: number
  timing: {
    prompt_ms: number
    overall_ms: number
    timing_logs: string[]
    llm_ms: number
    almost_zero: string
  }
  error: string | null
  success: boolean
}

export interface PaginatedReplyLogs {
  data: ReplyLogSummary[]
  total: number
  page: number
  page_size: number
  chat_id: string
}

/**
 * 获取回复器总览 - 轻量级，只统计文件数量
 */
export async function getReplierOverview(): Promise<ReplierOverview> {
  const response = await fetchWithAuth('/api/replier/overview')
  return response.json()
}

/**
 * 获取指定聊天的回复日志列表（分页）
 */
export async function getReplyChatLogs(chatId: string, page = 1, pageSize = 20, search?: string): Promise<PaginatedReplyLogs> {
  const params = new URLSearchParams({
    page: page.toString(),
    page_size: pageSize.toString()
  })
  if (search) {
    params.append('search', search)
  }
  const response = await fetchWithAuth(`/api/replier/chat/${chatId}/logs?${params}`)
  return response.json()
}

/**
 * 获取回复日志详情 - 按需加载
 */
export async function getReplyLogDetail(chatId: string, filename: string): Promise<ReplyLogDetail> {
  const response = await fetchWithAuth(`/api/replier/log/${chatId}/${filename}`)
  return response.json()
}
