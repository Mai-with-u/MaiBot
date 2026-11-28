import { defaultParseSearch, defaultStringifySearch } from '@tanstack/react-router'
import { expect, it } from 'vitest'
import { migrationUserId } from '../person-migration-navigation'

it('QQ 号和纯数字 OpenID 经路由解析仍完整保留字符串', () => {
  for (const source of ['123456789', '0012345']) {
    const target = '12345678901234567890123456789012'
    const search = defaultParseSearch(defaultStringifySearch({ source, target })) as Record<string, unknown>
    expect(migrationUserId(search.source)).toBe(source)
    expect(migrationUserId(search.target)).toBe(target)
  }
})
it('兼容旧链接的数字 QQ 号，拒绝丢失精度的数字', () => {
  expect(migrationUserId(123456789)).toBe('123456789')
  expect(migrationUserId(Number('12345678901234567890'))).toBeUndefined()
})

it('返回映射页的状态只序列化一次', () => {
  const params = new URLSearchParams(defaultStringifySearch({ state: { view: 'user' } }))
  expect(JSON.parse(params.get('state')!)).toEqual({ view: 'user' })
})
