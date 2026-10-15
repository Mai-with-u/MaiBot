import { useEffect, useState } from 'react'

import type { NewsItem } from '@/lib/news-api'

const STORAGE_KEY = 'maibot:news:read'

function readStoredKeys(): string[] {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]')
    return Array.isArray(value) ? value.filter((key): key is string => typeof key === 'string') : []
  } catch {
    return []
  }
}

function newsKey(item: NewsItem): string {
  return item.id || `${item.title}:${item.published_at}`
}

/** 已读记录保存在当前浏览器；打开详情或原文才标记为已读。 */
export function useNewsReadState() {
  const [readKeys, setReadKeys] = useState(readStoredKeys)

  useEffect(() => {
    const onStorage = (event: StorageEvent) => {
      if (event.key === STORAGE_KEY || event.key === null) setReadKeys(readStoredKeys())
    }
    window.addEventListener('storage', onStorage)
    return () => window.removeEventListener('storage', onStorage)
  }, [])

  const isUnread = (item: NewsItem) => !readKeys.includes(newsKey(item))

  const markRead = (item: NewsItem) => {
    const next = [...new Set([...readKeys, ...readStoredKeys(), newsKey(item)])]
    setReadKeys(next)
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(next))
    } catch {
      // 浏览器禁止存储时，仍保留本次打开期间的已读状态。
    }
  }

  return { isUnread, markRead }
}
