/**
 * useMutualGroups —— 共享组管理数据层（页面逻辑下沉）。
 *
 * 配置加载、组 CRUD、添加聊天对话框筛选留在本 hook；JSX 仍在 view。
 * 不把 bot 配置草稿塞进 useConfigForm。
 */
import { useMemo, useState, type Dispatch, type SetStateAction } from 'react'

import { useMutation, useQuery, useQueryClient, type UseQueryResult } from '@tanstack/react-query'

import { useToast } from '@/hooks/use-toast'
import { formatChatDisplayName } from '@/lib/chat-display'
import { type ChatStream, type ChatStreamType } from '@/lib/chat-management-api'
import { getBotConfig, updateBotConfigSection } from '@/lib/config-api'

export type MutualGroupKind = 'expression' | 'jargon' | 'memory'

export const MUTUAL_GROUP_CHAT_RESULT_LIMIT = 50

export const MUTUAL_GROUP_KIND_LABEL: Record<MutualGroupKind, string> = {
  expression: '表达',
  jargon: '黑话',
  memory: '记忆',
}

export interface TargetItem {
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

function getChatTypeText(chatType: ChatStreamType): string {
  return chatType === 'group' ? '群聊' : '私聊'
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

export function targetKey(target: TargetItem): string {
  return `${target.platform}:${target.item_id}:${getTargetRuleType(target)}`
}

export function targetLabel(target: TargetItem): string {
  return `${target.platform}:${target.item_id}:${getChatTypeText(getTargetRuleType(target))}`
}

export function getTargetDisplayName(
  target: TargetItem,
  chatNameByTargetKey: Map<string, string>
): string {
  return chatNameByTargetKey.get(targetKey(target)) ?? '未找到聊天流'
}

export function chatToTarget(chat: ChatStream): TargetItem {
  return {
    platform: chat.platform,
    item_id: getChatLogicalId(chat),
    rule_type: chat.chat_type,
  }
}

export interface UseMutualGroupsResult {
  kind: MutualGroupKind
  setKind: Dispatch<SetStateAction<MutualGroupKind>>
  addDialogGroupIndex: number | null
  addDialogSearch: string
  setAddDialogSearch: Dispatch<SetStateAction<string>>
  selectedTargetKeys: string[]
  configQuery: UseQueryResult<Record<string, unknown>, Error>
  groups: ChatStreamGroupConfig[]
  chatNameByTargetKey: Map<string, string>
  addDialogChats: ChatStream[]
  visibleAddDialogChats: ChatStream[]
  isAddDialogLimited: boolean
  selectedTargetKeySet: Set<string>
  editingDisabled: boolean
  globalMemorySharingEnabled: boolean
  createGroup: () => void
  openAddDialog: (groupIndex: number) => void
  closeAddDialog: () => void
  toggleAddDialogChat: (target: TargetItem) => void
  applySelectedChatsToGroup: () => void
  removeTarget: (groupIndex: number, targetIndex: number) => void
  deleteGroup: (groupIndex: number) => void
}

export function useMutualGroups(chats: ChatStream[]): UseMutualGroupsResult {
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
    onSuccess: () => {
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

  return {
    kind,
    setKind,
    addDialogGroupIndex,
    addDialogSearch,
    setAddDialogSearch,
    selectedTargetKeys,
    configQuery,
    groups,
    chatNameByTargetKey,
    addDialogChats,
    visibleAddDialogChats,
    isAddDialogLimited,
    selectedTargetKeySet,
    editingDisabled,
    globalMemorySharingEnabled,
    createGroup,
    openAddDialog,
    closeAddDialog,
    toggleAddDialogChat,
    applySelectedChatsToGroup,
    removeTarget,
    deleteGroup,
  }
}
