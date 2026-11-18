export interface DetailedStatisticsSummary {
  online_time: number
  total_messages: number
  total_replies: number
  total_requests: number
  total_tokens: number
  input_tokens: number
  output_tokens: number
  cache_hit_tokens: number
  cache_miss_tokens: number
  cache_hit_rate: number | null
  total_cost: number
  cost_per_100_messages: number
  cost_per_100_messages_excluding_replies: number
  cost_per_100_replies: number
  cost_per_hour: number
  tokens_per_hour: number
}

export interface DetailedStatisticsBreakdown {
  name: string
  is_embedding: boolean
  request_count: number
  input_tokens: number
  output_tokens: number
  total_tokens: number
  cache_hit_tokens: number
  cache_miss_tokens: number
  cache_hit_rate: number | null
  total_cost: number
  avg_time_cost: number
  std_time_cost: number
  avg_calls_per_reply: number | null
  avg_tokens_per_reply: number | null
  avg_tokens_per_call: number | null
  /** 实际调用尝试数（含失败），只有模型和任务组维度能与调用记录对应，其余为 null */
  attempt_count: number | null
  failure_count: number | null
}

export interface DetailedReliabilityItem {
  name: string
  attempt_count: number
  failure_count: number
  failure_rate: number | null
  avg_success_duration: number | null
}

export interface DetailedReliabilityData {
  /** 失败统计实际覆盖的起点；调用记录只保留 7 天，更长的时段会被截断 */
  coverage_start: string
  truncated: boolean
  attempt_count: number
  failure_count: number
  failure_rate: number | null
  avg_success_duration: number | null
  by_model: DetailedReliabilityItem[]
  by_provider: DetailedReliabilityItem[]
  by_task: DetailedReliabilityItem[]
}

export interface DetailedPeriodTrend {
  start_time: string
  bucket_minutes: number
  /** 趋势明细最多覆盖 30 天，更长的时段会被截断 */
  truncated: boolean
  time_labels: string[]
  costs: number[]
  tokens: number[]
  requests: number[]
  messages: number[]
  attempts: number[]
  failures: number[]
  cost_by_model: Record<string, number[]>
  tokens_by_model: Record<string, number[]>
  requests_by_model: Record<string, number[]>
  cost_by_module: Record<string, number[]>
  message_by_chat: Record<string, number[]>
  /** 7×24，星期一为第 0 行 */
  heatmap_messages: number[][]
  heatmap_costs: number[][]
}

export interface DetailedChatStatistics {
  name: string
  message_count: number
}

export interface DetailedDistributionItem {
  name: string
  value: number
}

export interface DetailedStatisticsDistributions {
  owner_tokens: DetailedDistributionItem[]
  model_tokens: DetailedDistributionItem[]
  module_tokens: DetailedDistributionItem[]
  request_type_tokens: DetailedDistributionItem[]
  chat_tokens: DetailedDistributionItem[]
  owner_costs: DetailedDistributionItem[]
  model_costs: DetailedDistributionItem[]
  module_costs: DetailedDistributionItem[]
  request_type_costs: DetailedDistributionItem[]
  chat_messages: DetailedDistributionItem[]
  chat_costs: DetailedDistributionItem[]
}

export interface DetailedStatisticsPeriod {
  key: string
  start_time: string
  end_time: string
  summary: DetailedStatisticsSummary
  /** 上一个等长周期的汇总；自部署以来没有上一周期 */
  previous_summary: DetailedStatisticsSummary | null
  models: DetailedStatisticsBreakdown[]
  modules: DetailedStatisticsBreakdown[]
  task_groups: DetailedStatisticsBreakdown[]
  request_types: DetailedStatisticsBreakdown[]
  chats: DetailedChatStatistics[]
  distributions: DetailedStatisticsDistributions
  reliability: DetailedReliabilityData
  trend: DetailedPeriodTrend
}

export interface DetailedStatisticsTrendData {
  time_labels: string[]
  total_cost_data: number[]
  cost_by_model: Record<string, number[]>
  cost_by_module: Record<string, number[]>
  message_by_chat: Record<string, number[]>
}

export interface DetailedStatisticsMetricsData {
  time_labels: string[]
  cost_per_100_messages: number[]
  cost_per_hour: number[]
  tokens_per_hour: number[]
  cost_per_100_replies: number[]
}

export interface DetailedStatisticsData {
  generated_at: string
  periods: DetailedStatisticsPeriod[]
  trends: Record<string, DetailedStatisticsTrendData>
  metrics: Record<string, DetailedStatisticsMetricsData>
}
