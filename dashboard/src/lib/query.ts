/**
 * 全局 QueryClient 配置（服务端状态的统一接缝）。
 *
 * 约定（架构评审敲定）：
 * - 查询（读）失败不弹全局 toast，由页面用 error 状态做局部呈现；
 * - 变更（写）失败默认弹全局 toast（对用户动作的反馈），单个 mutation 可用
 *   meta: { suppressErrorToast: true } 关闭后自行处理，meta.errorTitle 可定制标题；
 * - 不自动重试：错误应当及时完整暴露，而不是被重试拖延掩盖；
 * - 窗口聚焦不自动重新拉取：管理后台多为编辑场景，防止意外刷新；
 * - queryKey 以领域名开头分层（如 ['persons', 'list', 参数]），便于按前缀整体失效。
 */
import { MutationCache, QueryClient } from '@tanstack/react-query'

import { toast } from '@/hooks/use-toast'

declare module '@tanstack/react-query' {
  interface Register {
    mutationMeta: {
      /** 设为 true 时跳过全局错误 toast，由调用方自行处理错误 */
      suppressErrorToast?: boolean
      /** 全局错误 toast 的标题，默认「操作失败」 */
      errorTitle?: string
    }
  }
}

export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
        refetchOnWindowFocus: false,
        staleTime: 30_000,
      },
      mutations: {
        retry: false,
      },
    },
    mutationCache: new MutationCache({
      onError: (error, _variables, _context, mutation) => {
        if (mutation.meta?.suppressErrorToast) {
          return
        }
        toast({
          title: mutation.meta?.errorTitle ?? '操作失败',
          description: error instanceof Error ? error.message : String(error),
          variant: 'destructive',
        })
      },
    }),
  })
}

/** 应用级单例，在 main.tsx 经 QueryClientProvider 注入 */
export const queryClient = createQueryClient()
