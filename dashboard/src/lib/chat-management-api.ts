import { backendApi } from '@/lib/http'

export type ChatStreamType = 'group' | 'private'

export interface ChatStream {
  id: number | null
  session_id: string
  display_name: string
  chat_type: ChatStreamType
  target_id: string
  platform: string
  account_id: string | null
  scope: string | null
  user_id: string | null
  user_nickname: string | null
  user_cardname: string | null
  group_id: string | null
  group_name: string | null
  message_count: number
  expression_count: number
  jargon_count: number
  created_at: number | null
  last_active_at: number | null
  latest_message: string
  latest_message_at: number | null
}

export interface ChatConfigRule {
  platform: string
  item_id: string
  type: ChatStreamType | string
  use?: boolean
  learn?: boolean
  is_default?: boolean
  is_platform_default?: boolean
  is_wildcard?: boolean
}

export interface ChatLearningStatus {
  use: boolean
  learn: boolean
  matched_rule: ChatConfigRule | null
}

export interface ChatTalkFrequencyRule {
  platform: string
  item_id: string
  type: ChatStreamType | string
  time: string
  value: number
  value_label: string
  target_priority: number
  time_priority: number | null
  time_active: boolean
  is_effective: boolean
  is_default_target: boolean
}

export interface ChatTalkFrequencyDetail {
  enabled: boolean
  base_value: number
  base_value_label: string
  effective_value: number
  effective_value_label: string
  current_time: string
  matched_rules: ChatTalkFrequencyRule[]
}

export interface ChatPromptDetail {
  base_prompt_type: ChatStreamType | string
  base_prompt_title: string
  base_prompt: string
  chat_prompts: ChatPromptRule[]
}

export interface ChatPromptRule {
  index: number
  platform: string
  item_id: string
  rule_type: ChatStreamType | string
  prompt: string
}

export interface AdapterPolicyStatus {
  allowed: boolean
  configured: boolean
  chat_type: ChatStreamType | string
  target_id: string
  list_type: string
  source: string
  reason: string
  matched_ids: string[]
}

export interface ChatAdapterStatus {
  adapter_id: string
  plugin_id: string
  gateway_name: string
  platform: string
  account_id: string | null
  scope: string | null
  protocol: string
  route_type: string
  send_bound: boolean
  receive_bound: boolean
  routed: boolean
  policy: AdapterPolicyStatus
}

export interface ChatStreamDetail {
  session_id: string
  display_name: string
  chat_type: ChatStreamType
  platform: string
  target_id: string
  group_id: string | null
  user_id: string | null
  expression: ChatLearningStatus
  behavior?: ChatLearningStatus
  jargon: ChatLearningStatus
  talk_frequency: ChatTalkFrequencyDetail
  prompts: ChatPromptDetail
  adapters: ChatAdapterStatus[]
}

interface ChatStreamsResponse {
  success: boolean
  sessions?: ChatStream[]
  total?: number
}

interface ChatStreamDetailResponse {
  success: boolean
  detail?: ChatStreamDetail
}

interface ChatTargetResolveResponse {
  success: boolean
  found: boolean
  session?: ChatStream | null
}

export interface ChatTargetResolveRequest {
  platform: string
  item_id: string
  rule_type: ChatStreamType | string
}

interface ChatTargetsResolveResponse {
  success: boolean
  results: Array<{
    found: boolean
    session?: ChatStream | null
  }>
}

export interface ChatStreamDeleteItem {
  key: string
  label: string
  count: number
  unlinked?: number
}

export interface ChatStreamDeleteResult {
  success: boolean
  session_id: string
  deleted_total: number
  jargons?: {
    deleted: number
    unlinked: number
    removed_refs: number
  }
  items: ChatStreamDeleteItem[]
}

interface UpdateTalkFrequencyPayload {
  previous_time?: string | null
  time: string
  value: number
}

interface UpdateLearningPayload {
  use: boolean
  learn: boolean
}

interface UpdateChatPromptPayload {
  prompt: string
}

interface UpdateAdapterPolicyPayload {
  adapter_id: string
  action: 'allow' | 'block' | 'inherit'
}

export async function getChatStreams(limit = 1000): Promise<ChatStream[]> {
  const result = await backendApi.get<ChatStreamsResponse>('/api/chat/sessions', {
    query: { limit },
  })
  return result.sessions ?? []
}

export async function resolveChatTarget(
  platform: string,
  itemId: string,
  ruleType: ChatStreamType | string
): Promise<ChatTargetResolveResponse> {
  const [result] = await resolveChatTargets([
    {
      platform,
      item_id: itemId,
      rule_type: ruleType,
    },
  ])
  return { success: true, found: Boolean(result?.found), session: result?.session ?? null }
}

export async function resolveChatTargets(
  targets: ChatTargetResolveRequest[]
): Promise<ChatTargetsResolveResponse['results']> {
  const result = await backendApi.post<ChatTargetsResolveResponse>('/api/chat/resolve-targets', {
    body: { targets },
    errorMessage: '解析聊天流失败',
  })
  return result.results ?? []
}

export async function getChatStreamDetail(sessionId: string): Promise<ChatStreamDetail> {
  const result = await backendApi.get<ChatStreamDetailResponse>(
    `/api/chat/sessions/${encodeURIComponent(sessionId)}`
  )
  if (!result.detail) {
    throw new Error('聊天流详情为空')
  }
  return result.detail
}

export async function updateChatStreamTalkFrequency(
  sessionId: string,
  payload: UpdateTalkFrequencyPayload
): Promise<ChatStreamDetail> {
  const result = await backendApi.put<ChatStreamDetailResponse>(
    `/api/chat/sessions/${encodeURIComponent(sessionId)}/talk-frequency`,
    {
      body: payload,
      errorMessage: '保存发言频率失败',
    }
  )
  if (!result.detail) {
    throw new Error('聊天流详情为空')
  }
  return result.detail
}

export async function deleteChatStreamTalkFrequency(
  sessionId: string,
  time: string
): Promise<ChatStreamDetail> {
  const result = await backendApi.delete<ChatStreamDetailResponse>(
    `/api/chat/sessions/${encodeURIComponent(sessionId)}/talk-frequency`,
    {
      query: { time },
      errorMessage: '删除发言频率规则失败',
    }
  )
  if (!result.detail) {
    throw new Error('聊天流详情为空')
  }
  return result.detail
}

export async function updateChatStreamLearning(
  sessionId: string,
  kind: 'expression' | 'jargon' | 'behavior',
  payload: UpdateLearningPayload
): Promise<ChatStreamDetail> {
  const result = await backendApi.put<ChatStreamDetailResponse>(
    `/api/chat/sessions/${encodeURIComponent(sessionId)}/learning/${kind}`,
    {
      body: payload,
      errorMessage: '保存学习配置失败',
    }
  )
  if (!result.detail) {
    throw new Error('聊天流详情为空')
  }
  return result.detail
}

export async function upsertChatStreamPrompt(
  sessionId: string,
  payload: UpdateChatPromptPayload,
  index?: number
): Promise<ChatStreamDetail> {
  const result = await backendApi.put<ChatStreamDetailResponse>(
    `/api/chat/sessions/${encodeURIComponent(sessionId)}/prompts`,
    {
      body: payload,
      query: index === undefined ? undefined : { index },
      errorMessage: '保存聊天 Prompt 失败',
    }
  )
  if (!result.detail) {
    throw new Error('聊天流详情为空')
  }
  return result.detail
}

export async function deleteChatStreamPrompt(
  sessionId: string,
  index: number
): Promise<ChatStreamDetail> {
  const result = await backendApi.delete<ChatStreamDetailResponse>(
    `/api/chat/sessions/${encodeURIComponent(sessionId)}/prompts/${index}`,
    {
      errorMessage: '删除聊天 Prompt 失败',
    }
  )
  if (!result.detail) {
    throw new Error('聊天流详情为空')
  }
  return result.detail
}

export async function updateChatStreamAdapterPolicy(
  sessionId: string,
  payload: UpdateAdapterPolicyPayload
): Promise<ChatStreamDetail> {
  const result = await backendApi.put<ChatStreamDetailResponse>(
    `/api/chat/sessions/${encodeURIComponent(sessionId)}/adapters/policy`,
    {
      body: payload,
      errorMessage: '保存适配器放行规则失败',
    }
  )
  if (!result.detail) {
    throw new Error('聊天流详情为空')
  }
  return result.detail
}

export async function deleteChatStream(sessionId: string): Promise<ChatStreamDeleteResult> {
  return backendApi.delete<ChatStreamDeleteResult>(
    `/api/chat/sessions/${encodeURIComponent(sessionId)}`,
    {
      errorMessage: '删除聊天流失败',
    }
  )
}
