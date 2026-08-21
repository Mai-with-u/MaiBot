import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertCircle, Loader2, Save, ShieldCheck } from 'lucide-react'
import { useState } from 'react'

import { ListFieldEditor } from '@/components/ListFieldEditor'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { useToast } from '@/hooks/use-toast'
import { getAdapterHostPolicy, updateAdapterHostPolicy } from '@/lib/chat-management-api'
import type {
  AdapterHostDefaultAction,
  AdapterHostPolicy,
  AdapterPolicyDefaults,
  ChatStreamType,
} from '@/lib/chat-management-api'

interface AdapterHostPolicyPanelProps {
  pluginId: string
}

interface AdapterHostPolicyEditorProps {
  initialPolicy: AdapterHostPolicy
  globalDefaults: AdapterPolicyDefaults
  saving: boolean
  onSave: (policy: AdapterHostPolicy) => void
}

function clonePolicy(policy: AdapterHostPolicy): AdapterHostPolicy {
  return {
    group: {
      default_action: policy.group.default_action,
      allow_ids: [...policy.group.allow_ids],
      deny_ids: [...policy.group.deny_ids],
    },
    private: {
      default_action: policy.private.default_action,
      allow_ids: [...policy.private.allow_ids],
      deny_ids: [...policy.private.deny_ids],
    },
  }
}

function AdapterHostPolicyEditor({
  initialPolicy,
  globalDefaults,
  saving,
  onSave,
}: AdapterHostPolicyEditorProps) {
  const [policy, setPolicy] = useState<AdapterHostPolicy>(() => clonePolicy(initialPolicy))
  const hasChanges = JSON.stringify(policy) !== JSON.stringify(initialPolicy)

  const updateSection = (
    chatType: ChatStreamType,
    field: 'default_action' | 'allow_ids' | 'deny_ids',
    value: AdapterHostDefaultAction | string[]
  ) => {
    setPolicy((current) => ({
      ...current,
      [chatType]: {
        ...current[chatType],
        [field]: value,
      },
    }))
  }

  return (
    <div className="space-y-4">
      <Alert>
        <ShieldCheck className="h-4 w-4" />
        <AlertDescription className="space-y-1">
          <div className="font-medium">这是 MaiBot 主程序侧规则，与适配器自身名单相互独立。</div>
          <div>
            适配器自身的白名单仍在“设置”页管理；消息需要先通过适配器自身规则，再通过这里的主程序规则。
          </div>
        </AlertDescription>
      </Alert>

      <div className="grid gap-4 xl:grid-cols-2">
        {(['group', 'private'] as const).map((chatType) => {
          const section = policy[chatType]
          const globalAction = globalDefaults[chatType]
          const title = chatType === 'group' ? '群聊规则' : '私聊规则'
          return (
            <Card key={chatType}>
              <CardHeader>
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <CardTitle className="text-base">{title}</CardTitle>
                    <CardDescription>拒绝 ID 优先于放行 ID，支持使用 * 匹配全部。</CardDescription>
                  </div>
                  <Badge variant="outline">
                    全局默认：{globalAction === 'allow' ? '放行' : '拒绝'}
                  </Badge>
                </div>
              </CardHeader>
              <CardContent className="space-y-5">
                <div className="space-y-2">
                  <Label>未命中 ID 时</Label>
                  <Select
                    value={section.default_action}
                    onValueChange={(value) =>
                      updateSection(chatType, 'default_action', value as AdapterHostDefaultAction)
                    }
                  >
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="inherit">
                        继承全局（{globalAction === 'allow' ? '放行' : '拒绝'}）
                      </SelectItem>
                      <SelectItem value="allow">此适配器默认放行</SelectItem>
                      <SelectItem value="block">此适配器默认拒绝</SelectItem>
                    </SelectContent>
                  </Select>
                </div>

                <div className="space-y-2">
                  <div>
                    <Label>明确放行 ID</Label>
                    <p className="text-muted-foreground mt-1 text-xs">
                      群聊填写群号，私聊填写用户 ID。
                    </p>
                  </div>
                  <ListFieldEditor
                    value={section.allow_ids}
                    onChange={(value) =>
                      updateSection(
                        chatType,
                        'allow_ids',
                        value.map((item) => String(item))
                      )
                    }
                    itemType="string"
                    placeholder={chatType === 'group' ? '输入允许的群号' : '输入允许的用户 ID'}
                  />
                </div>

                <div className="space-y-2">
                  <div>
                    <Label>明确拒绝 ID</Label>
                    <p className="text-muted-foreground mt-1 text-xs">
                      同一 ID 不可同时出现在放行和拒绝列表。
                    </p>
                  </div>
                  <ListFieldEditor
                    value={section.deny_ids}
                    onChange={(value) =>
                      updateSection(
                        chatType,
                        'deny_ids',
                        value.map((item) => String(item))
                      )
                    }
                    itemType="string"
                    placeholder={chatType === 'group' ? '输入拒绝的群号' : '输入拒绝的用户 ID'}
                  />
                </div>
              </CardContent>
            </Card>
          )
        })}
      </div>

      <div className="flex justify-end">
        <Button disabled={!hasChanges || saving} onClick={() => onSave(policy)}>
          {saving ? (
            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
          ) : (
            <Save className="mr-2 h-4 w-4" />
          )}
          保存主程序规则
        </Button>
      </div>
    </div>
  )
}

export function AdapterHostPolicyPanel({ pluginId }: AdapterHostPolicyPanelProps) {
  const queryClient = useQueryClient()
  const { toast } = useToast()
  const queryKey = ['adapter-host-policy', pluginId] as const
  const policyQuery = useQuery({
    queryKey,
    queryFn: () => getAdapterHostPolicy(pluginId),
  })
  const saveMutation = useMutation({
    mutationFn: (policy: AdapterHostPolicy) => updateAdapterHostPolicy(pluginId, policy),
    onSuccess: (response) => {
      queryClient.setQueryData(queryKey, response)
      void queryClient.invalidateQueries({ queryKey: ['chat-stream-detail'] })
      toast({ title: '主程序放行规则已保存' })
    },
    onError: (error) => {
      toast({
        title: '主程序放行规则保存失败',
        description: error instanceof Error ? error.message : '请稍后重试',
        variant: 'destructive',
      })
    },
  })

  if (policyQuery.isLoading) {
    return (
      <div className="text-muted-foreground flex h-48 items-center justify-center gap-2 text-sm">
        <Loader2 className="h-4 w-4 animate-spin" />
        正在加载主程序规则
      </div>
    )
  }

  if (policyQuery.isError || !policyQuery.data) {
    return (
      <Alert variant="destructive">
        <AlertCircle className="h-4 w-4" />
        <AlertDescription>
          {policyQuery.error instanceof Error ? policyQuery.error.message : '主程序规则加载失败'}
        </AlertDescription>
      </Alert>
    )
  }

  return (
    <AdapterHostPolicyEditor
      key={JSON.stringify(policyQuery.data.policy)}
      initialPolicy={policyQuery.data.policy}
      globalDefaults={policyQuery.data.global_defaults}
      saving={saveMutation.isPending}
      onSave={(policy) => saveMutation.mutate(policy)}
    />
  )
}
