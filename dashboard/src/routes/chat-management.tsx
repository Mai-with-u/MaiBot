import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus, Trash2, X } from 'lucide-react'
import type { CSSProperties } from 'react'
import { useMemo, useState } from 'react'

import { Badge } from '@/components/ui/badge'
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
import { Skeleton } from '@/components/ui/skeleton'
import { useToast } from '@/hooks/use-toast'
import { formatChatDisplayName, getChatTypeText } from '@/lib/chat-display'
import { getChatStreams, type ChatStream, type ChatStreamType } from '@/lib/chat-management-api'
import { getBotConfig, updateBotConfigSection } from '@/lib/config-api'

type MutualGroupKind = 'expression' | 'jargon' | 'memory'
const MUTUAL_GROUP_CHAT_RESULT_LIMIT = 50

const MUTUAL_GROUP_KIND_LABEL: Record<MutualGroupKind, string> = {
  expression: '表达',
  jargon: '黑话',
  memory: '记忆',
}

interface TargetItem {
  platform: string
  item_id: string
  rule_type?: ChatStreamType | string
  type?: ChatStreamType | string
}

interface ChatStreamGroupConfig {
  targets?: TargetItem[]
  expression_groups?: TargetItem[]
  jargon_groups?: TargetItem[]
}

function getChatLogicalId(chat: ChatStream): string {
  return chat.target_id || (chat.chat_type === 'group' ? chat.group_id : chat.user_id) || '-'
}

function getTargetRuleType(target: TargetItem): ChatStreamType {
  return target.rule_type === 'private' || target.type === 'private' ? 'private' : 'group'
}

function normalizeTarget(target: unknown): TargetItem | null {
  if (!target || typeof target !== 'object') {
    return null
  }
  const rawTarget = target as Record<string, unknown>
  const platform = String(rawTarget.platform ?? '').trim()
  const itemId = String(rawTarget.item_id ?? '').trim()
  const rawRuleType = rawTarget.rule_type ?? rawTarget.type
  const ruleType = rawRuleType === 'private' ? 'private' : 'group'
  if (!platform || !itemId) {
    return null
  }
  return { platform, item_id: itemId, rule_type: ruleType }
}

function normalizeMutualGroups(value: unknown): ChatStreamGroupConfig[] {
  if (!Array.isArray(value)) {
    return []
  }
  return value.map((group) => {
    if (!group || typeof group !== 'object') {
      return { targets: [] }
    }
    const rawGroup = group as ChatStreamGroupConfig
    const rawTargets =
      rawGroup.targets ?? rawGroup.expression_groups ?? rawGroup.jargon_groups ?? []
    const targets = Array.isArray(rawTargets)
      ? rawTargets.map(normalizeTarget).filter((target): target is TargetItem => target !== null)
      : []
    return { targets }
  })
}

function serializeMutualGroups(groups: ChatStreamGroupConfig[]): ChatStreamGroupConfig[] {
  return groups.map((group) => ({
    targets: (group.targets ?? []).map((target) => ({
      platform: target.platform,
      item_id: target.item_id,
      rule_type: getTargetRuleType(target),
    })),
  }))
}

function targetKey(target: TargetItem): string {
  return `${target.platform}:${target.item_id}:${getTargetRuleType(target)}`
}

function targetLabel(target: TargetItem): string {
  return `${target.platform}:${target.item_id}:${getChatTypeText(getTargetRuleType(target))}`
}

function getTargetDisplayName(
  target: TargetItem,
  chatNameByTargetKey: Map<string, string>
): string {
  return chatNameByTargetKey.get(targetKey(target)) ?? '未找到聊天流'
}

function chatToTarget(chat: ChatStream): TargetItem {
  return {
    platform: chat.platform,
    item_id: getChatLogicalId(chat),
    rule_type: chat.chat_type,
  }
}

function MutualGroupsView({
  chats,
  onSectionSaved,
}: {
  chats: ChatStream[]
  onSectionSaved?: (sectionName: string, value: Record<string, unknown>) => void
}) {
  const queryClient = useQueryClient()
  const { toast } = useToast()
  const [kind, setKind] = useState<MutualGroupKind>(() => {
    if (typeof window === 'undefined') {
      return 'expression'
    }
    const queryKind = new URLSearchParams(window.location.search).get('kind')
    return queryKind === 'memory' || queryKind === 'jargon' ? queryKind : 'expression'
  })
  const [addDialogGroupIndex, setAddDialogGroupIndex] = useState<number | null>(null)
  const [addDialogSearch, setAddDialogSearch] = useState('')
  const [selectedTargetKeys, setSelectedTargetKeys] = useState<string[]>([])
  const configQuery = useQuery({
    queryKey: ['chat-management-mutual-groups-config'],
    queryFn: () => getBotConfig(),
  })
  const sectionName = kind === 'memory' ? 'a_memorix' : kind
  const groupFieldName =
    kind === 'memory'
      ? 'shared_memory_groups'
      : kind === 'expression'
        ? 'expression_groups'
        : 'jargon_groups'
  const sectionData = useMemo(() => {
    const raw = configQuery.data?.[sectionName]
    return (raw && typeof raw === 'object' ? raw : {}) as Record<string, unknown>
  }, [configQuery.data, sectionName])
  const globalMemorySharingEnabled =
    kind === 'memory' && sectionData.global_memory_sharing_enabled === true
  const groups = useMemo(
    () => normalizeMutualGroups(sectionData[groupFieldName]),
    [groupFieldName, sectionData]
  )
  const addDialogGroup = addDialogGroupIndex === null ? null : (groups[addDialogGroupIndex] ?? null)
  const selectedTargetKeySet = useMemo(() => new Set(selectedTargetKeys), [selectedTargetKeys])
  const addDialogExistingKeySet = useMemo(
    () => new Set((addDialogGroup?.targets ?? []).map(targetKey)),
    [addDialogGroup]
  )
  const chatNameByTargetKey = useMemo(
    () =>
      new Map(
        chats.map((chat) => [
          targetKey(chatToTarget(chat)),
          formatChatDisplayName(chat.display_name, chat.account_id),
        ])
      ),
    [chats]
  )
  const addDialogChats = useMemo(() => {
    const keyword = addDialogSearch.trim().toLowerCase()
    return chats.filter((chat) => {
      const target = chatToTarget(chat)
      if (addDialogExistingKeySet.has(targetKey(target))) {
        return false
      }
      if (!keyword) {
        return true
      }
      return [
        chat.display_name,
        chat.account_id,
        chat.platform,
        getChatLogicalId(chat),
        chat.user_id,
        chat.group_id,
        chat.session_id,
        getChatTypeText(chat.chat_type),
      ]
        .join(' ')
        .toLowerCase()
        .includes(keyword)
    })
  }, [addDialogExistingKeySet, addDialogSearch, chats])
  const visibleAddDialogChats = addDialogChats.slice(0, MUTUAL_GROUP_CHAT_RESULT_LIMIT)
  const isAddDialogLimited = addDialogChats.length > visibleAddDialogChats.length

  const saveMutation = useMutation({
    mutationFn: (nextSectionData: Record<string, unknown>) =>
      updateBotConfigSection(sectionName, nextSectionData),
    onSuccess: (_result, nextSectionData) => {
      onSectionSaved?.(sectionName, nextSectionData)
      void queryClient.invalidateQueries({ queryKey: ['chat-management-mutual-groups-config'] })
      toast({
        title: '共享组已保存',
        description: `${MUTUAL_GROUP_KIND_LABEL[kind]}共享组配置已更新。`,
      })
    },
    onError: (error) => {
      toast({
        title: '保存共享组失败',
        description: error instanceof Error ? error.message : '请稍后重试',
        variant: 'destructive',
      })
    },
  })

  const updateGroups = (nextGroups: ChatStreamGroupConfig[]) => {
    if (globalMemorySharingEnabled) {
      return
    }
    saveMutation.mutate({
      ...sectionData,
      [groupFieldName]: serializeMutualGroups(nextGroups),
    })
  }

  const createGroup = () => {
    if (globalMemorySharingEnabled) {
      return
    }
    updateGroups([...groups, { targets: [] }])
  }

  const openAddDialog = (groupIndex: number) => {
    if (globalMemorySharingEnabled) {
      return
    }
    setAddDialogGroupIndex(groupIndex)
    setAddDialogSearch('')
    setSelectedTargetKeys([])
  }

  const closeAddDialog = () => {
    setAddDialogGroupIndex(null)
    setAddDialogSearch('')
    setSelectedTargetKeys([])
  }

  const toggleAddDialogChat = (target: TargetItem) => {
    const key = targetKey(target)
    setSelectedTargetKeys((currentKeys) =>
      currentKeys.includes(key)
        ? currentKeys.filter((currentKey) => currentKey !== key)
        : [...currentKeys, key]
    )
  }

  const applySelectedChatsToGroup = () => {
    if (
      globalMemorySharingEnabled ||
      addDialogGroupIndex === null ||
      selectedTargetKeys.length === 0
    ) {
      return
    }
    const selectedKeySet = new Set(selectedTargetKeys)
    const selectedTargets = chats
      .map(chatToTarget)
      .filter((target) => selectedKeySet.has(targetKey(target)))
    const nextGroups = groups.map((group, index) => {
      if (index !== addDialogGroupIndex) {
        return group
      }
      const targets = group.targets ?? []
      const existingKeys = new Set(targets.map(targetKey))
      const nextTargets = selectedTargets.filter((target) => !existingKeys.has(targetKey(target)))
      return { targets: [...targets, ...nextTargets] }
    })
    updateGroups(nextGroups)
    closeAddDialog()
  }

  const removeTarget = (groupIndex: number, targetIndex: number) => {
    if (globalMemorySharingEnabled) {
      return
    }
    updateGroups(
      groups.map((group, index) =>
        index === groupIndex
          ? {
              targets: (group.targets ?? []).filter(
                (_, memberIndex) => memberIndex !== targetIndex
              ),
            }
          : group
      )
    )
  }

  const deleteGroup = (groupIndex: number) => {
    if (globalMemorySharingEnabled) {
      return
    }
    updateGroups(groups.filter((_, index) => index !== groupIndex))
  }
  const editingDisabled = saveMutation.isPending || globalMemorySharingEnabled

  return (
    <section className="bg-background flex min-h-0 flex-1 flex-col overflow-hidden rounded-md border">
      <div className="flex shrink-0 flex-col gap-3 border-b p-4 lg:flex-row lg:items-end lg:justify-between">
        <div className="space-y-1">
          <h2 className="text-base font-semibold">共享组管理</h2>
          <p className="text-muted-foreground text-sm">管理表达、黑话和记忆的聊天流共享组。</p>
        </div>
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
          <div className="bg-background inline-flex rounded-md border p-1">
            {[
              ['expression', '表达'],
              ['jargon', '黑话'],
              ['memory', '记忆'],
            ].map(([value, label]) => (
              <Button
                key={value}
                type="button"
                variant={kind === value ? 'secondary' : 'ghost'}
                size="sm"
                className="h-8"
                onClick={() => setKind(value as MutualGroupKind)}
              >
                {label}
              </Button>
            ))}
          </div>
          <Button type="button" disabled={editingDisabled} onClick={createGroup}>
            <Plus className="mr-2 h-4 w-4" />
            新建共享组
          </Button>
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-auto p-4">
        {globalMemorySharingEnabled && (
          <div className="bg-muted/30 text-muted-foreground mb-3 rounded-md border px-3 py-2 text-sm">
            全局共享记忆已开启，记忆共享组暂不参与普通记忆检索范围控制。
          </div>
        )}
        {configQuery.isLoading ? (
          <div className="space-y-3">
            <Skeleton className="h-20" />
            <Skeleton className="h-20" />
          </div>
        ) : configQuery.error ? (
          <div className="border-destructive/40 text-destructive rounded-md border p-4 text-sm">
            加载共享组失败
          </div>
        ) : groups.length === 0 ? (
          <div className="text-muted-foreground rounded-md border border-dashed p-6 text-center text-sm">
            暂无{MUTUAL_GROUP_KIND_LABEL[kind]}共享组。
          </div>
        ) : (
          <div className="grid gap-3">
            {groups.map((group, groupIndex) => (
              <div key={groupIndex} className="rounded-md border p-3">
                <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
                  <div className="font-medium">共享组 {groupIndex + 1}</div>
                  <div className="flex items-center gap-2">
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      disabled={editingDisabled}
                      onClick={() => openAddDialog(groupIndex)}
                    >
                      <Plus className="mr-2 h-4 w-4" />
                      添加聊天
                    </Button>
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      className="text-destructive hover:text-destructive"
                      disabled={editingDisabled}
                      aria-label={`删除共享组 ${groupIndex + 1}`}
                      onClick={() => deleteGroup(groupIndex)}
                    >
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </div>
                </div>
                <div className="mt-3 flex flex-wrap gap-2">
                  {(group.targets ?? []).length === 0 ? (
                    <span className="text-muted-foreground text-sm">空共享组</span>
                  ) : (
                    (group.targets ?? []).map((target, targetIndex) => (
                      <Badge
                        key={`${targetKey(target)}:${targetIndex}`}
                        variant="outline"
                        className="gap-2"
                        title={targetLabel(target)}
                      >
                        <span className="max-w-48 truncate text-xs">
                          {getTargetDisplayName(target, chatNameByTargetKey)}
                        </span>
                        <button
                          type="button"
                          className="text-muted-foreground hover:text-destructive"
                          disabled={editingDisabled}
                          aria-label={`移除 ${getTargetDisplayName(target, chatNameByTargetKey)}`}
                          onClick={() => removeTarget(groupIndex, targetIndex)}
                        >
                          <X className="h-3 w-3" />
                        </button>
                      </Badge>
                    ))
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      <Dialog
        open={addDialogGroupIndex !== null}
        onOpenChange={(open) => !open && closeAddDialog()}
      >
        <DialogContent style={{ '--dialog-width': '42rem' } as CSSProperties}>
          <DialogHeader>
            <DialogTitle>添加聊天</DialogTitle>
            <DialogDescription>
              选择要加入共享组 {addDialogGroupIndex === null ? '' : addDialogGroupIndex + 1}{' '}
              的聊天流。
            </DialogDescription>
          </DialogHeader>
          <DialogBody>
            <div className="space-y-3">
              <Input
                value={addDialogSearch}
                onChange={(event) => setAddDialogSearch(event.target.value)}
                placeholder="搜索名称、平台、用户、群号或会话 ID"
              />
              <div className="max-h-[22rem] overflow-auto rounded-md border">
                {addDialogChats.length === 0 ? (
                  <div className="text-muted-foreground p-4 text-center text-sm">
                    没有可加入的聊天流
                  </div>
                ) : (
                  <div className="divide-y">
                    {visibleAddDialogChats.map((chat) => {
                      const target = chatToTarget(chat)
                      const key = targetKey(target)
                      const checked = selectedTargetKeySet.has(key)
                      return (
                        <label
                          key={chat.session_id}
                          className="hover:bg-muted/60 flex cursor-pointer items-center gap-3 px-3 py-2"
                        >
                          <Checkbox
                            checked={checked}
                            onCheckedChange={() => toggleAddDialogChat(target)}
                            aria-label={`选择 ${formatChatDisplayName(chat.display_name, chat.account_id)}`}
                          />
                          <div className="min-w-0 flex-1">
                            <div className="truncate text-sm font-medium">
                              {formatChatDisplayName(chat.display_name, chat.account_id)}
                            </div>
                            <div className="text-muted-foreground truncate font-mono text-xs">
                              {chat.platform}:{getChatLogicalId(chat)}
                            </div>
                          </div>
                          <Badge variant="outline">{getChatTypeText(chat.chat_type)}</Badge>
                        </label>
                      )
                    })}
                  </div>
                )}
              </div>
              {isAddDialogLimited && (
                <div className="text-muted-foreground text-xs">
                  仅显示前 {MUTUAL_GROUP_CHAT_RESULT_LIMIT} 个匹配项，请输入关键词缩小范围。
                </div>
              )}
            </div>
          </DialogBody>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={closeAddDialog}>
              取消
            </Button>
            <Button
              type="button"
              disabled={selectedTargetKeys.length === 0 || editingDisabled}
              onClick={applySelectedChatsToGroup}
            >
              加入 {selectedTargetKeys.length} 个聊天
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </section>
  )
}

export function SharedGroupsSettings({
  onSectionSaved,
}: {
  onSectionSaved?: (sectionName: string, value: Record<string, unknown>) => void
} = {}) {
  const { data: chats = [] } = useQuery({
    queryKey: ['chat-streams'],
    queryFn: () => getChatStreams(),
  })

  return (
    <div className="flex h-[min(70vh,48rem)] min-h-80 min-w-0 flex-col">
      <MutualGroupsView chats={chats} onSectionSaved={onSectionSaved} />
    </div>
  )
}

export function ChatManagementPage() {
  return (
    <main className="p-4 md:p-6">
      <SharedGroupsSettings />
    </main>
  )
}
