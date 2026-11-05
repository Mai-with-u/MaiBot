import { backendApi } from '@/lib/http'

export interface AISearchCandidate {
  id: string
  title: string
  description: string
  category: string
  document: string
}

export interface AISearchRequest {
  query: string
  language: string
  candidates: AISearchCandidate[]
  help_session_id?: string
  allow_plugin_config_write?: boolean
}

export interface AISearchResult {
  id: string
  reason: string
}

export interface AISearchPlugin {
  id: string
  name: string
  description: string
  version: string
  compatibility: 'unknown' | 'compatible' | 'incompatible'
  recommended_version: string | null
  reasons: string[]
}

export interface AISearchInstallRequest {
  id: string
  plugin_id: string
  name: string
  version: string
  repository_url: string
  branch: string
  mode: 'releases' | 'branch'
}

export interface AISearchConfigUpdate {
  id: string
  plugin_id: string
  name: string
  changes: Array<{ path: string[]; before: unknown; after: unknown }>
  message: string
}

export interface AISearchArtifact {
  kind: 'markdown' | 'svg' | 'image'
  title: string
  content: string
}

export interface AISearchResponse {
  cached: boolean
  model_name: string
  answer: string
  suggestions: string[]
  sources: Array<{ title: string; url: string }>
  artifacts?: AISearchArtifact[]
  expanded_terms: string[]
  results: AISearchResult[]
  plugins: AISearchPlugin[]
  install_requests: AISearchInstallRequest[]
  config_updates: AISearchConfigUpdate[]
  total_tokens: number
  grounding_error: string
  used_local_config: boolean
}

export type AISearchProgressStage =
  | 'start'
  | 'planning'
  | 'skill'
  | 'plan'
  | 'tool'
  | 'finalizing'
  | 'correcting'
  | 'cache_hit'
  | 'completed'
  | 'failed'
export type AISearchProgressStatus = 'started' | 'completed' | 'failed'

export type AIHelpSkill = 'setup' | 'troubleshoot' | 'plugin' | 'develop'

export interface AIHelpPlanItem {
  title: string
  status: 'pending' | 'in_progress' | 'completed'
}

export interface AISearchProgressEvent {
  type: 'progress'
  stage: AISearchProgressStage
  status?: AISearchProgressStatus | null
  round?: number | null
  tool?: string
  skill?: AIHelpSkill | null
  plan?: AIHelpPlanItem[]
  query?: string
  targets?: string[]
  titles?: string[]
  count?: number | null
  error?: string
  install_request?: AISearchInstallRequest | null
  config_update?: AISearchConfigUpdate | null
}

type AIHelpSessionEvent = { type: 'session'; id: string }

type AISearchStreamEvent =
  | AISearchProgressEvent
  | AIHelpSessionEvent
  | { type: 'result'; response: AISearchResponse }
  | { type: 'error'; message: string; status?: number }

export async function searchWithAIStream(
  payload: AISearchRequest,
  onProgress: (event: AISearchProgressEvent | AIHelpSessionEvent) => void,
  signal?: AbortSignal
): Promise<AISearchResponse> {
  const response = await backendApi.post<Response>('/api/webui/search/ai/stream', {
    body: payload,
    signal,
    parse: 'response',
    errorMessage: 'AI 搜索失败',
  })
  if (!response.body) {
    throw new Error('AI 搜索接口没有返回可读取的数据流')
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let result: AISearchResponse | null = null

  const processLine = (line: string) => {
    if (!line.trim()) {
      return
    }
    const event = JSON.parse(line) as AISearchStreamEvent
    if (event.type === 'progress' || event.type === 'session') {
      onProgress(event)
    } else if (event.type === 'result') {
      result = event.response
    } else if (event.type === 'error') {
      throw new Error(event.message || 'AI 搜索失败')
    }
  }

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) {
        buffer += decoder.decode()
        break
      }
      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() ?? ''
      lines.forEach(processLine)
    }
    processLine(buffer)
  } finally {
    reader.releaseLock()
  }

  if (!result) {
    throw new Error('AI 搜索接口未返回最终结果')
  }
  return result
}

export interface AIHelpSession {
  id: string
  title: string
  updated_at: number
}

export interface AIHelpSessionDetail extends AIHelpSession {
  turns: Array<{
    id: number
    question: string
    reply: Pick<AISearchResponse, 'answer' | 'sources' | 'suggestions' | 'artifacts'> | null
    error: string
  }>
}

const helpSessionsPath = '/api/webui/search/help/sessions'

export function listAIHelpSessions(signal?: AbortSignal) {
  return backendApi.get<AIHelpSession[]>(helpSessionsPath, { signal })
}

export function getAIHelpSession(id: string, signal?: AbortSignal) {
  return backendApi.get<AIHelpSessionDetail>(`${helpSessionsPath}/${encodeURIComponent(id)}`, {
    signal,
  })
}

export async function deleteAIHelpSession(id: string) {
  await backendApi.delete<string>(`${helpSessionsPath}/${encodeURIComponent(id)}`, {
    parse: 'text',
  })
}

export const AI_HELP_IMAGE_PATH = '/api/webui/reasoning-process/image'

export function getAIHelpImage(path: string, signal: AbortSignal) {
  return backendApi.get<Blob>(AI_HELP_IMAGE_PATH, { query: { path }, parse: 'blob', signal })
}
