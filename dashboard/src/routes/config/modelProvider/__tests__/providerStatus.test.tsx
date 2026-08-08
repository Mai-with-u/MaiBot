import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import type { TestConnectionResult } from '@/lib/config-api'

import { renderProviderTestStatus } from '../providerStatus'

/** 构造连接测试结果，按需覆盖字段 */
const makeResult = (overrides: Partial<TestConnectionResult> = {}): TestConnectionResult => ({
  network_ok: true,
  api_key_valid: true,
  latency_ms: null,
  error: null,
  http_status: 200,
  ...overrides,
})

afterEach(() => {
  cleanup()
})

describe('renderProviderTestStatus', () => {
  it('测试中时优先显示加载徽章，忽略已有结果', () => {
    render(<>{renderProviderTestStatus(makeResult(), true)}</>)
    const badge = screen.getByLabelText('正在测试厂商连接')
    expect(badge).toBeInTheDocument()
    expect(badge).toHaveAttribute('title', '正在测试厂商连接')
  })

  it('无结果且未在测试时显示「未测试」占位徽章', () => {
    render(<>{renderProviderTestStatus(undefined, false)}</>)
    expect(screen.getByLabelText('未测试：尚未执行厂商连接测试')).toBeInTheDocument()
  })

  it('网络可达且 API Key 有效时显示带延迟的成功描述', () => {
    render(<>{renderProviderTestStatus(makeResult({ latency_ms: 123 }), false)}</>)
    expect(
      screen.getByLabelText('连接正常：网络可访问，API Key 有效，延迟 123ms')
    ).toBeInTheDocument()
  })

  it('网络可达且 API Key 有效但无延迟数据时不带延迟后缀', () => {
    render(<>{renderProviderTestStatus(makeResult({ latency_ms: null }), false)}</>)
    expect(screen.getByLabelText('连接正常：网络可访问，API Key 有效')).toBeInTheDocument()
  })

  it('API Key 无效时优先展示后端 error 文本', () => {
    render(
      <>{renderProviderTestStatus(makeResult({ api_key_valid: false, error: '401 未授权' }), false)}</>
    )
    expect(screen.getByLabelText('401 未授权')).toBeInTheDocument()
  })

  it('API Key 无效且无 error 时使用默认无效描述', () => {
    render(<>{renderProviderTestStatus(makeResult({ api_key_valid: false }), false)}</>)
    expect(
      screen.getByLabelText('连接异常：网络可访问，但 API Key 无效或已过期')
    ).toBeInTheDocument()
  })

  it('API Key 有效性未知（null）时显示「可访问」描述并附带延迟', () => {
    render(
      <>{renderProviderTestStatus(makeResult({ api_key_valid: null, latency_ms: 88 }), false)}</>
    )
    expect(
      screen.getByLabelText('可访问：网络连接正常，但未确认 API Key 是否有效，延迟 88ms')
    ).toBeInTheDocument()
  })

  it('网络不可达时显示 error 文本，缺省时用默认失败描述', () => {
    const { unmount } = render(
      <>{renderProviderTestStatus(makeResult({ network_ok: false, error: 'DNS 解析失败' }), false)}</>
    )
    expect(screen.getByLabelText('DNS 解析失败')).toBeInTheDocument()
    unmount()

    render(<>{renderProviderTestStatus(makeResult({ network_ok: false }), false)}</>)
    expect(screen.getByLabelText('连接失败：无法访问该厂商')).toBeInTheDocument()
  })
})
