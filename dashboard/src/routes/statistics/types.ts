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
  models: DetailedStatisticsBreakdown[]
  modules: DetailedStatisticsBreakdown[]
  request_types: DetailedStatisticsBreakdown[]
  chats: DetailedChatStatistics[]
  distributions: DetailedStatisticsDistributions
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
