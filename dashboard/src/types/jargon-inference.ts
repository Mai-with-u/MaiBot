export interface JargonInferenceFilters {
  search?: string
  session_id?: string
  session_ids?: string[]
  jargon_status?: 'confirmed_jargon' | 'confirmed_not_jargon' | 'manual_jargon' | 'pending'
  is_jargon?: boolean
  is_complete?: boolean
  is_global?: boolean
}

export interface JargonInferenceRequest {
  ids?: number[]
  filters?: JargonInferenceFilters
  mode: 'missing' | 'all'
  force: boolean
  last_status?: string
}

export interface JargonInferencePreview {
  total: number
  eligible: number
  skipped: number
  reasons: Record<string, number>
}

export interface JargonInferenceJob {
  id: string
  source: string
  status: string
  created_at: number
  updated_at: number
  total: number
  completed: number
  counts: Record<string, number>
}

export interface JargonInferenceTask {
  jargon_id: number
  content: string
  session_name: string
  status: string
  reason: string
  attempts: number
  requests: number
}

export interface JargonInferenceJobDetail {
  job: JargonInferenceJob
  items: JargonInferenceTask[]
  total: number
}

export interface JargonInferenceSettings {
  thresholds: number[]
  stop_after_last_threshold: boolean
  max_concurrency: number
  request_interval_seconds: number
  max_retries: number
  scheduled_enabled: boolean
  scan_interval_hours: number
  refresh_interval_hours: number
  require_new_evidence: boolean
  scheduled_scope: 'pending' | 'all' | 'failed'
  scheduled_session_ids: string[]
  max_items_per_scan: number
  evidence_limit: number
  history_limit: number
}

export interface JargonMeaningHistory {
  id: number
  jargon_id: number
  source: string
  previous_meaning: string
  meaning: string
  previous_is_jargon: boolean | null
  is_jargon: boolean | null
  created_at: number
}
