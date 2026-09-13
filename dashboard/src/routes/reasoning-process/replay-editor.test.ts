import { describe, expect, it } from 'vitest'

import type { ReasoningReplayResponse } from '@/lib/reasoning-process-api'

import {
  createBlankReplayItem,
  createEditableReplayItems,
  formatReplayTokenSummary,
  getReplayTextParts,
  normalizeReplayModelOptions,
  parseEditableReplayItems,
  unwrapModelConfigPayload,
  updateReplayTextPart,
} from './replay-editor'
import type { StructuredPromptPayload } from './schema'

describe('重放 Item 正文编辑', () => {
  const itemJson = JSON.stringify(
    {
      item_type: 'SystemMessageItem',
      meta: {
        item_id: 'item-1',
        logical_turn_id: null,
        timestamp: '2026-08-06T00:00:00.000Z',
      },
      parts: [
        { type: 'text', text: '第一行\n第二行' },
        { type: 'image', image_format: 'png' },
        { type: 'text', text: '补充内容' },
      ],
    },
    null,
    2
  )

  it('从 Item JSON 中提取可读正文并保留真实换行', () => {
    expect(getReplayTextParts(itemJson)).toEqual([
      { index: 0, text: '第一行\n第二行' },
      { index: 2, text: '补充内容' },
    ])
  })

  it('只更新指定正文，不破坏其他 parts', () => {
    const updated = JSON.parse(updateReplayTextPart(itemJson, 0, '修改后\n正文')) as {
      parts: Array<Record<string, unknown>>
    }

    expect(updated.parts[0].text).toBe('修改后\n正文')
    expect(updated.parts[1]).toEqual({ type: 'image', image_format: 'png' })
    expect(updated.parts[2].text).toBe('补充内容')
  })

  it('无效 JSON 不提供正文编辑视图', () => {
    expect(getReplayTextParts('{')).toEqual([])
  })

  it('非对象 JSON 或缺少 parts 时不提供正文编辑视图', () => {
    expect(getReplayTextParts('[]')).toEqual([])
    expect(getReplayTextParts('1')).toEqual([])
    expect(getReplayTextParts('{"item_type":"UserMessageItem"}')).toEqual([])
    expect(getReplayTextParts('{"parts":"nope"}')).toEqual([])
    expect(
      getReplayTextParts(
        JSON.stringify({
          parts: [null, { type: 'text', text: 1 }, { type: 'image' }, { type: 'text', text: '可读' }],
        })
      )
    ).toEqual([{ index: 3, text: '可读' }])
  })

  it('parts 结构无效时拒绝更新正文', () => {
    expect(() => updateReplayTextPart('[]', 0, 'x')).toThrow('无法更新正文：Item 的 parts 结构无效')
    expect(() => updateReplayTextPart('{}', 0, 'x')).toThrow('无法更新正文：Item 的 parts 结构无效')
    expect(() => updateReplayTextPart('{"parts":[]}', 0, 'x')).toThrow(
      '无法更新正文：Item 的 parts 结构无效'
    )
  })
})

describe('unwrapModelConfigPayload', () => {
  it('非对象返回空对象，有 config 对象则解包，否则原样返回', () => {
    expect(unwrapModelConfigPayload(null)).toEqual({})
    expect(unwrapModelConfigPayload(undefined)).toEqual({})
    expect(unwrapModelConfigPayload('toml')).toEqual({})
    expect(unwrapModelConfigPayload(0)).toEqual({})
    expect(unwrapModelConfigPayload({ config: { models: [{ name: 'a' }] } })).toEqual({
      models: [{ name: 'a' }],
    })
    expect(unwrapModelConfigPayload({ models: [{ name: 'b' }] })).toEqual({
      models: [{ name: 'b' }],
    })
    expect(unwrapModelConfigPayload({ config: 'not-object', models: [] })).toEqual({
      config: 'not-object',
      models: [],
    })
  })
})

describe('normalizeReplayModelOptions', () => {
  it('从解包后的 models 提取非空名称，忽略无效项', () => {
    expect(normalizeReplayModelOptions(null)).toEqual([])
    expect(normalizeReplayModelOptions({ models: 'nope' })).toEqual([])
    expect(
      normalizeReplayModelOptions({
        config: {
          models: [
            null,
            'skip',
            {},
            { name: '   ' },
            { name: 'gpt-test' },
            { name: ' other-model ' },
          ],
        },
      })
    ).toEqual([{ name: 'gpt-test' }, { name: 'other-model' }])
  })
})

describe('createEditableReplayItems / createBlankReplayItem / parseEditableReplayItems', () => {
  it('prompt 为空时得到空列表，有 request_items 时用 item_id-index 作为编辑 id', () => {
    expect(createEditableReplayItems(null)).toEqual([])

    const prompt = {
      request_items: [
        {
          item_type: 'UserMessageItem',
          meta: {
            item_id: 'same',
            logical_turn_id: null,
            timestamp: '2026-08-06T00:00:00.000Z',
          },
          parts: [{ type: 'text', text: '甲' }],
        },
        {
          item_type: 'SystemMessageItem',
          meta: {
            item_id: 'same',
            logical_turn_id: null,
            timestamp: '2026-08-06T00:00:00.000Z',
          },
          parts: [{ type: 'text', text: '乙' }],
        },
      ],
      output_items: [],
      generation_attempts: [],
    } satisfies StructuredPromptPayload

    const items = createEditableReplayItems(prompt)
    expect(items).toHaveLength(2)
    expect(items[0]).toMatchObject({ id: 'same-0', itemType: 'UserMessageItem' })
    expect(items[1]).toMatchObject({ id: 'same-1', itemType: 'SystemMessageItem' })
    expect(JSON.parse(items[0].jsonText).parts[0].text).toBe('甲')
  })

  it('空白 Item 带 UserMessageItem 与空正文，id 去掉 UUID 连字符', () => {
    const item = createBlankReplayItem()
    const parsed = JSON.parse(item.jsonText) as {
      item_type: string
      meta: { item_id: string }
      parts: Array<{ type: string; text: string }>
    }

    expect(item.itemType).toBe('UserMessageItem')
    expect(item.id).toBe(parsed.meta.item_id)
    expect(item.id).not.toContain('-')
    expect(parsed.item_type).toBe('UserMessageItem')
    expect(parsed.parts).toEqual([{ type: 'text', text: '' }])
  })

  it('没有 randomUUID 时用时间戳生成 Item id', () => {
    const original = globalThis.crypto.randomUUID
    Object.defineProperty(globalThis.crypto, 'randomUUID', {
      configurable: true,
      writable: true,
      value: undefined,
    })
    try {
      const item = createBlankReplayItem()
      expect(item.id.startsWith('manual')).toBe(true)
      expect(item.itemType).toBe('UserMessageItem')
    } finally {
      Object.defineProperty(globalThis.crypto, 'randomUUID', {
        configurable: true,
        writable: true,
        value: original,
      })
    }
  })

  it('解析合法 JSON Item，并在缺少 item_id 时用 fallback', () => {
    const blank = createBlankReplayItem()
    const parsed = parseEditableReplayItems([
      blank,
      {
        id: 'legacy',
        itemType: 'SystemMessageItem',
        jsonText: JSON.stringify({ item_type: 'SystemMessageItem' }),
      },
    ])

    expect(parsed).toHaveLength(2)
    expect(parsed[0].item_type).toBe('UserMessageItem')
    expect(parsed[0].meta.item_id).toBe(blank.id)
    expect(parsed[1].item_type).toBe('SystemMessageItem')
    expect(parsed[1].meta.item_id).toBe('replay-2')
  })

  it('JSON 无效或缺 item_type 时抛出带序号的错误', () => {
    expect(() =>
      parseEditableReplayItems([{ id: 'bad', itemType: 'UserMessageItem', jsonText: '{' }])
    ).toThrow('第 1 个 Item 不是有效 JSON')

    expect(() =>
      parseEditableReplayItems([
        {
          id: 'ok',
          itemType: 'UserMessageItem',
          jsonText: JSON.stringify({
            item_type: 'UserMessageItem',
            meta: { item_id: 'ok', logical_turn_id: null, timestamp: 't' },
          }),
        },
        { id: 'bad', itemType: 'UserMessageItem', jsonText: 'null' },
      ])
    ).toThrow('第 2 个 Item 缺少 item_type 或 meta.item_id')

    expect(() =>
      parseEditableReplayItems([
        { id: 'bad', itemType: 'UserMessageItem', jsonText: JSON.stringify({ foo: 1 }) },
      ])
    ).toThrow('第 1 个 Item 缺少 item_type 或 meta.item_id')
  })
})

describe('formatReplayTokenSummary', () => {
  function makeResult(overrides: Partial<ReasoningReplayResponse> = {}): ReasoningReplayResponse {
    return {
      schema_version: 6,
      success: true,
      output_items: [],
      generation_attempts: [],
      model_name: 'gpt-test',
      prompt_tokens: 12,
      completion_tokens: 3,
      total_tokens: 15,
      prompt_cache_hit_tokens: 0,
      prompt_cache_miss_tokens: 0,
      duration_ms: 0,
      ...overrides,
    }
  }

  it('默认只展示输入输出总计', () => {
    expect(formatReplayTokenSummary(makeResult())).toBe('输入 12 · 输出 3 · 总计 15')
  })

  it('有缓存命中或未命中时追加缓存字段', () => {
    expect(formatReplayTokenSummary(makeResult({ prompt_cache_hit_tokens: 4 }))).toBe(
      '输入 12 · 输出 3 · 总计 15 · 缓存命中 4'
    )
    expect(formatReplayTokenSummary(makeResult({ prompt_cache_miss_tokens: 2 }))).toBe(
      '输入 12 · 输出 3 · 总计 15 · 缓存命中 0'
    )
  })

  it('按耗时量级格式化毫秒与秒', () => {
    expect(formatReplayTokenSummary(makeResult({ duration_ms: 50 }))).toContain('耗时 50.0 ms')
    expect(formatReplayTokenSummary(makeResult({ duration_ms: 150 }))).toContain('耗时 150 ms')
    expect(formatReplayTokenSummary(makeResult({ duration_ms: 2500 }))).toContain('耗时 2.50 s')
    expect(formatReplayTokenSummary(makeResult({ duration_ms: Number.POSITIVE_INFINITY }))).toBe(
      '输入 12 · 输出 3 · 总计 15 · 耗时 '
    )
  })
})
