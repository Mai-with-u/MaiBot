/**
 * 行为学习（Behavior）API
 *
 * 请求样板（认证、解析、错误格式化）由 @/lib/http 的请求客户端承担；
 * 本文件只声明 endpoint 与响应类型。公开函数保持 throw 契约：
 * HTTP / 网络层失败由请求客户端以 ApiError 抛出。
 */
import { backendApi } from '@/lib/http'

const API_BASE = '/api/webui/behavior'

export interface BehaviorChatInfo {
  session_id: string
  display_name: string
  platform: string
  chat_type: string
  path_count: number
  cluster_count: number
  scene_count: number
  last_active_time: string | null
}

export interface BehaviorClusterTag {
  tag: string
  probability: number
  display?: string
}

export interface BehaviorSceneCluster {
  id: number | null
  name: string
  tags: BehaviorClusterTag[]
  source_count: number
  update_time: string | null
}

export interface BehaviorClusterItem extends BehaviorSceneCluster {
  session_id: string | null
  chat_name: string
  path_count: number
  enabled_path_count: number
  activation_count: number
  success_count: number
  failure_count: number
  observed_path_count: number
  self_reflection_path_count: number
  last_active_time: string | null
}

export interface BehaviorPathItem {
  id: number
  session_id: string | null
  chat_name: string
  scene_cluster_id: number | null
  scene_cluster_name: string
  scene_cluster_tags: BehaviorClusterTag[]
  scene_cluster_source_count: number
  actor_type: string
  learning_type: string
  action: string
  outcome: string
  count: number
  activation_count: number
  success_count: number
  failure_count: number
  score: number
  enabled: boolean
  last_active_time: string | null
  last_feedback_time: string | null
  update_time: string | null
}

export interface BehaviorPathListResponse {
  success: boolean
  total: number
  page: number
  page_size: number
  data: BehaviorPathItem[]
}

export interface BehaviorClusterListResponse {
  success: boolean
  total: number
  page: number
  page_size: number
  data: BehaviorClusterItem[]
}

export interface BehaviorReadableTag {
  tag: string
  kind: string
  cluster_key: string
  display: string
  probability: number
}

export interface BehaviorSceneGraphNode {
  id: number
  label: string
  short_label: string
  session_id: string
  source_count: number
  score: number
  path_count: number
  activation_count: number
  success_count: number
  failure_count: number
  update_time: string | null
  tags: BehaviorReadableTag[]
}

export interface BehaviorSceneGraphEdge {
  source: number
  target: number
  source_label: string
  target_label: string
  weight: number
  shared_tags: Array<{
    tag: string
    display: string
    left: number
    right: number
    overlap: number
  }>
}

export interface BehaviorTagNetworkNode {
  id: string
  kind: string
  cluster_key: string
  label: string
  aliases: string[]
  weight: number
  scene_count: number
  source_count: number
}

export interface BehaviorTagNetworkEdge {
  source: string
  target: string
  weight: number
  count: number
}

export interface BehaviorGraphData {
  scene_cluster_network: {
    nodes: BehaviorSceneGraphNode[]
    edges: BehaviorSceneGraphEdge[]
  }
  tag_network: {
    nodes: BehaviorTagNetworkNode[]
    edges: BehaviorTagNetworkEdge[]
  }
}

export interface BehaviorGraphNode {
  id: number
  kind: string
  label: string
  score: number
  source_count: number
}

export interface BehaviorGraphEdge {
  id: string
  source: string
  target: string
  kind: string
  weight: number
  count: number
}

export interface BehaviorPathDetail {
  path: BehaviorPathItem
  scene_cluster: BehaviorSceneCluster
  evidence: unknown[]
  feedback: unknown[]
  nodes: BehaviorGraphNode[]
  edges: BehaviorGraphEdge[]
}

export interface BehaviorDescriptor {
  node_kind: string
  name: string
  weight: number
}

export interface BehaviorMatchedCluster {
  cluster_id: number
  name: string
  score: number
  tags: BehaviorClusterTag[]
  source_count: number
}

export interface BehaviorRetrievalCandidate {
  behavior_id: number
  score: number
  path: BehaviorPathItem | null
}

export interface BehaviorRetrievalDebugStage {
  direct_tag_count: number
  expanded_tag_count?: number
  hop_counts?: Record<string, number>
  total_query_tag_count?: number
  cluster_count: number
}

export interface BehaviorRetrievalDebugInfo {
  direct?: BehaviorRetrievalDebugStage
  spread?: BehaviorRetrievalDebugStage
  direct_top_score?: number
  direct_locked?: boolean
  direct_lock_threshold?: number
  locked_direct_spread_factor?: number
}

export interface BehaviorScenarioDebugProfile {
  summary: string
  confidence: number
  tag_clusters: Array<{
    kind: string
    tags: string[]
  }>
}

export interface BehaviorRetrievalDebugPayload {
  retrieval_mode: string
  input_mode?: string
  scenario_profile?: BehaviorScenarioDebugProfile
  descriptors: BehaviorDescriptor[]
  matched_clusters: BehaviorMatchedCluster[]
  candidate_scores: Array<{ behavior_id: number; score: number }>
  candidates: BehaviorRetrievalCandidate[]
  retrieval_debug: BehaviorRetrievalDebugInfo
  error?: string
}

export interface BehaviorRetrievalDebugRequest {
  session_id?: string
  include_global: boolean
  retrieval_mode?: string
  scene_text?: string
  summary?: string
  tag_clusters: Array<{ tag_name: string; tag_aliases: string[] }>
  need: { tag_name: string; tag_aliases: string[] }
  other_traits: Array<{ tag_name: string; tag_aliases: string[] }>
  max_count: number
}

export async function listBehaviorChats(): Promise<{ success: boolean; data: BehaviorChatInfo[] }> {
  return backendApi.get<{ success: boolean; data: BehaviorChatInfo[] }>(`${API_BASE}/chats`)
}

export async function listBehaviorPaths(params: {
  session_id?: string
  search?: string
  enabled?: string
  actor_type?: string
  learning_type?: string
  sort_by?: string
  sort_order?: string
  page?: number
  page_size?: number
}): Promise<BehaviorPathListResponse> {
  // 字符串参数为空字符串时跳过（与原 URLSearchParams 构建语义一致）
  return backendApi.get<BehaviorPathListResponse>(`${API_BASE}/paths`, {
    query: {
      session_id: params.session_id || undefined,
      search: params.search || undefined,
      enabled: params.enabled || undefined,
      actor_type: params.actor_type || undefined,
      learning_type: params.learning_type || undefined,
      sort_by: params.sort_by || undefined,
      sort_order: params.sort_order || undefined,
      page: params.page,
      page_size: params.page_size,
    },
  })
}

export async function listBehaviorClusters(params: {
  session_id?: string
  search?: string
  sort_by?: string
  sort_order?: string
  page?: number
  page_size?: number
}): Promise<BehaviorClusterListResponse> {
  // 字符串参数为空字符串时跳过（与原 URLSearchParams 构建语义一致）
  return backendApi.get<BehaviorClusterListResponse>(`${API_BASE}/clusters`, {
    query: {
      session_id: params.session_id || undefined,
      search: params.search || undefined,
      sort_by: params.sort_by || undefined,
      sort_order: params.sort_order || undefined,
      page: params.page,
      page_size: params.page_size,
    },
  })
}

export async function getBehaviorGraphData(params: {
  session_id?: string
} = {}): Promise<{ success: boolean; data: BehaviorGraphData }> {
  return backendApi.get<{ success: boolean; data: BehaviorGraphData }>(`${API_BASE}/graph-data`, {
    query: { session_id: params.session_id || undefined },
  })
}

export async function getBehaviorPathDetail(pathId: number): Promise<{ success: boolean; data: BehaviorPathDetail }> {
  return backendApi.get<{ success: boolean; data: BehaviorPathDetail }>(`${API_BASE}/paths/${pathId}`)
}

export async function debugBehaviorRetrieval(
  payload: BehaviorRetrievalDebugRequest
): Promise<{ success: boolean; data: BehaviorRetrievalDebugPayload }> {
  return backendApi.post<{ success: boolean; data: BehaviorRetrievalDebugPayload }>(
    `${API_BASE}/retrieval-debug`,
    { body: payload }
  )
}
