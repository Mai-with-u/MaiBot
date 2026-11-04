import type { ThinkingProbeMode } from '@/lib/config-api'

export const THINKING_PROBE_LABELS: Record<ThinkingProbeMode, string> = {
  thinking_type: 'thinking.type',
  enable_thinking: 'enable_thinking',
  reasoning_effort_nested: 'reasoning.effort',
  reasoning_effort: 'reasoning_effort（high / off）',
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

// 与 OpenAI 客户端一致：顶层参数覆盖 body 中的同名参数。
function effectiveParams(params: Record<string, unknown>): Record<string, unknown> {
  return { ...(isRecord(params.body) ? params.body : {}), ...params }
}

/** 只恢复已配置的格式，不把手填参数视作已经测试通过。 */
export function readThinkingProbeMode(params: Record<string, unknown>): ThinkingProbeMode | null {
  const effective = effectiveParams(params)
  const modes: ThinkingProbeMode[] = []
  if (isRecord(effective.thinking) && ['enabled', 'disabled'].includes(String(effective.thinking.type))) {
    modes.push('thinking_type')
  }
  if (typeof effective.enable_thinking === 'boolean') modes.push('enable_thinking')
  if (isRecord(effective.reasoning) && typeof effective.reasoning.effort === 'string') {
    modes.push('reasoning_effort_nested')
  }
  if (typeof effective.reasoning_effort === 'string' && ['off', 'low', 'medium', 'high', 'xhigh', 'max'].includes(effective.reasoning_effort)) {
    modes.push('reasoning_effort')
  }
  return modes.length === 1 ? modes[0] : null
}

export function isProbedThinkingEnabled(params: Record<string, unknown>, mode: ThinkingProbeMode): boolean {
  const effective = effectiveParams(params)
  if (mode === 'enable_thinking') return effective.enable_thinking === true
  if (mode === 'thinking_type') return isRecord(effective.thinking) && effective.thinking.type !== 'disabled'
  if (mode === 'reasoning_effort') return effective.reasoning_effort !== 'off'
  return isRecord(effective.reasoning) && effective.reasoning.effort !== 'none'
}

function clearThinkingParams(params: Record<string, unknown>): Record<string, unknown> {
  const next = { ...params }
  for (const key of ['thinking', 'enable_thinking', 'reasoning_effort', 'thinking_budget', 'thinking_config']) {
    delete next[key]
  }
  if (isRecord(next.reasoning)) {
    const reasoning = { ...next.reasoning }
    delete reasoning.effort
    if (Object.keys(reasoning).length) next.reasoning = reasoning
    else delete next.reasoning
  } else {
    delete next.reasoning
  }
  return next
}

/** 清除显式思考配置，让服务商决定默认行为，同时保留其他请求参数。 */
export function resetThinkingProbeMode(params: Record<string, unknown>): Record<string, unknown> {
  const next = clearThinkingParams(params)
  if (isRecord(params.body)) next.body = clearThinkingParams(params.body)
  return next
}

/** 切换时同时清理 body 的冲突字段，保留 headers、query 与其他额外参数。 */
export function applyThinkingProbeMode(
  params: Record<string, unknown>,
  mode: ThinkingProbeMode,
  enabled: boolean,
): Record<string, unknown> {
  const effective = effectiveParams(params)
  const next = resetThinkingProbeMode(params)
  if (mode === 'thinking_type') next.thinking = { type: enabled ? 'enabled' : 'disabled' }
  else if (mode === 'enable_thinking') next.enable_thinking = enabled
  else if (mode === 'reasoning_effort') next.reasoning_effort = enabled ? 'high' : 'off'
  else {
    const reasoning = isRecord(effective.reasoning) ? effective.reasoning : {}
    next.reasoning = {
      ...reasoning,
      effort: enabled ? 'high' : 'none',
    }
  }
  return next
}
