import { Link, useNavigate } from '@tanstack/react-router'
import {
  ArrowRight,
  ArrowUpDown,
  ExternalLink,
  GripVertical,
  Pencil,
  Plus,
  RefreshCw,
  Send,
  Sparkles,
  X,
} from 'lucide-react'
import {
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useMemo,
  useState,
  type CSSProperties,
  type FormEvent,
  type KeyboardEvent,
  type ReactNode,
} from 'react'
import { useTranslation } from 'react-i18next'
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

import { RestartOverlay } from '@/components/restart-overlay'
import { SearchSuggestions } from '@/components/search-suggestions'
import { Board, BoardGrid, BoardModule, BoardRule, BoardScreen } from '@/components/ui/board'
import { Button } from '@/components/ui/button'
import { CHART_INITIAL_DIMENSION } from '@/components/ui/chart'
import { ConversationAvatar } from '@/components/ui/conversation'
import { SegmentedMeter } from '@/components/ui/segmented-meter'
import { StatusLight } from '@/components/ui/status-light'
import { Tooltip as UiTooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { matchSearchItems, useSearchIndex } from '@/hooks/use-search-index'
import type { NewsItem } from '@/lib/news-api'
import { stashQuickChatMessage } from '@/lib/quick-chat'
import { RestartProvider, useRestart } from '@/lib/restart-context'
import { openUpdateNotice } from '@/lib/update-notice-events'
import { APP_VERSION } from '@/lib/version'
import { HitokotoEditorDialog } from '@/routes/home/HitokotoEditorDialog'
import { NewsDialog } from '@/routes/home/NewsCard'
import { NewsStrip } from '@/routes/home/NewsStrip'
import { useBotStatus } from '@/routes/home/hooks/useBotStatus'
import { useDashboardData } from '@/routes/home/hooks/useDashboardData'
import { useFeatureStatus } from '@/routes/home/hooks/useFeatureStatus'
import { useLocalCacheMetrics } from '@/routes/home/hooks/useLocalCacheMetrics'
import { useMaibotVersion } from '@/routes/home/hooks/useMaibotVersion'
import { useNews } from '@/routes/home/hooks/useNews'
import { useNewsReadState } from '@/routes/home/hooks/useNewsReadState'
import { useQuickShortcuts } from '@/routes/home/hooks/useQuickShortcuts'
import { useReviewStats } from '@/routes/home/hooks/useReviewStats'
import { formatCacheRate, formatNumber } from '@/routes/home/StatisticsCards'
import {
  formatChartTimeAxis,
  formatTokenAxis,
  selectChartTimeSeries,
} from '@/routes/home/statistics-chart-utils'
import type { TimeSeriesData } from '@/routes/home/types'
import {
  HOME_CARD_SPANS,
  HOME_CARD_ZONE,
  homeGridRows,
  insertDesktopPetCard,
  loadHomeLayout,
  missingHomeCards,
  resetHomeLayout,
  saveHomeLayout,
  type HomeCardId,
  type HomeCardSlot,
  type HomeCardSpan,
  type HomeLayout,
  type HomeZone,
  visibleStatsSlots,
} from './home-layout'
import { DesktopPetCardActions, DesktopPetCardBody } from './DesktopPetCard'
import { getDesktopPetStatus } from '@/lib/desktop-pet-api'
import { useQuery } from '@tanstack/react-query'

const ExpressionReviewer = lazy(() =>
  import('@/components/expression-reviewer').then((module) => ({
    default: module.ExpressionReviewer,
  }))
)

const TIME_RANGES = [24, 168, 720] as const
const PERIOD_LABELS = ['24H', '7D', '30D'] as const
const SPAN_LABELS: Record<HomeCardSpan, string> = { 3: 'S', 4: '⅓', 6: 'M', 12: 'L' }

/** 一张卡片的外观与内容；排在哪、占几列由首页排法决定 */
interface HomeCardDef {
  code: string
  title: string
  tone: number
  actions?: ReactNode
  body: ReactNode
}
const COMPOSER_MODE_STORAGE_KEY = 'maibot-observation-composer-mode'
const COMPOSER_MODES = ['chat', 'ask'] as const
type ComposerMode = (typeof COMPOSER_MODES)[number]

function loadComposerMode(): ComposerMode {
  try {
    return localStorage.getItem(COMPOSER_MODE_STORAGE_KEY) === 'ask' ? 'ask' : 'chat'
  } catch {
    return 'chat'
  }
}

function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1)
  const value = bytes / 1024 ** index
  return `${value.toFixed(value >= 10 || index === 0 ? 0 : 1)} ${units[index]}`
}

/** 麦麦观察首页：一块 12 列面板收纳运行状态与用量，底部是直达本地聊天室的输入条。 */
export function ObservationHome() {
  return (
    <RestartProvider>
      <ObservationHomeContent />
    </RestartProvider>
  )
}

function ObservationHomeContent() {
  const { t, i18n } = useTranslation()
  const locale = i18n.resolvedLanguage ?? i18n.language
  const navigate = useNavigate()
  const { triggerRestart, isRestarting } = useRestart()
  const { botStatus, fetchBotStatus } = useBotStatus()
  const { dashboardData, timeRange, setTimeRange, fetchDashboardData } = useDashboardData()
  const { featureStatus, fetchFeatureStatus } = useFeatureStatus()
  const { localCacheStats, isLocalCacheStatsLoading, fetchLocalCacheStats } = useLocalCacheMetrics()
  const { uncheckedCount, fetchReviewStats } = useReviewStats()
  const { hitokoto, hitokotoSettings, fetchHitokoto, saveHitokotoSettings } = useMaibotVersion()
  const newsState = useNews()
  const newsReadState = useNewsReadState()
  const [selectedNews, setSelectedNews] = useState<NewsItem | null>(null)
  const [isReviewerOpen, setIsReviewerOpen] = useState(false)
  const [hitokotoEditorOpen, setHitokotoEditorOpen] = useState(false)
  const [draft, setDraft] = useState('')
  const [composerMode, setComposerMode] = useState<ComposerMode>(loadComposerMode)
  const helpQuery = composerMode === 'ask' ? draft.trim().toLowerCase() : ''
  const { searchItems, configSearchItems } = useSearchIndex(Boolean(helpQuery))
  const helpMatches = helpQuery
    ? matchSearchItems([...searchItems, ...configSearchItems], helpQuery, 6)
    : []
  const [layout, setLayout] = useState<HomeLayout>(loadHomeLayout)
  const [editing, setEditing] = useState(false)
  const [libraryOpen, setLibraryOpen] = useState(true)
  const [dragId, setDragId] = useState<HomeCardId | null>(null)
  // 桌宠状态：开启时 3 秒刷新一次；没开时放慢，只用来发现用户刚刚开启
  const petStatusQuery = useQuery({
    queryKey: ['desktop-pet-status'],
    queryFn: ({ signal }) => getDesktopPetStatus(signal),
    refetchInterval: (query) => (query.state.data?.enabled ? 3_000 : 30_000),
    retry: false,
  })
  const petStatus = petStatusQuery.data
  const petEnabled = Boolean(petStatus?.enabled)
  const statsSlots = visibleStatsSlots(layout, petEnabled)

  const handleRestart = useCallback(async () => {
    await triggerRestart()
  }, [triggerRestart])
  const { selectedQuickShortcuts } = useQuickShortcuts({
    isRestarting,
    handleRestart,
    uncheckedCount,
    onOpenReviewer: () => setIsReviewerOpen(true),
  })

  useEffect(() => {
    void fetchBotStatus()
    void fetchDashboardData()
    fetchFeatureStatus()
    fetchLocalCacheStats()
    fetchReviewStats()
    fetchHitokoto()
  }, [
    fetchBotStatus,
    fetchDashboardData,
    fetchFeatureStatus,
    fetchHitokoto,
    fetchLocalCacheStats,
    fetchReviewStats,
  ])

  const openNews = (item: NewsItem) => {
    newsReadState.markRead(item)
    setSelectedNews(item)
  }

  const summary = dashboardData?.summary
  const pending = '—'
  const series = useMemo(
    () => (dashboardData ? selectChartTimeSeries(dashboardData, timeRange) : []),
    [dashboardData, timeRange]
  )
  const models = useMemo(() => {
    const stats = [...(dashboardData?.model_stats ?? [])].sort(
      (a, b) => b.request_count - a.request_count
    )
    const total = stats.reduce((sum, item) => sum + item.request_count, 0)
    const top = stats.slice(0, 3)
    const rest = stats.slice(3).reduce((sum, item) => sum + item.request_count, 0)
    const rows = top.map((item) => ({ name: item.model_name, value: item.request_count }))
    if (rest > 0) rows.push({ name: t('observation.otherModels'), value: rest })
    return { rows, total }
  }, [dashboardData?.model_stats, t])
  const runtimeState = botStatus ? (botStatus.running ? 'running' : 'stopped') : 'unknown'
  const directories = localCacheStats?.directories ?? []
  const diskRows = [
    {
      key: 'images',
      label: t('home.storage.images'),
      size: directories.find((item) => item.key === 'images')?.total_size ?? 0,
    },
    {
      key: 'emoji',
      label: t('home.storage.emoji'),
      size: directories.find((item) => item.key === 'emoji')?.total_size ?? 0,
    },
    {
      key: 'logs',
      label: t('home.storage.logs'),
      size: directories.find((item) => item.key === 'logs')?.total_size ?? 0,
    },
    {
      key: 'database',
      label: t('home.storage.database'),
      size: localCacheStats?.database.total_size ?? 0,
    },
  ]
  const diskTotal =
    directories.reduce((sum, item) => sum + item.total_size, 0) +
    (localCacheStats?.database.total_size ?? 0)
  const news = (newsState.news ?? []).slice(0, 3)
  const unreadCount = (newsState.news ?? []).filter((item) => newsReadState.isUnread(item)).length
  const rangeLabel = t(
    `home.timeRange.${timeRange === 24 ? '24h' : timeRange === 168 ? '7d' : '30d'}`
  )

  const updateLayout = (next: HomeLayout) => {
    setLayout(next)
    saveHomeLayout(next)
  }

  // 开启桌宠后把桌宠卡片放进图表行；用户移除过就不再自动放回
  useEffect(() => {
    if (!petEnabled) return
    const next = insertDesktopPetCard(layout)
    if (next !== layout) {
      setLayout(next)
      saveHomeLayout(next)
    }
  }, [layout, petEnabled])
  const updateZone = (zone: HomeZone, slots: HomeCardSlot[]) =>
    updateLayout({ ...layout, [zone]: slots })
  const resizeCard = (zone: HomeZone, id: HomeCardId, span: HomeCardSpan) =>
    updateZone(
      zone,
      layout[zone].map((slot) => (slot.id === id ? { ...slot, span } : slot))
    )
  const removeCard = (zone: HomeZone, id: HomeCardId) =>
    updateLayout({
      ...layout,
      [zone]: layout[zone].filter((slot) => slot.id !== id),
      // 记住用户主动关掉了桌宠卡片，之后不再自动放回
      ...(id === 'pet' ? { petDismissed: true } : {}),
    })
  const addCard = (id: HomeCardId) => {
    const zone = HOME_CARD_ZONE[id]
    const span: HomeCardSpan = id === 'pet' ? 3 : zone === 'stats' ? 6 : 3
    updateLayout({
      ...layout,
      [zone]: [...layout[zone], { id, span }],
      ...(id === 'pet' ? { petDismissed: false } : {}),
    })
  }
  // 把 id 挪到 index 位置；位置没变就不写存储，拖动经过同一张卡时不会反复重排
  const moveCard = (zone: HomeZone, id: HomeCardId, index: number) => {
    const list = [...layout[zone]]
    const from = list.findIndex((slot) => slot.id === id)
    if (from < 0) return
    const [item] = list.splice(from, 1)
    const to = Math.max(0, Math.min(index, list.length))
    if (to === from) return
    list.splice(to, 0, item)
    updateZone(zone, list)
  }
  const dragOverCard = (zone: HomeZone, targetId: HomeCardId, after: boolean) => {
    if (!dragId || dragId === targetId || HOME_CARD_ZONE[dragId] !== zone) return
    const others = layout[zone].filter((slot) => slot.id !== dragId)
    const target = others.findIndex((slot) => slot.id === targetId)
    moveCard(zone, dragId, target + (after ? 1 : 0))
  }

  const changeComposerMode = (next: string) => {
    const mode: ComposerMode = next === 'ask' ? 'ask' : 'chat'
    setComposerMode(mode)
    try {
      localStorage.setItem(COMPOSER_MODE_STORAGE_KEY, mode)
    } catch {
      // 存储不可用时只影响下次打开时的默认模式
    }
  }

  const submitDraft = (event: FormEvent) => {
    event.preventDefault()
    // 答疑交给独立帮助页，聊天带着草稿进入本地聊天室。
    if (composerMode === 'ask') {
      const question = draft.trim()
      if (!question) return
      void navigate({ to: '/help-history', state: { helpDraft: { id: Date.now(), question } } })
      setDraft('')
      return
    }
    if (draft.trim()) stashQuickChatMessage(draft)
    void navigate({ to: '/chat', search: {} })
  }

  const cards: Record<HomeCardId, HomeCardDef> = {
    status: {
      code: 'STATUS',
      title: t('home.botStatus.title'),
      tone: 0,
      body: (
        <BoardScreen className="flex flex-col justify-between gap-3">
          <div className="flex items-end justify-between gap-3">
            <strong data-observation-hero={runtimeState}>
              {t(`home.botStatus.${runtimeState}`)}
            </strong>
            {botStatus && (
              <span className="flex flex-col items-end gap-0.5">
                <span data-board-label="true">{t('observation.uptime')}</span>
                <span data-board-number="true" className="text-lg">
                  {t('home.time.hoursMinutes', {
                    hours: Math.floor(botStatus.uptime / 3600),
                    minutes: Math.floor((botStatus.uptime % 3600) / 60),
                  })}
                </span>
              </span>
            )}
          </div>
          <div data-board-divider="true" className="flex gap-6 pt-3 text-sm">
            <span className="inline-flex items-center gap-2">
              <StatusLight status={featureStatus.visualEnabled ? 'on' : 'off'} decorative />
              {t('home.botStatus.visualEnabled')}
            </span>
            <span className="inline-flex items-center gap-2">
              <StatusLight status={featureStatus.memoryEnabled ? 'on' : 'off'} decorative />
              {t('home.botStatus.memoryEnabled')}
            </span>
            {petEnabled && (
              <span className="inline-flex items-center gap-2">
                <StatusLight status={petStatus?.ready ? 'on' : 'mixed'} decorative />
                {t('observation.pet.enabledLamp')}
              </span>
            )}
          </div>
        </BoardScreen>
      ),
    },
    quote: {
      code: 'QUOTE',
      title: t('home.hitokoto.title'),
      tone: 1,
      actions: (
        <>
          <button
            type="button"
            data-observation-card-action="true"
            aria-label={t('observation.refreshQuote')}
            title={t('observation.refreshQuote')}
            onClick={() => void fetchHitokoto()}
          >
            <RefreshCw aria-hidden="true" />
          </button>
          <button
            type="button"
            data-observation-card-action="true"
            aria-label={t('home.hitokoto.edit')}
            title={t('home.hitokoto.edit')}
            onClick={() => setHitokotoEditorOpen(true)}
          >
            <Pencil aria-hidden="true" />
          </button>
        </>
      ),
      body: (
        <BoardScreen className="flex flex-col justify-between gap-2">
          <p data-observation-quote-text="true">{hitokoto?.hitokoto ?? t('observation.loading')}</p>
          {hitokoto?.from && <span data-observation-quote-from="true">—— {hitokoto.from}</span>}
        </BoardScreen>
      ),
    },
    keys: {
      code: 'KEYS',
      title: t('home.quickActions.title'),
      tone: 2,
      body: (
        <BoardScreen flush className="grid grid-cols-2 grid-rows-3 gap-2">
          {selectedQuickShortcuts.slice(0, 6).map((shortcut) => {
            const Icon = shortcut.icon
            const content = (
              <>
                <Icon
                  className={
                    shortcut.id === 'action:restart' && isRestarting ? 'animate-spin' : undefined
                  }
                />
                <span className="min-w-0 flex-1 truncate text-left">{shortcut.label}</span>
                {shortcut.badge && <span data-observation-key-badge="true">{shortcut.badge}</span>}
                {shortcut.external && <ExternalLink className="h-3.5 w-3.5 shrink-0" />}
              </>
            )
            if (shortcut.href && !shortcut.external) {
              const url = new URL(shortcut.href, window.location.origin)
              return (
                <Button
                  key={shortcut.id}
                  variant="outline"
                  asChild
                  data-observation-shortcut="true"
                >
                  <Link
                    to={url.pathname}
                    search={Object.fromEntries(url.searchParams)}
                    hash={url.hash.slice(1)}
                    title={shortcut.label}
                  >
                    {content}
                  </Link>
                </Button>
              )
            }
            if (shortcut.href) {
              return (
                <Button
                  key={shortcut.id}
                  variant="outline"
                  asChild
                  data-observation-shortcut="true"
                >
                  <a
                    href={shortcut.href}
                    target="_blank"
                    rel="noopener noreferrer"
                    title={shortcut.label}
                  >
                    {content}
                  </a>
                </Button>
              )
            }
            return (
              <Button
                key={shortcut.id}
                variant="outline"
                data-observation-shortcut="true"
                onClick={shortcut.action}
                disabled={shortcut.disabled}
                title={shortcut.label}
              >
                {content}
              </Button>
            )
          })}
        </BoardScreen>
      ),
    },
    disk: {
      code: 'DISK',
      title: t('home.storage.title'),
      tone: 0,
      actions: (
        <Link to="/data-transfer" hash="local-cache" data-board-link="true">
          {t('home.storage.manage')} <ArrowRight aria-hidden="true" />
        </Link>
      ),
      body: (
        <BoardScreen className="flex flex-col justify-between gap-2">
          <div className="flex items-baseline justify-between">
            <strong
              data-board-number="true"
              data-observation-disk-total="true"
              className="text-2xl"
            >
              {localCacheStats
                ? formatBytes(diskTotal)
                : isLocalCacheStatsLoading
                  ? t('home.storage.reading')
                  : pending}
            </strong>
          </div>
          {diskRows.map((row) => (
            <div
              key={row.key}
              data-observation-disk-row="true"
              className="grid grid-cols-[3.5rem_minmax(0,1fr)_4.5rem] items-center gap-3 text-xs"
            >
              <span className="truncate">{row.label}</span>
              <SegmentedMeter
                label={row.label}
                total={diskTotal}
                segments={[{ label: row.label, value: row.size, className: 'bg-primary' }]}
                className="h-2"
              />
              <span data-board-number="true" className="text-right">
                {formatBytes(row.size)}
              </span>
            </div>
          ))}
        </BoardScreen>
      ),
    },
    news: {
      code: 'NEWS',
      title: t('home.news.title'),
      tone: 1,
      actions:
        unreadCount > 0 ? (
          <span data-board-badge="true">{t('observation.unread', { count: unreadCount })}</span>
        ) : undefined,
      body: (
        <BoardScreen className="flex flex-col overflow-y-auto">
          {news.length === 0 ? (
            <p data-board-label="true" className="m-auto">
              {newsState.isNewsLoading ? t('observation.loading') : t('observation.noNews')}
            </p>
          ) : (
            news.map((item) => (
              <button
                key={item.id}
                type="button"
                data-observation-news-row="true"
                onClick={() => openNews(item)}
              >
                <span
                  data-unread={newsReadState.isUnread(item) ? 'true' : 'false'}
                  aria-hidden="true"
                />
                <span className="min-w-0 flex-1 truncate text-left">{item.title}</span>
                <span data-board-label="true">
                  {new Date(item.published_at).toLocaleDateString(locale, {
                    month: '2-digit',
                    day: '2-digit',
                  })}
                </span>
              </button>
            ))
          )}
        </BoardScreen>
      ),
    },
    usage: {
      code: 'USAGE',
      title: t('home.stats.overviewTitle'),
      tone: 2,
      actions: (
        <Link to="/statistics" data-board-link="true">
          {t('observation.detailedStats')} <ArrowRight aria-hidden="true" />
        </Link>
      ),
      body: (
        <BoardScreen
          className="grid grid-cols-[repeat(2,minmax(0,1.2fr))_repeat(2,minmax(0,1fr))] grid-rows-2"
          flush={false}
        >
          <div data-observation-usage-big="true">
            <span data-board-label="true">{t('home.stats.totalRequests')}</span>
            <strong data-board-number="true">
              {summary ? formatNumber(summary.total_requests, locale) : pending}
            </strong>
            <span data-board-label="true">{rangeLabel}</span>
          </div>
          <div data-observation-usage-big="true">
            <span data-board-label="true">{t('home.stats.totalCost')}</span>
            <strong data-board-number="true">
              {summary ? `¥${summary.total_cost.toFixed(2)}` : pending}
            </strong>
            <span data-board-label="true">{rangeLabel}</span>
          </div>
          {[
            {
              label: t('home.stats.tokenUsage'),
              value: summary ? formatNumber(summary.total_tokens, locale) : pending,
            },
            {
              label: t('home.stats.avgResponse'),
              value: summary ? `${summary.avg_response_time.toFixed(2)}s` : pending,
            },
            {
              label: t('home.stats.onlineTime'),
              value: summary ? `${(summary.online_time / 3600).toFixed(1)}h` : pending,
            },
            {
              label: t('home.stats.messageProcessing'),
              value: summary ? formatNumber(summary.total_messages, locale) : pending,
            },
          ].map((metric) => (
            <div key={metric.label} data-observation-usage-small="true">
              <span data-board-label="true">{metric.label}</span>
              <strong data-board-number="true">{metric.value}</strong>
            </div>
          ))}
        </BoardScreen>
      ),
    },
    cache: {
      code: 'CACHE',
      title: t('home.cache.title'),
      tone: 0,
      body: (
        <BoardScreen className="flex flex-col">
          {[
            { label: t('home.cache.all'), rate: summary?.cache_hit_rate ?? null },
            { label: t('home.cache.chat'), rate: summary?.chat_cache_hit_rate ?? null },
          ].map((item) => (
            <div key={item.label} data-observation-cache-row="true">
              <div className="flex items-baseline justify-between gap-2">
                <span data-board-label="true">{item.label}</span>
                <strong data-board-number="true" className="text-2xl">
                  {summary ? formatCacheRate(item.rate) : pending}
                </strong>
              </div>
              <SegmentedMeter
                label={item.label}
                total={100}
                segments={[
                  {
                    label: item.label,
                    value: item.rate === null ? 0 : item.rate * 100,
                    className: 'bg-primary',
                  },
                ]}
                className="h-2"
              />
            </div>
          ))}
        </BoardScreen>
      ),
    },
    models: {
      code: 'MODELS',
      title: t('home.charts.modelDistribution'),
      tone: 1,
      body: (
        <BoardScreen className="flex flex-col justify-between gap-2">
          {models.rows.length === 0 ? (
            <p data-board-label="true" className="m-auto">
              {t('observation.noData')}
            </p>
          ) : (
            <>
              <div data-observation-model-bar="true" aria-hidden="true">
                {models.rows.map((row, index) => (
                  <span key={row.name} data-model-index={index} style={{ flexGrow: row.value }} />
                ))}
              </div>
              {models.rows.map((row, index) => (
                <div
                  key={row.name}
                  data-observation-model-row={index}
                  className="flex items-center gap-2 text-xs"
                >
                  <span data-observation-model-dot={index} aria-hidden="true" />
                  <span className="min-w-0 flex-1 truncate">{row.name}</span>
                  <span data-board-number="true">
                    {models.total > 0
                      ? `${((row.value / models.total) * 100).toFixed(1)}%`
                      : pending}
                  </span>
                </div>
              ))}
            </>
          )}
        </BoardScreen>
      ),
    },
    requests: {
      code: 'REQUESTS',
      title: t('home.charts.requestTrend'),
      tone: 2,
      body: (
        <BoardScreen flush className="min-h-0">
          <ObservationBarChart
            data={series}
            keys={[
              {
                key: 'requests',
                label: t('home.stats.totalRequests'),
                color: 'var(--board-chart-1)',
              },
            ]}
            timeRange={timeRange}
            locale={locale}
          />
        </BoardScreen>
      ),
    },
    tokens: {
      code: 'TOKENS',
      title: t('home.charts.tokenUsage'),
      tone: 0,
      body: (
        <BoardScreen flush className="min-h-0">
          <ObservationBarChart
            data={series}
            keys={[
              {
                key: 'input_tokens',
                label: t('home.stats.inputTokens'),
                color: 'var(--board-chart-1)',
              },
              {
                key: 'output_tokens',
                label: t('home.stats.outputTokens'),
                color: 'var(--board-chart-2)',
              },
            ]}
            timeRange={timeRange}
            locale={locale}
            tokenAxis
          />
        </BoardScreen>
      ),
    },
    pet: {
      code: 'PET',
      title: t('observation.pet.title'),
      tone: 1,
      actions: <DesktopPetCardActions />,
      body: petStatus ? (
        <DesktopPetCardBody status={petStatus} onChanged={() => void petStatusQuery.refetch()} />
      ) : (
        <BoardScreen className="flex items-center justify-center text-sm">{t('observation.loading')}</BoardScreen>
      ),
    },
  }

  const renderZone = (zone: HomeZone) =>
    (zone === 'stats' ? statsSlots : layout[zone]).map((slot, index) => (
      <HomeCard
        key={slot.id}
        def={cards[slot.id]}
        slot={slot}
        editing={editing}
        dragging={dragId === slot.id}
        onResize={(span) => resizeCard(zone, slot.id, span)}
        onRemove={() => removeCard(zone, slot.id)}
        onMove={(offset) => moveCard(zone, slot.id, index + offset)}
        onDragStart={() => setDragId(slot.id)}
        onDragOver={(after) => dragOverCard(zone, slot.id, after)}
        onDragEnd={() => setDragId(null)}
      />
    ))

  return (
    <div data-observation-home="true" data-editing={editing ? 'true' : 'false'}>
      <header data-observation-home-head="true">
        <div data-observation-news-strip="true">
          <NewsStrip news={newsState.news} readState={newsReadState} onOpen={openNews} />
        </div>
        <span data-observation-head-spacer="true" />
        <div data-observation-versions="true">
          <button type="button" onClick={() => openUpdateNotice('maibot')}>
            <span>MaiBot</span>
            <span data-observation-version-value="true">
              {botStatus?.version ? `V${botStatus.version}` : t('home.versionCard.unknown')}
            </span>
          </button>
          <button type="button" onClick={() => openUpdateNotice('console')}>
            <span>WebUI</span>
            <span data-observation-version-value="true">V{APP_VERSION}</span>
          </button>
        </div>
        {editing ? (
          <div data-observation-edit-tools="true">
            <Button
              type="button"
              variant="outline"
              size="sm"
              aria-pressed={libraryOpen}
              onClick={() => setLibraryOpen((open) => !open)}
            >
              <Plus aria-hidden="true" />
              {t('observation.cardLibrary')}
            </Button>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => setLayout(resetHomeLayout())}
            >
              {t('observation.resetLayout')}
            </Button>
            <Button type="button" size="sm" onClick={() => setEditing(false)}>
              {t('observation.doneEditing')}
            </Button>
          </div>
        ) : (
          <UiTooltip>
            <TooltipTrigger asChild>
              <Button
                type="button"
                variant="outline"
                size="icon"
                data-observation-edit-button="true"
                aria-label={t('observation.editCards')}
                onClick={() => {
                  setLibraryOpen(true)
                  setEditing(true)
                }}
              >
                <Pencil aria-hidden="true" />
              </Button>
            </TooltipTrigger>
            <TooltipContent side="bottom">{t('observation.editCards')}</TooltipContent>
          </UiTooltip>
        )}
      </header>

      <div data-observation-board-row="true">
        <Board sections>
          {layout.top.length > 0 && (
            <BoardGrid
              rows={homeGridRows(layout.top, 'var(--obs-top-row)', 'var(--obs-chart-row)')}
            >
              {renderZone('top')}
            </BoardGrid>
          )}
          {statsSlots.length > 0 && (
            <>
              <BoardRule code="STATS">
                <PeriodSwitch
                  value={timeRange}
                  label={t('home.stats.timeRange')}
                  onChange={setTimeRange}
                />
              </BoardRule>
              <BoardGrid
                grow
                rows={homeGridRows(statsSlots, 'var(--obs-stat-row)', 'var(--obs-chart-row)')}
              >
                {renderZone('stats')}
              </BoardGrid>
            </>
          )}
          {layout.top.length === 0 && statsSlots.length === 0 && (
            <p data-observation-board-empty="true">{t('observation.emptyBoard')}</p>
          )}
        </Board>
        {editing && libraryOpen && (
          <HomeCardLibrary
            missing={missingHomeCards(layout, petEnabled)}
            cards={cards}
            onAdd={addCard}
            onClose={() => setLibraryOpen(false)}
          />
        )}
      </div>

      <form
        data-observation-composer="true"
        data-mode={composerMode}
        onSubmit={submitDraft}
        aria-label={t(composerMode === 'ask' ? 'observation.askTitle' : 'observation.quickChat')}
      >
        <SearchSuggestions items={helpMatches} />
        {composerMode === 'ask' ? (
          <Button asChild variant="outline" size="sm" className="shrink-0 whitespace-nowrap">
            <Link to="/help-history">{t('observation.helpHistory')}</Link>
          </Button>
        ) : (
          <ConversationAvatar name={t('chat.botNameFallback')} bot />
        )}
        <label data-observation-composer-input="true">
          <span className="sr-only">
            {composerMode === 'ask' ? t('observation.askTitle') : t('observation.quickChat')}
          </span>
          {/* 占位文字自己画：原生 placeholder 换文案时没法过渡 */}
          <ModeStack
            mode={composerMode}
            hidden={draft.length > 0}
            data-observation-composer-placeholder="true"
            chat={t('observation.quickChatPlaceholder')}
            ask={t('observation.askPlaceholder')}
          />
          <input
            value={draft}
            maxLength={composerMode === 'ask' ? 500 : undefined}
            onChange={(event) => setDraft(event.target.value)}
          />
        </label>
        {/* 当前档：发送键左边一行小字，亮灯表示正在用的模式 */}
        <span data-observation-mode-label="true">
          <span data-observation-mode-lamp="true" aria-hidden="true" />
          <ModeStack
            mode={composerMode}
            chat={t('observation.modeChatLabel')}
            ask={t('observation.modeAskLabel')}
          />
        </span>
        {/* 发送键右侧接一截换档尾段：按主键发送，按尾段在聊天 / 答疑之间切换 */}
        <span data-observation-send-group="true">
          <Button type="submit" data-observation-send="true">
            <ModeStack
              mode={composerMode}
              layerClassName="gap-2"
              chat={
                <>
                  <Send aria-hidden="true" />
                  {t('observation.send')}
                </>
              }
              ask={
                <>
                  <Sparkles aria-hidden="true" />
                  {t('observation.ask')}
                </>
              }
            />
          </Button>
          <UiTooltip>
            <TooltipTrigger asChild>
              <Button
                type="button"
                data-observation-mode-tail="true"
                aria-label={t('observation.switchMode', {
                  mode:
                    composerMode === 'ask'
                      ? t('observation.modeChatLabel')
                      : t('observation.modeAskLabel'),
                })}
                onClick={() => changeComposerMode(composerMode === 'ask' ? 'chat' : 'ask')}
              >
                <ArrowUpDown aria-hidden="true" />
              </Button>
            </TooltipTrigger>
            <TooltipContent side="top">
              {t('observation.switchMode', {
                mode:
                  composerMode === 'ask'
                    ? t('observation.modeChatLabel')
                    : t('observation.modeAskLabel'),
              })}
            </TooltipContent>
          </UiTooltip>
        </span>
      </form>

      <NewsDialog selectedNews={selectedNews} onClose={() => setSelectedNews(null)} />
      {hitokotoEditorOpen && (
        <HitokotoEditorDialog
          initialSettings={hitokotoSettings}
          onOpenChange={setHitokotoEditorOpen}
          onSave={saveHitokotoSettings}
        />
      )}
      <RestartOverlay />
      {isReviewerOpen && (
        <Suspense fallback={null}>
          <ExpressionReviewer
            open
            onOpenChange={(open) => {
              setIsReviewerOpen(open)
              if (!open) fetchReviewStats()
            }}
          />
        </Suspense>
      )}
    </div>
  )
}

/** 统计刻线右端的三档拨杆：一颗键帽停在当前档，档位名刻在槽里 */
function PeriodSwitch({
  value,
  label,
  onChange,
}: {
  value: number
  label: string
  onChange: (hours: number) => void
}) {
  const index = Math.max(0, TIME_RANGES.indexOf(value as (typeof TIME_RANGES)[number]))
  // 方向键在三档之间循环，焦点跟着选中的那一档走
  const handleKeyDown = (event: KeyboardEvent<HTMLButtonElement>) => {
    const offset =
      event.key === 'ArrowRight' || event.key === 'ArrowDown'
        ? 1
        : event.key === 'ArrowLeft' || event.key === 'ArrowUp'
          ? -1
          : 0
    if (!offset) return
    event.preventDefault()
    const next = (index + offset + TIME_RANGES.length) % TIME_RANGES.length
    onChange(TIME_RANGES[next])
    event.currentTarget.parentElement?.querySelectorAll('button')[next]?.focus()
  }
  return (
    <div
      role="radiogroup"
      aria-label={label}
      data-observation-period="true"
      style={{ '--period-index': index } as CSSProperties}
    >
      <span data-observation-period-thumb="true" aria-hidden="true" />
      {TIME_RANGES.map((hours, i) => (
        <button
          key={hours}
          type="button"
          role="radio"
          aria-checked={hours === value}
          tabIndex={hours === value ? 0 : -1}
          onClick={() => onChange(hours)}
          onKeyDown={handleKeyDown}
        >
          {PERIOD_LABELS[i]}
        </button>
      ))}
    </div>
  )
}

/** 首页的一张卡片；编辑态下标题条换成拖动柄、宽度档和移除键，内容变淡且不可点 */
function HomeCard({
  def,
  slot,
  editing,
  dragging,
  onResize,
  onRemove,
  onMove,
  onDragStart,
  onDragOver,
  onDragEnd,
}: {
  def: HomeCardDef
  slot: HomeCardSlot
  editing: boolean
  dragging: boolean
  onResize: (span: HomeCardSpan) => void
  onRemove: () => void
  onMove: (offset: number) => void
  onDragStart: () => void
  onDragOver: (after: boolean) => void
  onDragEnd: () => void
}) {
  const { t } = useTranslation()
  const editActions = (
    <>
      <span role="group" aria-label={t('observation.cardWidth')} data-observation-card-sizes="true">
        {HOME_CARD_SPANS.map((span) => (
          <button
            key={span}
            type="button"
            aria-pressed={slot.span === span}
            onClick={() => onResize(span)}
          >
            {SPAN_LABELS[span]}
          </button>
        ))}
      </span>
      <button
        type="button"
        data-observation-card-remove="true"
        aria-label={t('observation.removeCard', { name: def.title })}
        title={t('observation.removeCard', { name: def.title })}
        onClick={onRemove}
      >
        <X aria-hidden="true" />
      </button>
    </>
  )
  return (
    <BoardModule
      code={def.code}
      title={def.title}
      span={slot.span}
      tone={def.tone}
      actions={editing ? editActions : def.actions}
      leading={
        editing ? (
          <button
            type="button"
            data-observation-card-grip="true"
            aria-label={t('observation.moveCard', { name: def.title })}
            title={t('observation.moveCard', { name: def.title })}
            onKeyDown={(event) => {
              if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') onMove(-1)
              else if (event.key === 'ArrowRight' || event.key === 'ArrowDown') onMove(1)
              else return
              event.preventDefault()
            }}
          >
            <GripVertical aria-hidden="true" />
          </button>
        ) : undefined
      }
      data-span={slot.span}
      data-editing={editing ? 'true' : 'false'}
      data-dragging={dragging ? 'true' : 'false'}
      draggable={editing}
      onDragStart={(event) => {
        event.dataTransfer.effectAllowed = 'move'
        event.dataTransfer.setData('text/plain', slot.id)
        onDragStart()
      }}
      onDragOver={(event) => {
        if (!editing) return
        event.preventDefault()
        const rect = event.currentTarget.getBoundingClientRect()
        onDragOver(event.clientX > rect.left + rect.width / 2)
      }}
      onDrop={(event) => event.preventDefault()}
      onDragEnd={onDragEnd}
    >
      {def.body}
    </BoardModule>
  )
}

/** 编辑态右侧的卡片库：列出不在面板上的卡片，点 + 加回它所属的那一段 */
function HomeCardLibrary({
  missing,
  cards,
  onAdd,
  onClose,
}: {
  missing: HomeCardId[]
  cards: Record<HomeCardId, HomeCardDef>
  onAdd: (id: HomeCardId) => void
  onClose: () => void
}) {
  const { t } = useTranslation()
  return (
    <aside data-observation-library="true" aria-label={t('observation.cardLibrary')}>
      <header data-observation-library-head="true">
        <span data-board-rule-code="true">LIBRARY</span>
        <span>{t('observation.cardLibrary')}</span>
        <button
          type="button"
          data-observation-card-action="true"
          aria-label={t('observation.closeLibrary')}
          onClick={onClose}
        >
          <X aria-hidden="true" />
        </button>
      </header>
      <p data-observation-library-hint="true">{t('observation.libraryHint')}</p>
      {missing.length === 0 ? (
        <p data-observation-library-empty="true">{t('observation.libraryEmpty')}</p>
      ) : (
        <ul data-observation-library-list="true">
          {missing.map((id) => (
            <li key={id}>
              <span data-observation-library-name="true">
                <b>{cards[id].title}</b>
                <span>
                  {cards[id].code} ·{' '}
                  {HOME_CARD_ZONE[id] === 'stats'
                    ? t('observation.zoneStats')
                    : t('observation.zoneTop')}
                </span>
              </span>
              <Button
                type="button"
                variant="outline"
                size="icon"
                aria-label={t('observation.addCard', { name: cards[id].title })}
                onClick={() => onAdd(id)}
              >
                <Plus aria-hidden="true" />
              </Button>
            </li>
          ))}
        </ul>
      )}
    </aside>
  )
}

/**
 * 输入条里随模式变化的一格内容：两种模式的内容叠在同一格里，只切换显隐并做 CSS 过渡，
 * 不挂载 / 卸载节点，格子宽度取两者较宽的一个，切换时旁边的元素不会跳动。
 */
function ModeStack({
  mode,
  chat,
  ask,
  className,
  layerClassName,
  hidden = false,
  ...rest
}: {
  mode: ComposerMode
  chat: ReactNode
  ask: ReactNode
  className?: string
  layerClassName?: string
  /** 整格淡出（占位文字在输入后隐藏） */
  hidden?: boolean
  'data-observation-composer-name'?: string
  'data-observation-composer-placeholder'?: string
}) {
  return (
    <span
      {...rest}
      data-observation-mode-stack="true"
      data-hidden={hidden ? 'true' : 'false'}
      aria-hidden={rest['data-observation-composer-placeholder'] ? true : undefined}
      className={className}
    >
      {COMPOSER_MODES.map((layer) => (
        <span
          key={layer}
          data-layer={layer}
          data-active={mode === layer ? 'true' : 'false'}
          aria-hidden={mode === layer ? undefined : true}
          className={layerClassName}
        >
          {layer === 'ask' ? ask : chat}
        </span>
      ))}
    </span>
  )
}

function ObservationBarChart({
  data,
  keys,
  timeRange,
  locale,
  tokenAxis = false,
}: {
  data: TimeSeriesData[]
  keys: Array<{ key: keyof TimeSeriesData; label: string; color: string }>
  timeRange: number
  locale: string
  tokenAxis?: boolean
}) {
  // 中尺寸下图表只有半屏宽，刻度减到三格、时间标签拉开，避免挤成一排数字
  const [compact, setCompact] = useState(false)
  return (
    <div data-observation-chart="true">
      {/* 图表铺在绝对定位层里：量出的尺寸不回传给网格，行高只由面板决定 */}
      <div data-observation-chart-canvas="true">
        <ResponsiveContainer
          width="100%"
          height="100%"
          initialDimension={CHART_INITIAL_DIMENSION}
          onResize={(width) => setCompact(width < 640)}
        >
          <BarChart
            data={data}
            margin={{ top: 8, right: 8, bottom: 0, left: 0 }}
            barCategoryGap="18%"
          >
            <CartesianGrid vertical={false} stroke="var(--board-chart-grid)" />
            <XAxis
              dataKey="timestamp"
              tickLine={false}
              axisLine={{ stroke: 'var(--board-chart-axis)' }}
              tick={{ fill: 'var(--board-chart-ink)', fontSize: 11 }}
              tickFormatter={(value) => formatChartTimeAxis(String(value), locale, timeRange)}
              minTickGap={compact ? 64 : 24}
            />
            <YAxis
              tickLine={false}
              axisLine={false}
              width={44}
              tickCount={compact ? 3 : 5}
              tick={{ fill: 'var(--board-chart-ink)', fontSize: 11 }}
              tickFormatter={
                tokenAxis ? (value) => formatTokenAxis(Number(value), locale) : undefined
              }
            />
            <Tooltip
              cursor={{ fill: 'var(--board-chart-grid)' }}
              contentStyle={{
                background: 'hsl(var(--popover))',
                border: '1px solid hsl(var(--border))',
                color: 'hsl(var(--popover-foreground))',
                fontSize: 12,
              }}
            />
            {keys.map((item) => (
              <Bar
                key={item.key}
                dataKey={item.key}
                name={item.label}
                stackId="stack"
                fill={item.color}
                radius={0}
              />
            ))}
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  )
}
