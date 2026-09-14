import { describe, expect, expectTypeOf, it } from 'vitest'

import type {
  DetailedChatStatistics,
  DetailedDistributionItem,
  DetailedStatisticsBreakdown,
  DetailedStatisticsData,
  DetailedStatisticsDistributions,
  DetailedStatisticsMetricsData,
  DetailedStatisticsPeriod,
  DetailedStatisticsSummary,
  DetailedStatisticsTrendData,
} from './types'

function makeSummary(
  overrides: Partial<DetailedStatisticsSummary> = {}
): DetailedStatisticsSummary {
  return {
    online_time: 0,
    total_messages: 0,
    total_replies: 0,
    total_requests: 0,
    total_tokens: 0,
    input_tokens: 0,
    output_tokens: 0,
    cache_hit_tokens: 0,
    cache_miss_tokens: 0,
    cache_hit_rate: null,
    total_cost: 0,
    cost_per_100_messages: 0,
    cost_per_100_messages_excluding_replies: 0,
    cost_per_100_replies: 0,
    cost_per_hour: 0,
    tokens_per_hour: 0,
    ...overrides,
  }
}

function makeBreakdown(
  overrides: Partial<DetailedStatisticsBreakdown> = {}
): DetailedStatisticsBreakdown {
  return {
    name: 'unknown',
    request_count: 0,
    input_tokens: 0,
    output_tokens: 0,
    total_tokens: 0,
    cache_hit_tokens: 0,
    cache_miss_tokens: 0,
    cache_hit_rate: null,
    total_cost: 0,
    avg_time_cost: 0,
    std_time_cost: 0,
    avg_calls_per_reply: null,
    avg_tokens_per_reply: null,
    avg_tokens_per_call: null,
    ...overrides,
  }
}

describe('DetailedStatistics 类型契约', () => {
  it('允许缓存命中率与人均指标为 null，并接受空集合', () => {
    const summary = makeSummary({ cache_hit_rate: null })
    const breakdown = makeBreakdown({
      cache_hit_rate: null,
      avg_calls_per_reply: null,
      avg_tokens_per_reply: null,
      avg_tokens_per_call: null,
    })
    const chat: DetailedChatStatistics = { name: '空聊天', message_count: 0 }
    const item: DetailedDistributionItem = { name: '空分布', value: 0 }
    const distributions: DetailedStatisticsDistributions = {
      owner_costs: [],
      model_costs: [item],
      module_costs: [],
      request_type_costs: [],
      chat_messages: [],
      chat_costs: [],
    }
    const trend: DetailedStatisticsTrendData = {
      time_labels: [],
      total_cost_data: [],
      cost_by_model: {},
      cost_by_module: {},
      message_by_chat: {},
    }
    const metrics: DetailedStatisticsMetricsData = {
      time_labels: [],
      cost_per_100_messages: [],
      cost_per_hour: [],
      tokens_per_hour: [],
      cost_per_100_replies: [],
    }
    const period: DetailedStatisticsPeriod = {
      key: 'all_time',
      start_time: '2026-01-01T00:00:00',
      end_time: '2026-01-02T00:00:00',
      summary,
      models: [breakdown],
      modules: [],
      request_types: [],
      chats: [chat],
      distributions,
    }
    const data: DetailedStatisticsData = {
      generated_at: '2026-01-02T00:00:00',
      periods: [period],
      trends: { '24h': trend },
      metrics: { '7d': metrics },
    }

    expect(data.periods[0].summary.cache_hit_rate).toBeNull()
    expect(data.periods[0].models[0].avg_calls_per_reply).toBeNull()
    expect(data.periods[0].models[0].avg_tokens_per_reply).toBeNull()
    expect(data.periods[0].models[0].avg_tokens_per_call).toBeNull()
    expect(data.periods[0].chats[0].message_count).toBe(0)
    expect(data.periods[0].distributions.model_costs).toEqual([item])
    expect(data.trends['24h'].cost_by_model).toEqual({})
    expect(data.metrics['7d'].tokens_per_hour).toEqual([])
  })

  it('命中率字段既可以是数字也可以是 null', () => {
    expectTypeOf<DetailedStatisticsSummary['cache_hit_rate']>().toEqualTypeOf<number | null>()
    expectTypeOf<DetailedStatisticsBreakdown['avg_calls_per_reply']>().toEqualTypeOf<
      number | null
    >()
    expectTypeOf<DetailedStatisticsData['trends']>().toEqualTypeOf<
      Record<string, DetailedStatisticsTrendData>
    >()
    expectTypeOf<DetailedStatisticsData['metrics']>().toEqualTypeOf<
      Record<string, DetailedStatisticsMetricsData>
    >()

    const hit: DetailedStatisticsSummary['cache_hit_rate'] = 0.25
    const miss: DetailedStatisticsSummary['cache_hit_rate'] = null
    expect(hit).toBe(0.25)
    expect(miss).toBeNull()
  })
})
