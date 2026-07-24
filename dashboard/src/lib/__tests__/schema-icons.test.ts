import { CircleHelp, MessageSquare, Settings } from 'lucide-react'
import { describe, expect, it } from 'vitest'

import { resolveSchemaIcon } from '../schema-icons'

describe('resolveSchemaIcon', () => {
  it('支持后端使用的 kebab-case 图标名', () => {
    expect(resolveSchemaIcon('message-square')).toBe(MessageSquare)
  })

  it('兼容已有 schema 使用的 PascalCase 图标名', () => {
    expect(resolveSchemaIcon('Settings')).toBe(Settings)
  })

  it('未预注册的图标使用稳定的通用图标', () => {
    expect(resolveSchemaIcon('Coffee')).toBe(CircleHelp)
  })
})
