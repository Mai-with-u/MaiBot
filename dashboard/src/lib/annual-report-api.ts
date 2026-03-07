import { fetchWithAuth } from './fetch-with-auth'

export interface TimeFootprintData {
  total_online_hours: number
  first_message_time: string | null
  first_message_user: string | null
  first_message_content: string | null
  busiest_day: string | null
  busiest_day_count: number
  hourly_distribution: number[]
  midnight_chat_count: number
  is_night_owl: boolean
}

export interface SocialNetworkData {
  total_groups: number
  top_groups: Array<{
    group_id: string
    group_name: string
    message_count: number
    is_webui?: boolean
  }>
  top_users: Array<{
    user_id: string
    user_nickname: string
    message_count: number
    is_webui?: boolean
  }>
  at_count: number
  mentioned_count: number
  longest_companion_user: string | null
  longest_companion_days: number
}

export interface BrainPowerData {
  total_tokens: number
  total_cost: number
  favorite_model: string | null
  favorite_model_count: number
  model_distribution: Array<{
    model: string
    count: number
    tokens: number
    cost: number
  }>
  top_reply_models: Array<{
    model: string
    count: number
  }>
  most_expensive_cost: number
  most_expensive_time: string | null
  top_token_consumers: Array<{
    user_id: string
    cost: number
    tokens: number
  }>
  silence_rate: number
  total_actions: number
  no_reply_count: number
  avg_interest_value: number
  max_interest_value: number
  max_interest_time: string | null
  avg_reasoning_length: number
  max_reasoning_length: number
  max_reasoning_time: string | null
}

export interface ExpressionVibeData {
  top_emoji: {
    id: number
    path: string
    description: string
    usage_count: number
    hash: string
  } | null
  top_emojis: Array<{
    id: number
    path: string
    description: string
    usage_count: number
    hash: string
  }>
  top_expressions: Array<{
    style: string
    count: number
  }>
  rejected_expression_count: number
  checked_expression_count: number
  total_expressions: number
  action_types: Array<{
    action: string
    count: number
  }>
  image_processed_count: number
  late_night_reply: {
    time: string
    content: string
  } | null
  favorite_reply: {
    content: string
    count: number
  } | null
}

export interface AchievementData {
  new_jargon_count: number
  sample_jargons: Array<{
    content: string
    meaning: string
    count: number
  }>
  total_messages: number
  total_replies: number
}

export interface AnnualReportData {
  year: number
  bot_name: string
  generated_at: string
  time_footprint: TimeFootprintData
  social_network: SocialNetworkData
  brain_power: BrainPowerData
  expression_vibe: ExpressionVibeData
  achievements: AchievementData
}

export async function getAnnualReport(year: number = 2025): Promise<AnnualReportData> {
  const response = await fetchWithAuth(`/api/webui/annual-report/full?year=${year}`)
  
  if (!response.ok) {
    const error = await response.json()
    throw new Error(error.detail || '获取年度报告失败')
  }
  
  return response.json()
}
