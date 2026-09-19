import { format } from 'date-fns'
import { zhCN } from 'date-fns/locale'
import {
  BarChart3,
  BrainCircuit,
  Calendar as CalendarIcon,
  ChevronDown,
  ChevronUp,
  Download,
  Filter,
  Pause,
  Play,
  Search,
  Terminal,
  Trash2,
  Type,
  X,
} from 'lucide-react'
import {
  lazy,
  Suspense,
  type KeyboardEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react'
import { createPortal } from 'react-dom'
import { useVirtualizer } from '@tanstack/react-virtual'

import { Button } from '@/components/ui/button'
import { Calendar } from '@/components/ui/calendar'
import { Card } from '@/components/ui/card'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Input } from '@/components/ui/input'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { Slider } from '@/components/ui/slider'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { logWebSocket, type LogEntry } from '@/lib/log-websocket'
import { getSetting, setSetting } from '@/lib/settings-manager'
import { cn } from '@/lib/utils'

import { ReasoningProcessPage } from './reasoning-process'

const StatisticsPage = lazy(() =>
  import('./statistics').then((module) => ({ default: module.StatisticsPage }))
)

// 字号配置
type FontSize = 'xs' | 'sm' | 'base'
type LogLevelFilter = LogEntry['level'] | 'all'
type LogViewerTab = 'terminal' | 'reasoning' | 'statistics'

const LINE_SPACING_MAX = 12
const LINE_SPACING_MIN = 0
const COLUMN_WIDTH_EXTRA_MAX = 96
const COLUMN_WIDTH_EXTRA_MIN = 0
const LOG_VIEWER_SWITCH_HINT_DISMISSED_KEY = 'log-viewer-switch-hint-dismissed'
const LOG_VIEWER_ACTIVE_TAB_KEY = 'log-viewer-active-tab'
const TOPBAR_SWITCH_COMPACT_GAP = 12
const TOPBAR_SWITCH_EXPAND_GAP = 72

const fontSizeConfig: Record<FontSize, { label: string; rowHeight: number; class: string }> = {
  xs: { label: '小', rowHeight: 28, class: 'text-[10px] sm:text-xs' },
  sm: { label: '中', rowHeight: 36, class: 'text-xs sm:text-sm' },
  base: { label: '大', rowHeight: 44, class: 'text-sm sm:text-base' },
}

const logColumnLayoutConfig: Record<
  FontSize,
  {
    gapClass: string
    levelClass: string
    levelWidth: number
    moduleClass: string
    moduleWidth: number
    timestampClass: string
    timestampWidth: number
  }
> = {
  xs: {
    gapClass: 'gap-1.5',
    timestampClass: 'w-[60px] lg:w-[60px]',
    timestampWidth: 60,
    levelClass: 'w-[30px] lg:w-[30px]',
    levelWidth: 30,
    moduleClass: 'w-[90px] lg:w-[90px]',
    moduleWidth: 90,
  },
  sm: {
    gapClass: 'gap-2',
    timestampClass: 'w-[76px] lg:w-[76px]',
    timestampWidth: 76,
    levelClass: 'w-[38px] lg:w-[38px]',
    levelWidth: 38,
    moduleClass: 'w-[112px] lg:w-[112px]',
    moduleWidth: 112,
  },
  base: {
    gapClass: 'gap-2.5',
    timestampClass: 'w-[92px] lg:w-[92px]',
    timestampWidth: 92,
    levelClass: 'w-[46px] lg:w-[46px]',
    levelWidth: 46,
    moduleClass: 'w-[136px] lg:w-[136px]',
    moduleWidth: 136,
  },
}

const levelPriority: Record<LogEntry['level'], number> = {
  DEBUG: 10,
  INFO: 20,
  WARNING: 30,
  ERROR: 40,
  CRITICAL: 50,
}

function formatLogTimestamp(timestamp: string) {
  const normalized = timestamp.trim()
  const match = normalized.match(/^(\d{4})-(\d{2})-(\d{2})([ T].*)$/)
  if (!match) {
    return timestamp
  }

  return `${match[2]}-${match[3]}${match[4].replace(/^T/, ' ')}`
}

function getModuleTextStyle(log: LogEntry) {
  if (!log.moduleColor) {
    return undefined
  }

  return {
    color: log.moduleColor,
    fontWeight: log.moduleBold ? 700 : undefined,
  }
}

function formatLogLevel(level: LogEntry['level']) {
  return level.slice(0, 4)
}

function clampNumber(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value))
}

function isFontSize(value: string): value is FontSize {
  return value in fontSizeConfig
}

function isLogLevelFilter(value: string): value is LogLevelFilter {
  return value === 'all' || value in levelPriority
}

const legacyModuleDisplayNames: Record<string, string> = {
  '_maibot_plugin_maibot_team_mai_statstic_plugin.client_statistics_service': 'Mai统计客户端服务',
  '_maibot_plugin_maibot_team_mai_statstic_plugin.plugin_store_service': 'Mai统计存储服务',
  '<runner>': '插件运行器',
  async_task_manager: '异步任务管理',
  chat: '所见',
  chat_manager: '聊天管理器',
  chat_utils: '聊天工具',
  emoji: '表情包',
  expression_vector_index: '表达向量索引',
  image: '图片',
  image_cache_cleanup: '图片缓存清理',
  local_storage: '本地存储',
  maisaka_monitor_event_store: '麦麦监控事件',
  maisaka_runtime: 'MaiSaka',
  maisaka_turn_scheduler: '读空气',
  model_utils: '模型工具',
  person_info: '人物',
  'plugin.github.sengokucola.statistics-chart-plugin': '统计图表插件',
  'plugin.local.replyer-regex-guard': '回复正则保护插件',
  'plugin.maibot-team.mai-statstic-plugin': 'Mai统计插件',
  'plugin.maibot-team.maibot-helper': '麦麦助手插件',
  'plugin.maibot-team.snowluma-adapter': 'SnowLuma适配器',
  'plugin.self_identity_plugin': '自我认知插件',
  'plugin.sengokucola.deepseek-thinking-marker': 'DeepSeek思考标记插件',
  'webui.api': 'WebUI接口',
  'webui.unified_ws': 'WebUI统一连接',
  'webui.ws_auth': 'WebUI鉴权连接',
  webui: 'WebUI',
}

function getModuleDisplayName(log: LogEntry): string {
  const backendDisplayName = log.moduleDisplayName?.trim()
  if (backendDisplayName && backendDisplayName !== log.module) return backendDisplayName
  if (legacyModuleDisplayNames[log.module]) return legacyModuleDisplayNames[log.module]
  if (log.module.includes('site-packages.watchfiles')) return '文件变更监控'
  if (log.module.startsWith('_maibot_plugin_')) return '插件运行器'
  return backendDisplayName || log.module
}

function loadStoredLogViewerTab(): LogViewerTab {
  if (typeof window === 'undefined') return 'terminal'

  const storedTab = localStorage.getItem(LOG_VIEWER_ACTIVE_TAB_KEY)
  return storedTab === 'reasoning' || storedTab === 'statistics' ? storedTab : 'terminal'
}

interface LogTerminalPaneProps {
  toolbarContainerId: string
  toolbarVisible: boolean
}

function LogTerminalPane({ toolbarContainerId, toolbarVisible }: LogTerminalPaneProps) {
  const [logs, setLogs] = useState<LogEntry[]>([])
  const [searchQuery, setSearchQuery] = useState('')
  const [levelFilter, setLevelFilter] = useState<LogLevelFilter>(() => {
    const savedLevelFilter = getSetting('logLevelFilter')
    return isLogLevelFilter(savedLevelFilter) ? savedLevelFilter : 'INFO'
  })
  const [storedModuleFilter, setStoredModuleFilter] = useState<string>(() =>
    getSetting('logModuleFilter')
  )
  const [dateFrom, setDateFrom] = useState<Date | undefined>(undefined)
  const [dateTo, setDateTo] = useState<Date | undefined>(undefined)
  const [autoScroll, setAutoScroll] = useState(() => getSetting('logAutoScroll'))
  const [connected, setConnected] = useState(false)
  const [fontSize, setFontSize] = useState<FontSize>(() => {
    const savedFontSize = getSetting('logFontSize')
    return isFontSize(savedFontSize) ? savedFontSize : 'xs'
  }) // 默认使用小字号以显示更多信息
  const [lineSpacing, setLineSpacing] = useState(() =>
    clampNumber(getSetting('logLineSpacing'), LINE_SPACING_MIN, LINE_SPACING_MAX)
  ) // 行间距，默认4px（紧凑）
  const [columnWidthExtra, setColumnWidthExtra] = useState(() =>
    clampNumber(getSetting('logColumnWidthExtra'), COLUMN_WIDTH_EXTRA_MIN, COLUMN_WIDTH_EXTRA_MAX)
  )
  const [filtersOpen, setFiltersOpen] = useState(() => getSetting('logFiltersOpen'))
  const [toolbarRoot, setToolbarRoot] = useState<HTMLElement | null>(null)
  const parentRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    setToolbarRoot(document.getElementById(toolbarContainerId))
  }, [toolbarContainerId])

  // 订阅全局 WebSocket 连接
  useEffect(() => {
    // 日志管理器会批量推送快照，避免每条日志都触发一次 React 更新。
    const unsubscribeLogs = logWebSocket.onLog((nextLogs) => {
      setLogs(nextLogs)
    })

    const unsubscribeConnection = logWebSocket.onConnectionChange((isConnected) => {
      setConnected(isConnected)
    })

    return () => {
      unsubscribeLogs()
      unsubscribeConnection()
    }
  }, [])

  // 解析并缓存已隐藏的模块名集合
  const hiddenModules = useMemo(() => {
    if (!storedModuleFilter || storedModuleFilter === 'all') {
      return new Set<string>()
    }
    try {
      const parsed = JSON.parse(storedModuleFilter)
      if (Array.isArray(parsed)) {
        return new Set<string>(parsed)
      }
    } catch {
      // 解析失败时降级为不隐藏
    }
    return new Set<string>()
  }, [storedModuleFilter])

  // 动态收集当前日志中出现过的所有模块
  const availableModules = useMemo(() => {
    const moduleMap = new Map<string, string>()
    for (const log of logs) {
      if (!moduleMap.has(log.module)) {
        moduleMap.set(log.module, getModuleDisplayName(log))
      }
    }
    return Array.from(moduleMap.entries()).map(([module, displayName]) => ({
      module,
      displayName,
    }))
  }, [logs])

  // 过滤日志
  const filteredLogs = useMemo(() => {
    const query = searchQuery.trim().toLowerCase()

    return logs.filter((log) => {
      // 模块过滤
      if (hiddenModules.has(log.module)) {
        return false
      }

      // 日志级别过滤
      if (levelFilter !== 'all') {
        const minPriority = levelPriority[levelFilter]
        const currentPriority = levelPriority[log.level] ?? 0
        if (currentPriority < minPriority) {
          return false
        }
      }

      // 文本搜索（同时匹配模块名、显示名和消息内容）
      if (query) {
        const moduleMatch = log.module.toLowerCase().includes(query)
        const displayNameMatch = getModuleDisplayName(log).toLowerCase().includes(query)
        const messageMatch = log.message.toLowerCase().includes(query)
        if (!moduleMatch && !displayNameMatch && !messageMatch) {
          return false
        }
      }

      // 日期范围过滤
      if (dateFrom || dateTo) {
        const logDate = new Date(log.timestamp)
        if (dateFrom) {
          const fromDate = new Date(dateFrom)
          fromDate.setHours(0, 0, 0, 0)
          if (logDate < fromDate) return false
        }
        if (dateTo) {
          const toDate = new Date(dateTo)
          toDate.setHours(23, 59, 59, 999)
          if (logDate > toDate) return false
        }
      }

      return true
    })
  }, [logs, hiddenModules, levelFilter, searchQuery, dateFrom, dateTo])

  // 动态行高 = 字号基础高度 + 行间距
  const dynamicRowHeight = fontSizeConfig[fontSize].rowHeight + lineSpacing

  // 虚拟列表
  const rowVirtualizer = useVirtualizer({
    count: filteredLogs.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => dynamicRowHeight,
    overscan: 20,
  })

  // 自动滚动到底部（使用虚拟列表的 scrollToIndex）
  useEffect(() => {
    if (autoScroll && filteredLogs.length > 0) {
      rowVirtualizer.scrollToIndex(filteredLogs.length - 1, {
        align: 'end',
        behavior: 'auto',
      })
    }
  }, [filteredLogs.length, autoScroll, rowVirtualizer])

  // 清空日志（仅清空当前前端显示）
  const handleClear = () => {
    setLogs([])
  }

  // 导出日志
  const handleExport = () => {
    const content = filteredLogs
      .map(
        (log) =>
          `[${log.timestamp}] [${log.level}] [${log.module}${log.line_no !== undefined ? `:${log.line_no}` : ''}] ${log.message}`
      )
      .join('\n')

    const blob = new Blob([content], { type: 'text/plain;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `logs-${format(new Date(), 'yyyy-MM-dd-HHmmss')}.txt`
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
  }

  // 切换模块显示状态
  const toggleModule = (module: string) => {
    const next = new Set(hiddenModules)
    if (next.has(module)) {
      next.delete(module)
    } else {
      next.add(module)
    }
    const serialized = next.size === 0 ? 'all' : JSON.stringify(Array.from(next))
    setStoredModuleFilter(serialized)
    setSetting('logModuleFilter', serialized)
  }

  // 切换自动滚动
  const handleToggleAutoScroll = () => {
    const next = !autoScroll
    setAutoScroll(next)
    setSetting('logAutoScroll', next)
  }

  // 切换级别过滤
  const handleLevelFilterChange = (value: LogLevelFilter) => {
    setLevelFilter(value)
    setSetting('logLevelFilter', value)
  }

  // 切换字号
  const handleFontSizeChange = (value: FontSize) => {
    setFontSize(value)
    setSetting('logFontSize', value)
  }

  // 调整行间距
  const handleLineSpacingChange = (values: number[]) => {
    const next = clampNumber(values[0] ?? 4, LINE_SPACING_MIN, LINE_SPACING_MAX)
    setLineSpacing(next)
    setSetting('logLineSpacing', next)
  }

  // 调整列宽增量
  const handleColumnWidthExtraChange = (values: number[]) => {
    const next = clampNumber(
      values[0] ?? 0,
      COLUMN_WIDTH_EXTRA_MIN,
      COLUMN_WIDTH_EXTRA_MAX
    )
    setColumnWidthExtra(next)
    setSetting('logColumnWidthExtra', next)
  }

  // 切换筛选展开/收起
  const handleFiltersOpenChange = (open: boolean) => {
    setFiltersOpen(open)
    setSetting('logFiltersOpen', open)
  }

  // 重置所有筛选
  const handleResetFilters = () => {
    setSearchQuery('')
    handleLevelFilterChange('INFO')
    setStoredModuleFilter('all')
    setSetting('logModuleFilter', 'all')
    setDateFrom(undefined)
    setDateTo(undefined)
  }

  // 键盘快捷键处理
  const handleKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Escape') {
      setSearchQuery('')
    }
  }

  // 动态列宽计算
  const currentLayout = logColumnLayoutConfig[fontSize]
  const dynamicTimestampWidth = currentLayout.timestampWidth + columnWidthExtra
  const dynamicLevelWidth = currentLayout.levelWidth + Math.round(columnWidthExtra * 0.5)
  const dynamicModuleWidth = currentLayout.moduleWidth + columnWidthExtra

  // 工具栏内容
  const toolbarContent = (
    <Collapsible open={filtersOpen} onOpenChange={handleFiltersOpenChange}>
      <div className="grid w-full gap-2 lg:grid-cols-[minmax(0,1fr)_minmax(620px,760px)]">
        {/* 模块标签筛选栏 */}
        <div
          className={cn(
            'flex min-h-9 min-w-0 flex-wrap content-start items-start gap-1.5',
            'border-border/80 bg-background/35 overflow-y-auto border p-1.5',
            '[scrollbar-gutter:stable]',
            filtersOpen ? 'max-h-[104px] lg:h-0 lg:max-h-none lg:min-h-full' : 'h-10 max-h-10'
          )}
          aria-label="模块显示筛选"
        >
          {availableModules.length === 0 ? (
            <span className="text-muted-foreground self-center px-1 text-xs">等待日志数据...</span>
          ) : (
            availableModules.map(({ module, displayName }) => {
              const isHidden = hiddenModules.has(module)
              return (
                <button
                  key={module}
                  type="button"
                  aria-pressed={!isHidden}
                  aria-label={`${isHidden ? '显示' : '隐藏'} ${displayName}`}
                  title={`${isHidden ? '显示' : '隐藏'} ${displayName}（${module}）`}
                  onClick={() => toggleModule(module)}
                  className={cn(
                    'h-7 max-w-full border px-2 text-xs font-medium transition-colors',
                    'focus-visible:ring-ring focus-visible:ring-2 focus-visible:ring-offset-1 focus-visible:outline-none',
                    'active:translate-y-px',
                    !isHidden
                      ? 'border-primary/60 bg-primary/10 text-foreground hover:bg-primary/20'
                      : 'border-border/60 bg-muted/30 text-muted-foreground/45 hover:text-muted-foreground'
                  )}
                >
                  <span className="block max-w-40 truncate">{displayName}</span>
                </button>
              )
            })
          )}
        </div>

        {/* 控制按钮与搜索栏 */}
        <div className="flex min-w-0 flex-col gap-2">
          {/* 第一行：搜索、滚动、清空、导出、筛选、状态 */}
          <div className="flex w-full flex-wrap items-center gap-1.5 lg:justify-end">
            {/* 搜索框 */}
            <div className="relative min-w-[180px] flex-1 lg:max-w-64">
              <Search className="text-muted-foreground absolute top-1/2 left-2 h-3.5 w-3.5 -translate-y-1/2" />
              <Input
                placeholder="搜索日志..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                onKeyDown={handleKeyDown}
                className="h-8 pr-8 pl-8 text-xs sm:text-sm"
              />
              {searchQuery && (
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  onClick={() => setSearchQuery('')}
                  className="absolute top-1/2 right-0.5 h-7 w-7 -translate-y-1/2"
                  title="清空搜索"
                  aria-label="清空搜索"
                >
                  <X className="h-3.5 w-3.5" />
                </Button>
              )}
            </div>

            {/* 自动滚动切换 */}
            <Button
              variant={autoScroll ? 'default' : 'outline'}
              size="sm"
              onClick={handleToggleAutoScroll}
              className="h-8 px-2"
              title={autoScroll ? '自动滚动' : '已暂停'}
            >
              {autoScroll ? <Play className="h-3.5 w-3.5" /> : <Pause className="h-3.5 w-3.5" />}
              <span className="ml-1 text-xs">{autoScroll ? '滚动' : '暂停'}</span>
            </Button>

            {/* 清空日志 */}
            <Button
              variant="outline"
              size="sm"
              onClick={handleClear}
              disabled={logs.length === 0}
              className="h-8 px-2"
              title="清空日志"
            >
              <Trash2 className="h-3.5 w-3.5" />
              <span className="ml-1 text-xs">清空</span>
            </Button>

            {/* 导出日志 */}
            <Button
              variant="outline"
              size="sm"
              onClick={handleExport}
              disabled={filteredLogs.length === 0}
              className="h-8 px-2"
              title="导出日志"
            >
              <Download className="h-3.5 w-3.5" />
              <span className="ml-1 text-xs">导出</span>
            </Button>

            {/* 筛选折叠切换 */}
            <CollapsibleTrigger asChild>
              <Button
                variant="outline"
                size="sm"
                className="h-8 px-2"
                title={filtersOpen ? '收起筛选' : '展开筛选'}
              >
                <Filter className="h-3.5 w-3.5" />
                <span className="ml-1 text-xs">筛选</span>
                {filtersOpen ? (
                  <ChevronUp className="ml-1 h-3.5 w-3.5" />
                ) : (
                  <ChevronDown className="ml-1 h-3.5 w-3.5" />
                )}
              </Button>
            </CollapsibleTrigger>

            {/* 连接状态与日志计数 */}
            <div className="text-muted-foreground ml-auto flex items-center gap-2 text-xs whitespace-nowrap lg:ml-1">
              <span className="flex items-center gap-1.5">
                <span
                  className={cn(
                    'h-2 w-2 rounded-full',
                    connected ? 'animate-pulse bg-green-500' : 'bg-red-500'
                  )}
                />
                {connected ? '已连接' : '未连接'}
              </span>
              <span>
                <span className="font-mono">
                  {filteredLogs.length} / {logs.length}
                </span>
                <span className="ml-1">条日志</span>
              </span>
            </div>
          </div>

          {/* 第二行：高级筛选（级别、日期、字号、间距） */}
          <CollapsibleContent className="w-full space-y-2">
            {/* 级别筛选 */}
            <div className="flex flex-col gap-2 sm:flex-row sm:gap-2">
              <Select
                value={levelFilter}
                onValueChange={(val) => handleLevelFilterChange(val as LogLevelFilter)}
              >
                <SelectTrigger className="h-8 w-full text-xs sm:flex-1">
                  <Filter className="mr-1.5 h-3.5 w-3.5" />
                  <SelectValue placeholder="最低级别" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">全部级别</SelectItem>
                  <SelectItem value="DEBUG">DEBUG 及以上</SelectItem>
                  <SelectItem value="INFO">INFO 及以上</SelectItem>
                  <SelectItem value="WARNING">WARNING 及以上</SelectItem>
                  <SelectItem value="ERROR">ERROR 及以上</SelectItem>
                  <SelectItem value="CRITICAL">CRITICAL</SelectItem>
                </SelectContent>
              </Select>

              {/* 重置筛选 */}
              <Button
                variant="outline"
                size="sm"
                onClick={handleResetFilters}
                className="h-8 w-full sm:w-auto"
                title="重置筛选"
              >
                <X className="h-3.5 w-3.5 sm:mr-1" />
                <span className="text-xs">重置</span>
              </Button>
            </div>

            {/* 日期范围 */}
            <div className="flex flex-col gap-2 sm:flex-row sm:gap-2">
              {/* 开始日期 */}
              <Popover>
                <PopoverTrigger asChild>
                  <Button
                    variant="outline"
                    size="sm"
                    className={cn(
                      'h-8 w-full justify-start text-left font-normal sm:flex-1',
                      !dateFrom && 'text-muted-foreground'
                    )}
                  >
                    <CalendarIcon className="mr-1.5 h-3.5 w-3.5" />
                    <span className="text-xs">
                      {dateFrom ? format(dateFrom, 'PP', { locale: zhCN }) : '开始日期'}
                    </span>
                  </Button>
                </PopoverTrigger>
                <PopoverContent className="w-auto p-0" align="start">
                  <Calendar
                    mode="single"
                    selected={dateFrom}
                    onSelect={setDateFrom}
                    initialFocus
                    locale={zhCN}
                  />
                </PopoverContent>
              </Popover>

              {/* 结束日期 */}
              <Popover>
                <PopoverTrigger asChild>
                  <Button
                    variant="outline"
                    size="sm"
                    className={cn(
                      'h-8 w-full justify-start text-left font-normal sm:flex-1',
                      !dateTo && 'text-muted-foreground'
                    )}
                  >
                    <CalendarIcon className="mr-1.5 h-3.5 w-3.5" />
                    <span className="text-xs">
                      {dateTo ? format(dateTo, 'PP', { locale: zhCN }) : '结束日期'}
                    </span>
                  </Button>
                </PopoverTrigger>
                <PopoverContent className="w-auto p-0" align="start">
                  <Calendar
                    mode="single"
                    selected={dateTo}
                    onSelect={setDateTo}
                    initialFocus
                    locale={zhCN}
                  />
                </PopoverContent>
              </Popover>

              {(dateFrom || dateTo) && (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    setDateFrom(undefined)
                    setDateTo(undefined)
                  }}
                  className="h-8 w-full sm:w-auto"
                >
                  <X className="h-3.5 w-3.5 sm:mr-1" />
                  <span className="text-xs">清除</span>
                </Button>
              )}
            </div>

            {/* 字号选择、行间距与列宽微调 */}
            <div className="border-border/50 flex flex-col gap-2 border-t pt-2 sm:flex-row sm:items-center sm:gap-3">
              {/* 字号 */}
              <div className="flex items-center gap-2">
                <div className="text-muted-foreground flex items-center gap-1.5 text-xs">
                  <Type className="h-3.5 w-3.5" />
                  <span>字号</span>
                </div>
                <div className="flex gap-1">
                  {(Object.keys(fontSizeConfig) as FontSize[]).map((key) => (
                    <Button
                      key={key}
                      variant={fontSize === key ? 'default' : 'outline'}
                      size="sm"
                      onClick={() => handleFontSizeChange(key)}
                      className="h-6 px-2 text-xs"
                    >
                      {fontSizeConfig[key].label}
                    </Button>
                  ))}
                </div>
              </div>

              {/* 行间距 */}
              <div className="flex max-w-[200px] flex-1 items-center gap-2">
                <span className="text-muted-foreground text-xs whitespace-nowrap">行距</span>
                <Slider
                  value={[lineSpacing]}
                  onValueChange={handleLineSpacingChange}
                  min={LINE_SPACING_MIN}
                  max={LINE_SPACING_MAX}
                  step={2}
                  className="flex-1"
                />
                <span className="text-muted-foreground w-7 text-xs">{lineSpacing}px</span>
              </div>

              {/* 列宽 */}
              <div className="flex max-w-[220px] flex-1 items-center gap-2">
                <span className="text-muted-foreground text-xs whitespace-nowrap">列宽</span>
                <Slider
                  value={[columnWidthExtra]}
                  onValueChange={handleColumnWidthExtraChange}
                  min={COLUMN_WIDTH_EXTRA_MIN}
                  max={COLUMN_WIDTH_EXTRA_MAX}
                  step={8}
                  className="flex-1"
                />
                <span className="text-muted-foreground w-9 text-xs">+{columnWidthExtra}</span>
              </div>
            </div>
          </CollapsibleContent>
        </div>
      </div>
    </Collapsible>
  )

  const toolbarPortal =
    toolbarVisible && toolbarRoot ? createPortal(toolbarContent, toolbarRoot) : null

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden">
      {toolbarPortal}

      {/* 日志终端 - 占据剩余所有空间 */}
      <div className="min-h-[260px] flex-1 px-2 pb-2 sm:px-3 sm:pb-3 lg:px-4 lg:pt-2 lg:pb-4">
        <Card
          className="h-full overflow-hidden border-[#24170f]/70 dark:border-[#1d120c]/80"
          style={{ backgroundColor: '#633312' }}
        >
          {/* 日志列表卡片内容 */}
          <div
            ref={parentRef}
            className={cn(
              'h-full overflow-auto selection:bg-[#5a3924] selection:text-[#fff2df]',
              '[&::-webkit-scrollbar]:w-2.5',
              '[&::-webkit-scrollbar-track]:bg-transparent',
              '[&::-webkit-scrollbar-thumb]:bg-border [&::-webkit-scrollbar-thumb]:rounded-full',
              '[&::-webkit-scrollbar-thumb:hover]:bg-border/80'
            )}
            style={{ backgroundColor: '#211607' }}
          >
            <div
              className={cn(
                'relative p-2 font-mono selection:bg-[#5a3924] selection:text-[#fff2df] sm:p-3',
                fontSizeConfig[fontSize].class
              )}
              style={{
                height: `${rowVirtualizer.getTotalSize()}px`,
                minHeight: '100%',
              }}
            >
              {filteredLogs.length === 0 ? (
                <div className="py-8 text-center text-xs text-gray-500 sm:text-sm dark:text-gray-600">
                  暂无日志数据
                </div>
              ) : (
                rowVirtualizer.getVirtualItems().map((virtualRow) => {
                  const log = filteredLogs[virtualRow.index]
                  const formattedTimestamp = formatLogTimestamp(log.timestamp)
                  const formattedLevel = formatLogLevel(log.level)
                  const moduleStyle = getModuleTextStyle(log)

                  return (
                    <div
                      key={virtualRow.key}
                      data-index={virtualRow.index}
                      ref={rowVirtualizer.measureElement}
                      className="absolute top-0 left-0 w-full px-2 sm:px-3"
                      style={{
                        transform: `translateY(${virtualRow.start}px)`,
                        paddingTop: `${lineSpacing / 2}px`,
                        paddingBottom: `${lineSpacing / 2}px`,
                      }}
                    >
                      {/* 移动端紧凑排版 (< sm) */}
                      <div className="flex flex-col gap-0.5 sm:hidden">
                        <div className="flex items-center gap-2">
                          <span className="text-[10px] text-gray-500 dark:text-gray-600">
                            {formattedTimestamp}
                          </span>
                          <span
                            className={cn('text-[10px] font-semibold', {
                              'text-blue-400 dark:text-blue-500': log.level === 'DEBUG',
                              'text-green-400 dark:text-green-500': log.level === 'INFO',
                              'text-yellow-400 dark:text-yellow-500': log.level === 'WARNING',
                              'text-red-400 dark:text-red-500': log.level === 'ERROR',
                              'text-purple-400 dark:text-purple-500': log.level === 'CRITICAL',
                            })}
                          >
                            [{formattedLevel}]
                          </span>
                        </div>
                        <div
                          className={cn(
                            'truncate text-[10px]',
                            !moduleStyle && 'text-cyan-400 dark:text-cyan-500'
                          )}
                          style={moduleStyle}
                        >
                          {getModuleDisplayName(log)}
                        </div>
                        <div
                          className={cn(
                            'text-[10px] break-words whitespace-pre-wrap',
                            !moduleStyle && 'text-gray-300 dark:text-gray-400'
                          )}
                          style={moduleStyle}
                        >
                          {log.message}
                        </div>
                      </div>

                      {/* 宽屏桌面端并排排版 (>= sm) */}
                      <div className={cn('hidden items-start sm:flex', currentLayout.gapClass)}>
                        <span
                          className={cn(
                            'flex-shrink-0 text-gray-500 dark:text-gray-600',
                            currentLayout.timestampClass
                          )}
                          style={{ width: dynamicTimestampWidth }}
                        >
                          {formattedTimestamp}
                        </span>
                        <span
                          className={cn(
                            'flex-shrink-0 font-semibold',
                            currentLayout.levelClass,
                            {
                              'text-blue-400 dark:text-blue-500': log.level === 'DEBUG',
                              'text-green-400 dark:text-green-500': log.level === 'INFO',
                              'text-yellow-400 dark:text-yellow-500': log.level === 'WARNING',
                              'text-red-400 dark:text-red-500': log.level === 'ERROR',
                              'text-purple-400 dark:text-purple-500': log.level === 'CRITICAL',
                            }
                          )}
                          style={{ width: dynamicLevelWidth }}
                        >
                          [{formattedLevel}]
                        </span>
                        <span
                          className={cn(
                            'flex-shrink-0 truncate',
                            currentLayout.moduleClass,
                            !moduleStyle && 'text-cyan-400 dark:text-cyan-500'
                          )}
                          style={{ ...moduleStyle, width: dynamicModuleWidth }}
                        >
                          {getModuleDisplayName(log)}
                        </span>
                        <span
                          className={cn(
                            'flex-1 break-words whitespace-pre-wrap',
                            !moduleStyle && 'text-gray-300 dark:text-gray-400'
                          )}
                          style={moduleStyle}
                        >
                          {log.message}
                        </span>
                      </div>
                    </div>
                  )
                })
              )}
            </div>
          </div>
        </Card>
      </div>
    </div>
  )
}

interface LogViewerPageProps {
  defaultTab?: LogViewerTab
}

export function LogViewerPage({ defaultTab }: LogViewerPageProps) {
  const [activeTab, setActiveTab] = useState<LogViewerTab>(() => defaultTab ?? loadStoredLogViewerTab())
  const [topbarTabsRoot, setTopbarTabsRoot] = useState<HTMLElement | null>(null)
  const [topbarTabsCompact, setTopbarTabsCompact] = useState(false)
  const topbarTabsCompactRef = useRef(false)
  const [reasoningToolbarVisible, setReasoningToolbarVisible] = useState(activeTab === 'reasoning')
  const [showSwitchHint, setShowSwitchHint] = useState(() => {
    if (typeof window === 'undefined') return false
    return localStorage.getItem(LOG_VIEWER_SWITCH_HINT_DISMISSED_KEY) !== 'true'
  })

  const toolbarContainerId = 'log-terminal-toolbar'
  const topbarTabsContainerId = 'log-viewer-topbar-tabs'
  const reasoningTopbarActionsContainerId = 'reasoning-topbar-actions'

  useEffect(() => {
    const frameId = requestAnimationFrame(() => {
      setTopbarTabsRoot(document.getElementById(topbarTabsContainerId))
    })
    return () => cancelAnimationFrame(frameId)
  }, [topbarTabsContainerId])

  useEffect(() => {
    topbarTabsCompactRef.current = topbarTabsCompact
  }, [topbarTabsCompact])

  useEffect(() => {
    localStorage.setItem(LOG_VIEWER_ACTIVE_TAB_KEY, activeTab)
  }, [activeTab])

  useEffect(() => {
    if (!topbarTabsRoot) return

    let frameId = 0
    const updateCompactState = () => {
      cancelAnimationFrame(frameId)
      frameId = requestAnimationFrame(() => {
        const workspaceTabs = document.querySelector('[data-dashboard-workspace-tabs="true"]')
        const measureWorkspaceTabs = document.querySelector(
          '[data-dashboard-workspace-tabs-measure="true"]'
        )
        const switcherMeasure = topbarTabsRoot.querySelector(
          '[data-log-viewer-switcher-measure="true"]'
        )

        if (
          !(workspaceTabs instanceof HTMLElement) ||
          !(measureWorkspaceTabs instanceof HTMLElement) ||
          !(switcherMeasure instanceof HTMLElement)
        ) {
          setTopbarTabsCompact(false)
          return
        }

        const topbarRect = topbarTabsRoot.getBoundingClientRect()
        const switcherRect = switcherMeasure.getBoundingClientRect()
        const workspaceRect = workspaceTabs.getBoundingClientRect()
        const measureTabsRect = measureWorkspaceTabs.getBoundingClientRect()

        const availableSpace =
          workspaceRect.right - measureTabsRect.width - (topbarRect.left + switcherRect.width)
        const threshold = topbarTabsCompactRef.current
          ? TOPBAR_SWITCH_EXPAND_GAP
          : TOPBAR_SWITCH_COMPACT_GAP

        setTopbarTabsCompact(availableSpace < threshold)
      })
    }

    updateCompactState()
    window.addEventListener('resize', updateCompactState)

    const resizeObserver = new ResizeObserver(updateCompactState)
    resizeObserver.observe(document.body)
    resizeObserver.observe(topbarTabsRoot)

    const workspaceTabs = document.querySelector('[data-dashboard-workspace-tabs="true"]')
    if (workspaceTabs instanceof HTMLElement) {
      resizeObserver.observe(workspaceTabs)
    }
    const measureWorkspaceTabs = document.querySelector(
      '[data-dashboard-workspace-tabs-measure="true"]'
    )
    if (measureWorkspaceTabs instanceof HTMLElement) {
      resizeObserver.observe(measureWorkspaceTabs)
    }

    return () => {
      cancelAnimationFrame(frameId)
      window.removeEventListener('resize', updateCompactState)
      resizeObserver.disconnect()
    }
  }, [activeTab, reasoningToolbarVisible, topbarTabsRoot])

  const renderMeasureTabSwitcher = () => {
    const showReasoningActionsPlaceholder = activeTab === 'reasoning' && !reasoningToolbarVisible

    return (
      <div
        data-log-viewer-switcher-measure="true"
        aria-hidden="true"
        className="pointer-events-none invisible absolute top-0 left-0 flex min-w-0 items-center gap-2"
      >
        <div className="bg-muted text-muted-foreground inline-flex h-9 items-center justify-center rounded-lg p-1">
          <div className="inline-flex items-center justify-center gap-1.5 rounded-md px-3 py-1 text-sm font-medium whitespace-nowrap">
            <Terminal className="h-4 w-4" />
            <span>终端</span>
          </div>
          <div className="inline-flex items-center justify-center gap-1.5 rounded-md px-3 py-1 text-sm font-medium whitespace-nowrap">
            <BrainCircuit className="h-4 w-4" />
            <span>推理过程</span>
          </div>
          <div className="inline-flex items-center justify-center gap-1.5 rounded-md px-3 py-1 text-sm font-medium whitespace-nowrap">
            <BarChart3 className="h-4 w-4" />
            <span>详细统计</span>
          </div>
        </div>
        {showReasoningActionsPlaceholder && <div className="h-9 w-9" />}
      </div>
    )
  }

  const renderTabSwitcher = (includeTopbarActions = false, compact = false) => {
    const labelClass = includeTopbarActions && compact ? 'sr-only' : undefined

    return (
      <div className="flex min-w-0 items-center gap-2">
        <TabsList
          data-log-viewer-switcher={includeTopbarActions ? 'true' : undefined}
          data-log-viewer-switcher-compact={includeTopbarActions && compact ? 'true' : undefined}
        >
          <TabsTrigger value="terminal" className="gap-1.5" aria-label="终端">
            <Terminal className="h-4 w-4" />
            <span className={labelClass}>终端</span>
          </TabsTrigger>
          <TabsTrigger value="reasoning" className="gap-1.5" aria-label="推理过程">
            <BrainCircuit className="h-4 w-4" />
            <span className={labelClass}>推理过程</span>
          </TabsTrigger>
          <TabsTrigger value="statistics" className="gap-1.5" aria-label="详细统计">
            <BarChart3 className="h-4 w-4" />
            <span className={labelClass}>详细统计</span>
          </TabsTrigger>
        </TabsList>
        {includeTopbarActions && (
          <div id={reasoningTopbarActionsContainerId} className="hidden items-center sm:flex" />
        )}
      </div>
    )
  }

  const topbarTabsPortal = topbarTabsRoot
    ? createPortal(
        <>
          {renderTabSwitcher(true, topbarTabsCompact)}
          {renderMeasureTabSwitcher()}
        </>,
        topbarTabsRoot
      )
    : null

  const dismissSwitchHint = () => {
    localStorage.setItem(LOG_VIEWER_SWITCH_HINT_DISMISSED_KEY, 'true')
    setShowSwitchHint(false)
  }

  return (
    <Tabs
      value={activeTab}
      onValueChange={(value) => setActiveTab(value as LogViewerTab)}
      className="flex h-full min-h-0 flex-col overflow-hidden"
    >
      {topbarTabsPortal}
      <div
        className={cn(
          'flex shrink-0 flex-col sm:flex-row items-stretch sm:items-center justify-between gap-2 border-b px-3 py-1.5 lg:px-4',
          ((activeTab === 'reasoning' && !reasoningToolbarVisible) || activeTab === 'statistics') &&
            'sm:hidden'
        )}
      >
        {/* 移动端下 Tab 独占置顶一行，不再和右侧面板同层并排 */}
        <div className="sm:hidden flex justify-start">{renderTabSwitcher()}</div>
        {/* 移动端宽度占满 100%，桌面端恢复原状 */}
        <div id={toolbarContainerId} className="flex min-w-0 w-full sm:w-auto sm:flex-1 justify-start sm:justify-end" />
      </div>
      {showSwitchHint && (
        <div className="shrink-0 border-b px-3 py-2 lg:px-4">
          <div className="border-primary/30 bg-primary/5 flex items-start gap-2 rounded-md border px-3 py-2 text-sm">
            <div className="flex min-w-0 flex-1 flex-wrap items-center gap-x-2 gap-y-1">
              <span className="text-foreground font-medium">小提示</span>
              <span className="text-muted-foreground">
                可以在左上角切换「终端」「推理过程」和「详细统计」。
              </span>
            </div>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="h-6 w-6 shrink-0"
              onClick={dismissSwitchHint}
              title="关闭提示"
              aria-label="关闭提示"
            >
              <X className="h-3.5 w-3.5" />
            </Button>
          </div>
        </div>
      )}
      <TabsContent value="terminal" className="m-0 min-h-0 flex-1 overflow-hidden">
        <LogTerminalPane
          toolbarContainerId={toolbarContainerId}
          toolbarVisible={activeTab === 'terminal'}
        />
      </TabsContent>
      <TabsContent value="reasoning" className="m-0 min-h-0 flex-1 overflow-hidden p-2 lg:p-4">
        <ReasoningProcessPage
          embedded
          toolbarContainerId={toolbarContainerId}
          toolbarVisible={activeTab === 'reasoning'}
          topbarActionsContainerId={reasoningTopbarActionsContainerId}
          onToolbarContentVisibleChange={setReasoningToolbarVisible}
        />
      </TabsContent>
      <TabsContent value="statistics" className="m-0 min-h-0 flex-1 overflow-y-auto">
        <Suspense
          fallback={
            <div className="text-muted-foreground flex h-full items-center justify-center text-sm">
              正在加载详细统计…
            </div>
          }
        >
          <StatisticsPage />
        </Suspense>
      </TabsContent>
    </Tabs>
  )
}

export function ReasoningLogViewerPage() {
  return <LogViewerPage defaultTab="reasoning" />
}

export function StatisticsLogViewerPage() {
  return <LogViewerPage defaultTab="statistics" />
}
