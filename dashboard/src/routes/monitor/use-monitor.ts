/**
 * 监控页面共享工具和钩子
 */
import { useState, useEffect, useCallback } from 'react'
import { getChatList } from '@/lib/expression-api'
import type { ChatInfo } from '@/types/expression'

/**
 * 聊天名称映射 Hook
 * 从表达方式 API 获取聊天列表，构建 chat_id -> chat_name 映射
 */
export function useChatNameMap() {
  const [chatNameMap, setChatNameMap] = useState<Map<string, string>>(new Map())
  const [loading, setLoading] = useState(true)

  const loadChatNameMap = useCallback(async () => {
    try {
      setLoading(true)
      const response = await getChatList()
      if (response?.data) {
        const nameMap = new Map<string, string>()
        response.data.forEach((chat: ChatInfo) => {
          nameMap.set(chat.chat_id, chat.chat_name)
        })
        setChatNameMap(nameMap)
      }
    } catch (error) {
      console.error('加载聊天列表失败:', error)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    loadChatNameMap()
  }, [loadChatNameMap])

  const getChatName = useCallback((chatId: string): string => {
    return chatNameMap.get(chatId) || chatId
  }, [chatNameMap])

  return { chatNameMap, getChatName, loading, reload: loadChatNameMap }
}

/**
 * 格式化时间戳为本地时间字符串
 */
export function formatTimestamp(timestamp: number): string {
  return new Date(timestamp * 1000).toLocaleString('zh-CN')
}

/**
 * 格式化时间戳为相对时间
 */
export function formatRelativeTime(timestamp: number): string {
  const now = Date.now() / 1000
  const diff = now - timestamp
  if (diff < 60) return '刚刚'
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`
  return `${Math.floor(diff / 86400)} 天前`
}

/**
 * 自动刷新 Hook
 */
export function useAutoRefresh(
  enabled: boolean,
  callback: () => void,
  interval: number = 10000
) {
  useEffect(() => {
    if (!enabled) return

    const timer = setInterval(callback, interval)
    return () => clearInterval(timer)
  }, [enabled, callback, interval])
}
