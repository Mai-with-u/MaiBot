import { useMemo, useState } from 'react'
import { AlertCircle, CalendarClock, ExternalLink, RefreshCw } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { CartesianGrid, Cell, Line, LineChart, Pie, PieChart, XAxis, YAxis } from 'recharts'

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import {
  ChartContainer,
  ChartTooltip,
  ChartTooltipContent,
  type ChartConfig,
} from '@/components/ui/chart'
import { DashboardTabBar, DashboardTabTrigger } from '@/components/ui/dashboard-tabs'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { cn } from '@/lib/utils'

import type {
  DetailedDistributionItem,
  DetailedStatisticsBreakdown,
  DetailedStatisticsData,
  DetailedStatisticsMetricsData,
  DetailedStatisticsPeriod,
  DetailedStatisticsTrendData,
} from './types'
import { useDetailedStatistics } from './useDetailedStatistics'

const PERIOD_KEYS = [
  'all_time',
  'last_30_days',
  'last_7_days',
  'last_3_days',
  'last_24_hours',
  'last_3_hours',
  'last_hour',
  'last_15_minutes',
] as const

const CHART_COLORS = [
  'hsl(var(--color-chart-1))',
  'hsl(var(--color-chart-2))',
  'hsl(var(--color-chart-3))',
  'hsl(var(--color-chart-4))',
  'hsl(var(--color-chart-5))',
]

function formatNumber(value: number, locale: string, maximumFractionDigits = 2): string {
  return new Intl.NumberFormat(locale, {
    notation: Math.abs(value) >= 10_000 ? 'compact' : 'standard',
    maximumFractionDigits,
  }).format(value)
}

function formatCurrency(value: number, locale: string, digits = 2): string {
  return `¥${new Intl.NumberFormat(locale, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(value)}`
}

function formatPercent(value: number | null): string {
  return value === null ? 'N/A' : `${(value * 100).toFixed(2)}%`
}

function formatOptionalNumber(value: number | null, locale: string): string {
  return value === null ? 'N/A' : formatNumber(value, locale)
}

function formatDuration(seconds: number, locale: string): string {
  const totalSeconds = Math.max(0, Math.floor(seconds))
  const days = Math.floor(totalSeconds / 86400)
  const hours = Math.floor((totalSeconds % 86400) / 3600)
  const minutes = Math.floor((totalSeconds % 3600) / 60)
  const remainingSeconds = totalSeconds % 60
  const parts: string[] = []
  const formatUnit = (value: number, unit: 'day' | 'hour' | 'minute' | 'second') =>
    new Intl.NumberFormat(locale, {
      style: 'unit',
      unit,
      unitDisplay: 'narrow',
      maximumFractionDigits: 0,
    }).format(value)

  if (days > 0) parts.push(formatUnit(days, 'day'))
  if (hours > 0 || days > 0) parts.push(formatUnit(hours, 'hour'))
  if (minutes > 0 || hours > 0 || days > 0) parts.push(formatUnit(minutes, 'minute'))
  if (parts.length === 0) parts.push(formatUnit(remainingSeconds, 'second'))
  return parts.join(' ')
}

function formatDateTime(value: string, locale: string): string {
  return new Date(value).toLocaleString(locale, {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
}

function StatisticsPageSkeleton() {
  return (
    <div className="space-y-5 rounded-xl border p-4 sm:p-5" aria-hidden="true">
      <Skeleton className="h-11 w-full" />
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4 2xl:grid-cols-6">
        {Array.from({ length: 16 }, (_, index) => (
          <Skeleton key={index} className="h-28" />
        ))}
      </div>
      <Skeleton className="h-[360px] w-full" />
    </div>
  )
}

interface MetricItemProps {
  label: string
  value: string
  detail?: string
}

function MetricItem({ label, value, detail }: MetricItemProps) {
  return (
    <div className="group bg-card hover:border-primary/60 relative min-h-28 min-w-0 overflow-hidden rounded-lg border px-4 py-4 shadow-sm transition-[border-color,box-shadow,transform] duration-200 hover:-translate-y-0.5 hover:shadow-md">
      <div className="bg-primary/75 absolute inset-x-0 top-0 h-0.5" />
      <div className="text-muted-foreground truncate text-xs font-medium tracking-wide">
        {label}
      </div>
      <div className="text-primary mt-2 truncate text-xl font-bold tracking-tight tabular-nums">
        {value}
      </div>
      {detail && <div className="text-muted-foreground mt-2 truncate text-[11px]">{detail}</div>}
    </div>
  )
}

function SummaryMetrics({ period, locale }: { period: DetailedStatisticsPeriod; locale: string }) {
  const { t } = useTranslation()
  const summary = period.summary
  const items: MetricItemProps[] = [
    {
      label: t('statisticsPage.summary.onlineTime'),
      value: formatDuration(summary.online_time, locale),
    },
    {
      label: t('statisticsPage.summary.messages'),
      value: formatNumber(summary.total_messages, locale),
    },
    {
      label: t('statisticsPage.summary.replies'),
      value: formatNumber(summary.total_replies, locale),
    },
    {
      label: t('statisticsPage.summary.requests'),
      value: formatNumber(summary.total_requests, locale),
    },
    {
      label: t('statisticsPage.summary.tokens'),
      value: formatNumber(summary.total_tokens, locale),
    },
    {
      label: t('statisticsPage.summary.inputTokens'),
      value: formatNumber(summary.input_tokens, locale),
    },
    {
      label: t('statisticsPage.summary.outputTokens'),
      value: formatNumber(summary.output_tokens, locale),
    },
    {
      label: t('statisticsPage.summary.cacheHitRate'),
      value: formatPercent(summary.cache_hit_rate),
    },
    {
      label: t('statisticsPage.summary.cacheHitTokens'),
      value: formatNumber(summary.cache_hit_tokens, locale),
    },
    {
      label: t('statisticsPage.summary.cacheMissTokens'),
      value: formatNumber(summary.cache_miss_tokens, locale),
    },
    {
      label: t('statisticsPage.summary.totalCost'),
      value: formatCurrency(summary.total_cost, locale),
    },
    {
      label: t('statisticsPage.summary.costPerMessages'),
      value: formatCurrency(summary.cost_per_100_messages, locale, 4),
      detail: t('statisticsPage.units.per100'),
    },
    {
      label: t('statisticsPage.summary.costPerReceivedMessages'),
      value: formatCurrency(summary.cost_per_100_messages_excluding_replies, locale, 4),
      detail: t('statisticsPage.units.per100'),
    },
    {
      label: t('statisticsPage.summary.costPerReplies'),
      value: formatCurrency(summary.cost_per_100_replies, locale, 4),
      detail: t('statisticsPage.units.per100'),
    },
    {
      label: t('statisticsPage.summary.costPerHour'),
      value: formatCurrency(summary.cost_per_hour, locale),
      detail: t('statisticsPage.units.perHour'),
    },
    {
      label: t('statisticsPage.summary.tokensPerHour'),
      value: formatNumber(summary.tokens_per_hour, locale),
      detail: t('statisticsPage.units.perHour'),
    },
  ]

  return (
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4 2xl:grid-cols-6">
      {items.map((item) => (
        <MetricItem key={item.label} {...item} />
      ))}
    </div>
  )
}

function BreakdownTable({ rows, locale }: { rows: DetailedStatisticsBreakdown[]; locale: string }) {
  const { t } = useTranslation()

  if (rows.length === 0) {
    return <EmptyState />
  }

  return (
    <div className="overflow-hidden rounded-md border">
      <Table className="min-w-[1520px]">
        <TableHeader className="bg-muted/60">
          <TableRow>
            <TableHead className="bg-muted sticky left-0 z-10 min-w-52 font-semibold">
              {t('statisticsPage.table.name')}
            </TableHead>
            <TableHead>{t('statisticsPage.table.requests')}</TableHead>
            <TableHead>{t('statisticsPage.table.inputTokens')}</TableHead>
            <TableHead>{t('statisticsPage.table.outputTokens')}</TableHead>
            <TableHead>{t('statisticsPage.table.totalTokens')}</TableHead>
            <TableHead>{t('statisticsPage.table.cacheHitTokens')}</TableHead>
            <TableHead>{t('statisticsPage.table.cacheMissTokens')}</TableHead>
            <TableHead>{t('statisticsPage.table.cacheHitRate')}</TableHead>
            <TableHead>{t('statisticsPage.table.cost')}</TableHead>
            <TableHead>{t('statisticsPage.table.avgTime')}</TableHead>
            <TableHead>{t('statisticsPage.table.stdTime')}</TableHead>
            <TableHead>{t('statisticsPage.table.callsPerReply')}</TableHead>
            <TableHead>{t('statisticsPage.table.tokensPerReply')}</TableHead>
            <TableHead>{t('statisticsPage.table.tokensPerCall')}</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((row) => (
            <TableRow key={row.name}>
              <TableCell
                className="bg-card sticky left-0 z-10 max-w-64 truncate font-medium"
                title={row.name}
              >
                {row.name}
              </TableCell>
              <TableCell>{formatNumber(row.request_count, locale)}</TableCell>
              <TableCell>{formatNumber(row.input_tokens, locale)}</TableCell>
              <TableCell>{formatNumber(row.output_tokens, locale)}</TableCell>
              <TableCell>{formatNumber(row.total_tokens, locale)}</TableCell>
              <TableCell>{formatNumber(row.cache_hit_tokens, locale)}</TableCell>
              <TableCell>{formatNumber(row.cache_miss_tokens, locale)}</TableCell>
              <TableCell>{formatPercent(row.cache_hit_rate)}</TableCell>
              <TableCell>{formatCurrency(row.total_cost, locale)}</TableCell>
              <TableCell>{row.avg_time_cost.toFixed(1)}s</TableCell>
              <TableCell>{row.std_time_cost.toFixed(1)}s</TableCell>
              <TableCell>{formatOptionalNumber(row.avg_calls_per_reply, locale)}</TableCell>
              <TableCell>{formatOptionalNumber(row.avg_tokens_per_reply, locale)}</TableCell>
              <TableCell>{formatOptionalNumber(row.avg_tokens_per_call, locale)}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  )
}

function ChatTable({ period, locale }: { period: DetailedStatisticsPeriod; locale: string }) {
  const { t } = useTranslation()
  if (period.chats.length === 0) {
    return <EmptyState />
  }
  return (
    <div className="overflow-hidden rounded-md border">
      <Table>
        <TableHeader className="bg-muted/60">
          <TableRow>
            <TableHead>{t('statisticsPage.table.chat')}</TableHead>
            <TableHead className="w-44 text-right">{t('statisticsPage.table.messages')}</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {period.chats.map((chat) => (
            <TableRow key={chat.name}>
              <TableCell className="font-medium">{chat.name}</TableCell>
              <TableCell className="text-right tabular-nums">
                {formatNumber(chat.message_count, locale)}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  )
}

function EmptyState() {
  const { t } = useTranslation()
  return (
    <div className="text-muted-foreground flex min-h-40 items-center justify-center text-sm">
      {t('statisticsPage.empty')}
    </div>
  )
}

function DetailTables({ period, locale }: { period: DetailedStatisticsPeriod; locale: string }) {
  const { t } = useTranslation()
  return (
    <Card className="border-l-primary overflow-hidden border-l-4 shadow-sm">
      <CardHeader className="border-b pb-4">
        <CardTitle className="text-lg">{t('statisticsPage.sections.breakdowns')}</CardTitle>
      </CardHeader>
      <CardContent className="pt-4 sm:pt-4">
        <Tabs defaultValue="models">
          <TabsList className="border-border/70 bg-muted/40 mb-4 h-auto w-full justify-start overflow-x-auto rounded-md border p-1">
            <TabsTrigger value="models">{t('statisticsPage.breakdowns.models')}</TabsTrigger>
            <TabsTrigger value="modules">{t('statisticsPage.breakdowns.modules')}</TabsTrigger>
            <TabsTrigger value="requestTypes">
              {t('statisticsPage.breakdowns.requestTypes')}
            </TabsTrigger>
            <TabsTrigger value="chats">{t('statisticsPage.breakdowns.chats')}</TabsTrigger>
          </TabsList>
          <TabsContent value="models" className="mt-0">
            <BreakdownTable rows={period.models} locale={locale} />
          </TabsContent>
          <TabsContent value="modules" className="mt-0">
            <BreakdownTable rows={period.modules} locale={locale} />
          </TabsContent>
          <TabsContent value="requestTypes" className="mt-0">
            <BreakdownTable rows={period.request_types} locale={locale} />
          </TabsContent>
          <TabsContent value="chats" className="mt-0">
            <ChatTable period={period} locale={locale} />
          </TabsContent>
        </Tabs>
      </CardContent>
    </Card>
  )
}

function DistributionChart({
  title,
  data,
  valueKind,
  locale,
}: {
  title: string
  data: DetailedDistributionItem[]
  valueKind: 'currency' | 'count'
  locale: string
}) {
  const { t } = useTranslation()
  const [hiddenNames, setHiddenNames] = useState<Set<string>>(() => new Set())
  const chartData = data.map((item, index) => ({
    ...item,
    fill: CHART_COLORS[index % CHART_COLORS.length],
  }))
  const visibleData = chartData.filter((item) => !hiddenNames.has(item.name))
  const config = Object.fromEntries(
    chartData.map((item, index) => [`item_${index}`, { label: item.name, color: item.fill }])
  ) as ChartConfig

  const toggleName = (name: string) => {
    setHiddenNames((current) => {
      const next = new Set(current)
      if (next.has(name)) next.delete(name)
      else next.add(name)
      return next
    })
  }

  return (
    <Card className="min-w-0 overflow-hidden shadow-sm">
      <CardHeader className="border-b pb-3">
        <CardTitle className="truncate text-sm" title={title}>
          {title}
        </CardTitle>
      </CardHeader>
      <CardContent className="pt-4 sm:pt-4">
        {data.length === 0 ? (
          <EmptyState />
        ) : (
          <>
            <ChartContainer config={config} className="aspect-auto h-[280px] w-full min-w-0">
              <PieChart>
                <ChartTooltip
                  content={
                    <ChartTooltipContent
                      formatter={(value) =>
                        valueKind === 'currency'
                          ? formatCurrency(Number(value), locale)
                          : formatNumber(Number(value), locale)
                      }
                    />
                  }
                />
                <Pie
                  data={visibleData}
                  dataKey="value"
                  nameKey="name"
                  innerRadius={54}
                  outerRadius={96}
                  paddingAngle={1}
                  isAnimationActive={false}
                >
                  {visibleData.map((item) => (
                    <Cell key={item.name} fill={item.fill} />
                  ))}
                </Pie>
              </PieChart>
            </ChartContainer>
            <div className="mt-2 grid max-h-28 grid-cols-1 gap-1 overflow-y-auto sm:grid-cols-2">
              {chartData.map((item) => {
                const hidden = hiddenNames.has(item.name)
                return (
                  <button
                    key={item.name}
                    type="button"
                    aria-pressed={!hidden}
                    className={cn(
                      'hover:bg-muted flex min-w-0 items-center gap-2 rounded-md px-2 py-1 text-left text-xs transition-colors',
                      hidden && 'text-muted-foreground line-through opacity-60'
                    )}
                    onClick={() => toggleName(item.name)}
                    title={t('statisticsPage.charts.toggleSeries', { name: item.name })}
                  >
                    <span
                      className="h-2.5 w-2.5 shrink-0 rounded-sm"
                      style={{ backgroundColor: item.fill }}
                    />
                    <span className="min-w-0 flex-1 truncate">{item.name}</span>
                    <span className="shrink-0 tabular-nums">
                      {valueKind === 'currency'
                        ? formatCurrency(item.value, locale)
                        : formatNumber(item.value, locale)}
                    </span>
                  </button>
                )
              })}
            </div>
          </>
        )}
      </CardContent>
    </Card>
  )
}

function DistributionGrid({
  period,
  locale,
}: {
  period: DetailedStatisticsPeriod
  locale: string
}) {
  const { t } = useTranslation()
  const charts = [
    [t('statisticsPage.distributions.ownerCosts'), period.distributions.owner_costs, 'currency'],
    [t('statisticsPage.distributions.modelCosts'), period.distributions.model_costs, 'currency'],
    [t('statisticsPage.distributions.moduleCosts'), period.distributions.module_costs, 'currency'],
    [
      t('statisticsPage.distributions.requestTypeCosts'),
      period.distributions.request_type_costs,
      'currency',
    ],
    [t('statisticsPage.distributions.chatMessages'), period.distributions.chat_messages, 'count'],
    [t('statisticsPage.distributions.chatCosts'), period.distributions.chat_costs, 'currency'],
  ] as const

  return (
    <section className="space-y-4">
      <div className="border-border flex items-center gap-3 border-b pb-3">
        <span className="bg-primary h-6 w-1 rounded-full" aria-hidden="true" />
        <h2 className="text-lg font-semibold">{t('statisticsPage.sections.distributions')}</h2>
      </div>
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        {charts.map(([title, data, valueKind]) => (
          <DistributionChart
            key={title}
            title={title}
            data={data}
            valueKind={valueKind}
            locale={locale}
          />
        ))}
      </div>
    </section>
  )
}

function PeriodPanel({ period, locale }: { period: DetailedStatisticsPeriod; locale: string }) {
  const { t } = useTranslation()
  return (
    <div className="space-y-5">
      <div className="border-border/80 bg-muted/35 flex items-center gap-3 rounded-md border px-4 py-3">
        <CalendarClock className="text-primary size-4 shrink-0" aria-hidden="true" />
        <div className="text-muted-foreground text-xs font-medium">
          {t('statisticsPage.periodRange', {
            start: formatDateTime(period.start_time, locale),
            end: formatDateTime(period.end_time, locale),
          })}
        </div>
      </div>
      <SummaryMetrics period={period} locale={locale} />
      <DetailTables period={period} locale={locale} />
      <DistributionGrid period={period} locale={locale} />
    </div>
  )
}

interface SeriesDefinition {
  key: string
  label: string
  values: number[]
  color: string
}

function MultiSeriesChart({
  title,
  labels,
  series,
  valueKind,
  locale,
}: {
  title: string
  labels: string[]
  series: Record<string, number[]>
  valueKind: 'currency' | 'count'
  locale: string
}) {
  const { t } = useTranslation()
  const [hiddenKeys, setHiddenKeys] = useState<Set<string>>(() => new Set())
  const definitions: SeriesDefinition[] = Object.entries(series).map(([label, values], index) => ({
    key: `series_${index}`,
    label,
    values,
    color: CHART_COLORS[index % CHART_COLORS.length],
  }))
  const chartData = labels.map((label, index) => {
    const item: Record<string, string | number> = { label }
    for (const definition of definitions) {
      item[definition.key] = definition.values[index] ?? 0
    }
    return item
  })
  const config = Object.fromEntries(
    definitions.map((definition) => [
      definition.key,
      { label: definition.label, color: definition.color },
    ])
  ) as ChartConfig

  return (
    <Card className="overflow-hidden shadow-sm">
      <CardHeader className="border-b pb-3">
        <CardTitle className="text-sm">{title}</CardTitle>
      </CardHeader>
      <CardContent className="pt-4 sm:pt-4">
        {definitions.length === 0 ? (
          <EmptyState />
        ) : (
          <>
            <ChartContainer config={config} className="aspect-auto h-[340px] w-full min-w-0">
              <LineChart data={chartData} margin={{ left: 8, right: 8, top: 8 }}>
                <CartesianGrid vertical={false} strokeDasharray="3 3" />
                <XAxis dataKey="label" tickLine={false} axisLine={false} minTickGap={24} />
                <YAxis
                  tickLine={false}
                  axisLine={false}
                  width={64}
                  tickFormatter={(value) =>
                    valueKind === 'currency'
                      ? formatCurrency(Number(value), locale)
                      : formatNumber(Number(value), locale)
                  }
                />
                <ChartTooltip
                  content={
                    <ChartTooltipContent
                      formatter={(value) =>
                        valueKind === 'currency'
                          ? formatCurrency(Number(value), locale)
                          : formatNumber(Number(value), locale)
                      }
                    />
                  }
                />
                {definitions.map((definition) => (
                  <Line
                    key={definition.key}
                    dataKey={definition.key}
                    name={definition.label}
                    stroke={definition.color}
                    strokeWidth={2}
                    dot={false}
                    hide={hiddenKeys.has(definition.key)}
                    isAnimationActive={false}
                  />
                ))}
              </LineChart>
            </ChartContainer>
            <div className="mt-2 flex max-h-24 flex-wrap gap-1 overflow-y-auto">
              {definitions.map((definition) => {
                const hidden = hiddenKeys.has(definition.key)
                return (
                  <button
                    key={definition.key}
                    type="button"
                    aria-pressed={!hidden}
                    className={cn(
                      'hover:bg-muted inline-flex max-w-60 items-center gap-1.5 rounded-md px-2 py-1 text-xs transition-colors',
                      hidden && 'text-muted-foreground line-through opacity-60'
                    )}
                    onClick={() =>
                      setHiddenKeys((current) => {
                        const next = new Set(current)
                        if (next.has(definition.key)) next.delete(definition.key)
                        else next.add(definition.key)
                        return next
                      })
                    }
                    title={t('statisticsPage.charts.toggleSeries', { name: definition.label })}
                  >
                    <span
                      className="h-2 w-2 shrink-0 rounded-sm"
                      style={{ backgroundColor: definition.color }}
                    />
                    <span className="truncate">{definition.label}</span>
                  </button>
                )
              })}
            </div>
          </>
        )}
      </CardContent>
    </Card>
  )
}

function SingleSeriesChart({
  title,
  labels,
  values,
  valueKind,
  locale,
}: {
  title: string
  labels: string[]
  values: number[]
  valueKind: 'currency' | 'count'
  locale: string
}) {
  const data = labels.map((label, index) => ({ label, value: values[index] ?? 0 }))
  const config = { value: { label: title, color: CHART_COLORS[0] } } satisfies ChartConfig
  return (
    <Card className="overflow-hidden shadow-sm">
      <CardHeader className="border-b pb-3">
        <CardTitle className="text-sm">{title}</CardTitle>
      </CardHeader>
      <CardContent className="pt-4 sm:pt-4">
        <ChartContainer config={config} className="aspect-auto h-[320px] w-full min-w-0">
          <LineChart data={data} margin={{ left: 8, right: 8, top: 8 }}>
            <CartesianGrid vertical={false} strokeDasharray="3 3" />
            <XAxis dataKey="label" tickLine={false} axisLine={false} minTickGap={24} />
            <YAxis
              tickLine={false}
              axisLine={false}
              width={64}
              tickFormatter={(value) =>
                valueKind === 'currency'
                  ? formatCurrency(Number(value), locale)
                  : formatNumber(Number(value), locale)
              }
            />
            <ChartTooltip
              content={
                <ChartTooltipContent
                  formatter={(value) =>
                    valueKind === 'currency'
                      ? formatCurrency(Number(value), locale)
                      : formatNumber(Number(value), locale)
                  }
                />
              }
            />
            <Line
              dataKey="value"
              stroke="var(--color-value)"
              strokeWidth={2}
              dot={false}
              isAnimationActive={false}
            />
          </LineChart>
        </ChartContainer>
      </CardContent>
    </Card>
  )
}

function TotalCostChart({ data, locale }: { data: DetailedStatisticsTrendData; locale: string }) {
  const { t } = useTranslation()
  return (
    <SingleSeriesChart
      title={t('statisticsPage.trends.totalCost')}
      labels={data.time_labels}
      values={data.total_cost_data}
      valueKind="currency"
      locale={locale}
    />
  )
}

function TrendsPanel({ data, locale }: { data: DetailedStatisticsData; locale: string }) {
  const { t } = useTranslation()
  const [range, setRange] = useState('24h')
  const trendData = data.trends[range]
  if (!trendData) return <EmptyState />

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-base font-semibold">{t('statisticsPage.tabs.trends')}</h2>
        <div className="flex flex-wrap gap-1" aria-label={t('statisticsPage.charts.timeRange')}>
          {['6h', '12h', '24h', '48h'].map((value) => (
            <Button
              key={value}
              type="button"
              size="sm"
              variant={range === value ? 'secondary' : 'outline'}
              aria-pressed={range === value}
              onClick={() => setRange(value)}
            >
              {t(`statisticsPage.ranges.${value}`)}
            </Button>
          ))}
        </div>
      </div>
      <div className="grid grid-cols-1 gap-4">
        <TotalCostChart data={trendData} locale={locale} />
        <MultiSeriesChart
          title={t('statisticsPage.trends.moduleCosts')}
          labels={trendData.time_labels}
          series={trendData.cost_by_module}
          valueKind="currency"
          locale={locale}
        />
        <MultiSeriesChart
          title={t('statisticsPage.trends.modelCosts')}
          labels={trendData.time_labels}
          series={trendData.cost_by_model}
          valueKind="currency"
          locale={locale}
        />
        <MultiSeriesChart
          title={t('statisticsPage.trends.chatMessages')}
          labels={trendData.time_labels}
          series={trendData.message_by_chat}
          valueKind="count"
          locale={locale}
        />
      </div>
    </div>
  )
}

function MetricsPanel({ data, locale }: { data: DetailedStatisticsData; locale: string }) {
  const { t } = useTranslation()
  const [range, setRange] = useState('7d')
  const metrics = data.metrics[range] as DetailedStatisticsMetricsData | undefined
  if (!metrics) return <EmptyState />

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-base font-semibold">{t('statisticsPage.tabs.metrics')}</h2>
        <div className="flex flex-wrap gap-1" aria-label={t('statisticsPage.charts.timeScale')}>
          {['24h', '7d', '30d'].map((value) => (
            <Button
              key={value}
              type="button"
              size="sm"
              variant={range === value ? 'secondary' : 'outline'}
              aria-pressed={range === value}
              onClick={() => setRange(value)}
            >
              {t(`statisticsPage.ranges.${value}`)}
            </Button>
          ))}
        </div>
      </div>
      <div className="grid grid-cols-1 gap-4">
        <SingleSeriesChart
          title={t('statisticsPage.metrics.costPerMessages')}
          labels={metrics.time_labels}
          values={metrics.cost_per_100_messages}
          valueKind="currency"
          locale={locale}
        />
        <SingleSeriesChart
          title={t('statisticsPage.metrics.costPerHour')}
          labels={metrics.time_labels}
          values={metrics.cost_per_hour}
          valueKind="currency"
          locale={locale}
        />
        <SingleSeriesChart
          title={t('statisticsPage.metrics.tokensPerHour')}
          labels={metrics.time_labels}
          values={metrics.tokens_per_hour}
          valueKind="count"
          locale={locale}
        />
        <SingleSeriesChart
          title={t('statisticsPage.metrics.costPerReplies')}
          labels={metrics.time_labels}
          values={metrics.cost_per_100_replies}
          valueKind="currency"
          locale={locale}
        />
      </div>
    </div>
  )
}

function getOrderedPeriods(data: DetailedStatisticsData): DetailedStatisticsPeriod[] {
  const order = new Map(PERIOD_KEYS.map((key, index) => [key, index]))
  return [...data.periods].sort(
    (left, right) =>
      (order.get(left.key as (typeof PERIOD_KEYS)[number]) ?? 99) -
      (order.get(right.key as (typeof PERIOD_KEYS)[number]) ?? 99)
  )
}

export function StatisticsPage() {
  const { t, i18n } = useTranslation()
  const locale = i18n.resolvedLanguage || i18n.language
  const { data, error, loading, refresh } = useDetailedStatistics()
  const periods = useMemo(() => (data ? getOrderedPeriods(data) : []), [data])
  const [activeTab, setActiveTab] = useState('all_time')
  const validTabValues = new Set([...periods.map((period) => period.key), 'trends', 'metrics'])
  const selectedTab = validTabValues.has(activeTab) ? activeTab : (periods[0]?.key ?? 'trends')

  return (
    <div className="flex min-h-full flex-col p-4 sm:p-6">
      <div className="mx-auto w-full max-w-[1800px]">
        <header className="border-primary/30 bg-card relative mb-5 overflow-hidden rounded-xl border p-5 shadow-sm sm:p-6">
          <div className="bg-primary absolute inset-y-0 left-0 w-1.5" aria-hidden="true" />
          <div className="flex flex-col justify-between gap-5 sm:flex-row sm:items-start">
            <div className="min-w-0">
              <h1 className="text-3xl font-bold tracking-tight sm:text-4xl">
                {t('statisticsPage.title')}
              </h1>
              <div className="bg-primary mt-3 h-0.5 w-28" aria-hidden="true" />
              <p className="text-muted-foreground mt-3 max-w-2xl text-sm sm:text-base">
                {t('statisticsPage.description')}
              </p>
            </div>
            <div className="flex shrink-0 flex-col items-start gap-3 sm:items-end">
              {data && (
                <div className="border-border/80 bg-muted/35 rounded-md border px-3 py-2 text-xs font-medium tabular-nums">
                  {t('statisticsPage.generatedAt', {
                    time: formatDateTime(data.generated_at, locale),
                  })}
                </div>
              )}
              <div className="flex flex-wrap gap-2">
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => void refresh()}
                  disabled={loading}
                >
                  <RefreshCw className={cn(loading && 'animate-spin')} />
                  {t('statisticsPage.actions.refresh')}
                </Button>
                <Button asChild variant="outline" size="sm">
                  <a href="/maibot_statistics.html" target="_blank" rel="noopener noreferrer">
                    <ExternalLink />
                    {t('statisticsPage.actions.openHtml')}
                  </a>
                </Button>
              </div>
            </div>
          </div>
        </header>

        {error && !data ? (
          <Alert variant="destructive">
            <AlertCircle />
            <AlertTitle>{t('statisticsPage.error.title')}</AlertTitle>
            <AlertDescription>{error || t('statisticsPage.error.description')}</AlertDescription>
          </Alert>
        ) : loading && !data ? (
          <StatisticsPageSkeleton />
        ) : data ? (
          <Tabs
            value={selectedTab}
            onValueChange={setActiveTab}
            className="border-primary/25 bg-card/40 min-w-0 rounded-xl border p-3 shadow-sm sm:p-5"
          >
            <DashboardTabBar className="border-border/70 bg-muted/45 rounded-md border">
              {periods.map((period) => (
                <DashboardTabTrigger key={period.key} value={period.key}>
                  {t(`statisticsPage.periods.${period.key}`)}
                </DashboardTabTrigger>
              ))}
              <DashboardTabTrigger value="trends">
                {t('statisticsPage.tabs.trends')}
              </DashboardTabTrigger>
              <DashboardTabTrigger value="metrics">
                {t('statisticsPage.tabs.metrics')}
              </DashboardTabTrigger>
            </DashboardTabBar>

            {periods.map((period) => (
              <TabsContent key={period.key} value={period.key} className="mt-5">
                <PeriodPanel period={period} locale={locale} />
              </TabsContent>
            ))}
            <TabsContent value="trends" className="mt-5">
              <TrendsPanel data={data} locale={locale} />
            </TabsContent>
            <TabsContent value="metrics" className="mt-5">
              <MetricsPanel data={data} locale={locale} />
            </TabsContent>
          </Tabs>
        ) : null}
      </div>
    </div>
  )
}
