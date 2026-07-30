import type { ComponentType, ReactNode } from 'react'
import {
  Activity,
  BarChart3,
  Coins,
  Database,
  Gauge,
  Network,
  Timer,
} from 'lucide-react'
import { useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  Pie,
  PieChart,
  XAxis,
  YAxis,
} from 'recharts'

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import {
  ChartContainer,
  ChartLegend,
  ChartLegendContent,
  ChartTooltip,
  ChartTooltipContent,
  type ChartConfig,
} from '@/components/ui/chart'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Skeleton } from '@/components/ui/skeleton'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { ZoomableChart } from '@/components/ui/zoomable-chart'
import { cn } from '@/lib/utils'

import { useDashboardData } from './hooks/useDashboardData'
import {
  formatChartTimeAxis,
  formatTokenAxis,
  selectChartTimeSeries,
} from './statistics-chart-utils'
import type { DashboardData, StatisticsSummary } from './types'

const TIME_RANGES = [24, 168, 720] as const

const PIE_COLORS = [
  'hsl(var(--color-chart-1))',
  'hsl(var(--color-chart-2))',
  'hsl(var(--color-chart-3))',
  'hsl(var(--color-chart-4))',
  'hsl(var(--color-chart-5))',
]

const requestChartConfig = {
  requests: {
    label: '请求数',
    color: 'hsl(var(--color-chart-1))',
  },
} satisfies ChartConfig

const costChartConfig = {
  cost: {
    label: '花费',
    color: 'hsl(var(--color-chart-2))',
  },
} satisfies ChartConfig

const tokenChartConfig = {
  input_tokens: {
    label: '输入 Token',
    color: 'hsl(var(--color-chart-1))',
  },
  output_tokens: {
    label: '输出 Token',
    color: 'hsl(var(--color-chart-3))',
  },
} satisfies ChartConfig

const dailyChartConfig = {
  requests: {
    label: '请求数',
    color: 'hsl(var(--color-chart-1))',
  },
  cost: {
    label: '花费',
    color: 'hsl(var(--color-chart-2))',
  },
} satisfies ChartConfig

interface StatisticsCardData {
  data: DashboardData | null
  error: string | null
  loading: boolean
  timeRange: number
  setTimeRange: (hours: number) => void
}

interface StatisticsCardFrameProps {
  title: string
  icon: ComponentType<{ className?: string }>
  state: StatisticsCardData
  titleless?: boolean
  children: (data: DashboardData) => ReactNode
}

function useStatisticsCardData(): StatisticsCardData {
  const { dashboardData, error, loading, timeRange, setTimeRange, fetchDashboardData } =
    useDashboardData()

  useEffect(() => {
    void fetchDashboardData()
  }, [fetchDashboardData])

  return {
    data: dashboardData,
    error,
    loading,
    timeRange,
    setTimeRange,
  }
}

function TimeRangeTextSwitch({
  value,
  onChange,
}: {
  value: number
  onChange: (hours: number) => void
}) {
  const { t } = useTranslation()
  const labels: Record<number, string> = {
    24: t('home.timeRange.24h'),
    168: t('home.timeRange.7d'),
    720: t('home.timeRange.30d'),
  }

  return (
    <div
      data-home-time-range="true"
      className="flex shrink-0 items-center text-xs"
      aria-label={t('home.stats.timeRange')}
    >
      {TIME_RANGES.map((hours, index) => (
        <span key={hours} className="inline-flex items-center">
          {index > 0 && (
            <span className="text-border px-2" aria-hidden="true">
              |
            </span>
          )}
          <button
            type="button"
            aria-pressed={value === hours}
            className={cn(
              'text-muted-foreground hover:text-foreground whitespace-nowrap transition-colors',
              value === hours && 'text-primary font-semibold'
            )}
            onClick={() => onChange(hours)}
          >
            {labels[hours]}
          </button>
        </span>
      ))}
    </div>
  )
}

function StatisticsCardFrame({
  title,
  icon: Icon,
  state,
  titleless = false,
  children,
}: StatisticsCardFrameProps) {
  return (
    <Card className="relative flex h-full min-h-0 flex-col">
      {!titleless && (
        <CardHeader className="flex flex-row flex-wrap items-start justify-between gap-2 pb-3">
          <div className="min-w-0">
            <CardTitle className="flex items-center gap-2 text-sm font-medium">
              <Icon className="h-4 w-4" />
              {title}
            </CardTitle>
          </div>
          <TimeRangeTextSwitch value={state.timeRange} onChange={state.setTimeRange} />
        </CardHeader>
      )}
      <CardContent
        data-home-titleless-content={titleless ? 'true' : undefined}
        className={cn(
          'flex min-h-0 flex-1 flex-col',
          titleless && 'relative pt-4 sm:pt-5'
        )}
      >
        {titleless && (
          <div className="absolute top-3 right-4 z-10">
            <TimeRangeTextSwitch value={state.timeRange} onChange={state.setTimeRange} />
          </div>
        )}
        {state.loading && !state.data ? (
          <div className="space-y-3">
            <Skeleton className="h-24 w-full" />
            <Skeleton className="h-4 w-2/3" />
          </div>
        ) : state.error && !state.data ? (
          <div className="text-destructive flex h-24 items-center justify-center text-sm">
            {state.error}
          </div>
        ) : state.data ? (
          children(state.data)
        ) : null}
      </CardContent>
    </Card>
  )
}

function formatNumber(value: number, locale: string): string {
  return new Intl.NumberFormat(locale, {
    notation: value >= 10_000 ? 'compact' : 'standard',
    maximumFractionDigits: 2,
  }).format(value)
}

function formatDateTime(value: string, locale: string): string {
  return new Date(value).toLocaleString(locale, {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function formatCacheRate(value: number | null): string {
  return value === null ? 'N/A' : `${(value * 100).toFixed(2)}%`
}

function SummaryMetric({
  label,
  value,
  detail,
  detailOnHover = false,
}: {
  label: string
  value: string
  detail?: string
  detailOnHover?: boolean
}) {
  const metricLine = (
    <div className="flex min-w-0 items-baseline justify-center gap-2.5 text-base leading-none">
      <span className="text-muted-foreground min-w-0 truncate font-bold">{label}</span>
      <span className="text-primary shrink-0 text-xl leading-none font-black">
        {value}
      </span>
    </div>
  )

  return (
    <div
      data-home-summary-metric="true"
      className="border-border flex min-h-10 min-w-0 flex-col justify-center px-2 py-0.5"
    >
      {detail && detailOnHover ? (
        <Tooltip>
          <TooltipTrigger asChild>{metricLine}</TooltipTrigger>
          <TooltipContent sideOffset={1} className="px-2 py-1 leading-none">
            {detail}
          </TooltipContent>
        </Tooltip>
      ) : (
        metricLine
      )}
      {detail && !detailOnHover && (
        <p data-home-metric-detail="true" className="text-muted-foreground text-[11px] leading-4">
          {detail}
        </p>
      )}
    </div>
  )
}

function PrimarySummaryMetric({
  label,
  value,
}: {
  label: string
  value: string
}) {
  return (
    <div
      data-home-summary-primary="true"
      className="flex min-h-0 min-w-0 flex-col justify-center px-3 py-1"
    >
      <div className="text-muted-foreground truncate text-xs font-bold">{label}</div>
      <div className="text-primary mt-1 truncate text-3xl leading-none font-black tracking-[-0.04em] tabular-nums">
        {value}
      </div>
    </div>
  )
}

export function StatisticsOverviewCard() {
  const { t, i18n } = useTranslation()
  const state = useStatisticsCardData()
  const locale = i18n.resolvedLanguage || i18n.language

  return (
    <StatisticsCardFrame
      title={t('home.stats.overviewTitle')}
      icon={BarChart3}
      state={state}
      titleless
    >
      {({ summary }) => (
        <div
          data-home-statistics-overview="true"
          className="grid min-h-0 flex-1 grid-rows-2"
        >
          <div
            data-home-summary-primary-group="true"
            className="divide-border grid min-h-0 w-full grid-cols-2 divide-x pr-28 sm:w-3/5"
          >
            <PrimarySummaryMetric
              label={t('home.stats.totalRequests')}
              value={formatNumber(summary.total_requests, locale)}
            />
            <PrimarySummaryMetric
              label={t('home.stats.totalCost')}
              value={`¥${summary.total_cost.toFixed(2)}`}
            />
          </div>
          <div
            data-home-summary-secondary="true"
            className="border-border grid min-h-0 grid-cols-4 border-t pt-1"
          >
            <SummaryMetric
              label={t('home.stats.tokenUsage')}
              value={formatNumber(summary.total_tokens, locale)}
              detail={`${t('home.stats.inputTokens')} ${formatNumber(summary.input_tokens, locale)} · ${t('home.stats.outputTokens')} ${formatNumber(summary.output_tokens, locale)}`}
              detailOnHover
            />
            <SummaryMetric
              label={t('home.stats.avgResponse')}
              value={`${summary.avg_response_time.toFixed(2)}s`}
            />
            <SummaryMetric
              label={t('home.stats.onlineTime')}
              value={`${(summary.online_time / 3600).toFixed(1)}h`}
            />
            <SummaryMetric
              label={t('home.stats.messageProcessing')}
              value={formatNumber(summary.total_messages, locale)}
              detail={t('home.stats.replied', { num: formatNumber(summary.total_replies, locale) })}
              detailOnHover
            />
          </div>
        </div>
      )}
    </StatisticsCardFrame>
  )
}

function CacheBreakdown({ summary, locale }: { summary: StatisticsSummary; locale: string }) {
  const { t } = useTranslation()
  const rates = [
    {
      label: t('home.cache.all'),
      hitRate: summary.cache_hit_rate,
      total: summary.cache_hit_tokens + summary.cache_miss_tokens,
    },
    {
      label: t('home.cache.chat'),
      hitRate: summary.chat_cache_hit_rate,
      total: summary.chat_cache_hit_tokens + summary.chat_cache_miss_tokens,
    },
  ]

  return (
    <div data-home-cache-breakdown="true" className="divide-border grid grid-cols-2 divide-x">
      {rates.map((rate) => (
        <div key={rate.label} className="flex min-w-0 flex-col px-3 py-1">
          <div className="text-muted-foreground text-xs font-medium">{rate.label}</div>
          <div className="text-primary mt-1 text-2xl font-bold tracking-tight">
            {formatCacheRate(rate.hitRate)}
          </div>
          <div className="bg-muted mt-3 h-1.5 overflow-hidden rounded-full">
            <div
              className="bg-primary h-full transition-[width]"
              style={{
                width: `${rate.hitRate === null ? 0 : rate.hitRate * 100}%`,
              }}
            />
          </div>
          <div className="text-muted-foreground mt-2 truncate text-[11px]">
            {t('home.cache.eligibleTokens', { value: formatNumber(rate.total, locale) })}
          </div>
        </div>
      ))}
    </div>
  )
}

export function PromptCacheCard() {
  const { t, i18n } = useTranslation()
  const state = useStatisticsCardData()
  const locale = i18n.resolvedLanguage || i18n.language

  return (
    <StatisticsCardFrame title={t('home.cache.title')} icon={Gauge} state={state}>
      {({ summary }) => <CacheBreakdown summary={summary} locale={locale} />}
    </StatisticsCardFrame>
  )
}

function ChartCard({ kind }: { kind: 'requests' | 'cost' | 'tokens' }) {
  const { t, i18n } = useTranslation()
  const state = useStatisticsCardData()
  const locale = i18n.resolvedLanguage || i18n.language
  const metadata = {
    requests: {
      title: t('home.charts.requestTrend'),
      icon: Activity,
    },
    cost: {
      title: t('home.charts.costTrend'),
      icon: Coins,
    },
    tokens: {
      title: t('home.charts.tokenUsage'),
      icon: Database,
    },
  }[kind]

  return (
    <StatisticsCardFrame {...metadata} state={state}>
      {(data) => {
        const chartData = selectChartTimeSeries(data, state.timeRange)
        const chartGuide = (
          <ChartLegend
            verticalAlign="top"
            height={28}
            content={
              <ChartLegendContent
                data-home-chart-guide="true"
                verticalAlign="top"
                className="justify-start px-2 pb-2 text-[11px]"
              />
            }
          />
        )
        return (
          <ZoomableChart aria-label={metadata.title} className="h-full min-h-[240px]">
            <ChartContainer
              config={
                kind === 'requests'
                  ? requestChartConfig
                  : kind === 'cost'
                    ? costChartConfig
                    : tokenChartConfig
              }
              className="aspect-auto h-full min-h-[240px] w-full"
            >
              {kind === 'requests' ? (
                <LineChart data={chartData}>
                  <CartesianGrid
                    strokeDasharray="3 3"
                    stroke="hsl(var(--color-muted-foreground) / 0.2)"
                  />
                  <XAxis
                    dataKey="timestamp"
                    tickFormatter={(value) => formatChartTimeAxis(value, locale, state.timeRange)}
                    angle={-45}
                    textAnchor="end"
                    height={60}
                    stroke="hsl(var(--color-muted-foreground))"
                    tick={{ fill: 'hsl(var(--color-muted-foreground))' }}
                  />
                  <YAxis
                    stroke="hsl(var(--color-muted-foreground))"
                    tick={{ fill: 'hsl(var(--color-muted-foreground))' }}
                  />
                  <ChartTooltip
                    content={
                      <ChartTooltipContent
                        labelFormatter={(value) => formatDateTime(value as string, locale)}
                      />
                    }
                  />
                  {chartGuide}
                  <Line
                    type="monotone"
                    dataKey="requests"
                    stroke="var(--color-requests)"
                    strokeWidth={2}
                    dot={false}
                    isAnimationActive={false}
                  />
                </LineChart>
              ) : kind === 'cost' ? (
                <BarChart data={chartData}>
                  <CartesianGrid
                    strokeDasharray="3 3"
                    stroke="hsl(var(--color-muted-foreground) / 0.2)"
                  />
                  <XAxis
                    dataKey="timestamp"
                    tickFormatter={(value) => formatChartTimeAxis(value, locale, state.timeRange)}
                    angle={-45}
                    textAnchor="end"
                    height={60}
                    stroke="hsl(var(--color-muted-foreground))"
                    tick={{ fill: 'hsl(var(--color-muted-foreground))' }}
                  />
                  <YAxis
                    stroke="hsl(var(--color-muted-foreground))"
                    tick={{ fill: 'hsl(var(--color-muted-foreground))' }}
                  />
                  <ChartTooltip
                    content={
                      <ChartTooltipContent
                        labelFormatter={(value) => formatDateTime(value as string, locale)}
                      />
                    }
                  />
                  {chartGuide}
                  <Bar dataKey="cost" fill="var(--color-cost)" />
                </BarChart>
              ) : (
                <BarChart data={chartData}>
                  <CartesianGrid
                    strokeDasharray="3 3"
                    stroke="hsl(var(--color-muted-foreground) / 0.2)"
                  />
                  <XAxis
                    dataKey="timestamp"
                    tickFormatter={(value) => formatChartTimeAxis(value, locale, state.timeRange)}
                    angle={-45}
                    textAnchor="end"
                    height={60}
                    stroke="hsl(var(--color-muted-foreground))"
                    tick={{ fill: 'hsl(var(--color-muted-foreground))' }}
                  />
                  <YAxis
                    tickFormatter={(value) => formatTokenAxis(Number(value), locale)}
                    stroke="hsl(var(--color-muted-foreground))"
                    tick={{ fill: 'hsl(var(--color-muted-foreground))' }}
                  />
                  <ChartTooltip
                    content={
                      <ChartTooltipContent
                        labelFormatter={(value) => formatDateTime(value as string, locale)}
                      />
                    }
                  />
                  {chartGuide}
                  <Bar dataKey="input_tokens" stackId="tokens" fill="var(--color-input_tokens)" />
                  <Bar dataKey="output_tokens" stackId="tokens" fill="var(--color-output_tokens)" />
                </BarChart>
              )}
            </ChartContainer>
          </ZoomableChart>
        )
      }}
    </StatisticsCardFrame>
  )
}

export function RequestTrendCard() {
  return <ChartCard kind="requests" />
}

export function CostTrendCard() {
  return <ChartCard kind="cost" />
}

export function TokenTrendCard() {
  return <ChartCard kind="tokens" />
}

export function ModelDistributionCard() {
  const { t } = useTranslation()
  const state = useStatisticsCardData()

  return (
    <StatisticsCardFrame title={t('home.charts.modelDistribution')} icon={Network} state={state}>
      {({ model_stats: modelStats }) => {
        const data = modelStats.map((item, index) => ({
          name: item.model_name,
          value: item.total_cost,
          fill: PIE_COLORS[index % PIE_COLORS.length],
        }))
        const config = Object.fromEntries(
          data.map((entry) => [
            entry.name,
            {
              label: entry.name,
              color: entry.fill,
            },
          ])
        ) as ChartConfig
        return (
          <ChartContainer config={config} className="aspect-auto h-full min-h-[240px] w-full">
            <PieChart>
              <ChartTooltip content={<ChartTooltipContent />} />
              <Pie
                data={data}
                dataKey="value"
                nameKey="name"
                cx="50%"
                cy="50%"
                labelLine={false}
                label={({ name, percent }) => {
                  if (percent && percent < 0.05) return ''
                  return `${name} ${percent ? (percent * 100).toFixed(0) : 0}%`
                }}
                outerRadius={92}
              >
                {data.map((entry) => (
                  <Cell key={entry.name} fill={entry.fill} />
                ))}
              </Pie>
            </PieChart>
          </ChartContainer>
        )
      }}
    </StatisticsCardFrame>
  )
}

export function ModelDetailsCard() {
  const { t, i18n } = useTranslation()
  const state = useStatisticsCardData()
  const locale = i18n.resolvedLanguage || i18n.language

  return (
    <StatisticsCardFrame title={t('home.charts.modelDetails')} icon={Timer} state={state}>
      {({ model_stats: modelStats }) => (
        <ScrollArea className="h-full min-h-[240px] pr-3">
          <div className="space-y-2">
            {modelStats.map((stat) => (
              <div key={stat.model_name} className="rounded-md border p-3 text-xs">
                <div className="mb-2 truncate text-sm font-semibold">{stat.model_name}</div>
                <div className="grid grid-cols-2 gap-x-3 gap-y-1 sm:grid-cols-3">
                  <span>
                    {t('home.charts.requestCount')}:{' '}
                    <strong>{formatNumber(stat.request_count, locale)}</strong>
                  </span>
                  <span>
                    {t('home.stats.inputTokens')}:{' '}
                    <strong>{formatNumber(stat.input_tokens, locale)}</strong>
                  </span>
                  <span>
                    {t('home.stats.outputTokens')}:{' '}
                    <strong>{formatNumber(stat.output_tokens, locale)}</strong>
                  </span>
                  <span>
                    {t('home.cache.hitRate')}:{' '}
                    <strong>{formatCacheRate(stat.cache_hit_rate)}</strong>
                  </span>
                  <span>
                    {t('home.charts.costLabel')}: <strong>¥{stat.total_cost.toFixed(2)}</strong>
                  </span>
                  <span>
                    {t('home.charts.avgTime')}:{' '}
                    <strong>{stat.avg_response_time.toFixed(2)}s</strong>
                  </span>
                </div>
              </div>
            ))}
          </div>
        </ScrollArea>
      )}
    </StatisticsCardFrame>
  )
}

export function DailyStatisticsCard() {
  const { t, i18n } = useTranslation()
  const state = useStatisticsCardData()
  const locale = i18n.resolvedLanguage || i18n.language

  return (
    <StatisticsCardFrame title={t('home.charts.dailyStats')} icon={BarChart3} state={state}>
      {(data) => {
        const chartData = selectChartTimeSeries(data, state.timeRange)
        return (
          <ZoomableChart aria-label={t('home.charts.dailyStats')} className="h-full min-h-[240px]">
            <ChartContainer
              config={dailyChartConfig}
              className="aspect-auto h-full min-h-[240px] w-full"
            >
              <BarChart data={chartData}>
                <CartesianGrid
                  strokeDasharray="3 3"
                  stroke="hsl(var(--color-muted-foreground) / 0.2)"
                />
                <XAxis
                  dataKey="timestamp"
                  tickFormatter={(value) => formatChartTimeAxis(value, locale, state.timeRange)}
                  stroke="hsl(var(--color-muted-foreground))"
                  tick={{ fill: 'hsl(var(--color-muted-foreground))' }}
                />
                <YAxis
                  yAxisId="left"
                  stroke="hsl(var(--color-muted-foreground))"
                  tick={{ fill: 'hsl(var(--color-muted-foreground))' }}
                />
                <YAxis
                  yAxisId="right"
                  orientation="right"
                  stroke="hsl(var(--color-muted-foreground))"
                  tick={{ fill: 'hsl(var(--color-muted-foreground))' }}
                />
                <ChartTooltip
                  content={
                    <ChartTooltipContent
                      labelFormatter={(value) => formatDateTime(value as string, locale)}
                    />
                  }
                />
                <ChartLegend content={<ChartLegendContent />} />
                <Bar yAxisId="left" dataKey="requests" fill="var(--color-requests)" />
                <Bar yAxisId="right" dataKey="cost" fill="var(--color-cost)" />
              </BarChart>
            </ChartContainer>
          </ZoomableChart>
        )
      }}
    </StatisticsCardFrame>
  )
}
