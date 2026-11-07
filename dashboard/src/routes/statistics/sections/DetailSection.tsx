import { useMemo, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'

import { BoardModule, BoardRule, BoardScreen } from '@/components/ui/board'
import { Input } from '@/components/ui/input'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'

import {
  costPerMillionTokens,
  failureRate,
  FAILURE_RATE_ALERT,
  formatCurrency,
  formatNumber,
  formatPercent,
  seriesColor,
} from '../statistics-model'
import type { DetailedStatisticsBreakdown, DetailedStatisticsPeriod } from '../types'
import { EmptyState, OptionSwitch, Swatch } from './shared'

export type DetailDimension = 'models' | 'taskGroups' | 'modules' | 'requestTypes' | 'chats'

const DIMENSIONS: DetailDimension[] = ['models', 'taskGroups', 'modules', 'requestTypes', 'chats']

type ColumnGroup = 'token' | 'cache' | 'perf' | 'unit' | 'reply'
const COLUMN_GROUPS: ColumnGroup[] = ['token', 'cache', 'perf', 'unit', 'reply']
const DEFAULT_GROUPS: ColumnGroup[] = ['token', 'cache', 'perf']

type SortKey =
  | 'name'
  | 'requests'
  | 'tokens'
  | 'cacheRate'
  | 'cost'
  | 'failureRate'
  | 'time'
  | 'unitCost'
  | 'costPerCall'
  | 'callsPerReply'
  | 'tokensPerReply'

interface Column {
  key: SortKey
  group: ColumnGroup | null
  /** 嵌入模型没有输出和缓存，这些列不展示 */
  generationOnly?: boolean
}

const COLUMNS: Column[] = [
  { key: 'name', group: null },
  { key: 'requests', group: null },
  { key: 'tokens', group: 'token' },
  { key: 'cacheRate', group: 'cache', generationOnly: true },
  { key: 'cost', group: null },
  { key: 'failureRate', group: 'perf' },
  { key: 'time', group: 'perf' },
  { key: 'unitCost', group: 'unit' },
  { key: 'costPerCall', group: 'unit' },
  { key: 'callsPerReply', group: 'reply', generationOnly: true },
  { key: 'tokensPerReply', group: 'reply', generationOnly: true },
]

/** 排序时没有数值的行一律排在最后，不受升降序影响 */
function sortValue(row: DetailedStatisticsBreakdown, key: SortKey): number | string | null {
  switch (key) {
    case 'name':
      return row.name
    case 'requests':
      return row.request_count
    case 'tokens':
      return row.total_tokens
    case 'cacheRate':
      return row.cache_hit_rate
    case 'cost':
      return row.total_cost
    case 'failureRate':
      return failureRate(row)
    case 'time':
      return row.avg_time_cost
    case 'unitCost':
      return costPerMillionTokens(row.total_cost, row.total_tokens)
    case 'costPerCall':
      return row.request_count > 0 ? (row.total_cost / row.request_count) * 1000 : null
    case 'callsPerReply':
      return row.avg_calls_per_reply
    case 'tokensPerReply':
      return row.avg_tokens_per_reply
  }
}

function dimensionRows(period: DetailedStatisticsPeriod, dimension: DetailDimension) {
  switch (dimension) {
    case 'models':
      return period.models
    case 'taskGroups':
      return period.task_groups
    case 'modules':
      return period.modules
    case 'requestTypes':
      return period.request_types
    case 'chats':
      return []
  }
}

interface TableState {
  dimension: DetailDimension
  query: string
  sortKey: SortKey
  ascending: boolean
}

/** 同一个名称可能同时有生成和嵌入两行，展开状态需要区分 */
function rowKey(row: DetailedStatisticsBreakdown) {
  return `${row.is_embedding ? 'e' : 'g'}:${row.name}`
}

/** 外部（饼图图例）要求筛选明细表时传入；seq 变化才触发，避免重复点击同一项被忽略 */
export interface DetailFilterRequest {
  dimension: DetailDimension
  name: string
  seq: number
}

export function DetailSection({
  period,
  locale,
  filterRequest,
}: {
  period: DetailedStatisticsPeriod
  locale: string
  filterRequest: DetailFilterRequest | null
}) {
  const { t } = useTranslation()
  const [hideEmbedding, setHideEmbedding] = useState(false)
  const [groups, setGroups] = useState<Set<ColumnGroup>>(() => new Set(DEFAULT_GROUPS))
  const toggleGroup = (group: ColumnGroup) =>
    setGroups((current) => {
      const next = new Set(current)
      if (next.has(group)) next.delete(group)
      else next.add(group)
      return next
    })

  return (
    <>
      <BoardRule code="DETAIL">
        <span data-board-label="true">{t('statisticsPage.sections.breakdowns')}</span>
        <label className="flex items-center gap-1.5 text-xs">
          <input
            type="checkbox"
            checked={hideEmbedding}
            onChange={(event) => setHideEmbedding(event.target.checked)}
          />
          {t('statisticsPage.detail.hideEmbedding')}
        </label>
        <OptionSwitch
          label={t('statisticsPage.detail.columnGroups')}
          value={'' as ColumnGroup}
          onChange={toggleGroup}
          options={COLUMN_GROUPS.map((group) => ({
            value: group,
            label: t(`statisticsPage.detail.groups.${group}`),
          }))}
          multiple={groups}
        />
      </BoardRule>
      <div data-stats-tables="true">
        <DetailTable
          id="primary"
          initialDimension="models"
          period={period}
          locale={locale}
          groups={groups}
          hideEmbedding={hideEmbedding}
          filterRequest={filterRequest}
        />
        <DetailTable
          id="secondary"
          initialDimension="modules"
          period={period}
          locale={locale}
          groups={groups}
          hideEmbedding={hideEmbedding}
          filterRequest={null}
        />
      </div>
    </>
  )
}

function DetailTable({
  id,
  initialDimension,
  period,
  locale,
  groups,
  hideEmbedding,
  filterRequest,
}: {
  id: string
  initialDimension: DetailDimension
  period: DetailedStatisticsPeriod
  locale: string
  groups: Set<ColumnGroup>
  hideEmbedding: boolean
  filterRequest: DetailFilterRequest | null
}) {
  const { t } = useTranslation()
  const [state, setState] = useState<TableState>({
    dimension: initialDimension,
    query: '',
    sortKey: 'cost',
    ascending: false,
  })
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set())
  const [handledSeq, setHandledSeq] = useState(0)

  // 收到新的筛选请求时在渲染期同步状态，避免先渲染旧筛选再闪一下
  if (filterRequest && filterRequest.seq !== handledSeq) {
    setHandledSeq(filterRequest.seq)
    setState((current) => ({ ...current, dimension: filterRequest.dimension, query: filterRequest.name }))
    setExpanded(new Set())
  }

  const allRows = dimensionRows(period, state.dimension)
  const rows = useMemo(() => {
    const keyword = state.query.trim().toLowerCase()
    const filtered = allRows.filter(
      (row) => (!hideEmbedding || !row.is_embedding) && (!keyword || row.name.toLowerCase().includes(keyword))
    )
    return [...filtered].sort((left, right) => {
      const a = sortValue(left, state.sortKey)
      const b = sortValue(right, state.sortKey)
      if (a === null && b === null) return 0
      if (a === null) return 1
      if (b === null) return -1
      const order = typeof a === 'string' ? a.localeCompare(b as string) : a - (b as number)
      return state.ascending ? order : -order
    })
  }, [allRows, hideEmbedding, state.query, state.sortKey, state.ascending])

  const maxima = useMemo(
    () => ({
      requests: Math.max(0, ...allRows.map((row) => row.request_count)),
      cost: Math.max(0, ...allRows.map((row) => row.total_cost)),
    }),
    [allRows]
  )
  const totalCost = allRows.reduce((sum, row) => sum + row.total_cost, 0)
  const columns = COLUMNS.filter((column) => column.group === null || groups.has(column.group))

  const sortBy = (key: SortKey) =>
    setState((current) => ({
      ...current,
      sortKey: key,
      ascending: current.sortKey === key ? !current.ascending : key === 'name',
    }))

  const totals = rows.reduce(
    (sum, row) => ({
      requests: sum.requests + row.request_count,
      tokens: sum.tokens + row.total_tokens,
      cost: sum.cost + row.total_cost,
      hit: sum.hit + row.cache_hit_tokens,
      miss: sum.miss + row.cache_miss_tokens,
      attempts: sum.attempts + (row.attempt_count ?? 0),
      failures: sum.failures + (row.failure_count ?? 0),
    }),
    { requests: 0, tokens: 0, cost: 0, hit: 0, miss: 0, attempts: 0, failures: 0 }
  )

  return (
    <BoardModule
      code={id === 'primary' ? 'TABLE A' : 'TABLE B'}
      title={t(`statisticsPage.breakdowns.${state.dimension}`)}
      span={12}
      spanNarrow={12}
      tone={id === 'primary' ? 0 : 1}
      actions={
        <span data-board-label="true">
          {t('statisticsPage.detail.rowCount', {
            shown: state.dimension === 'chats' ? period.chats.length : rows.length,
            total: state.dimension === 'chats' ? period.chats.length : allRows.length,
          })}
        </span>
      }
    >
      <BoardScreen className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <OptionSwitch
            label={t('statisticsPage.detail.dimension')}
            value={state.dimension}
            onChange={(dimension) => {
              setState((current) => ({ ...current, dimension }))
              setExpanded(new Set())
            }}
            options={DIMENSIONS.map((value) => ({ value, label: t(`statisticsPage.breakdowns.${value}`) }))}
          />
          {(
            <Input
              value={state.query}
              onChange={(event) => setState((current) => ({ ...current, query: event.target.value }))}
              placeholder={t('statisticsPage.detail.search')}
              aria-label={t('statisticsPage.detail.search')}
              className="h-8 w-52"
            />
          )}
        </div>
        {state.dimension === 'chats' ? (
          <ChatRows period={period} locale={locale} query={state.query} />
        ) : rows.length === 0 ? (
          <EmptyState />
        ) : (
          <div data-stats-table-wrap="true">
            <Table className="min-w-[760px]">
              <TableHeader>
                <TableRow>
                  {columns.map((column) => {
                    const active = state.sortKey === column.key
                    return (
                      <TableHead
                        key={column.key}
                        data-sticky-name={column.key === 'name' ? 'true' : undefined}
                        aria-sort={active ? (state.ascending ? 'ascending' : 'descending') : 'none'}
                        className="whitespace-nowrap"
                      >
                        <button type="button" className="inline-flex items-center gap-1" onClick={() => sortBy(column.key)}>
                          {t(`statisticsPage.columns.${column.key}`)}
                          {active && <span aria-hidden="true">{state.ascending ? '▲' : '▼'}</span>}
                        </button>
                      </TableHead>
                    )
                  })}
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.map((row) => {
                  const key = rowKey(row)
                  const open = expanded.has(key)
                  return (
                    <DetailRow
                      key={key}
                      row={row}
                      index={allRows.indexOf(row)}
                      columns={columns}
                      open={open}
                      maxima={maxima}
                      totalCost={totalCost}
                      locale={locale}
                      onToggle={() =>
                        setExpanded((current) => {
                          const next = new Set(current)
                          if (next.has(key)) next.delete(key)
                          else next.add(key)
                          return next
                        })
                      }
                    />
                  )
                })}
              </TableBody>
              <tfoot>
                <TableRow>
                  {columns.map((column) => (
                    <TableCell key={column.key} data-sticky-name={column.key === 'name' ? 'true' : undefined} className="font-semibold">
                      {footerCell(column.key, totals, rows.length, locale, t)}
                    </TableCell>
                  ))}
                </TableRow>
              </tfoot>
            </Table>
          </div>
        )}
      </BoardScreen>
    </BoardModule>
  )
}

function footerCell(
  key: SortKey,
  totals: { requests: number; tokens: number; cost: number; hit: number; miss: number; attempts: number; failures: number },
  count: number,
  locale: string,
  t: (key: string, options?: Record<string, unknown>) => string
) {
  switch (key) {
    case 'name':
      return t('statisticsPage.detail.total', { count })
    case 'requests':
      return formatNumber(totals.requests, locale)
    case 'tokens':
      return formatNumber(totals.tokens, locale)
    case 'cacheRate':
      return totals.hit + totals.miss > 0 ? formatPercent(totals.hit / (totals.hit + totals.miss)) : 'N/A'
    case 'cost':
      return formatCurrency(totals.cost, locale)
    case 'failureRate':
      return totals.attempts > 0 ? formatPercent(totals.failures / totals.attempts) : 'N/A'
    case 'unitCost': {
      const unit = costPerMillionTokens(totals.cost, totals.tokens)
      return unit === null ? 'N/A' : formatCurrency(unit, locale)
    }
    case 'costPerCall':
      return totals.requests > 0 ? formatCurrency((totals.cost / totals.requests) * 1000, locale) : 'N/A'
    default:
      return ''
  }
}

function MiniBar({ ratio, alert = false }: { ratio: number; alert?: boolean }) {
  return (
    <div data-stats-bar="true" data-alert={alert ? 'true' : undefined} aria-hidden="true">
      <span style={{ width: `${Math.min(1, Math.max(0, ratio)) * 100}%` }} />
    </div>
  )
}

function DetailRow({
  row,
  index,
  columns,
  open,
  maxima,
  totalCost,
  locale,
  onToggle,
}: {
  row: DetailedStatisticsBreakdown
  index: number
  columns: Column[]
  open: boolean
  maxima: { requests: number; cost: number }
  totalCost: number
  locale: string
  onToggle: () => void
}) {
  const { t } = useTranslation()
  const rate = failureRate(row)
  const unit = costPerMillionTokens(row.total_cost, row.total_tokens)
  const cells: Record<SortKey, ReactNode> = {
    name: (
      <span className="inline-flex items-center gap-2">
        <Swatch color={seriesColor(index)} />
        <span className="max-w-64 truncate font-medium" title={row.name}>
          {row.name}
        </span>
        {row.is_embedding && <span className="text-muted-foreground text-xs">{t('statisticsPage.detail.embeddingTag')}</span>}
        {!row.is_embedding && row.total_cost === 0 && row.request_count > 0 && (
          <span className="text-muted-foreground text-xs">{t('statisticsPage.detail.freeTag')}</span>
        )}
      </span>
    ),
    requests: (
      <>
        <span className="tabular-nums">{formatNumber(row.request_count, locale)}</span>
        <MiniBar ratio={maxima.requests > 0 ? row.request_count / maxima.requests : 0} />
      </>
    ),
    tokens: (
      <>
        <span className="tabular-nums">{formatNumber(row.total_tokens, locale)}</span>
        <div className="text-muted-foreground text-xs tabular-nums">
          {formatNumber(row.input_tokens, locale)} / {formatNumber(row.output_tokens, locale)}
        </div>
      </>
    ),
    cacheRate:
      row.cache_hit_rate === null ? (
        <span className="text-muted-foreground">N/A</span>
      ) : (
        <>
          <span className="tabular-nums">{formatPercent(row.cache_hit_rate)}</span>
          <MiniBar ratio={row.cache_hit_rate} alert={row.cache_hit_rate < 0.3} />
        </>
      ),
    cost:
      row.total_cost === 0 ? (
        <span className="text-muted-foreground tabular-nums">{formatCurrency(0, locale)}</span>
      ) : (
        <>
          <span className="font-semibold tabular-nums">{formatCurrency(row.total_cost, locale)}</span>
          <span className="text-muted-foreground ml-1.5 text-xs tabular-nums">
            {formatPercent(totalCost > 0 ? row.total_cost / totalCost : null)}
          </span>
          <MiniBar ratio={maxima.cost > 0 ? row.total_cost / maxima.cost : 0} />
        </>
      ),
    failureRate:
      rate === null ? (
        <span className="text-muted-foreground">N/A</span>
      ) : (
        <>
          <span
            className="tabular-nums"
            data-stats-alert-text={rate >= FAILURE_RATE_ALERT ? 'true' : undefined}
          >
            {formatPercent(rate)}
          </span>
          <div className="text-muted-foreground text-xs tabular-nums">
            {t('statisticsPage.detail.failureCount', { value: formatNumber(row.failure_count ?? 0, locale) })}
          </div>
        </>
      ),
    time: (
      <span className="whitespace-nowrap tabular-nums">
        {row.avg_time_cost.toFixed(1)}s ± {row.std_time_cost.toFixed(1)}
      </span>
    ),
    unitCost: <span className="tabular-nums">{unit === null ? 'N/A' : formatCurrency(unit, locale)}</span>,
    costPerCall: (
      <span className="tabular-nums">
        {row.request_count > 0 ? formatCurrency((row.total_cost / row.request_count) * 1000, locale) : 'N/A'}
      </span>
    ),
    callsPerReply: (
      <span className="tabular-nums">
        {row.avg_calls_per_reply === null ? 'N/A' : formatNumber(row.avg_calls_per_reply, locale)}
      </span>
    ),
    tokensPerReply: (
      <span className="tabular-nums">
        {row.avg_tokens_per_reply === null ? 'N/A' : formatNumber(row.avg_tokens_per_reply, locale)}
      </span>
    ),
  }

  return (
    <>
      <TableRow className="cursor-pointer" aria-expanded={open} onClick={onToggle}>
        {columns.map((column) => (
          <TableCell
            key={column.key}
            data-sticky-name={column.key === 'name' ? 'true' : undefined}
            className="whitespace-nowrap"
          >
            {column.generationOnly && row.is_embedding ? (
              <span className="text-muted-foreground">—</span>
            ) : (
              cells[column.key]
            )}
          </TableCell>
        ))}
      </TableRow>
      {open && (
        <TableRow>
          <TableCell colSpan={columns.length}>
            <dl className="grid grid-cols-2 gap-3 text-xs sm:grid-cols-3 lg:grid-cols-6">
              {[
                [t('statisticsPage.table.inputTokens'), formatNumber(row.input_tokens, locale)],
                [t('statisticsPage.table.outputTokens'), formatNumber(row.output_tokens, locale)],
                [t('statisticsPage.table.cacheHitTokens'), formatNumber(row.cache_hit_tokens, locale)],
                [t('statisticsPage.table.cacheMissTokens'), formatNumber(row.cache_miss_tokens, locale)],
                [
                  t('statisticsPage.table.tokensPerCall'),
                  row.avg_tokens_per_call === null ? 'N/A' : formatNumber(row.avg_tokens_per_call, locale),
                ],
                [
                  t('statisticsPage.detail.attemptsFailures'),
                  row.attempt_count === null
                    ? 'N/A'
                    : `${formatNumber(row.failure_count ?? 0, locale)} / ${formatNumber(row.attempt_count, locale)}`,
                ],
              ].map(([label, value]) => (
                <div key={label}>
                  <dt className="text-muted-foreground">{label}</dt>
                  <dd className="mt-0.5 text-sm tabular-nums">{value}</dd>
                </div>
              ))}
            </dl>
          </TableCell>
        </TableRow>
      )}
    </>
  )
}

function ChatRows({
  period,
  locale,
  query,
}: {
  period: DetailedStatisticsPeriod
  locale: string
  query: string
}) {
  const { t } = useTranslation()
  const keyword = query.trim().toLowerCase()
  const chats = period.chats
    .filter((chat) => !keyword || chat.name.toLowerCase().includes(keyword))
    .sort((a, b) => b.message_count - a.message_count)
  if (chats.length === 0) return <EmptyState />
  return (
    <div data-stats-table-wrap="true">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>{t('statisticsPage.table.chat')}</TableHead>
            <TableHead className="text-right">{t('statisticsPage.table.messages')}</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {chats.map((chat) => (
            <TableRow key={chat.name}>
              <TableCell className="break-words font-medium [overflow-wrap:anywhere]">{chat.name}</TableCell>
              <TableCell className="text-right tabular-nums">{formatNumber(chat.message_count, locale)}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  )
}
