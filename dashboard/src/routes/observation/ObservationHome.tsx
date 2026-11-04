import { Link, useNavigate } from '@tanstack/react-router'
import { ArrowRight, ArrowUpDown, ExternalLink, Send, Sparkles } from 'lucide-react'
import {
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useMemo,
  useState,
  type FormEvent,
  type ReactNode,
} from 'react'
import { useTranslation } from 'react-i18next'
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

import { useAskMai } from '@/components/observation-layout/ask-context'
import { RestartOverlay } from '@/components/restart-overlay'
import { Board, BoardModule, BoardScreen } from '@/components/ui/board'
import { Button } from '@/components/ui/button'
import { CHART_INITIAL_DIMENSION } from '@/components/ui/chart'
import { ConversationAvatar } from '@/components/ui/conversation'
import { SegmentedControl } from '@/components/ui/management'
import { SegmentedMeter } from '@/components/ui/segmented-meter'
import { StatusLight } from '@/components/ui/status-light'
import { Tooltip as UiTooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
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
import { ObservationQuote } from './ObservationQuote'

const ExpressionReviewer = lazy(() =>
  import('@/components/expression-reviewer').then((module) => ({
    default: module.ExpressionReviewer,
  }))
)

const TIME_RANGES = [24, 168, 720] as const
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
  const askMai = useAskMai()

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
    // 答疑：交给全局搜索里的 AI 查文档与插件市场；聊天：带着草稿进入本地聊天室
    if (composerMode === 'ask') {
      askMai(draft.trim())
      setDraft('')
      return
    }
    if (draft.trim()) stashQuickChatMessage(draft)
    void navigate({ to: '/chat', search: {} })
  }

  return (
    <div data-observation-home="true">
      <header data-observation-home-head="true">
        <div data-observation-home-heading="true">
          <h1>{t('sidebar.menu.maisakaMonitor')}</h1>
          <ObservationQuote
            text={
              hitokoto
                ? `“${hitokoto.hitokoto}”${hitokoto.from ? ` —— ${hitokoto.from}` : ''}`
                : ''
            }
            editLabel={t('home.hitokoto.edit')}
            onEdit={() => setHitokotoEditorOpen(true)}
          />
        </div>
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
        <div data-observation-news-strip="true">
          <NewsStrip news={newsState.news} readState={newsReadState} onOpen={openNews} />
        </div>
        <SegmentedControl
          aria-label={t('home.stats.timeRange')}
          value={String(timeRange)}
          onValueChange={(next) => setTimeRange(Number(next))}
          options={TIME_RANGES.map((hours) => ({
            value: String(hours),
            label: t(`home.timeRange.${hours === 24 ? '24h' : hours === 168 ? '7d' : '30d'}`),
          }))}
        />
      </header>

      <Board rows="minmax(11.5rem, auto) minmax(12.5rem, auto) minmax(10rem, 1fr)">
        <BoardModule code="STATUS" title={t('home.botStatus.title')} span={3} tone={0}>
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
            </div>
          </BoardScreen>
        </BoardModule>

        <BoardModule code="KEYS" title={t('home.quickActions.title')} span={3} tone={1}>
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
                  {shortcut.badge && (
                    <span data-observation-key-badge="true">{shortcut.badge}</span>
                  )}
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
        </BoardModule>

        <BoardModule
          code="DISK"
          title={t('home.storage.title')}
          span={3}
          tone={2}
          actions={
            <Link to="/data-transfer" hash="local-cache" data-board-link="true">
              {t('home.storage.manage')} <ArrowRight aria-hidden="true" />
            </Link>
          }
        >
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
        </BoardModule>

        <BoardModule
          code="NEWS"
          title={t('home.news.title')}
          span={3}
          tone={0}
          actions={
            unreadCount > 0 ? (
              <span data-board-badge="true">{t('observation.unread', { count: unreadCount })}</span>
            ) : undefined
          }
        >
          <BoardScreen className="flex flex-col">
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
        </BoardModule>

        <BoardModule
          code="USAGE"
          title={t('home.stats.overviewTitle')}
          span={6}
          tone={1}
          actions={
            <Link to="/statistics" data-board-link="true">
              {t('observation.detailedStats')} <ArrowRight aria-hidden="true" />
            </Link>
          }
        >
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
        </BoardModule>

        <BoardModule code="CACHE" title={t('home.cache.title')} span={3} tone={2}>
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
        </BoardModule>

        <BoardModule code="MODELS" title={t('home.charts.modelDistribution')} span={3} tone={0}>
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
        </BoardModule>

        <BoardModule code="REQUESTS" title={t('home.charts.requestTrend')} span={6} tone={1}>
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
        </BoardModule>

        <BoardModule code="TOKENS" title={t('home.charts.tokenUsage')} span={6} tone={2}>
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
        </BoardModule>
      </Board>

      <form
        data-observation-composer="true"
        data-mode={composerMode}
        onSubmit={submitDraft}
        aria-label={t('observation.quickChat')}
      >
        <ModeStack
          mode={composerMode}
          className="shrink-0"
          chat={<ConversationAvatar name={t('chat.botNameFallback')} bot />}
          ask={
            <span data-observation-composer-icon="true" aria-hidden="true">
              <Sparkles />
            </span>
          }
        />
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
          <input value={draft} onChange={(event) => setDraft(event.target.value)} />
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
