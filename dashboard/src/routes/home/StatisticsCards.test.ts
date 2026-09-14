import { cleanup, render, screen } from '@testing-library/react'
import { createElement } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import userEvent from '@testing-library/user-event'

import {
  CostTrendCard,
  DailyStatisticsCard,
  ModelDetailsCard,
  ModelDistributionCard,
  PromptCacheCard,
  RequestTrendCard,
  StatisticsOverviewCard,
  TokenTrendCard,
} from './StatisticsCards'
import {
  formatChartTimeAxis,
  formatTokenAxis,
  selectChartTimeSeries,
} from './statistics-chart-utils'

import type { ReactElement, ReactNode } from 'react'
import type { DashboardData, ModelStatistics, TimeSeriesData } from './types'

const HOURLY_TS = '2026-07-28T13:45:00+08:00'
const DAILY_TS = '2026-07-01T00:00:00+08:00'

const i18nState = vi.hoisted(() => ({
  resolvedLanguage: 'en-US' as string | undefined,
  language: 'zh-CN',
}))

const dashboardState = vi.hoisted(() => ({
  data: null as DashboardData | null,
  error: null as string | null,
  loading: false,
  timeRange: 24,
  setTimeRange: vi.fn(),
  fetchDashboardData: vi.fn(async () => {}),
}))

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, options?: Record<string, unknown>) => {
      if (!options) {
        return key
      }
      return `${key}:${Object.entries(options)
        .map(([name, value]) => `${name}=${value}`)
        .join(',')}`
    },
    i18n: i18nState,
  }),
}))

vi.mock('./hooks/useDashboardData', () => ({
  useDashboardData: () => ({
    dashboardData: dashboardState.data,
    error: dashboardState.error,
    loading: dashboardState.loading,
    timeRange: dashboardState.timeRange,
    setTimeRange: dashboardState.setTimeRange,
    fetchDashboardData: dashboardState.fetchDashboardData,
  }),
}))

vi.mock('@/components/ui/zoomable-chart', async () => {
  const { createElement } = await import('react')
  return {
    ZoomableChart: ({
      children,
      'aria-label': ariaLabel,
    }: {
      children?: ReactNode
      'aria-label': string
    }) => createElement('div', { role: 'img', 'aria-label': ariaLabel }, children),
  }
})

vi.mock('@/components/ui/tooltip', async () => {
  const { createElement } = await import('react')
  return {
    Tooltip: ({ children }: { children?: ReactNode }) => createElement('div', null, children),
    TooltipTrigger: ({ children }: { children?: ReactNode }) => children ?? null,
    TooltipContent: ({ children }: { children?: ReactNode }) =>
      createElement('div', { 'data-testid': 'metric-tooltip' }, children),
    TooltipProvider: ({ children }: { children?: ReactNode }) => children ?? null,
  }
})

vi.mock('recharts', async () => {
  const { cloneElement, createElement, isValidElement } = await import('react')
  const passthrough = ({ children }: { children?: ReactNode }) => createElement('div', null, children)
  return {
    __esModule: true,
    ResponsiveContainer: passthrough,
    LineChart: ({
      data,
      children,
    }: {
      data?: { timestamp: string }[]
      children?: ReactNode
    }) =>
      createElement(
        'div',
        { 'data-testid': 'line-chart', 'data-first-ts': data?.[0]?.timestamp ?? '' },
        children
      ),
    Line: passthrough,
    BarChart: ({
      data,
      children,
    }: {
      data?: { timestamp: string }[]
      children?: ReactNode
    }) =>
      createElement(
        'div',
        { 'data-testid': 'bar-chart', 'data-first-ts': data?.[0]?.timestamp ?? '' },
        children
      ),
    Bar: passthrough,
    PieChart: passthrough,
    Pie: ({
      data,
      label,
      children,
    }: {
      data?: { name: string }[]
      label?: (props: { name: string; percent?: number }) => ReactNode
      children?: ReactNode
    }) =>
      createElement(
        'div',
        { 'data-testid': 'pie-chart' },
        ...(data ?? []).map((entry, index) => {
          const percent = index === 0 ? 0.04 : index === 1 ? undefined : 0.2
          return createElement(
            'span',
            { key: entry.name, 'data-testid': `pie-label-${entry.name}` },
            label?.({ name: entry.name, percent })
          )
        }),
        children
      ),
    Cell: passthrough,
    XAxis: ({ tickFormatter }: { tickFormatter?: (value: string) => string }) =>
      createElement(
        'div',
        { 'data-testid': 'chart-xaxis' },
        tickFormatter ? tickFormatter(HOURLY_TS) : null
      ),
    YAxis: ({
      tickFormatter,
      yAxisId,
    }: {
      tickFormatter?: (value: number) => string
      yAxisId?: string
    }) =>
      createElement(
        'div',
        { 'data-testid': `chart-yaxis-${yAxisId ?? 'default'}` },
        tickFormatter ? tickFormatter(1_500) : null,
        tickFormatter ? createElement('span', null, tickFormatter(2_000_000)) : null
      ),
    CartesianGrid: passthrough,
    Tooltip: ({ content }: { content?: ReactNode }) =>
      createElement(
        'div',
        { 'data-testid': 'chart-tooltip' },
        isValidElement(content)
          ? cloneElement(content as ReactElement<Record<string, unknown>>, {
              active: true,
              label: HOURLY_TS,
              payload: [
                {
                  name: 'requests',
                  value: 12,
                  dataKey: 'requests',
                  payload: { fill: 'hsl(var(--color-chart-1))' },
                },
              ],
            })
          : content
      ),
    Legend: ({ content }: { content?: ReactNode }) =>
      createElement(
        'div',
        { 'data-testid': 'chart-legend' },
        isValidElement(content)
          ? cloneElement(content as ReactElement<Record<string, unknown>>, {
              payload: [
                {
                  dataKey: 'requests',
                  value: 'requests',
                  color: '#111',
                  payload: {},
                },
              ],
            })
          : content
      ),
    AreaChart: passthrough,
    Area: passthrough,
    ReferenceLine: passthrough,
  }
})

function makePoint(timestamp: string): TimeSeriesData {
  return {
    timestamp,
    online_seconds: 0,
    requests: 1,
    cost: 1,
    tokens: 1,
    input_tokens: 1,
    output_tokens: 1,
    cache_hit_tokens: 1,
    cache_miss_tokens: 1,
  }
}

function makeModel(name: string, overrides: Partial<ModelStatistics> = {}): ModelStatistics {
  return {
    model_name: name,
    request_count: 10,
    total_cost: 1.5,
    total_tokens: 100,
    input_tokens: 80,
    output_tokens: 20,
    cache_hit_tokens: 40,
    cache_miss_tokens: 40,
    cache_hit_rate: 0.5,
    avg_response_time: 1.23,
    ...overrides,
  }
}

function makeDashboardData(overrides: Partial<DashboardData> = {}): DashboardData {
  return {
    summary: {
      total_requests: 1_500_000,
      total_cost: 12.3,
      total_tokens: 2_500,
      input_tokens: 999,
      output_tokens: 1_000_000,
      cache_hit_tokens: 100,
      cache_miss_tokens: 900,
      cache_hit_rate: 0.1234,
      chat_cache_hit_tokens: 0,
      chat_cache_miss_tokens: 0,
      chat_cache_hit_rate: null,
      online_time: 7200,
      total_messages: 42,
      total_replies: 7,
      avg_response_time: 1.2,
      cost_per_hour: 1,
      tokens_per_hour: 100,
    },
    model_stats: [
      makeModel('alpha', { total_cost: 80, request_count: 1_500_000 }),
      makeModel('beta', { cache_hit_rate: null, total_cost: 10 }),
      makeModel('gamma', { total_cost: 5 }),
      makeModel('delta', { total_cost: 4 }),
      makeModel('epsilon', { total_cost: 3 }),
      makeModel('zeta', { total_cost: 2 }),
    ],
    hourly_data: [makePoint(HOURLY_TS)],
    daily_data: [makePoint(DAILY_TS)],
    recent_activity: [],
    ...overrides,
  }
}

function resetDashboardState(overrides: Partial<typeof dashboardState> = {}) {
  dashboardState.data = makeDashboardData()
  dashboardState.error = null
  dashboardState.loading = false
  dashboardState.timeRange = 24
  dashboardState.setTimeRange = vi.fn((hours: number) => {
    dashboardState.timeRange = hours
  })
  dashboardState.fetchDashboardData = vi.fn(async () => {})
  i18nState.resolvedLanguage = 'en-US'
  i18nState.language = 'zh-CN'
  Object.assign(dashboardState, overrides)
}

afterEach(() => {
  cleanup()
})

describe('统计图表坐标格式', () => {
  it('24 小时使用小时数据，7 天和 30 天使用每日数据', () => {
    const hourlyData = [makePoint('2026-07-28T01:00:00+08:00')]
    const dailyData = [makePoint('2026-07-28T00:00:00+08:00')]
    const data = { hourly_data: hourlyData, daily_data: dailyData }

    expect(selectChartTimeSeries(data, 24)).toBe(hourlyData)
    expect(selectChartTimeSeries(data, 168)).toBe(dailyData)
    expect(selectChartTimeSeries(data, 720)).toBe(dailyData)
  })

  it('24 小时横轴只显示时间，7 天和 30 天横轴只显示日期', () => {
    const timestamp = '2026-07-28T13:45:00+08:00'

    expect(formatChartTimeAxis(timestamp, 'en-US', 24)).toContain(':')
    expect(formatChartTimeAxis(timestamp, 'en-US', 24)).not.toContain('/')
    expect(formatChartTimeAxis(timestamp, 'en-US', 168)).toContain('/')
    expect(formatChartTimeAxis(timestamp, 'en-US', 720)).toContain('/')
  })

  it('Token 纵轴使用最多一位小数的 K 和 M 缩写', () => {
    expect(formatTokenAxis(999, 'en-US')).toBe('999')
    expect(formatTokenAxis(1_000, 'en-US')).toBe('1K')
    expect(formatTokenAxis(1_550, 'en-US')).toBe('1.6K')
    expect(formatTokenAxis(1_000_000, 'en-US')).toBe('1M')
    expect(formatTokenAxis(1_250_000, 'en-US')).toBe('1.3M')
  })
})

describe('StatisticsCards 卡片渲染与状态', () => {
  beforeEach(() => {
    resetDashboardState()
  })

  it('概览卡片格式化 M/K 数值、悬浮明细，并在挂载时拉取数据', async () => {
    render(createElement(StatisticsOverviewCard))

    expect(dashboardState.fetchDashboardData).toHaveBeenCalled()
    expect(document.querySelector('[data-home-titleless-content="true"]')).toBeInTheDocument()
    expect(document.querySelector('[data-home-statistics-overview="true"]')).toBeInTheDocument()
    expect(screen.getByText('1.5M')).toBeInTheDocument()
    expect(screen.getByText('¥12.30')).toBeInTheDocument()
    expect(screen.getByText('2.5K')).toBeInTheDocument()
    expect(screen.getByText('1.20s')).toBeInTheDocument()
    expect(screen.getByText('2.0h')).toBeInTheDocument()
    expect(screen.getByText('42')).toBeInTheDocument()
    expect(screen.getByText('home.stats.inputTokens 999 · home.stats.outputTokens 1M')).toBeInTheDocument()
    expect(screen.getByText('home.stats.replied:num=7')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'home.timeRange.24h' })).toHaveAttribute(
      'aria-pressed',
      'true'
    )

    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'home.timeRange.7d' }))
    expect(dashboardState.setTimeRange).toHaveBeenCalledWith(168)
    await user.click(screen.getByRole('button', { name: 'home.timeRange.30d' }))
    expect(dashboardState.setTimeRange).toHaveBeenCalledWith(720)
  })

  it('无数据时加载展示骨架，出错展示错误；有缓存数据时忽略加载/错误态', () => {
    resetDashboardState({ data: null, loading: true, error: null })
    const view = render(createElement(StatisticsOverviewCard))
    expect(document.querySelectorAll('.animate-pulse').length).toBeGreaterThan(0)
    expect(document.querySelector('[data-home-statistics-overview="true"]')).not.toBeInTheDocument()

    dashboardState.loading = false
    dashboardState.error = '仪表盘加载失败'
    view.rerender(createElement(StatisticsOverviewCard))
    expect(screen.getByText('仪表盘加载失败')).toBeInTheDocument()

    dashboardState.error = null
    view.rerender(createElement(StatisticsOverviewCard))
    expect(screen.queryByText('仪表盘加载失败')).not.toBeInTheDocument()
    expect(document.querySelectorAll('.animate-pulse')).toHaveLength(0)
    expect(document.querySelector('[data-home-statistics-overview="true"]')).not.toBeInTheDocument()

    dashboardState.data = makeDashboardData()
    dashboardState.loading = true
    dashboardState.error = '仍报错'
    view.rerender(createElement(StatisticsOverviewCard))
    expect(document.querySelector('[data-home-statistics-overview="true"]')).toBeInTheDocument()
    expect(screen.queryByText('仍报错')).not.toBeInTheDocument()
    expect(document.querySelectorAll('.animate-pulse')).toHaveLength(0)
  })

  it('缓存卡片展示命中率百分比、N/A 以及参与统计 Token', () => {
    render(createElement(PromptCacheCard))
    expect(screen.getByText('home.cache.title')).toBeInTheDocument()
    expect(screen.getByText('12.34%')).toBeInTheDocument()
    expect(screen.getByText('N/A')).toBeInTheDocument()
    expect(screen.getByText('home.cache.eligibleTokens:value=1K')).toBeInTheDocument()
    expect(screen.getByText('home.cache.eligibleTokens:value=0')).toBeInTheDocument()
    const bars = document.querySelectorAll('[data-home-cache-breakdown="true"] [style]')
    expect([...bars].some((node) => (node as HTMLElement).style.width === '12.34%')).toBe(true)
    expect([...bars].some((node) => (node as HTMLElement).style.width === '0%')).toBe(true)
    expect(document.querySelector('[data-home-titleless-content="true"]')).not.toBeInTheDocument()
  })

  it('请求/花费/Token 趋势按时间范围选择小时或每日序列，并格式化坐标', () => {
    render(createElement(RequestTrendCard))
    expect(screen.getByRole('img', { name: 'home.charts.requestTrend' })).toBeInTheDocument()
    expect(screen.getByTestId('line-chart')).toHaveAttribute('data-first-ts', HOURLY_TS)
    expect(screen.getByTestId('chart-xaxis')).toHaveTextContent(':')
    expect(screen.getByTestId('chart-tooltip')).toBeInTheDocument()

    cleanup()
    resetDashboardState({ timeRange: 168 })
    render(createElement(CostTrendCard))
    expect(screen.getByRole('img', { name: 'home.charts.costTrend' })).toBeInTheDocument()
    expect(screen.getByTestId('bar-chart')).toHaveAttribute('data-first-ts', DAILY_TS)

    cleanup()
    resetDashboardState({ timeRange: 720 })
    render(createElement(TokenTrendCard))
    expect(screen.getByRole('img', { name: 'home.charts.tokenUsage' })).toBeInTheDocument()
    expect(screen.getByTestId('bar-chart')).toHaveAttribute('data-first-ts', DAILY_TS)
    expect(screen.getByTestId('chart-yaxis-default')).toHaveTextContent('1.5K')
    expect(screen.getByTestId('chart-yaxis-default')).toHaveTextContent('2M')
  })

  it('模型花费饼图按占比隐藏小于 5% 的标签，详情卡展示 N/A 命中率', () => {
    render(createElement(ModelDistributionCard))
    expect(screen.getByText('home.charts.modelDistribution')).toBeInTheDocument()
    expect(screen.getByTestId('pie-label-alpha')).toHaveTextContent('')
    expect(screen.getByTestId('pie-label-beta')).toHaveTextContent('beta 0%')
    expect(screen.getByTestId('pie-label-gamma')).toHaveTextContent('gamma 20%')

    cleanup()
    resetDashboardState()
    render(createElement(ModelDetailsCard))
    expect(screen.getByText('alpha')).toBeInTheDocument()
    expect(screen.getByText('1.5M')).toBeInTheDocument()
    expect(screen.getByText('N/A')).toBeInTheDocument()
    expect(screen.getByText('¥80.00')).toBeInTheDocument()
    expect(screen.getAllByText('1.23s').length).toBeGreaterThan(0)
  })

  it('每日统计使用双轴柱状图；resolvedLanguage 为空时回退 language', () => {
    i18nState.resolvedLanguage = undefined
    i18nState.language = 'en-US'
    render(createElement(DailyStatisticsCard))
    expect(screen.getByRole('img', { name: 'home.charts.dailyStats' })).toBeInTheDocument()
    expect(screen.getByTestId('bar-chart')).toHaveAttribute('data-first-ts', HOURLY_TS)
    expect(screen.getByTestId('chart-yaxis-left')).toBeInTheDocument()
    expect(screen.getByTestId('chart-yaxis-right')).toBeInTheDocument()
    expect(screen.getByTestId('chart-legend')).toBeInTheDocument()
  })

  it('各卡片在 resolvedLanguage 为空时都能回退到 language 完成格式化', () => {
    i18nState.resolvedLanguage = undefined
    i18nState.language = 'en-US'
    render(createElement(StatisticsOverviewCard))
    expect(screen.getByText('1.5M')).toBeInTheDocument()

    cleanup()
    resetDashboardState()
    i18nState.resolvedLanguage = undefined
    i18nState.language = 'en-US'
    render(createElement(PromptCacheCard))
    expect(screen.getByText('12.34%')).toBeInTheDocument()

    cleanup()
    resetDashboardState()
    i18nState.resolvedLanguage = undefined
    i18nState.language = 'en-US'
    render(createElement(RequestTrendCard))
    expect(screen.getByTestId('chart-xaxis')).toHaveTextContent(':')

    cleanup()
    resetDashboardState()
    i18nState.resolvedLanguage = undefined
    i18nState.language = 'en-US'
    render(createElement(ModelDetailsCard))
    expect(screen.getByText('¥80.00')).toBeInTheDocument()
  })
})
