import { describe, expect, it } from 'vitest'

import { getReplayTextParts, updateReplayTextPart } from './replay-editor'

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
})
