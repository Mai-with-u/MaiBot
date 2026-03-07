/**
 * 表达方式管理页面内部类型定义
 */

import type { Expression } from '@/types/expression'

/**
 * 删除确认状态
 */
export interface DeleteConfirmState {
  expression: Expression | null
  isOpen: boolean
}

/**
 * 统计数据
 */
export interface StatsData {
  total: number
  recent_7days: number
  chat_count: number
  top_chats: Record<string, number>
}

/**
 * 页面状态
 */
export interface PageState {
  expressions: Expression[]
  loading: boolean
  total: number
  page: number
  pageSize: number
  search: string
  selectedExpression: Expression | null
  isDetailDialogOpen: boolean
  isEditDialogOpen: boolean
  isCreateDialogOpen: boolean
  deleteConfirmExpression: Expression | null
  selectedIds: Set<number>
  isBatchDeleteDialogOpen: boolean
  jumpToPage: string
  stats: StatsData
  chatNameMap: Map<string, string>
  isReviewerOpen: boolean
  uncheckedCount: number
}
