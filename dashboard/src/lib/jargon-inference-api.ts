import { backendApi } from '@/lib/http'
import type {
  JargonInferenceJob,
  JargonInferenceJobDetail,
  JargonInferencePreview,
  JargonInferenceRequest,
  JargonInferenceSettings,
  JargonMeaningHistory,
} from '@/types/jargon-inference'

const API_BASE = '/api/webui/jargon/inference'

export function previewJargonInference(body: JargonInferenceRequest) {
  return backendApi.post<JargonInferencePreview>(`${API_BASE}/preview`, { body })
}

export function startJargonInference(body: JargonInferenceRequest) {
  return backendApi.post<JargonInferenceJob>(`${API_BASE}/jobs`, { body })
}

export function getJargonInferenceJobs(
  options: { page?: number; page_size?: number; source?: string; active_only?: boolean } = {}
) {
  return backendApi.get<{
    items: JargonInferenceJob[]
    total: number
    page: number
    page_size: number
  }>(`${API_BASE}/jobs`, { query: options })
}

export function getJargonInferenceJob(id: string, page = 1) {
  return backendApi.get<JargonInferenceJobDetail>(`${API_BASE}/jobs/${encodeURIComponent(id)}`, {
    query: { page, page_size: 50 },
  })
}

export function cancelJargonInference(id: string) {
  return backendApi.post<JargonInferenceJob>(`${API_BASE}/jobs/${encodeURIComponent(id)}/cancel`)
}

export function retryJargonInference(id: string) {
  return backendApi.post<JargonInferenceJob>(`${API_BASE}/jobs/${encodeURIComponent(id)}/retry`)
}

export function getJargonInferenceSettings() {
  return backendApi.get<JargonInferenceSettings>(`${API_BASE}/settings`)
}

export function saveJargonInferenceSettings(
  body: JargonInferenceSettings & { expected_settings?: JargonInferenceSettings }
) {
  return backendApi.put<JargonInferenceSettings>(`${API_BASE}/settings`, { body })
}

export function rescanJargonInference() {
  return backendApi.post<JargonInferenceJob>(`${API_BASE}/rescan`)
}

export function getJargonMeaningHistory(id: number) {
  return backendApi.get<{ items: JargonMeaningHistory[] }>(`${API_BASE}/history/${id}`)
}

export function restoreJargonMeaning(id: number) {
  return backendApi.post<{ success: boolean }>(`${API_BASE}/history/${id}/restore`)
}

export function lockJargonInference(id: number, locked: boolean) {
  return backendApi.post<{ success: boolean }>(`${API_BASE}/lock/${id}`, {
    body: { locked },
  })
}
