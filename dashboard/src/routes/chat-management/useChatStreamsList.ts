/**
 * useChatStreamsList —— 聊天流管理页列表状态机（页面逻辑下沉）。
 *
 * 全量拉取后在客户端按搜索/类型过滤，PAGE_SIZE=10 切片分页；
 * 不套 useDataList（那会变成服务端分页）。
 */
import { useEffect, useMemo, useState, type Dispatch, type SetStateAction } from 'react'

import { useQuery, type UseQueryResult } from '@tanstack/react-query'

import {
  getChatStreamDetail,
  getChatStreams,
  type ChatStream,
  type ChatStreamDetail,
  type ChatStreamType,
} from '@/lib/chat-management-api'

const PAGE_SIZE = 10

export type ChatTypeFilter = 'all' | ChatStreamType
export type ChatManagementView = 'groups' | 'streams'

function matchesSearch(chat: ChatStream, query: string): boolean {
  const normalizedQuery = query.trim().toLowerCase()
  if (!normalizedQuery) {
    return true
  }

  return [
    chat.id,
    chat.display_name,
    chat.session_id,
    chat.chat_type,
    chat.target_id,
    chat.platform,
    chat.account_id,
    chat.group_id,
    chat.group_name,
    chat.user_id,
    chat.user_nickname,
    chat.user_cardname,
  ]
    .filter(Boolean)
    .some((value) => String(value).toLowerCase().includes(normalizedQuery))
}

function matchesTypeFilter(chat: ChatStream, filter: ChatTypeFilter): boolean {
  return filter === 'all' || chat.chat_type === filter
}

export interface UseChatStreamsListResult {
  activeView: ChatManagementView
  setActiveView: Dispatch<SetStateAction<ChatManagementView>>
  search: string
  setSearch: Dispatch<SetStateAction<string>>
  typeFilter: ChatTypeFilter
  setTypeFilter: Dispatch<SetStateAction<ChatTypeFilter>>
  setPage: Dispatch<SetStateAction<number>>
  selectedChat: ChatStream | null
  setSelectedChat: Dispatch<SetStateAction<ChatStream | null>>
  deletingChat: ChatStream | null
  setDeletingChat: Dispatch<SetStateAction<ChatStream | null>>
  chats: ChatStream[]
  error: Error | null
  isFetching: boolean
  isLoading: boolean
  refetch: () => void
  detailQuery: UseQueryResult<ChatStreamDetail, Error>
  filteredChats: ChatStream[]
  pageCount: number
  currentPage: number
  paginatedChats: ChatStream[]
  visibleStart: number
  visibleEnd: number
  groupCount: number
  privateCount: number
  handleChatDeleted: (sessionId: string) => void
}

export function useChatStreamsList(): UseChatStreamsListResult {
  const [activeView, setActiveView] = useState<ChatManagementView>(() => {
    if (typeof window === 'undefined') {
      return 'streams'
    }
    return new URLSearchParams(window.location.search).get('view') === 'groups'
      ? 'groups'
      : 'streams'
  })
  const [search, setSearch] = useState('')
  const [typeFilter, setTypeFilter] = useState<ChatTypeFilter>('all')
  const [page, setPage] = useState(1)
  const [selectedChat, setSelectedChat] = useState<ChatStream | null>(null)
  const [deletingChat, setDeletingChat] = useState<ChatStream | null>(null)
  const {
    data: chats = [],
    error,
    isFetching,
    isLoading,
    refetch,
  } = useQuery({
    queryKey: ['chat-streams'],
    queryFn: () => getChatStreams(),
  })
  const detailQuery = useQuery({
    queryKey: ['chat-stream-detail', selectedChat?.session_id],
    queryFn: () => getChatStreamDetail(selectedChat?.session_id ?? ''),
    enabled: Boolean(selectedChat?.session_id),
  })

  const filteredChats = useMemo(
    () =>
      chats.filter((chat) => matchesTypeFilter(chat, typeFilter) && matchesSearch(chat, search)),
    [chats, search, typeFilter]
  )
  const pageCount = Math.max(1, Math.ceil(filteredChats.length / PAGE_SIZE))
  const currentPage = Math.min(page, pageCount)
  const paginatedChats = useMemo(() => {
    const start = (currentPage - 1) * PAGE_SIZE
    return filteredChats.slice(start, start + PAGE_SIZE)
  }, [currentPage, filteredChats])
  const visibleStart = filteredChats.length === 0 ? 0 : (currentPage - 1) * PAGE_SIZE + 1
  const visibleEnd = Math.min(currentPage * PAGE_SIZE, filteredChats.length)
  const groupCount = chats.filter((chat) => chat.chat_type === 'group').length
  const privateCount = chats.length - groupCount

  useEffect(() => {
    const frameId = window.requestAnimationFrame(() => setPage(1))
    return () => window.cancelAnimationFrame(frameId)
  }, [search, typeFilter])

  useEffect(() => {
    if (page > pageCount) {
      const frameId = window.requestAnimationFrame(() => setPage(pageCount))
      return () => window.cancelAnimationFrame(frameId)
    }
  }, [page, pageCount])

  const handleChatDeleted = (sessionId: string) => {
    if (selectedChat?.session_id === sessionId) {
      setSelectedChat(null)
    }
  }

  return {
    activeView,
    setActiveView,
    search,
    setSearch,
    typeFilter,
    setTypeFilter,
    setPage,
    selectedChat,
    setSelectedChat,
    deletingChat,
    setDeletingChat,
    chats,
    error,
    isFetching,
    isLoading,
    refetch,
    detailQuery,
    filteredChats,
    pageCount,
    currentPage,
    paginatedChats,
    visibleStart,
    visibleEnd,
    groupCount,
    privateCount,
    handleChatDeleted,
  }
}
