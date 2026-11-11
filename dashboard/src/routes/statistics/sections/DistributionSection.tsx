import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Cell, Pie, PieChart, Tooltip } from 'recharts'

import { BoardModule, BoardRule, BoardScreen } from '@/components/ui/board'
import { ChartContainer } from '@/components/ui/chart'

import {
  formatCurrency,
  formatNumber,
  formatPercent,
  rankWithOther,
  type RankedItems,
} from '../statistics-model'
import type { DetailedDistributionItem, DetailedStatisticsPeriod } from '../types'
import type { DetailDimension } from './DetailSection'
import { EmptyState, OptionSwitch, Swatch } from './shared'

type DistributionMetric = 'costs' | 'tokens'

/** 环形图单独展示的扇区数，其余合并为「其他」 */
const PIE_SLICE_LIMIT = 7

interface PieDefinition {
  key: string
  code: string
  /** 点击图例时联动的明细表维度；调用来源没有对应的明细表 */
  dimension: DetailDimension | null
  entries: DetailedDistributionItem[]
}

function buildPies(period: DetailedStatisticsPeriod, metric: DistributionMetric): PieDefinition[] {
  const { distributions } = period
  const taskEntries = period.task_groups.map((row) => ({
    name: row.name,
    value: metric === 'costs' ? row.total_cost : row.total_tokens,
  }))
  return [
    { key: 'owner', code: 'SOURCE', dimension: null, entries: distributions[`owner_${metric}`] },
    { key: 'model', code: 'MODEL', dimension: 'models', entries: distributions[`model_${metric}`] },
    { key: 'module', code: 'MODULE', dimension: 'modules', entries: distributions[`module_${metric}`] },
    {
      key: 'requestType',
      code: 'REQUEST',
      dimension: 'requestTypes',
      entries: distributions[`request_type_${metric}`],
    },
    { key: 'taskGroup', code: 'TASK', dimension: 'taskGroups', entries: taskEntries },
    { key: 'chat', code: 'CHAT', dimension: 'chats', entries: distributions[`chat_${metric}`] },
  ]
}

export function DistributionSection({
  period,
  locale,
  onSelect,
}: {
  period: DetailedStatisticsPeriod
  locale: string
  onSelect: (dimension: DetailDimension, name: string) => void
}) {
  const { t } = useTranslation()
  const [metric, setMetric] = useState<DistributionMetric>('costs')
  const pies = buildPies(period, metric)
  const formatValue = (value: number) =>
    metric === 'costs' ? formatCurrency(value, locale) : formatNumber(value, locale)

  return (
    <>
      <BoardRule code="BREAKDOWN">
        <span data-board-label="true">{t('statisticsPage.sections.distributions')}</span>
        <span data-board-label="true" className="max-md:hidden">
          {t('statisticsPage.distributions.hint')}
        </span>
        <OptionSwitch
          label={t('statisticsPage.distributions.metric')}
          value={metric}
          onChange={setMetric}
          options={(['costs', 'tokens'] as const).map((value) => ({
            value,
            label: t(`statisticsPage.distributions.${value}`),
          }))}
        />
      </BoardRule>
      <div data-stats-pies="true">
        {pies.map((pie, index) => (
          <PieModule
            key={pie.key}
            pie={pie}
            tone={index}
            title={t(`statisticsPage.distributions.titles.${pie.key}`, {
              metric: t(`statisticsPage.distributions.${metric}`),
            })}
            totalLabel={t(`statisticsPage.distributions.totals.${metric}`)}
            ranked={rankWithOther(pie.entries, PIE_SLICE_LIMIT)}
            formatValue={formatValue}
            onSelect={onSelect}
          />
        ))}
      </div>
    </>
  )
}

function PieModule({
  pie,
  tone,
  title,
  totalLabel,
  ranked,
  formatValue,
  onSelect,
}: {
  pie: PieDefinition
  tone: number
  title: string
  totalLabel: string
  ranked: RankedItems
  formatValue: (value: number) => string
  onSelect: (dimension: DetailDimension, name: string) => void
}) {
  const { t } = useTranslation()
  const [activeIndex, setActiveIndex] = useState<number | null>(null)
  const label = (index: number) =>
    ranked.items[index].other ? t('statisticsPage.other', { count: ranked.otherCount }) : ranked.items[index].name
  const single = ranked.items.length === 1 && !ranked.items[0].other

  return (
    <BoardModule
      code={pie.code}
      title={title}
      span={1}
      spanNarrow={1}
      tone={tone}
      actions={
        pie.entries.length > 1 ? (
          <span data-board-label="true">{t('statisticsPage.distributions.itemCount', { count: pie.entries.length })}</span>
        ) : undefined
      }
    >
      <BoardScreen>
        {ranked.items.length === 0 ? (
          <EmptyState />
        ) : (
          <div data-stats-pie="true">
            <div data-stats-pie-chart="true">
              <ChartContainer config={{}} className="aspect-square h-full w-full">
                <PieChart>
                  <Tooltip
                    isAnimationActive={false}
                    content={({ active, payload }) => {
                      if (!active || !payload?.length) return null
                      const item = payload[0]
                      const value = Number(item.value)
                      return (
                        <div className="border-border/50 bg-background text-foreground rounded-md border px-2.5 py-1.5 text-xs shadow-xl">
                          <div className="font-medium">{item.name}</div>
                          <div className="font-mono tabular-nums">
                            {formatValue(value)} · {formatPercent(value / ranked.total)}
                          </div>
                        </div>
                      )
                    }}
                  />
                  <Pie
                    data={ranked.items.map((item, index) => ({ name: label(index), value: item.value }))}
                    dataKey="value"
                    nameKey="name"
                    innerRadius="58%"
                    outerRadius="92%"
                    paddingAngle={ranked.items.length > 1 ? 1 : 0}
                    stroke="none"
                    isAnimationActive={false}
                  >
                    {ranked.items.map((item, index) => (
                      <Cell
                        key={item.name || 'other'}
                        fill={item.color}
                        fillOpacity={activeIndex === null || activeIndex === index ? 1 : 0.25}
                      />
                    ))}
                  </Pie>
                </PieChart>
              </ChartContainer>
              <div data-stats-pie-center="true">
                <span>{totalLabel}</span>
                <strong>{formatValue(ranked.total)}</strong>
              </div>
            </div>
            <div data-stats-pie-legend="true" data-chart-legend="true">
              {ranked.items.map((item, index) => {
                const content = (
                  <>
                    <Swatch color={item.color} />
                    <span data-stats-row-name="true" title={label(index)}>
                      {label(index)}
                    </span>
                    <span data-board-number="true">{formatValue(item.value)}</span>
                    <span data-board-label="true" className="w-12 text-right">
                      {formatPercent(item.value / ranked.total)}
                    </span>
                  </>
                )
                const active = activeIndex === null || activeIndex === index ? 'true' : 'false'
                // 「其他」和调用来源没有可筛选的明细行，只做高亮
                return pie.dimension && !item.other ? (
                  <button
                    key={item.name}
                    type="button"
                    data-stats-row="true"
                    data-active={active}
                    title={t('statisticsPage.distributions.filterTable', { name: item.name })}
                    onMouseEnter={() => setActiveIndex(index)}
                    onMouseLeave={() => setActiveIndex(null)}
                    onFocus={() => setActiveIndex(index)}
                    onBlur={() => setActiveIndex(null)}
                    onClick={() => onSelect(pie.dimension as DetailDimension, item.name)}
                  >
                    {content}
                  </button>
                ) : (
                  <div
                    key={item.name || 'other'}
                    data-stats-row="true"
                    data-active={active}
                    onMouseEnter={() => setActiveIndex(index)}
                    onMouseLeave={() => setActiveIndex(null)}
                  >
                    {content}
                  </div>
                )
              })}
              {single && (
                <p data-board-label="true" className="pt-2">
                  {t('statisticsPage.distributions.singleSource', { name: ranked.items[0].name })}
                </p>
              )}
              {ranked.zeroCount > 0 && (
                <p data-board-label="true" className="pt-1">
                  {t('statisticsPage.distributions.zeroCount', { count: ranked.zeroCount })}
                </p>
              )}
            </div>
          </div>
        )}
      </BoardScreen>
    </BoardModule>
  )
}
