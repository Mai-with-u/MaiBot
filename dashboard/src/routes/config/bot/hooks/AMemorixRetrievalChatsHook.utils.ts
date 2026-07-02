import type { MemoryImportChatTargetPayload } from '@/lib/memory-api'

export type RetrievalChatTokenKind = 'stream' | 'group' | 'user' | 'private'
export type RetrievalFilterKind = 'chat_stream' | 'chat_summary' | 'episode'

export interface RetrievalChatTokenOption {
  key: string
  label: string
  token: string
  description: string
  kind: RetrievalChatTokenKind
}

export interface RetrievalChatsCopy {
  badge: string
  emptyText: string
  helperText: string
  title: string
}

const TOKEN_KIND_LABELS: Record<RetrievalChatTokenKind, string> = {
  stream: '聊天流',
  group: '群聊',
  user: '用户',
  private: '私聊',
}

const TOKEN_KIND_ORDER: RetrievalChatTokenKind[] = ['stream', 'group', 'user', 'private']

const formatChatTarget = (target: MemoryImportChatTargetPayload): string => {
  const suffix = target.is_group ? '群聊' : '私聊'
  const platform = target.platform ? ` · ${target.platform}` : ''
  return `${target.chat_name || target.chat_id} (${suffix}${platform})`
}

const createOption = (
  target: MemoryImportChatTargetPayload,
  kind: RetrievalChatTokenKind,
  tokenValue: string,
): RetrievalChatTokenOption => ({
  key: `${kind}:${target.chat_id}:${tokenValue}`,
  label: formatChatTarget(target),
  token: `${kind}:${tokenValue}`,
  description: `${TOKEN_KIND_LABELS[kind]} · ${tokenValue}`,
  kind,
})

export const buildAMemorixRetrievalChatTokenOptions = (
  targets: MemoryImportChatTargetPayload[],
): RetrievalChatTokenOption[] => {
  const options: RetrievalChatTokenOption[] = []
  const seen = new Set<string>()

  const pushOption = (
    target: MemoryImportChatTargetPayload,
    kind: RetrievalChatTokenKind,
    tokenValue?: string | null,
  ) => {
    const cleanValue = String(tokenValue ?? '').trim()
    if (!cleanValue) {
      return
    }
    const token = `${kind}:${cleanValue}`
    if (seen.has(token)) {
      return
    }
    seen.add(token)
    options.push(createOption(target, kind, cleanValue))
  }

  for (const target of targets) {
    pushOption(target, 'stream', target.chat_id)
    if (target.is_group) {
      pushOption(target, 'group', target.group_id)
      continue
    }
    pushOption(target, 'private', target.user_id)
    pushOption(target, 'user', target.user_id)
  }

  return options.sort((left, right) => {
    const kindDelta = TOKEN_KIND_ORDER.indexOf(left.kind) - TOKEN_KIND_ORDER.indexOf(right.kind)
    if (kindDelta !== 0) {
      return kindDelta
    }
    return left.label.localeCompare(right.label, 'zh-CN')
  })
}

export const resolveAMemorixRetrievalChatsCopy = (fieldPath: string): RetrievalChatsCopy => {
  if (fieldPath === 'a_memorix.filter.chats') {
    return {
      badge: '入口过滤',
      emptyText: '当前未限制哪些聊天流可以使用记忆。',
      helperText: '影响当前聊天流是否允许使用记忆能力；黑名单会阻止列表内聊天流写入和查询记忆。',
      title: '聊天过滤范围',
    }
  }

  if (fieldPath.includes('.chat_summary.')) {
    return {
      badge: '聊天总结',
      emptyText: '当前未限制其他聊天流的聊天总结命中。',
      helperText: '只影响跨聊天流的 source_type=chat_summary 或 source=chat_summary:<session_id> 检索命中。',
      title: '聊天总结跨聊天流过滤范围',
    }
  }

  if (fieldPath.includes('.episode.')) {
    return {
      badge: 'Episode',
      emptyText: '当前未限制其他聊天流的 Episode 命中。',
      helperText: '只影响跨聊天流的 type=episode 检索命中；人物画像和画像证据不受这里控制。',
      title: 'Episode 跨聊天流过滤范围',
    }
  }

  return {
    badge: '普通聊天流',
    emptyText: '当前未限制其他聊天流的普通聊天记忆命中。',
    helperText: '只影响跨聊天流的普通 paragraph/relation 命中；聊天总结和 Episode 使用各自的过滤范围。',
    title: '普通聊天流跨聊天流过滤范围',
  }
}
