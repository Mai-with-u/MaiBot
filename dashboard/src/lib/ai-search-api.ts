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
  answer: string
  suggestions: string[]
  sources: Array<{ title: string; url: string }>
  expanded_terms: string[]
  results: AISearchResult[]
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
}

export type AISearchProgressStage =
  | 'start'
  | 'planning'
  | 'tool'
  | 'finalizing'
  | 'correcting'
  | 'cache_hit'
  | 'completed'
  | 'failed'
export type AISearchProgressStatus = 'started' | 'completed' | 'failed'

export interface AISearchProgressEvent {
  type: 'progress'
  stage: AISearchProgressStage
  status?: AISearchProgressStatus | null
  round?: number | null
  tool?: string
  query?: string
  targets?: string[]
  titles?: string[]
  count?: number | null
  error?: string
}

type AISearchStreamEvent =
  | AISearchProgressEvent
  | { type: 'result'; response: AISearchResponse }
  | { type: 'error'; message: string; status?: number }

export async function searchWithAIStream(
  payload: AISearchRequest,
  onProgress: (event: AISearchProgressEvent) => void,
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
    if (event.type === 'progress') {
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
