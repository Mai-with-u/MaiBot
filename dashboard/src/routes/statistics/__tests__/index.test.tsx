import type { ReactElement, ReactNode } from 'react'

import { act, cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { backendApi } from '@/lib/http'

import { StatisticsPage } from '../index'
import type {
  DetailedPeriodTrend,
  DetailedReliabilityData,
  DetailedStatisticsBreakdown,
  DetailedStatisticsData,
  DetailedStatisticsDistributions,
  DetailedStatisticsMetricsData,
  DetailedStatisticsPeriod,
  DetailedStatisticsSummary,
  DetailedStatisticsTrendData,
} from '../types'

const i18nState = vi.hoisted(() => ({
  resolvedLanguage: 'zh-CN' as string | undefined,
  language: 'zh-CN',
}))

vi.mock('react-i18next', () => {
  const t = (key: string, options?: Record<string, unknown>) => {
    if (key === 'statisticsPage.heatmap.weekdays') {
      return '周一,周二,周三,周四,周五,周六,周日'
    }
    if (options && 'name' in options && key === 'statisticsPage.charts.toggleSeries') {
      return `toggle:${String(options.name)}`
    }
    if (options && 'name' in options && key === 'statisticsPage.distributions.filterTable') {
      return `filter:${String(options.name)}`
    }
    return key
  }
  return { useTranslation: () => ({ t, i18n: i18nState }) }
})

vi.mock('recharts', async () => {
  const { cloneElement, isValidElement } = await import('react')
  const Stub = ({ children }: { children?: ReactNode }) => <div>{children}</div>
  return {
    __esModule: true,
    ResponsiveContainer: Stub,
    LineChart: Stub,
    Line: Stub,
    BarChart: Stub,
    Bar: Stub,
    ComposedChart: Stub,
    ReferenceLine: Stub,
    PieChart: Stub,
    Pie: Stub,
    Cell: Stub,
    XAxis: Stub,
    CartesianGrid: Stub,
    Legend: Stub,
    YAxis: ({
      tickFormatter,
      children,
    }: {
      tickFormatter?: (value: number) => string
      children?: ReactNode
    }) => (
      <div data-testid="chart-yaxis">
        {tickFormatter ? <span>{tickFormatter(12_000)}</span> : null}
        {tickFormatter ? <span>{tickFormatter(0.5)}</span> : null}
        {children}
      </div>
    ),
    Tooltip: ({
      content,
      children,
    }: {
      content?: ReactNode | ((props: Record<string, unknown>) => ReactNode)
      children?: ReactNode
    }) => {
      const sampleProps = {
        active: true,
        label: '12:00',
        payload: [
          {
            name: 'value',
            value: 12,
            dataKey: 'value',
            color: 'var(--stats-series-0)',
            payload: {
              fill: 'var(--stats-series-0)',
              label: '12:00',
              attempts: 10,
              failures: 1,
              rate: 0.1,
            },
          },
        ],
      }
      return (
        <div data-testid="chart-tooltip">
          {typeof content === 'function'
            ? content(sampleProps)
            : isValidElement(content)
              ? cloneElement(content as ReactElement<Record<string, unknown>>, sampleProps)
              : content}
          {children}
        </div>
      )
    },
  }
})

const httpMocks = vi.hoisted(() => ({
  get: vi.fn(),
}))

vi.mock('@/lib/http', () => ({ backendApi: { get: httpMocks.get } }))

const breakdown: DetailedStatisticsBreakdown = {
  name: 'model-a',
  is_embedding: false,
  request_count: 60,
  input_tokens: 20_000,
  output_tokens: 10_000,
  total_tokens: 30_000,
  cache_hit_tokens: 15_000,
  cache_miss_tokens: 5_000,
  cache_hit_rate: 0.75,
  total_cost: 0.3,
  avg_time_cost: 1.5,
  std_time_cost: 3.5,
  avg_calls_per_reply: 1,
  avg_tokens_per_reply: 150,
  avg_tokens_per_call: 500,
  attempt_count: 60,
  failure_count: 6,
}

const abnormalBreakdown: DetailedStatisticsBreakdown = {
  name: 'broken-model',
  is_embedding: false,
  request_count: 0,
  input_tokens: 0,
  output_tokens: 0,
  total_tokens: 0,
  cache_hit_tokens: 0,
  cache_miss_tokens: 0,
  cache_hit_rate: null,
  total_cost: 0,
  avg_time_cost: 0,
  std_time_cost: 0,
  avg_calls_per_reply: null,
  avg_tokens_per_reply: null,
  avg_tokens_per_call: null,
  attempt_count: null,
  failure_count: null,
}

function makeSummary(overrides: Partial<DetailedStatisticsSummary> = {}): DetailedStatisticsSummary {
  return {
    online_time: 3600,
    total_messages: 10,
    total_replies: 2,
    total_requests: 60,
    total_tokens: 30_000,
    input_tokens: 20_000,
    output_tokens: 10_000,
    cache_hit_tokens: 15_000,
    cache_miss_tokens: 5_000,
    cache_hit_rate: 0.75,
    total_cost: 0.3,
    cost_per_100_messages: 3,
    cost_per_100_messages_excluding_replies: 3.75,
    cost_per_100_replies: 15,
    cost_per_hour: 0.3,
    tokens_per_hour: 30_000,
    ...overrides,
  }
}

function makeEmptyDistributions(): DetailedStatisticsDistributions {
  return {
    owner_tokens: [],
    model_tokens: [],
    module_tokens: [],
    request_type_tokens: [],
    chat_tokens: [],
    owner_costs: [],
    model_costs: [],
    module_costs: [],
    request_type_costs: [],
    chat_messages: [],
    chat_costs: [],
  }
}

function makeReliability(overrides: Partial<DetailedReliabilityData> = {}): DetailedReliabilityData {
  return {
    coverage_start: '2026-06-24T12:00:00',
    truncated: false,
    attempt_count: 60,
    failure_count: 6,
    failure_rate: 0.1,
    avg_success_duration: 1.5,
    by_model: [
      {
        name: 'model-a',
        attempt_count: 60,
        failure_count: 6,
        failure_rate: 0.1,
        avg_success_duration: 1.5,
      },
    ],
    by_provider: [
      {
        name: 'provider-a',
        attempt_count: 60,
        failure_count: 6,
        failure_rate: 0.1,
        avg_success_duration: 1.5,
      },
    ],
    by_task: [
      {
        name: 'replyer',
        attempt_count: 60,
        failure_count: 6,
        failure_rate: 0.1,
        avg_success_duration: 1.5,
      },
    ],
    ...overrides,
  }
}

function makePeriodTrend(overrides: Partial<DetailedPeriodTrend> = {}): DetailedPeriodTrend {
  const heatmapMessages = Array.from({ length: 7 }, () => Array.from({ length: 24 }, () => 0))
  heatmapMessages[0][12] = 10
  const heatmapCosts = Array.from({ length: 7 }, () => Array.from({ length: 24 }, () => 0))
  heatmapCosts[0][12] = 0.3
  return {
    bucket_minutes: 60,
    start_time: '2026-06-01T12:00:00',
    truncated: false,
    time_labels: ['12:00', '13:00'],
    costs: [0.2, 0.1],
    tokens: [20_000, 10_000],
    requests: [40, 20],
    messages: [6, 4],
    attempts: [40, 20],
    failures: [4, 2],
    cost_by_model: { 'model-a': [0.2, 0.1] },
    tokens_by_model: { 'model-a': [20_000, 10_000] },
    requests_by_model: { 'model-a': [40, 20] },
    cost_by_module: { replyer: [0.2, 0.1] },
    message_by_chat: { 测试群聊: [6, 4] },
    heatmap_messages: heatmapMessages,
    heatmap_costs: heatmapCosts,
    ...overrides,
  }
}

function makePeriod(overrides: Partial<DetailedStatisticsPeriod> = {}): DetailedStatisticsPeriod {
  return {
    key: 'last_7_days',
    start_time: '2026-06-24T12:00:00',
    end_time: '2026-07-01T12:00:00',
    summary: makeSummary(),
    previous_summary: makeSummary({ total_cost: 0.2, total_requests: 50, total_tokens: 25_000 }),
    models: [breakdown],
    task_groups: [{ ...breakdown, name: 'replyer' }],
    modules: [{ ...breakdown, name: 'replyer', attempt_count: null, failure_count: null }],
    request_types: [{ ...breakdown, name: 'replyer.chat', attempt_count: null, failure_count: null }],
    chats: [{ name: '测试群聊', message_count: 10 }],
    distributions: {
      owner_tokens: [{ name: '本体', value: 30_000 }],
      model_tokens: [{ name: 'model-a', value: 30_000 }],
      module_tokens: [{ name: 'replyer', value: 30_000 }],
      request_type_tokens: [{ name: 'replyer.chat', value: 30_000 }],
      chat_tokens: [{ name: '测试群聊', value: 30_000 }],
      owner_costs: [{ name: '本体', value: 0.3 }],
      model_costs: [{ name: 'model-a', value: 0.3 }],
      module_costs: [{ name: 'replyer', value: 0.3 }],
      request_type_costs: [{ name: 'replyer.chat', value: 0.3 }],
      chat_messages: [{ name: '测试群聊', value: 10 }],
      chat_costs: [{ name: '测试群聊', value: 0.3 }],
    },
    reliability: makeReliability(),
    trend: makePeriodTrend(),
    ...overrides,
  }
}

function makeTrend(overrides: Partial<DetailedStatisticsTrendData> = {}): DetailedStatisticsTrendData {
  return {
    time_labels: ['12:00', '13:00'],
    total_cost_data: [0.3],
    cost_by_model: { 'model-a': [0.3] },
    cost_by_module: { replyer: [0.3] },
    message_by_chat: { 测试群聊: [10] },
    ...overrides,
  }
}

function makeMetrics(
  overrides: Partial<DetailedStatisticsMetricsData> = {}
): DetailedStatisticsMetricsData {
  return {
    time_labels: ['07-01', '07-02'],
    cost_per_100_messages: [3],
    cost_per_hour: [0.3],
    tokens_per_hour: [30],
    cost_per_100_replies: [15],
    ...overrides,
  }
}

function makeData(overrides: Partial<DetailedStatisticsData> = {}): DetailedStatisticsData {
  return {
    generated_at: '2026-07-01T12:00:00',
    periods: [makePeriod()],
    trends: {
      '24h': makeTrend(),
    },
    metrics: {
      '7d': makeMetrics(),
    },
    ...overrides,
  }
}

const detailedData = makeData()

function createDeferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((nextResolve, nextReject) => {
    resolve = nextResolve
    reject = nextReject
  })
  return { promise, resolve, reject }
}

async function renderFreshPage() {
  cleanup()
  vi.resetModules()
  const { StatisticsPage: Page } = await import('../index')
  let view!: ReturnType<typeof render>
  await act(async () => {
    view = render(<Page />)
    await Promise.resolve()
  })
  return view
}

async function waitForPageData() {
  await waitFor(() => {
    expect(screen.getByText('statisticsPage.summary.requests')).toBeInTheDocument()
  })
}

describe('StatisticsPage', () => {
  beforeEach(() => {
    i18nState.resolvedLanguage = 'zh-CN'
    i18nState.language = 'zh-CN'
    vi.mocked(backendApi.get).mockResolvedValue(detailedData)
  })

  afterEach(() => {
    cleanup()
  })

  it('加载详细统计并显示核心模块', async () => {
    render(<StatisticsPage />)

    await waitFor(() => {
      expect(screen.getAllByText('model-a').length).toBeGreaterThan(0)
    })
    expect(backendApi.get).toHaveBeenCalledWith('/api/webui/statistics/detailed')
    expect(screen.getByText('statisticsPage.summary.requests')).toBeInTheDocument()
    expect(screen.getByText('statisticsPage.modules.usage')).toBeInTheDocument()
    expect(screen.getByText('statisticsPage.modules.failureRank')).toBeInTheDocument()
    expect(screen.getByText('statisticsPage.modules.heatmap')).toBeInTheDocument()
  })

  it('接口失败时展示错误提示', async () => {
    vi.mocked(backendApi.get).mockRejectedValue(new Error('统计接口挂了'))
    await renderFreshPage()

    expect(await screen.findByRole('alert')).toBeInTheDocument()
    expect(screen.getByText('statisticsPage.error.title')).toBeInTheDocument()
    expect(screen.getByText('统计接口挂了')).toBeInTheDocument()
    expect(screen.queryByText('statisticsPage.summary.requests')).not.toBeInTheDocument()
  })

  it('非 Error 失败时不展示错误页也不展示数据', async () => {
    vi.mocked(backendApi.get).mockRejectedValue('network down')
    await renderFreshPage()

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'statisticsPage.actions.refresh' })).not.toBeDisabled()
    })
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.queryByText('statisticsPage.summary.requests')).not.toBeInTheDocument()
    expect(document.querySelector('.animate-pulse')).toBeNull()
  })

  it('首次加载且无缓存时展示骨架屏', async () => {
    const deferred = createDeferred<DetailedStatisticsData>()
    vi.mocked(backendApi.get).mockImplementation(() => deferred.promise)

    const view = await renderFreshPage()
    expect(view.container.querySelector('.animate-pulse')).not.toBeNull()
    expect(screen.getByRole('button', { name: 'statisticsPage.actions.refresh' })).toBeDisabled()

    await act(async () => {
      deferred.resolve(detailedData)
    })
    await waitForPageData()
    expect(view.container.querySelector('.animate-pulse')).toBeNull()
  })

  it('空分类、空聊天与空分布展示空状态，并可切换双明细表维度', async () => {
    const user = userEvent.setup()
    vi.mocked(backendApi.get).mockResolvedValue(
      makeData({
        periods: [
          makePeriod({
            models: [],
            task_groups: [],
            modules: [],
            request_types: [],
            chats: [],
            distributions: makeEmptyDistributions(),
            reliability: makeReliability({
              attempt_count: 0,
              failure_count: 0,
              failure_rate: null,
              by_model: [],
              by_provider: [],
              by_task: [],
            }),
            trend: makePeriodTrend({
              costs: [],
              tokens: [],
              requests: [],
              messages: [],
              attempts: [],
              failures: [],
              heatmap_messages: [],
              heatmap_costs: [],
            }),
          }),
        ],
      })
    )
    await renderFreshPage()
    await waitForPageData()

    expect(screen.getAllByText('statisticsPage.empty').length).toBeGreaterThan(0)

    const dimensionGroups = screen.getAllByRole('group', { name: 'statisticsPage.detail.dimension' })
    expect(dimensionGroups).toHaveLength(2)
    await user.click(within(dimensionGroups[0]).getByRole('button', { name: 'statisticsPage.breakdowns.chats' }))
    expect(screen.getAllByText('statisticsPage.empty').length).toBeGreaterThan(0)
  })

  it('可切换统一趋势图的指标、拆分维度与模式，并切换可靠性维度', async () => {
    const user = userEvent.setup()
    await renderFreshPage()
    await waitForPageData()

    const metricGroup = screen.getByRole('group', { name: 'statisticsPage.trend.metric' })
    await user.click(within(metricGroup).getByRole('button', { name: 'statisticsPage.trend.metrics.tokens' }))
    expect(within(metricGroup).getByRole('button', { name: 'statisticsPage.trend.metrics.tokens' })).toHaveAttribute(
      'aria-pressed',
      'true'
    )

    const modeGroup = screen.getByRole('group', { name: 'statisticsPage.trend.mode' })
    await user.click(within(modeGroup).getByRole('button', { name: 'statisticsPage.trend.modes.line' }))
    expect(within(modeGroup).getByRole('button', { name: 'statisticsPage.trend.modes.line' })).toHaveAttribute(
      'aria-pressed',
      'true'
    )

    const reliabilityGroup = screen.getByRole('group', { name: 'statisticsPage.reliability.dimension' })
    await user.click(
      within(reliabilityGroup).getByRole('button', { name: 'statisticsPage.reliability.dimensions.provider' })
    )
    expect(screen.getByText('provider-a')).toBeInTheDocument()
  })

  it('展示缓存命中率与环比，按周期排序，并支持点击饼图图例联动筛选明细表', async () => {
    const user = userEvent.setup()
    i18nState.resolvedLanguage = undefined
    i18nState.language = 'zh-CN'

    vi.mocked(backendApi.get).mockResolvedValue(
      makeData({
        periods: [
          makePeriod({
            key: 'weird_period',
            previous_summary: null,
            summary: makeSummary({ online_time: -8, cache_hit_rate: 0 }),
            models: [abnormalBreakdown],
            task_groups: [],
            modules: [],
            request_types: [],
            chats: [],
            distributions: makeEmptyDistributions(),
          }),
          makePeriod({
            key: 'last_15_minutes',
            summary: makeSummary({
              online_time: 45,
              total_tokens: 15_000,
              total_messages: 10_000,
              cache_hit_rate: null,
              cache_hit_tokens: 0,
              cache_miss_tokens: 0,
            }),
            models: [abnormalBreakdown, { ...breakdown, name: 'model-b' }],
            chats: [{ name: '私聊用户', message_count: 12_000 }],
          }),
          makePeriod({
            key: 'last_7_days',
            summary: makeSummary({
              online_time: 90_061,
              cache_hit_rate: 0.5,
            }),
          }),
        ],
      })
    )
    await renderFreshPage()
    await waitForPageData()

    const periodGroup = screen.getByRole('group', { name: 'statisticsPage.sections.period' })
    const periodButtons = within(periodGroup)
      .getAllByRole('button')
      .map((button) => button.getAttribute('aria-label'))
    expect(periodButtons).toEqual([
      'statisticsPage.periods.last_7_days',
      'statisticsPage.periods.last_15_minutes',
      'statisticsPage.periods.weird_period',
    ])

    expect(screen.getByText(/1天 1小时 1分钟/)).toBeInTheDocument()
    expect(screen.getAllByText('50.0%').length).toBeGreaterThan(0)

    // 点击饼图图例联动筛选 TABLE A
    const filterButtons = screen.getAllByTitle('filter:replyer')
    await user.click(filterButtons[0])
    const searchInputs = screen.getAllByRole('textbox', { name: 'statisticsPage.detail.search' })
    expect(searchInputs[0]).toHaveValue('replyer')

    await user.click(
      within(periodGroup).getByRole('button', { name: 'statisticsPage.periods.last_15_minutes' })
    )
    expect(screen.getByText(/45秒/)).toBeInTheDocument()
    expect(screen.getAllByText('1.5万').length).toBeGreaterThan(0)
    expect(screen.getAllByText('N/A').length).toBeGreaterThan(0)
  })

  it('可隐藏趋势图系列、展开明细行并在刷新时再次请求接口', async () => {
    const user = userEvent.setup()
    await renderFreshPage()
    await waitForPageData()

    const seriesButton = screen.getByTitle('toggle:model-a')
    expect(seriesButton).toHaveAttribute('aria-pressed', 'true')
    await user.click(seriesButton)
    expect(seriesButton).toHaveAttribute('aria-pressed', 'false')
    await user.click(seriesButton)
    expect(seriesButton).toHaveAttribute('aria-pressed', 'true')

    // 展开第一张明细表的第一行查看补充字段
    const tables = screen.getAllByRole('table')
    const dataRow = within(tables[0]).getAllByRole('row')[1]
    await user.click(dataRow)
    expect(within(tables[0]).getByText('statisticsPage.detail.attemptsFailures')).toBeInTheDocument()

    vi.mocked(backendApi.get).mockClear()
    vi.mocked(backendApi.get).mockResolvedValue(detailedData)
    await user.click(screen.getByRole('button', { name: 'statisticsPage.actions.refresh' }))
    await waitFor(() => {
      expect(backendApi.get).toHaveBeenCalledWith('/api/webui/statistics/detailed')
    })
  })

  it('模块缓存命中时立即展示已有数据，刷新过程不再出现骨架屏', async () => {
    await renderFreshPage()
    await waitForPageData()
    cleanup()

    const deferred = createDeferred<DetailedStatisticsData>()
    vi.mocked(backendApi.get).mockImplementation(() => deferred.promise)
    const { StatisticsPage: CachedPage } = await import('../index')
    let view!: ReturnType<typeof render>
    await act(async () => {
      view = render(<CachedPage />)
      await Promise.resolve()
    })

    expect(screen.getByText('statisticsPage.summary.requests')).toBeInTheDocument()
    expect(screen.getAllByText('75.0%').length).toBeGreaterThan(0)
    expect(view.container.querySelector('.animate-pulse')).toBeNull()
    expect(screen.getByRole('button', { name: 'statisticsPage.actions.refresh' })).toBeDisabled()

    await act(async () => {
      deferred.resolve(detailedData)
    })
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'statisticsPage.actions.refresh' })).not.toBeDisabled()
    })
  })

  it('千禧主题下 HTML 图例与图表使用同一套配色变量', async () => {
    const { readFileSync } = await vi.importActual<{
      readFileSync: (path: string, encoding: 'utf8') => string
    }>('node:fs')
    const { fileURLToPath } = await vi.importActual<{
      fileURLToPath: (url: string) => string
    }>('node:url')
    const testFileUrl = import.meta.url
    const style = document.createElement('style')
    style.textContent = readFileSync(
      fileURLToPath(new URL('../../../styles/millennium.css', testFileUrl).href),
      'utf8'
    )
    document.head.appendChild(style)
    document.documentElement.dataset.dashboardStyle = 'millennium'
    try {
      await renderFreshPage()
      await waitForPageData()

      const chartColor = (element: Element) =>
        getComputedStyle(element).getPropertyValue('--color-chart-1')
      const chart = document.querySelector('[data-chart]')
      const legend = document.querySelector('[data-chart-legend="true"]')
      expect(chart).not.toBeNull()
      expect(legend).not.toBeNull()
      expect(chartColor(chart!)).not.toBe('')
      expect(chartColor(legend!)).toBe(chartColor(chart!))
    } finally {
      delete document.documentElement.dataset.dashboardStyle
      style.remove()
    }
  })

  it('没有周期数据时展示空状态', async () => {
    vi.mocked(backendApi.get).mockResolvedValue(
      makeData({
        periods: [],
        trends: {},
        metrics: {},
      })
    )
    await renderFreshPage()

    await waitFor(() => {
      expect(screen.getByText('statisticsPage.empty')).toBeInTheDocument()
    })
    expect(screen.getByText('statisticsPage.generatedAt')).toBeInTheDocument()
  })
})
