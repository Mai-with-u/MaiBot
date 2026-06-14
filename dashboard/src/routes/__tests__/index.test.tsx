import type { ReactNode } from 'react'

import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

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
vi.mock('@tanstack/react-router', () => ({ Link: ({ children }: { children: ReactNode }) => <span>{children}</span> }))
vi.mock('@/lib/restart-context', () => ({
  RestartProvider: ({ children }: { children: ReactNode }) => <>{children}</>,
  useRestart: () => ({ isRestarting: false, triggerRestart: vi.fn() }),
}))
vi.mock('@/components/restart-overlay', () => ({ RestartOverlay: () => null }))
vi.mock('@/components/expression-reviewer', () => ({
  ExpressionReviewer: ({ open }: { open: boolean }) => (open ? <div data-testid="expression-reviewer" /> : null),
}))
// recharts 在 jsdom 无尺寸，显式列出用到的导出 stub 为占位
// （含 @/components/ui/chart.tsx 在模块加载期 `import * as` 访问的成员，避免命名空间缺成员崩溃）
vi.mock('recharts', () => {
  const Stub = ({ children }: { children?: ReactNode }) => <div>{children}</div>
  return {
    __esModule: true,
    ResponsiveContainer: Stub,
    LineChart: Stub, Line: Stub,
    BarChart: Stub, Bar: Stub,
    PieChart: Stub, Pie: Stub, Cell: Stub,
    AreaChart: Stub, Area: Stub,
    XAxis: Stub, YAxis: Stub, CartesianGrid: Stub,
    Tooltip: Stub, Legend: Stub, ReferenceLine: Stub,
  }
})
vi.mock('@/lib/http', () => ({ backendApi: { get: vi.fn() } }))
vi.mock('@/lib/config-api', () => ({ getBotConfigCached: vi.fn(), getModelConfigCached: vi.fn() }))
vi.mock('@/lib/expression-api', () => ({ getReviewStats: vi.fn() }))
vi.mock('@/lib/system-api', () => ({ getLocalCacheStats: vi.fn() }))
vi.mock('@/lib/plugin-api', () => ({ getInstalledPlugins: vi.fn(), getPluginConfigSchema: vi.fn() }))

const dashboardData = {
  summary: { total_requests: 1234, total_cost: 12.3, total_tokens: 56789, online_time: 3600, total_messages: 100, total_replies: 90, avg_response_time: 1.2, cost_per_hour: 1, tokens_per_hour: 100 },
  model_stats: [{ model_name: 'gpt-4', request_count: 100, total_cost: 5, total_tokens: 2000, avg_response_time: 2 }],
  hourly_data: [{ timestamp: '2025-01-01T00:00:00Z', requests: 10, cost: 1, tokens: 500 }],
  daily_data: [{ timestamp: '2025-01-01T00:00:00Z', requests: 240, cost: 24, tokens: 12000 }],
  recent_activity: [],
}
const botStatus = { running: true, uptime: 3600, version: '1.0.0', start_time: '2025-01-01T00:00:00Z' }

beforeEach(() => {
  vi.mocked(backendApi.get).mockImplementation((path: string) => {
    if (path.includes('/system/status')) return Promise.resolve(botStatus) as never
    if (path.includes('/statistics/dashboard')) return Promise.resolve(dashboardData) as never
    return Promise.resolve({}) as never
  })
  vi.mocked(configApi.getBotConfigCached).mockResolvedValue({} as never)
  vi.mocked(configApi.getModelConfigCached).mockResolvedValue({} as never)
  vi.mocked(expressionApi.getReviewStats).mockResolvedValue({ unchecked: 3, passed: 10 } as never)
  vi.mocked(systemApi.getLocalCacheStats).mockResolvedValue({ directories: [], database: { total_size: 0, files: [], tables: [] } } as never)
  vi.mocked(pluginApi.getInstalledPlugins).mockResolvedValue([] as never)
  // 一言 + GitHub 版本走原生 fetch
  vi.stubGlobal('fetch', vi.fn((url: string) => {
    if (typeof url === 'string' && url.includes('github')) {
      return Promise.resolve({ ok: true, json: async () => [{ tag_name: 'v2.0.0', draft: false, prerelease: false, html_url: '' }] })
    }
    return Promise.resolve({ ok: true, json: async () => ({ hitokoto: '测试一言', from: '来源' }) })
  }) as never)
})

describe('IndexPage 特征化', () => {
  it('初始加载调用各数据源 API（仪表盘/状态/审核统计/本地缓存/配置）', async () => {
    render(<IndexPage />)
    await waitFor(() =>
      expect(backendApi.get).toHaveBeenCalledWith(
        '/api/webui/statistics/dashboard',
        expect.objectContaining({ query: { hours: 24 } }),
      ),
    )
    await waitFor(() => expect(backendApi.get).toHaveBeenCalledWith(expect.stringContaining('/system/status')))
    expect(expressionApi.getReviewStats).toHaveBeenCalled()
    expect(systemApi.getLocalCacheStats).toHaveBeenCalled()
    expect(configApi.getBotConfigCached).toHaveBeenCalled()
  })

  it('一言通过原生 fetch 拉取', async () => {
    render(<IndexPage />)
    await waitFor(() => expect(fetch).toHaveBeenCalledWith(expect.stringContaining('hitokoto')))
  })

  it('切换时间范围以新的 hours 重新拉取仪表盘', async () => {
    const user = userEvent.setup()
    render(<IndexPage />)
    // 等待首屏渲染完成（仪表盘统计区出现时间范围 tab）
    // 时间范围 tab（7 天 = 168 小时）；168 在此前测试中未被缓存，点击后必触发新请求
    const sevenDay = await screen.findByRole('tab', { name: /home\.timeRange\.7d/ })
    await user.click(sevenDay)
    await waitFor(() =>
      expect(backendApi.get).toHaveBeenCalledWith('/api/webui/statistics/dashboard', expect.objectContaining({ query: { hours: 168 } })),
    )
  })
})
