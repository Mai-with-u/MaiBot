import type { ComponentType, ReactNode } from 'react'
import {
  Activity,
  BarChart3,
  Clock,
  Coins,
  Database,
  DollarSign,
  Gauge,
  MessageSquare,
  Network,
  Timer,
  Zap,
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
import { ZoomableChart } from '@/components/ui/zoomable-chart'
import { cn } from '@/lib/utils'

import { useDashboardData } from './hooks/useDashboardData'
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
    <div className="flex shrink-0 items-center text-xs" aria-label={t('home.stats.timeRange')}>
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

function StatisticsCardFrame({ title, icon: Icon, state, children }: StatisticsCardFrameProps) {
  return (
    <Card className="flex h-full min-h-0 flex-col">
      <CardHeader className="flex flex-row flex-wrap items-start justify-between gap-2 pb-3">
        <div className="min-w-0">
          <CardTitle className="flex items-center gap-2 text-sm font-medium">
            <Icon className="h-4 w-4" />
            {title}
          </CardTitle>
        </div>
        <TimeRangeTextSwitch value={state.timeRange} onChange={state.setTimeRange} />
      </CardHeader>
      <CardContent className="flex min-h-0 flex-1 flex-col">
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
  icon: Icon,
  detail,
}: {
  label: string
  value: string
  icon: ComponentType<{ className?: string }>
  detail?: string
}) {
  return (
    <div className="border-border flex min-h-12 min-w-0 flex-col justify-center px-2 py-1">
      <div className="flex min-w-0 items-center gap-1.5 text-xs">
        <Icon className="text-muted-foreground h-3.5 w-3.5 shrink-0" />
        <span className="text-muted-foreground shrink-0 font-bold">{label}</span>
        <span className="text-primary ml-auto min-w-0 truncate text-right text-[15px] font-bold">
          {value}
        </span>
      </div>
      {detail && <p className="text-muted-foreground text-[11px] leading-4">{detail}</p>}
    </div>
  )
}

export function StatisticsOverviewCard() {
  const { t, i18n } = useTranslation()
  const state = useStatisticsCardData()
  const locale = i18n.resolvedLanguage || i18n.language

  return (
    <StatisticsCardFrame title={t('home.stats.overviewTitle')} icon={BarChart3} state={state}>
      {({ summary }) => (
        <div className="grid gap-y-1 lg:grid-cols-2 xl:grid-cols-3 [&>*:not(:nth-child(2n+1))]:lg:border-l [&>*:not(:nth-child(3n+1))]:xl:border-l">
          <SummaryMetric
            label={t('home.stats.totalRequests')}
            value={formatNumber(summary.total_requests, locale)}
            icon={Activity}
          />
          <SummaryMetric
            label={t('home.stats.totalCost')}
            value={`¥${summary.total_cost.toFixed(2)}`}
            detail={
              summary.cost_per_hour > 0
                ? t('home.stats.perHour', { value: `¥${summary.cost_per_hour.toFixed(2)}` })
                : t('home.stats.noData')
            }
            icon={DollarSign}
          />
          <SummaryMetric
            label={t('home.stats.tokenUsage')}
            value={formatNumber(summary.total_tokens, locale)}
            detail={`${t('home.stats.inputTokens')} ${formatNumber(summary.input_tokens, locale)} · ${t('home.stats.outputTokens')} ${formatNumber(summary.output_tokens, locale)}`}
            icon={Database}
          />
          <SummaryMetric
            label={t('home.stats.avgResponse')}
            value={`${summary.avg_response_time.toFixed(2)}s`}
            detail={t('home.stats.avgResponseDesc')}
            icon={Zap}
          />
          <SummaryMetric
            label={t('home.stats.onlineTime')}
            value={`${(summary.online_time / 3600).toFixed(1)}h`}
            icon={Clock}
          />
          <SummaryMetric
            label={t('home.stats.messageProcessing')}
            value={formatNumber(summary.total_messages, locale)}
            detail={t('home.stats.replied', { num: formatNumber(summary.total_replies, locale) })}
            icon={MessageSquare}
          />
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
    <div className="grid grid-cols-2 gap-3">
      {rates.map((rate) => (
        <div key={rate.label} className="flex min-w-0 flex-col rounded-md border p-3">
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
      {({ hourly_data: hourlyData }) => (
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
              <LineChart data={hourlyData}>
                <CartesianGrid
                  strokeDasharray="3 3"
                  stroke="hsl(var(--color-muted-foreground) / 0.2)"
                />
                <XAxis
                  dataKey="timestamp"
                  tickFormatter={(value) => formatDateTime(value, locale)}
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
              <BarChart data={hourlyData}>
                <CartesianGrid
                  strokeDasharray="3 3"
                  stroke="hsl(var(--color-muted-foreground) / 0.2)"
                />
                <XAxis
                  dataKey="timestamp"
                  tickFormatter={(value) => formatDateTime(value, locale)}
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
                <Bar dataKey="cost" fill="var(--color-cost)" />
              </BarChart>
            ) : (
              <BarChart data={hourlyData}>
                <CartesianGrid
                  strokeDasharray="3 3"
                  stroke="hsl(var(--color-muted-foreground) / 0.2)"
                />
                <XAxis
                  dataKey="timestamp"
                  tickFormatter={(value) => formatDateTime(value, locale)}
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
                <ChartLegend content={<ChartLegendContent />} />
                <Bar dataKey="input_tokens" stackId="tokens" fill="var(--color-input_tokens)" />
                <Bar dataKey="output_tokens" stackId="tokens" fill="var(--color-output_tokens)" />
              </BarChart>
            )}
          </ChartContainer>
        </ZoomableChart>
      )}
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

export function RecentActivityCard() {
  const { t, i18n } = useTranslation()
  const state = useStatisticsCardData()
  const locale = i18n.resolvedLanguage || i18n.language

  return (
    <StatisticsCardFrame title={t('home.charts.recentActivity')} icon={Zap} state={state}>
      {({ recent_activity: recentActivity }) => (
        <ScrollArea className="h-full min-h-[240px] pr-3">
          <div className="space-y-2">
            {recentActivity.map((activity) => (
              <div
                key={`${activity.timestamp}-${activity.model}-${activity.request_type}`}
                className="grid gap-1 rounded-md border p-3 text-xs sm:grid-cols-[minmax(0,1fr)_auto]"
              >
                <div className="min-w-0">
                  <div className="truncate font-semibold">{activity.model}</div>
                  <div className="text-muted-foreground truncate">{activity.request_type}</div>
                </div>
                <div className="text-muted-foreground sm:text-right">
                  <div>{formatDateTime(activity.timestamp, locale)}</div>
                  <div>
                    {t('home.stats.inputTokens')} {formatNumber(activity.input_tokens, locale)}
                    {' · '}
                    {t('home.stats.outputTokens')} {formatNumber(activity.output_tokens, locale)}
                  </div>
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
      {({ daily_data: dailyData }) => (
        <ZoomableChart aria-label={t('home.charts.dailyStats')} className="h-full min-h-[240px]">
          <ChartContainer
            config={dailyChartConfig}
            className="aspect-auto h-full min-h-[240px] w-full"
          >
            <BarChart data={dailyData}>
              <CartesianGrid
                strokeDasharray="3 3"
                stroke="hsl(var(--color-muted-foreground) / 0.2)"
              />
              <XAxis
                dataKey="timestamp"
                tickFormatter={(value) =>
                  new Date(value).toLocaleDateString(locale, {
                    month: 'numeric',
                    day: 'numeric',
                  })
                }
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
                    labelFormatter={(value) => new Date(value as string).toLocaleDateString(locale)}
                  />
                }
              />
              <ChartLegend content={<ChartLegendContent />} />
              <Bar yAxisId="left" dataKey="requests" fill="var(--color-requests)" />
              <Bar yAxisId="right" dataKey="cost" fill="var(--color-cost)" />
            </BarChart>
          </ChartContainer>
        </ZoomableChart>
      )}
    </StatisticsCardFrame>
  )
}
