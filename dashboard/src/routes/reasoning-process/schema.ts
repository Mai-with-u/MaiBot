import type {
  ContextItemSnapshot,
  GenerationAttemptSnapshot,
  GenerationTraceSnapshot,
} from '@/lib/reasoning-process-api'

export type LegacyStructuredPromptMessage = {
  index?: number
  role?: string
  content?: unknown
  item_id?: string
  item_type?: string
  logical_turn_id?: string | null
  meta?: Record<string, unknown>
  ordinal?: number | null
  provider_type?: string
  reasoning_content?: unknown
  reasoning_representation?: string
  response_group_id?: string | null
  status?: string
  timestamp?: string
  tool_call_id?: string
  tool_name?: string
  tool_calls?: unknown[]
}

export type LegacyStructuredPromptOutput = {
  title?: string
  content?: unknown
  tool_calls?: unknown[]
}

export type ProviderResponsePayload = Record<string, unknown> & {
  output?: unknown[]
}

export type StructuredPromptLlmCall = {
  inference_stage: string
  request?: {
    kind?: string
    operation?: string
    request_type?: string
    selection_reason?: string
    task_name?: string
  }
  metadata?: {
    client_type?: string
    created_at?: string
    model_name?: string
    duration_ms?: number
    provider_name?: string
    request_id?: string
    session_id?: string | null
    status?: string
    updated_at?: string
  }
  request_items: ContextItemSnapshot[]
  output_items: ContextItemSnapshot[]
  generation_attempts: GenerationAttemptSnapshot[]
}

export type StructuredPromptPayload = {
  schema_version?: number
  request?: {
    kind?: string
    operation?: string
    request_type?: string
    selection_reason?: string
    task_name?: string
  }
  metadata?: {
    client_type?: string
    created_at?: string
    model_name?: string
    duration_ms?: number
    provider_name?: string
    request_id?: string
    session_id?: string | null
    status?: string
    updated_at?: string
  }
  presentation?: {
    output_title?: string
  }
  request_items: ContextItemSnapshot[]
  output_items: ContextItemSnapshot[]
  tool_definitions?: unknown[]
  jargon_learning_calls?: StructuredPromptLlmCall[]
  generation_attempts: GenerationAttemptSnapshot[]
  request_parameters?: Record<string, unknown>
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value && typeof value === 'object' && !Array.isArray(value))
}

export function stringifyStructuredValue(value: unknown): string {
  if (typeof value === 'string') return value
  if (value === null || value === undefined) return ''
  return JSON.stringify(value, null, 2)
}

export function stringifyPromptContent(value: unknown): string {
  if (typeof value === 'string') return value
  if (value === null || value === undefined) return ''
  if (!Array.isArray(value)) return stringifyStructuredValue(value)

  return value

    .map((item) => {
      if (typeof item === 'string') return item
      if (isRecord(item) && item.type === 'text' && typeof item.text === 'string') return item.text
      if (isRecord(item)) {
        const partType = String(item.type || '')
          .trim()
          .toLowerCase()
        if (['image', 'image_url', 'input_image'].includes(partType)) {
          const imageFormat =
            String(item.image_format || item.format || 'unknown').trim() || 'unknown'
          const sizeText = typeof item.size_bytes === 'number' ? ` ${item.size_bytes} B` : ''
          return `[图片 image/${imageFormat}${sizeText}]`
        }
      }
      return stringifyStructuredValue(item)
    })
    .filter(Boolean)
    .join('\n')
    .trim()
}

export function createLegacyItemMeta(
  source: LegacyStructuredPromptMessage | Record<string, unknown>,
  fallbackId: string,
  overrides: Partial<ContextItemSnapshot['meta']> = {}
): ContextItemSnapshot['meta'] {
  const sourceMeta = isRecord(source.meta) ? source.meta : source
  return {
    item_id: String(sourceMeta.item_id || fallbackId),
    logical_turn_id:
      typeof sourceMeta.logical_turn_id === 'string' ? sourceMeta.logical_turn_id : null,
    timestamp:
      typeof sourceMeta.timestamp === 'string' && sourceMeta.timestamp.trim()
        ? sourceMeta.timestamp
        : '1970-01-01T00:00:00.000Z',
    ...overrides,
  }
}

export function normalizeLegacyContentParts(content: unknown): Record<string, unknown>[] {
  const rawParts = Array.isArray(content) ? content : [content]
  return rawParts.flatMap((part) => {
    if (typeof part === 'string') return [{ type: 'text', text: part }]
    if (!isRecord(part))
      return part === null || part === undefined ? [] : [{ type: 'text', text: String(part) }]
    const partType = String(part.type || '')
      .trim()
      .toLowerCase()
    if (partType === 'refusal') {
      return [{ ...part, type: 'refusal', refusal: String(part.refusal || '') }]
    }
    if (['image', 'image_url', 'input_image'].includes(partType)) {
      return [{ ...part, type: 'image' }]
    }
    if (partType === 'text') return [{ ...part, type: 'text', text: String(part.text || '') }]
    return [{ type: 'text', text: stringifyStructuredValue(part) }]
  })
}

export function normalizeLegacyToolCall(
  rawToolCall: unknown,
  fallbackId: string
): Record<string, unknown> {
  const toolCall = isRecord(rawToolCall) ? rawToolCall : {}
  const fn = isRecord(toolCall.function) ? toolCall.function : toolCall
  const extraContent = isRecord(toolCall.extra_content) ? toolCall.extra_content : {}
  return {
    call_id: String(toolCall.id || toolCall.call_id || fallbackId),
    func_name: String(fn.name || toolCall.func_name || 'unknown'),
    args: fn.arguments ?? toolCall.arguments ?? toolCall.args ?? {},
    extra_content: {
      ...extraContent,
      ...(toolCall.source ? { tool_call_source: toolCall.source } : {}),
      ...(toolCall.source_label ? { tool_call_source_label: toolCall.source_label } : {}),
    },
  }
}

export function migrateLegacyMessageToItems(
  message: LegacyStructuredPromptMessage,
  index: number,
  prefix: string
): ContextItemSnapshot[] {
  const role = String(message.role || 'user')
    .trim()
    .toLowerCase()
  const itemType = String(message.item_type || '').trim()
  const baseId = String(message.item_id || `${prefix}-${index + 1}`)
  const baseMeta = createLegacyItemMeta(message, baseId)
  const contentText = stringifyPromptContent(message.content)

  if (itemType === 'ReasoningItem' || role === 'reasoning') {
    return [
      {
        item_type: 'ReasoningItem',
        meta: baseMeta,
        representation: message.reasoning_representation || 'raw_text',
        summary_parts: [],
        text_parts: contentText ? [contentText] : [],
      },
    ]
  }
  if (itemType === 'FunctionCallItem' || role === 'function_call') {
    return [
      {
        item_type: 'FunctionCallItem',
        meta: baseMeta,
        tool_call: normalizeLegacyToolCall(message.tool_calls?.[0], `${baseId}-call`),
      },
    ]
  }
  if (itemType === 'FunctionCallOutputItem' || role === 'tool') {
    return [
      {
        item_type: 'FunctionCallOutputItem',
        meta: baseMeta,
        call_id: String(message.tool_call_id || `${baseId}-call`),
        output: contentText,
        success: true,
        tool_name: String(message.tool_name || ''),
      },
    ]
  }
  if (itemType === 'ProviderActivityItem' || role === 'provider_activity') {
    return [
      {
        item_type: 'ProviderActivityItem',
        meta: baseMeta,
        action_type: '',
        call_id: String(message.tool_call_id || ''),
        details: [],
        display_summary: contentText,
        provider_type: String(message.provider_type || 'unknown'),
        source_count: 0,
        status: String(message.status || ''),
      },
    ]
  }
  if (itemType === 'ProviderOpaqueItem' || role === 'provider_opaque') {
    return [
      {
        item_type: 'ProviderOpaqueItem',
        meta: baseMeta,
        display_summary: contentText,
        provider_type: String(message.provider_type || 'unknown'),
      },
    ]
  }

  const normalizedMessageType =
    role === 'system'
      ? 'SystemMessageItem'
      : role === 'assistant'
        ? 'AssistantMessageItem'
        : 'UserMessageItem'
  const directMessageItem: ContextItemSnapshot = {
    item_type: normalizedMessageType,
    meta: baseMeta,
    parts: normalizeLegacyContentParts(message.content),
  }
  if (itemType || role !== 'assistant') return [directMessageItem]

  const reasoningText = stringifyPromptContent(message.reasoning_content)
  const toolCalls = message.tool_calls ?? []
  if (!reasoningText && toolCalls.length === 0) return [directMessageItem]

  const logicalTurnId = message.logical_turn_id || `${baseId}-turn`
  const groupItems: ContextItemSnapshot[] = []
  if (reasoningText) {
    groupItems.push({
      item_type: 'ReasoningItem',
      meta: createLegacyItemMeta(message, `${baseId}-reasoning`, {
        item_id: `${baseId}-reasoning`,
        logical_turn_id: logicalTurnId,
      }),
      representation: 'raw_text',
      summary_parts: [],
      text_parts: [reasoningText],
    })
  }
  if (contentText) {
    groupItems.push({
      ...directMessageItem,
      meta: createLegacyItemMeta(message, baseId, {
        logical_turn_id: logicalTurnId,
      }),
    })
  }
  toolCalls.forEach((toolCall, toolIndex) => {
    groupItems.push({
      item_type: 'FunctionCallItem',
      meta: createLegacyItemMeta(message, `${baseId}-call-${toolIndex + 1}`, {
        item_id: `${baseId}-call-${toolIndex + 1}`,
        logical_turn_id: logicalTurnId,
      }),
      tool_call: normalizeLegacyToolCall(toolCall, `${baseId}-call-${toolIndex + 1}`),
    })
  })
  return groupItems
}

export function normalizeContextItemSnapshot(
  value: unknown,
  index: number,
  prefix: string
): ContextItemSnapshot | null {
  if (!isRecord(value)) return null
  const itemType = String(value.item_type || '').trim()
  if (!itemType) return null
  return {
    ...value,
    item_type: itemType,
    meta: createLegacyItemMeta(value, `${prefix}-${index + 1}`),
  } as ContextItemSnapshot
}

export function normalizeGenerationTrace(value: unknown): GenerationTraceSnapshot | undefined {
  if (!isRecord(value)) return undefined
  const outputItemIds = Array.isArray(value.output_item_ids)
    ? value.output_item_ids.filter((item): item is string => typeof item === 'string')
    : []
  return {
    provider: String(value.provider || ''),
    endpoint: String(value.endpoint || ''),
    model: String(value.model || ''),
    response_id: typeof value.response_id === 'string' ? value.response_id : null,
    status: String(value.status || ''),
    prompt_tokens: typeof value.prompt_tokens === 'number' ? value.prompt_tokens : 0,
    completion_tokens: typeof value.completion_tokens === 'number' ? value.completion_tokens : 0,
    total_tokens: typeof value.total_tokens === 'number' ? value.total_tokens : 0,
    prompt_cache_hit_tokens:
      typeof value.prompt_cache_hit_tokens === 'number' ? value.prompt_cache_hit_tokens : 0,
    prompt_cache_miss_tokens:
      typeof value.prompt_cache_miss_tokens === 'number' ? value.prompt_cache_miss_tokens : 0,
    output_item_ids: outputItemIds,
  }
}

export function normalizeGenerationAttempt(
  value: unknown,
  index: number,
  fallback: {
    requestItems: ContextItemSnapshot[]
    outputItems: ContextItemSnapshot[]
    toolDefinitions: unknown[]
    requestParameters: Record<string, unknown>
    providerRequest?: Record<string, unknown>
    generationTrace?: GenerationTraceSnapshot
    providerResponse?: ProviderResponsePayload
    requestKind?: string
  }
): GenerationAttemptSnapshot | null {
  if (!isRecord(value)) return null
  const legacyError = isRecord(value.error) ? value.error : undefined
  const trace =
    normalizeGenerationTrace(value.trace ?? value.generation_trace) ?? fallback.generationTrace
  const requestItems = Array.isArray(value.request_items)
    ? value.request_items
        .map((item, itemIndex) =>
          normalizeContextItemSnapshot(item, itemIndex, `attempt-${index + 1}-request`)
        )
        .filter((item): item is ContextItemSnapshot => item !== null)
    : fallback.requestItems
  const outputItems = Array.isArray(value.output_items)
    ? value.output_items
        .map((item, itemIndex) =>
          normalizeContextItemSnapshot(item, itemIndex, `attempt-${index + 1}-output`)
        )
        .filter((item): item is ContextItemSnapshot => item !== null)
    : fallback.outputItems
  const rawToolDefinitions = Array.isArray(value.tool_definitions)
    ? value.tool_definitions
    : fallback.toolDefinitions
  const toolDefinitions = rawToolDefinitions.filter(isRecord)
  const requestParameters = isRecord(value.request_parameters)
    ? value.request_parameters
    : fallback.requestParameters
  const providerAttempt =
    typeof value.provider_attempt === 'number'
      ? value.provider_attempt
      : typeof value.attempt === 'number'
        ? value.attempt
        : index + 1
  const modelAttempt = typeof value.model_attempt === 'number' ? value.model_attempt : 1
  const status = String(value.status || trace?.status || (legacyError ? 'failed' : 'succeeded'))
  return {
    ...value,
    attempt_id: String(value.attempt_id || `legacy-attempt-${providerAttempt}-${index + 1}`),
    workflow_purpose: String(value.workflow_purpose || fallback.requestKind || 'legacy'),
    workflow_attempt: typeof value.workflow_attempt === 'number' ? value.workflow_attempt : 1,
    provider_attempt: providerAttempt,
    model_attempt: modelAttempt,
    status,
    started_at: String(value.started_at || value.timestamp || ''),
    duration_ms: typeof value.duration_ms === 'number' ? value.duration_ms : 0,
    provider: String(value.provider || value.provider_name || trace?.provider || ''),
    endpoint: String(value.endpoint || trace?.endpoint || ''),
    model: String(value.model || value.model_name || trace?.model || ''),
    client_type: String(value.client_type || ''),
    operation: String(value.operation || ''),
    wire_protocol: String(value.wire_protocol || value.client_type || ''),
    request_items: requestItems,
    tool_definitions: toolDefinitions,
    request_parameters: requestParameters,
    wire_request: value.wire_request ?? value.provider_request ?? fallback.providerRequest ?? null,
    wire_response:
      value.wire_response ?? fallback.providerResponse ?? legacyError?.response_body ?? null,
    output_items: outputItems,
    trace: trace ?? null,
    error: legacyError
      ? {
          type: typeof legacyError.type === 'string' ? legacyError.type : undefined,
          status_code: typeof legacyError.status_code === 'number' ? legacyError.status_code : null,
          message: typeof legacyError.message === 'string' ? legacyError.message : undefined,
          response_body: legacyError.response_body,
        }
      : null,
  }
}

export function migrateLegacyOutputToItems(
  output: LegacyStructuredPromptOutput | null | undefined,
  prefix: string
): ContextItemSnapshot[] {
  if (!output) return []
  return migrateLegacyMessageToItems(
    {
      role: 'assistant',
      content: output.content,
      tool_calls: output.tool_calls,
    },
    0,
    prefix
  )
}

export function bindLegacyToolTurns(items: ContextItemSnapshot[]): ContextItemSnapshot[] {
  const turnByCallId = new Map<string, string>()
  for (const item of items) {
    if (item.item_type !== 'FunctionCallItem' || !isRecord(item.tool_call)) continue
    const callId = String(item.tool_call.call_id || '').trim()
    if (!callId) continue
    const turnId = item.meta.logical_turn_id || `legacy-tool-${callId}-turn`
    turnByCallId.set(callId, turnId)
  }

  return items.map((item) => {
    const callId =
      item.item_type === 'FunctionCallItem' && isRecord(item.tool_call)
        ? String(item.tool_call.call_id || '').trim()
        : item.item_type === 'FunctionCallOutputItem'
          ? String(item.call_id || '').trim()
          : ''
    const logicalTurnId = turnByCallId.get(callId)
    if (!logicalTurnId) return item
    return {
      ...item,
      meta: {
        ...item.meta,
        logical_turn_id: logicalTurnId,
      },
    }
  })
}

export function normalizeStructuredPromptPayload(value: unknown): StructuredPromptPayload | null {
  if (!isRecord(value)) return null
  const rawMessages = Array.isArray(value.messages)
    ? (value.messages.filter(isRecord) as LegacyStructuredPromptMessage[])
    : []
  const requestItems = Array.isArray(value.request_items)
    ? value.request_items
        .map((item, index) => normalizeContextItemSnapshot(item, index, 'request'))
        .filter((item): item is ContextItemSnapshot => item !== null)
    : bindLegacyToolTurns(
        rawMessages.flatMap((message, index) =>
          migrateLegacyMessageToItems(message, index, 'request')
        )
      )
  const legacyOutput = isRecord(value.output)
    ? (value.output as LegacyStructuredPromptOutput)
    : null
  const outputItems = Array.isArray(value.output_items)
    ? value.output_items
        .map((item, index) => normalizeContextItemSnapshot(item, index, 'output'))
        .filter((item): item is ContextItemSnapshot => item !== null)
    : migrateLegacyOutputToItems(legacyOutput, 'output')
  const toolDefinitions = Array.isArray(value.tool_definitions) ? value.tool_definitions : []
  const requestParameters = isRecord(value.request_parameters) ? value.request_parameters : {}
  const generationTrace = normalizeGenerationTrace(value.generation_trace)
  const providerResponse = isRecord(value.provider_response)
    ? (value.provider_response as ProviderResponsePayload)
    : undefined
  const rawGenerationAttempts = Array.isArray(value.generation_attempts)
    ? value.generation_attempts
    : Array.isArray(value.attempts)
      ? value.attempts
      : generationTrace || providerResponse
        ? [{}]
        : []
  const generationAttempts = rawGenerationAttempts
    .map((attempt, attemptIndex) =>
      normalizeGenerationAttempt(attempt, attemptIndex, {
        requestItems,
        outputItems,
        toolDefinitions,
        requestParameters,
        providerRequest: isRecord(value.provider_request) ? value.provider_request : undefined,
        generationTrace,
        providerResponse,
        requestKind: isRecord(value.request)
          ? String(value.request.kind || value.request_type || '')
          : '',
      })
    )
    .filter((attempt): attempt is GenerationAttemptSnapshot => attempt !== null)
  const rawCalls = Array.isArray(value.jargon_learning_calls) ? value.jargon_learning_calls : []
  const jargonLearningCalls = rawCalls.flatMap((rawCall, callIndex) => {
    const normalizedCall = normalizeStructuredPromptPayload(rawCall)
    if (!normalizedCall || !isRecord(rawCall)) return []
    return [
      {
        inference_stage: String(rawCall.inference_stage || `stage_${callIndex + 1}`),
        request: normalizedCall.request,
        metadata: normalizedCall.metadata,
        request_items: normalizedCall.request_items,
        output_items: normalizedCall.output_items,
        generation_attempts: normalizedCall.generation_attempts,
      },
    ]
  })
  const itemFirstPayload = Object.fromEntries(
    Object.entries(value).filter(
      ([key]) =>
        ![
          'jargon_learning_calls',
          'messages',
          'output',
          'output_items',
          'request_items',
          'generation_trace',
          'generation_attempts',
          'attempts',
          'provider_request',
          'provider_response',
          'schema_version',
        ].includes(key)
    )
  )

  return {
    ...itemFirstPayload,
    schema_version: 6,
    presentation: isRecord(value.presentation)
      ? value.presentation
      : { output_title: legacyOutput?.title || '输出结果' },
    request_items: requestItems,
    output_items: outputItems,
    generation_attempts: generationAttempts,
    jargon_learning_calls: jargonLearningCalls,
  }
}

export function parseStructuredPrompt(content: string): StructuredPromptPayload | null {
  if (!content.trim()) return null
  try {
    const payload = JSON.parse(content) as unknown
    return normalizeStructuredPromptPayload(payload)
  } catch {
    return null
  }
}
