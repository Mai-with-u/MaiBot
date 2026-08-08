import { describe, expect, it } from 'vitest'
import type { ContextItemSnapshot } from '@/lib/reasoning-process-api'
import { getRequestItemDiff } from './generation-attempts'

function createItem(itemId: string, text: string): ContextItemSnapshot {
  return {
    item_type: 'UserMessageItem',
    meta: {
      item_id: itemId,
      logical_turn_id: null,
      timestamp: '2026-08-06T00:00:00.000Z',
    },
    parts: [{ type: 'text', text }],
  }
}

describe('getRequestItemDiff', () => {
  it('内容完全一致时不返回差异', () => {
    const items = [createItem('item-1', '相同内容')]

    expect(getRequestItemDiff(items, structuredClone(items))).toEqual({
      changedOrAdded: [],
      removed: [],
    })
  })

  it('只返回新增、变更和移除的 Item', () => {
    const unchangedItem = createItem('item-1', '保持不变')
    const changedItem = createItem('item-2', '修改后')
    const addedItem = createItem('item-4', '新增')
    const removedItem = createItem('item-3', '被移除')

    expect(
      getRequestItemDiff(
        [unchangedItem, createItem('item-2', '修改前'), removedItem],
        [unchangedItem, changedItem, addedItem]
      )
    ).toEqual({
      changedOrAdded: [changedItem, addedItem],
      removed: [removedItem],
    })
  })
})
