import { useEffect, useState } from 'react'

import type { NewsItem } from '@/lib/news-api'
import { NOTIFICATIONS_RESET_EVENT } from '@/lib/settings-manager'

const STORAGE_KEY = 'maibot:news:read'
const STRIP_DISMISSED_KEY = 'maibot:news:strip-dismissed'

function readStoredKeys(storageKey = STORAGE_KEY): string[] {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(storageKey) || '[]')
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
  const [dismissedKeys, setDismissedKeys] = useState(() => readStoredKeys(STRIP_DISMISSED_KEY))

  useEffect(() => {
    const onStorage = (event: StorageEvent) => {
      if (event.key === STORAGE_KEY || event.key === null) setReadKeys(readStoredKeys())
      if (event.key === STRIP_DISMISSED_KEY || event.key === null) setDismissedKeys(readStoredKeys(STRIP_DISMISSED_KEY))
    }
    const onReset = () => {
      setReadKeys(readStoredKeys())
      setDismissedKeys(readStoredKeys(STRIP_DISMISSED_KEY))
    }
    window.addEventListener('storage', onStorage)
    window.addEventListener(NOTIFICATIONS_RESET_EVENT, onReset)
    return () => {
      window.removeEventListener('storage', onStorage)
      window.removeEventListener(NOTIFICATIONS_RESET_EVENT, onReset)
    }
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

  /** 首页资讯条关掉时记下当时的全部资讯，之后出现任何一条新资讯都会重新显示。 */
  const isStripDismissed = (news: NewsItem[]) =>
    news.every((item) => dismissedKeys.includes(newsKey(item)))

  const dismissStrip = (news: NewsItem[]) => {
    const next = news.map(newsKey)
    setDismissedKeys(next)
    try {
      localStorage.setItem(STRIP_DISMISSED_KEY, JSON.stringify(next))
    } catch {
      // 浏览器禁止存储时，仅在本次打开期间隐藏。
    }
  }

  return { isUnread, markRead, isStripDismissed, dismissStrip }
}
