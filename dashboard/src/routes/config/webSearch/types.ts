export type WebAdapter = 'deepseek' | 'tavily' | 'stepsearch_mcp' | 'anysearch' | 'exa' | 'firecrawl'

export interface WebServiceConfig {
  adapter: WebAdapter
  api_key?: string
  base_url?: string
  server_ref?: string
  timeout?: number
  search_depth?: 'basic' | 'advanced' | 'fast' | 'ultra-fast'
  extract_depth?: 'basic' | 'advanced'
  category?: '' | 'programming' | 'research' | 'gov' | 'business'
}

export interface WebSearchConfig {
  preview_chars: number
  services: Record<string, WebServiceConfig>
}

export function readWebSearchConfig(value: unknown): WebSearchConfig {
  const config = (value ?? {}) as Partial<WebSearchConfig>
  return {
    preview_chars: config.preview_chars ?? 4000,
    services: config.services ?? {},
  }
}
