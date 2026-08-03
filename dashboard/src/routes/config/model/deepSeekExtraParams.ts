export type DeepSeekClientType = 'openai' | 'openai_responses'
export type DeepSeekReasoningEffort = 'low' | 'high' | 'max'

const DEEPSEEK_REASONING_EFFORTS = new Set<DeepSeekReasoningEffort>(['low', 'high', 'max'])
const DEEPSEEK_RESPONSES_EFFORTS = new Set([...DEEPSEEK_REASONING_EFFORTS, 'none'])
const DEEPSEEK_WEB_SEARCH_TYPES = new Set(['web_search', 'web_search_2025_08_26'])

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function withoutKeys(
  params: Record<string, unknown>,
  keys: string[]
): Record<string, unknown> {
  const nextParams = { ...params }
  for (const key of keys) {
    delete nextParams[key]
  }
  return nextParams
}

export function isDeepSeekThinkingEnabled(
  params: Record<string, unknown>,
  clientType: DeepSeekClientType
): boolean {
  if (clientType === 'openai_responses') {
    const reasoning = isRecord(params.reasoning) ? params.reasoning : {}
    return reasoning.effort !== 'none'
  }

  const thinking = isRecord(params.thinking) ? params.thinking : {}
  return thinking.type !== 'disabled'
}

export function getDeepSeekReasoningEffort(
  params: Record<string, unknown>,
  clientType: DeepSeekClientType
): DeepSeekReasoningEffort {
  const value =
    clientType === 'openai_responses'
      ? isRecord(params.reasoning)
        ? params.reasoning.effort
        : undefined
      : params.reasoning_effort

  return DEEPSEEK_REASONING_EFFORTS.has(value as DeepSeekReasoningEffort)
    ? (value as DeepSeekReasoningEffort)
    : 'high'
}

export function setDeepSeekThinkingEnabled(
  params: Record<string, unknown>,
  clientType: DeepSeekClientType,
  enabled: boolean
): Record<string, unknown> {
  if (clientType === 'openai_responses') {
    const nextParams = withoutKeys(params, ['thinking', 'reasoning_effort'])
    const reasoning = isRecord(nextParams.reasoning) ? nextParams.reasoning : {}
    return {
      ...nextParams,
      reasoning: {
        ...reasoning,
        effort: enabled ? getDeepSeekReasoningEffort(params, clientType) : 'none',
      },
    }
  }

  const nextParams = withoutKeys(params, ['reasoning'])
  const thinking = isRecord(nextParams.thinking) ? nextParams.thinking : {}
  const result: Record<string, unknown> = {
    ...nextParams,
    thinking: { ...thinking, type: enabled ? 'enabled' : 'disabled' },
  }
  if (enabled) {
    result.reasoning_effort = getDeepSeekReasoningEffort(params, clientType)
  } else {
    delete result.reasoning_effort
  }
  return result
}

export function setDeepSeekReasoningEffort(
  params: Record<string, unknown>,
  clientType: DeepSeekClientType,
  effort: DeepSeekReasoningEffort
): Record<string, unknown> {
  if (clientType === 'openai_responses') {
    const nextParams = withoutKeys(params, ['thinking', 'reasoning_effort'])
    const reasoning = isRecord(nextParams.reasoning) ? nextParams.reasoning : {}
    return { ...nextParams, reasoning: { ...reasoning, effort } }
  }

  const nextParams = withoutKeys(params, ['reasoning'])
  const thinking = isRecord(nextParams.thinking) ? nextParams.thinking : {}
  return {
    ...nextParams,
    thinking: { ...thinking, type: 'enabled' },
    reasoning_effort: effort,
  }
}

export function isDeepSeekWebSearchEnabled(params: Record<string, unknown>): boolean {
  if (!Array.isArray(params.tools)) return false
  return params.tools.some(
    (tool) => isRecord(tool) && DEEPSEEK_WEB_SEARCH_TYPES.has(String(tool.type))
  )
}

export function setDeepSeekWebSearchEnabled(
  params: Record<string, unknown>,
  enabled: boolean
): Record<string, unknown> {
  if (params.tools !== undefined && !Array.isArray(params.tools)) {
    throw new Error('tools 必须是工具定义数组')
  }

  const otherTools = (params.tools ?? []).filter(
    (tool) => !isRecord(tool) || !DEEPSEEK_WEB_SEARCH_TYPES.has(String(tool.type))
  )
  const tools = enabled ? [...otherTools, { type: 'web_search' }] : otherTools
  const nextParams = { ...params }
  if (tools.length > 0) {
    nextParams.tools = tools
  } else {
    delete nextParams.tools
  }
  return nextParams
}

export function validateDeepSeekExtraParams(
  params: Record<string, unknown>,
  clientType: DeepSeekClientType
): string | null {
  if (clientType === 'openai_responses') {
    if ('thinking' in params || 'reasoning_effort' in params) {
      return 'Responses 客户端请使用 reasoning，不能重复配置 thinking 或 reasoning_effort'
    }
    if (params.reasoning !== undefined) {
      if (!isRecord(params.reasoning)) return 'reasoning 必须是对象'
      const effort = params.reasoning.effort
      if (effort !== undefined && !DEEPSEEK_RESPONSES_EFFORTS.has(String(effort))) {
        return 'reasoning.effort 只能是 none、low、high 或 max'
      }
    }
  } else {
    if ('reasoning' in params) {
      return 'Chat Completions 客户端请使用 thinking，不能重复配置 reasoning'
    }
    if (params.thinking !== undefined) {
      if (!isRecord(params.thinking)) return 'thinking 必须是对象'
      if (!['enabled', 'disabled'].includes(String(params.thinking.type))) {
        return 'thinking.type 只能是 enabled 或 disabled'
      }
    }
    if (
      params.reasoning_effort !== undefined &&
      !DEEPSEEK_REASONING_EFFORTS.has(params.reasoning_effort as DeepSeekReasoningEffort)
    ) {
      return 'reasoning_effort 只能是 low、high 或 max'
    }
  }

  if (params.tools !== undefined && !Array.isArray(params.tools)) {
    return 'tools 必须是工具定义数组'
  }
  if (Array.isArray(params.tools)) {
    if (!params.tools.every(isRecord)) return 'tools 中的每一项都必须是工具定义对象'
    const webSearchCount = params.tools.filter((tool) =>
      DEEPSEEK_WEB_SEARCH_TYPES.has(String(tool.type))
    ).length
    if (webSearchCount > 1) return '联网搜索工具只能配置一次'
    if (clientType === 'openai' && webSearchCount > 0) {
      return 'DeepSeek Chat Completions 不支持原生联网搜索，请使用 Responses 客户端'
    }
  }

  return null
}

