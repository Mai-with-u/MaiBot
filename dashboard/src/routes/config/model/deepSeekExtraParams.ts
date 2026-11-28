export type DeepSeekClientType = 'openai' | 'openai_responses' | 'anthropic'
export type DeepSeekReasoningEffort = 'low' | 'high' | 'max'

const DEEPSEEK_REASONING_EFFORTS = new Set<DeepSeekReasoningEffort>(['low', 'high', 'max'])
const DEEPSEEK_RESPONSES_EFFORTS = new Set([...DEEPSEEK_REASONING_EFFORTS, 'none'])
const DEEPSEEK_MESSAGES_EFFORTS = new Map<string, DeepSeekReasoningEffort>([
  ['minimal', 'low'],
  ['low', 'low'],
  ['medium', 'high'],
  ['high', 'high'],
  ['xhigh', 'high'],
  ['max', 'max'],
  ['ultra', 'max'],
])
type DeepSeekSearchTool = 'deepseek_web_search' | 'web_search'
const DEEPSEEK_WEB_SEARCH_TYPES = new Set(['deepseek_web_search', 'web_search', 'web_search_2025_08_26'])
const FLASH_MODELS = new Set(['deepseek-flash', 'deepseek-v4-flash', 'deepseek-v4-flash-vision-exp'])

export function getDeepSeekSearchTool(model: string, client: DeepSeekClientType): DeepSeekSearchTool | null {
  if (client === 'anthropic') return null
  if (FLASH_MODELS.has(model.trim())) return 'deepseek_web_search'
  if (model.trim() === 'deepseek-v4-pro' && client === 'openai_responses') return 'web_search'
  return null
}

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
  if (clientType === 'anthropic') {
    const effort = isRecord(params.output_config) ? params.output_config.effort : undefined
    return typeof effort === 'string' ? DEEPSEEK_MESSAGES_EFFORTS.get(effort) ?? 'high' : 'high'
  }

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

function setMessagesThinking(
  params: Record<string, unknown>,
  effort: DeepSeekReasoningEffort | undefined
): Record<string, unknown> {
  const nextParams = withoutKeys(params, ['reasoning', 'reasoning_effort'])
  const thinking = isRecord(nextParams.thinking) ? nextParams.thinking : {}
  const outputConfig = isRecord(nextParams.output_config) ? { ...nextParams.output_config } : {}
  nextParams.thinking = { ...thinking, type: effort ? 'enabled' : 'disabled' }
  if (effort) {
    outputConfig.effort = effort
  } else {
    delete outputConfig.effort
  }
  if (Object.keys(outputConfig).length) {
    nextParams.output_config = outputConfig
  } else {
    delete nextParams.output_config
  }
  return nextParams
}

export function setDeepSeekThinkingEnabled(
  params: Record<string, unknown>,
  clientType: DeepSeekClientType,
  enabled: boolean
): Record<string, unknown> {
  if (clientType === 'anthropic') {
    return setMessagesThinking(params, enabled ? getDeepSeekReasoningEffort(params, clientType) : undefined)
  }
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
  if (clientType === 'anthropic') return setMessagesThinking(params, effort)
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
  toolType: DeepSeekSearchTool | null
): Record<string, unknown> {
  if (params.tools !== undefined && !Array.isArray(params.tools)) {
    throw new Error('tools 必须是工具定义数组')
  }

  const otherTools = (params.tools ?? []).filter(
    (tool) => !isRecord(tool) || !DEEPSEEK_WEB_SEARCH_TYPES.has(String(tool.type))
  )
  const tools = toolType ? [...otherTools, { type: toolType }] : otherTools
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
  clientType: DeepSeekClientType,
  searchTool: DeepSeekSearchTool | null
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
  } else if (clientType === 'anthropic') {
    if ('reasoning' in params || 'reasoning_effort' in params) {
      return 'Anthropic Messages 客户端请使用 output_config.effort'
    }
    if (params.thinking !== undefined) {
      if (!isRecord(params.thinking)) return 'thinking 必须是对象'
      if (!['enabled', 'disabled', 'adaptive'].includes(String(params.thinking.type))) {
        return 'thinking.type 只能是 enabled、disabled 或 adaptive'
      }
    }
    if (params.output_config !== undefined) {
      if (!isRecord(params.output_config)) return 'output_config 必须是对象'
      const effort = params.output_config.effort
      if (effort !== undefined && (typeof effort !== 'string' || !DEEPSEEK_MESSAGES_EFFORTS.has(effort))) {
        return 'output_config.effort 无效，可使用 low、high 或 max'
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
    const searchTools = params.tools.filter((tool) =>
      DEEPSEEK_WEB_SEARCH_TYPES.has(String(tool.type))
    )
    if (searchTools.length > 1) return '联网搜索工具只能配置一次'
    if (searchTools.length && searchTools[0].type !== searchTool) {
      return searchTool
        ? `当前模型使用 ${searchTool}，请重新开启联网搜索以更新工具类型`
        : '当前模型和接口组合不支持此联网工具，请关闭联网搜索或更换模型'
    }
  }

  return null
}

