/**
 * 仪表盘首页领域类型（页面逻辑下沉）。
 *
 * 从 routes/index.tsx 抽出，供 home/hooks 下的领域 hook 与主渲染层共享。
 */
import type { LucideIcon } from 'lucide-react'

// 机器人状态接口
export interface BotStatus {
  running: boolean
  uptime: number
  version: string
  start_time: string
}

export interface ReleaseStatus {
  version: string
  url: string
}

export interface StatisticsSummary {
  total_requests: number
  total_cost: number
  total_tokens: number
  online_time: number
  total_messages: number
  total_replies: number
  avg_response_time: number
  cost_per_hour: number
  tokens_per_hour: number
}

export interface ModelStatistics {
  model_name: string
  request_count: number
  total_cost: number
  total_tokens: number
  avg_response_time: number
}

export interface TimeSeriesData {
  timestamp: string
  requests: number
  cost: number
  tokens: number
}

export interface RecentActivity {
  timestamp: string
  model: string
  request_type: string
  tokens: number
  cost: number
  time_cost: number
  status: string
}

export interface DashboardData {
  summary: StatisticsSummary
  model_stats: ModelStatistics[]
  hourly_data: TimeSeriesData[]
  daily_data: TimeSeriesData[]
  recent_activity: RecentActivity[]
}

export interface FeatureStatus {
  memoryEnabled: boolean
  visualEnabled: boolean
}

export type QuickShortcutCategory = 'system' | 'config' | 'resource' | 'plugin' | 'monitor' | 'external'

export interface QuickShortcutDefinition {
  id: string
  category: QuickShortcutCategory
  label: string
  description: string
  icon: LucideIcon
  href?: string
  action?: () => void | Promise<void>
  disabled?: boolean
  badge?: string
  external?: boolean
}

export const DEFAULT_TIME_RANGE = 24
