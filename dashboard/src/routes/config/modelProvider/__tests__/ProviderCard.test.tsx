import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { TestConnectionResult } from '@/lib/config-api'

import { ProviderCard } from '../ProviderCard'
import type { APIProvider } from '../types'

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

interface RenderOptions {
  provider?: APIProvider
  actualIndex?: number
  testingProviders?: Set<string>
  testResults?: Map<string, TestConnectionResult>
}

function renderCard(options: RenderOptions = {}) {
  const onEdit = vi.fn()
  const onDelete = vi.fn()
  const onTest = vi.fn()
  const provider = options.provider ?? makeProvider()
  render(
    <ProviderCard
      provider={provider}
      actualIndex={options.actualIndex ?? 0}
      testingProviders={options.testingProviders ?? new Set()}
      testResults={options.testResults ?? new Map()}
      onEdit={onEdit}
      onDelete={onDelete}
      onTest={onTest}
    />
  )
  return { onEdit, onDelete, onTest, provider }
}

afterEach(() => {
  cleanup()
})

describe('ProviderCard', () => {
  it('渲染提供商名称、URL、客户端类型与重试/超时信息', () => {
    renderCard()
    expect(screen.getByRole('heading', { name: 'DeepSeek' })).toBeInTheDocument()
    expect(screen.getByText('https://api.deepseek.com')).toBeInTheDocument()
    expect(screen.getByText('openai')).toBeInTheDocument()
    expect(screen.getByText('2 次 / 10 秒')).toBeInTheDocument()
    expect(screen.getByText('30')).toBeInTheDocument()
  })

  it('未测试时显示「未测试」状态徽章', () => {
    renderCard()
    expect(screen.getByLabelText('未测试：尚未执行厂商连接测试')).toBeInTheDocument()
  })

  it('存在测试结果时按结果渲染状态徽章', () => {
    const results = new Map<string, TestConnectionResult>([
      [
        'DeepSeek',
        { network_ok: false, api_key_valid: null, latency_ms: null, error: null, http_status: null },
      ],
    ])
    renderCard({ testResults: results })
    expect(screen.getByLabelText('连接失败：无法访问该厂商')).toBeInTheDocument()
  })

  it('测试进行中时测试按钮禁用并显示加载徽章', () => {
    renderCard({ testingProviders: new Set(['DeepSeek']) })
    expect(screen.getByTitle('测试连接')).toBeDisabled()
    expect(screen.getByLabelText('正在测试厂商连接')).toBeInTheDocument()
  })

  it('点击测试按钮以提供商名称调用 onTest', async () => {
    const user = userEvent.setup()
    const { onTest } = renderCard()
    await user.click(screen.getByTitle('测试连接'))
    expect(onTest).toHaveBeenCalledWith('DeepSeek')
  })

  it('点击编辑按钮以 provider 与 actualIndex 调用 onEdit', async () => {
    const user = userEvent.setup()
    const { onEdit, provider } = renderCard({ actualIndex: 3 })
    await user.click(screen.getByLabelText('编辑厂商 DeepSeek'))
    expect(onEdit).toHaveBeenCalledWith(provider, 3)
  })

  it('点击删除按钮以 actualIndex 调用 onDelete', async () => {
    const user = userEvent.setup()
    const { onDelete } = renderCard({ actualIndex: 5 })
    await user.click(screen.getByLabelText('删除厂商 DeepSeek'))
    expect(onDelete).toHaveBeenCalledWith(5)
  })
})
