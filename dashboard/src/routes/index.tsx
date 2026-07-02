import type { CSSProperties } from 'react'
import { Link } from '@tanstack/react-router'
import {
  Activity,
  AlertCircle,
  BarChart3,
  CheckCircle2,
  Clock,
  Database,
  DollarSign,
  ExternalLink,
  FileText,
  HardDrive,
  ImageIcon,
  MessageSquare,
  Plus,
  Power,
  RefreshCw,
  Smile,
  TrendingUp,
  Zap,
} from 'lucide-react'
import { useCallback, useContext, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  Pie,
  PieChart,
  XAxis,
  YAxis,
} from 'recharts'

import { ExpressionReviewer } from '@/components/expression-reviewer'
import { RestartOverlay } from '@/components/restart-overlay'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import {
  ChartContainer,
  ChartLegend,
  ChartLegendContent,
  ChartTooltip,
  ChartTooltipContent,
  type ChartConfig,
} from '@/components/ui/chart'
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
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { ThinkingIllustration } from '@/components/ui/thinking-illustration'
import { ZoomableChart } from '@/components/ui/zoomable-chart'
import { RestartProvider, useRestart } from '@/lib/restart-context'
import { ThemeProviderContext } from '@/lib/theme-context'
import type { DashboardStyle } from '@/lib/theme/tokens'
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

// 主导出组件：包装 RestartProvider
export function IndexPage() {
  return (
    <RestartProvider>
      <IndexPageContent />
    </RestartProvider>
  )
}

const FUTURE_RETRO_PIE_COLORS = [
  '#0b5a66',
  '#c84d24',
  '#8b6f2a',
  '#2f7d6f',
  '#9b3f58',
  '#57704a',
  '#284b63',
  '#d08a2d',
  '#6b5b95',
  '#7a4f2b',
]

// 为饼图生成颜色；未来复古模式使用更贴近纸张、青绿边框和橘红强调色的调色盘。
const generatePieColors = (count: number, dashboardStyle: DashboardStyle): string[] => {
  if (dashboardStyle === 'future-retro') {
    return Array.from({ length: count }, (_, index) => FUTURE_RETRO_PIE_COLORS[index % FUTURE_RETRO_PIE_COLORS.length])
  }

  const colors: string[] = []
  for (let i = 0; i < count; i++) {
    // 使用黄金角度分布色相，避免相邻颜色相似
    const hue = (i * 137.508) % 360
    colors.push(`hsl(${hue}, 70%, 55%)`)
  }
  return colors
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
      className="inline-flex items-center gap-1.5 rounded-md border bg-background px-2 py-1 text-xs text-muted-foreground"
    >
      <span
        data-dashboard-feature-status-light="true"
        className={cn(
          'h-2.5 w-2.5 rounded-full',
          enabled ? 'bg-green-500 shadow-[0_0_0_3px_rgba(34,197,94,0.18)]' : 'bg-muted-foreground/30'
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

function IndexPageContent() {
  const { t, i18n } = useTranslation()
  const { themeConfig } = useContext(ThemeProviderContext)
  const currentLocale = i18n.resolvedLanguage || i18n.language || 'zh-CN'
  const { triggerRestart, isRestarting } = useRestart()

  // 各数据源领域 hook（页面逻辑下沉，主文件退化为薄渲染层）
  const { dashboardData, loading, loadingProgress, timeRange, setTimeRange, fetchDashboardData } = useDashboardData()
  const { botStatus, isBotStatusLoading, fetchBotStatus } = useBotStatus()
  const { featureStatus, fetchFeatureStatus } = useFeatureStatus()
  const { localCacheStats, isLocalCacheStatsLoading, fetchLocalCacheStats } = useLocalCacheMetrics()
  const { uncheckedCount, fetchReviewStats } = useReviewStats()
  const { hitokoto, hitokotoLoading, maibotStableRelease, fetchHitokoto } = useMaibotVersion()
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
  } = useQuickShortcuts({ isRestarting, handleRestart, uncheckedCount, onOpenReviewer: openReviewer })

  // 初始加载各数据源
  useEffect(() => {
    fetchDashboardData()
    fetchHitokoto()
    fetchBotStatus(true)
    fetchFeatureStatus()
    fetchLocalCacheStats()
    fetchReviewStats()
    fetchPlatformAccountConfig()
  }, [fetchDashboardData, fetchHitokoto, fetchBotStatus, fetchFeatureStatus, fetchLocalCacheStats, fetchReviewStats, fetchPlatformAccountConfig])

  if (loading || !dashboardData) {
    return (
      <div className="flex items-center justify-center h-[calc(100vh-200px)]">
        <div className="text-center space-y-6 w-full max-w-md px-4">
          <ThinkingIllustration size="lg" className="mx-auto" />
          <div className="space-y-2">
            <Progress value={loadingProgress} className="h-2" />
            <p className="text-xs text-muted-foreground">{loadingProgress}%</p>
          </div>
        </div>
      </div>
    )
  }

  // 解构数据，提供默认值以防止 undefined 错误
  const { 
    summary: rawSummary, 
    model_stats = [], 
    hourly_data = [], 
    daily_data = [], 
    recent_activity = [] 
  } = dashboardData

  // 为 summary 提供默认值
  const summary = rawSummary ?? {
    total_requests: 0,
    total_cost: 0,
    total_tokens: 0,
    online_time: 0,
    total_messages: 0,
    total_replies: 0,
    avg_response_time: 0,
    cost_per_hour: 0,
    tokens_per_hour: 0,
  }

  // 格式化时间显示
  const formatTime = (seconds: number) => {
    const hours = Math.floor(seconds / 3600)
    const minutes = Math.floor((seconds % 3600) / 60)
    return t('home.time.hoursMinutes', { hours, minutes })
  }

  // 格式化大数字（自动选择合适单位）
  const formatNumber = (num: number): { display: string; exact: string; needsExact: boolean } => {
    const exact = num.toLocaleString(currentLocale)
    
    if (num >= 1_000_000_000) {
      return { display: `${(num / 1_000_000_000).toFixed(2)}B`, exact, needsExact: true }
    } else if (num >= 1_000_000) {
      return { display: `${(num / 1_000_000).toFixed(2)}M`, exact, needsExact: true }
    } else if (num >= 10_000) {
      return { display: `${(num / 1_000).toFixed(1)}K`, exact, needsExact: true }
    } else if (num >= 1_000) {
      return { display: `${(num / 1_000).toFixed(2)}K`, exact, needsExact: true }
    }
    return { display: exact, exact, needsExact: false }
  }

  // 格式化金额（自动选择合适单位）
  const formatCurrency = (num: number): { display: string; exact: string; needsExact: boolean } => {
    const exact = `¥${num.toLocaleString(currentLocale, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
    
    if (num >= 1_000_000) {
      return { display: `¥${(num / 1_000_000).toFixed(2)}M`, exact, needsExact: true }
    } else if (num >= 10_000) {
      return { display: `¥${(num / 1_000).toFixed(1)}K`, exact, needsExact: true }
    } else if (num >= 1_000) {
      return { display: `¥${(num / 1_000).toFixed(2)}K`, exact, needsExact: true }
    }
    return { display: exact, exact, needsExact: false }
  }

  // 格式化日期时间
  const formatDateTime = (isoString: string) => {
    const date = new Date(isoString)
    return date.toLocaleString(currentLocale, {
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    })
  }

  // 准备饼图数据（模型花费分布）- 使用黄金角度分布避免相邻颜色相似
  const pieColors = generatePieColors(model_stats.length, themeConfig.dashboardStyle)
  const modelPieData = model_stats.map((stat, index) => ({
    name: stat.model_name,
    value: stat.total_cost,
    fill: pieColors[index],
  }))

  // 图表配置
  const chartConfig = {
    requests: {
      label: t('home.charts.requests'),
      color: 'hsl(var(--color-chart-1))',
    },
    cost: {
      label: t('home.charts.cost'),
      color: 'hsl(var(--color-chart-2))',
    },
    tokens: {
      label: 'Tokens',
      color: 'hsl(var(--color-chart-3))',
    },
  } satisfies ChartConfig

  const localCacheDirectories = localCacheStats?.directories ?? []
  const imageCacheDirectory = localCacheDirectories.find((item) => item.key === 'images')
  const emojiCacheDirectory = localCacheDirectories.find((item) => item.key === 'emoji')
  const logCacheDirectory = localCacheDirectories.find((item) => item.key === 'logs')
  const imageCacheSize = imageCacheDirectory?.total_size ?? 0
  const emojiCacheSize = emojiCacheDirectory?.total_size ?? 0
  const logCacheSize = logCacheDirectory?.total_size ?? 0
  const databaseSize = localCacheStats?.database.total_size ?? 0
  const totalStorageSize = localCacheDirectories.reduce((total, item) => total + item.total_size, 0) + databaseSize
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
      id: 'builtin:version',
      title: t('home.versionCard.title'),
      width: 'small',
      source: 'builtin',
      render: () => (
        <Card className="h-full">
          <CardHeader className="pb-3">
            <CardTitle className="flex h-5 items-center gap-2 text-sm font-medium leading-5">
              <FileText className="h-4 w-4" />
              {t('home.versionCard.title')}
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-3">
              <div className="flex items-center justify-between gap-3">
                <span className="text-sm text-muted-foreground">{t('home.versionCard.mainVersion')}</span>
                <Badge
                  variant="secondary"
                  data-dashboard-version-value="true"
                  className="border border-primary/20 bg-primary/10 px-2 py-0.5 font-semibold text-primary"
                >
                  {botStatus?.version ? `v${botStatus?.version}` : t('home.versionCard.unknown')}
                </Badge>
              </div>
              <div className="flex items-center justify-between gap-3">
                <span className="text-sm text-muted-foreground">{t('home.versionCard.webuiVersion')}</span>
                <Badge
                  variant="secondary"
                  data-dashboard-version-value="true"
                  className="border border-primary/20 bg-primary/10 px-2 py-0.5 font-semibold text-primary"
                >
                  v{APP_VERSION}
                </Badge>
              </div>
              <div className="space-y-1 border-t border-border/50 pt-2 text-xs text-muted-foreground/60">
                <a
                  href={maibotStableRelease?.url || 'https://github.com/Mai-with-u/MaiBot/releases'}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="flex items-center justify-between gap-2 transition-colors hover:text-muted-foreground"
                >
                  <span>{t('home.versionCard.stableLatest')}</span>
                  <span className="inline-flex items-center gap-1">
                    {maibotStableRelease ? `v${maibotStableRelease?.version}` : t('home.versionCard.githubReleases')}
                    <ExternalLink className="h-3 w-3" />
                  </span>
                </a>
              </div>
            </div>
          </CardContent>
        </Card>
      ),
    },
    {
      id: 'builtin:bot-status',
      title: t('home.botStatus.title'),
      width: 'medium',
      source: 'builtin',
      render: () => (
        <Card className="h-full">
          <CardHeader className="pb-3">
            <CardTitle className="flex h-5 items-center gap-2 text-sm font-medium leading-5">
              <StreamlineIcon name="button-power-circle-1-remix" fallback={Power} className="h-4 w-4" />
              {t('home.botStatus.title')}
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-3">
              {themeConfig.dashboardStyle === 'future-retro' ? (
                <div className="space-y-2">
                  {isBotStatusLoading && !botStatus ? (
                    <FeatureStatusIndicator enabled={false} accent="green" label={t('home.botStatus.loading')} />
                  ) : botStatus?.running === true ? (
                    <FeatureStatusIndicator
                      enabled
                      accent="green"
                      label={t('home.botStatus.running')}
                      detail={t('home.botStatus.uptime', { time: formatTime(botStatus?.uptime ?? 0) })}
                    />
                  ) : botStatus ? (
                    <FeatureStatusIndicator enabled accent="red" label={t('home.botStatus.stopped')} />
                  ) : (
                    <FeatureStatusIndicator enabled={false} accent="green" label={t('home.botStatus.unknown')} />
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
                          <div data-dashboard-status-dot="true" data-state="loading" className="h-3 w-3 rounded-full bg-muted-foreground/40 animate-pulse" />
                          <Badge data-dashboard-status-badge="true" data-state="loading" variant="outline" className="whitespace-nowrap text-muted-foreground">
                            <RefreshCw className="h-3 w-3 mr-1 animate-spin" />
                            {t('home.botStatus.loading')}
                          </Badge>
                        </>
                      ) : botStatus?.running === true ? (
                        <>
                          <div data-dashboard-status-dot="true" data-state="running" className="h-3 w-3 rounded-full bg-green-500 animate-pulse" />
                          <Badge data-dashboard-status-badge="true" data-state="running" variant="outline" className="whitespace-nowrap text-green-600 border-green-300 bg-green-50">
                            <CheckCircle2 className="h-3 w-3 mr-1" />
                            {t('home.botStatus.running')}
                          </Badge>
                        </>
                      ) : botStatus ? (
                        <>
                          <div data-dashboard-status-dot="true" data-state="stopped" className="h-3 w-3 rounded-full bg-red-500" />
                          <Badge data-dashboard-status-badge="true" data-state="stopped" variant="outline" className="whitespace-nowrap text-red-600 border-red-300 bg-red-50">
                            <AlertCircle className="h-3 w-3 mr-1" />
                            {t('home.botStatus.stopped')}
                          </Badge>
                        </>
                      ) : (
                        <>
                          <div data-dashboard-status-dot="true" data-state="unknown" className="h-3 w-3 rounded-full bg-muted-foreground/40" />
                          <Badge data-dashboard-status-badge="true" data-state="unknown" variant="outline" className="whitespace-nowrap text-muted-foreground">
                            <AlertCircle className="h-3 w-3 mr-1" />
                            {t('home.botStatus.unknown')}
                          </Badge>
                        </>
                      )}
                    </div>
                    {botStatus && (
                      <div className="flex items-center gap-2 text-xs text-muted-foreground">
                        <span>{t('home.botStatus.uptime', { time: formatTime(botStatus?.uptime ?? 0) })}</span>
                      </div>
                    )}
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <FeatureStatusLight enabled={featureStatus.visualEnabled} label={t('home.botStatus.visualEnabled')} />
                    <FeatureStatusLight enabled={featureStatus.memoryEnabled} label={t('home.botStatus.memoryEnabled')} />
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
      width: 'large',
      source: 'builtin',
      render: () => (
        <Card className="h-full">
          <CardHeader className="flex flex-row items-start justify-between space-y-0 pb-3">
            <CardTitle className="flex h-5 items-center gap-2 text-sm font-medium leading-5">
              <StreamlineIcon name="one-finger-short-tap-remix" fallback={Zap} className="h-4 w-4" />
              {t('home.quickActions.title')}
            </CardTitle>
            <Button
              variant="ghost"
              size="icon"
              onClick={() => setQuickShortcutDialogOpen(true)}
              aria-label={t('home.quickActions.customize')}
              className="h-8 w-8"
            >
              <Plus className="h-4 w-4" />
            </Button>
          </CardHeader>
          <CardContent>
            {selectedQuickShortcuts.length === 0 ? (
              <div className="flex flex-col gap-3 rounded-lg border border-dashed p-4 text-sm text-muted-foreground sm:flex-row sm:items-center sm:justify-between">
                <span>{t('home.quickActions.empty')}</span>
                <Button variant="outline" size="sm" onClick={() => setQuickShortcutDialogOpen(true)}>
                  {t('home.quickActions.add')}
                </Button>
              </div>
            ) : (
              <div className="flex flex-wrap gap-2">
                {selectedQuickShortcuts.map((shortcut) => {
                  const Icon = shortcut.icon
                  const content = (
                    <>
                      <Icon className={`h-4 w-4 ${shortcut.id === 'action:restart' && isRestarting ? 'animate-spin' : ''}`} />
                      <span className="min-w-0 flex-1 truncate text-left">{shortcut.label}</span>
                      {shortcut.badge && (
                        <span data-quick-action-badge="true" className="ml-1 shrink-0 rounded-full bg-orange-500 px-1.5 py-0.5 text-xs text-white">
                          {shortcut.badge}
                        </span>
                      )}
                      {shortcut.external && <ExternalLink className="h-3.5 w-3.5 shrink-0" />}
                    </>
                  )

                  if (shortcut.href) {
                    return (
                      <Button key={shortcut.id} variant="outline" size="sm" asChild className="max-w-[14rem] justify-start gap-2 overflow-hidden sm:max-w-[18rem]">
                        <a href={shortcut.href} target={shortcut.external ? '_blank' : undefined} rel={shortcut.external ? 'noopener noreferrer' : undefined} title={shortcut.label}>
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
    ...(platformAccountConfigured === false
      ? [{
          id: 'builtin:platform-guide',
          title: t('home.platformGuide.title'),
          width: 'full' as const,
          source: 'builtin' as const,
          render: () => (
            <Card className="h-full border-2 border-orange-500 bg-orange-50/80 dark:border-orange-500 dark:bg-orange-950/20">
              <CardHeader className="pb-3">
                <CardTitle className="text-2xl text-orange-700 dark:text-orange-300">{t('home.platformGuide.title')}</CardTitle>
                <CardDescription>{t('home.platformGuide.description')}</CardDescription>
              </CardHeader>
              <CardContent className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                <p className="text-sm text-muted-foreground">{t('home.platformGuide.detail')}</p>
                <Button asChild className="shrink-0">
                  <Link to="/config/bot">{t('home.platformGuide.action')}</Link>
                </Button>
              </CardContent>
            </Card>
          ),
        }]
      : []),
    {
      id: 'builtin:stats-overview',
      title: t('home.stats.overviewTitle'),
      width: 'wide',
      source: 'builtin',
      render: () => (
        <Card className="h-full">
          <CardHeader className="flex flex-col gap-3 pb-3 sm:flex-row sm:items-start sm:justify-between">
            <div className="space-y-1.5">
              <CardTitle className="flex h-5 items-center gap-2 text-sm font-medium leading-5">
                <BarChart3 className="h-4 w-4" />
                {t('home.stats.overviewTitle')}
              </CardTitle>
              <CardDescription>
                {t('home.stats.recentPeriod', {
                  range: timeRange < 48
                    ? timeRange + t('home.stats.hours')
                    : Math.floor(timeRange / 24) + t('home.stats.days'),
                })}
              </CardDescription>
            </div>
            <Tabs value={timeRange.toString()} onValueChange={(v) => setTimeRange(Number(v))}>
              <TabsList className="grid grid-cols-3">
                <TabsTrigger value="24">{t('home.timeRange.24h')}</TabsTrigger>
                <TabsTrigger value="168">{t('home.timeRange.7d')}</TabsTrigger>
                <TabsTrigger value="720">{t('home.timeRange.30d')}</TabsTrigger>
              </TabsList>
            </Tabs>
          </CardHeader>
          <CardContent className="flex flex-col justify-center py-2 sm:py-2">
            <div className="grid gap-y-0.5 lg:grid-cols-2 xl:grid-cols-3 [&>*:nth-child(even)]:lg:border-l [&>*:nth-child(odd)]:lg:border-l-0 [&>*:not(:nth-child(3n+1))]:xl:border-l [&>*:nth-child(3n+1)]:xl:border-l-0">
              <div className="flex min-h-9 min-w-0 flex-col justify-center border-border px-2 py-0.5">
                <div className="flex min-w-0 items-center gap-1.5 text-xs leading-[15px]">
                  <Activity className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                  <span className="shrink-0 font-bold text-muted-foreground">{t('home.stats.totalRequests')}</span>
                  <span className="ml-auto min-w-0 truncate text-right text-[15px] font-bold leading-4 text-primary">
                    {formatNumber(summary.total_requests).display}
                    {formatNumber(summary.total_requests).needsExact && <span className="ml-1 text-xs font-normal text-muted-foreground">({formatNumber(summary.total_requests).exact})</span>}
                  </span>
                </div>
              </div>
              <div className="flex min-h-9 min-w-0 flex-col justify-center border-border px-2 py-0.5">
                <div className="flex min-w-0 items-center gap-1.5 text-xs leading-[15px]">
                  <DollarSign className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                  <span className="shrink-0 font-bold text-muted-foreground">{t('home.stats.totalCost')}</span>
                  <span className="ml-auto min-w-0 truncate text-right text-[15px] font-bold leading-4 text-primary">
                    {formatCurrency(summary.total_cost).display}
                    {formatCurrency(summary.total_cost).needsExact && <span className="ml-1 text-xs font-normal text-muted-foreground">({formatCurrency(summary.total_cost).exact})</span>}
                  </span>
                </div>
                <p className="mt-0 text-[11px] leading-[13px] text-muted-foreground">
                  {summary.cost_per_hour > 0 ? t('home.stats.perHour', { value: `¥${summary.cost_per_hour.toFixed(2)}` }) : t('home.stats.noData')}
                </p>
              </div>
              <div className="flex min-h-9 min-w-0 flex-col justify-center border-border px-2 py-0.5">
                <div className="flex min-w-0 items-center gap-1.5 text-xs leading-[15px]">
                  <Database className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                  <span className="shrink-0 font-bold text-muted-foreground">{t('home.stats.tokenUsage')}</span>
                  <span className="ml-auto min-w-0 truncate text-right text-[15px] font-bold leading-4 text-primary">
                    {formatNumber(summary.total_tokens).display}
                    {formatNumber(summary.total_tokens).needsExact && <span className="ml-1 text-xs font-normal text-muted-foreground">({formatNumber(summary.total_tokens).exact})</span>}
                  </span>
                </div>
                <p className="mt-0 text-[11px] leading-[13px] text-muted-foreground">
                  {summary.tokens_per_hour > 0 ? t('home.stats.perHour', { value: formatNumber(summary.tokens_per_hour).display }) : t('home.stats.noData')}
                </p>
              </div>
              <div className="flex min-h-9 min-w-0 flex-col justify-center border-border px-2 py-0.5">
                <div className="flex min-w-0 items-center gap-1.5 text-xs leading-[15px]">
                  <Zap className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                  <span className="shrink-0 font-bold text-muted-foreground">{t('home.stats.avgResponse')}</span>
                  <span className="ml-auto min-w-0 truncate text-right text-[15px] font-bold leading-4 text-primary">{summary.avg_response_time.toFixed(2)}s</span>
                </div>
                <p className="mt-0 text-[11px] leading-[13px] text-muted-foreground">{t('home.stats.avgResponseDesc')}</p>
              </div>
              <div className="flex min-h-9 min-w-0 flex-col justify-center border-border px-2 py-0.5">
                <div className="flex min-w-0 items-center gap-1.5 text-xs leading-[15px]">
                  <Clock className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                  <span className="shrink-0 font-bold text-muted-foreground">{t('home.stats.onlineTime')}</span>
                  <span className="ml-auto min-w-0 truncate text-right text-[15px] font-bold leading-4 text-primary">
                    {formatTime(summary.online_time)}
                    <span className="ml-1 text-xs font-normal text-muted-foreground">({summary.online_time.toLocaleString()}{t('home.stats.seconds')})</span>
                  </span>
                </div>
              </div>
              <div className="flex min-h-9 min-w-0 flex-col justify-center border-border px-2 py-0.5">
                <div className="flex min-w-0 items-center gap-1.5 text-xs leading-[15px]">
                  <MessageSquare className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                  <span className="shrink-0 font-bold text-muted-foreground">{t('home.stats.messageProcessing')}</span>
                  <span className="ml-auto min-w-0 truncate text-right text-[15px] font-bold leading-4 text-primary">
                    {formatNumber(summary.total_messages).display}
                    {formatNumber(summary.total_messages).needsExact && <span className="ml-1 text-xs font-normal text-muted-foreground">({formatNumber(summary.total_messages).exact})</span>}
                  </span>
                </div>
                <p className="mt-0 text-[11px] leading-[13px] text-muted-foreground">
                  {t('home.stats.replied', { num: formatNumber(summary.total_replies).display })}
                  {formatNumber(summary.total_replies).needsExact && <span>({formatNumber(summary.total_replies).exact})</span>}
                </p>
              </div>
              <div className="flex min-h-9 min-w-0 flex-col justify-center border-border px-2 py-0.5">
                <div className="flex min-w-0 items-center gap-1.5 text-xs leading-[15px]">
                  <TrendingUp className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                  <span className="shrink-0 font-bold text-muted-foreground">{t('home.stats.costEfficiency')}</span>
                  <span className="ml-auto min-w-0 truncate text-right text-[15px] font-bold leading-4 text-primary">
                    {summary.total_messages > 0 ? `¥${((summary.total_cost / summary.total_messages) * 100).toFixed(2)}` : '¥0.00'}
                  </span>
                </div>
                <p className="mt-0 text-[11px] leading-[13px] text-muted-foreground">{t('home.stats.per100Messages')}</p>
              </div>
            </div>
          </CardContent>
        </Card>
      ),
    },
    {
      id: 'builtin:storage',
      title: t('home.storage.title'),
      width: 'medium',
      source: 'builtin',
      render: () => (
        <Card className="h-full xl:self-stretch">
          <CardHeader className="pb-3">
            <CardTitle className="flex h-5 items-center gap-2 text-sm font-medium leading-5">
              <HardDrive className="h-4 w-4" />
              {t('home.storage.title')}
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-3">
              <div>
                <div className="text-2xl font-bold">
                  {hasLocalCacheStats ? formatStorageBytes(totalStorageSize) : isLocalCacheStatsLoading ? t('home.storage.reading') : '-'}
                </div>
                {!hasLocalCacheStats && (
                  <p className="mt-1 text-xs text-muted-foreground">
                    {isLocalCacheStatsLoading ? t('home.storage.readingDescription') : t('home.storage.unavailable')}
                  </p>
                )}
              </div>
              {hasLocalCacheStats && (
                <div className="space-y-2.5">
                  {storageDetails.map((item) => {
                    const Icon = item.icon
                    const percent = totalStorageSize > 0 ? (item.size / totalStorageSize) * 100 : 0
                    const visiblePercent = item.size > 0 ? Math.max(percent, 2) : 0
                    return (
                      <div key={item.key} className="space-y-1.5">
                        <div className="flex min-w-0 items-center gap-2 text-xs">
                          <Icon className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                          <span className="shrink-0 font-bold">{item.label}</span>
                          <span className="shrink-0 font-semibold text-primary">{formatStorageBytes(item.size)}</span>
                          <span className="min-w-0 truncate text-muted-foreground">{item.detail}</span>
                          <span className="ml-auto shrink-0 text-muted-foreground">{percent.toFixed(percent >= 10 ? 0 : 1)}%</span>
                        </div>
                        <div className="h-1.5 overflow-hidden rounded-full bg-muted">
                          <div className="h-full rounded-full bg-primary transition-all" style={{ width: `${visiblePercent}%` }} />
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
  const showLegacyHomeCards = false

  return (
    <ScrollArea className="h-full">
      <div className="space-y-2 sm:space-y-4 p-4 sm:p-6">
      {/* 标题和控制栏 */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl sm:text-3xl font-bold">{t('home.title')}</h1>
        </div>
      </div>

      {/* 一言 */}
      <div
        className={cn(
          'flex items-center gap-3 rounded-lg bg-muted/20 px-4 py-1',
          themeConfig.dashboardStyle !== 'future-retro' && 'border border-dashed border-muted-foreground/30'
        )}
      >
        {hitokotoLoading ? (
          <Skeleton className="h-5 flex-1" />
        ) : hitokoto ? (
          <p
            className={cn(
              'flex-1 truncate text-muted-foreground',
              themeConfig.dashboardStyle === 'future-retro'
                ? 'text-[1.05rem] font-medium tracking-wide'
                : 'text-sm italic'
            )}
            style={
              themeConfig.dashboardStyle === 'future-retro'
                ? {
                    fontFamily:
                      '"MaiRetroQuote", "Noto Serif SC", "SimSun", serif',
                    textShadow: '0 0.035em 0 hsl(var(--background))',
                  }
                : undefined
            }
          >
            "{hitokoto.hitokoto}" —— {hitokoto.from}
          </p>
        ) : null}
      </div>

      <HomeCardManager
        cards={homeCards}
        pluginCards={pluginHomeCards}
        controlsPortalId="home-card-controls-bottom"
      />

      {showLegacyHomeCards && (
      <>
      {/* 机器人状态和快速操作 */}
      <div
        data-home-summary-cards="true"
        className="grid items-stretch gap-4 grid-cols-1 lg:grid-cols-[minmax(14rem,0.8fr)_minmax(16rem,1fr)_minmax(0,1.6fr)]"
      >
        {/* 机器人状态卡片 */}
        <Card className="lg:col-span-1">
          <CardHeader className="pb-3">
            <CardTitle className="flex h-5 items-center gap-2 text-sm font-medium leading-5">
              <FileText className="h-4 w-4" />
              {t('home.versionCard.title')}
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-3">
              <div className="flex items-center justify-between gap-3">
                <span className="text-sm text-muted-foreground">{t('home.versionCard.mainVersion')}</span>
                <Badge
                  variant="secondary"
                  data-dashboard-version-value="true"
                  className="border border-primary/20 bg-primary/10 px-2 py-0.5 font-semibold text-primary"
                >
                  {botStatus?.version ? `v${botStatus?.version}` : t('home.versionCard.unknown')}
                </Badge>
              </div>
              <div className="flex items-center justify-between gap-3">
                <span className="text-sm text-muted-foreground">{t('home.versionCard.webuiVersion')}</span>
                <Badge
                  variant="secondary"
                  data-dashboard-version-value="true"
                  className="border border-primary/20 bg-primary/10 px-2 py-0.5 font-semibold text-primary"
                >
                  v{APP_VERSION}
                </Badge>
              </div>
              <div className="space-y-1 border-t border-border/50 pt-2 text-xs text-muted-foreground/60">
                <a
                  href={maibotStableRelease?.url || 'https://github.com/Mai-with-u/MaiBot/releases'}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="flex items-center justify-between gap-2 transition-colors hover:text-muted-foreground"
                >
                  <span>{t('home.versionCard.stableLatest')}</span>
                  <span className="inline-flex items-center gap-1">
                    {maibotStableRelease ? `v${maibotStableRelease?.version}` : t('home.versionCard.githubReleases')}
                    <ExternalLink className="h-3 w-3" />
                  </span>
                </a>
              </div>
            </div>
          </CardContent>
        </Card>

        <Card className="lg:col-span-1">
          <CardHeader className="pb-3">
            <CardTitle className="flex h-5 items-center gap-2 text-sm font-medium leading-5">
              <StreamlineIcon name="button-power-circle-1-remix" fallback={Power} className="h-4 w-4" />
              {t('home.botStatus.title')}
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-3">
              {themeConfig.dashboardStyle === 'future-retro' ? (
                <div className="space-y-2">
                  {isBotStatusLoading && !botStatus ? (
                    <FeatureStatusIndicator enabled={false} accent="green" label={t('home.botStatus.loading')} />
                  ) : botStatus?.running === true ? (
                    <FeatureStatusIndicator
                      enabled
                      accent="green"
                      label={t('home.botStatus.running')}
                      detail={t('home.botStatus.uptime', { time: formatTime(botStatus?.uptime ?? 0) })}
                    />
                  ) : botStatus ? (
                    <FeatureStatusIndicator enabled accent="red" label={t('home.botStatus.stopped')} />
                  ) : (
                    <FeatureStatusIndicator enabled={false} accent="green" label={t('home.botStatus.unknown')} />
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
                            className="h-3 w-3 rounded-full bg-muted-foreground/40 animate-pulse"
                          />
                          <Badge
                            data-dashboard-status-badge="true"
                            data-state="loading"
                            variant="outline"
                            className="whitespace-nowrap text-muted-foreground"
                          >
                            <RefreshCw className="h-3 w-3 mr-1 animate-spin" />
                            {t('home.botStatus.loading')}
                          </Badge>
                        </>
                      ) : botStatus?.running === true ? (
                        <>
                          <div
                            data-dashboard-status-dot="true"
                            data-state="running"
                            className="h-3 w-3 rounded-full bg-green-500 animate-pulse"
                          />
                          <Badge
                            data-dashboard-status-badge="true"
                            data-state="running"
                            variant="outline"
                            className="whitespace-nowrap text-green-600 border-green-300 bg-green-50"
                          >
                            <CheckCircle2 className="h-3 w-3 mr-1" />
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
                            className="whitespace-nowrap text-red-600 border-red-300 bg-red-50"
                          >
                            <AlertCircle className="h-3 w-3 mr-1" />
                            {t('home.botStatus.stopped')}
                          </Badge>
                        </>
                      ) : (
                        <>
                          <div
                            data-dashboard-status-dot="true"
                            data-state="unknown"
                            className="h-3 w-3 rounded-full bg-muted-foreground/40"
                          />
                          <Badge
                            data-dashboard-status-badge="true"
                            data-state="unknown"
                            variant="outline"
                            className="whitespace-nowrap text-muted-foreground"
                          >
                            <AlertCircle className="h-3 w-3 mr-1" />
                            {t('home.botStatus.unknown')}
                          </Badge>
                        </>
                      )}
                    </div>
                    {botStatus && (
                      <div className="flex items-center gap-2 text-xs text-muted-foreground">
                        <span>{t('home.botStatus.uptime', { time: formatTime(botStatus?.uptime ?? 0) })}</span>
                      </div>
                    )}
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <FeatureStatusLight enabled={featureStatus.visualEnabled} label={t('home.botStatus.visualEnabled')} />
                    <FeatureStatusLight enabled={featureStatus.memoryEnabled} label={t('home.botStatus.memoryEnabled')} />
                  </div>
                </>
              )}
            </div>
          </CardContent>
        </Card>

        {/* 快速操作卡片 */}
        <Card>
          <CardHeader className="flex flex-row items-start justify-between space-y-0 pb-3">
            <CardTitle className="flex h-5 items-center gap-2 text-sm font-medium leading-5">
              <StreamlineIcon name="one-finger-short-tap-remix" fallback={Zap} className="h-4 w-4" />
              {t('home.quickActions.title')}
            </CardTitle>
            <Button
              variant="ghost"
              size="icon"
              onClick={() => setQuickShortcutDialogOpen(true)}
              aria-label={t('home.quickActions.customize')}
              className="h-8 w-8"
            >
              <Plus className="h-4 w-4" />
            </Button>
          </CardHeader>
          <CardContent>
            {selectedQuickShortcuts.length === 0 ? (
              <div className="flex flex-col gap-3 rounded-lg border border-dashed p-4 text-sm text-muted-foreground sm:flex-row sm:items-center sm:justify-between">
                <span>{t('home.quickActions.empty')}</span>
                <Button variant="outline" size="sm" onClick={() => setQuickShortcutDialogOpen(true)}>
                  {t('home.quickActions.add')}
                </Button>
              </div>
            ) : (
              <div className="flex flex-wrap gap-2">
                {selectedQuickShortcuts.map((shortcut) => {
                  const Icon = shortcut.icon
                  const content = (
                    <>
                      <Icon className={`h-4 w-4 ${shortcut.id === 'action:restart' && isRestarting ? 'animate-spin' : ''}`} />
                      <span className="min-w-0 flex-1 truncate text-left">
                        {shortcut.label}
                      </span>
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

      </div>

      {platformAccountConfigured === false && (
        <Card className="border-2 border-orange-500 bg-orange-50/80 dark:border-orange-500 dark:bg-orange-950/20">
          <CardHeader className="pb-3">
            <CardTitle className="text-2xl text-orange-700 dark:text-orange-300">
              {t('home.platformGuide.title')}
            </CardTitle>
            <CardDescription>
              {t('home.platformGuide.description')}
            </CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <p className="text-sm text-muted-foreground">
              {t('home.platformGuide.detail')}
            </p>
            <Button asChild className="shrink-0">
              <Link to="/config/bot">
                {t('home.platformGuide.action')}
              </Link>
            </Button>
          </CardContent>
        </Card>
      )}

      <div className="grid gap-4 grid-cols-1 xl:grid-cols-[minmax(0,1fr)_320px]">
        {/* 统计概览 */}
        <Card>
          <CardHeader className="flex flex-col gap-3 pb-3 sm:flex-row sm:items-start sm:justify-between">
            <div className="space-y-1.5">
              <CardTitle className="flex h-5 items-center gap-2 text-sm font-medium leading-5">
                <BarChart3 className="h-4 w-4" />
                {t('home.stats.overviewTitle')}
              </CardTitle>
              <CardDescription>
                {t('home.stats.recentPeriod', {
                  range: timeRange < 48
                    ? timeRange + t('home.stats.hours')
                    : Math.floor(timeRange / 24) + t('home.stats.days'),
                })}
              </CardDescription>
            </div>
            <Tabs value={timeRange.toString()} onValueChange={(v) => setTimeRange(Number(v))}>
              <TabsList className="grid grid-cols-3">
                <TabsTrigger value="24">{t('home.timeRange.24h')}</TabsTrigger>
                <TabsTrigger value="168">{t('home.timeRange.7d')}</TabsTrigger>
                <TabsTrigger value="720">{t('home.timeRange.30d')}</TabsTrigger>
              </TabsList>
            </Tabs>
          </CardHeader>
          <CardContent className="flex flex-col justify-center py-2 sm:py-2">
            <div className="grid gap-y-0.5 lg:grid-cols-2 xl:grid-cols-3 [&>*:nth-child(even)]:lg:border-l [&>*:nth-child(odd)]:lg:border-l-0 [&>*:not(:nth-child(3n+1))]:xl:border-l [&>*:nth-child(3n+1)]:xl:border-l-0">
              <div className="flex min-h-9 min-w-0 flex-col justify-center border-border px-2 py-0.5">
                <div className="flex min-w-0 items-center gap-1.5 text-xs leading-[15px]">
                  <Activity className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                  <span className="shrink-0 font-bold text-muted-foreground">{t('home.stats.totalRequests')}</span>
                  <span className="ml-auto min-w-0 truncate text-right text-[15px] font-bold leading-4 text-primary">
                    {formatNumber(summary.total_requests).display}
                    {formatNumber(summary.total_requests).needsExact && (
                      <span className="ml-1 text-xs font-normal text-muted-foreground">
                        ({formatNumber(summary.total_requests).exact})
                      </span>
                    )}
                  </span>
                </div>
              </div>

              <div className="flex min-h-9 min-w-0 flex-col justify-center border-border px-2 py-0.5">
                <div className="flex min-w-0 items-center gap-1.5 text-xs leading-[15px]">
                  <DollarSign className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                  <span className="shrink-0 font-bold text-muted-foreground">{t('home.stats.totalCost')}</span>
                  <span className="ml-auto min-w-0 truncate text-right text-[15px] font-bold leading-4 text-primary">
                    {formatCurrency(summary.total_cost).display}
                    {formatCurrency(summary.total_cost).needsExact && (
                      <span className="ml-1 text-xs font-normal text-muted-foreground">
                        ({formatCurrency(summary.total_cost).exact})
                      </span>
                    )}
                  </span>
                </div>
                <p className="mt-0 text-[11px] leading-[13px] text-muted-foreground">
                  {summary.cost_per_hour > 0
                    ? t('home.stats.perHour', { value: `¥${summary.cost_per_hour.toFixed(2)}` })
                    : t('home.stats.noData')}
                </p>
              </div>

              <div className="flex min-h-9 min-w-0 flex-col justify-center border-border px-2 py-0.5">
                <div className="flex min-w-0 items-center gap-1.5 text-xs leading-[15px]">
                  <Database className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                  <span className="shrink-0 font-bold text-muted-foreground">{t('home.stats.tokenUsage')}</span>
                  <span className="ml-auto min-w-0 truncate text-right text-[15px] font-bold leading-4 text-primary">
                    {formatNumber(summary.total_tokens).display}
                    {formatNumber(summary.total_tokens).needsExact && (
                      <span className="ml-1 text-xs font-normal text-muted-foreground">
                        ({formatNumber(summary.total_tokens).exact})
                      </span>
                    )}
                  </span>
                </div>
                <p className="mt-0 text-[11px] leading-[13px] text-muted-foreground">
                  {summary.tokens_per_hour > 0
                    ? t('home.stats.perHour', { value: formatNumber(summary.tokens_per_hour).display })
                    : t('home.stats.noData')}
                </p>
              </div>

              <div className="flex min-h-9 min-w-0 flex-col justify-center border-border px-2 py-0.5">
                <div className="flex min-w-0 items-center gap-1.5 text-xs leading-[15px]">
                  <Zap className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                  <span className="shrink-0 font-bold text-muted-foreground">{t('home.stats.avgResponse')}</span>
                  <span className="ml-auto min-w-0 truncate text-right text-[15px] font-bold leading-4 text-primary">
                    {summary.avg_response_time.toFixed(2)}s
                  </span>
                </div>
                <p className="mt-0 text-[11px] leading-[13px] text-muted-foreground">{t('home.stats.avgResponseDesc')}</p>
              </div>

              <div className="flex min-h-9 min-w-0 flex-col justify-center border-border px-2 py-0.5">
                <div className="flex min-w-0 items-center gap-1.5 text-xs leading-[15px]">
                  <Clock className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                  <span className="shrink-0 font-bold text-muted-foreground">{t('home.stats.onlineTime')}</span>
                  <span className="ml-auto min-w-0 truncate text-right text-[15px] font-bold leading-4 text-primary">
                    {formatTime(summary.online_time)}
                    <span className="ml-1 text-xs font-normal text-muted-foreground">
                      ({summary.online_time.toLocaleString()}{t('home.stats.seconds')})
                    </span>
                  </span>
                </div>
              </div>

              <div className="flex min-h-9 min-w-0 flex-col justify-center border-border px-2 py-0.5">
                <div className="flex min-w-0 items-center gap-1.5 text-xs leading-[15px]">
                  <MessageSquare className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                  <span className="shrink-0 font-bold text-muted-foreground">{t('home.stats.messageProcessing')}</span>
                  <span className="ml-auto min-w-0 truncate text-right text-[15px] font-bold leading-4 text-primary">
                    {formatNumber(summary.total_messages).display}
                    {formatNumber(summary.total_messages).needsExact && (
                      <span className="ml-1 text-xs font-normal text-muted-foreground">
                        ({formatNumber(summary.total_messages).exact})
                      </span>
                    )}
                  </span>
                </div>
                <p className="mt-0 text-[11px] leading-[13px] text-muted-foreground">
                  {t('home.stats.replied', { num: formatNumber(summary.total_replies).display })}
                  {formatNumber(summary.total_replies).needsExact && (
                    <span>({formatNumber(summary.total_replies).exact})</span>
                  )}
                </p>
              </div>

              <div className="flex min-h-9 min-w-0 flex-col justify-center border-border px-2 py-0.5">
                <div className="flex min-w-0 items-center gap-1.5 text-xs leading-[15px]">
                  <TrendingUp className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                  <span className="shrink-0 font-bold text-muted-foreground">{t('home.stats.costEfficiency')}</span>
                  <span className="ml-auto min-w-0 truncate text-right text-[15px] font-bold leading-4 text-primary">
                    {summary.total_messages > 0
                      ? `¥${((summary.total_cost / summary.total_messages) * 100).toFixed(2)}`
                      : '¥0.00'}
                  </span>
                </div>
                <p className="mt-0 text-[11px] leading-[13px] text-muted-foreground">{t('home.stats.per100Messages')}</p>
              </div>
            </div>
          </CardContent>
        </Card>

        <Card className="xl:self-stretch">
          <CardHeader className="pb-3">
            <CardTitle className="flex h-5 items-center gap-2 text-sm font-medium leading-5">
              <HardDrive className="h-4 w-4" />
              {t('home.storage.title')}
            </CardTitle>
          </CardHeader>
          <CardContent>
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
                  <p className="mt-1 text-xs text-muted-foreground">
                    {isLocalCacheStatsLoading ? t('home.storage.readingDescription') : t('home.storage.unavailable')}
                  </p>
                )}
              </div>
              {hasLocalCacheStats && (
                <div className="space-y-2.5">
                  {storageDetails.map((item) => {
                    const Icon = item.icon
                    const percent = totalStorageSize > 0 ? (item.size / totalStorageSize) * 100 : 0
                    const visiblePercent = item.size > 0 ? Math.max(percent, 2) : 0

                    return (
                      <div key={item.key} className="space-y-1.5">
                        <div className="flex min-w-0 items-center gap-2 text-xs">
                          <Icon className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                          <span className="shrink-0 font-bold">{item.label}</span>
                          <span className="shrink-0 font-semibold text-primary">{formatStorageBytes(item.size)}</span>
                          <span className="min-w-0 truncate text-muted-foreground">{item.detail}</span>
                          <span className="ml-auto shrink-0 text-muted-foreground">
                            {percent.toFixed(percent >= 10 ? 0 : 1)}%
                          </span>
                        </div>
                        <div className="h-1.5 overflow-hidden rounded-full bg-muted">
                          <div
                            className="h-full rounded-full bg-primary transition-all"
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
      </div>
      </>
      )}

      {/* 图表区域 */}
      <Tabs defaultValue="trends" className="space-y-4">
        <TabsList className="grid w-full grid-cols-2 sm:grid-cols-4">
          <TabsTrigger value="trends">{t('home.charts.tabs.trends')}</TabsTrigger>
          <TabsTrigger value="models">{t('home.charts.tabs.models')}</TabsTrigger>
          <TabsTrigger value="activity">{t('home.charts.tabs.activity')}</TabsTrigger>
          <TabsTrigger value="daily">{t('home.charts.tabs.daily')}</TabsTrigger>
        </TabsList>

        {/* 趋势图表 */}
        <TabsContent value="trends" className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle>{t('home.charts.requestTrend')}</CardTitle>
              <CardDescription>{t('home.charts.requestTrendDesc', { hours: timeRange })}</CardDescription>
            </CardHeader>
            <CardContent>
              <ZoomableChart aria-label={t('home.ariaLabel.requestTrend')}>
              <ChartContainer config={chartConfig} className="h-[300px] sm:h-[400px] w-full aspect-auto">
                <LineChart data={hourly_data}>
                  <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--color-muted-foreground) / 0.2)" />
                  <XAxis
                    dataKey="timestamp"
                    tickFormatter={(value) => formatDateTime(value)}
                    angle={-45}
                    textAnchor="end"
                    height={60}
                    stroke="hsl(var(--color-muted-foreground))"
                    tick={{ fill: 'hsl(var(--color-muted-foreground))' }}
                  />
                  <YAxis stroke="hsl(var(--color-muted-foreground))" tick={{ fill: 'hsl(var(--color-muted-foreground))' }} />
                  <ChartTooltip
                    content={<ChartTooltipContent labelFormatter={(value) => formatDateTime(value as string)} />}
                  />
                  <Line
                    type="monotone"
                    dataKey="requests"
                    stroke="var(--color-requests)"
                    strokeWidth={2}
                  />
                </LineChart>
              </ChartContainer>
              </ZoomableChart>
            </CardContent>
          </Card>

          <div className="grid gap-4 grid-cols-1 lg:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle>{t('home.charts.costTrend')}</CardTitle>
                <CardDescription>{t('home.charts.costTrendDesc')}</CardDescription>
              </CardHeader>
              <CardContent>
                <ZoomableChart aria-label={t('home.ariaLabel.costTrend')}>
                <ChartContainer config={chartConfig} className="h-[250px] sm:h-[300px] w-full aspect-auto">
                  <BarChart data={hourly_data}>
                    <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--color-muted-foreground) / 0.2)" />
                    <XAxis
                      dataKey="timestamp"
                      tickFormatter={(value) => formatDateTime(value)}
                      angle={-45}
                      textAnchor="end"
                      height={60}
                      stroke="hsl(var(--color-muted-foreground))"
                      tick={{ fill: 'hsl(var(--color-muted-foreground))' }}
                    />
                    <YAxis stroke="hsl(var(--color-muted-foreground))" tick={{ fill: 'hsl(var(--color-muted-foreground))' }} />
                    <ChartTooltip
                      content={<ChartTooltipContent labelFormatter={(value) => formatDateTime(value as string)} />}
                    />
                    <Bar dataKey="cost" fill="var(--color-cost)" />
                  </BarChart>
                </ChartContainer>
                </ZoomableChart>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>{t('home.charts.tokenUsage')}</CardTitle>
                <CardDescription>{t('home.charts.tokenUsageDesc')}</CardDescription>
              </CardHeader>
              <CardContent>
                <ZoomableChart aria-label={t('home.ariaLabel.tokenUsage')}>
                <ChartContainer config={chartConfig} className="h-[250px] sm:h-[300px] w-full aspect-auto">
                  <BarChart data={hourly_data}>
                    <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--color-muted-foreground) / 0.2)" />
                    <XAxis
                      dataKey="timestamp"
                      tickFormatter={(value) => formatDateTime(value)}
                      angle={-45}
                      textAnchor="end"
                      height={60}
                      stroke="hsl(var(--color-muted-foreground))"
                      tick={{ fill: 'hsl(var(--color-muted-foreground))' }}
                    />
                    <YAxis stroke="hsl(var(--color-muted-foreground))" tick={{ fill: 'hsl(var(--color-muted-foreground))' }} />
                    <ChartTooltip
                      content={<ChartTooltipContent labelFormatter={(value) => formatDateTime(value as string)} />}
                    />
                    <Bar dataKey="tokens" fill="var(--color-tokens)" />
                  </BarChart>
                </ChartContainer>
                </ZoomableChart>
              </CardContent>
            </Card>
          </div>
        </TabsContent>

        {/* 模型统计 */}
        <TabsContent value="models" className="space-y-4">
          <div className="grid gap-4 grid-cols-1 lg:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle>{t('home.charts.modelDistribution')}</CardTitle>
                <CardDescription>{t('home.charts.modelDistributionDesc', { count: model_stats.length })}</CardDescription>
              </CardHeader>
              <CardContent>
                <ChartContainer
                  config={
                    Object.fromEntries(
                      model_stats.map((stat, i) => [
                        stat.model_name,
                        {
                          label: stat.model_name,
                          color: pieColors[i],
                        },
                      ])
                    ) as ChartConfig
                  }
                  className="h-[300px] sm:h-[400px] w-full aspect-auto"
                >
                  <PieChart>
                    <ChartTooltip content={<ChartTooltipContent />} />
                    <Pie
                      data={modelPieData}
                      cx="50%"
                      cy="50%"
                      labelLine={false}
                      label={({ name, percent }) => {
                        // 只显示占比大于5%的标签，避免小块标签重叠
                        if (percent && percent < 0.05) return ''
                        return `${name} ${percent ? (percent * 100).toFixed(0) : 0}%`
                      }}
                      outerRadius={100}
                      dataKey="value"
                      nameKey="name"
                    >
                      {modelPieData.map((entry, index) => (
                        <Cell key={`cell-${index}`} fill={entry.fill} />
                      ))}
                    </Pie>
                  </PieChart>
                </ChartContainer>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>{t('home.charts.modelDetails')}</CardTitle>
                <CardDescription>{t('home.charts.modelDetailsDesc')}</CardDescription>
              </CardHeader>
              <CardContent>
                <ScrollArea className="h-[300px] sm:h-[400px]">
                  <div className="space-y-3">
                    {model_stats.map((stat, index) => (
                      <div
                        key={index}
                        className="p-4 rounded-lg border bg-card hover:bg-accent/50 transition-colors"
                      >
                        <div className="flex items-center justify-between mb-2">
                          <h4 className="font-semibold text-sm truncate flex-1 min-w-0">
                            {stat.model_name}
                          </h4>
                          <div
                            className="w-3 h-3 rounded-full ml-2 flex-shrink-0"
                            style={{
                              backgroundColor: pieColors[index],
                            }}
                          />
                        </div>
                        <div className="grid grid-cols-2 gap-2 text-xs">
                          <div>
                            <span className="text-muted-foreground">{t('home.charts.requestCount')}:</span>
                            <span className="ml-1 font-medium">
                              {stat.request_count.toLocaleString()}
                            </span>
                          </div>
                          <div>
                            <span className="text-muted-foreground">{t('home.charts.costLabel')}:</span>
                            <span className="ml-1 font-medium">¥{stat.total_cost.toFixed(2)}</span>
                          </div>
                          <div>
                            <span className="text-muted-foreground">Tokens:</span>
                            <span className="ml-1 font-medium">
                              {(stat.total_tokens / 1000).toFixed(1)}K
                            </span>
                          </div>
                          <div>
                            <span className="text-muted-foreground">{t('home.charts.avgTime')}:</span>
                            <span className="ml-1 font-medium">
                              {stat.avg_response_time.toFixed(2)}s
                            </span>
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                </ScrollArea>
              </CardContent>
            </Card>
          </div>
        </TabsContent>
        <TabsContent value="activity">
          <Card>
            <CardHeader>
              <CardTitle>{t('home.charts.recentActivity')}</CardTitle>
              <CardDescription>{t('home.charts.recentActivityDesc')}</CardDescription>
            </CardHeader>
            <CardContent>
              <ScrollArea className="h-[400px] sm:h-[500px]">
                <div className="space-y-2">
                  {recent_activity.map((activity, index) => (
                    <div
                      key={index}
                      className="p-3 sm:p-4 rounded-lg border bg-card hover:bg-accent/50 transition-colors"
                    >
                      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 mb-2">
                        <div className="flex-1 min-w-0">
                          <div className="font-medium text-sm truncate">{activity.model}</div>
                          <div className="text-xs text-muted-foreground">
                            {activity.request_type}
                          </div>
                        </div>
                        <div className="text-xs text-muted-foreground flex-shrink-0">
                          {formatDateTime(activity.timestamp)}
                        </div>
                      </div>
                      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-xs">
                        <div>
                          <span className="text-muted-foreground">Tokens:</span>
                          <span className="ml-1">{activity.tokens}</span>
                        </div>
                        <div>
                          <span className="text-muted-foreground">{t('home.charts.costLabel')}:</span>
                          <span className="ml-1">¥{activity.cost.toFixed(4)}</span>
                        </div>
                        <div>
                          <span className="text-muted-foreground">{t('home.charts.timeCost')}:</span>
                          <span className="ml-1">{activity.time_cost.toFixed(2)}s</span>
                        </div>
                        <div>
                          <span className="text-muted-foreground">{t('home.charts.status')}:</span>
                          <span
                            className={`ml-1 ${activity.status === 'success' ? 'text-green-600' : 'text-red-600'}`}
                          >
                            {activity.status}
                          </span>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              </ScrollArea>
            </CardContent>
          </Card>
        </TabsContent>

        {/* 日统计 */}
        <TabsContent value="daily">
          <Card>
            <CardHeader>
              <CardTitle>{t('home.charts.dailyStats')}</CardTitle>
              <CardDescription>{t('home.charts.dailyStatsDesc')}</CardDescription>
            </CardHeader>
            <CardContent>
              <ChartContainer
                config={{
                  requests: {
                    label: t('home.charts.requests'),
                    color: 'hsl(var(--color-chart-1))',
                  },
                  cost: {
                    label: t('home.charts.cost'),
                    color: 'hsl(var(--color-chart-2))',
                  },
                }}
                className="h-[400px] sm:h-[500px] w-full aspect-auto"
              >
                <BarChart data={daily_data}>
                  <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--color-muted-foreground) / 0.2)" />
                  <XAxis
                    dataKey="timestamp"
                    tickFormatter={(value) => {
                      const date = new Date(value)
                      return new Intl.DateTimeFormat(currentLocale, {
                        month: 'numeric',
                        day: 'numeric',
                      }).format(date)
                    }}
                    stroke="hsl(var(--color-muted-foreground))"
                    tick={{ fill: 'hsl(var(--color-muted-foreground))' }}
                  />
                  <YAxis yAxisId="left" stroke="hsl(var(--color-muted-foreground))" tick={{ fill: 'hsl(var(--color-muted-foreground))' }} />
                  <YAxis yAxisId="right" orientation="right" stroke="hsl(var(--color-muted-foreground))" tick={{ fill: 'hsl(var(--color-muted-foreground))' }} />
                  <ChartTooltip
                    content={
                      <ChartTooltipContent
                        labelFormatter={(value) => {
                          const date = new Date(value as string)
                          return date.toLocaleDateString(currentLocale)
                        }}
                      />
                    }
                  />
                  <ChartLegend content={<ChartLegendContent />} />
                  <Bar yAxisId="left" dataKey="requests" fill="var(--color-requests)" />
                  <Bar yAxisId="right" dataKey="cost" fill="var(--color-cost)" />
                </BarChart>
              </ChartContainer>
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>

      <div id="home-card-controls-bottom" className="flex justify-end pt-2" />

      <Dialog open={quickShortcutDialogOpen} onOpenChange={setQuickShortcutDialogOpen}>
        <DialogContent style={{ '--dialog-width': '46rem' } as CSSProperties}>
          <DialogHeader>
            <DialogTitle>{t('home.quickActions.dialog.title')}</DialogTitle>
            <DialogDescription>
              {t('home.quickActions.dialog.description')}
            </DialogDescription>
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
                      className="flex cursor-pointer items-start gap-3 rounded-lg border p-3 transition-colors hover:bg-accent/40"
                    >
                      <Checkbox
                        id={checkboxId}
                        className="mt-0.5"
                        checked={checked}
                        onCheckedChange={(value) => toggleQuickShortcut(shortcut.id, value === true)}
                      />
                      <Icon className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
                      <span className="min-w-0 flex-1">
                        <span className="flex flex-wrap items-center gap-2">
                          <span className="font-medium">{shortcut.label}</span>
                          <Badge variant="outline" className="text-[10px]">
                            {t(`home.quickActions.categories.${shortcut.category}`)}
                          </Badge>
                        </span>
                        <span className="mt-1 block text-sm text-muted-foreground">
                          {shortcut.description}
                        </span>
                      </span>
                    </label>
                  )
                })}
                {filteredQuickShortcutOptions.length === 0 && (
                  <div className="rounded-lg border border-dashed p-6 text-center text-sm text-muted-foreground">
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
      <ExpressionReviewer
        open={isReviewerOpen}
        onOpenChange={(open) => {
          setIsReviewerOpen(open)
          if (!open) {
            // 关闭审核器时刷新统计
            fetchReviewStats()
          }
        }}
      />
    </div>
    </ScrollArea>
  )
}
