// @vitest-environment node

import { describe, expect, it } from 'vitest'

import {
  bindLegacyToolTurns,
  createLegacyItemMeta,
  isRecord,
  migrateLegacyMessageToItems,
  migrateLegacyOutputToItems,
  normalizeContextItemSnapshot,
  normalizeGenerationAttempt,
  normalizeGenerationTrace,
  normalizeLegacyContentParts,
  normalizeLegacyToolCall,
  normalizeStructuredPromptPayload,
  parseStructuredPrompt,
  stringifyPromptContent,
  stringifyStructuredValue,
} from './schema'

const itemMeta = {
  item_id: 'item-1',
  logical_turn_id: null,
  timestamp: '2026-08-05T00:00:00.000Z',
}

describe('reasoning process schema v6 migration', () => {
  it.each([1, 2, 3])('migrates v%s chat logs to item-first v6', (schemaVersion) => {
    const migrated = normalizeStructuredPromptPayload({
      schema_version: schemaVersion,
      messages: [{ role: 'user', content: `旧日志 v${schemaVersion}` }],
      output: { title: '输出', content: '旧版正文' },
    })

    expect(migrated?.schema_version).toBe(6)
    expect(migrated?.request_items[0].item_type).toBe('UserMessageItem')
    expect(migrated?.output_items[0].item_type).toBe('AssistantMessageItem')
    expect(migrated?.generation_attempts).toEqual([])
  })

  it('migrates v4 item logs and binds legacy tool turns', () => {
    const migrated = normalizeStructuredPromptPayload({
      schema_version: 4,
      messages: [
        {
          role: 'assistant',
          content: '',
          tool_calls: [{ id: 'call-1', function: { name: 'lookup', arguments: {} } }],
        },
        { role: 'tool', tool_call_id: 'call-1', content: '工具结果' },
      ],
      output: { content: '完成' },
    })

    const call = migrated?.request_items.find((item) => item.item_type === 'FunctionCallItem')
    const output = migrated?.request_items.find(
      (item) => item.item_type === 'FunctionCallOutputItem'
    )
    expect(migrated?.schema_version).toBe(6)
    expect(call?.meta.logical_turn_id).toBeTruthy()
    expect(output?.meta.logical_turn_id).toBe(call?.meta.logical_turn_id)
  })

  it('migrates v5 trace and provider response into one attempt', () => {
    const migrated = normalizeStructuredPromptPayload({
      schema_version: 5,
      request: { kind: 'replyer' },
      request_items: [
        { item_type: 'UserMessageItem', meta: itemMeta, parts: [{ type: 'text', text: '问题' }] },
      ],
      output_items: [
        {
          item_type: 'AssistantMessageItem',
          meta: itemMeta,
          parts: [{ type: 'text', text: '回答' }],
        },
      ],
      generation_trace: {
        provider: 'test-provider',
        endpoint: 'responses',
        model: 'test-model',
        response_id: 'resp-1',
        status: 'completed',
        output_item_ids: ['item-1'],
      },
      provider_response: { id: 'resp-1', output: [] },
    })

    expect(migrated?.schema_version).toBe(6)
    expect(migrated?.generation_attempts).toHaveLength(1)
    expect(migrated?.generation_attempts[0].workflow_purpose).toBe('replyer')
    expect(migrated?.generation_attempts[0].trace?.response_id).toBe('resp-1')
    expect(migrated?.generation_attempts[0].wire_response).toMatchObject({ id: 'resp-1' })
    expect(migrated).not.toHaveProperty('generation_trace')
    expect(migrated).not.toHaveProperty('provider_response')
  })

  it('keeps v6 attempt order and rejects invalid JSON', () => {
    const migrated = normalizeStructuredPromptPayload({
      schema_version: 6,
      request_items: [],
      output_items: [],
      generation_attempts: [
        { attempt_id: 'first', provider_attempt: 1, status: 'failed' },
        { attempt_id: 'second', provider_attempt: 2, status: 'succeeded' },
      ],
    })

    expect(migrated?.generation_attempts.map((attempt) => attempt.attempt_id)).toEqual([
      'first',
      'second',
    ])
    expect(parseStructuredPrompt('{invalid json')).toBeNull()
  })
})

describe('schema helpers and remaining payload branches', () => {
  it('isRecord / stringifyStructuredValue / stringifyPromptContent cover scalar and multimodal parts', () => {
    expect(isRecord({ a: 1 })).toBe(true)
    expect(isRecord(null)).toBe(false)
    expect(isRecord([1])).toBe(false)

    expect(stringifyStructuredValue('plain')).toBe('plain')
    expect(stringifyStructuredValue(null)).toBe('')
    expect(stringifyStructuredValue(undefined)).toBe('')
    expect(stringifyStructuredValue({ k: 1 })).toBe(JSON.stringify({ k: 1 }, null, 2))

    expect(stringifyPromptContent('hello')).toBe('hello')
    expect(stringifyPromptContent(null)).toBe('')
    expect(stringifyPromptContent(undefined)).toBe('')
    expect(stringifyPromptContent({ nested: true })).toBe(JSON.stringify({ nested: true }, null, 2))
    expect(
      stringifyPromptContent([
        'line-a',
        { type: 'text', text: 'line-b' },
        { type: 'image', image_format: 'png', size_bytes: 12 },
        { type: 'image', image_format: '   ' },
        { type: 'IMAGE_URL', format: 'jpeg' },
        { type: 'input_image' },
        { type: '  ', image_format: '   ' },
        { type: 'unknown', extra: 1 },
        7,
        '',
      ])
    ).toBe(
      [
        'line-a',
        'line-b',
        '[图片 image/png 12 B]',
        '[图片 image/unknown]',
        '[图片 image/jpeg]',
        '[图片 image/unknown]',
        JSON.stringify({ type: '  ', image_format: '   ' }, null, 2),
        JSON.stringify({ type: 'unknown', extra: 1 }, null, 2),
        '7',
      ].join('\n')
    )
  })

  it('normalizes legacy content parts, tool calls and item meta fallbacks', () => {
    expect(normalizeLegacyContentParts('hi')).toEqual([{ type: 'text', text: 'hi' }])
    expect(normalizeLegacyContentParts(null)).toEqual([])
    expect(normalizeLegacyContentParts(undefined)).toEqual([])
    expect(normalizeLegacyContentParts(3)).toEqual([{ type: 'text', text: '3' }])
    expect(
      normalizeLegacyContentParts([
        { type: 'refusal', refusal: 'nope' },
        { type: 'refusal' },
        { type: 'image_url', url: 'x' },
        { type: 'input_image' },
        { type: 'text' },
        { type: 'other', n: 1 },
      ])
    ).toEqual([
      { type: 'refusal', refusal: 'nope' },
      { type: 'refusal', refusal: '' },
      { type: 'image', url: 'x' },
      { type: 'image' },
      { type: 'text', text: '' },
      { type: 'text', text: JSON.stringify({ type: 'other', n: 1 }, null, 2) },
    ])

    expect(normalizeLegacyToolCall('bad', 'fb')).toEqual({
      call_id: 'fb',
      func_name: 'unknown',
      args: {},
      extra_content: {},
    })
    expect(
      normalizeLegacyToolCall(
        {
          call_id: 'c2',
          func_name: 'lookup',
          arguments: { q: 1 },
          extra_content: { keep: true },
          source: 'plugin',
          source_label: '插件',
        },
        'fb'
      )
    ).toEqual({
      call_id: 'c2',
      func_name: 'lookup',
      args: { q: 1 },
      extra_content: {
        keep: true,
        tool_call_source: 'plugin',
        tool_call_source_label: '插件',
      },
    })
    expect(
      normalizeLegacyToolCall({ id: 'c3', args: { z: 2 }, function: { name: 'fn' } }, 'fb')
    ).toMatchObject({ call_id: 'c3', func_name: 'fn', args: { z: 2 } })

    expect(
      createLegacyItemMeta(
        { meta: { item_id: 'm1', logical_turn_id: 'turn-a', timestamp: '  ' } },
        'fallback'
      )
    ).toEqual({
      item_id: 'm1',
      logical_turn_id: 'turn-a',
      timestamp: '1970-01-01T00:00:00.000Z',
    })
    expect(createLegacyItemMeta({ item_id: 'raw' }, 'fallback').item_id).toBe('raw')
  })

  it('migrates every legacy role/item_type, including assistant reasoning groups', () => {
    const reasoning = migrateLegacyMessageToItems(
      { role: 'reasoning', content: 'think', reasoning_representation: 'summary' },
      0,
      'req'
    )
    expect(reasoning[0]).toMatchObject({
      item_type: 'ReasoningItem',
      representation: 'summary',
      text_parts: ['think'],
    })
    expect(
      migrateLegacyMessageToItems({ item_type: 'ReasoningItem', content: '' }, 1, 'req')[0]
    ).toMatchObject({ item_type: 'ReasoningItem', text_parts: [] })

    expect(
      migrateLegacyMessageToItems(
        { role: 'function_call', tool_calls: [{ id: 't1', function: { name: 'n', arguments: {} } }] },
        0,
        'req'
      )[0].item_type
    ).toBe('FunctionCallItem')
    expect(
      migrateLegacyMessageToItems({ item_type: 'FunctionCallItem' }, 0, 'req')[0].item_type
    ).toBe('FunctionCallItem')

    expect(
      migrateLegacyMessageToItems(
        { role: 'tool', tool_call_id: 'c1', content: 'out', tool_name: 'lookup' },
        0,
        'req'
      )[0]
    ).toMatchObject({
      item_type: 'FunctionCallOutputItem',
      call_id: 'c1',
      output: 'out',
      tool_name: 'lookup',
    })
    expect(migrateLegacyMessageToItems({ role: 'tool', content: 'out' }, 0, 'req')[0]).toMatchObject({
      item_type: 'FunctionCallOutputItem',
      call_id: 'req-1-call',
    })

    expect(
      migrateLegacyMessageToItems(
        {
          role: 'provider_activity',
          content: 'act',
          tool_call_id: 'c9',
          provider_type: 'openai',
          status: 'ok',
        },
        0,
        'req'
      )[0]
    ).toMatchObject({
      item_type: 'ProviderActivityItem',
      display_summary: 'act',
      provider_type: 'openai',
      status: 'ok',
    })
    expect(
      migrateLegacyMessageToItems({ item_type: 'ProviderOpaqueItem', content: 'opaque' }, 0, 'req')[0]
    ).toMatchObject({ item_type: 'ProviderOpaqueItem', display_summary: 'opaque' })
    expect(
      migrateLegacyMessageToItems({ item_type: 'ProviderActivityItem' }, 0, 'req')[0]
    ).toMatchObject({
      item_type: 'ProviderActivityItem',
      call_id: '',
      provider_type: 'unknown',
      status: '',
    })

    expect(migrateLegacyMessageToItems({ role: 'system', content: 'sys' }, 0, 'req')[0].item_type).toBe(
      'SystemMessageItem'
    )

    const grouped = migrateLegacyMessageToItems(
      {
        role: 'assistant',
        content: 'answer',
        reasoning_content: 'chain',
        logical_turn_id: 'turn-x',
        tool_calls: [{ id: 'call-z', function: { name: 'lookup', arguments: { q: 1 } } }],
      },
      0,
      'output'
    )
    expect(grouped.map((item) => item.item_type)).toEqual([
      'ReasoningItem',
      'AssistantMessageItem',
      'FunctionCallItem',
    ])
    expect(grouped.every((item) => item.meta.logical_turn_id === 'turn-x')).toBe(true)

    const toolsOnly = migrateLegacyMessageToItems(
      { role: 'assistant', content: '', tool_calls: [{ id: 'only' }] },
      0,
      'output'
    )
    expect(toolsOnly.map((item) => item.item_type)).toEqual(['FunctionCallItem'])

    const typedAssistant = migrateLegacyMessageToItems(
      { role: 'assistant', item_type: 'AssistantMessageItem', content: 'x', tool_calls: [{ id: 'n' }] },
      0,
      'output'
    )
    expect(typedAssistant).toHaveLength(1)
    expect(typedAssistant[0].item_type).toBe('AssistantMessageItem')
  })

  it('normalizes snapshots, traces, attempts and binds legacy tool turns', () => {
    expect(normalizeContextItemSnapshot('nope', 0, 'p')).toBeNull()
    expect(normalizeContextItemSnapshot({ item_type: '  ' }, 0, 'p')).toBeNull()
    expect(
      normalizeContextItemSnapshot({ item_type: 'UserMessageItem', extra: true }, 2, 'p')
    ).toMatchObject({ item_type: 'UserMessageItem', extra: true, meta: { item_id: 'p-3' } })

    expect(normalizeGenerationTrace('bad')).toBeUndefined()
    expect(
      normalizeGenerationTrace({
        provider: 'p',
        endpoint: 'e',
        model: 'm',
        response_id: 1,
        status: 'ok',
        prompt_tokens: 1,
        completion_tokens: 2,
        total_tokens: 3,
        prompt_cache_hit_tokens: 4,
        prompt_cache_miss_tokens: 5,
        output_item_ids: ['a', 2, null],
      })
    ).toEqual({
      provider: 'p',
      endpoint: 'e',
      model: 'm',
      response_id: null,
      status: 'ok',
      prompt_tokens: 1,
      completion_tokens: 2,
      total_tokens: 3,
      prompt_cache_hit_tokens: 4,
      prompt_cache_miss_tokens: 5,
      output_item_ids: ['a'],
    })

    expect(
      normalizeGenerationAttempt('bad', 0, {
        requestItems: [],
        outputItems: [],
        toolDefinitions: [],
        requestParameters: {},
      })
    ).toBeNull()

    const attempt = normalizeGenerationAttempt(
      {
        attempt: 4,
        generation_trace: { provider: 'tp', endpoint: 'te', model: 'tm', status: 'failed' },
        request_items: [{ item_type: 'UserMessageItem' }, 'skip'],
        output_items: [{ item_type: 'AssistantMessageItem' }, null],
        tool_definitions: [{ name: 't' }, 'no'],
        request_parameters: { temperature: 0.2 },
        provider_request: { body: 1 },
        error: { type: 'api', status_code: 500, message: 'boom', response_body: { err: true } },
        provider_name: 'pn',
        model_name: 'mn',
        timestamp: 'ts',
      },
      2,
      {
        requestItems: [],
        outputItems: [],
        toolDefinitions: [],
        requestParameters: {},
        requestKind: 'replyer',
      }
    )
    expect(attempt).toMatchObject({
      provider_attempt: 4,
      model_attempt: 1,
      status: 'failed',
      started_at: 'ts',
      workflow_purpose: 'replyer',
      provider: 'pn',
      model: 'mn',
      wire_request: { body: 1 },
      wire_response: { err: true },
      error: { type: 'api', status_code: 500, message: 'boom' },
    })
    expect(attempt?.request_items).toHaveLength(1)
    expect(attempt?.output_items).toHaveLength(1)
    expect(attempt?.tool_definitions).toEqual([{ name: 't' }])

    const succeeded = normalizeGenerationAttempt(
      {
        model_attempt: 3,
        workflow_attempt: 2,
        duration_ms: 15,
        client_type: 'openai',
      },
      0,
      {
        requestItems: [],
        outputItems: [],
        toolDefinitions: [{ name: 'fb' }],
        requestParameters: { p: 1 },
      }
    )
    expect(succeeded).toMatchObject({
      model_attempt: 3,
      workflow_attempt: 2,
      duration_ms: 15,
      status: 'succeeded',
      wire_protocol: 'openai',
    })

    const typedError = normalizeGenerationAttempt(
      { error: { type: 1, status_code: 'x', message: 2, response_body: 'raw' } },
      0,
      {
        requestItems: [],
        outputItems: [],
        toolDefinitions: [],
        requestParameters: {},
      }
    )
    expect(typedError).toMatchObject({
      status: 'failed',
      error: { type: undefined, status_code: null, message: undefined, response_body: 'raw' },
    })

    expect(migrateLegacyOutputToItems(null, 'output')).toEqual([])
    expect(migrateLegacyOutputToItems(undefined, 'output')).toEqual([])

    const bound = bindLegacyToolTurns([
      {
        item_type: 'FunctionCallItem',
        meta: { item_id: 'a', logical_turn_id: null, timestamp: '' },
        tool_call: 'bad',
      },
      {
        item_type: 'FunctionCallItem',
        meta: { item_id: 'b', logical_turn_id: null, timestamp: '' },
        tool_call: { call_id: '  ' },
      },
      {
        item_type: 'FunctionCallItem',
        meta: { item_id: 'c', logical_turn_id: null, timestamp: '' },
        tool_call: { call_id: 'call-9' },
      },
      {
        item_type: 'FunctionCallOutputItem',
        meta: { item_id: 'd', logical_turn_id: null, timestamp: '' },
        call_id: 'call-9',
        output: 'r',
        success: true,
        tool_name: 't',
      },
      {
        item_type: 'UserMessageItem',
        meta: { item_id: 'e', logical_turn_id: null, timestamp: '' },
        parts: [],
      },
    ])
    expect(bound[2].meta.logical_turn_id).toBe('legacy-tool-call-9-turn')
    expect(bound[3].meta.logical_turn_id).toBe('legacy-tool-call-9-turn')
    expect(bound[4].meta.logical_turn_id).toBeNull()
  })

  it('normalizes v6 payloads with attempts alias, jargon calls and parse edge cases', () => {
    expect(normalizeStructuredPromptPayload(null)).toBeNull()
    expect(normalizeStructuredPromptPayload([])).toBeNull()

    const migrated = normalizeStructuredPromptPayload({
      request: { kind: '' },
      request_type: 'planner',
      presentation: { output_title: '自定义标题' },
      attempts: [
        {
          attempt_id: 'a1',
          provider_attempt: 1,
          status: 'succeeded',
          request_items: [{ item_type: 'UserMessageItem', parts: [] }],
          output_items: [{ item_type: 'AssistantMessageItem', parts: [] }],
        },
        'skip-me',
      ],
      provider_request: { url: 'https://api.example' },
      request_parameters: { max_tokens: 8 },
      tool_definitions: [{ name: 'lookup' }],
      jargon_learning_calls: [
        'bad',
        {
          inference_stage: 'learn',
          request_items: [{ item_type: 'UserMessageItem', parts: [{ type: 'text', text: '词' }] }],
          output_items: [],
          generation_attempts: [],
        },
        { output_items: [{ item_type: 'AssistantMessageItem', parts: [] }] },
      ],
    })

    expect(migrated?.schema_version).toBe(6)
    expect(migrated?.presentation).toEqual({ output_title: '自定义标题' })
    expect(migrated?.generation_attempts).toHaveLength(1)
    expect(migrated?.generation_attempts[0].workflow_purpose).toBe('planner')
    expect(migrated?.generation_attempts[0].wire_request).toEqual({ url: 'https://api.example' })
    expect(migrated?.jargon_learning_calls).toHaveLength(2)
    expect(migrated?.jargon_learning_calls?.[0].inference_stage).toBe('learn')
    expect(migrated?.jargon_learning_calls?.[1].inference_stage).toBe('stage_3')

    expect(parseStructuredPrompt('   ')).toBeNull()
    expect(parseStructuredPrompt('[]')).toBeNull()
    expect(
      parseStructuredPrompt(
        JSON.stringify({
          schema_version: 6,
          request_items: [],
          output_items: [],
          generation_attempts: [],
        })
      )?.schema_version
    ).toBe(6)
  })
})
