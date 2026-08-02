import type { ReactNode } from 'react'

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { backendApi } from '@/lib/http'

import { StatisticsPage } from '../index'

vi.mock('react-i18next', () => {
  const t = (key: string) => key
  const i18n = { resolvedLanguage: 'zh-CN', language: 'zh-CN' }
  return { useTranslation: () => ({ t, i18n }) }
})

vi.mock('recharts', () => {
  const Stub = ({ children }: { children?: ReactNode }) => <div>{children}</div>
  return {
    __esModule: true,
    ResponsiveContainer: Stub,
    LineChart: Stub,
    Line: Stub,
    PieChart: Stub,
    Pie: Stub,
    Cell: Stub,
    XAxis: Stub,
    YAxis: Stub,
    CartesianGrid: Stub,
    Tooltip: Stub,
    Legend: Stub,
  }
})

vi.mock('@/lib/http', () => ({ backendApi: { get: vi.fn() } }))

const breakdown = {
  name: 'model-a',
  request_count: 2,
  input_tokens: 20,
  output_tokens: 10,
  total_tokens: 30,
  cache_hit_tokens: 15,
  cache_miss_tokens: 5,
  cache_hit_rate: 0.75,
  total_cost: 0.3,
  avg_time_cost: 1.5,
  std_time_cost: 0.5,
  avg_calls_per_reply: 1,
  avg_tokens_per_reply: 15,
  avg_tokens_per_call: 15,
}

const detailedData = {
  generated_at: '2026-07-01T12:00:00',
  periods: [
    {
      key: 'all_time',
      start_time: '2026-06-01T12:00:00',
      end_time: '2026-07-01T12:00:00',
      summary: {
        online_time: 3600,
        total_messages: 10,
        total_replies: 2,
        total_requests: 2,
        total_tokens: 30,
        input_tokens: 20,
        output_tokens: 10,
        cache_hit_tokens: 15,
        cache_miss_tokens: 5,
        cache_hit_rate: 0.75,
        total_cost: 0.3,
        cost_per_100_messages: 3,
        cost_per_100_messages_excluding_replies: 3.75,
        cost_per_100_replies: 15,
        cost_per_hour: 0.3,
        tokens_per_hour: 30,
      },
      models: [breakdown],
      modules: [{ ...breakdown, name: 'replyer' }],
      request_types: [{ ...breakdown, name: 'replyer.chat' }],
      chats: [{ name: '测试群聊', message_count: 10 }],
      distributions: {
        owner_costs: [{ name: '本体', value: 0.3 }],
        model_costs: [{ name: 'model-a', value: 0.3 }],
        module_costs: [{ name: 'replyer', value: 0.3 }],
        request_type_costs: [{ name: 'replyer.chat', value: 0.3 }],
        chat_messages: [{ name: '测试群聊', value: 10 }],
        chat_costs: [{ name: '测试群聊', value: 0.3 }],
      },
    },
  ],
  trends: {
    '24h': {
      time_labels: ['12:00'],
      total_cost_data: [0.3],
      cost_by_model: { 'model-a': [0.3] },
      cost_by_module: { replyer: [0.3] },
      message_by_chat: { 测试群聊: [10] },
    },
  },
  metrics: {
    '7d': {
      time_labels: ['07-01'],
      cost_per_100_messages: [3],
      cost_per_hour: [0.3],
      tokens_per_hour: [30],
      cost_per_100_replies: [15],
    },
  },
}

describe('StatisticsPage', () => {
  beforeEach(() => {
    vi.mocked(backendApi.get).mockResolvedValue(detailedData)
  })

  it('加载同源详细统计并保留 HTML 报告入口', async () => {
    const user = userEvent.setup()
    render(<StatisticsPage />)

    await waitFor(() => {
      expect(screen.getAllByText('model-a').length).toBeGreaterThan(0)
    })
    expect(backendApi.get).toHaveBeenCalledWith('/api/webui/statistics/detailed')
    expect(screen.getByText('statisticsPage.summary.requests')).toBeInTheDocument()

    const htmlLink = screen.getByRole('link', { name: 'statisticsPage.actions.openHtml' })
    expect(htmlLink).toHaveAttribute('href', '/maibot_statistics.html')
    expect(htmlLink).toHaveAttribute('target', '_blank')

    await user.click(screen.getByRole('tab', { name: 'statisticsPage.tabs.trends' }))
    expect(screen.getByText('statisticsPage.trends.totalCost')).toBeInTheDocument()
  })
})
