import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus, Trash2 } from 'lucide-react'
import { useState } from 'react'

import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { MultiSelectChips } from '@/components/ui/multi-select-chips'
import { Switch } from '@/components/ui/switch'
import {
  RelationshipGraph,
  type RelationshipNode,
  type RelationshipEdge,
} from '@/components/ui/relationship-graph'
import { useToast } from '@/hooks/use-toast'
import { formatChatDisplayName } from '@/lib/chat-display'
import { getChatStreams } from '@/lib/chat-management-api'
import {
  deleteSharingGroup,
  getSharingGroups,
  saveSharingGroup,
  type SharingCapability,
  type SharingGroup,
  type SharingGroupInput,
  type SharingRule,
} from '@/lib/sharing-groups-api'

const labels: Record<SharingCapability, string> = {
  expression: '表达',
  jargon: '黑话',
  memory: '记忆',
}
const palette = ['#39845A', '#397FAE', '#B97132', '#9863A5', '#B45C6E', '#438E91']

const allChatsRule: SharingRule = { platform: '*', item_id: '*', rule_type: 'all' }
function coversAllChats(rule: SharingRule) {
  return rule.platform === '*' && rule.item_id === '*' && rule.rule_type === 'all'
}

function GroupEditor({
  group,
  color,
  onClose,
  onSaved,
}: {
  group?: SharingGroup
  color: string
  onClose: () => void
  onSaved: () => void
}) {
  const [draft, setDraft] = useState<SharingGroupInput>(() =>
    group
      ? { ...group }
      : {
          name: '',
          color,
          note: '',
          enabled: true,
          member_ids: [],
          rules: [],
          capabilities: { expression: true, jargon: false, memory: false },
        }
  )
  const [search, setSearch] = useState('')
  const isGlobal = draft.rules.some(coversAllChats)
  const chats = useQuery({ queryKey: ['chat-streams'], queryFn: () => getChatStreams() })
  const { toast } = useToast()
  const mutation = useMutation({
    mutationFn: () => saveSharingGroup(draft, group?.id),
    onSuccess: () => {
      onSaved()
      onClose()
    },
    onError: (error) =>
      toast({ title: '保存相通组失败', description: error.message, variant: 'destructive' }),
  })
  const remove = useMutation({
    mutationFn: () => deleteSharingGroup(group!),
    onSuccess: () => {
      onSaved()
      onClose()
    },
    onError: (error) =>
      toast({ title: '删除相通组失败', description: error.message, variant: 'destructive' }),
  })
  const busy = mutation.isPending || remove.isPending
  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !busy) onClose()
      }}
    >
      <DialogContent style={{ maxWidth: '48rem' }}>
        <DialogHeader>
          <DialogTitle>{group ? '编辑相通组' : '新建相通组'}</DialogTitle>
          <DialogDescription>成员统一管理，只共享直接所属组开启的内容。</DialogDescription>
        </DialogHeader>
        <DialogBody>
          <fieldset disabled={busy} className="space-y-4">
            <div className="grid grid-cols-[1fr_auto] gap-3">
              <Label className="space-y-2">
                名称
                <Input
                  aria-label="名称"
                  maxLength={100}
                  value={draft.name}
                  onChange={(event) => setDraft({ ...draft, name: event.target.value })}
                />
              </Label>
              <Label className="space-y-2">
                颜色
                <Input
                  aria-label="颜色"
                  type="color"
                  className="w-20"
                  value={draft.color}
                  onChange={(event) => setDraft({ ...draft, color: event.target.value })}
                />
              </Label>
            </div>
            <Label className="block space-y-2">
              备注
              <Input
                aria-label="备注"
                maxLength={4000}
                value={draft.note}
                onChange={(event) => setDraft({ ...draft, note: event.target.value })}
              />
            </Label>
            <Label className="flex items-center gap-2">
              <Switch
                checked={draft.enabled}
                onCheckedChange={(enabled) => setDraft({ ...draft, enabled })}
              />
              启用相通组
            </Label>
            <div className="flex flex-wrap gap-4">
                  {(Object.keys(labels) as SharingCapability[]).map((capability) => (
                    <Label key={capability} className="flex items-center gap-2">
                      <Switch
                        aria-label={`共享${labels[capability]}`}
                        checked={draft.capabilities[capability] === true}
                        onCheckedChange={(enabled) =>
                          setDraft({
                            ...draft,
                            capabilities: { ...draft.capabilities, [capability]: enabled },
                          })
                        }
                      />
                      共享{labels[capability]}
                    </Label>
                  ))}
                </div>
                <div className="space-y-2">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="text-sm font-medium">成员聊天</span>
                    <Label htmlFor="group-all-chats" className="flex items-center gap-2">
                      <Switch
                        id="group-all-chats"
                        checked={isGlobal}
                        onCheckedChange={(checked) =>
                          setDraft((current) => ({
                            ...current,
                            rules: checked
                              ? [...current.rules, allChatsRule]
                              : current.rules.filter((rule) => !coversAllChats(rule)),
                          }))
                        }
                      />
                      覆盖所有聊天
                    </Label>
                  </div>
                  {isGlobal ? (
                    <p className="text-muted-foreground text-xs">包含所有平台现有和以后新增的群聊、私聊。{draft.capabilities.jargon && '启用黑话学习的聊天会在此组共同积累线索和推断释义。'}</p>
                  ) : (
                    <>
                      <Input
                        placeholder="搜索聊天名称"
                        value={search}
                        onChange={(event) => setSearch(event.target.value)}
                      />
                      {chats.isError ? (
                        <p className="text-destructive">聊天列表加载失败</p>
                      ) : (
                        <div className="max-h-56 overflow-auto border-2 p-2">
                          {chats.data
                            ?.filter((chat) =>
                              chat.display_name.toLowerCase().includes(search.toLowerCase())
                            )
                            .map((chat) => (
                              <Label key={chat.session_id} className="flex items-center gap-2 p-2">
                                <Checkbox
                                  aria-label={`选择 ${chat.display_name}`}
                                  checked={draft.member_ids.includes(chat.session_id)}
                                  onCheckedChange={(checked) =>
                                    setDraft({
                                      ...draft,
                                      member_ids: checked
                                        ? [...draft.member_ids, chat.session_id]
                                        : draft.member_ids.filter((id) => id !== chat.session_id),
                                    })
                                  }
                                />
                                {formatChatDisplayName(chat.display_name, chat.account_id)}
                              </Label>
                            ))}
                        </div>
                      )}
                      {chats.isSuccess &&
                        draft.member_ids
                          .filter((id) => !chats.data.some((chat) => chat.session_id === id))
                          .map((id) => (
                            <div key={id} className="flex items-center gap-2 text-sm">
                              未找到聊天流
                              <Button
                                size="xs"
                                variant="outline"
                                onClick={() =>
                                  setDraft({
                                    ...draft,
                                    member_ids: draft.member_ids.filter((member) => member !== id),
                                  })
                                }
                              >
                                移除
                              </Button>
                            </div>
                          ))}
                    </>
                  )}
                </div>
                {!isGlobal && draft.rules.length > 0 && (
                  <div className="space-y-2">
                    <Label>迁移保留的成员规则</Label>
                    <p className="text-muted-foreground text-xs">
                      规则会持续匹配聊天，删除规则后可改为勾选固定成员。
                    </p>
                    {draft.rules.map((rule, index) => (
                      <div
                        key={index}
                        className="flex items-center justify-between border-2 p-2 text-sm"
                      >
                        <span>
                          {rule.platform} · {rule.item_id} ·{' '}
                          {rule.rule_type === 'all'
                            ? '全部聊天'
                            : rule.rule_type === 'group'
                              ? '群聊'
                              : '私聊'}
                        </span>
                        <Button
                          size="xs"
                          variant="outline"
                          onClick={() =>
                            setDraft({ ...draft, rules: draft.rules.filter((_, i) => i !== index) })
                          }
                        >
                          移除规则
                        </Button>
                      </div>
                    ))}
                  </div>
                )}
              </fieldset>
            </DialogBody>
            <DialogFooter>
              {group && (
                <Button variant="destructive" disabled={busy} onClick={() => remove.mutate()}>
                  <Trash2 className="h-4 w-4" />
                  {remove.isPending ? '正在删除…' : '删除组'}
                </Button>
              )}
              <Button variant="outline" disabled={busy} onClick={onClose}>
                取消
              </Button>
              <Button disabled={busy || !draft.name.trim()} onClick={() => mutation.mutate()}>
                保存
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      )
    }

    export function SharedGroupsSettings({
      fullHeight = false,
    }: {
      fullHeight?: boolean
      onSectionSaved?: (sectionName: string, value: Record<string, unknown>) => void
    } = {}) {
      const queryClient = useQueryClient()
      const query = useQuery({ queryKey: ['sharing-groups'], queryFn: getSharingGroups })
      const chats = useQuery({ queryKey: ['chat-streams'], queryFn: () => getChatStreams() })
      const [editing, setEditing] = useState<string | null>(() => {
        const id = new URLSearchParams(window.location.search).get('group')
        return id && id !== 'all' ? id : null
      })
      const [selected, setSelected] = useState<string>()
      const [layers, setLayers] = useState<Record<SharingCapability, boolean>>({
        expression: true,
        jargon: true,
        memory: true,
      })
      const groups = query.data?.data ?? []
      const nodes: RelationshipNode[] = []
      const edges: RelationshipEdge[] = []
      const memberNodes = new Map<string, RelationshipNode>()
      const memberGroupNames = new Map<string, string[]>()
      for (const group of groups) {
        const active =
          group.enabled &&
          (Object.keys(labels) as SharingCapability[]).some(
            (key) => layers[key] && group.capabilities[key]
          )
        const opacity = active ? 1 : 0.2
        const isGlobal = group.rules.some(coversAllChats)
        nodes.push({
          id: group.id,
          label: group.name,
          isGlobal,
          description: group.enabled
            ? (Object.keys(labels) as SharingCapability[])
                .filter((key) => group.capabilities[key])
                .map((key) => labels[key])
                .join(' · ') || '未开启共享内容'
            : '已停用',
          colors: [group.color],
          column: 0,
          opacity,
        })
        if (isGlobal) continue
        for (const id of group.resolved_ids) {
          const chat = chats.data?.find((item) => item.session_id === id)
          const nodeId = `member:${id}`
          const existing = memberNodes.get(nodeId)
          const owners = [...(memberGroupNames.get(nodeId) ?? []), group.name]
          memberGroupNames.set(nodeId, owners)
          memberNodes.set(nodeId, {
            id: nodeId,
            label: chat ? formatChatDisplayName(chat.display_name, chat.account_id) : '未找到聊天流',
            description: owners.length > 1 ? `共同成员 · ${owners.join('、')}` : undefined,
            colors: [...new Set([...(existing?.colors ?? []), group.color])],
            column: 1,
            opacity: Math.max(existing?.opacity ?? 0, opacity),
          })
          edges.push({ from: group.id, to: nodeId, color: group.color, opacity })
        }
        group.rules.forEach((rule, index) => {
          const resolved = chats.data?.some(
            (chat) =>
              group.resolved_ids.includes(chat.session_id) &&
              (rule.platform === '*' || rule.platform === chat.platform) &&
              (rule.item_id === '*' || rule.item_id === chat.target_id) &&
              (rule.rule_type === 'all' || rule.rule_type === chat.chat_type)
          )
          if (resolved) return
          const id = `rule:${group.id}:${index}`
          nodes.push({
            id,
            label: rule.item_id === '*' ? '动态匹配全部目标' : `目标 ${rule.item_id}`,
            description: `${rule.platform} · ${rule.rule_type === 'all' ? '全部聊天' : rule.rule_type === 'group' ? '群聊' : '私聊'}`,
            column: 1,
            colors: [group.color],
            opacity,
          })
          edges.push({ from: group.id, to: id, color: group.color, opacity })
        })
      }
      nodes.push(...memberNodes.values())
      const refresh = () => {
        for (const key of ['sharing-groups', 'expression', 'jargon', 'memory'])
          void queryClient.invalidateQueries({ queryKey: [key] })
      }
      return (
        <div
          className={fullHeight ? 'flex min-h-0 flex-1 flex-col' : 'flex h-[70vh] min-h-80 flex-col'}
        >
          <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
            {query.data?.jargon_conflicts?.map(({ left, right }, i) => (
              <p key={i} role="alert" className="text-destructive">「{left}」与「{right}」的黑话范围重叠。线索会先记在来源聊天下。调整成员或规则后，系统会将线索用于后续推断。</p>
            ))}
            {query.isPending ? (
              <p>正在加载相通组…</p>
            ) : query.isError ? (
              <p className="text-destructive">加载相通组失败</p>
            ) : (
              <RelationshipGraph
                layout="overview"
                toolbarActions={
                  <>
                <MultiSelectChips<SharingCapability>
                  mode="chips"
                  size="xs"
                  ariaLabel="关系图层"
                  selected={
                    new Set(
                      (Object.keys(labels) as SharingCapability[]).filter((key) => layers[key])
                    )
                  }
                  onToggle={(key) => setLayers((prev) => ({ ...prev, [key]: !prev[key] }))}
                  options={(Object.keys(labels) as SharingCapability[]).map((key) => ({
                    value: key,
                    label: `${labels[key]}层`,
                  }))}
                />
                <Button onClick={() => setEditing('new')}>
                  <Plus className="h-4 w-4" />
                  新建相通组
                </Button>
              </>
            }
            nodes={nodes}
            edges={edges}
            columns={['相通组', '成员 / 动态范围']}
            selectedId={selected}
            onSelect={(id) => {
              setSelected(id)
              if (groups.some((group) => group.id === id)) setEditing(id)
            }}
          />
        )}
        {editing && (editing === 'new' || groups.some((group) => group.id === editing)) && (
          <GroupEditor
            key={editing}
            group={groups.find((group) => group.id === editing)}
            color={palette[groups.length % palette.length]}
            onClose={() => setEditing(null)}
            onSaved={refresh}
          />
        )}
        {editing &&
          editing !== 'new' &&
          query.isSuccess &&
          !groups.some((group) => group.id === editing) && (
            <Dialog open onOpenChange={() => setEditing(null)}>
              <DialogContent>
                <DialogHeader>
                  <DialogTitle>相通组不存在</DialogTitle>
                  <DialogDescription>该组可能已被删除，请从图中重新选择。</DialogDescription>
                </DialogHeader>
              </DialogContent>
            </Dialog>
          )}
      </div>
    </div>
  )
}

export function ChatManagementPage() {
  return (
    <main className="flex h-full min-h-0 flex-1 flex-col overflow-hidden p-3 sm:p-4">
      <SharedGroupsSettings fullHeight />
    </main>
  )
}
