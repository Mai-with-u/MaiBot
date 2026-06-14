/**
 * useReviewStats —— 表达方式审核统计领域 hook（页面逻辑下沉）。
 *
 * 收编 index.tsx 的 uncheckedCount 状态与 fetchReviewStats（审核器关闭时刷新）。
 */
import { useCallback, useEffect, useRef, useState } from 'react'

import { getReviewStats } from '@/lib/expression-api'

export function useReviewStats() {
  const [uncheckedCount, setUncheckedCount] = useState(0)

  // 使用 ref 跟踪组件是否已卸载，防止内存泄漏
  const isMountedRef = useRef(true)
  useEffect(() => {
    isMountedRef.current = true
    return () => {
      isMountedRef.current = false
    }
  }, [])

  // 获取审核统计
  const fetchReviewStats = useCallback(async () => {
    try {
      const result = await getReviewStats()
      if (isMountedRef.current) {
        setUncheckedCount(result.unchecked)
      }
    } catch (error) {
      console.error('获取审核统计失败:', error)
    }
  }, [])

  return {
    uncheckedCount,
    fetchReviewStats,
  }
}
