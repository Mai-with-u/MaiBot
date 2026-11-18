import { useTranslation } from 'react-i18next'

import { BoardGrid, BoardModule, BoardScreen } from '@/components/ui/board'
import { SegmentedMeter } from '@/components/ui/segmented-meter'

import {
  formatCurrency,
  formatDuration,
  formatNumber,
  formatPercent,
  rankWithOther,
  relativeChange,
  summaryDelta,
  type SummaryDelta,
} from '../statistics-model'
import type { DetailedStatisticsPeriod } from '../types'
import { DeltaBadge, EmptyState, SparkBars, Swatch } from './shared'

/** 迷你走势只取最近 24 个时间桶，避免长时段挤成细线 */
const SPARK_BUCKETS = 24

interface OverviewProps {
  period: DetailedStatisticsPeriod
  locale: string
  compare: boolean
  onShowModels: () => void
}

export function OverviewSection({ period, locale, compare, onShowModels }: OverviewProps) {
  return (
    <>
      <BoardGrid>
        <UsageModule period={period} locale={locale} compare={compare} />
        <CacheModule period={period} locale={locale} />
        <ModelCostModule period={period} onShowModels={onShowModels} />
      </BoardGrid>
      <BoardGrid>
        <TokenFlowModule period={period} locale={locale} />
        <UnitCostModule period={period} locale={locale} compare={compare} />
      </BoardGrid>
    </>
  )
}

function UsageModule({ period, locale, compare }: Omit<OverviewProps, 'onShowModels'>) {
  const { t } = useTranslation()
  const { summary, trend, reliability } = period
  const previous = compare ? period.previous_summary : null
  const recent = (values: number[]) => values.slice(-SPARK_BUCKETS)
  const kpis = [
    {
      key: 'cost',
      label: t('statisticsPage.summary.totalCost'),
      value: formatCurrency(summary.total_cost, locale),
      delta: summaryDelta(summary, previous, 'total_cost', true),
      details: [
        [t('statisticsPage.summary.costPerHour'), formatCurrency(summary.cost_per_hour, locale)],
        [t('statisticsPage.summary.costPerMessages'), formatCurrency(summary.cost_per_100_messages, locale, 4)],
      ],
      spark: recent(trend.costs),
    },
    {
      key: 'requests',
      label: t('statisticsPage.summary.requests'),
      value: formatNumber(summary.total_requests, locale),
      delta: summaryDelta(summary, previous, 'total_requests', false),
      details: [
        [
          t('statisticsPage.summary.callsPerReply'),
          summary.total_replies > 0 ? (summary.total_requests / summary.total_replies).toFixed(1) : 'N/A',
        ],
        [t('statisticsPage.summary.failureRate'), formatPercent(reliability.failure_rate)],
      ],
      spark: recent(trend.requests),
    },
    {
      key: 'tokens',
      label: t('statisticsPage.summary.tokens'),
      value: formatNumber(summary.total_tokens, locale),
      delta: summaryDelta(summary, previous, 'total_tokens', true),
      details: [
        [t('statisticsPage.summary.inputTokens'), formatNumber(summary.input_tokens, locale)],
        [t('statisticsPage.summary.outputTokens'), formatNumber(summary.output_tokens, locale)],
      ],
      spark: recent(trend.tokens),
    },
    {
      key: 'messages',
      label: t('statisticsPage.summary.messages'),
      value: formatNumber(summary.total_messages, locale),
      delta: summaryDelta(summary, previous, 'total_messages', false),
      details: [
        [t('statisticsPage.summary.replies'), formatNumber(summary.total_replies, locale)],
        [
          t('statisticsPage.summary.replyRate'),
          formatPercent(summary.total_messages > 0 ? summary.total_replies / summary.total_messages : null),
        ],
      ],
      spark: recent(trend.messages),
    },
  ]

  return (
    <BoardModule
      code="USAGE"
      title={t('statisticsPage.modules.usage')}
      span={6}
      spanNarrow={12}
      tone={2}
      actions={
        <span data-board-label="true">
          {t('statisticsPage.summary.onlineTime')} {formatDuration(summary.online_time, locale)}
        </span>
      }
    >
      <BoardScreen>
        <div data-stats-kpis="true">
          {kpis.map((kpi) => (
            <div key={kpi.key} data-stats-kpi="true">
              <div className="flex flex-wrap items-center gap-1.5">
                <span data-board-label="true">{kpi.label}</span>
                <DeltaBadge delta={kpi.delta} />
              </div>
              <strong data-board-number="true" title={kpi.value}>
                {kpi.value}
              </strong>
              {kpi.details.map(([label, value]) => (
                <div key={label} data-stats-kv="true">
                  <span>{label}</span>
                  <span>{value}</span>
                </div>
              ))}
              <SparkBars values={kpi.spark} label={t('statisticsPage.summary.sparkLabel', { name: kpi.label })} />
            </div>
          ))}
        </div>
      </BoardScreen>
    </BoardModule>
  )
}

function CacheModule({ period, locale }: { period: DetailedStatisticsPeriod; locale: string }) {
  const { t } = useTranslation()
  const { summary } = period
  // 只列缓存 Token 最多的四个模型，命中率按缓存 Token 计算
  const models = period.models
    .filter((row) => row.cache_hit_rate !== null)
    .sort((a, b) => b.cache_hit_tokens + b.cache_miss_tokens - (a.cache_hit_tokens + a.cache_miss_tokens))
    .slice(0, 4)
  const uncachedInput = Math.max(0, summary.input_tokens - summary.cache_hit_tokens - summary.cache_miss_tokens)
  const rows = [
    { name: t('statisticsPage.cache.all'), rate: summary.cache_hit_rate, primary: true },
    ...models.map((row) => ({ name: row.name, rate: row.cache_hit_rate, primary: false })),
  ]

  return (
    <BoardModule code="CACHE" title={t('statisticsPage.modules.cache')} span={3} spanNarrow={6} tone={0}>
      <BoardScreen className="flex flex-col gap-2.5">
        {rows.map((row) => (
          <div key={row.name} className="flex flex-col gap-1">
            <div className="flex items-baseline justify-between gap-2">
              <span data-board-label="true" className="truncate" title={row.name}>
                {row.name}
              </span>
              <strong data-board-number="true" className={row.primary ? 'text-2xl' : 'text-sm'}>
                {formatPercent(row.rate)}
              </strong>
            </div>
            <SegmentedMeter
              label={row.name}
              total={100}
              segments={[
                {
                  label: row.name,
                  value: row.rate === null ? 0 : row.rate * 100,
                  className: row.rate !== null && row.rate < 0.3 ? 'bg-[var(--stats-alert)]' : 'bg-primary',
                },
              ]}
              className={row.primary ? 'h-2' : 'h-1.5'}
            />
          </div>
        ))}
        {uncachedInput > 0 && (
          <p data-board-label="true" className="mt-auto">
            {t('statisticsPage.cache.uncachedHint', { tokens: formatNumber(uncachedInput, locale) })}
          </p>
        )}
      </BoardScreen>
    </BoardModule>
  )
}

function ModelCostModule({
  period,
  onShowModels,
}: {
  period: DetailedStatisticsPeriod
  onShowModels: () => void
}) {
  const { t } = useTranslation()
  const ranked = rankWithOther(period.distributions.model_costs, 5)
  return (
    <BoardModule
      code="MODELS"
      title={t('statisticsPage.modules.modelCosts')}
      span={3}
      spanNarrow={6}
      tone={1}
      actions={
        <button type="button" data-board-link="true" onClick={onShowModels}>
          {t('statisticsPage.actions.details')} →
        </button>
      }
    >
      <BoardScreen className="flex flex-col gap-1">
        {ranked.items.length === 0 ? (
          <EmptyState />
        ) : (
          <>
            <div data-stats-stack="true" className="mb-1.5" aria-hidden="true">
              {ranked.items.map((item) => (
                <span key={item.name || 'other'} style={{ flexGrow: item.value, background: item.color }} />
              ))}
            </div>
            {ranked.items.map((item) => (
              <div key={item.name || 'other'} data-stats-row="true">
                <Swatch color={item.color} />
                <span data-stats-row-name="true" title={item.name}>
                  {item.other ? t('statisticsPage.other', { count: ranked.otherCount }) : item.name}
                </span>
                <span data-board-number="true">{formatPercent(item.value / ranked.total)}</span>
              </div>
            ))}
            {ranked.zeroCount > 0 && (
              <p data-board-label="true" className="mt-auto pt-1">
                {t('statisticsPage.models.freeHint', { count: ranked.zeroCount })}
              </p>
            )}
          </>
        )}
      </BoardScreen>
    </BoardModule>
  )
}

function TokenFlowModule({ period, locale }: { period: DetailedStatisticsPeriod; locale: string }) {
  const { t } = useTranslation()
  const { summary } = period
  const uncachedInput = Math.max(0, summary.input_tokens - summary.cache_hit_tokens - summary.cache_miss_tokens)
  const segments = [
    { key: 'hit', label: t('statisticsPage.flow.cacheHit'), value: summary.cache_hit_tokens, color: 'var(--stats-series-0)' },
    { key: 'miss', label: t('statisticsPage.flow.cacheMiss'), value: summary.cache_miss_tokens, color: 'var(--stats-series-2)' },
    { key: 'none', label: t('statisticsPage.flow.noCacheInfo'), value: uncachedInput, color: 'var(--stats-series-4)' },
    { key: 'output', label: t('statisticsPage.flow.output'), value: summary.output_tokens, color: 'var(--stats-series-1)' },
  ]
  const total = summary.total_tokens

  return (
    <BoardModule
      code="FLOW"
      title={t('statisticsPage.modules.flow')}
      span={6}
      spanNarrow={12}
      tone={0}
      actions={<span data-board-label="true">{t('statisticsPage.flow.total', { tokens: formatNumber(total, locale) })}</span>}
    >
      <BoardScreen className="flex flex-col gap-3">
        {total === 0 ? (
          <EmptyState />
        ) : (
          <>
            <div data-stats-stack="true" data-size="large" role="img" aria-label={t('statisticsPage.modules.flow')}>
              {segments.map((segment) => (
                <span
                  key={segment.key}
                  title={`${segment.label} ${formatNumber(segment.value, locale)}`}
                  style={{ flexGrow: segment.value, background: segment.color }}
                />
              ))}
            </div>
            <div className="grid gap-x-6 sm:grid-cols-2">
              {segments.map((segment) => (
                <div key={segment.key} data-stats-row="true">
                  <Swatch color={segment.color} />
                  <span data-stats-row-name="true">{segment.label}</span>
                  <span data-board-number="true">{formatNumber(segment.value, locale)}</span>
                  <span data-board-label="true" className="w-12 text-right">
                    {formatPercent(segment.value / total)}
                  </span>
                </div>
              ))}
              <div data-stats-row="true">
                <span data-stats-row-name="true">{t('statisticsPage.flow.ioRatio')}</span>
                <span data-board-number="true">
                  {summary.output_tokens > 0 ? `${(summary.input_tokens / summary.output_tokens).toFixed(1)} : 1` : 'N/A'}
                </span>
              </div>
            </div>
          </>
        )}
      </BoardScreen>
    </BoardModule>
  )
}

function UnitCostModule({ period, locale, compare }: Omit<OverviewProps, 'onShowModels'>) {
  const { t } = useTranslation()
  const { summary } = period
  const previous = compare ? period.previous_summary : null
  const replyRate = (value: typeof summary) =>
    value.total_messages > 0 ? value.total_replies / value.total_messages : null
  const currentReplyRate = replyRate(summary)
  const previousReplyRate = previous ? replyRate(previous) : null
  const replyRateDelta: SummaryDelta | null =
    previous && currentReplyRate !== null && previousReplyRate !== null
      ? { value: relativeChange(currentReplyRate, previousReplyRate), upIsBad: false }
      : null
  const rows = [
    {
      label: t('statisticsPage.summary.costPerMessages'),
      value: formatCurrency(summary.cost_per_100_messages, locale, 4),
      delta: summaryDelta(summary, previous, 'cost_per_100_messages', true),
    },
    {
      label: t('statisticsPage.summary.costPerReceivedMessages'),
      value: formatCurrency(summary.cost_per_100_messages_excluding_replies, locale, 4),
      delta: summaryDelta(summary, previous, 'cost_per_100_messages_excluding_replies', true),
    },
    {
      label: t('statisticsPage.summary.costPerReplies'),
      value: formatCurrency(summary.cost_per_100_replies, locale, 4),
      delta: summaryDelta(summary, previous, 'cost_per_100_replies', true),
    },
    {
      label: t('statisticsPage.summary.costPerHour'),
      value: formatCurrency(summary.cost_per_hour, locale),
      delta: summaryDelta(summary, previous, 'cost_per_hour', true),
    },
    {
      label: t('statisticsPage.summary.tokensPerHour'),
      value: formatNumber(summary.tokens_per_hour, locale),
      delta: summaryDelta(summary, previous, 'tokens_per_hour', true),
    },
    {
      label: t('statisticsPage.summary.replyRate'),
      value: formatPercent(currentReplyRate),
      delta: replyRateDelta,
    },
  ]

  return (
    <BoardModule
      code="UNIT"
      title={t('statisticsPage.modules.unit')}
      span={6}
      spanNarrow={12}
      tone={1}
      actions={
        previous ? <span data-board-label="true">{t('statisticsPage.delta.vsPrevious')}</span> : undefined
      }
    >
      <BoardScreen>
        {rows.map((row) => (
          <div key={row.label} data-stats-row="true" className="text-sm">
            <span data-stats-row-name="true">{row.label}</span>
            <span data-board-number="true">{row.value}</span>
            <span className="inline-flex w-20 justify-end">
              <DeltaBadge delta={row.delta} />
            </span>
          </div>
        ))}
      </BoardScreen>
    </BoardModule>
  )
}
