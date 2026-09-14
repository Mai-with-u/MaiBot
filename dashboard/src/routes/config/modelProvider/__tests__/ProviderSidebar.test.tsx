import type { TestConnectionResult } from '@/lib/config-api'

import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ProviderSidebar } from '../ProviderSidebar'
import type { APIProvider } from '../types'

function makeProvider(name: string, baseUrl: string): APIProvider {
  return {
    name,
    base_url: baseUrl,
    api_key: 'sk-test',
    client_type: 'openai',
    max_retry: 2,
    timeout: 30,
    retry_interval: 10,
  }
}

function makeResult(overrides: Partial<TestConnectionResult> = {}): TestConnectionResult {
  return {
    network_ok: true,
    api_key_valid: true,
    latency_ms: 12,
    error: null,
    http_status: 200,
    ...overrides,
  }
}

afterEach(() => cleanup())

describe('ProviderSidebar', () => {
  it('空列表仍渲染全部入口，总数为 0，添加厂商触发回调', async () => {
    const user = userEvent.setup()
    const onAdd = vi.fn()
    const onSelectProvider = vi.fn()
    render(
      <ProviderSidebar
        providers={[]}
        modelCounts={new Map()}
        selectedProvider=""
        testingProviders={new Set()}
        testResults={new Map()}
        onSelectProvider={onSelectProvider}
        onAdd={onAdd}
      />
    )

    const allButton = screen.getByRole('button', { name: '全部' })
    expect(allButton).toHaveAttribute('aria-pressed', 'true')
    expect(allButton).toHaveTextContent('0')
    expect(allButton).toHaveClass('bg-primary')

    await user.click(screen.getByRole('button', { name: '添加厂商' }))
    await user.click(allButton)
    expect(onAdd).toHaveBeenCalledOnce()
    expect(onSelectProvider).toHaveBeenCalledWith('')
  })

  it('按模型数量降序排列，数量相同时保持原顺序，缺计数显示 0', async () => {
    const user = userEvent.setup()
    const onSelectProvider = vi.fn()
    const alpha = makeProvider('alpha', 'https://alpha.example/v1')
    const beta = makeProvider('beta', 'https://beta.example/v1')
    const gamma = makeProvider('gamma', 'https://gamma.example/v1')
    const idle = makeProvider('idle', 'https://idle.example/v1')

    render(
      <ProviderSidebar
        providers={[alpha, beta, gamma, idle]}
        modelCounts={new Map([
          ['alpha', 2],
          ['beta', 5],
          ['gamma', 2],
        ])}
        selectedProvider="beta"
        testingProviders={new Set()}
        testResults={new Map()}
        onSelectProvider={onSelectProvider}
        onAdd={vi.fn()}
      />
    )

    const names = screen.getAllByRole('button', { name: /筛选厂商/ }).map((button) => button.textContent)
    expect(names.map((text) => text?.split('https://')[0])).toEqual(['beta', 'alpha', 'gamma', 'idle'])

    const allButton = screen.getByRole('button', { name: '全部' })
    expect(allButton).toHaveAttribute('aria-pressed', 'false')
    expect(allButton).not.toHaveClass('bg-primary')
    // Map 中 2+5+2，idle 不在计数里
    expect(allButton).toHaveTextContent('9')

    const betaButton = screen.getByRole('button', { name: '筛选厂商 beta' })
    expect(betaButton).toHaveAttribute('aria-pressed', 'true')
    expect(betaButton).toHaveAttribute(
      'title',
      expect.stringContaining('beta · https://beta.example/v1')
    )
    expect(betaButton).toHaveTextContent('https://beta.example/v1')
    expect(betaButton.closest('div')).toHaveClass('bg-primary')
    expect(betaButton.closest('div')).toHaveTextContent('5')
    expect(screen.getByRole('button', { name: '筛选厂商 idle' }).closest('div')).toHaveTextContent('0')

    await user.click(screen.getByRole('button', { name: '筛选厂商 alpha' }))
    expect(onSelectProvider).toHaveBeenCalledWith('alpha')
  })

  it('计数均为空或 0 时保持原始顺序', () => {
    const first = makeProvider('first', 'https://first.example/v1')
    const second = makeProvider('second', 'https://second.example/v1')
    render(
      <ProviderSidebar
        providers={[first, second]}
        modelCounts={new Map([['second', 0]])}
        selectedProvider=""
        testingProviders={new Set()}
        testResults={new Map()}
        onSelectProvider={vi.fn()}
        onAdd={vi.fn()}
      />
    )

    const names = screen
      .getAllByRole('button', { name: /筛选厂商/ })
      .map((button) => button.getAttribute('aria-label'))
    expect(names).toEqual(['筛选厂商 first', '筛选厂商 second'])
    expect(screen.getByRole('button', { name: '全部' })).toHaveTextContent('0')
  })

  it('把测试中、成功和失败状态展示到厂商名称下划线', () => {
    const testing = makeProvider('testing', 'https://testing.example/v1')
    const ok = makeProvider('ok', 'https://ok.example/v1')
    const failed = makeProvider('failed', 'https://failed.example/v1')

    render(
      <ProviderSidebar
        providers={[testing, ok, failed]}
        modelCounts={new Map([
          ['testing', 1],
          ['ok', 1],
          ['failed', 1],
        ])}
        selectedProvider=""
        testingProviders={new Set(['testing'])}
        testResults={new Map([
          ['ok', makeResult({ latency_ms: 40 })],
          ['failed', makeResult({ network_ok: false, error: 'DNS 失败' })],
        ])}
        onSelectProvider={vi.fn()}
        onAdd={vi.fn()}
      />
    )

    expect(screen.getByText('testing')).toHaveClass('border-amber-500')
    expect(screen.getByRole('button', { name: '筛选厂商 testing' })).toHaveAttribute(
      'title',
      expect.stringContaining('正在测试厂商连接')
    )
    expect(screen.getByText('ok')).toHaveClass('border-green-500')
    expect(screen.getByRole('button', { name: '筛选厂商 ok' })).toHaveAttribute(
      'title',
      expect.stringContaining('延迟 40ms')
    )
    expect(screen.getByText('failed')).toHaveClass('border-red-500')
    expect(screen.getByRole('button', { name: '筛选厂商 failed' })).toHaveAttribute(
      'title',
      expect.stringContaining('DNS 失败')
    )
  })
})
