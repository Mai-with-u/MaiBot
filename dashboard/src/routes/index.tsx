import type { CSSProperties } from 'react'
import { Link } from '@tanstack/react-router'
import {
  AlertCircle,
  CheckCircle2,
  Database,
  ExternalLink,
  FileText,
  HardDrive,
  ImageIcon,
  Plus,
  Power,
  RefreshCw,
  Smile,
} from 'lucide-react'
import { lazy, Suspense, useCallback, useContext, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { RestartOverlay } from '@/components/restart-overlay'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Checkbox } from '@/components/ui/checkbox'
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Progress } from '@/components/ui/progress'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Skeleton } from '@/components/ui/skeleton'
import { StreamlineIcon } from '@/components/ui/streamline-icon'
import { ThinkingIllustration } from '@/components/ui/thinking-illustration'
import { RestartProvider, useRestart } from '@/lib/restart-context'
import { ThemeProviderContext } from '@/lib/theme-context'
import { backendApi } from '@/lib/http'
import { cn } from '@/lib/utils'
import { APP_VERSION } from '@/lib/version'

import { useBotStatus } from './home/hooks/useBotStatus'
import { useDashboardData } from './home/hooks/useDashboardData'
import { useFeatureStatus } from './home/hooks/useFeatureStatus'
import { useLocalCacheMetrics } from './home/hooks/useLocalCacheMetrics'
import { useMaibotVersion } from './home/hooks/useMaibotVersion'
import { HomeCardManager, type HomeCardDefinition } from './home/HomeCardManager'
import { usePluginHomeCards } from './home/hooks/usePluginHomeCards'
import { useQuickShortcuts } from './home/hooks/useQuickShortcuts'
import { useReviewStats } from './home/hooks/useReviewStats'
import {
  CostTrendCard,
  DailyStatisticsCard,
  ModelDetailsCard,
  ModelDistributionCard,
  PromptCacheCard,
  RecentActivityCard,
  RequestTrendCard,
  StatisticsOverviewCard,
  TokenTrendCard,
} from './home/StatisticsCards'

const ExpressionReviewer = lazy(() =>
  import('@/components/expression-reviewer').then((module) => ({
    default: module.ExpressionReviewer,
  }))
)

// 主导出组件：包装 RestartProvider
export function IndexPage() {
  return (
    <RestartProvider>
      <IndexPageContent />
    </RestartProvider>
  )
}

interface BotPlatformConfig {
  platform?: string
  qq_account?: string | number
  platforms?: string[]
}

const UNCONFIGURED_ACCOUNT_VALUES = new Set(['', '0'])

function hasConfiguredPlatformAccount(config: BotPlatformConfig | undefined): boolean {
  if (!config) return false
  const qqAccount = String(config.qq_account ?? '').trim()
  if (!UNCONFIGURED_ACCOUNT_VALUES.has(qqAccount)) return true
  return (config.platforms ?? []).some((entry) => {
    const [, ...accountParts] = String(entry ?? '').split(':')
    const account = accountParts.join(':').trim()
    return !UNCONFIGURED_ACCOUNT_VALUES.has(account)
  })
}

// 内部实现组件
function FeatureStatusIndicator({
  accent,
  detail,
  enabled,
  label,
}: {
  accent: 'green' | 'orange' | 'yellow' | 'red'
  detail?: string
  enabled: boolean
  label: string
}) {
  const enabledColorClass = {
    green: 'text-green-600',
    orange: 'text-orange-600',
    yellow: 'text-yellow-600',
    red: 'text-red-600',
  }[accent]
  const enabledBarClass = {
    green: 'bg-green-500',
    orange: 'bg-orange-500',
    yellow: 'bg-yellow-400',
    red: 'bg-red-500',
  }[accent]

  return (
    <div
      data-dashboard-feature-status="true"
      data-accent={accent}
      data-enabled={enabled ? 'true' : 'false'}
      className={cn(
        'flex min-h-9 w-full items-center gap-2.5 px-1 py-1 font-sans text-base font-bold transition-colors',
        enabled ? enabledColorClass : 'text-muted-foreground/55'
      )}
    >
      <span
        data-dashboard-feature-status-bar="true"
        className={cn(
          'h-8 w-2.5 shrink-0 rounded-[2px] transition-colors',
          enabled ? enabledBarClass : 'bg-muted-foreground/25'
        )}
      />
      <span className="min-w-0 flex-1 truncate">
        {label}
        {detail && <span className="ml-2 text-sm font-semibold opacity-75">· {detail}</span>}
      </span>
    </div>
  )
}

function FeatureStatusLight({ enabled, label }: { enabled: boolean; label: string }) {
  return (
    <div
      data-dashboard-feature-status="true"
      data-enabled={enabled ? 'true' : 'false'}
      className="bg-background text-muted-foreground inline-flex items-center gap-1.5 rounded-md border px-2 py-1 text-xs"
    >
      <span
        data-dashboard-feature-status-light="true"
        className={cn(
          'h-2.5 w-2.5 rounded-full',
          enabled
            ? 'bg-green-500 shadow-[0_0_0_3px_rgba(34,197,94,0.18)]'
            : 'bg-muted-foreground/30'
        )}
      />
      <span>{label}</span>
    </div>
  )
}

function formatStorageBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) {
    return '0 B'
  }
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  const unitIndex = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1)
  const value = bytes / 1024 ** unitIndex
  return `${value.toFixed(value >= 10 || unitIndex === 0 ? 0 : 1)} ${units[unitIndex]}`
}

function compareVersions(left: string, right: string): number {
  const parseVersion = (version: string): number[] => {
    const match = version.replace(/^v/i, '').match(/^\d+(?:\.\d+)*/)
    return (match?.[0] ?? '0').split('.').map((part) => Number.parseInt(part, 10))
  }
  const leftParts = parseVersion(left)
  const rightParts = parseVersion(right)
  const length = Math.max(leftParts.length, rightParts.length)

  for (let index = 0; index < length; index += 1) {
    const difference = (leftParts[index] ?? 0) - (rightParts[index] ?? 0)
    if (difference !== 0) return difference
  }
  return 0
}

function IndexPageContent() {
  const { t } = useTranslation()
  const { themeConfig } = useContext(ThemeProviderContext)
  const { triggerRestart, isRestarting } = useRestart()

  // 各数据源领域 hook（页面逻辑下沉，主文件退化为薄渲染层）
  const {
    dashboardData,
    error: dashboardError,
    loading,
    loadingProgress,
    fetchDashboardData,
  } = useDashboardData()
  const { botStatus, isBotStatusLoading, fetchBotStatus } = useBotStatus()
  const { featureStatus, fetchFeatureStatus } = useFeatureStatus()
  const { localCacheStats, isLocalCacheStatsLoading, fetchLocalCacheStats } = useLocalCacheMetrics()
  const { uncheckedCount, fetchReviewStats } = useReviewStats()
  const { hitokoto, hitokotoLoading, maibotStableRelease, versionCompatibility, fetchHitokoto } =
    useMaibotVersion()
  const { pluginHomeCards } = usePluginHomeCards()

  const [isReviewerOpen, setIsReviewerOpen] = useState(false)
  const [platformAccountConfigured, setPlatformAccountConfigured] = useState<boolean | null>(null)

  const handleRestart = useCallback(async () => {
    await triggerRestart()
  }, [triggerRestart])

  const openReviewer = useCallback(() => setIsReviewerOpen(true), [])

  const fetchPlatformAccountConfig = useCallback(async () => {
    try {
      const data = await backendApi.get<{ config: { bot?: BotPlatformConfig } }>(
        '/api/webui/config/bot',
        { errorMessage: '读取平台账号配置失败' }
      )
      setPlatformAccountConfigured(hasConfiguredPlatformAccount(data.config.bot))
    } catch (error) {
      console.error('读取平台账号配置失败:', error)
      setPlatformAccountConfigured(null)
    }
  }, [])

  const {
    quickShortcutIds,
    quickShortcutDialogOpen,
    setQuickShortcutDialogOpen,
    quickShortcutSearch,
    setQuickShortcutSearch,
    isPluginShortcutsLoading,
    selectedQuickShortcuts,
    filteredQuickShortcutOptions,
    toggleQuickShortcut,
    resetQuickShortcuts,
  } = useQuickShortcuts({
    isRestarting,
    handleRestart,
    uncheckedCount,
    onOpenReviewer: openReviewer,
  })

  // 初始加载各数据源
  useEffect(() => {
    fetchDashboardData()
    fetchHitokoto()
    fetchBotStatus(true)
    fetchFeatureStatus()
    fetchLocalCacheStats()
    fetchReviewStats()
    fetchPlatformAccountConfig()
  }, [
    fetchDashboardData,
    fetchHitokoto,
    fetchBotStatus,
    fetchFeatureStatus,
    fetchLocalCacheStats,
    fetchReviewStats,
    fetchPlatformAccountConfig,
  ])

  if (dashboardError && !dashboardData) {
    return (
      <div className="flex h-[calc(100vh-200px)] items-center justify-center px-4">
        <Card className="w-full max-w-md">
          <CardHeader className="text-center">
            <AlertCircle className="text-destructive mx-auto h-10 w-10" />
            <CardTitle>仪表盘加载失败</CardTitle>
            <CardDescription>{dashboardError}</CardDescription>
          </CardHeader>
          <CardContent className="flex justify-center">
            <Button onClick={() => void fetchDashboardData(true)}>
              <RefreshCw className="mr-2 h-4 w-4" />
              重新加载
            </Button>
          </CardContent>
        </Card>
      </div>
    )
  }

  if (loading || !dashboardData) {
    return (
      <div className="flex h-[calc(100vh-200px)] items-center justify-center">
        <div className="w-full max-w-md space-y-6 px-4 text-center">
          <ThinkingIllustration size="lg" className="mx-auto" />
          <div className="space-y-2">
            <Progress value={loadingProgress} className="h-2" />
            <p className="text-muted-foreground text-xs">{loadingProgress}%</p>
          </div>
        </div>
      </div>
    )
  }

  // 格式化时间显示
  const formatTime = (seconds: number) => {
    const hours = Math.floor(seconds / 3600)
    const minutes = Math.floor((seconds % 3600) / 60)
    return t('home.time.hoursMinutes', { hours, minutes })
  }

  const localCacheDirectories = localCacheStats?.directories ?? []
  const imageCacheDirectory = localCacheDirectories.find((item) => item.key === 'images')
  const emojiCacheDirectory = localCacheDirectories.find((item) => item.key === 'emoji')
  const logCacheDirectory = localCacheDirectories.find((item) => item.key === 'logs')
  const imageCacheSize = imageCacheDirectory?.total_size ?? 0
  const emojiCacheSize = emojiCacheDirectory?.total_size ?? 0
  const logCacheSize = logCacheDirectory?.total_size ?? 0
  const databaseSize = localCacheStats?.database.total_size ?? 0
  const totalStorageSize =
    localCacheDirectories.reduce((total, item) => total + item.total_size, 0) + databaseSize
  const hasLocalCacheStats = localCacheStats !== null
  const storageDetails = [
    {
      key: 'images',
      label: t('home.storage.images'),
      size: imageCacheSize,
      detail: t('home.storage.files', { count: imageCacheDirectory?.file_count ?? 0 }),
      icon: ImageIcon,
    },
    {
      key: 'emoji',
      label: t('home.storage.emoji'),
      size: emojiCacheSize,
      detail: t('home.storage.filesAndRecords', {
        files: emojiCacheDirectory?.file_count ?? 0,
        records: emojiCacheDirectory?.db_records ?? 0,
      }),
      icon: Smile,
    },
    {
      key: 'logs',
      label: t('home.storage.logs'),
      size: logCacheSize,
      detail: t('home.storage.files', { count: logCacheDirectory?.file_count ?? 0 }),
      icon: FileText,
    },
    {
      key: 'database',
      label: t('home.storage.database'),
      size: databaseSize,
      detail: t('home.storage.databaseDetail', {
        files: localCacheStats?.database.files.length ?? 0,
        tables: localCacheStats?.database.tables.length ?? 0,
      }),
      icon: Database,
    },
  ]

  const homeCards: HomeCardDefinition[] = [
    {
      id: 'builtin:bot-status',
      title: t('home.botStatus.title'),
      width: 'small',
      category: 'status',
      source: 'builtin',
      render: () => (
        <Card className="h-full">
          <CardHeader className="pb-3">
            <CardTitle className="flex h-5 items-center gap-2 text-sm leading-5 font-medium">
              <StreamlineIcon
                name="button-power-circle-1-remix"
                fallback={Power}
                className="h-4 w-4"
              />
              {t('home.botStatus.title')}
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-3">
              {themeConfig.dashboardStyle === 'future-retro' ? (
                <div className="space-y-2">
                  {isBotStatusLoading && !botStatus ? (
                    <FeatureStatusIndicator
                      enabled={false}
                      accent="green"
                      label={t('home.botStatus.loading')}
                    />
                  ) : botStatus?.running === true ? (
                    <FeatureStatusIndicator
                      enabled
                      accent="green"
                      label={t('home.botStatus.running')}
                      detail={t('home.botStatus.uptime', {
                        time: formatTime(botStatus?.uptime ?? 0),
                      })}
                    />
                  ) : botStatus ? (
                    <FeatureStatusIndicator
                      enabled
                      accent="red"
                      label={t('home.botStatus.stopped')}
                    />
                  ) : (
                    <FeatureStatusIndicator
                      enabled={false}
                      accent="green"
                      label={t('home.botStatus.unknown')}
                    />
                  )}
                  <FeatureStatusIndicator
                    accent="orange"
                    enabled={featureStatus.visualEnabled}
                    label={t('home.botStatus.visualEnabled')}
                  />
                  <FeatureStatusIndicator
                    accent="yellow"
                    enabled={featureStatus.memoryEnabled}
                    label={t('home.botStatus.memoryEnabled')}
                  />
                </div>
              ) : (
                <>
                  <div className="flex flex-wrap items-center gap-4">
                    <div className="flex items-center gap-2">
                      {isBotStatusLoading && !botStatus ? (
                        <>
                          <div
                            data-dashboard-status-dot="true"
                            data-state="loading"
                            className="bg-muted-foreground/40 h-3 w-3 animate-pulse rounded-full"
                          />
                          <Badge
                            data-dashboard-status-badge="true"
                            data-state="loading"
                            variant="outline"
                            className="text-muted-foreground whitespace-nowrap"
                          >
                            <RefreshCw className="mr-1 h-3 w-3 animate-spin" />
                            {t('home.botStatus.loading')}
                          </Badge>
                        </>
                      ) : botStatus?.running === true ? (
                        <>
                          <div
                            data-dashboard-status-dot="true"
                            data-state="running"
                            className="h-3 w-3 animate-pulse rounded-full bg-green-500"
                          />
                          <Badge
                            data-dashboard-status-badge="true"
                            data-state="running"
                            variant="outline"
                            className="border-green-300 bg-green-50 whitespace-nowrap text-green-600"
                          >
                            <CheckCircle2 className="mr-1 h-3 w-3" />
                            {t('home.botStatus.running')}
                          </Badge>
                        </>
                      ) : botStatus ? (
                        <>
                          <div
                            data-dashboard-status-dot="true"
                            data-state="stopped"
                            className="h-3 w-3 rounded-full bg-red-500"
                          />
                          <Badge
                            data-dashboard-status-badge="true"
                            data-state="stopped"
                            variant="outline"
                            className="border-red-300 bg-red-50 whitespace-nowrap text-red-600"
                          >
                            <AlertCircle className="mr-1 h-3 w-3" />
                            {t('home.botStatus.stopped')}
                          </Badge>
                        </>
                      ) : (
                        <>
                          <div
                            data-dashboard-status-dot="true"
                            data-state="unknown"
                            className="bg-muted-foreground/40 h-3 w-3 rounded-full"
                          />
                          <Badge
                            data-dashboard-status-badge="true"
                            data-state="unknown"
                            variant="outline"
                            className="text-muted-foreground whitespace-nowrap"
                          >
                            <AlertCircle className="mr-1 h-3 w-3" />
                            {t('home.botStatus.unknown')}
                          </Badge>
                        </>
                      )}
                    </div>
                    {botStatus && (
                      <div className="text-muted-foreground flex items-center gap-2 text-xs">
                        <span>
                          {t('home.botStatus.uptime', { time: formatTime(botStatus?.uptime ?? 0) })}
                        </span>
                      </div>
                    )}
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <FeatureStatusLight
                      enabled={featureStatus.visualEnabled}
                      label={t('home.botStatus.visualEnabled')}
                    />
                    <FeatureStatusLight
                      enabled={featureStatus.memoryEnabled}
                      label={t('home.botStatus.memoryEnabled')}
                    />
                  </div>
                </>
              )}
            </div>
          </CardContent>
        </Card>
      ),
    },
    {
      id: 'builtin:quick-actions',
      title: t('home.quickActions.title'),
      width: 'medium',
      category: 'status',
      source: 'builtin',
      render: () => (
        <Card className="h-full">
          <CardContent data-home-titleless-content="true" className="relative pt-4 sm:pt-5">
            {selectedQuickShortcuts.length > 0 && (
              <Button
                variant="ghost"
                size="icon"
                onClick={() => setQuickShortcutDialogOpen(true)}
                aria-label={t('home.quickActions.customize')}
                className="absolute top-3 right-4 h-8 w-8"
              >
                <Plus className="h-4 w-4" />
              </Button>
            )}
            {selectedQuickShortcuts.length === 0 ? (
              <div className="text-muted-foreground flex flex-col gap-3 rounded-lg border border-dashed p-4 text-sm sm:flex-row sm:items-center sm:justify-between">
                <span>{t('home.quickActions.empty')}</span>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setQuickShortcutDialogOpen(true)}
                >
                  {t('home.quickActions.add')}
                </Button>
              </div>
            ) : (
              <div className="flex flex-wrap gap-2 pr-12">
                {selectedQuickShortcuts.map((shortcut) => {
                  const Icon = shortcut.icon
                  const content = (
                    <>
                      <Icon
                        className={`h-4 w-4 ${shortcut.id === 'action:restart' && isRestarting ? 'animate-spin' : ''}`}
                      />
                      <span className="min-w-0 flex-1 truncate text-left">{shortcut.label}</span>
                      {shortcut.badge && (
                        <span
                          data-quick-action-badge="true"
                          className="ml-1 shrink-0 rounded-full bg-orange-500 px-1.5 py-0.5 text-xs text-white"
                        >
                          {shortcut.badge}
                        </span>
                      )}
                      {shortcut.external && <ExternalLink className="h-3.5 w-3.5 shrink-0" />}
                    </>
                  )

                  if (shortcut.href) {
                    return (
                      <Button
                        key={shortcut.id}
                        variant="outline"
                        size="sm"
                        asChild
                        className="max-w-[14rem] justify-start gap-2 overflow-hidden sm:max-w-[18rem]"
                      >
                        <a
                          href={shortcut.href}
                          target={shortcut.external ? '_blank' : undefined}
                          rel={shortcut.external ? 'noopener noreferrer' : undefined}
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
                      size="sm"
                      onClick={shortcut.action}
                      disabled={shortcut.disabled}
                      className="max-w-[14rem] justify-start gap-2 overflow-hidden sm:max-w-[18rem]"
                      title={shortcut.label}
                    >
                      {content}
                    </Button>
                  )
                })}
              </div>
            )}
          </CardContent>
        </Card>
      ),
    },
    {
      id: 'builtin:stats-overview',
      title: t('home.stats.overviewTitle'),
      description: t('home.stats.overviewDesc'),
      width: 'wide',
      allowedWidths: ['large', 'wide', 'full'],
      preferredHeight: 'low',
      category: 'statistics',
      source: 'builtin',
      render: () => <StatisticsOverviewCard />,
    },
    {
      id: 'builtin:prompt-cache',
      title: t('home.cache.title'),
      description: t('home.cache.description'),
      width: 'medium',
      allowedWidths: ['medium', 'large'],
      preferredHeight: 'low',
      category: 'statistics',
      source: 'builtin',
      render: () => <PromptCacheCard />,
    },
    {
      id: 'builtin:request-trend',
      title: t('home.charts.requestTrend'),
      description: t('home.charts.requestTrendDescCompact'),
      width: 'large',
      allowedWidths: ['large', 'wide', 'full'],
      preferredHeight: 'high',
      category: 'statistics',
      source: 'builtin',
      render: () => <RequestTrendCard />,
    },
    {
      id: 'builtin:token-trend',
      title: t('home.charts.tokenUsage'),
      description: t('home.charts.tokenUsageSplitDesc'),
      width: 'large',
      allowedWidths: ['large', 'wide', 'full'],
      preferredHeight: 'high',
      category: 'statistics',
      source: 'builtin',
      render: () => <TokenTrendCard />,
    },
    {
      id: 'builtin:cost-trend',
      title: t('home.charts.costTrend'),
      description: t('home.charts.costTrendDesc'),
      width: 'large',
      allowedWidths: ['large', 'wide', 'full'],
      preferredHeight: 'high',
      category: 'statistics',
      source: 'builtin',
      defaultHidden: true,
      render: () => <CostTrendCard />,
    },
    {
      id: 'builtin:model-distribution',
      title: t('home.charts.modelDistribution'),
      description: t('home.charts.modelDistributionCardDesc'),
      width: 'large',
      allowedWidths: ['large', 'wide'],
      preferredHeight: 'high',
      category: 'analysis',
      source: 'builtin',
      defaultHidden: true,
      render: () => <ModelDistributionCard />,
    },
    {
      id: 'builtin:model-details',
      title: t('home.charts.modelDetails'),
      description: t('home.charts.modelDetailsTokenDesc'),
      width: 'large',
      allowedWidths: ['large', 'wide', 'full'],
      preferredHeight: 'high',
      category: 'analysis',
      source: 'builtin',
      defaultHidden: true,
      render: () => <ModelDetailsCard />,
    },
    {
      id: 'builtin:recent-activity',
      title: t('home.charts.recentActivity'),
      description: t('home.charts.recentActivityDesc'),
      width: 'full',
      allowedWidths: ['wide', 'full'],
      preferredHeight: 'high',
      category: 'analysis',
      source: 'builtin',
      defaultHidden: true,
      render: () => <RecentActivityCard />,
    },
    {
      id: 'builtin:daily-statistics',
      title: t('home.charts.dailyStats'),
      description: t('home.charts.dailyStatsRangeDesc'),
      width: 'full',
      allowedWidths: ['wide', 'full'],
      preferredHeight: 'high',
      category: 'analysis',
      source: 'builtin',
      defaultHidden: true,
      render: () => <DailyStatisticsCard />,
    },
    {
      id: 'builtin:storage',
      title: t('home.storage.title'),
      width: 'large',
      category: 'status',
      source: 'builtin',
      render: () => (
        <Card className="h-full xl:self-stretch">
          <CardContent data-home-titleless-content="true" className="pt-4 sm:pt-5">
            <div className="space-y-3">
              <div>
                <div className="text-2xl font-bold">
                  {hasLocalCacheStats
                    ? formatStorageBytes(totalStorageSize)
                    : isLocalCacheStatsLoading
                      ? t('home.storage.reading')
                      : '-'}
                </div>
                {!hasLocalCacheStats && (
                  <p className="text-muted-foreground mt-1 text-xs">
                    {isLocalCacheStatsLoading
                      ? t('home.storage.readingDescription')
                      : t('home.storage.unavailable')}
                  </p>
                )}
              </div>
              {hasLocalCacheStats && (
                <div
                  data-home-storage-details="true"
                  className="grid grid-cols-1 gap-x-5 gap-y-3 lg:grid-cols-2"
                >
                  {storageDetails.map((item) => {
                    const Icon = item.icon
                    const percent = totalStorageSize > 0 ? (item.size / totalStorageSize) * 100 : 0
                    const visiblePercent = item.size > 0 ? Math.max(percent, 2) : 0
                    return (
                      <div key={item.key} className="space-y-1.5">
                        <div className="flex min-w-0 items-center gap-2 text-xs">
                          <Icon className="text-muted-foreground h-3.5 w-3.5 shrink-0" />
                          <span className="shrink-0 font-bold">{item.label}</span>
                          <span className="text-primary shrink-0 font-semibold">
                            {formatStorageBytes(item.size)}
                          </span>
                          <span className="text-muted-foreground min-w-0 truncate">
                            {item.detail}
                          </span>
                          <span className="text-muted-foreground ml-auto shrink-0">
                            {percent.toFixed(percent >= 10 ? 0 : 1)}%
                          </span>
                        </div>
                        <div className="bg-muted h-1.5 overflow-hidden rounded-full">
                          <div
                            className="bg-primary h-full rounded-full transition-all"
                            style={{ width: `${visiblePercent}%` }}
                          />
                        </div>
                      </div>
                    )
                  })}
                </div>
              )}
              <Button variant="outline" size="sm" asChild className="w-full justify-start gap-2">
                <Link to="/settings" search={{ tab: 'local-cache' }}>
                  <HardDrive className="h-4 w-4" />
                  {t('home.storage.manage')}
                </Link>
              </Button>
            </div>
          </CardContent>
        </Card>
      ),
    },
  ]
  const firstRowCardIds = ['builtin:bot-status', 'builtin:quick-actions', 'builtin:storage']
  const orderedHomeCards = [
    ...firstRowCardIds
      .map((id) => homeCards.find((card) => card.id === id))
      .filter((card): card is HomeCardDefinition => card !== undefined),
    ...homeCards.filter((card) => !firstRowCardIds.includes(card.id)),
  ]
  const maibotUpdateAvailable = Boolean(
    botStatus?.version &&
    maibotStableRelease &&
    compareVersions(maibotStableRelease.version, botStatus.version) > 0
  )
  const versionsMismatch =
    versionCompatibility?.status !== undefined && versionCompatibility.status !== 'compatible'

  return (
    <ScrollArea className="h-full">
      <div className="space-y-2 p-4 sm:space-y-4 sm:p-6">
        {dashboardError && (
          <Card className="border-destructive/50 bg-destructive/5">
            <CardContent className="flex flex-col gap-3 pt-4 sm:flex-row sm:items-center sm:justify-between">
              <div className="text-destructive flex min-w-0 items-center gap-2 text-sm">
                <AlertCircle className="h-4 w-4 shrink-0" />
                <span className="truncate">{dashboardError}</span>
              </div>
              <Button
                variant="outline"
                size="sm"
                className="shrink-0"
                onClick={() => void fetchDashboardData(true)}
              >
                <RefreshCw className="mr-2 h-4 w-4" />
                重新加载
              </Button>
            </CardContent>
          </Card>
        )}
        {platformAccountConfigured === false && (
          <Card className="border-2 border-orange-500 bg-orange-50/80 dark:border-orange-500 dark:bg-orange-950/20">
            <CardHeader className="pb-3">
              <CardTitle className="text-2xl text-orange-700 dark:text-orange-300">
                {t('home.platformGuide.title')}
              </CardTitle>
              <CardDescription>{t('home.platformGuide.description')}</CardDescription>
            </CardHeader>
            <CardContent className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <p className="text-muted-foreground text-sm">{t('home.platformGuide.detail')}</p>
              <Button asChild className="shrink-0">
                <Link to="/config/bot">{t('home.platformGuide.action')}</Link>
              </Button>
            </CardContent>
          </Card>
        )}

        <div
          className={cn(
            'text-primary flex flex-wrap items-center gap-x-7 gap-y-2 font-sans font-black tracking-[0.12em] uppercase',
            versionsMismatch && 'text-amber-600 dark:text-amber-400'
          )}
        >
          <span className="inline-flex items-baseline gap-2">
            <span className="text-[11px] tracking-[0.2em] opacity-70">
              {t('home.versionCard.maibotVersion')}
            </span>
            <span className="text-base">
              {botStatus?.version ? `V${botStatus.version}` : t('home.versionCard.unknown')}
            </span>
          </span>
          <span className="inline-flex items-baseline gap-2">
            <span className="text-[11px] tracking-[0.2em] opacity-70">
              {t('home.versionCard.consoleVersion')}
            </span>
            <span className="text-base">V{APP_VERSION}</span>
          </span>
          {maibotUpdateAvailable && maibotStableRelease && (
            <a
              href={maibotStableRelease.url}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1.5 text-sm text-sky-600 hover:underline dark:text-sky-400"
            >
              {t('home.versionCard.updateAvailable')} V{maibotStableRelease.version}
              <ExternalLink className="h-3.5 w-3.5" />
            </a>
          )}
          {versionsMismatch && <span className="text-sm">{t('home.versionCard.mismatch')}</span>}
        </div>

        <HomeCardManager
          cards={orderedHomeCards}
          pluginCards={pluginHomeCards}
          controlsPortalId="home-card-controls-bottom"
          firstRowSeparator={
            <div
              className={cn(
                'bg-muted/20 flex h-full w-full items-center gap-3 rounded-lg px-4',
                themeConfig.dashboardStyle !== 'future-retro' &&
                  'border-muted-foreground/30 border border-dashed'
              )}
            >
              {hitokotoLoading ? (
                <Skeleton className="h-5 flex-1" />
              ) : hitokoto ? (
                <p
                  className={cn(
                    'text-muted-foreground flex-1 truncate',
                    themeConfig.dashboardStyle === 'future-retro'
                      ? 'text-[1.05rem] font-medium tracking-wide'
                      : 'text-sm italic'
                  )}
                  style={
                    themeConfig.dashboardStyle === 'future-retro'
                      ? {
                          fontFamily: '"MaiRetroQuote", "Noto Serif SC", "SimSun", serif',
                          textShadow: '0 0.035em 0 hsl(var(--background))',
                        }
                      : undefined
                  }
                >
                  "{hitokoto.hitokoto}" —— {hitokoto.from}
                </p>
              ) : null}
            </div>
          }
        />

        <div id="home-card-controls-bottom" className="flex justify-end pt-2" />

        <Dialog open={quickShortcutDialogOpen} onOpenChange={setQuickShortcutDialogOpen}>
          <DialogContent style={{ '--dialog-width': '46rem' } as CSSProperties}>
            <DialogHeader>
              <DialogTitle>{t('home.quickActions.dialog.title')}</DialogTitle>
              <DialogDescription>{t('home.quickActions.dialog.description')}</DialogDescription>
            </DialogHeader>
            <DialogBody viewportClassName="max-h-[60vh]">
              <div className="space-y-4 pr-1">
                <Input
                  value={quickShortcutSearch}
                  onChange={(event) => setQuickShortcutSearch(event.target.value)}
                  placeholder={t('home.quickActions.dialog.searchPlaceholder')}
                />
                <div className="space-y-2">
                  {filteredQuickShortcutOptions.map((shortcut) => {
                    const Icon = shortcut.icon
                    const checked = quickShortcutIds.includes(shortcut.id)
                    const checkboxId = `quick-shortcut-${shortcut.id}`
                    return (
                      <label
                        key={shortcut.id}
                        htmlFor={checkboxId}
                        className="hover:bg-accent/40 flex cursor-pointer items-start gap-3 rounded-lg border p-3 transition-colors"
                      >
                        <Checkbox
                          id={checkboxId}
                          className="mt-0.5"
                          checked={checked}
                          onCheckedChange={(value) =>
                            toggleQuickShortcut(shortcut.id, value === true)
                          }
                        />
                        <Icon className="text-muted-foreground mt-0.5 h-4 w-4 shrink-0" />
                        <span className="min-w-0 flex-1">
                          <span className="flex flex-wrap items-center gap-2">
                            <span className="font-medium">{shortcut.label}</span>
                            <Badge variant="outline" className="text-[10px]">
                              {t(`home.quickActions.categories.${shortcut.category}`)}
                            </Badge>
                          </span>
                          <span className="text-muted-foreground mt-1 block text-sm">
                            {shortcut.description}
                          </span>
                        </span>
                      </label>
                    )
                  })}
                  {filteredQuickShortcutOptions.length === 0 && (
                    <div className="text-muted-foreground rounded-lg border border-dashed p-6 text-center text-sm">
                      {isPluginShortcutsLoading
                        ? t('home.quickActions.dialog.loadingPluginEntries')
                        : t('home.quickActions.dialog.noMatches')}
                    </div>
                  )}
                </div>
              </div>
            </DialogBody>
            <DialogFooter>
              <Button variant="outline" onClick={resetQuickShortcuts}>
                {t('home.quickActions.dialog.restoreDefault')}
              </Button>
              <Button onClick={() => setQuickShortcutDialogOpen(false)}>
                {t('home.quickActions.dialog.done')}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>

        {/* 重启遮罩层 */}
        <RestartOverlay />

        {/* 表达方式审核器 */}
        {isReviewerOpen && (
          <Suspense fallback={null}>
            <ExpressionReviewer
              open
              onOpenChange={(open) => {
                setIsReviewerOpen(open)
                if (!open) {
                  // 关闭审核器时刷新统计
                  fetchReviewStats()
                }
              }}
            />
          </Suspense>
        )}
      </div>
    </ScrollArea>
  )
}
