import { backendApi } from '@/lib/http'

export interface AISearchCandidate {
  id: string
  title: string
  description: string
  category: string
}

export interface AISearchRequest {
  query: string
  language: string
  candidates: AISearchCandidate[]
}

export interface AISearchResult {
  id: string
  score: number
  reason: string
}

export interface AISearchResponse {
  success: boolean
  cached: boolean
  model_name: string
  expanded_terms: string[]
  results: AISearchResult[]
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
}

export async function searchWithAI(
  payload: AISearchRequest,
  signal?: AbortSignal
): Promise<AISearchResponse> {
  return backendApi.post<AISearchResponse>('/api/webui/search/ai', {
    body: payload,
    signal,
    errorMessage: 'AI 搜索失败',
  })
}
