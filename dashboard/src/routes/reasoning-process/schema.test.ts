// @vitest-environment node

import { describe, expect, it } from 'vitest'

import { normalizeStructuredPromptPayload, parseStructuredPrompt } from './schema'

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
