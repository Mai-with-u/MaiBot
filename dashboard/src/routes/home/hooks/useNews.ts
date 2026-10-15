/**
 * useNews —— 首页资讯领域 hook（页面逻辑下沉）。
 *
 * 挂载时拉取一次资讯列表，带模块级 TTL 缓存，重复进入首页不会立即重新请求。
 * 设计判断：
 * - 资讯不需要轮询，手动刷新走 fetchNews(true) 强制刷新。
 * - 失败时保留上一次成功的数据，仅暴露错误信息供卡片展示重试入口。
 */
import { useCallback, useEffect, useRef, useState } from 'react'

import { getNews, type NewsItem } from '@/lib/news-api'

const NEWS_CACHE_TTL = 10 * 60_000

// 模块级缓存（跨组件实例存活）
let newsCache: { timestamp: number; items: NewsItem[] } | null = null

function getCachedNews(): NewsItem[] | null {
  if (!newsCache || Date.now() - newsCache.timestamp > NEWS_CACHE_TTL) {
    return null
  }
  return newsCache.items
}

function getNewsErrorMessage(error: unknown): string {
  if (error instanceof Error && error.message.trim()) {
    return error.message
  }
  return '资讯加载失败，请稍后重试'
}

export function useNews() {
  const [news, setNews] = useState<NewsItem[] | null>(newsCache?.items ?? null)
  const [isNewsLoading, setIsNewsLoading] = useState(!newsCache)
  const [newsError, setNewsError] = useState<string | null>(null)

  // 使用 ref 跟踪组件是否已卸载，防止内存泄漏
  const isMountedRef = useRef(true)
  useEffect(() => {
    isMountedRef.current = true
    return () => {
      isMountedRef.current = false
    }
  }, [])

  const fetchNews = useCallback(async (force = false) => {
    const cachedNews = force ? null : getCachedNews()
    if (cachedNews) {
      setNews(cachedNews)
      setNewsError(null)
      setIsNewsLoading(false)
      return
    }

    setIsNewsLoading(true)
    try {
      const data = await getNews(force)
      if (isMountedRef.current) {
        newsCache = { timestamp: Date.now(), items: data.items }
        setNews(data.items)
        setNewsError(null)
      }
    } catch (error) {
      console.error('获取首页资讯失败:', error)
      if (isMountedRef.current) {
        setNewsError(getNewsErrorMessage(error))
      }
    } finally {
      if (isMountedRef.current) {
        setIsNewsLoading(false)
      }
    }
  }, [])

  // 首页挂载时主动拉取一次；这是数据获取 effect，允许它更新 loading/data 状态。
  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    void fetchNews()
  }, [fetchNews])
  /* eslint-enable react-hooks/set-state-in-effect */

  return {
    news,
    isNewsLoading,
    newsError,
    fetchNews,
  }
}
