import { motion } from 'motion/react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Cell, Pie, PieChart, Tooltip } from 'recharts'

import { BoardGrid, BoardModule, BoardRule, BoardScreen } from '@/components/ui/board'
import { ChartContainer, type ChartConfig } from '@/components/ui/chart'
import { useMotion } from '@/lib/motion'

import {
  formatCurrency,
  formatNumber,
  formatPercent,
  rankWithOther,
  type RankedItems,
} from '../statistics-model'
import type { DetailedDistributionItem, DetailedStatisticsPeriod } from '../types'
import type { DetailDimension } from './DetailSection'
import { ChartTooltipBox, EmptyState, OptionSwitch, Swatch } from './shared'

type DistributionMetric = 'costs' | 'tokens'
type DistributionKey = 'module' | 'taskGroup' | 'requestType' | 'chat' | 'model' | 'owner'

/** 环形图单独展示的扇区数，其余合并为「其他」（最多 6 块扇区） */
const PIE_SLICE_LIMIT = 5

interface PieDefinition {
  key: DistributionKey
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
    { key: 'module', code: 'MODULE', dimension: 'modules', entries: distributions[`module_${metric}`] },
    { key: 'taskGroup', code: 'TASK', dimension: 'taskGroups', entries: taskEntries },
    {
      key: 'requestType',
      code: 'REQUEST',
      dimension: 'requestTypes',
      entries: distributions[`request_type_${metric}`],
    },
    { key: 'chat', code: 'CHAT', dimension: 'chats', entries: distributions[`chat_${metric}`] },
    { key: 'model', code: 'MODEL', dimension: 'models', entries: distributions[`model_${metric}`] },
    { key: 'owner', code: 'SOURCE', dimension: null, entries: distributions[`owner_${metric}`] },
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
  const [selectedKey, setSelectedKey] = useState<DistributionKey>('module')
  const pies = buildPies(period, metric)
  const activePie = pies.find((item) => item.key === selectedKey) ?? pies[0]
  const formatValue = (value: number) =>
    metric === 'costs' ? formatCurrency(value, locale) : formatNumber(value, locale)
  const dimensionLabel = (key: DistributionKey) =>
    t(`statisticsPage.distributions.titles.${key}`, { metric: '' }).replace(/\s{2,}/g, ' ').trim()

  return (
    <>
      <BoardRule code="BREAKDOWN">
        <span data-board-label="true" className="shrink-0 whitespace-nowrap">
          {t('statisticsPage.sections.distributions')}
        </span>
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
      <BoardGrid>
        <PieModule
          pie={activePie}
          pies={pies}
          selectedKey={activePie.key}
          transitionKey={`${metric}:${activePie.key}`}
          onSelectDimension={setSelectedKey}
          dimensionLabel={dimensionLabel}
          title={t(`statisticsPage.distributions.titles.${activePie.key}`, {
            metric: t(`statisticsPage.distributions.${metric}`),
          })}
          totalLabel={t(`statisticsPage.distributions.totals.${metric}`)}
          ranked={rankWithOther(activePie.entries, PIE_SLICE_LIMIT)}
          formatValue={formatValue}
          onSelect={onSelect}
        />
      </BoardGrid>
    </>
  )
}

function PieModule({
  pie,
  pies,
  selectedKey,
  transitionKey,
  onSelectDimension,
  dimensionLabel,
  title,
  totalLabel,
  ranked,
  formatValue,
  onSelect,
}: {
  pie: PieDefinition
  pies: PieDefinition[]
  selectedKey: DistributionKey
  transitionKey: string
  onSelectDimension: (key: DistributionKey) => void
  dimensionLabel: (key: DistributionKey) => string
  title: string
  totalLabel: string
  ranked: RankedItems
  formatValue: (value: number) => string
  onSelect: (dimension: DetailDimension, name: string) => void
}) {
  const { t } = useTranslation()
  const { enabled, transitions } = useMotion()
  const [hovered, setHovered] = useState<{ key: string; index: number } | null>(null)
  const activeIndex = hovered?.key === transitionKey ? hovered.index : null
  const setActiveIndex = (index: number | null) =>
    setHovered(index === null ? null : { key: transitionKey, index })
  const label = (index: number) =>
    ranked.items[index].other ? t('statisticsPage.other', { count: ranked.otherCount }) : ranked.items[index].name
  const single = ranked.items.length === 1 && !ranked.items[0].other
  const chartConfig = Object.fromEntries(
    ranked.items.map((item, index) => [
      item.other ? 'other' : `slice-${index}`,
      { label: label(index), color: item.color },
    ])
  ) satisfies ChartConfig

  return (
    <BoardModule
      code={pie.code}
      title={title}
      span={12}
      spanNarrow={12}
      tone={0}
      actions={
        pie.entries.length > 1 ? (
          <span data-board-label="true">{t('statisticsPage.distributions.itemCount', { count: pie.entries.length })}</span>
        ) : undefined
      }
    >
      <BoardScreen className="flex flex-col gap-4">
        <OptionSwitch
          label={t('statisticsPage.sections.distributions')}
          value={selectedKey}
          onChange={onSelectDimension}
          options={pies.map((item) => ({
            value: item.key,
            label: dimensionLabel(item.key),
          }))}
        />
        {ranked.items.length === 0 ? (
          <EmptyState />
        ) : (
          <motion.div
            key={transitionKey}
            initial={enabled ? { opacity: 0, y: 4 } : false}
            animate={{ opacity: 1, y: 0 }}
            transition={transitions.fast}
            data-stats-pie="true"
            className="w-full max-w-4xl gap-6"
          >
            <div data-stats-pie-chart="true">
              <ChartContainer config={chartConfig} className="aspect-square h-full w-full">
                <PieChart>
                  <Tooltip
                    isAnimationActive={false}
                    content={({ active, payload }) => {
                      if (!active || !payload?.length) return null
                      const item = payload[0]
                      const value = Number(item.value)
                      return (
                        <ChartTooltipBox>
                          <div className="font-medium">{item.name}</div>
                          <div className="font-medium tabular-nums">
                            {formatValue(value)} · {formatPercent(value / ranked.total)}
                          </div>
                        </ChartTooltipBox>
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
                <strong className="whitespace-nowrap tabular-nums">{formatValue(ranked.total)}</strong>
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
                    <span data-board-number="true" className="whitespace-nowrap tabular-nums">
                      {formatValue(item.value)}
                    </span>
                    <span data-board-label="true" className="w-12 shrink-0 whitespace-nowrap text-right tabular-nums">
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
          </motion.div>
        )}
      </BoardScreen>
    </BoardModule>
  )
}
