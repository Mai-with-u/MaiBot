import { useQuery } from '@tanstack/react-query'

import { getBehaviorGraphData, type BehaviorGraphData } from '@/lib/behavior-api'

/**
 * 行为学习图谱数据（场景簇网络 + tag 网络，只读服务端态）。
 *
 * 仅在场景簇图谱 / Tag 网络 tab 激活时拉取（enabled 控制），随 sessionId 变化重拉。
 * 读失败局部呈现，不弹全局 toast。
 */
export function useBehaviorGraph(params: { sessionId: string; enabled: boolean }) {
  const { sessionId, enabled } = params
  const query = useQuery({
    queryKey: ['behavior', 'graph', { sessionId }],
    queryFn: () =>
      getBehaviorGraphData({
        session_id: sessionId === 'all' ? undefined : sessionId,
      }),
    enabled,
  })
  // result.success 为契约字段，仅成功时取 data，否则保持 null 占位
  const graphData: BehaviorGraphData | null = query.data?.success ? (query.data.data ?? null) : null
  return {
    graphData,
    loading: query.isFetching,
  }
}
