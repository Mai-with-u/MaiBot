import { describe, expect, it } from 'vitest'

import type { APIProvider } from '../types'
import { cleanProviderData, validateProvider } from '../utils'

/** 构造一个字段齐全的合法提供商，按需覆盖部分字段 */
const makeProvider = (overrides: Partial<APIProvider> = {}): APIProvider => ({
  name: 'DeepSeek',
  base_url: 'https://api.deepseek.com',
  api_key: 'sk-test',
  client_type: 'openai',
  max_retry: 2,
  timeout: 30,
  retry_interval: 10,
  ...overrides,
})

describe('cleanProviderData', () => {
  it('数值字段为 null 时填充默认值 2/30/10', () => {
    const cleaned = cleanProviderData(
      makeProvider({ max_retry: null, timeout: null, retry_interval: null })
    )
    expect(cleaned.max_retry).toBe(2)
    expect(cleaned.timeout).toBe(30)
    expect(cleaned.retry_interval).toBe(10)
  })

  it('已有数值不被覆盖，包括 0 这样的合法边界值', () => {
    const cleaned = cleanProviderData(
      makeProvider({ max_retry: 0, timeout: 5, retry_interval: 1 })
    )
    // 使用 ?? 而不是 ||，0 应保留而不是被替换为默认值
    expect(cleaned.max_retry).toBe(0)
    expect(cleaned.timeout).toBe(5)
    expect(cleaned.retry_interval).toBe(1)
  })

  it('返回新对象且不修改入参，其他字段原样保留', () => {
    const source = makeProvider({ timeout: null })
    const cleaned = cleanProviderData(source)
    expect(cleaned).not.toBe(source)
    expect(source.timeout).toBeNull()
    expect(cleaned.name).toBe('DeepSeek')
    expect(cleaned.base_url).toBe('https://api.deepseek.com')
    expect(cleaned.api_key).toBe('sk-test')
    expect(cleaned.client_type).toBe('openai')
  })
})

describe('validateProvider', () => {
  it('provider 为 null 时返回「提供商数据为空」', () => {
    const result = validateProvider(null)
    expect(result.isValid).toBe(false)
    expect(result.errors).toEqual({ name: '提供商数据为空' })
  })

  it('名称为空白字符串时报「请输入提供商名称」', () => {
    const result = validateProvider(makeProvider({ name: '   ' }))
    expect(result.isValid).toBe(false)
    expect(result.errors.name).toBe('请输入提供商名称')
  })

  it('名称与现有提供商重复（忽略大小写与首尾空格）时报重复错误', () => {
    const existing = [makeProvider({ name: 'DeepSeek' })]
    const result = validateProvider(makeProvider({ name: '  deepseek ' }), existing, null)
    expect(result.isValid).toBe(false)
    expect(result.errors.name).toBe('提供商名称已存在，请使用其他名称')
  })

  it('编辑时排除自身索引，名称与自己相同不算重复', () => {
    const existing = [makeProvider({ name: 'DeepSeek' }), makeProvider({ name: 'OpenAI' })]
    const result = validateProvider(makeProvider({ name: 'DeepSeek' }), existing, 0)
    expect(result.isValid).toBe(true)
    expect(result.errors).toEqual({})
  })

  it('编辑时与其他索引的提供商重名仍然报错', () => {
    const existing = [makeProvider({ name: 'DeepSeek' }), makeProvider({ name: 'OpenAI' })]
    const result = validateProvider(makeProvider({ name: 'OpenAI' }), existing, 0)
    expect(result.isValid).toBe(false)
    expect(result.errors.name).toBe('提供商名称已存在，请使用其他名称')
  })

  it('base_url 与 api_key 为空白时分别报对应错误', () => {
    const result = validateProvider(makeProvider({ base_url: ' ', api_key: '' }))
    expect(result.isValid).toBe(false)
    expect(result.errors.base_url).toBe('请输入基础 URL')
    expect(result.errors.api_key).toBe('请输入 API Key')
    // 名称合法，不应报名称错误
    expect(result.errors.name).toBeUndefined()
  })

  it('三个必填字段同时为空时一次性返回全部错误', () => {
    const result = validateProvider(makeProvider({ name: '', base_url: '', api_key: '' }))
    expect(result.isValid).toBe(false)
    expect(result.errors).toEqual({
      name: '请输入提供商名称',
      base_url: '请输入基础 URL',
      api_key: '请输入 API Key',
    })
  })

  it('全部字段合法时校验通过，errors 为空对象', () => {
    const result = validateProvider(makeProvider(), [], null)
    expect(result.isValid).toBe(true)
    expect(result.errors).toEqual({})
  })
})
