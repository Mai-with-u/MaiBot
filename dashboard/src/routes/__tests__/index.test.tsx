import type { ReactNode } from 'react'

import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { HomeCardManager } from '../home/HomeCardManager'
import { IndexPage } from '../index'
import { backendApi } from '@/lib/http'
import * as configApi from '@/lib/config-api'
import * as expressionApi from '@/lib/expression-api'
import * as systemApi from '@/lib/system-api'
import * as pluginApi from '@/lib/plugin-api'

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
  window.localStorage.clear()
})

// i18n 测试环境未初始化，t() 返回 key；mock 为恒等便于断言。
// t/i18n 必须是稳定引用（工厂内创建一次）——否则每渲染返回新 t，
// 会让依赖 [t] 的 fetchHitokoto 失稳、主 effect 无限重跑直至 OOM。
vi.mock('react-i18next', () => {
  const t = (k: string) => k
  const i18n = { resolvedLanguage: 'zh-CN', language: 'zh-CN' }
  return { useTranslation: () => ({ t, i18n }) }
})
vi.mock('@tanstack/react-router', () => ({
  Link: ({ children }: { children: ReactNode }) => <span>{children}</span>,
}))
vi.mock('@/lib/restart-context', () => ({
  RestartProvider: ({ children }: { children: ReactNode }) => <>{children}</>,
  useRestart: () => ({ isRestarting: false, triggerRestart: vi.fn() }),
}))
vi.mock('@/components/restart-overlay', () => ({ RestartOverlay: () => null }))
vi.mock('@/components/expression-reviewer', () => ({
  ExpressionReviewer: ({ open }: { open: boolean }) =>
    open ? <div data-testid="expression-reviewer" /> : null,
}))
// recharts 在 jsdom 无尺寸，显式列出用到的导出 stub 为占位
// （含 @/components/ui/chart.tsx 在模块加载期 `import * as` 访问的成员，避免命名空间缺成员崩溃）
vi.mock('recharts', () => {
  const Stub = ({ children }: { children?: ReactNode }) => <div>{children}</div>
  return {
    __esModule: true,
    ResponsiveContainer: Stub,
    LineChart: Stub,
    Line: Stub,
    BarChart: Stub,
    Bar: Stub,
    PieChart: Stub,
    Pie: Stub,
    Cell: Stub,
    AreaChart: Stub,
    Area: Stub,
    XAxis: Stub,
    YAxis: Stub,
    CartesianGrid: Stub,
    Tooltip: Stub,
    Legend: Stub,
    ReferenceLine: Stub,
  }
})
vi.mock('@/lib/http', () => ({ backendApi: { get: vi.fn() } }))
vi.mock('@/lib/config-api', () => ({ getBotConfigCached: vi.fn(), getModelConfigCached: vi.fn() }))
vi.mock('@/lib/expression-api', () => ({ getReviewStats: vi.fn() }))
vi.mock('@/lib/system-api', () => ({ getLocalCacheStats: vi.fn() }))
vi.mock('@/lib/plugin-api', () => ({
  getInstalledPlugins: vi.fn(),
  getPluginConfigSchema: vi.fn(),
  getPluginHomeCards: vi.fn(),
}))

const dashboardData = {
  summary: {
    total_requests: 1234,
    total_cost: 12.3,
    total_tokens: 56789,
    input_tokens: 48000,
    output_tokens: 8789,
    cache_hit_tokens: 24000,
    cache_miss_tokens: 24000,
    cache_hit_rate: 0.5,
    online_time: 3600,
    total_messages: 100,
    total_replies: 90,
    avg_response_time: 1.2,
    cost_per_hour: 1,
    tokens_per_hour: 100,
  },
  model_stats: [
    {
      model_name: 'gpt-4',
      request_count: 100,
      total_cost: 5,
      total_tokens: 2000,
      input_tokens: 1600,
      output_tokens: 400,
      cache_hit_tokens: 800,
      cache_miss_tokens: 800,
      cache_hit_rate: 0.5,
      avg_response_time: 2,
    },
  ],
  hourly_data: [
    {
      timestamp: '2025-01-01T00:00:00Z',
      requests: 10,
      cost: 1,
      tokens: 500,
      input_tokens: 400,
      output_tokens: 100,
      cache_hit_tokens: 200,
      cache_miss_tokens: 200,
    },
  ],
  daily_data: [
    {
      timestamp: '2025-01-01T00:00:00Z',
      requests: 240,
      cost: 24,
      tokens: 12000,
      input_tokens: 10000,
      output_tokens: 2000,
      cache_hit_tokens: 5000,
      cache_miss_tokens: 5000,
    },
  ],
  recent_activity: [],
}
const botStatus = {
  running: true,
  uptime: 3600,
  version: '1.0.0',
  start_time: '2025-01-01T00:00:00Z',
}

beforeEach(() => {
  vi.mocked(backendApi.get).mockImplementation((path: string) => {
    if (path.includes('/system/status')) return Promise.resolve(botStatus) as never
    if (path.includes('/statistics/dashboard')) return Promise.resolve(dashboardData) as never
    return Promise.resolve({}) as never
  })
  vi.mocked(configApi.getBotConfigCached).mockResolvedValue({} as never)
  vi.mocked(configApi.getModelConfigCached).mockResolvedValue({} as never)
  vi.mocked(expressionApi.getReviewStats).mockResolvedValue({ unchecked: 3, passed: 10 } as never)
  vi.mocked(systemApi.getLocalCacheStats).mockResolvedValue({
    directories: [],
    database: { total_size: 0, files: [], tables: [] },
  } as never)
  vi.mocked(pluginApi.getInstalledPlugins).mockResolvedValue([] as never)
  vi.mocked(pluginApi.getPluginHomeCards).mockResolvedValue([])
  // 一言 + GitHub 版本走原生 fetch
  vi.stubGlobal(
    'fetch',
    vi.fn((url: string) => {
      if (typeof url === 'string' && url.includes('github')) {
        return Promise.resolve({
          ok: true,
          json: async () => [{ tag_name: 'v2.0.0', draft: false, prerelease: false, html_url: '' }],
        })
      }
      return Promise.resolve({
        ok: true,
        json: async () => ({ hitokoto: '测试一言', from: '来源' }),
      })
    }) as never
  )
})

describe('IndexPage 特征化', () => {
  it('初始加载调用各数据源 API（仪表盘/状态/审核统计/本地缓存/配置）', async () => {
    render(<IndexPage />)
    await waitFor(() =>
      expect(backendApi.get).toHaveBeenCalledWith(
        '/api/webui/statistics/dashboard',
        expect.objectContaining({ query: { hours: 24 } })
      )
    )
    await waitFor(() =>
      expect(backendApi.get).toHaveBeenCalledWith(expect.stringContaining('/system/status'))
    )
    expect(expressionApi.getReviewStats).toHaveBeenCalled()
    expect(systemApi.getLocalCacheStats).toHaveBeenCalled()
    expect(configApi.getBotConfigCached).toHaveBeenCalled()
  })

  it('一言通过原生 fetch 拉取', async () => {
    render(<IndexPage />)
    await waitFor(() => expect(fetch).toHaveBeenCalledWith(expect.stringContaining('hitokoto')))
  })

  it('首页使用精简版本行且不再显示标题和版本卡片', async () => {
    window.localStorage.setItem(
      'maibot-home-card-layout-v1',
      JSON.stringify({
        order: [
          'builtin:bot-status',
          'builtin:quick-actions',
          'builtin:stats-overview',
          'builtin:storage',
        ],
        hidden: [],
        rowModes: {},
      })
    )
    render(<IndexPage />)

    expect(await screen.findByText('V1.0.0')).toBeInTheDocument()
    expect(screen.getByText('V1.6.0')).toBeInTheDocument()
    expect(
      await screen.findByRole('link', { name: /home\.versionCard\.updateAvailable V2\.0\.0/ })
    ).toBeInTheDocument()
    expect(screen.queryByText('home.title')).not.toBeInTheDocument()
    expect(screen.queryByText('home.versionCard.title')).not.toBeInTheDocument()

    await screen.findByText('home.storage.manage')
    expect(screen.queryByText('home.quickActions.title')).not.toBeInTheDocument()
    expect(screen.queryByText('home.storage.title')).not.toBeInTheDocument()
    const customizeButton = screen.getByRole('button', { name: 'home.quickActions.customize' })
    expect(customizeButton.parentElement).toHaveAttribute('data-home-titleless-content', 'true')
    expect(document.querySelector('[data-home-storage-details="true"]')).toHaveClass(
      'lg:grid-cols-2'
    )
    const cardIds = Array.from(document.querySelectorAll('[data-home-card-id]')).map((card) =>
      card.getAttribute('data-home-card-id')
    )
    expect(cardIds.slice(0, 3)).toEqual([
      'builtin:bot-status',
      'builtin:quick-actions',
      'builtin:storage',
    ])
  })

  it('插件首页卡片可以隐藏卡面标题', async () => {
    render(
      <HomeCardManager
        cards={[]}
        pluginCards={[
          {
            id: 'plugin:test:titleless',
            name: 'titleless',
            plugin_id: 'test',
            title: '仅用于管理的标题',
            show_title: false,
            description: '',
            content: '无标题卡片内容',
            link_url: '',
            link_label: '',
            icon: '',
            width: 'medium',
            order: 1000,
            enabled: true,
          },
        ]}
      />
    )

    expect(await screen.findByText('无标题卡片内容')).toBeInTheDocument()
    expect(screen.queryByText('仅用于管理的标题')).not.toBeInTheDocument()
  })

  it('切换时间范围以新的 hours 重新拉取仪表盘', async () => {
    const user = userEvent.setup()
    render(<IndexPage />)
    // 每张统计积木都拥有独立的轻量时间范围按钮。
    const sevenDayButtons = await screen.findAllByRole('button', { name: /home\.timeRange\.7d/ })
    await user.click(sevenDayButtons[0])
    await waitFor(() =>
      expect(backendApi.get).toHaveBeenCalledWith(
        '/api/webui/statistics/dashboard',
        expect.objectContaining({ query: { hours: 168 } })
      )
    )
  })
})
