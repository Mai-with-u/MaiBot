import { useQuery } from '@tanstack/react-query'

import { getBehaviorPathDetail, type BehaviorPathDetail } from '@/lib/behavior-api'

/**
 * 单条行为经验路径的局部图谱详情（只读服务端态）。
 *
 * 仅在选中某条路径（pathId != null）时拉取。读失败局部呈现，不弹全局 toast。
 */
export function useBehaviorPathDetail(pathId: number | null) {
  const query = useQuery({
    queryKey: ['behavior', 'path-detail', pathId],
    queryFn: () => getBehaviorPathDetail(pathId as number),
    enabled: pathId !== null,
  })
  const detail: BehaviorPathDetail | null = query.data?.data ?? null
  return {
    detail,
    loading: query.isFetching,
  }
}
