import { useQuery } from '@tanstack/react-query'

import { listBehaviorPaths, type BehaviorPathItem } from '@/lib/behavior-api'

const PAGE_SIZE = 20

/** 行为经验路径查询参数（由主文件的本地态喂入） */
export interface UseBehaviorPathsParams {
  sessionId: string
  search: string
  enabledFilter: string
  learningTypeFilter: string
  sortBy: string
  sortOrder: string
  page: number
}

/**
 * 行为经验路径列表（只读服务端分页态）。
 *
 * 服务端按 {data,total} 分页返回，UI 再按场景簇本地分组渲染——因此直接用 useQuery，
 * 而不套 useDataList（分组/展开是本地 UI 态）。读失败局部呈现，不弹全局 toast。
 */
export function useBehaviorPaths(params: UseBehaviorPathsParams) {
  const { sessionId, search, enabledFilter, learningTypeFilter, sortBy, sortOrder, page } = params
  const query = useQuery({
    queryKey: [
      'behavior',
      'paths',
      { sessionId, search, enabledFilter, learningTypeFilter, sortBy, sortOrder, page },
    ],
    queryFn: () =>
      listBehaviorPaths({
        session_id: sessionId,
        search,
        enabled: enabledFilter,
        learning_type: learningTypeFilter,
        sort_by: sortBy,
        sort_order: sortOrder,
        page,
        page_size: PAGE_SIZE,
      }),
  })
  const paths: BehaviorPathItem[] = query.data?.data ?? []
  const total = query.data?.total ?? paths.length
  return {
    paths,
    total,
    loading: query.isFetching,
    refetch: query.refetch,
  }
}
