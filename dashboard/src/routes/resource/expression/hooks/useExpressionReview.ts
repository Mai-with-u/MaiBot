/**
 * useExpressionReview —— 表达方式「审核」领域 hook（页面逻辑下沉切片）。
 *
 * 收编单条 / 批量审核状态切换的写逻辑：
 * - 单条切换：基于当前 checked / modified_by 计算下一个目标态，调用 updateExpressionReviewStatus；
 * - 批量切换：对选中集并发调用，汇总成功 / 失败数；
 * - 写成功后调用 onChanged()（由页面接 list.invalidate()）重新拉取列表与统计，服务端为准；
 * - 写失败弹全局 toast（与原页面一致）。
 *
 * 注意行为变化：原页面用 applyReviewStatusUpdates 做乐观本地过滤（把不再匹配
 * reviewFilter 的项即时移出列表）。改为 invalidate 后变为重新拉取（服务端为准）。
 */
import { useCallback } from 'react'

import { useToast } from '@/hooks/use-toast'

import { updateExpressionReviewStatus } from '@/lib/expression-api'

import type { Expression } from '@/types/expression'

export interface UseExpressionReviewOptions {
  /** 写成功后回调（页面接 list.invalidate()），刷新列表 + 统计 */
  onChanged: () => void
}

export interface UseExpressionReviewResult {
  /** 单条审核状态切换（在「人工通过」与「取消人工通过」间翻转） */
  toggleReviewStatus: (expression: Expression) => Promise<void>
  /** 批量审核状态设置（approved=true 设为通过，false 取消通过） */
  batchReviewStatus: (expressionIds: number[], approved: boolean) => Promise<void>
}

export function useExpressionReview({
  onChanged,
}: UseExpressionReviewOptions): UseExpressionReviewResult {
  const { toast } = useToast()

  const toggleReviewStatus = useCallback(
    async (expression: Expression) => {
      const isUserApproved = expression.checked && expression.modified_by === 'user'
      const nextApproved = !isUserApproved

      try {
        await updateExpressionReviewStatus(expression.id, nextApproved)
        toast({
          title: nextApproved ? '已通过' : '已拒绝',
          description: nextApproved ? '已设为人工通过' : '已取消人工通过',
        })
        onChanged()
      } catch (error) {
        toast({
          title: '更新审核状态失败',
          description: error instanceof Error ? error.message : '无法更新表达方式审核状态',
          variant: 'destructive',
        })
      }
    },
    [onChanged, toast]
  )

  const batchReviewStatus = useCallback(
    async (expressionIds: number[], approved: boolean) => {
      if (expressionIds.length === 0) {
        return
      }

      try {
        // 用 allSettled 而非 all：单条失败不应中断其余项，仍需汇总成功/失败数
        const results = await Promise.allSettled(
          expressionIds.map((expressionId) =>
            updateExpressionReviewStatus(expressionId, approved)
          )
        )
        const updatedCount = results.filter((result) => result.status === 'fulfilled').length
        const failedCount = results.length - updatedCount

        if (updatedCount > 0) {
          onChanged()
        }

        toast({
          title: approved ? '批量设为通过完成' : '批量设为不通过完成',
          description:
            failedCount > 0
              ? `成功 ${updatedCount} 个，失败 ${failedCount} 个`
              : `已更新 ${updatedCount} 个表达方式`,
          variant: failedCount > 0 ? 'destructive' : undefined,
        })
      } catch (error) {
        toast({
          title: '批量更新审核状态失败',
          description: error instanceof Error ? error.message : '无法批量更新表达方式审核状态',
          variant: 'destructive',
        })
      }
    },
    [onChanged, toast]
  )

  return {
    toggleReviewStatus,
    batchReviewStatus,
  }
}
