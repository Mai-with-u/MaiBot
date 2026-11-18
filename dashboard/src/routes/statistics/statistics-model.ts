import type {
  DetailedChatStatistics,
  DetailedStatisticsBreakdown,
  DetailedStatisticsPeriod,
  DetailedStatisticsSummary,
} from './types'

export const PERIOD_KEYS = [
  'all_time',
  'last_30_days',
  'last_7_days',
  'last_3_days',
  'last_24_hours',
  'last_3_hours',
  'last_hour',
  'last_15_minutes',
] as const

/** 时段按钮上的短代号，与首页 24H / 7D / 30D 拨杆保持同一种写法 */
export const PERIOD_CODES: Record<string, string> = {
  all_time: 'ALL',
  last_30_days: '30D',
  last_7_days: '7D',
  last_3_days: '3D',
  last_24_hours: '24H',
  last_3_hours: '3H',
  last_hour: '1H',
  last_15_minutes: '15M',
}

/** 系列配色数量；具体颜色由 statistics.css 按界面风格提供 */
export const SERIES_COLOR_COUNT = 8

export function seriesColor(index: number): string {
  return `var(--stats-series-${index % SERIES_COLOR_COUNT})`
}

export const OTHER_SERIES_COLOR = 'var(--stats-series-other)'

/** 失败率达到这一阈值即标为异常 */
export const FAILURE_RATE_ALERT = 0.05

export function formatNumber(value: number, locale: string, maximumFractionDigits = 2): string {
  return new Intl.NumberFormat(locale, {
    notation: Math.abs(value) >= 10_000 ? 'compact' : 'standard',
    maximumFractionDigits,
  }).format(value)
}

export function formatCurrency(value: number, locale: string, digits = 2): string {
  return `¥${new Intl.NumberFormat(locale, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(value)}`
}

export function formatPercent(value: number | null, digits = 1): string {
  return value === null ? 'N/A' : `${(value * 100).toFixed(digits)}%`
}

export function formatSeconds(value: number | null): string {
  return value === null ? 'N/A' : `${value.toFixed(1)}s`
}

export function formatDuration(seconds: number, locale: string): string {
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

export function formatDateTime(value: string, locale: string): string {
  return new Date(value).toLocaleString(locale, {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
}

/** 相对上一周期的变化率；上一周期为 0 时无法比较，返回 null */
export function relativeChange(current: number, previous: number | undefined): number | null {
  if (previous === undefined || previous === 0) return null
  return (current - previous) / previous
}

export function getOrderedPeriods(periods: DetailedStatisticsPeriod[]): DetailedStatisticsPeriod[] {
  const order = new Map<string, number>(PERIOD_KEYS.map((key, index) => [key, index]))
  return [...periods].sort((left, right) => (order.get(left.key) ?? 99) - (order.get(right.key) ?? 99))
}

export interface RankedItem {
  name: string
  value: number
  color: string
  /** 合并后的「其他」项 */
  other: boolean
}

export interface RankedItems {
  items: RankedItem[]
  total: number
  /** 被合并进「其他」的项数 */
  otherCount: number
  /** 数值为 0 的项数（不参与排名） */
  zeroCount: number
}

/** 按数值降序取前 limit 项，其余合并为一项「其他」；颜色按排名固定，图与图例一致 */
export function rankWithOther(
  entries: Array<{ name: string; value: number }>,
  limit: number
): RankedItems {
  const positive = entries.filter((entry) => entry.value > 0).sort((a, b) => b.value - a.value)
  const total = positive.reduce((sum, entry) => sum + entry.value, 0)
  const head = positive.slice(0, limit)
  const rest = positive.slice(limit)
  const items: RankedItem[] = head.map((entry, index) => ({
    ...entry,
    color: seriesColor(index),
    other: false,
  }))
  if (rest.length > 0) {
    items.push({
      name: '',
      value: rest.reduce((sum, entry) => sum + entry.value, 0),
      color: OTHER_SERIES_COLOR,
      other: true,
    })
  }
  return { items, total, otherCount: rest.length, zeroCount: entries.length - positive.length }
}

export function failureRate(row: Pick<DetailedStatisticsBreakdown, 'attempt_count' | 'failure_count'>) {
  if (row.attempt_count === null || row.failure_count === null || row.attempt_count === 0) return null
  return row.failure_count / row.attempt_count
}

/** 每百万 Token 花费；没有 Token 时返回 null */
export function costPerMillionTokens(cost: number, tokens: number): number | null {
  return tokens > 0 ? (cost / tokens) * 1_000_000 : null
}

export type InsightTone = 'alert' | 'notice' | 'good'

export interface Insight {
  id: string
  tone: InsightTone
  /** statisticsPage.insights 下的文案键 */
  key: string
  params: Record<string, string>
}

interface InsightContext {
  period: DetailedStatisticsPeriod
  locale: string
}

/**
 * 根据当前时段生成文字洞察。每条规则只在数据足以支撑结论时输出，
 * 阈值写在规则里，便于对照文案理解。
 */
export function buildInsights({ period, locale }: InsightContext): Insight[] {
  const insights: Insight[] = []
  const summary = period.summary
  const previous = period.previous_summary
  const models = period.models.filter((row) => !row.is_embedding)
  const percent = (value: number) => formatPercent(value)

  // 环比：花费变化超过 10% 才提示
  if (previous) {
    const costChange = relativeChange(summary.total_cost, previous.total_cost)
    if (costChange !== null && Math.abs(costChange) >= 0.1) {
      insights.push({
        id: 'cost-change',
        tone: costChange > 0 ? 'alert' : 'good',
        key: costChange > 0 ? 'costUp' : 'costDown',
        params: {
          change: percent(Math.abs(costChange)),
          current: formatCurrency(summary.total_cost, locale),
          previous: formatCurrency(previous.total_cost, locale),
        },
      })
    }
  }

  // 失败率：至少 20 次尝试才判断，避免小样本误报
  const failing = period.reliability.by_model
    .filter((item) => item.attempt_count >= 20 && item.failure_rate !== null)
    .sort((a, b) => (b.failure_rate ?? 0) - (a.failure_rate ?? 0))[0]
  if (failing && (failing.failure_rate ?? 0) >= FAILURE_RATE_ALERT) {
    insights.push({
      id: 'failure-rate',
      tone: 'alert',
      key: 'failureRate',
      params: {
        name: failing.name,
        rate: percent(failing.failure_rate ?? 0),
        failures: formatNumber(failing.failure_count, locale),
        attempts: formatNumber(failing.attempt_count, locale),
        overall: formatPercent(period.reliability.failure_rate),
      },
    })
  }

  // 失败集中：单个模型贡献三成以上失败
  const totalFailures = period.reliability.failure_count
  const mostFailures = [...period.reliability.by_model].sort((a, b) => b.failure_count - a.failure_count)[0]
  if (mostFailures && totalFailures > 0 && mostFailures.failure_count / totalFailures >= 0.3) {
    insights.push({
      id: 'failure-share',
      tone: 'alert',
      key: 'failureShare',
      params: {
        name: mostFailures.name,
        failures: formatNumber(mostFailures.failure_count, locale),
        share: percent(mostFailures.failure_count / totalFailures),
      },
    })
  }

  // 成本集中：单个模型占四成以上花费
  const topCost = [...models].sort((a, b) => b.total_cost - a.total_cost)[0]
  if (topCost && summary.total_cost > 0 && topCost.total_cost / summary.total_cost >= 0.4) {
    insights.push({
      id: 'cost-share',
      tone: 'notice',
      key: 'costShare',
      params: {
        name: topCost.name,
        share: percent(topCost.total_cost / summary.total_cost),
        cost: formatCurrency(topCost.total_cost, locale),
      },
    })
  }

  // 缓存命中偏低：缓存 Token 占比不低于 5%，且命中率不到整体的六成
  const overallHitRate = summary.cache_hit_rate
  const cacheTokenTotal = summary.cache_hit_tokens + summary.cache_miss_tokens
  if (overallHitRate !== null && cacheTokenTotal > 0) {
    const lowCache = models
      .filter(
        (row) =>
          row.cache_hit_rate !== null &&
          (row.cache_hit_tokens + row.cache_miss_tokens) / cacheTokenTotal >= 0.05
      )
      .sort((a, b) => (a.cache_hit_rate ?? 0) - (b.cache_hit_rate ?? 0))[0]
    if (lowCache && (lowCache.cache_hit_rate ?? 0) < overallHitRate * 0.6) {
      insights.push({
        id: 'cache-low',
        tone: 'alert',
        key: 'cacheLow',
        params: {
          name: lowCache.name,
          rate: formatPercent(lowCache.cache_hit_rate),
          overall: formatPercent(overallHitRate),
        },
      })
    }
  }

  // 单价离群：付费模型的每百万 Token 花费达到全局均价 5 倍
  const averageUnit = costPerMillionTokens(summary.total_cost, summary.total_tokens)
  if (averageUnit !== null && averageUnit > 0) {
    const priciest = models
      .map((row) => ({ row, unit: costPerMillionTokens(row.total_cost, row.total_tokens) }))
      .filter((entry): entry is { row: DetailedStatisticsBreakdown; unit: number } => entry.unit !== null)
      .sort((a, b) => b.unit - a.unit)[0]
    if (priciest && priciest.unit / averageUnit >= 5) {
      insights.push({
        id: 'unit-price',
        tone: 'notice',
        key: 'unitPrice',
        params: {
          name: priciest.row.name,
          unit: formatCurrency(priciest.unit, locale),
          ratio: (priciest.unit / averageUnit).toFixed(0),
        },
      })
    }
  }

  // 耗时长尾：至少 50 次调用，标准差达到均值 2 倍
  const volatile = models
    .filter((row) => row.request_count >= 50 && row.avg_time_cost > 0)
    .sort((a, b) => b.std_time_cost / b.avg_time_cost - a.std_time_cost / a.avg_time_cost)[0]
  if (volatile && volatile.std_time_cost / volatile.avg_time_cost >= 2) {
    insights.push({
      id: 'latency',
      tone: 'notice',
      key: 'latencyTail',
      params: {
        name: volatile.name,
        std: formatSeconds(volatile.std_time_cost),
        avg: formatSeconds(volatile.avg_time_cost),
      },
    })
  }

  // 最活跃的聊天流
  const topChat = [...period.chats].sort(
    (a: DetailedChatStatistics, b: DetailedChatStatistics) => b.message_count - a.message_count
  )[0]
  if (topChat && summary.total_messages > 0) {
    insights.push({
      id: 'top-chat',
      tone: 'good',
      key: 'topChat',
      params: {
        name: topChat.name,
        share: percent(topChat.message_count / summary.total_messages),
        messages: formatNumber(topChat.message_count, locale),
      },
    })
  }

  return insights
}

export interface SummaryDelta {
  value: number | null
  /** 上升是否算坏事（花费、Token 上升标红） */
  upIsBad: boolean
}

export function summaryDelta(
  summary: DetailedStatisticsSummary,
  previous: DetailedStatisticsSummary | null,
  key: keyof DetailedStatisticsSummary,
  upIsBad: boolean
): SummaryDelta | null {
  if (!previous) return null
  const current = summary[key]
  const before = previous[key]
  if (current === null || before === null) return null
  return { value: relativeChange(current, before), upIsBad }
}
