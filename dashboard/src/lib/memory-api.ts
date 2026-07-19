import type { PluginConfigSchema } from '@/lib/plugin-api'

import { backendApi } from '@/lib/http'
import type { HttpMethod } from '@/lib/http'

const API_BASE = '/api/webui/memory'

interface MemoryRequestOptions {
  method?: HttpMethod
  /** 请求体：对象走 JSON 序列化，FormData 原样发送 */
  body?: unknown
}

/**
 * 记忆 API 统一入口：拼接记忆模块路径前缀，请求经由主后端实例 backendApi。
 * 失败统一抛出 ApiError（含路由未命中诊断），不再静默重试本地兜底地址。
 */
function requestJson<T>(path: string, options: MemoryRequestOptions = {}): Promise<T> {
  return backendApi.request<T>(options.method ?? 'GET', `${API_BASE}${path}`, {
    body: options.body,
  })
}

export interface MemoryGraphNodePayload {
  id: string
  name: string
  attributes?: Record<string, unknown>
}

export interface MemoryGraphEdgePayload {
  source: string
  target: string
  weight: number
  relation_hashes?: string[]
  predicates?: string[]
  relation_count?: number
  evidence_count?: number
  label?: string
}

export interface MemoryGraphPayload {
  success: boolean
  nodes: MemoryGraphNodePayload[]
  edges: MemoryGraphEdgePayload[]
  total_nodes: number
  total_edges: number
}

export interface MemoryGraphSearchItem {
  type: 'entity' | 'relation'
  title: string
  matched_field: string
  matched_value: string
  entity_name?: string
  entity_hash?: string
  appearance_count?: number
  subject?: string
  predicate?: string
  object?: string
  relation_hash?: string
  confidence?: number
  created_at?: number
}

export interface MemoryGraphSearchPayload {
  success: boolean
  query: string
  limit: number
  count: number
  items: MemoryGraphSearchItem[]
  error?: string
}

export interface MemoryGraphRelationDetailPayload {
  hash: string
  subject: string
  predicate: string
  object: string
  text: string
  confidence: number
  paragraph_count: number
  paragraph_hashes: string[]
  source_paragraph: string
}

export interface MemoryGraphParagraphDetailPayload {
  hash: string
  content: string
  preview: string
  source: string
  created_at?: number | null
  updated_at?: number | null
  entity_count: number
  relation_count: number
  entities: string[]
  relations: string[]
}

export interface MemoryEvidenceGraphNodePayload {
  id: string
  type: 'entity' | 'relation' | 'paragraph'
  content: string
  metadata?: MemoryEvidenceGraphNodeMetadata
}

export interface MemoryEvidenceGraphEdgePayload {
  source: string
  target: string
  kind: 'mentions' | 'supports' | 'subject' | 'object'
  label: string
  weight: number
}

export interface MemoryEvidenceGraphPayload {
  nodes: MemoryEvidenceGraphNodePayload[]
  edges: MemoryEvidenceGraphEdgePayload[]
  focus_entities: string[]
}

export interface MemoryEvidenceEntityNodeMetadata extends Record<string, unknown> {
  entity_name?: string
}

export interface MemoryEvidenceRelationNodeMetadata extends Record<string, unknown> {
  hash?: string
  subject?: string
  predicate?: string
  object?: string
  confidence?: number
  paragraph_count?: number
  paragraph_hashes?: string[]
  text?: string
}

export interface MemoryEvidenceParagraphNodeMetadata extends Record<string, unknown> {
  hash?: string
  source?: string
  updated_at?: number | null
  entity_count?: number
  relation_count?: number
  preview?: string
}

export type MemoryEvidenceGraphNodeMetadata =
  | MemoryEvidenceEntityNodeMetadata
  | MemoryEvidenceRelationNodeMetadata
  | MemoryEvidenceParagraphNodeMetadata
  | Record<string, unknown>

export interface MemoryGraphNodeDetailPayload {
  success: boolean
  node: {
    id: string
    type: 'entity'
    content: string
    hash?: string
    appearance_count?: number
  }
  relations: MemoryGraphRelationDetailPayload[]
  paragraphs: MemoryGraphParagraphDetailPayload[]
  evidence_graph: MemoryEvidenceGraphPayload
}

export interface MemoryGraphEdgeDetailPayload {
  success: boolean
  edge: MemoryGraphEdgePayload
  relations: MemoryGraphRelationDetailPayload[]
  paragraphs: MemoryGraphParagraphDetailPayload[]
  evidence_graph: MemoryEvidenceGraphPayload
}

export interface MemoryGraphParagraphDetailResponsePayload {
  success: boolean
  paragraph: MemoryGraphParagraphDetailPayload
  evidence_graph: MemoryEvidenceGraphPayload
}

export interface MemoryVectorStoreSnapshot {
  available: boolean
  dimension: number
  num_vectors: number
  has_data: boolean
}

export interface MemoryVectorMigrationProgress extends Record<string, unknown> {
  total?: number
  processed?: number
  percent?: number
  elapsed_seconds?: number
  estimated_remaining_seconds?: number | null
}

export interface MemoryVectorAutoMigrationStatus {
  running?: boolean
  attempted?: boolean
  success?: boolean
  stage?: string
  progress?: MemoryVectorMigrationProgress
  last_error?: string
  started_at?: number | null
  finished_at?: number | null
  updated_at?: number | null
}

export interface MemoryVectorPoolsStatus {
  configured_mode?: string
  effective_mode?: string
  ready?: boolean
  single_pool?: MemoryVectorStoreSnapshot
  paragraph_pool?: MemoryVectorStoreSnapshot
  graph_pool?: MemoryVectorStoreSnapshot
  ready_manifest?: string
  auto_migration?: MemoryVectorAutoMigrationStatus
}

export interface MemoryRuntimeConfigPayload {
  success: boolean
  config: Record<string, unknown>
  data_dir: string
  embedding_dimension: number
  embedding_fingerprint?: Record<string, unknown>
  stored_embedding_fingerprint?: Record<string, unknown>
  embedding_fingerprint_status?: string
  fuzzy_modify_candidate_limit?: number
  stored_vector_dimension?: number
  vector_rebuild_required?: boolean
  vector_rebuild_message?: string
  auto_save: boolean
  relation_vectors_enabled: boolean
  vector_pools?: MemoryVectorPoolsStatus
  vector_pools_ready?: boolean
  vector_pools_effective_mode?: string
  runtime_ready: boolean
  embedding_degraded: boolean
  embedding_degraded_reason: string
  embedding_degraded_since?: number | null
  embedding_last_check?: number | null
  paragraph_vector_backfill_pending: number
  paragraph_vector_backfill_running: number
  paragraph_vector_backfill_failed: number
  paragraph_vector_backfill_done: number
}

export interface MemoryRuntimeSelfCheckPayload {
  success: boolean
  report?: Record<string, unknown>
  error?: string
}

export interface MemoryVectorRebuildPayload {
  success: boolean
  dry_run?: boolean
  counts?: Record<string, number>
  stats?: Record<string, { done: number; failed: number }>
  total?: number
  done?: number
  failed?: number
  errors?: string[]
  elapsed_ms?: number
  embedding_degraded?: boolean
  embedding_fingerprint?: Record<string, unknown>
  stored_embedding_fingerprint?: Record<string, unknown>
  embedding_fingerprint_status?: string
  stored_vector_dimension?: number
  embedding_dimension?: number
  vector_rebuild_required?: boolean
  vector_rebuild_message?: string
  self_check?: Record<string, unknown>
  error?: string
}

export interface MemoryConfigPayload {
  success: boolean
  config: Record<string, unknown>
  path: string
}

export interface MemoryRawConfigPayload {
  success: boolean
  config: string
  path: string
  exists?: boolean
  using_default?: boolean
}

export interface MemoryConfigSchemaPayload {
  success: boolean
  schema: PluginConfigSchema
  path: string
}

export interface MemoryImportGuidePayload {
  success: boolean
  content: string
  source?: string
  path?: string
  settings?: MemoryImportSettings
}

export interface MemoryTaskPayload {
  task_id?: string
  status?: string
  mode?: string
  created_at?: number
  updated_at?: number
  [key: string]: unknown
}

export interface MemoryTaskListPayload {
  success: boolean
  items: MemoryTaskPayload[]
  count?: number
  settings?: Record<string, unknown>
}

export type MemoryImportInputMode = 'text' | 'json'

export type MemoryImportTaskKind =
  | 'upload'
  | 'paste'
  | 'raw_scan'
  | 'lpmm_openie'
  | 'lpmm_convert'
  | 'temporal_backfill'
  | 'maibot_migration'

export interface MemoryImportSettings {
  max_queue_size?: number
  max_files_per_task?: number
  max_file_size_mb?: number
  max_paste_chars?: number
  default_file_concurrency?: number
  default_chunk_concurrency?: number
  default_narrative_window_size?: number
  default_narrative_overlap?: number
  default_factual_target_size?: number
  max_chunk_chars?: number
  max_file_concurrency?: number
  max_chunk_concurrency?: number
  poll_interval_ms?: number
  maibot_source_db_default?: string
  maibot_target_data_dir?: string
  path_aliases?: Record<string, string>
  llm_retry?: Record<string, number>
  convert_enable_staging_switch?: boolean
  convert_keep_backup_count?: number
}

export interface MemoryImportSettingsPayload {
  success: boolean
  settings: MemoryImportSettings
}

export interface MemoryImportPathAliasesPayload {
  success: boolean
  path_aliases: Record<string, string>
}

export interface MemoryImportChatTargetPayload {
  chat_id: string
  chat_name: string
  platform?: string | null
  group_id?: string | null
  user_id?: string | null
  account_id?: string | null
  scope?: string | null
  is_group: boolean
  last_active_at?: number | null
}

export interface MemoryImportChatTargetsPayload {
  success: boolean
  data: MemoryImportChatTargetPayload[]
}

export type MemoryTimelineEventCategory =
  | 'paragraph'
  | 'episode'
  | 'profile'
  | 'feedback'
  | 'delete'
  | 'maintenance'

export interface MemoryTimelineJumpTargetPayload {
  tab: 'graph' | 'episodes' | 'profiles' | 'feedback' | 'delete' | 'maintenance' | 'timeline'
  params: Record<string, string | number | boolean | null | undefined>
}

export interface MemoryTimelineEventPayload {
  event_id: string
  event_type: string
  category: MemoryTimelineEventCategory
  occurred_at: number
  chat_id: string
  chat_name: string
  title: string
  summary: string
  object_count: number
  key_id?: string
  source?: string
  attribution?: string
  metadata?: Record<string, unknown>
  jump_target: MemoryTimelineJumpTargetPayload
}

export interface MemoryTimelinePayload {
  success: boolean
  chat: Pick<MemoryImportChatTargetPayload, 'chat_id' | 'chat_name' | 'platform' | 'group_id' | 'user_id' | 'account_id' | 'is_group'>
  range: {
    time_start?: number | null
    time_end?: number | null
    min_time?: number | null
    max_time?: number | null
  }
  items: MemoryTimelineEventPayload[]
  summary: {
    total: number
    by_type: Record<string, number>
  }
}

export interface MemoryImportResolvePathPayload {
  success?: boolean
  alias: string
  relative_path: string
  resolved_path: string
  exists: boolean
  is_file: boolean
  is_dir: boolean
  error?: string
}

export interface MemoryImportChunkPayload {
  chunk_id: string
  index: number
  chunk_type: string
  status: string
  step: string
  failed_at: string
  retryable: boolean
  error: string
  progress: number
  content_preview: string
  updated_at: number
}

export interface MemoryImportFilePayload {
  file_id: string
  name: string
  source_kind: string
  input_mode: MemoryImportInputMode
  status: string
  current_step: string
  detected_strategy_type: string
  total_chunks: number
  done_chunks: number
  failed_chunks: number
  cancelled_chunks: number
  progress: number
  error: string
  created_at: number
  updated_at: number
  source_path?: string
  content_hash?: string
  retry_chunk_indexes?: number[]
  retry_mode?: string
  chunks?: MemoryImportChunkPayload[]
}

export interface MemoryImportRetrySummary {
  chunk_retry_files?: number
  chunk_retry_chunks?: number
  file_fallback_files?: number
  skipped_files?: number
  parent_task_id?: string
  skipped_details?: Array<Record<string, string>>
}

export interface MemoryImportTaskPayload extends MemoryTaskPayload {
  task_id: string
  source: string
  status: string
  current_step: string
  total_chunks: number
  done_chunks: number
  failed_chunks: number
  cancelled_chunks: number
  progress: number
  error: string
  file_count: number
  created_at: number
  started_at?: number | null
  finished_at?: number | null
  updated_at: number
  task_kind?: MemoryImportTaskKind | string
  schema_detected?: string
  artifact_paths?: Record<string, string>
  rollback_info?: Record<string, unknown>
  retry_parent_task_id?: string
  retry_summary?: MemoryImportRetrySummary
  params?: Record<string, unknown>
  files?: MemoryImportFilePayload[]
}

export interface MemoryImportTaskListPayload {
  success: boolean
  items: MemoryImportTaskPayload[]
  count?: number
  settings?: MemoryImportSettings
}

export interface MemoryImportTaskDetailPayload {
  success: boolean
  task?: MemoryImportTaskPayload
  error?: string
}

export interface MemoryImportChunkListPayload {
  success: boolean
  task_id?: string
  file_id?: string
  offset?: number
  limit?: number
  total?: number
  items?: MemoryImportChunkPayload[]
  error?: string
}

export interface MemoryImportActionPayload {
  success: boolean
  task?: MemoryImportTaskPayload
  error?: string
}

export interface MemoryTuningProfilePayload {
  success: boolean
  profile?: Record<string, unknown>
  runtime_profile?: Record<string, unknown>
  persistable_profile?: Record<string, unknown>
  settings?: Record<string, unknown>
  toml?: string
}

export interface MemoryDeleteCountsPayload {
  relations?: number
  paragraphs?: number
  entities?: number
  sources?: number
  requested_sources?: number
  matched_sources?: number
  [key: string]: number | undefined
}

export interface MemoryDeletePreviewItemPayload {
  item_type: string
  item_hash: string
  item_key?: string
  label?: string
  preview?: string
  source?: string
}

export interface MemoryDeleteRequestPayload {
  mode: string
  selector: Record<string, unknown> | string
  reason?: string
  requested_by?: string
}

export interface MemoryDeletePreviewPayload {
  success: boolean
  mode: string
  selector: Record<string, unknown> | string
  counts: MemoryDeleteCountsPayload
  sources: string[]
  items: MemoryDeletePreviewItemPayload[]
  item_count: number
  dry_run?: boolean
  requested_source_count?: number
  matched_source_count?: number
  vector_ids?: string[]
  error?: string
}

export interface MemoryDeleteExecutePayload {
  success: boolean
  mode: string
  operation_id: string
  counts: MemoryDeleteCountsPayload
  sources: string[]
  deleted_count: number
  deleted_entity_count: number
  deleted_relation_count: number
  deleted_paragraph_count: number
  deleted_source_count: number
  deleted_vector_count?: number
  requested_source_count?: number
  matched_source_count?: number
  error?: string
  deleted?: boolean | number
}

export interface MemoryDeleteOperationItemPayload {
  item_type: string
  item_hash: string
  item_key?: string
  payload?: Record<string, unknown>
  created_at?: number
}

export interface MemoryDeleteOperationPayload {
  operation_id: string
  mode: string
  selector?: Record<string, unknown> | string
  reason?: string | null
  requested_by?: string | null
  status?: string
  created_at?: number
  restored_at?: number | null
  summary?: Record<string, unknown>
  items?: MemoryDeleteOperationItemPayload[]
}

export interface MemoryDeleteOperationListPayload {
  success: boolean
  items: MemoryDeleteOperationPayload[]
  count?: number
  error?: string
}

export interface MemoryDeleteOperationDetailPayload {
  success: boolean
  operation?: MemoryDeleteOperationPayload | null
  error?: string
}

export interface MemoryCorrectionRequestPayload {
  request_text: string
  scope?: string
  person_id?: string
  person_keyword?: string
  chat_id?: string
  limit?: number
  requested_by?: string
  reason?: string
}

export interface MemoryCorrectionExecuteRequestPayload {
  plan_id: string
  confirmed?: boolean
  requested_by?: string
  reason?: string
}

export interface MemoryCorrectionRollbackRequestPayload {
  requested_by?: string
  reason?: string
}

export type MemoryCorrectionScope = 'person_profile' | 'memory' | (string & {})
export type MemoryCorrectionTargetType = 'paragraph' | 'relation' | (string & {})
export type MemoryCorrectionAction = 'mark_superseded' | 'ingest_text' | 'refresh_person_profile' | (string & {})
export type MemoryCorrectionStatus =
  | 'awaiting_confirmation'
  | 'executing'
  | 'executed'
  | 'failed'
  | 'rolled_back'
  | 'rollback_failed'
  | (string & {})

export interface MemoryCorrectionRelationPayload {
  subject: string
  predicate: string
  object: string
  confidence: number
}

export type MemoryCorrectionRelationCascadeAction =
  | 'mark_inactive'
  | 'mark_stale_evidence'
  | 'skipped_protected'

export type MemoryCorrectionEntityCascadeAction = 'record_impact_only'

export interface MemoryCorrectionStaleMarkPayload {
  paragraph_hash: string
  relation_hash: string
  query_tool_id: string
  task_id?: number | null
  reason: string
  source_type: string
  source_id: string
  source_operation_id: string
  created_at?: number | null
  updated_at?: number | null
}

export interface MemoryCorrectionRelationCascadePayload {
  paragraph_hash: string
  relation_hash: string
  action: MemoryCorrectionRelationCascadeAction
  reason: string
  source_reason?: string
  subject: string
  predicate: string
  object: string
  is_pinned?: boolean
  protected_until?: number | null
  is_inactive?: boolean
  inactive_since?: number | null
  preview_only?: boolean
  source_operation_id?: string
  previous_metadata?: Record<string, unknown>
  updated_metadata?: Record<string, unknown>
  previous_is_inactive?: boolean
  previous_inactive_since?: number | null
  written_mark?: MemoryCorrectionStaleMarkPayload | null
}

export interface MemoryCorrectionEntityCascadePayload {
  paragraph_hash: string
  entity_hash: string
  action: MemoryCorrectionEntityCascadeAction
  reason: string
  name: string
  type: string
  preview_only?: boolean
}

export interface MemoryCorrectionCascadeCountsPayload {
  relations: number
  relations_mark_inactive: number
  relations_mark_stale_evidence: number
  relations_skipped_protected: number
  entities: number
}

export interface MemoryCorrectionCascadePreviewPayload {
  relations: MemoryCorrectionRelationCascadePayload[]
  entities: MemoryCorrectionEntityCascadePayload[]
  counts: MemoryCorrectionCascadeCountsPayload
}

export interface MemoryCorrectionStaleMarkSnapshotPayload {
  paragraph_hash: string
  relation_hash: string
  source_type: string
  source_id: string
  source_operation_id: string
  previous_mark?: MemoryCorrectionStaleMarkPayload | null
  written_mark?: MemoryCorrectionStaleMarkPayload | null
}

export interface MemoryCorrectionTargetCascadePayload {
  relations_marked_inactive: MemoryCorrectionRelationCascadePayload[]
  relations_marked_stale: MemoryCorrectionRelationCascadePayload[]
  relations_skipped: MemoryCorrectionRelationCascadePayload[]
  impacted_entities: MemoryCorrectionEntityCascadePayload[]
  stale_mark_snapshots: MemoryCorrectionStaleMarkSnapshotPayload[]
}

export type MemoryCorrectionStaleRollbackAction =
  | 'deleted'
  | 'restored'
  | 'skipped_due_to_source_mismatch'
  | 'already_missing'
  | 'restore_failed'
  | 'invalid_target'

export interface MemoryCorrectionStaleMarkRollbackPayload {
  success: boolean
  action: MemoryCorrectionStaleRollbackAction
  paragraph_hash: string
  relation_hash: string
  before?: MemoryCorrectionStaleMarkPayload
  after?: MemoryCorrectionStaleMarkPayload | null
  current?: MemoryCorrectionStaleMarkPayload
  expected_source?: {
    source_type: string
    source_id: string
    source_operation_id: string
  }
  error?: string
}

export interface MemoryCorrectionCandidatePayload {
  candidate_id: string
  target_type: MemoryCorrectionTargetType
  evidence_type: string
  hash: string
  content: string
  source: string
  metadata: Record<string, unknown>
  score?: number | null
}

export interface MemoryCorrectionMarkSupersededOperationPayload {
  action: 'mark_superseded'
  candidate_id: string
  target_type: MemoryCorrectionTargetType
  hash: string
  reason: string
  valid_to?: number | null
}

export interface MemoryCorrectionIngestTextOperationPayload {
  action: 'ingest_text'
  text: string
  source_type: string
  chat_id: string
  person_ids: string[]
  participants: string[]
  tags: string[]
  relations: MemoryCorrectionRelationPayload[]
  valid_from?: number | null
  reason: string
}

export interface MemoryCorrectionRefreshPersonProfileOperationPayload {
  action: 'refresh_person_profile'
  person_id: string
}

export type MemoryCorrectionOperationPayload =
  | MemoryCorrectionMarkSupersededOperationPayload
  | MemoryCorrectionIngestTextOperationPayload
  | MemoryCorrectionRefreshPersonProfileOperationPayload
  | {
      action: MemoryCorrectionAction
      reason?: string
    }

export interface MemoryCorrectionPlanContentPayload {
  scope: MemoryCorrectionScope
  request_text: string
  person_id: string
  chat_id: string
  confidence: number
  risk_level: string
  reason: string
  operations: MemoryCorrectionOperationPayload[]
}

export interface MemoryCorrectionPreviewContentPayload {
  request_text: string
  scope: MemoryCorrectionScope
  person_id: string
  person_keyword: string
  chat_id: string
  candidates: MemoryCorrectionCandidatePayload[]
  operations: MemoryCorrectionOperationPayload[]
  cascade_preview?: MemoryCorrectionCascadePreviewPayload
  requires_confirmation: boolean
  confirm_threshold: number
  reason: string
}

export interface MemoryCorrectionAttemptPayload {
  status: string
  started_at?: number
  finished_at?: number
  requested_by?: string
  reason?: string
  recovered_from_stale_executing?: boolean
}

export interface MemoryCorrectionIngestResultPayload {
  operation: MemoryCorrectionIngestTextOperationPayload
  result: Record<string, unknown>
}

export interface MemoryCorrectionSupersededTargetPayload {
  target_type: MemoryCorrectionTargetType
  hash: string
  previous_metadata: Record<string, unknown>
  previous_is_inactive?: boolean
  previous_inactive_since?: number | null
  cascade?: MemoryCorrectionTargetCascadePayload
}

export interface MemoryCorrectionExecutionPayload {
  success?: boolean
  stored_ids?: string[]
  ingest_results?: MemoryCorrectionIngestResultPayload[]
  superseded_targets?: MemoryCorrectionSupersededTargetPayload[]
  refreshed_profiles?: Array<Record<string, unknown>>
  changed_at?: number
  changed_by?: string
  reason?: string
  attempt?: MemoryCorrectionAttemptPayload
  rollback?: MemoryCorrectionRollbackResultPayload
  error?: string
}

export interface MemoryCorrectionRollbackItemPayload {
  type: string
  result: Record<string, unknown>
}

export interface MemoryCorrectionRestoredTargetPayload {
  target_type: MemoryCorrectionTargetType
  hash: string
}

export interface MemoryCorrectionRestoreFailurePayload {
  target_type: MemoryCorrectionTargetType
  hash: string
  error: string
}

export interface MemoryCorrectionRollbackResultPayload {
  success: boolean
  stored_ids_deleted?: string[]
  stored_ids_delete_requested?: string[]
  new_relations_deactivated: string[]
  restored_targets: MemoryCorrectionRestoredTargetPayload[]
  restore_failures?: MemoryCorrectionRestoreFailurePayload[]
  stale_marks_deleted?: MemoryCorrectionStaleMarkRollbackPayload[]
  stale_marks_restored?: MemoryCorrectionStaleMarkRollbackPayload[]
  stale_marks_skipped?: MemoryCorrectionStaleMarkRollbackPayload[]
  items: MemoryCorrectionRollbackItemPayload[]
  requested_by: string
  reason: string
  error?: string
}

export interface MemoryCorrectionPlanPayload {
  plan_id: string
  request_text: string
  scope: MemoryCorrectionScope
  target_person_id: string
  target_chat_id: string
  status: MemoryCorrectionStatus
  confidence: number
  plan: MemoryCorrectionPlanContentPayload
  preview: MemoryCorrectionPreviewContentPayload
  execution: MemoryCorrectionExecutionPayload
  created_at: number
  updated_at: number
  executed_at?: number | null
  requested_by: string
  reason: string
}

export interface MemoryCorrectionPreviewPayload {
  success: boolean
  plan_id?: string
  plan?: MemoryCorrectionPlanPayload | null
  preview?: MemoryCorrectionPreviewContentPayload
  requires_confirmation?: boolean
  raw_plan?: Record<string, unknown>
  candidates?: MemoryCorrectionCandidatePayload[]
  request_text?: string
  error?: string
}

export interface MemoryCorrectionExecutePayload {
  success: boolean
  plan?: MemoryCorrectionPlanPayload | null
  execution?: MemoryCorrectionExecutionPayload
  requires_confirmation?: boolean
  error?: string
}

export interface MemoryCorrectionPlanListPayload {
  success: boolean
  items: MemoryCorrectionPlanPayload[]
  count?: number
  error?: string
}

export interface MemoryCorrectionPlanDetailPayload {
  success: boolean
  plan?: MemoryCorrectionPlanPayload | null
  error?: string
}

export interface MemoryCorrectionRollbackPayload {
  success: boolean
  plan?: MemoryCorrectionPlanPayload | null
  rollback?: MemoryCorrectionRollbackResultPayload
  error?: string
}

export interface MemoryFeedbackAffectedCountsPayload {
  relations?: number
  stale_paragraphs?: number
  episode_sources?: number
  profile_person_ids?: number
  correction_paragraphs?: number
  corrected_relations?: number
}

export interface MemoryFeedbackActionLogPayload {
  id: number
  task_id: number
  query_tool_id: string
  action_type: string
  target_hash: string
  reason?: string
  before_payload?: Record<string, unknown>
  after_payload?: Record<string, unknown>
  created_at?: number
}

export interface MemoryFeedbackCorrectionSummaryPayload {
  task_id: number
  query_tool_id: string
  session_id: string
  query_text: string
  query_timestamp?: number
  task_status: string
  decision: string
  decision_confidence: number
  feedback_message_count: number
  rollback_status: string
  affected_counts: MemoryFeedbackAffectedCountsPayload
  created_at?: number
  updated_at?: number
}

export interface MemoryFeedbackCorrectionDetailTaskPayload extends MemoryFeedbackCorrectionSummaryPayload {
  query_snapshot?: Record<string, unknown>
  decision_payload?: Record<string, unknown>
  rollback_plan_summary?: Record<string, unknown>
  rollback_result?: Record<string, unknown>
  rollback_error?: string
  rollback_requested_by?: string
  rollback_reason?: string
  rollback_requested_at?: number
  rolled_back_at?: number
  action_logs?: MemoryFeedbackActionLogPayload[]
}

export interface MemoryFeedbackCorrectionListPayload {
  success: boolean
  items: MemoryFeedbackCorrectionSummaryPayload[]
  count?: number
  error?: string
}

export interface MemoryFeedbackCorrectionDetailPayload {
  success: boolean
  task?: MemoryFeedbackCorrectionDetailTaskPayload | null
  error?: string
}

export interface MemoryFeedbackCorrectionRollbackPayload {
  success: boolean
  already_rolled_back?: boolean
  result?: Record<string, unknown>
  task?: MemoryFeedbackCorrectionDetailTaskPayload | null
  error?: string
}

export interface MemorySourceItemPayload {
  source: string
  paragraph_count?: number
  relation_count?: number
  episode_rebuild_blocked?: boolean
  [key: string]: unknown
}

export interface MemorySourceListPayload {
  success: boolean
  items: MemorySourceItemPayload[]
  count: number
}

export interface MemoryEpisodeItemPayload extends Record<string, unknown> {
  episode_id?: string
  id?: string
  title?: string
  summary?: string
  content?: string
  source?: string
  person_id?: string
  person_name?: string
  time_start?: number | null
  time_end?: number | null
  created_at?: number | null
  updated_at?: number | null
}

export interface MemoryEpisodeParagraphPayload extends Record<string, unknown> {
  hash?: string
  content?: string
  preview?: string
  source?: string
  created_at?: number | null
  updated_at?: number | null
}

export interface MemoryEpisodeListPayload {
  success: boolean
  items: MemoryEpisodeItemPayload[]
  count?: number
  error?: string
}

export interface MemoryEpisodeDetailPayload {
  success: boolean
  episode?: MemoryEpisodeItemPayload & {
    paragraphs?: MemoryEpisodeParagraphPayload[]
  }
  error?: string
}

export interface MemoryEpisodeStatusPayload extends Record<string, unknown> {
  success: boolean
  counts?: Record<string, number>
  running?: Array<Record<string, unknown>>
  failed?: Array<Record<string, unknown>>
  error?: string
}

export interface MemoryEpisodeActionPayload extends Record<string, unknown> {
  success: boolean
  error?: string
  detail?: string
  processed?: number
  rebuilt?: number
  failed?: number
  unfinished?: number
  failures?: Array<{ source?: string; error?: string; reason?: string }>
  unfinished_items?: Array<{ source?: string; error?: string; reason?: string }>
}

export interface MemoryProfileItemPayload extends Record<string, unknown> {
  person_id: string
  person_name?: string
  profile_version?: number
  profile_text?: string
  updated_at?: number | null
  expires_at?: number | null
  source_note?: string
  has_manual_override?: boolean
  manual_override?: Record<string, unknown> | string | null
}

export interface MemoryProfileListPayload {
  success: boolean
  items: MemoryProfileItemPayload[]
  count?: number
  error?: string
}

export interface MemoryProfileQueryPayload extends Record<string, unknown> {
  success?: boolean
  profile?: MemoryProfileItemPayload | Record<string, unknown>
  person_id?: string
  profile_text?: string
  evidence?: Array<Record<string, unknown>>
  error?: string
}

export interface MemoryProfileEvidenceItemPayload extends Record<string, unknown> {
  evidence_key?: string
  evidence_type?: string
  hash?: string
  content?: string
  source?: string
  source_type?: string
  metadata?: Record<string, unknown>
  score?: number | null
  confidence?: number | null
  correction_mode?: string
  deletable?: boolean
  not_deletable_reason?: string
}

export interface MemoryProfileEvidencePayload extends Record<string, unknown> {
  success: boolean
  person_id?: string
  person_name?: string
  profile_text?: string
  auto_profile_text?: string
  profile_version?: number
  updated_at?: number | null
  expires_at?: number | null
  profile_source?: string
  has_manual_override?: boolean
  manual_override_text?: string
  evidence?: MemoryProfileEvidenceItemPayload[]
  evidence_count?: number
  error?: string
}

export interface MemoryProfileEvidenceCorrectPayload extends Record<string, unknown> {
  success: boolean
  person_id?: string
  evidence?: MemoryProfileEvidenceItemPayload
  delete_result?: Record<string, unknown>
  operation_id?: string
  refreshed_profile?: Record<string, unknown>
  refreshed_evidence?: MemoryProfileEvidencePayload
  error?: string
}

export interface MemoryProfileOverridePayload extends Record<string, unknown> {
  success: boolean
  override?: Record<string, unknown>
  deleted?: boolean
  person_id?: string
  error?: string
}

export interface MemoryMaintenanceItemPayload extends Record<string, unknown> {
  hash?: string
  relation_hash?: string
  subject?: string
  predicate?: string
  object?: string
  text?: string
  deleted_at?: number | null
  updated_at?: number | null
  source?: string
}

export interface MemoryRecycleBinPayload {
  success: boolean
  items: MemoryMaintenanceItemPayload[]
  count?: number
  error?: string
}

export interface MemoryMaintenanceActionPayload extends Record<string, unknown> {
  success: boolean
  detail?: string
  error?: string
}

export async function getMemoryGraph(limit: number = 120): Promise<MemoryGraphPayload> {
  return requestJson<MemoryGraphPayload>(`/graph?limit=${limit}`)
}

export async function getMemoryGraphSearch(
  query: string,
  limit: number = 50,
): Promise<MemoryGraphSearchPayload> {
  const params = new URLSearchParams({
    query,
    limit: String(limit),
  })
  return requestJson<MemoryGraphSearchPayload>(`/graph/search?${params.toString()}`)
}

export async function getMemoryGraphNodeDetail(
  nodeId: string,
  options?: {
    relationLimit?: number
    paragraphLimit?: number
    evidenceNodeLimit?: number
  },
): Promise<MemoryGraphNodeDetailPayload> {
  const params = new URLSearchParams({
    node_id: nodeId,
    relation_limit: String(options?.relationLimit ?? 20),
    paragraph_limit: String(options?.paragraphLimit ?? 20),
    evidence_node_limit: String(options?.evidenceNodeLimit ?? 80),
  })
  return requestJson<MemoryGraphNodeDetailPayload>(`/graph/node-detail?${params.toString()}`)
}

export async function getMemoryGraphEdgeDetail(
  source: string,
  target: string,
  options?: {
    paragraphLimit?: number
    evidenceNodeLimit?: number
  },
): Promise<MemoryGraphEdgeDetailPayload> {
  const params = new URLSearchParams({
    source,
    target,
    paragraph_limit: String(options?.paragraphLimit ?? 20),
    evidence_node_limit: String(options?.evidenceNodeLimit ?? 80),
  })
  return requestJson<MemoryGraphEdgeDetailPayload>(`/graph/edge-detail?${params.toString()}`)
}

export async function getMemoryGraphParagraphDetail(
  paragraphHash: string,
  options?: {
    evidenceNodeLimit?: number
  },
): Promise<MemoryGraphParagraphDetailResponsePayload> {
  const params = new URLSearchParams({
    paragraph_hash: paragraphHash,
    evidence_node_limit: String(options?.evidenceNodeLimit ?? 80),
  })
  return requestJson<MemoryGraphParagraphDetailResponsePayload>(`/graph/paragraph-detail?${params.toString()}`)
}

export async function previewMemoryDelete(
  payload: MemoryDeleteRequestPayload,
): Promise<MemoryDeletePreviewPayload> {
  return requestJson<MemoryDeletePreviewPayload>('/delete/preview', {
    method: 'POST',
    body: payload,
  })
}

export async function executeMemoryDelete(
  payload: MemoryDeleteRequestPayload,
): Promise<MemoryDeleteExecutePayload> {
  return requestJson<MemoryDeleteExecutePayload>('/delete/execute', {
    method: 'POST',
    body: payload,
  })
}

export async function restoreMemoryDelete(payload: {
  operation_id: string
  mode?: string
  selector?: Record<string, unknown> | string
  reason?: string
  requested_by?: string
}): Promise<Record<string, unknown>> {
  return requestJson('/delete/restore', {
    method: 'POST',
    body: payload,
  })
}

export async function getMemoryDeleteOperations(
  limit: number = 20,
  mode: string = '',
): Promise<MemoryDeleteOperationListPayload> {
  const params = new URLSearchParams({ limit: String(limit) })
  if (mode.trim()) {
    params.set('mode', mode)
  }
  return requestJson<MemoryDeleteOperationListPayload>(`/delete/operations?${params.toString()}`)
}

export async function getMemoryDeleteOperation(
  operationId: string,
): Promise<MemoryDeleteOperationDetailPayload> {
  return requestJson<MemoryDeleteOperationDetailPayload>(`/delete/operations/${encodeURIComponent(operationId)}`)
}

export async function previewMemoryCorrection(
  payload: MemoryCorrectionRequestPayload,
): Promise<MemoryCorrectionPreviewPayload> {
  const body = { ...payload }
  if (body.limit === undefined) {
    delete body.limit
  }
  return requestJson<MemoryCorrectionPreviewPayload>('/corrections/preview', {
    method: 'POST',
    body,
  })
}

export async function executeMemoryCorrection(
  payload: MemoryCorrectionExecuteRequestPayload,
): Promise<MemoryCorrectionExecutePayload> {
  return requestJson<MemoryCorrectionExecutePayload>('/corrections/execute', {
    method: 'POST',
    body: payload,
  })
}

export async function getMemoryCorrectionPlans(
  options?: {
    limit?: number
    status?: string
    scope?: string
  },
): Promise<MemoryCorrectionPlanListPayload> {
  const params = new URLSearchParams({
    limit: String(options?.limit ?? 50),
  })
  if (options?.status?.trim()) {
    params.set('status', options.status.trim())
  }
  if (options?.scope?.trim()) {
    params.set('scope', options.scope.trim())
  }
  return requestJson<MemoryCorrectionPlanListPayload>(`/corrections/plans?${params.toString()}`)
}

export async function getMemoryCorrectionPlan(
  planId: string,
): Promise<MemoryCorrectionPlanDetailPayload> {
  return requestJson<MemoryCorrectionPlanDetailPayload>(`/corrections/plans/${encodeURIComponent(planId)}`)
}

export async function rollbackMemoryCorrectionPlan(
  planId: string,
  payload: MemoryCorrectionRollbackRequestPayload,
): Promise<MemoryCorrectionRollbackPayload> {
  return requestJson<MemoryCorrectionRollbackPayload>(`/corrections/plans/${encodeURIComponent(planId)}/rollback`, {
    method: 'POST',
    body: payload,
  })
}

export async function getMemoryFeedbackCorrections(
  options?: {
    limit?: number
    status?: string
    rollbackStatus?: string
    query?: string
  },
): Promise<MemoryFeedbackCorrectionListPayload> {
  const params = new URLSearchParams({
    limit: String(options?.limit ?? 50),
  })
  if (options?.status?.trim()) {
    params.set('status', options.status.trim())
  }
  if (options?.rollbackStatus?.trim()) {
    params.set('rollback_status', options.rollbackStatus.trim())
  }
  if (options?.query?.trim()) {
    params.set('query', options.query.trim())
  }
  return requestJson<MemoryFeedbackCorrectionListPayload>(`/feedback-corrections?${params.toString()}`)
}

export async function getMemoryFeedbackCorrection(
  taskId: number,
): Promise<MemoryFeedbackCorrectionDetailPayload> {
  return requestJson<MemoryFeedbackCorrectionDetailPayload>(`/feedback-corrections/${taskId}`)
}

export async function rollbackMemoryFeedbackCorrection(
  taskId: number,
  payload: {
    requested_by?: string
    reason?: string
  },
): Promise<MemoryFeedbackCorrectionRollbackPayload> {
  return requestJson<MemoryFeedbackCorrectionRollbackPayload>(`/feedback-corrections/${taskId}/rollback`, {
    method: 'POST',
    body: payload,
  })
}

export async function getMemorySources(): Promise<MemorySourceListPayload> {
  return requestJson<MemorySourceListPayload>('/sources')
}

export async function getMemoryTimeline(options: {
  chatId: string
  timeStart?: number
  timeEnd?: number
  types?: string[]
  limit?: number
}): Promise<MemoryTimelinePayload> {
  const params = new URLSearchParams({
    chat_id: options.chatId,
    limit: String(options.limit ?? 100),
  })
  if (options.timeStart !== undefined) {
    params.set('time_start', String(options.timeStart))
  }
  if (options.timeEnd !== undefined) {
    params.set('time_end', String(options.timeEnd))
  }
  const cleanTypes = (options.types ?? []).map((item) => item.trim()).filter(Boolean)
  if (cleanTypes.length > 0) {
    params.set('types', cleanTypes.join(','))
  }
  return requestJson<MemoryTimelinePayload>(`/timeline?${params.toString()}`)
}

export async function getMemoryEpisodes(options?: {
  query?: string
  limit?: number
  source?: string
  personId?: string
  platform?: string
  userId?: string
  timeStart?: number
  timeEnd?: number
}): Promise<MemoryEpisodeListPayload> {
  const params = new URLSearchParams({
    query: options?.query ?? '',
    limit: String(options?.limit ?? 20),
    source: options?.source ?? '',
    person_id: options?.personId ?? '',
    platform: options?.platform ?? '',
    user_id: options?.userId ?? '',
  })
  if (options?.timeStart !== undefined) {
    params.set('time_start', String(options.timeStart))
  }
  if (options?.timeEnd !== undefined) {
    params.set('time_end', String(options.timeEnd))
  }
  return requestJson<MemoryEpisodeListPayload>(`/episodes?${params.toString()}`)
}

export async function getMemoryEpisode(episodeId: string): Promise<MemoryEpisodeDetailPayload> {
  return requestJson<MemoryEpisodeDetailPayload>(`/episodes/${encodeURIComponent(episodeId)}`)
}

export async function rebuildMemoryEpisodes(payload: {
  source?: string
  sources?: string[]
  all?: boolean
}): Promise<MemoryEpisodeActionPayload> {
  return requestJson<MemoryEpisodeActionPayload>('/episodes/rebuild', {
    method: 'POST',
    body: payload,
  })
}

export async function getMemoryEpisodeStatus(limit: number = 20): Promise<MemoryEpisodeStatusPayload> {
  return requestJson<MemoryEpisodeStatusPayload>(`/episodes/status?limit=${limit}`)
}

export async function processMemoryEpisodePending(payload: {
  limit?: number
  max_retry?: number
}): Promise<MemoryEpisodeActionPayload> {
  return requestJson<MemoryEpisodeActionPayload>('/episodes/process-pending', {
    method: 'POST',
    body: payload,
  })
}

export async function getMemoryProfiles(limit: number = 50): Promise<MemoryProfileListPayload> {
  return requestJson<MemoryProfileListPayload>(`/profiles?limit=${limit}`)
}

export async function searchMemoryProfiles(options: {
  personId?: string
  personKeyword?: string
  platform?: string
  userId?: string
  limit?: number
}): Promise<MemoryProfileListPayload> {
  const params = new URLSearchParams({
    person_id: options.personId ?? '',
    person_keyword: options.personKeyword ?? '',
    platform: options.platform ?? '',
    user_id: options.userId ?? '',
    limit: String(options.limit ?? 50),
  })
  return requestJson<MemoryProfileListPayload>(`/profiles/search?${params.toString()}`)
}

export async function queryMemoryProfile(options: {
  personId?: string
  personKeyword?: string
  platform?: string
  userId?: string
  limit?: number
  forceRefresh?: boolean
}): Promise<MemoryProfileQueryPayload> {
  const params = new URLSearchParams({
    person_id: options.personId ?? '',
    person_keyword: options.personKeyword ?? '',
    platform: options.platform ?? '',
    user_id: options.userId ?? '',
    limit: String(options.limit ?? 12),
    force_refresh: options.forceRefresh ? 'true' : 'false',
  })
  return requestJson<MemoryProfileQueryPayload>(`/profiles/query?${params.toString()}`)
}

export async function setMemoryProfileOverride(payload: {
  person_id: string
  override_text: string
  updated_by?: string
  source?: string
}): Promise<MemoryProfileOverridePayload> {
  return requestJson<MemoryProfileOverridePayload>('/profiles/override', {
    method: 'POST',
    body: payload,
  })
}

export async function deleteMemoryProfileOverride(personId: string): Promise<MemoryProfileOverridePayload> {
  return requestJson<MemoryProfileOverridePayload>(`/profiles/override/${encodeURIComponent(personId)}`, {
    method: 'DELETE',
  })
}

export async function getMemoryProfileEvidence(options: {
  personId: string
  limit?: number
  forceRefresh?: boolean
}): Promise<MemoryProfileEvidencePayload> {
  const params = new URLSearchParams({
    limit: String(options.limit ?? 12),
    force_refresh: options.forceRefresh ? 'true' : 'false',
  })
  return requestJson<MemoryProfileEvidencePayload>(`/profiles/${encodeURIComponent(options.personId)}/evidence?${params.toString()}`)
}

export async function correctMemoryProfileEvidence(payload: {
  person_id: string
  evidence_type: string
  hash: string
  requested_by?: string
  reason?: string
  refresh?: boolean
  limit?: number
}): Promise<MemoryProfileEvidenceCorrectPayload> {
  return requestJson<MemoryProfileEvidenceCorrectPayload>(`/profiles/${encodeURIComponent(payload.person_id)}/evidence/correct`, {
    method: 'POST',
    body: {
      evidence_type: payload.evidence_type,
      hash: payload.hash,
      requested_by: payload.requested_by ?? 'knowledge_base',
      reason: payload.reason ?? 'profile_evidence_correction',
      refresh: payload.refresh ?? true,
      limit: payload.limit ?? 12,
    },
  })
}

export async function getMemoryRecycleBin(limit: number = 50): Promise<MemoryRecycleBinPayload> {
  return requestJson<MemoryRecycleBinPayload>(`/maintenance/recycle-bin?limit=${limit}`)
}

function maintainMemory(path: string, payload: { target: string; hours?: number }): Promise<MemoryMaintenanceActionPayload> {
  return requestJson<MemoryMaintenanceActionPayload>(path, {
    method: 'POST',
    body: payload,
  })
}

export async function restoreMaintainedMemory(target: string): Promise<MemoryMaintenanceActionPayload> {
  return maintainMemory('/maintenance/restore', { target })
}

export async function reinforceMemory(target: string): Promise<MemoryMaintenanceActionPayload> {
  return maintainMemory('/maintenance/reinforce', { target })
}

export async function freezeMemory(target: string): Promise<MemoryMaintenanceActionPayload> {
  return maintainMemory('/maintenance/freeze', { target })
}

export async function protectMemory(target: string, hours?: number): Promise<MemoryMaintenanceActionPayload> {
  return maintainMemory('/maintenance/protect', hours === undefined ? { target } : { target, hours })
}

export async function getMemoryRuntimeConfig(): Promise<MemoryRuntimeConfigPayload> {
  return requestJson<MemoryRuntimeConfigPayload>('/runtime/config')
}

export async function refreshMemoryRuntimeSelfCheck(): Promise<MemoryRuntimeSelfCheckPayload> {
  return requestJson<MemoryRuntimeSelfCheckPayload>('/runtime/self-check/refresh', {
    method: 'POST',
  })
}

export async function rebuildMemoryRuntimeVectors(payload: {
  dry_run?: boolean
  batch_size?: number
  include_relations?: boolean | null
} = {}): Promise<MemoryVectorRebuildPayload> {
  return requestJson<MemoryVectorRebuildPayload>('/runtime/vectors/rebuild', {
    method: 'POST',
    body: payload,
  })
}

export async function getMemoryConfigSchema(): Promise<MemoryConfigSchemaPayload> {
  return requestJson<MemoryConfigSchemaPayload>('/config/schema')
}

export async function getMemoryConfig(): Promise<MemoryConfigPayload> {
  return requestJson<MemoryConfigPayload>('/config')
}

export async function updateMemoryConfig(config: Record<string, unknown>): Promise<{ success: boolean; message?: string }> {
  return requestJson('/config', {
    method: 'PUT',
    body: { config },
  })
}

export async function getMemoryConfigRaw(): Promise<MemoryRawConfigPayload> {
  return requestJson<MemoryRawConfigPayload>('/config/raw')
}

export async function updateMemoryConfigRaw(config: string): Promise<{ success: boolean; message?: string }> {
  return requestJson('/config/raw', {
    method: 'PUT',
    body: { config },
  })
}

export async function getMemoryImportGuide(): Promise<MemoryImportGuidePayload> {
  return requestJson<MemoryImportGuidePayload>('/import/guide')
}

export async function getMemoryImportSettings(): Promise<MemoryImportSettingsPayload> {
  return requestJson<MemoryImportSettingsPayload>('/import/settings')
}

export async function getMemoryImportPathAliases(): Promise<MemoryImportPathAliasesPayload> {
  return requestJson<MemoryImportPathAliasesPayload>('/import/path-aliases')
}

export async function getMemoryImportChatTargets(): Promise<MemoryImportChatTargetsPayload> {
  return requestJson<MemoryImportChatTargetsPayload>('/import/chat-targets')
}

export async function resolveMemoryImportPath(payload: {
  alias: string
  relative_path?: string
  must_exist?: boolean
}): Promise<MemoryImportResolvePathPayload> {
  return requestJson<MemoryImportResolvePathPayload>('/import/resolve-path', {
    method: 'POST',
    body: payload,
  })
}

export async function getMemoryImportTasks(limit: number = 20): Promise<MemoryImportTaskListPayload> {
  return requestJson<MemoryImportTaskListPayload>(`/import/tasks?limit=${limit}`)
}

export async function getMemoryImportTask(taskId: string, includeChunks: boolean = false): Promise<MemoryImportTaskDetailPayload> {
  return requestJson<MemoryImportTaskDetailPayload>(
    `/import/tasks/${encodeURIComponent(taskId)}?include_chunks=${includeChunks ? 'true' : 'false'}`,
  )
}

export async function getMemoryImportTaskChunks(
  taskId: string,
  fileId: string,
  offset: number = 0,
  limit: number = 50,
): Promise<MemoryImportChunkListPayload> {
  return requestJson<MemoryImportChunkListPayload>(
    `/import/tasks/${encodeURIComponent(taskId)}/chunks/${encodeURIComponent(fileId)}?offset=${offset}&limit=${limit}`,
  )
}

export async function createMemoryUploadImport(files: File[], payload: Record<string, unknown>): Promise<MemoryImportActionPayload> {
  const formData = new FormData()
  files.forEach((file) => {
    formData.append('files', file)
  })
  formData.append('payload_json', JSON.stringify(payload))
  return requestJson<MemoryImportActionPayload>('/import/upload', {
    method: 'POST',
    body: formData,
  })
}

export async function createMemoryPasteImport(payload: Record<string, unknown>): Promise<MemoryImportActionPayload> {
  return requestJson<MemoryImportActionPayload>('/import/paste', {
    method: 'POST',
    body: payload,
  })
}

export async function createMemoryRawScanImport(payload: Record<string, unknown>): Promise<MemoryImportActionPayload> {
  return requestJson<MemoryImportActionPayload>('/import/raw-scan', {
    method: 'POST',
    body: payload,
  })
}

export async function createMemoryLpmmOpenieImport(payload: Record<string, unknown>): Promise<MemoryImportActionPayload> {
  return requestJson<MemoryImportActionPayload>('/import/lpmm-openie', {
    method: 'POST',
    body: payload,
  })
}

export async function createMemoryLpmmConvertImport(payload: Record<string, unknown>): Promise<MemoryImportActionPayload> {
  return requestJson<MemoryImportActionPayload>('/import/lpmm-convert', {
    method: 'POST',
    body: payload,
  })
}

export async function createMemoryTemporalBackfillImport(payload: Record<string, unknown>): Promise<MemoryImportActionPayload> {
  return requestJson<MemoryImportActionPayload>('/import/temporal-backfill', {
    method: 'POST',
    body: payload,
  })
}

export async function createMemoryMaibotMigrationImport(payload: Record<string, unknown>): Promise<MemoryImportActionPayload> {
  return requestJson<MemoryImportActionPayload>('/import/maibot-migration', {
    method: 'POST',
    body: payload,
  })
}

export async function cancelMemoryImportTask(taskId: string): Promise<MemoryImportActionPayload> {
  return requestJson<MemoryImportActionPayload>(`/import/tasks/${encodeURIComponent(taskId)}/cancel`, {
    method: 'POST',
  })
}

export async function retryMemoryImportTask(
  taskId: string,
  payload: {
    overrides?: Record<string, unknown>
  } = {},
): Promise<MemoryImportActionPayload> {
  return requestJson<MemoryImportActionPayload>(`/import/tasks/${encodeURIComponent(taskId)}/retry`, {
    method: 'POST',
    body: payload,
  })
}

export async function getMemoryTuningProfile(): Promise<MemoryTuningProfilePayload> {
  return requestJson<MemoryTuningProfilePayload>('/retrieval_tuning/profile')
}

export async function getMemoryTuningTasks(limit: number = 20): Promise<MemoryTaskListPayload> {
  return requestJson<MemoryTaskListPayload>(`/retrieval_tuning/tasks?limit=${limit}`)
}

export async function createMemoryTuningTask(payload: Record<string, unknown>): Promise<{
  success: boolean
  task?: MemoryTaskPayload
  error?: string
  message?: string
}> {
  return requestJson('/retrieval_tuning/tasks', {
    method: 'POST',
    body: payload,
  })
}

export async function applyBestMemoryTuningProfile(
  taskId: string,
  payload: { persist?: boolean; validate?: boolean } = {},
): Promise<{ success: boolean; error?: string; message?: string; persisted?: boolean; runtime_rebuilt?: boolean; validation_passed?: boolean }> {
  return requestJson(`/retrieval_tuning/tasks/${encodeURIComponent(taskId)}/apply-best`, {
    method: 'POST',
    body: payload,
  })
}

export async function getMemoryTuningReport(taskId: string, format: 'md' | 'json' = 'md'): Promise<{ success: boolean; content: string; path: string; error?: string }> {
  return requestJson(`/retrieval_tuning/tasks/${encodeURIComponent(taskId)}/report?format=${format}`)
}
