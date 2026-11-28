import { motion } from 'motion/react'
import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Bar, BarChart, CartesianGrid, Line, LineChart, Tooltip, XAxis, YAxis } from 'recharts'

import { BoardGrid, BoardModule, BoardRule, BoardScreen } from '@/components/ui/board'
import { ChartContainer, type ChartConfig } from '@/components/ui/chart'
import { useMotion } from '@/lib/motion'
import { cn } from '@/lib/utils'

import {
  buildInsights,
  formatCurrency,
  formatDateTime,
  formatNumber,
  formatPercent,
  OTHER_SERIES_COLOR,
  seriesColor,
} from '../statistics-model'
import type { DetailedPeriodTrend, DetailedStatisticsPeriod } from '../types'
import { ChartTooltipBox, EmptyState, OptionSwitch, Swatch } from './shared'

export type TrendMetric = 'cost' | 'tokens' | 'requests' | 'messages'
export type TrendGroup = 'total' | 'model' | 'module' | 'chat'
type TrendMode = 'stack' | 'line' | 'percent'

/** 每个指标可用的分组；后端只按这些维度提供了时间序列 */
const GROUPS_BY_METRIC: Record<TrendMetric, TrendGroup[]> = {
  cost: ['total', 'model', 'module'],
  tokens: ['total', 'model'],
  requests: ['total', 'model'],
  messages: ['total', 'chat'],
}

const METRIC_CODES: Record<TrendMetric, string> = {
  cost: 'COST',
  tokens: 'TOKENS',
  requests: 'REQUESTS',
  messages: 'MESSAGES',
}

/** 趋势图单独展示的系列数，其余合并为「其他」（最多 5 条系列） */
const TREND_SERIES_LIMIT = 4

interface TrendSeries {
  key: string
  name: string
  color: string
  values: number[]
}

function totalSeries(trend: DetailedPeriodTrend, metric: TrendMetric): number[] {
  return { cost: trend.costs, tokens: trend.tokens, requests: trend.requests, messages: trend.messages }[metric]
}

function groupedSeries(trend: DetailedPeriodTrend, metric: TrendMetric, group: TrendGroup) {
  if (group === 'model') {
    return { cost: trend.cost_by_model, tokens: trend.tokens_by_model, requests: trend.requests_by_model }[
      metric as 'cost' | 'tokens' | 'requests'
    ]
  }
  if (group === 'module') return trend.cost_by_module
  return trend.message_by_chat
}

export function buildTrendSeries(
  trend: DetailedPeriodTrend,
  metric: TrendMetric,
  group: TrendGroup,
  totalLabel: string,
  otherLabel: (count: number) => string
): TrendSeries[] {
  if (group === 'total') {
    return [{ key: 's0', name: totalLabel, color: seriesColor(0), values: totalSeries(trend, metric) }]
  }
  const entries = Object.entries(groupedSeries(trend, metric, group))
    .map(([name, values]) => ({ name, values, sum: values.reduce((total, value) => total + value, 0) }))
    .filter((entry) => entry.sum > 0)
    .sort((a, b) => b.sum - a.sum)
  const head = entries.slice(0, TREND_SERIES_LIMIT)
  const rest = entries.slice(TREND_SERIES_LIMIT)
  const series: TrendSeries[] = head.map((entry, index) => ({
    key: `s${index}`,
    name: entry.name,
    color: seriesColor(index),
    values: entry.values,
  }))
  if (rest.length > 0) {
    series.push({
      key: 'other',
      name: otherLabel(rest.length),
      color: OTHER_SERIES_COLOR,
      values: trend.time_labels.map((_, index) => rest.reduce((sum, entry) => sum + (entry.values[index] ?? 0), 0)),
    })
  }
  return series
}

interface TrendSectionProps {
  period: DetailedStatisticsPeriod
  locale: string
}

export function TrendSection({ period, locale }: TrendSectionProps) {
  const { t } = useTranslation()
  const [metric, setMetric] = useState<TrendMetric>('cost')
  const [group, setGroup] = useState<TrendGroup>('model')
  const [mode, setMode] = useState<TrendMode>('stack')
  const [hidden, setHidden] = useState<Set<string>>(() => new Set())
  const availableGroups = GROUPS_BY_METRIC[metric]
  const activeGroup = availableGroups.includes(group) ? group : availableGroups[1] ?? 'total'

  const changeMetric = (next: TrendMetric) => {
    setMetric(next)
    setHidden(new Set())
  }
  const changeGroup = (next: TrendGroup) => {
    setGroup(next)
    setHidden(new Set())
  }

  const activeMode = activeGroup === 'total' && mode === 'percent' ? 'stack' : mode

  return (
    <>
      <BoardRule code="TREND">
        <span data-board-label="true" className="shrink-0 whitespace-nowrap">
          {t('statisticsPage.sections.trend')}
        </span>
        <OptionSwitch
          label={t('statisticsPage.trend.metric')}
          value={metric}
          onChange={changeMetric}
          options={(['cost', 'tokens', 'requests', 'messages'] as const).map((value) => ({
            value,
            label: t(`statisticsPage.trend.metrics.${value}`),
          }))}
        />
      </BoardRule>
      <BoardGrid>
        <TrendChartModule
          trend={period.trend}
          metric={metric}
          group={activeGroup}
          availableGroups={availableGroups}
          mode={activeMode}
          onGroupChange={changeGroup}
          onModeChange={setMode}
          hidden={hidden}
          onToggle={(key) =>
            setHidden((current) => {
              const next = new Set(current)
              if (next.has(key)) next.delete(key)
              else next.add(key)
              return next
            })
          }
          locale={locale}
        />
        <InsightsModule period={period} locale={locale} />
      </BoardGrid>
    </>
  )
}

function TrendChartModule({
  trend,
  metric,
  group,
  availableGroups,
  mode,
  onGroupChange,
  onModeChange,
  hidden,
  onToggle,
  locale,
}: {
  trend: DetailedPeriodTrend
  metric: TrendMetric
  group: TrendGroup
  availableGroups: TrendGroup[]
  mode: TrendMode
  onGroupChange: (group: TrendGroup) => void
  onModeChange: (mode: TrendMode) => void
  hidden: Set<string>
  onToggle: (key: string) => void
  locale: string
}) {
  const { t } = useTranslation()
  const { enabled, transitions } = useMotion()
  const series = useMemo(
    () =>
      buildTrendSeries(trend, metric, group, t(`statisticsPage.trend.totals.${metric}`), (count) =>
        t('statisticsPage.other', { count })
      ),
    [trend, metric, group, t]
  )
  const visible = series.filter((item) => !hidden.has(item.key))
  const data = trend.time_labels.map((label, index) => {
    const row: Record<string, string | number> = { label }
    for (const item of series) row[item.key] = item.values[index] ?? 0
    return row
  })
  const config = Object.fromEntries(
    series.map((item) => [item.key, { label: item.name, color: item.color }])
  ) satisfies ChartConfig
  const formatValue = (value: number) =>
    metric === 'cost' ? formatCurrency(value, locale) : formatNumber(value, locale)
  const sum = totalSeries(trend, metric).reduce((total, value) => total + value, 0)

  const axisTick = { fontSize: 12 }
  const tooltip = (
    <Tooltip
      isAnimationActive={false}
      cursor={{ fill: 'hsl(var(--muted-foreground) / 0.12)' }}
      content={({ active, payload, label }) => {
        if (!active || !payload?.length) return null
        const rows = [...payload].sort((a, b) => Number(b.value) - Number(a.value))
        const total = rows.reduce((value, item) => value + Number(item.value ?? 0), 0)
        return (
          <ChartTooltipBox className="min-w-44">
            <div className="mb-1 font-medium">{label}</div>
            {rows.map((item) => (
              <div key={String(item.dataKey)} className="flex items-center justify-between gap-3 leading-6">
                <span className="flex min-w-0 items-center gap-1.5">
                  <Swatch color={String(item.color)} />
                  <span className="truncate">{item.name}</span>
                </span>
                <span className="font-medium tabular-nums">
                  {formatValue(Number(item.value))}
                  {mode === 'percent' && total > 0 && (
                    <span className="text-muted-foreground ml-1">{formatPercent(Number(item.value) / total)}</span>
                  )}
                </span>
              </div>
            ))}
            {rows.length > 1 && (
              <div className="mt-1 flex justify-between border-t border-dashed pt-1 font-medium">
                <span>{t('statisticsPage.trend.sum')}</span>
                <span className="font-medium tabular-nums">{formatValue(total)}</span>
              </div>
            )}
          </ChartTooltipBox>
        )
      }}
    />
  )
  const axes = (
    <>
      <CartesianGrid vertical={false} strokeDasharray="2 4" />
      <XAxis dataKey="label" tickLine={false} axisLine={false} minTickGap={28} tick={axisTick} />
      <YAxis
        tickLine={false}
        axisLine={false}
        width={64}
        tick={axisTick}
        tickFormatter={(value) =>
          mode === 'percent' ? formatPercent(Number(value), 0) : formatValue(Number(value))
        }
      />
    </>
  )

  return (
    <BoardModule
      code={METRIC_CODES[metric]}
      title={t(`statisticsPage.trend.titles.${metric}`)}
      span={8}
      spanNarrow={12}
      tone={2}
      actions={
        <span data-board-label="true">
          {t('statisticsPage.trend.sum')} {formatValue(sum)}
        </span>
      }
    >
      <BoardScreen flush className="flex flex-col gap-2.5">
        <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
          <div className="flex min-w-0 flex-wrap items-center gap-2">
            <span data-board-label="true" className="shrink-0 whitespace-nowrap">
              {t('statisticsPage.trend.group')}
            </span>
            <OptionSwitch
              label={t('statisticsPage.trend.group')}
              value={group}
              onChange={onGroupChange}
              options={availableGroups.map((value) => ({
                value,
                label: t(`statisticsPage.trend.groups.${value}`),
              }))}
            />
          </div>
          <div className="flex min-w-0 flex-wrap items-center gap-2">
            <span data-board-label="true" className="shrink-0 whitespace-nowrap">
              {t('statisticsPage.trend.mode')}
            </span>
            <OptionSwitch
              label={t('statisticsPage.trend.mode')}
              value={mode}
              onChange={onModeChange}
              options={(['stack', 'line', 'percent'] as const).map((value) => ({
                value,
                label: t(`statisticsPage.trend.modes.${value}`),
                disabled: value === 'percent' && group === 'total',
              }))}
            />
          </div>
        </div>
        {trend.truncated && (
          <p data-board-label="true">
            {t('statisticsPage.trend.truncated', { start: formatDateTime(trend.start_time, locale) })}
          </p>
        )}
        {series.length === 0 || sum === 0 ? (
          <EmptyState />
        ) : (
          <>
            <motion.div
              key={`${metric}:${group}:${mode}`}
              initial={enabled ? { opacity: 0, y: 4 } : false}
              animate={{ opacity: 1, y: 0 }}
              transition={transitions.fast}
              className="min-w-0"
            >
              <ChartContainer config={config} className="aspect-auto h-[300px] w-full min-w-0">
                {mode === 'line' ? (
                  <LineChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                    {axes}
                    {tooltip}
                    {visible.map((item) => (
                      <Line
                        key={item.key}
                        dataKey={item.key}
                        name={item.name}
                        stroke={item.color}
                        strokeWidth={2}
                        dot={false}
                        isAnimationActive={false}
                      />
                    ))}
                  </LineChart>
                ) : (
                  <BarChart
                    data={data}
                    margin={{ top: 8, right: 8, left: 0, bottom: 0 }}
                    stackOffset={mode === 'percent' ? 'expand' : 'none'}
                    barCategoryGap="18%"
                  >
                    {axes}
                    {tooltip}
                    {visible.map((item) => (
                      <Bar
                        key={item.key}
                        dataKey={item.key}
                        name={item.name}
                        stackId="trend"
                        fill={item.color}
                        isAnimationActive={false}
                      />
                    ))}
                  </BarChart>
                )}
              </ChartContainer>
            </motion.div>
            {group !== 'total' && (
              <div data-chart-legend="true" className="flex flex-wrap gap-1.5 pt-0.5">
                {series.map((item) => {
                  const off = hidden.has(item.key)
                  return (
                    <button
                      key={item.key}
                      type="button"
                      aria-pressed={!off}
                      title={t('statisticsPage.charts.toggleSeries', { name: item.name })}
                      onClick={() => onToggle(item.key)}
                      className={cn(
                        'inline-flex max-w-60 cursor-pointer items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium transition-all duration-150',
                        off
                          ? 'border-border/50 bg-transparent text-muted-foreground line-through opacity-50 hover:opacity-75'
                          : 'border-border/75 bg-muted/45 text-foreground hover:border-border hover:bg-muted/80'
                      )}
                    >
                      <Swatch color={item.color} />
                      <span className="truncate">{item.name}</span>
                    </button>
                  )
                })}
              </div>
            )}
          </>
        )}
      </BoardScreen>
    </BoardModule>
  )
}

function InsightsModule({ period, locale }: TrendSectionProps) {
  const { t } = useTranslation()
  const insights = useMemo(() => buildInsights({ period, locale }), [period, locale])
  const alerts = insights.filter((item) => item.tone === 'alert').length
  return (
    <BoardModule
      code="INSIGHTS"
      title={t('statisticsPage.modules.insights')}
      span={4}
      spanNarrow={12}
      tone={1}
      actions={
        alerts > 0 ? (
          <span data-board-badge="true">{t('statisticsPage.insights.alertCount', { count: alerts })}</span>
        ) : undefined
      }
    >
      <BoardScreen>
        {insights.length === 0 ? (
          <EmptyState>{t('statisticsPage.insights.empty')}</EmptyState>
        ) : (
          <ul>
            {insights.map((insight) => (
              <li key={insight.id} data-stats-insight={insight.tone}>
                <span>{t(`statisticsPage.insights.${insight.key}`, insight.params)}</span>
              </li>
            ))}
          </ul>
        )}
      </BoardScreen>
    </BoardModule>
  )
}
