import { resolveApiPath } from '@/lib/api-base'
import { backendApi } from '@/lib/http'

const API_BASE = '/api/webui/reasoning-process'

export type ReasoningPromptFile = {
  stage: string
  session_id: string
  resolved_session_id: string | null
  session_display_name: string | null
  platform: string | null
  chat_type: string | null
  target_id: string | null
  stem: string
  timestamp: number | null
  text_path: string | null
  html_path: string | null
  json_path: string | null
  output_preview: string | null
  action_preview: string | null
  has_behavior_choice_insert: boolean
  model_name: string | null
  duration_ms: number | null
  size: number
  modified_at: number
}

export type ReasoningPromptStageInfo = {
  name: string
  session_count: number
  latest_modified_at: number
}

export type ReasoningPromptSessionInfo = {
  name: string
  platform: string
  chat_type: string
  target_id: string
  resolved_session_id: string | null
  display_name: string
  account_id: string | null
  matched_current_account: boolean
}

export type ReasoningPromptListResponse = {
  items: ReasoningPromptFile[]
  total: number
  page: number
  page_size: number
  stages: string[]
  stage_infos: ReasoningPromptStageInfo[]
  sessions: string[]
  session_infos: ReasoningPromptSessionInfo[]
  selected_session: string
}

export type ReasoningPromptStagesResponse = {
  stages: string[]
  stage_infos: ReasoningPromptStageInfo[]
}

export type ReasoningPromptContentResponse = {
  path: string
  content: string
  size: number
  modified_at: number
  model_name: string | null
  duration_ms: number | null
  message_avatars: Record<string, ReasoningPromptMessageAvatar>
}

export type ReasoningPromptMessageAvatar = {
  message_id: string
  platform: string
  user_id: string
  display_name: string
  avatar_url: string | null
}

export type ReasoningPromptListParams = {
  stage?: string
  session?: string
  action?: string
  search?: string
  page?: number
  pageSize?: number
}

export async function listReasoningPromptFiles(
  params: ReasoningPromptListParams
): Promise<ReasoningPromptListResponse> {
  return backendApi.get<ReasoningPromptListResponse>(`${API_BASE}/files`, {
    query: {
      stage: params.stage ?? 'planner',
      session: params.session ?? 'auto',
      action: params.action ?? '',
      search: params.search ?? '',
      page: params.page ?? 1,
      page_size: params.pageSize ?? 50,
    },
    cache: 'no-store',
    errorMessage: '加载推理过程失败',
  })
}

export async function listReasoningPromptStages(): Promise<ReasoningPromptStagesResponse> {
  return backendApi.get<ReasoningPromptStagesResponse>(`${API_BASE}/stages`, {
    cache: 'no-store',
    errorMessage: '加载推理过程类型失败',
  })
}

export async function getReasoningPromptFile(
  path: string
): Promise<ReasoningPromptContentResponse> {
  return backendApi.get<ReasoningPromptContentResponse>(`${API_BASE}/file`, {
    query: { path },
    cache: 'no-store',
    errorMessage: '读取推理过程文件失败',
  })
}

export async function getReasoningPromptHtmlUrl(path: string): Promise<string> {
  return resolveApiPath(`${API_BASE}/html?path=${encodeURIComponent(path)}`)
}
