import { describe, expect, it } from 'vitest'

import type { TestConnectionResult } from '@/lib/config-api'

import { getProviderTestStatus } from '../providerStatus'

/** 构造连接测试结果，按需覆盖字段 */
const makeResult = (overrides: Partial<TestConnectionResult> = {}): TestConnectionResult => ({
  network_ok: true,
  api_key_valid: true,
  latency_ms: null,
  error: null,
  http_status: 200,
  ...overrides,
})

describe('getProviderTestStatus', () => {
  it('测试中时优先返回动态状态，忽略已有结果', () => {
    expect(getProviderTestStatus(makeResult(), true)).toEqual({
      description: '正在测试厂商连接',
      className: 'border-amber-500 animate-pulse',
    })
  })

  it('无结果且未在测试时返回透明下划线', () => {
    expect(getProviderTestStatus(undefined, false)).toEqual({
      description: '未测试：尚未执行厂商连接测试',
      className: 'border-transparent',
    })
  })

  it('网络可达且 API Key 有效时返回带延迟的成功状态', () => {
    expect(getProviderTestStatus(makeResult({ latency_ms: 123 }), false)).toEqual({
      description: '连接正常：网络可访问，API Key 有效，延迟 123ms',
      className: 'border-green-500',
    })
  })

  it('API Key 无效时优先展示后端错误', () => {
    expect(
      getProviderTestStatus(makeResult({ api_key_valid: false, error: '401 未授权' }), false)
    ).toEqual({ description: '401 未授权', className: 'border-red-500' })
  })

  it('API Key 有效性未知时返回可访问状态', () => {
    expect(
      getProviderTestStatus(makeResult({ api_key_valid: null, latency_ms: 88 }), false)
    ).toEqual({
      description: '可访问：网络连接正常，但未确认 API Key 是否有效，延迟 88ms',
      className: 'border-blue-500',
    })
  })

  it('网络不可达时返回错误状态', () => {
    expect(
      getProviderTestStatus(makeResult({ network_ok: false, error: 'DNS 解析失败' }), false)
    ).toEqual({ description: 'DNS 解析失败', className: 'border-red-500' })
  })
})
