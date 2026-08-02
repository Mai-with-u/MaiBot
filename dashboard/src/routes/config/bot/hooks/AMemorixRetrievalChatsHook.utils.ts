import type { ChatStream } from '@/lib/chat-management-api'
import { formatChatDisplayName } from '@/lib/chat-display'

export type RetrievalFilterKind = 'chat_stream' | 'chat_summary' | 'episode'

export interface RetrievalChatTokenOption {
  key: string
  label: string
  token: string
  description: string
}

export interface RetrievalChatsCopy {
  badge: string
  emptyText: string
  helperText: string
  title: string
}

const formatChatTarget = (chat: ChatStream): string => {
  const suffix = chat.chat_type === 'group' ? '群聊' : '私聊'
  const name = formatChatDisplayName(chat.display_name || chat.target_id || chat.session_id, chat.account_id)
  return `${name} (${suffix} · ${chat.platform})`
}

export const buildAMemorixRetrievalChatTokenOptions = (
  chats: ChatStream[]
): RetrievalChatTokenOption[] => {
  const optionMap = new Map<string, RetrievalChatTokenOption>()
  chats.forEach((chat) => {
    const sessionId = chat.session_id.trim()
    if (!sessionId) return
    const token = `stream:${sessionId}`
    optionMap.set(token, {
      key: token,
      label: formatChatTarget(chat),
      token,
      description: `${chat.platform}:${chat.target_id || sessionId} · ${sessionId}`,
    })
  })
  return Array.from(optionMap.values()).sort((left, right) => left.label.localeCompare(right.label, 'zh-CN'))
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
      helperText:
        '只影响跨聊天流的 source_type=chat_summary 或 source=chat_summary:<session_id> 检索命中。',
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
    helperText:
      '只影响跨聊天流的普通 paragraph/relation 命中；聊天总结和 Episode 使用各自的过滤范围。',
    title: '普通聊天流跨聊天流过滤范围',
  }
}
