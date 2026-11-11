import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Bar, CartesianGrid, ComposedChart, Line, ReferenceLine, Tooltip, XAxis, YAxis } from 'recharts'

import { BoardGrid, BoardModule, BoardRule, BoardScreen } from '@/components/ui/board'
import { ChartContainer } from '@/components/ui/chart'
import { SegmentedMeter } from '@/components/ui/segmented-meter'

import {
  FAILURE_RATE_ALERT,
  formatDateTime,
  formatNumber,
  formatPercent,
  formatSeconds,
} from '../statistics-model'
import type { DetailedReliabilityData, DetailedReliabilityItem, DetailedStatisticsPeriod } from '../types'
import { EmptyState, OptionSwitch } from './shared'

type ReliabilityDimension = 'model' | 'provider' | 'task'

export function ReliabilitySection({ period, locale }: { period: DetailedStatisticsPeriod; locale: string }) {
  const { t } = useTranslation()
  const { reliability } = period
  return (
    <>
      <BoardRule code="RELIABILITY">
        <span data-board-label="true">{t('statisticsPage.sections.reliability')}</span>
        <span data-board-label="true">
          {reliability.truncated
            ? t('statisticsPage.reliability.truncated', {
                start: formatDateTime(reliability.coverage_start, locale),
              })
            : t('statisticsPage.reliability.source')}
        </span>
      </BoardRule>
      <BoardGrid>
        <FailureRankModule reliability={reliability} locale={locale} />
        <FailureTrendModule period={period} locale={locale} />
      </BoardGrid>
    </>
  )
}

function FailureRankModule({ reliability, locale }: { reliability: DetailedReliabilityData; locale: string }) {
  const { t } = useTranslation()
  const [dimension, setDimension] = useState<ReliabilityDimension>('model')
  const items: DetailedReliabilityItem[] = {
    model: reliability.by_model,
    provider: reliability.by_provider,
    task: reliability.by_task,
  }[dimension]
  const maxRate = Math.max(0, ...items.map((item) => item.failure_rate ?? 0))

  return (
    <BoardModule
      code="ERRORS"
      title={t('statisticsPage.modules.failureRank')}
      span={5}
      spanNarrow={12}
      tone={2}
      actions={
        <OptionSwitch
          label={t('statisticsPage.reliability.dimension')}
          value={dimension}
          onChange={setDimension}
          options={(['model', 'provider', 'task'] as const).map((value) => ({
            value,
            label: t(`statisticsPage.reliability.dimensions.${value}`),
          }))}
        />
      }
    >
      <BoardScreen>
        {items.length === 0 ? (
          <EmptyState>{t('statisticsPage.reliability.empty')}</EmptyState>
        ) : (
          <>
            <div data-stats-error-row="true" data-head="true">
              <span>{t('statisticsPage.table.name')}</span>
              <span data-numeric="true">{t('statisticsPage.reliability.attempts')}</span>
              <span data-numeric="true">{t('statisticsPage.reliability.failures')}</span>
              <span data-numeric="true">{t('statisticsPage.reliability.failureRate')}</span>
            </div>
            {items.map((item) => {
              const alert = (item.failure_rate ?? 0) >= FAILURE_RATE_ALERT
              return (
                <div key={item.name} data-stats-error-row="true">
                  <span data-stats-row-name="true" title={item.name}>
                    {item.name}
                  </span>
                  <span data-numeric="true">{formatNumber(item.attempt_count, locale)}</span>
                  <span data-numeric="true">{formatNumber(item.failure_count, locale)}</span>
                  <span data-numeric="true" data-stats-alert-text={alert ? 'true' : undefined}>
                    {formatPercent(item.failure_rate)}
                  </span>
                  <SegmentedMeter
                    label={item.name}
                    total={maxRate > 0 ? maxRate * 100 : 100}
                    segments={[
                      {
                        label: item.name,
                        value: (item.failure_rate ?? 0) * 100,
                        className: alert ? 'bg-[var(--stats-alert)]' : 'bg-primary',
                      },
                    ]}
                    className="h-1.5"
                  />
                </div>
              )
            })}
            <p data-board-label="true" className="pt-2">
              {t('statisticsPage.reliability.hint', { threshold: formatPercent(FAILURE_RATE_ALERT, 0) })}
            </p>
          </>
        )}
      </BoardScreen>
    </BoardModule>
  )
}

function FailureTrendModule({ period, locale }: { period: DetailedStatisticsPeriod; locale: string }) {
  const { t } = useTranslation()
  const { reliability, trend } = period
  const data = trend.time_labels.map((label, index) => {
    const attempts = trend.attempts[index] ?? 0
    const failures = trend.failures[index] ?? 0
    return { label, attempts, failures, rate: attempts > 0 ? failures / attempts : 0 }
  })
  const hasAttempts = data.some((row) => row.attempts > 0)
  const summary = [
    { key: 'attempts', label: t('statisticsPage.reliability.attempts'), value: formatNumber(reliability.attempt_count, locale) },
    { key: 'failures', label: t('statisticsPage.reliability.failures'), value: formatNumber(reliability.failure_count, locale), alert: true },
    { key: 'rate', label: t('statisticsPage.reliability.failureRate'), value: formatPercent(reliability.failure_rate), alert: true },
    { key: 'duration', label: t('statisticsPage.reliability.avgSuccessDuration'), value: formatSeconds(reliability.avg_success_duration) },
  ]
  const axisTick = { fontSize: 11 }

  return (
    <BoardModule
      code="FAILURES"
      title={t('statisticsPage.modules.failureTrend')}
      span={7}
      spanNarrow={12}
      tone={0}
      actions={<span data-board-label="true">{t('statisticsPage.reliability.chartLegend')}</span>}
    >
      <BoardScreen flush className="flex flex-col gap-2">
        <div className="flex flex-col gap-2">
          <div data-stats-failure-summary="true">
            {summary.map((item) => (
              <div key={item.key}>
                <span className="text-xs opacity-80">{item.label}</span>
                <strong data-stats-alert-text={item.alert && reliability.failure_count > 0 ? 'true' : undefined}>
                  {item.value}
                </strong>
              </div>
            ))}
          </div>
          {!hasAttempts ? (
            <EmptyState>{t('statisticsPage.reliability.empty')}</EmptyState>
          ) : (
            <ChartContainer config={{}} className="aspect-auto h-[240px] w-full min-w-0">
              <ComposedChart data={data} margin={{ top: 8, right: 0, left: 0, bottom: 0 }} barCategoryGap="18%">
                <CartesianGrid vertical={false} strokeDasharray="2 4" />
                <XAxis dataKey="label" tickLine={false} axisLine={false} minTickGap={28} tick={axisTick} />
                <YAxis
                  yAxisId="count"
                  tickLine={false}
                  axisLine={false}
                  width={48}
                  tick={axisTick}
                  allowDecimals={false}
                  tickFormatter={(value) => formatNumber(Number(value), locale)}
                />
                <YAxis
                  yAxisId="rate"
                  orientation="right"
                  tickLine={false}
                  axisLine={false}
                  width={48}
                  tick={axisTick}
                  tickFormatter={(value) => formatPercent(Number(value), 0)}
                />
                <Tooltip
                  isAnimationActive={false}
                  cursor={{ fill: 'hsl(var(--muted-foreground) / 0.12)' }}
                  content={({ active, payload, label }) => {
                    if (!active || !payload?.length) return null
                    const row = payload[0].payload as (typeof data)[number]
                    return (
                      <div className="border-border/50 bg-background text-foreground min-w-40 rounded-md border px-2.5 py-1.5 text-xs shadow-xl">
                        <div className="mb-1 font-medium">{label}</div>
                        {[
                          [t('statisticsPage.reliability.attempts'), formatNumber(row.attempts, locale)],
                          [t('statisticsPage.reliability.failures'), formatNumber(row.failures, locale)],
                          [t('statisticsPage.reliability.failureRate'), formatPercent(row.attempts > 0 ? row.rate : null)],
                        ].map(([name, value]) => (
                          <div key={name} className="flex justify-between gap-3 leading-6">
                            <span>{name}</span>
                            <span className="font-mono tabular-nums">{value}</span>
                          </div>
                        ))}
                      </div>
                    )
                  }}
                />
                <ReferenceLine
                  yAxisId="rate"
                  y={FAILURE_RATE_ALERT}
                  stroke="var(--stats-alert)"
                  strokeDasharray="5 4"
                />
                <Bar yAxisId="count" dataKey="failures" fill="var(--stats-series-1)" isAnimationActive={false} />
                <Line
                  yAxisId="rate"
                  dataKey="rate"
                  stroke="var(--stats-alert)"
                  strokeWidth={2}
                  dot={false}
                  isAnimationActive={false}
                />
              </ComposedChart>
            </ChartContainer>
          )}
        </div>
      </BoardScreen>
    </BoardModule>
  )
}
