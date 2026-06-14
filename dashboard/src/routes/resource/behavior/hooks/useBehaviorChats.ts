import { useQuery } from '@tanstack/react-query'

import { listBehaviorChats, type BehaviorChatInfo } from '@/lib/behavior-api'

/**
 * 行为学习聊天流列表（只读服务端态）。
 *
 * 读失败按 query.ts 约定不弹全局 toast，由页面用 chats 占位空数组做局部呈现。
 */
export function useBehaviorChats() {
  const query = useQuery({
    queryKey: ['behavior', 'chats'],
    queryFn: () => listBehaviorChats(),
  })
  // result.success 为契约字段，仅在成功时取 data，否则保持空数组占位
  const chats: BehaviorChatInfo[] = query.data?.success ? query.data.data : []
  return {
    chats,
    refetch: query.refetch,
  }
}
