import { ArrowRight, ChevronRight, X } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import type { NewsItem } from '@/lib/news-api'

import type { useNewsReadState } from './hooks/useNewsReadState'

const ROLL_INTERVAL_MS = 4000

/** 首页顶部资讯条：多条未读时轮流滚动并提供下一条按钮，只剩一条时才能关闭。 */
export function NewsStrip({ news, readState, onOpen }: {
  news: NewsItem[] | null | undefined
  readState: ReturnType<typeof useNewsReadState>
  onOpen: (item: NewsItem) => void
}) {
  const { t } = useTranslation()
  const [index, setIndex] = useState(0)
  const [paused, setPaused] = useState(false)

  const latest = news?.[0]
  const unread = news?.filter(readState.isUnread) ?? []
  const items = unread.length > 0 ? unread : latest ? [latest] : []
  const rolling = items.length > 1

  useEffect(() => {
    if (!rolling || paused) return
    const timer = window.setInterval(() => setIndex((value) => value + 1), ROLL_INTERVAL_MS)
    return () => window.clearInterval(timer)
  }, [rolling, paused])

  if (!news || !latest || readState.isStripDismissed(news)) return null

  const position = index % items.length
  const current = items[position]

  return (
    <div
      data-home-news-strip="true"
      className="text-muted-foreground flex min-w-0 max-w-full flex-1 items-center gap-2 text-xs font-normal normal-case tracking-normal"
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
      onFocus={() => setPaused(true)}
      onBlur={() => setPaused(false)}
    >
      <button
        type="button"
        data-home-news-open="true"
        className="hover:text-foreground flex min-w-0 flex-1 items-center gap-2 text-left transition-colors focus-visible:outline-2 focus-visible:outline-offset-2"
        title={current.title}
        onClick={() => onOpen(current)}
      >
        <span data-home-news-label="true" className="shrink-0">{t('home.news.title')}</span>
        <span className="min-w-0 flex-1 overflow-hidden" aria-live={rolling ? 'polite' : undefined}>
          <span key={`${position}-${current.id || current.title}`} data-home-news-title="true" className="block truncate">
            {current.title}
          </span>
        </span>
        <ArrowRight className="h-3 w-3 shrink-0" aria-hidden="true" />
      </button>
      {rolling ? (
        <button
          type="button"
          data-home-news-next="true"
          className="hover:text-foreground flex shrink-0 items-center gap-1 tabular-nums transition-colors focus-visible:outline-2 focus-visible:outline-offset-2"
          title={t('home.news.next')}
          aria-label={t('home.news.next')}
          onClick={() => setIndex(position + 1)}
        >
          {position + 1}/{items.length}
          <ChevronRight className="h-3 w-3" aria-hidden="true" />
        </button>
      ) : (
        <button
          type="button"
          data-home-news-close="true"
          className="hover:text-foreground shrink-0 rounded-sm p-0.5 transition-colors focus-visible:outline-2 focus-visible:outline-offset-2"
          title={t('home.news.dismiss')}
          aria-label={t('home.news.dismiss')}
          onClick={() => readState.dismissStrip(news)}
        >
          <X className="h-3 w-3" aria-hidden="true" />
        </button>
      )}
    </div>
  )
}
