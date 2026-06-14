/**
 * 规划器 / 回复器监控 API
 *
 * 请求样板（认证、解析、错误格式化）由 @/lib/http 的请求客户端承担；
 * 本文件只声明 endpoint 与响应类型。请求失败时抛出 ApiError（throw 契约）。
 */
import { backendApi } from '@/lib/http'

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
  return backendApi.get<PlannerOverview>('/api/planner/overview', {
    errorMessage: '获取规划器总览失败',
  })
}

/**
 * 获取指定聊天的规划日志列表（分页）
 */
export async function getChatLogs(chatId: string, page = 1, pageSize = 20, search?: string): Promise<PaginatedChatLogs> {
  return backendApi.get<PaginatedChatLogs>(`/api/planner/chat/${chatId}/logs`, {
    query: {
      page,
      page_size: pageSize,
      search: search || undefined,
    },
    errorMessage: '获取规划日志列表失败',
  })
}

/**
 * 获取规划日志详情 - 按需加载
 */
export async function getLogDetail(chatId: string, filename: string): Promise<PlanLogDetail> {
  return backendApi.get<PlanLogDetail>(`/api/planner/log/${chatId}/${filename}`, {
    errorMessage: '获取规划日志详情失败',
  })
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
  return backendApi.get<PlannerStats>('/api/planner/stats', {
    errorMessage: '获取规划器统计失败',
  })
}

export async function getAllLogs(page = 1, pageSize = 20): Promise<PaginatedPlanLogs> {
  return backendApi.get<PaginatedPlanLogs>('/api/planner/all-logs', {
    query: { page, page_size: pageSize },
    errorMessage: '获取规划日志失败',
  })
}

export async function getChatList(): Promise<string[]> {
  return backendApi.get<string[]>('/api/planner/chats', {
    errorMessage: '获取聊天列表失败',
  })
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
  return backendApi.get<ReplierOverview>('/api/replier/overview', {
    errorMessage: '获取回复器总览失败',
  })
}

/**
 * 获取指定聊天的回复日志列表（分页）
 */
export async function getReplyChatLogs(chatId: string, page = 1, pageSize = 20, search?: string): Promise<PaginatedReplyLogs> {
  return backendApi.get<PaginatedReplyLogs>(`/api/replier/chat/${chatId}/logs`, {
    query: {
      page,
      page_size: pageSize,
      search: search || undefined,
    },
    errorMessage: '获取回复日志列表失败',
  })
}

/**
 * 获取回复日志详情 - 按需加载
 */
export async function getReplyLogDetail(chatId: string, filename: string): Promise<ReplyLogDetail> {
  return backendApi.get<ReplyLogDetail>(`/api/replier/log/${chatId}/${filename}`, {
    errorMessage: '获取回复日志详情失败',
  })
}
