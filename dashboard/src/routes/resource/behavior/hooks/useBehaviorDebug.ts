import { useMutation } from '@tanstack/react-query'

import { debugBehaviorRetrieval, type BehaviorRetrievalDebugRequest } from '@/lib/behavior-api'

/**
 * 行为检索调试（写动作：触发一次检索试跑）。
 *
 * 写失败按 query.ts 约定弹全局 toast；调用方读取 data 渲染调试结果。
 */
export function useBehaviorDebug() {
  const mutation = useMutation({
    mutationFn: (payload: BehaviorRetrievalDebugRequest) => debugBehaviorRetrieval(payload),
    meta: { errorTitle: '检索调试失败' },
  })
  return {
    runDebug: mutation.mutateAsync,
    result: mutation.data?.data ?? null,
    loading: mutation.isPending,
  }
}
