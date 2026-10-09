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
  model_name: string | null
  duration_ms: number | null
  prompt_tokens: number | null
  completion_tokens: number | null
  total_tokens: number | null
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
  prompt_tokens: number | null
  completion_tokens: number | null
  total_tokens: number | null
  message_avatars: Record<string, ReasoningPromptMessageAvatar>
}

export type ReasoningPromptMessageAvatar = {
  message_id: string
  platform: string
  user_id: string
  display_name: string
  avatar_url: string | null
}

export type ContextItemMetaSnapshot = {
  item_id: string
  logical_turn_id: string | null
  timestamp: string
}

export type ContextItemSnapshot = {
  item_type: string
  meta: ContextItemMetaSnapshot
  parts?: Record<string, unknown>[]
  phase?: string | null
  representation?: string
  summary_parts?: string[]
  text_parts?: string[]
  tool_call?: Record<string, unknown>
  call_id?: string
  output?: string
  success?: boolean
  tool_name?: string
  action_type?: string
  details?: string[]
  display_summary?: string
  provider_type?: string
  source_count?: number
  status?: string
  [key: string]: unknown
}

export type GenerationTraceSnapshot = {
  provider: string
  endpoint: string
  model: string
  response_id: string | null
  status: string
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  prompt_cache_hit_tokens: number
  prompt_cache_miss_tokens: number
  output_item_ids: string[]
}

export type GenerationAttemptSnapshot = {
  attempt_id: string
  workflow_purpose: string
  workflow_attempt: number
  provider_attempt: number
  model_attempt: number
  status: string
  started_at: string
  duration_ms: number
  provider: string
  endpoint: string
  model: string
  client_type: string
  operation: string
  wire_protocol: string
  request_items: ContextItemSnapshot[]
  tool_definitions: Record<string, unknown>[]
  request_parameters: Record<string, unknown>
  wire_request: unknown
  wire_response: unknown
  output_items: ContextItemSnapshot[]
  trace?: GenerationTraceSnapshot | null
  error?: {
    type?: string
    status_code?: number | null
    message?: string
    response_body?: unknown
  } | null
  retry_interval?: number
  [key: string]: unknown
}

export type ReasoningReplayRequest = {
  source_path?: string | null
  stage?: string
  model_name: string
  item_schema_version: number
  request_items: ContextItemSnapshot[]
  tool_definitions?: Record<string, unknown>[]
  temperature?: number | null
  max_tokens?: number | null
}

// 后端 /replay 的 attempt 是 serialize_generation_attempt 的精简结构（仅元数据与 error），
// 不保证携带 attempt 级的 request_items/tool_definitions/output_items 等渲染字段。
export type ReasoningReplayWireAttempt = Partial<GenerationAttemptSnapshot> &
  Record<string, unknown>

// normalizeReplayResult 规范化后的重放结果：attempt 字段已补齐默认值，可安全渲染。
export type NormalizedReplayResult = Omit<ReasoningReplayResponse, 'generation_attempts'> & {
  generation_attempts: GenerationAttemptSnapshot[]
}

export type ReasoningReplayResponse = {
  schema_version: 6
  success: boolean
  output_items: ContextItemSnapshot[]
  generation_attempts: ReasoningReplayWireAttempt[]
  model_name: string
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

export async function getReasoningPromptImageUrl(path: string): Promise<string> {
  return resolveApiPath(`${API_BASE}/image?path=${encodeURIComponent(path)}`)
}

export async function replayReasoningPrompt(
  request: ReasoningReplayRequest
): Promise<ReasoningReplayResponse> {
  return backendApi.post<ReasoningReplayResponse>(`${API_BASE}/replay`, {
    body: request,
    errorMessage: '重放推理请求失败',
  })
}

export interface ReasoningToolCallRecord {
  call_id: string
  stage: string
  session: string
  stem: string
}

export interface ReasoningToolCallRecordsResponse {
  records: ReasoningToolCallRecord[]
}

/** 查找一次请求输出的工具调用里，哪些工具（如 reply）留有自己的推理过程记录。 */
export async function findReasoningToolCallRecords(params: {
  session: string
  sourceStage: string
  sourceStem: string
  callIds: string[]
}): Promise<ReasoningToolCallRecordsResponse> {
  return backendApi.get<ReasoningToolCallRecordsResponse>(`${API_BASE}/tool-call-records`, {
    query: {
      session: params.session,
      source_stage: params.sourceStage,
      source_stem: params.sourceStem,
      call_id: params.callIds,
    },
    cache: 'no-store',
    errorMessage: '查找工具推理记录失败',
  })
}

export interface ReasoningToolCallSourceResponse {
  record: ReasoningToolCallRecord | null
}

/** 从工具自己的推理记录（如回复器）反查发起这次调用的推理记录（通常是规划器）。 */
export async function findReasoningToolCallSource(params: {
  session: string
  stage: string
  stem: string
  callId: string
}): Promise<ReasoningToolCallSourceResponse> {
  return backendApi.get<ReasoningToolCallSourceResponse>(`${API_BASE}/tool-call-source`, {
    query: {
      session: params.session,
      stage: params.stage,
      stem: params.stem,
      call_id: params.callId,
    },
    cache: 'no-store',
    errorMessage: '查找发起调用的推理记录失败',
  })
}
