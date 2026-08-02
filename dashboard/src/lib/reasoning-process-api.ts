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
  display_title: string | null
  related_json_paths: string[]
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

export type ReasoningPromptClearStageResponse = {
  stage: string
  deleted_files: number
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

export type ReasoningReplayMessage = {
  role: string
  content: unknown
  tool_call_id?: string
  tool_calls?: unknown[]
}

export type ReasoningReplayRequest = {
  source_path?: string | null
  stage?: string
  model_name: string
  messages: ReasoningReplayMessage[]
  tool_definitions?: Record<string, unknown>[]
  temperature?: number | null
  max_tokens?: number | null
}

export type ReasoningReplayResponse = {
  success: boolean
  response: string
  reasoning: string
  model_name: string
  tool_calls?: unknown[] | null
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  prompt_cache_hit_tokens: number
  prompt_cache_miss_tokens: number
  duration_ms: number
  error?: string | null
}

export type ReasoningPromptListParams = {
  stage?: string
  session?: string
  action?: string
  search?: string
  targetStem?: string
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
      target_stem: params.targetStem ?? '',
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

export async function clearReasoningPromptStage(
  stage: string
): Promise<ReasoningPromptClearStageResponse> {
  return backendApi.delete<ReasoningPromptClearStageResponse>(
    `${API_BASE}/stages/${encodeURIComponent(stage)}`,
    {
      errorMessage: '清空推理过程失败',
    }
  )
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

export async function replayReasoningPrompt(
  request: ReasoningReplayRequest
): Promise<ReasoningReplayResponse> {
  return backendApi.post<ReasoningReplayResponse>(`${API_BASE}/replay`, {
    body: request,
    errorMessage: '重放推理请求失败',
  })
}
