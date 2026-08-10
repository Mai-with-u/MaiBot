// @vitest-environment node

import { describe, expect, it } from 'vitest'

import type { ContextItemSnapshot } from '@/lib/reasoning-process-api'

import { getContextItemImages } from './context-items'

const itemMeta = {
  item_id: 'item-1',
  logical_turn_id: null,
  timestamp: '2026-08-10T00:00:00.000Z',
}

describe('getContextItemImages', () => {
  it('提取日志中省略 base64 后保留的本地图片路径', () => {
    const item: ContextItemSnapshot = {
      item_type: 'UserMessageItem',
      meta: itemMeta,
      parts: [
        { type: 'text', text: '请选择图片' },
        {
          type: 'image',
          image_format: 'png',
          size_bytes: 1024,
          base64_omitted: true,
          image_path: 'data/prompt_imgs/example.png',
        },
      ],
    }

    expect(getContextItemImages(item)).toEqual([
      {
        path: 'data/prompt_imgs/example.png',
        mimeType: 'image/png',
        sizeBytes: 1024,
      },
    ])
  })

  it('兼容引用对象中的图片路径，忽略 Base64 和非消息 Item', () => {
    const messageItem: ContextItemSnapshot = {
      item_type: 'UserMessageItem',
      meta: itemMeta,
      parts: [
        { type: 'input_image', format: 'jpeg', image_base64: 'YWJj' },
        {
          type: 'input_image',
          format: 'jpeg',
          image_reference: { image_path: 'data/prompt_imgs/reference.jpg' },
        },
      ],
    }
    const reasoningItem: ContextItemSnapshot = {
      item_type: 'ReasoningItem',
      meta: itemMeta,
      parts: [{ type: 'image', image_path: 'data/prompt_imgs/hidden.png' }],
    }

    expect(getContextItemImages(messageItem)).toEqual([
      {
        path: 'data/prompt_imgs/reference.jpg',
        mimeType: 'image/jpeg',
        sizeBytes: undefined,
      },
    ])
    expect(getContextItemImages(reasoningItem)).toEqual([])
  })
})
