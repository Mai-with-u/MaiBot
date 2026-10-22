import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { useToast } from '@/hooks/use-toast'
import {
  CHAT_ADAPTER_STATUS_QUERY_KEY,
  getAdapterPolicyDefaults,
  updateAdapterPolicyDefaults,
  type AdapterPolicyDefaults,
} from '@/lib/chat-management-api'

import { AdapterAccountsPanel } from './AdapterAccountsPanel'
import { AdapterPolicyDefaultsView } from './AdapterPolicyDefaultsView'

export function AdapterPolicyDefaultsCard() {
  const queryClient = useQueryClient()
  const { toast } = useToast()
  const defaultsQuery = useQuery({
    queryKey: ['adapter-policy-defaults'],
    queryFn: getAdapterPolicyDefaults,
  })
  const defaultsMutation = useMutation({
    mutationFn: updateAdapterPolicyDefaults,
    onSuccess: (defaults) => {
      queryClient.setQueryData(['adapter-policy-defaults'], defaults)
      void queryClient.invalidateQueries({ queryKey: ['chat-stream-detail'] })
      void queryClient.invalidateQueries({ queryKey: [CHAT_ADAPTER_STATUS_QUERY_KEY] })
      void queryClient.invalidateQueries({ queryKey: ['adapter-host-policy'] })
      toast({ title: '适配器默认策略已保存' })
    },
    onError: (error) => {
      toast({
        title: '适配器默认策略保存失败',
        description: error instanceof Error ? error.message : '请稍后重试',
        variant: 'destructive',
      })
    },
  })

  const saveDefaultPolicy = (chatType: keyof AdapterPolicyDefaults, action: 'allow' | 'block') => {
    if (!defaultsQuery.data || defaultsQuery.data[chatType] === action) return
    defaultsMutation.mutate({ ...defaultsQuery.data, [chatType]: action })
  }

  return (
    <AdapterPolicyDefaultsView
      defaults={defaultsQuery.data}
      disabled={defaultsMutation.isPending}
      error={defaultsQuery.isError ? '默认策略加载失败' : undefined}
      onChange={saveDefaultPolicy}
      accounts={<AdapterAccountsPanel />}
    />
  )
}
