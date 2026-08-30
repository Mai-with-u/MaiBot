import { useMemo, useState } from 'react'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Boxes, Brain, Database, Plus, RefreshCw, ShieldCheck, Users } from 'lucide-react'

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Checkbox } from '@/components/ui/checkbox'
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import { Textarea } from '@/components/ui/textarea'
import {
  assignWorkspaceChats,
  createWorkspace,
  getAvailableWorkspaceChats,
  getWorkspaces,
  updateWorkspace,
} from '@/lib/workspaces-api'

import type { WorkspaceCreateInput, WorkspaceItem } from '@/lib/workspaces-api'

const EMPTY_CREATE: WorkspaceCreateInput = {
  name: '',
  description: '',
  memory_mode: 'private',
  inherit_global_tools: true,
  inherit_global_plugins: true,
}

export function WorkspacesPage() {
  const queryClient = useQueryClient()
  const [selectedId, setSelectedId] = useState('')
  const [createOpen, setCreateOpen] = useState(false)
  const [createForm, setCreateForm] = useState<WorkspaceCreateInput>(EMPTY_CREATE)
  const [selectedChats, setSelectedChats] = useState<Set<string>>(new Set())

  const workspaceQuery = useQuery({ queryKey: ['workspaces'], queryFn: getWorkspaces })
  const chatsQuery = useQuery({ queryKey: ['workspace-chats'], queryFn: getAvailableWorkspaceChats })
  const workspaces = workspaceQuery.data?.data ?? []
  const selectedWorkspace = useMemo(
    () => workspaces.find((item) => item.id === selectedId) ?? workspaces[0],
    [selectedId, workspaces],
  )

  const refresh = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['workspaces'] }),
      queryClient.invalidateQueries({ queryKey: ['workspace-chats'] }),
    ])
  }

  const createMutation = useMutation({
    mutationFn: createWorkspace,
    onSuccess: async (workspace) => {
      setSelectedId(workspace.id)
      setCreateOpen(false)
      setCreateForm(EMPTY_CREATE)
      await refresh()
    },
  })

  const updateMutation = useMutation({
    mutationFn: ({ id, input }: { id: string; input: Partial<WorkspaceItem> }) =>
      updateWorkspace(id, input),
    onSuccess: refresh,
  })

  const assignMutation = useMutation({
    mutationFn: ({ id, sessionIds }: { id: string; sessionIds: string[] }) =>
      assignWorkspaceChats(id, sessionIds),
    onSuccess: async () => {
      setSelectedChats(new Set())
      await refresh()
    },
  })

  const visibleChats = chatsQuery.data ?? []
  const selectedMembers = selectedWorkspace
    ? visibleChats.filter((chat) => chat.workspace_id === selectedWorkspace.id)
    : []

  const toggleChat = (sessionId: string) => {
    setSelectedChats((current) => {
      const next = new Set(current)
      if (next.has(sessionId)) next.delete(sessionId)
      else next.add(sessionId)
      return next
    })
  }

  if (workspaceQuery.isLoading) {
    return <div className="p-6 text-sm text-muted-foreground">正在加载子系统配置…</div>
  }

  return (
    <div className="mx-auto flex w-full max-w-[1500px] flex-col gap-6 p-4 md:p-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="flex items-center gap-2 text-2xl font-semibold"><Boxes className="size-6" />子系统</h1>
          <p className="mt-1 text-sm text-muted-foreground">按聊天分组管理记忆空间、人设、工具和插件策略。</p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" onClick={() => void refresh()}><RefreshCw className="mr-2 size-4" />刷新</Button>
          <Dialog open={createOpen} onOpenChange={setCreateOpen}>
            <DialogTrigger asChild><Button><Plus className="mr-2 size-4" />新建子系统</Button></DialogTrigger>
            <DialogContent>
              <DialogHeader><DialogTitle>新建子系统</DialogTitle></DialogHeader>
              <div className="space-y-4">
                <div className="space-y-2"><Label htmlFor="workspace-name">名称</Label><Input id="workspace-name" value={createForm.name} onChange={(event) => setCreateForm({ ...createForm, name: event.target.value })} /></div>
                <div className="space-y-2"><Label htmlFor="workspace-description">说明</Label><Textarea id="workspace-description" value={createForm.description} onChange={(event) => setCreateForm({ ...createForm, description: event.target.value })} /></div>
                <div className="space-y-2"><Label>记忆模式</Label><Select value={createForm.memory_mode} onValueChange={(value: 'private' | 'public') => setCreateForm({ ...createForm, memory_mode: value })}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent className="z-[100] bg-popover"><SelectItem value="private">建立独立逻辑记忆库</SelectItem><SelectItem value="public">使用公共记忆库</SelectItem></SelectContent></Select></div>
              </div>
              <DialogFooter><Button variant="outline" onClick={() => setCreateOpen(false)}>取消</Button><Button disabled={!createForm.name.trim() || createMutation.isPending} onClick={() => createMutation.mutate(createForm)}>创建</Button></DialogFooter>
            </DialogContent>
          </Dialog>
        </div>
      </div>

      <Alert>
        <ShieldCheck className="size-4" />
        <AlertTitle>第一阶段基础能力已启用</AlertTitle>
        <AlertDescription>会话归属、人设覆盖入口和工具策略已按 Workspace 解析。A-Memorix 记忆对象的空间成员关系、选择性同步和完整插件事件隔离将在后续阶段接入；当前独立记忆库为逻辑空间元数据，不会错误宣称已经物理隔离。</AlertDescription>
      </Alert>

      {(workspaceQuery.error || chatsQuery.error || createMutation.error || updateMutation.error || assignMutation.error) && (
        <Alert variant="destructive"><AlertTitle>操作失败</AlertTitle><AlertDescription>{String(workspaceQuery.error || chatsQuery.error || createMutation.error || updateMutation.error || assignMutation.error)}</AlertDescription></Alert>
      )}

      <div className="grid gap-5 xl:grid-cols-[360px_minmax(0,1fr)]">
        <div className="space-y-3">
          {workspaces.map((workspace) => (
            <button key={workspace.id} type="button" className={`w-full rounded-xl border p-4 text-left transition-colors ${selectedWorkspace?.id === workspace.id ? 'border-primary bg-primary/5' : 'bg-card hover:bg-accent/50'}`} onClick={() => setSelectedId(workspace.id)}>
              <div className="flex items-start justify-between gap-3"><div><div className="font-semibold">{workspace.name}</div><div className="mt-1 line-clamp-2 text-xs text-muted-foreground">{workspace.description || '暂无说明'}</div></div>{workspace.is_default && <Badge>默认</Badge>}</div>
              <div className="mt-3 flex flex-wrap gap-2 text-xs"><Badge variant="outline"><Users className="mr-1 size-3" />{workspace.member_count} 个显式成员</Badge><Badge variant="outline"><Database className="mr-1 size-3" />{workspace.memory_space_name}</Badge></div>
            </button>
          ))}
        </div>

        {selectedWorkspace && (
          <div className="space-y-5">
            <Card>
              <CardHeader><CardTitle>{selectedWorkspace.name}</CardTitle><CardDescription>策略版本 {selectedWorkspace.policy_revision} · 未显式分配的聊天仍进入默认子系统</CardDescription></CardHeader>
              <CardContent className="grid gap-4 md:grid-cols-2">
                <div className="flex items-center justify-between rounded-lg border p-3"><div><div className="font-medium">继承全局工具</div><div className="text-xs text-muted-foreground">关闭后仅显式允许的工具可见</div></div><Switch checked={selectedWorkspace.inherit_global_tools} onCheckedChange={(checked) => updateMutation.mutate({ id: selectedWorkspace.id, input: { inherit_global_tools: checked } })} /></div>
                <div className="flex items-center justify-between rounded-lg border p-3"><div><div className="font-medium">继承全局插件</div><div className="text-xs text-muted-foreground">完整事件与 Hook 隔离仍在开发阶段</div></div><Switch checked={selectedWorkspace.inherit_global_plugins} onCheckedChange={(checked) => updateMutation.mutate({ id: selectedWorkspace.id, input: { inherit_global_plugins: checked } })} /></div>
                <div className="rounded-lg border p-3"><div className="flex items-center gap-2 font-medium"><Database className="size-4" />记忆空间</div><div className="mt-1 text-sm text-muted-foreground">{selectedWorkspace.memory_space_name}</div></div>
                <div className="rounded-lg border p-3"><div className="flex items-center gap-2 font-medium"><Brain className="size-4" />人设覆盖</div><div className="mt-1 text-sm text-muted-foreground">{selectedWorkspace.persona_profile_id ? '已绑定独立人设' : '继承全局人设'}</div></div>
              </CardContent>
            </Card>

            <Card>
              <CardHeader><CardTitle>聊天成员</CardTitle><CardDescription>一个聊天只能有一个主子系统。勾选后保存会自动从原子系统改派。</CardDescription></CardHeader>
              <CardContent className="space-y-3">
                <div className="max-h-[430px] space-y-2 overflow-y-auto pr-1">
                  {visibleChats.map((chat) => {
                    const isCurrent = chat.workspace_id === selectedWorkspace.id
                    return <label key={chat.session_id} className="flex cursor-pointer items-center gap-3 rounded-lg border p-3 hover:bg-accent/40"><Checkbox checked={selectedChats.has(chat.session_id)} onCheckedChange={() => toggleChat(chat.session_id)} /><div className="min-w-0 flex-1"><div className="truncate text-sm font-medium">{chat.display_name}</div><div className="truncate text-xs text-muted-foreground">{chat.platform} · {chat.chat_type === 'group' ? '群聊' : '私聊'} · 当前：{chat.workspace_name}</div></div>{isCurrent && <Badge variant="secondary">当前成员</Badge>}</label>
                  })}
                  {!visibleChats.length && <div className="py-10 text-center text-sm text-muted-foreground">尚未发现聊天流，请先让机器人收到一条消息。</div>}
                </div>
                <div className="flex items-center justify-between"><span className="text-sm text-muted-foreground">当前解析到 {selectedMembers.length} 个聊天，已选择 {selectedChats.size} 个</span><Button disabled={!selectedChats.size || assignMutation.isPending} onClick={() => assignMutation.mutate({ id: selectedWorkspace.id, sessionIds: [...selectedChats] })}>保存成员归属</Button></div>
              </CardContent>
            </Card>
          </div>
        )}
      </div>
    </div>
  )
}
