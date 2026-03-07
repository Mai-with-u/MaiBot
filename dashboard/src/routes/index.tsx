import { useEffect, useState, useCallback, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import axios from 'axios'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Skeleton } from '@/components/ui/skeleton'
import { Progress } from '@/components/ui/progress'
import { fetchWithAuth } from '@/lib/fetch-with-auth'
import {
  ChartContainer,
  ChartTooltip,
  ChartTooltipContent,
  ChartLegend,
  ChartLegendContent,
  type ChartConfig,
} from '@/components/ui/chart'
import {
  LineChart,
  Line,
  BarChart,
  Bar,
  PieChart,
  Pie,
  Cell,
  XAxis,
  YAxis,
  CartesianGrid,
} from 'recharts'
import {
  Activity,
  TrendingUp,
  DollarSign,
  Clock,
  MessageSquare,
  Zap,
  Database,
  RefreshCw,
  Power,
  RotateCcw,
  FileText,
  Settings,
  Puzzle,
  CheckCircle2,
  AlertCircle,
  ClipboardList,
  ClipboardCheck,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Link } from '@tanstack/react-router'
import { RestartProvider, useRestart } from '@/lib/restart-context'
import { RestartOverlay } from '@/components/restart-overlay'
import { ExpressionReviewer } from '@/components/expression-reviewer'
import { getReviewStats } from '@/lib/expression-api'
import { ZoomableChart } from '@/components/ui/zoomable-chart'

// 主导出组件：包装 RestartProvider
export function IndexPage() {
  return (
    <RestartProvider>
      <IndexPageContent />
    </RestartProvider>
  )
}

// 机器人状态接口
interface BotStatus {
  running: boolean
  uptime: number
  version: string
  start_time: string
}

interface StatisticsSummary {
  total_requests: number
  total_cost: number
  total_tokens: number
  online_time: number
  total_messages: number
  total_replies: number
  avg_response_time: number
  cost_per_hour: number
  tokens_per_hour: number
}

interface ModelStatistics {
  model_name: string
  request_count: number
  total_cost: number
  total_tokens: number
  avg_response_time: number
}

interface TimeSeriesData {
  timestamp: string
  requests: number
  cost: number
  tokens: number
}

interface RecentActivity {
  timestamp: string
  model: string
  request_type: string
  tokens: number
  cost: number
  time_cost: number
  status: string
}

interface DashboardData {
  summary: StatisticsSummary
  model_stats: ModelStatistics[]
  hourly_data: TimeSeriesData[]
  daily_data: TimeSeriesData[]
  recent_activity: RecentActivity[]
}

// 为饼图生成更丰富的颜色方案 (HSL色相均匀分布)
const generatePieColors = (count: number): string[] => {
  const colors: string[] = []
  for (let i = 0; i < count; i++) {
    // 使用黄金角度分布色相，避免相邻颜色相似
    const hue = (i * 137.508) % 360
    colors.push(`hsl(${hue}, 70%, 55%)`)
  }
  return colors
}

// 内部实现组件
function IndexPageContent() {
  const { t } = useTranslation()
  const [dashboardData, setDashboardData] = useState<DashboardData | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadingProgress, setLoadingProgress] = useState(0)
  const [timeRange, setTimeRange] = useState(24) // 默认24小时
  const [autoRefresh, setAutoRefresh] = useState(true)
  const [hitokoto, setHitokoto] = useState<{ hitokoto: string; from: string } | null>(null)
  const [hitokotoLoading, setHitokotoLoading] = useState(true)
  const [botStatus, setBotStatus] = useState<BotStatus | null>(null)
  const [isReviewerOpen, setIsReviewerOpen] = useState(false)
  const [uncheckedCount, setUncheckedCount] = useState(0)
  const { triggerRestart, isRestarting } = useRestart()
  
  // 使用 ref 跟踪组件是否已卸载，防止内存泄漏
  const isMountedRef = useRef(true)
  // 使用 ref 存储 interval ID，方便清理
  const refreshIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null)

  // 组件卸载时清理
  useEffect(() => {
    isMountedRef.current = true
    return () => {
      isMountedRef.current = false
      // 清理自动刷新定时器
      if (refreshIntervalRef.current) {
        clearInterval(refreshIntervalRef.current)
        refreshIntervalRef.current = null
      }
    }
  }, [])

  // 获取审核统计
  const fetchReviewStats = useCallback(async () => {
    try {
      const result = await getReviewStats()
      if (result.success && isMountedRef.current) {
        setUncheckedCount(result.data.unchecked)
      }
    } catch (error) {
      console.error('获取审核统计失败:', error)
    }
  }, [])

  // 获取一言
  const fetchHitokoto = useCallback(async () => {
    try {
      setHitokotoLoading(true)
      const response = await axios.get('https://v1.hitokoto.cn/?c=a&c=b&c=c&c=d&c=h&c=i&c=k')
      if (isMountedRef.current) {
        setHitokoto({
          hitokoto: response.data.hitokoto,
          from: response.data.from || response.data.from_who || t('home.unknownSource')
        })
      }
    } catch (error) {
      console.error('获取一言失败:', error)
      if (isMountedRef.current) {
        setHitokoto({
          hitokoto: t('home.hitokotoFallback'),
          from: t('home.hitokotoFallbackFrom')
        })
      }
    } finally {
      if (isMountedRef.current) {
        setHitokotoLoading(false)
      }
    }
  }, [t])

  // 获取机器人状态
  const fetchBotStatus = useCallback(async () => {
    try {
      const response = await fetchWithAuth('/api/webui/system/status')
      if (!isMountedRef.current) return
      if (response.ok) {
        const data = await response.json()
        setBotStatus(data)
      } else {
        setBotStatus(null)
      }
    } catch (error) {
      console.error('获取机器人状态失败:', error)
      if (isMountedRef.current) {
        setBotStatus(null)
      }
    }
  }, [])

  // 重启机器人
  const handleRestart = async () => {
    await triggerRestart()
  }

  const fetchDashboardData = useCallback(async () => {
    try {
      const response = await fetchWithAuth(`/api/webui/statistics/dashboard?hours=${timeRange}`)
      if (!isMountedRef.current) return
      if (response.ok) {
        const data = await response.json()
        setDashboardData(data)
      }
      setLoading(false)
      setLoadingProgress(100)
    } catch (error) {
      console.error('Failed to fetch dashboard data:', error)
      if (isMountedRef.current) {
        setLoading(false)
        setLoadingProgress(100)
      }
    }
  }, [timeRange])

  // 伪加载进度条效果
  useEffect(() => {
    if (!loading) return

    setLoadingProgress(0)
    
    // 快速到15%
    const timer1 = setTimeout(() => setLoadingProgress(15), 200)
    // 到30%
    const timer2 = setTimeout(() => setLoadingProgress(30), 800)
    // 到45%
    const timer3 = setTimeout(() => setLoadingProgress(45), 2000)
    // 到60%
    const timer4 = setTimeout(() => setLoadingProgress(60), 4000)
    // 到75%
    const timer5 = setTimeout(() => setLoadingProgress(75), 6500)
    // 到85%
    const timer6 = setTimeout(() => setLoadingProgress(85), 9000)
    // 到92%
    const timer7 = setTimeout(() => setLoadingProgress(92), 11000)

    return () => {
      clearTimeout(timer1)
      clearTimeout(timer2)
      clearTimeout(timer3)
      clearTimeout(timer4)
      clearTimeout(timer5)
      clearTimeout(timer6)
      clearTimeout(timer7)
    }
  }, [loading])

  useEffect(() => {
    fetchDashboardData()
    fetchHitokoto()
    fetchBotStatus()
    fetchReviewStats()
  }, [fetchDashboardData, fetchHitokoto, fetchBotStatus, fetchReviewStats])

  // 自动刷新
  useEffect(() => {
    // 清理旧的定时器
    if (refreshIntervalRef.current) {
      clearInterval(refreshIntervalRef.current)
      refreshIntervalRef.current = null
    }
    
    if (!autoRefresh) return

    refreshIntervalRef.current = setInterval(() => {
      if (isMountedRef.current) {
        fetchDashboardData()
        fetchBotStatus()
      }
    }, 30000) // 30秒刷新一次

    return () => {
      if (refreshIntervalRef.current) {
        clearInterval(refreshIntervalRef.current)
        refreshIntervalRef.current = null
      }
    }
  }, [autoRefresh, fetchDashboardData, fetchBotStatus])

  if (loading || !dashboardData) {
    return (
      <div className="flex items-center justify-center h-[calc(100vh-200px)]">
        <div className="text-center space-y-6 w-full max-w-md px-4">
          <RefreshCw className="h-12 w-12 animate-spin mx-auto text-primary" />
          <div className="space-y-2">
            <p className="text-lg font-medium">{t('home.loading')}</p>
            <p className="text-sm text-muted-foreground">{t('home.loadingHint')}</p>
          </div>
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
    const exact = num.toLocaleString('zh-CN')
    
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
    const exact = `¥${num.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
    
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
    return date.toLocaleString('zh-CN', {
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    })
  }

  // 准备饼图数据（模型请求分布）- 使用黄金角度分布避免相邻颜色相似
  const pieColors = generatePieColors(model_stats.length)
  const modelPieData = model_stats.map((stat, index) => ({
    name: stat.model_name,
    value: stat.request_count,
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

  return (
    <ScrollArea className="h-full">
      <div className="space-y-4 sm:space-y-6 p-4 sm:p-6">
      {/* 标题和控制栏 */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl sm:text-3xl font-bold">{t('home.title')}</h1>
          <p className="text-sm text-muted-foreground mt-1">
            {t('home.subtitle')}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Tabs value={timeRange.toString()} onValueChange={(v) => setTimeRange(Number(v))}>
            <TabsList className="grid grid-cols-3 w-full sm:w-auto">
              <TabsTrigger value="24">{t('home.timeRange.24h')}</TabsTrigger>
              <TabsTrigger value="168">{t('home.timeRange.7d')}</TabsTrigger>
              <TabsTrigger value="720">{t('home.timeRange.30d')}</TabsTrigger>
            </TabsList>
          </Tabs>
          <Button
            variant={autoRefresh ? 'default' : 'outline'}
            size="sm"
            onClick={() => setAutoRefresh(!autoRefresh)}
            className="gap-2"
          >
            <RefreshCw className={`h-4 w-4 ${autoRefresh ? 'animate-spin' : ''}`} />
            <span className="hidden sm:inline">{t('home.autoRefresh')}</span>
          </Button>
          <Button variant="outline" size="sm" onClick={fetchDashboardData}>
            <RefreshCw className="h-4 w-4" />
          </Button>
        </div>
      </div>

      {/* 一言 */}
      <div className="flex items-center gap-3 px-4 py-2 rounded-lg border border-dashed border-muted-foreground/30 bg-muted/20">
        {hitokotoLoading ? (
          <Skeleton className="h-5 flex-1" />
        ) : hitokoto ? (
          <p className="flex-1 text-sm text-muted-foreground italic truncate">
            "{hitokoto.hitokoto}" —— {hitokoto.from}
          </p>
        ) : null}
        <Button 
          variant="ghost" 
          size="icon" 
          className="h-7 w-7 shrink-0" 
          onClick={fetchHitokoto}
          disabled={hitokotoLoading}
        >
          <RefreshCw className={`h-3.5 w-3.5 ${hitokotoLoading ? 'animate-spin' : ''}`} />
        </Button>
      </div>

      {/* 机器人状态和快速操作 */}
      <div className="grid gap-4 grid-cols-1 lg:grid-cols-3">
        {/* 机器人状态卡片 */}
        <Card className="lg:col-span-1">
          <CardHeader className="pb-3">
            <CardTitle className="text-sm font-medium flex items-center gap-2">
              <Power className="h-4 w-4" />
              {t('home.botStatus.title')}
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="flex items-center gap-4">
              <div className="flex items-center gap-2">
                {botStatus?.running ? (
                  <>
                    <div className="h-3 w-3 rounded-full bg-green-500 animate-pulse" />
                    <Badge variant="outline" className="text-green-600 border-green-300 bg-green-50">
                      <CheckCircle2 className="h-3 w-3 mr-1" />
                      {t('home.botStatus.running')}
                    </Badge>
                  </>
                ) : (
                  <>
                    <div className="h-3 w-3 rounded-full bg-red-500" />
                    <Badge variant="outline" className="text-red-600 border-red-300 bg-red-50">
                      <AlertCircle className="h-3 w-3 mr-1" />
                      {t('home.botStatus.stopped')}
                    </Badge>
                  </>
                )}
              </div>
              {botStatus && (
                <div className="text-xs text-muted-foreground">
                  <span>v{botStatus.version}</span>
                  <span className="mx-2">|</span>
                  <span>{t('home.botStatus.uptime', { time: formatTime(botStatus.uptime) })}</span>
                </div>
              )}
            </div>
          </CardContent>
        </Card>

        {/* 快速操作卡片 */}
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-sm font-medium flex items-center gap-2">
              <Zap className="h-4 w-4" />
              {t('home.quickActions.title')}
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="flex flex-wrap gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={handleRestart}
                disabled={isRestarting}
                className="gap-2"
              >
                <RotateCcw className={`h-4 w-4 ${isRestarting ? 'animate-spin' : ''}`} />
                {isRestarting ? t('home.quickActions.restarting') : t('home.quickActions.restart')}
              </Button>
              <Button 
                variant="outline" 
                size="sm" 
                onClick={() => setIsReviewerOpen(true)}
                className="gap-2"
              >
                <ClipboardCheck className="h-4 w-4" />
                {t('home.quickActions.expressionReview')}
                {uncheckedCount > 0 && (
                  <span className="ml-1 px-1.5 py-0.5 text-xs rounded-full bg-orange-500 text-white">
                    {uncheckedCount > 99 ? '99+' : uncheckedCount}
                  </span>
                )}
              </Button>
              <Button variant="outline" size="sm" asChild className="gap-2">
                <Link to="/logs">
                  <FileText className="h-4 w-4" />
                  {t('home.quickActions.viewLogs')}
                </Link>
              </Button>
              <Button variant="outline" size="sm" asChild className="gap-2">
                <Link to="/plugins">
                  <Puzzle className="h-4 w-4" />
                  {t('home.quickActions.pluginManage')}
                </Link>
              </Button>
              <Button variant="outline" size="sm" asChild className="gap-2">
                <Link to="/settings">
                  <Settings className="h-4 w-4" />
                  {t('home.quickActions.systemSettings')}
                </Link>
              </Button>
            </div>
          </CardContent>
        </Card>

        {/* 问卷调查卡片 */}
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-sm font-medium flex items-center gap-2">
              <ClipboardList className="h-4 w-4" />
              {t('home.survey.title')}
            </CardTitle>
            <CardDescription className="text-xs">
              {t('home.survey.description')}
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="flex flex-wrap gap-2">
              <Button variant="outline" size="sm" asChild className="gap-2">
                <Link to="/survey/webui-feedback">
                  <FileText className="h-4 w-4" />
                  {t('home.survey.webui')}
                </Link>
              </Button>
              <Button variant="outline" size="sm" asChild className="gap-2">
                <Link to="/survey/maibot-feedback">
                  <MessageSquare className="h-4 w-4" />
                  {t('home.survey.maibot')}
                </Link>
              </Button>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* 核心指标卡片 */}
      <div className="grid gap-4 grid-cols-1 xs:grid-cols-2 lg:grid-cols-4">
        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">{t('home.stats.totalRequests')}</CardTitle>
            <Activity className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">
              {formatNumber(summary.total_requests).display}
              {formatNumber(summary.total_requests).needsExact && (
                <span className="text-xs font-normal text-muted-foreground ml-1">({formatNumber(summary.total_requests).exact})</span>
              )}
            </div>
            <p className="text-xs text-muted-foreground mt-1">
              {t('home.stats.recentPeriod', { range: timeRange < 48 ? timeRange + t('home.stats.hours') : Math.floor(timeRange / 24) + t('home.stats.days') })}
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">{t('home.stats.totalCost')}</CardTitle>
            <DollarSign className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">
              {formatCurrency(summary.total_cost).display}
              {formatCurrency(summary.total_cost).needsExact && (
                <span className="text-xs font-normal text-muted-foreground ml-1">({formatCurrency(summary.total_cost).exact})</span>
              )}
            </div>
            <p className="text-xs text-muted-foreground mt-1">
              {summary.cost_per_hour > 0 ? t('home.stats.perHour', { value: `¥${summary.cost_per_hour.toFixed(2)}` }) : t('home.stats.noData')}
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">{t('home.stats.tokenUsage')}</CardTitle>
            <Database className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">
              {formatNumber(summary.total_tokens).display}
              {formatNumber(summary.total_tokens).needsExact && (
                <span className="text-xs font-normal text-muted-foreground ml-1">({formatNumber(summary.total_tokens).exact})</span>
              )}
            </div>
            <p className="text-xs text-muted-foreground mt-1">
              {summary.tokens_per_hour > 0
                ? t('home.stats.perHour', { value: formatNumber(summary.tokens_per_hour).display })
                : t('home.stats.noData')}
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">{t('home.stats.avgResponse')}</CardTitle>
            <Zap className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{summary.avg_response_time.toFixed(2)}s</div>
            <p className="text-xs text-muted-foreground mt-1">{t('home.stats.avgResponseDesc')}</p>
          </CardContent>
        </Card>
      </div>

      {/* 次要指标 */}
      <div className="grid gap-4 grid-cols-1 sm:grid-cols-3">
        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">{t('home.stats.onlineTime')}</CardTitle>
            <Clock className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-xl font-bold">
              {formatTime(summary.online_time)}
              <span className="text-xs font-normal text-muted-foreground ml-1">({summary.online_time.toLocaleString()}{t('home.stats.seconds')})</span>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">{t('home.stats.messageProcessing')}</CardTitle>
            <MessageSquare className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-xl font-bold">
              {formatNumber(summary.total_messages).display}
              {formatNumber(summary.total_messages).needsExact && (
                <span className="text-xs font-normal text-muted-foreground ml-1">({formatNumber(summary.total_messages).exact})</span>
              )}
            </div>
            <p className="text-xs text-muted-foreground mt-1">
              {t('home.stats.replied', { num: formatNumber(summary.total_replies).display })}
              {formatNumber(summary.total_replies).needsExact && (
                <span>({formatNumber(summary.total_replies).exact})</span>
              )}
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">{t('home.stats.costEfficiency')}</CardTitle>
            <TrendingUp className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-xl font-bold">
              {summary.total_messages > 0
                ? `¥${((summary.total_cost / summary.total_messages) * 100).toFixed(2)}`
                : '¥0.00'}
            </div>
            <p className="text-xs text-muted-foreground mt-1">{t('home.stats.per100Messages')}</p>
          </CardContent>
        </Card>
      </div>

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
                              backgroundColor: `hsl(var(--color-chart-${(index % 5) + 1}))`,
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
                      return `${date.getMonth() + 1}/${date.getDate()}`
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
                          return date.toLocaleDateString('zh-CN')
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
