/**
 * 黑话管理页面的内部类型定义
 */

/**
 * 统计数据
 */
export interface StatsData {
  total: number
  confirmed_jargon: number
  confirmed_not_jargon: number
  pending: number
  global_count: number
  complete_count: number
  chat_count: number
  top_chats: Record<string, number>
}
