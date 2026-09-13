import { afterEach, describe, expect, it, vi } from 'vitest'

import { generateId } from '../id'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('generateId', () => {
  it('优先使用 crypto.randomUUID', () => {
    vi.stubGlobal('crypto', {
      randomUUID: () => '11111111-2222-4333-8444-555555555555',
    })

    expect(generateId()).toBe('11111111-2222-4333-8444-555555555555')
  })

  it('没有 crypto 时回退到时间戳与随机数拼接', () => {
    vi.stubGlobal('crypto', undefined)
    vi.spyOn(Date, 'now').mockReturnValue(1_700_000_000_000)
    vi.spyOn(Math, 'random').mockReturnValue(0.123456789)

    expect(generateId()).toBe(
      `${(1_700_000_000_000).toString(36)}-${(0.123456789).toString(36).slice(2, 11)}`
    )
  })

  it('crypto.randomUUID 不是函数时同样走回退，且连续调用互不相同', () => {
    vi.stubGlobal('crypto', { randomUUID: 'not-a-function' })

    const ids = new Set(Array.from({ length: 8 }, () => generateId()))

    expect(ids.size).toBe(8)
    for (const id of ids) {
      expect(id).toMatch(/^[a-z0-9]+-[a-z0-9]+$/)
    }
  })
})
