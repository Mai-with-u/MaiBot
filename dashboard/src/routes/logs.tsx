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
  })
  const [lineSpacing, setLineSpacing] = useState<number>(() => {
    const saved = getSetting('logLineSpacing')
    return typeof saved === 'number' ? clampNumber(saved, LINE_SPACING_MIN, LINE_SPACING_MAX) : 4
  })
  const [columnWidthExtra, setColumnWidthExtra] = useState<number>(() => {
    const saved = getSetting('logColumnWidthExtra')
    return typeof saved === 'number'
      ? clampNumber(saved, COLUMN_WIDTH_EXTRA_MIN, COLUMN_WIDTH_EXTRA_MAX)
      : 0
  })
  const [hiddenModules, setHiddenModules] = useState<Set<string>>(() => {
    try {
      const stored = localStorage.getItem('log-hidden-modules')
      return stored ? new Set(JSON.parse(stored)) : new Set()
    } catch {
      return new Set()
    }
  })
  const [filtersOpen, setFiltersOpen] = useState<boolean>(() =>
    Boolean(getSetting('logFiltersOpen', false))
  )
  const [toolbarRoot, setToolbarRoot] = useState<HTMLElement | null>(null)

  const parentRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    setToolbarRoot(document.getElementById(toolbarContainerId))
  }, [toolbarContainerId])

  useEffect(() => {
    setStoredModuleFilter(getSetting('logModuleFilter'))
  }, [])

  // 保存隐藏的模块列表到 localStorage
  const saveHiddenModules = useCallback((modules: Set<string>) => {
    setHiddenModules(modules)
    try {
      localStorage.setItem('log-hidden-modules', JSON.stringify([...modules]))
    } catch {
      // 忽略保存错误
    }
  }, [])

  // 切换模块显示状态
  const toggleModuleVisibility = useCallback(
    (moduleId: string) => {
      const newHidden = new Set(hiddenModules)
      if (newHidden.has(moduleId)) {
        newHidden.delete(moduleId)
      } else {
        newHidden.add(moduleId)
      }
      saveHiddenModules(newHidden)
    },
    [hiddenModules, saveHiddenModules]
  )

  // 提取所有不重复的模块列表，附带展示名（优先取最新出现的展示名）
  const uniqueModules = useMemo(() => {
    const moduleMap = new Map<string, string>()
    logs.forEach((log) => {
      moduleMap.set(log.module, getModuleDisplayName(log))
    })
    return Array.from(moduleMap.entries()).map(([id, displayName]) => ({ id, displayName }))
  }, [logs])

  // 导出日志
  const exportLogs = useCallback(() => {
    const text = logs
      .map((log) => `[${log.timestamp}] [${log.level}] [${log.module}] ${log.message}`)
      .join('\n')
    const blob = new Blob([text], { type: 'text/plain;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `maibot-logs-${format(new Date(), 'yyyyMMdd-HHmmss')}.log`
    a.click()
    URL.revokeObjectURL(url)
  }, [logs])

  // 清空日志
  const clearLogs = useCallback(() => {
    setLogs([])
  }, [])

  // 订阅 WebSocket 日志
  useEffect(() => {
    const unsubscribeLogs = logWebSocket.subscribe((newLog) => {
      setLogs((prev) => [...prev, newLog])
    })

    const unsubscribeStatus = logWebSocket.subscribeStatus((status) => {
      setConnected(status)
    })

    // 连接 WebSocket
    logWebSocket.connect()

    return () => {
      unsubscribeLogs()
      unsubscribeStatus()
    }
  }, [])

  // 快速过滤级别
  const handleLevelFilterChange = useCallback((value: string) => {
    const filter = value as LogLevelFilter
    setLevelFilter(filter)
    setSetting('logLevelFilter', filter)
  }, [])

  const handleFiltersOpenChange = useCallback((open: boolean) => {
    setFiltersOpen(open)
    setSetting('logFiltersOpen', open)
  }, [])

  const handleFontSizeChange = (size: FontSize) => {
    setFontSize(size)
    setSetting('logFontSize', size)
  }

  const handleLineSpacingChange = ([value]: number[]) => {
    const nextValue = clampNumber(value, LINE_SPACING_MIN, LINE_SPACING_MAX)
    setLineSpacing(nextValue)
    setSetting('logLineSpacing', nextValue)
  }

  const handleColumnWidthExtraChange = ([value]: number[]) => {
    const nextValue = clampNumber(value, COLUMN_WIDTH_EXTRA_MIN, COLUMN_WIDTH_EXTRA_MAX)
    setColumnWidthExtra(nextValue)
    setSetting('logColumnWidthExtra', nextValue)
  }

  const handleSearchKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'Escape' && searchQuery) {
      event.preventDefault()
      setSearchQuery('')
    }
  }

  // 清除时间筛选
  const clearDateFilter = () => {
    setDateFrom(undefined)
    setDateTo(undefined)
  }

  const resetFilters = () => {
    setSearchQuery('')
    setDateFrom(undefined)
    setDateTo(undefined)
    handleLevelFilterChange('INFO')
    saveHiddenModules(new Set())
  }

  // 过滤日志
  const filteredLogs = useMemo(() => {
    return logs.filter((log) => {
      // 搜索过滤
      const matchesSearch =
        searchQuery === '' ||
        log.message.toLowerCase().includes(searchQuery.toLowerCase()) ||
        log.module.toLowerCase().includes(searchQuery.toLowerCase()) ||
        getModuleDisplayName(log).toLowerCase().includes(searchQuery.toLowerCase())

      // 级别过滤：选择某个级别时显示该级别及以上的日志
      const matchesLevel =
        levelFilter === 'all' || levelPriority[log.level] >= levelPriority[levelFilter]

      // 模块过滤
      const matchesModule = !hiddenModules.has(log.module)

      // 时间过滤
      let matchesDate = true
      if (dateFrom || dateTo) {
        const logDate = new Date(log.timestamp)
        if (dateFrom && logDate < dateFrom) matchesDate = false
        if (dateTo) {
          const endOfDay = new Date(dateTo)
          endOfDay.setHours(23, 59, 59, 999)
          if (logDate > endOfDay) matchesDate = false
        }
      }

      return matchesSearch && matchesLevel && matchesModule && matchesDate
    })
  }, [logs, searchQuery, levelFilter, hiddenModules, dateFrom, dateTo])

  // 虚拟滚动配置 - 根据字号和行间距动态计算行高
  const estimatedRowHeight = fontSizeConfig[fontSize].rowHeight + lineSpacing
  const logColumnLayout = logColumnLayoutConfig[fontSize]
  const timestampWidth = logColumnLayout.timestampWidth + columnWidthExtra
  const levelWidth = logColumnLayout.levelWidth + Math.round(columnWidthExtra * 0.5)
  const moduleWidth = logColumnLayout.moduleWidth + columnWidthExtra

  // TanStack Virtual 与 React Compiler 不兼容，保持现有虚拟列表实现
  // eslint-disable-next-line react-hooks/incompatible-library
  const rowVirtualizer = useVirtualizer({
    count: filteredLogs.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => estimatedRowHeight,
    overscan: 50, // 增加预渲染数量以减少快速滚动时的空白
  })

  // 用于追踪是否是程序触发的滚动
  const isAutoScrollingRef = useRef(false)
  // 用于追踪上一次的日志数量
  const prevLogCountRef = useRef(filteredLogs.length)

  // 检测用户滚动行为，当用户向上滚动时禁用自动滚动
  useEffect(() => {
    const scrollElement = parentRef.current
    if (!scrollElement) return

    const handleScroll = () => {
      // 如果是程序触发的滚动，忽略
      if (isAutoScrollingRef.current) return

      const { scrollTop, scrollHeight, clientHeight } = scrollElement
      const distanceFromBottom = scrollHeight - scrollTop - clientHeight

      // 如果距离底部超过 100px，说明用户在向上查看，禁用自动滚动
      if (distanceFromBottom > 100 && autoScroll) {
        setAutoScroll(false)
      }
      // 如果用户滚动到接近底部（小于 50px），可以重新启用自动滚动
      else if (distanceFromBottom < 50 && !autoScroll) {
        setAutoScroll(true)
      }
    }

    scrollElement.addEventListener('scroll', handleScroll, { passive: true })
    return () => scrollElement.removeEventListener('scroll', handleScroll)
  }, [autoScroll])

  // 自动滚动到底部
  useEffect(() => {
    // 只有在日志数量增加时才滚动（避免删除日志时触发）
    const logCountIncreased = filteredLogs.length > prevLogCountRef.current
    prevLogCountRef.current = filteredLogs.length

    if (autoScroll && filteredLogs.length > 0 && logCountIncreased) {
      isAutoScrollingRef.current = true
      rowVirtualizer.scrollToIndex(filteredLogs.length - 1, {
        align: 'end',
        behavior: 'auto',
      })
      // 稍后重置标志，给滚动事件处理一些时间
      requestAnimationFrame(() => {
        requestAnimationFrame(() => {
          isAutoScrollingRef.current = false
        })
      })
    }
  }, [filteredLogs.length, autoScroll, rowVirtualizer])

  // 工具栏内容
  const toolbarContent = (
    <Collapsible open={filtersOpen} onOpenChange={handleFiltersOpenChange}>
      <div className="grid w-full gap-2 lg:grid-cols-[minmax(0,1fr)_minmax(620px,760px)]">
        <div
          className={cn(
            'flex min-h-9 min-w-0 flex-wrap content-start items-start gap-1.5',
            'overflow-y-auto pr-1',
            'max-h-24 sm:max-h-28 lg:max-h-32'
          )}
          role="group"
          aria-label="模块显示筛选"
        >
          {uniqueModules.map(({ id, displayName }) => {
            const visible = !hiddenModules.has(id)
            return (
              <button
                key={id}
                type="button"
                aria-pressed={visible}
                aria-label={`${visible ? '隐藏' : '显示'} ${displayName}`}
                title={`${visible ? '隐藏' : '显示'} ${displayName}（${id}）`}
                onClick={() => toggleModuleVisibility(id)}
                className={cn(
                  'h-7 max-w-full border px-2 text-xs font-medium transition-colors',
                  'focus-visible:ring-ring focus-visible:ring-2 focus-visible:ring-offset-1 focus-visible:outline-none',
                  'active:translate-y-px',
                  visible
                    ? 'border-primary/60 bg-primary/10 text-foreground hover:bg-primary/20'
                    : 'border-border/60 bg-muted/30 text-muted-foreground/45 hover:text-muted-foreground'
                )}
              >
                <span className="truncate">{displayName}</span>
              </button>
            )
          })}
        </div>

        <div className="flex min-w-0 flex-col gap-2">
          <div className="flex w-full flex-wrap items-center gap-1.5 lg:justify-end">
            <div className="relative min-w-[180px] flex-1 lg:max-w-64">
              <Search className="text-muted-foreground absolute top-1/2 left-2 h-3.5 w-3.5 -translate-y-1/2" />
              <Input
                placeholder="搜索日志… (Esc 清除)"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                onKeyDown={handleSearchKeyDown}
                className="h-8 pl-8 text-xs"
              />
              {searchQuery && (
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setSearchQuery('')}
                  className="text-muted-foreground hover:text-foreground absolute top-1/2 right-1 h-6 w-6 -translate-y-1/2 p-0"
                  aria-label="清除搜索"
                >
                  <X className="h-3.5 w-3.5" />
                </Button>
              )}
            </div>

            <Button
              variant={autoScroll ? 'default' : 'outline'}
              size="sm"
              onClick={() => setAutoScroll(!autoScroll)}
              className="h-8 px-2"
              title={autoScroll ? '自动滚动' : '已暂停'}
            >
              {autoScroll ? <Pause className="h-3.5 w-3.5" /> : <Play className="h-3.5 w-3.5" />}
              <span className="ml-1 text-xs">{autoScroll ? '滚动' : '暂停'}</span>
            </Button>

            <Button
              variant="outline"
              size="sm"
              onClick={clearLogs}
              className="h-8 px-2"
              title="清空当前日志显示"
            >
              <Trash2 className="h-3.5 w-3.5" />
              <span className="ml-1 text-xs">清空</span>
            </Button>

            <Button
              variant="outline"
              size="sm"
              onClick={exportLogs}
              className="h-8 px-2"
              title="导出当前日志为文本文件"
            >
              <Download className="h-3.5 w-3.5" />
              <span className="ml-1 text-xs">导出</span>
            </Button>

            <CollapsibleTrigger asChild>
              <Button
                variant="outline"
                size="sm"
                className="h-8 px-2"
                title={filtersOpen ? '收起高级筛选' : '展开高级筛选'}
              >
                <Filter className="h-3.5 w-3.5" />
                <span className="ml-1 text-xs">筛选</span>
                {filtersOpen ? (
                  <ChevronUp className="ml-0.5 h-3.5 w-3.5" />
                ) : (
                  <ChevronDown className="ml-0.5 h-3.5 w-3.5" />
                )}
              </Button>
            </CollapsibleTrigger>

            <div className="text-muted-foreground ml-auto flex items-center gap-2 text-xs whitespace-nowrap lg:ml-1">
              <span className="flex items-center gap-1.5">
                <span
                  className={cn(
                    'h-2 w-2 rounded-full',
                    connected ? 'animate-pulse bg-green-500' : 'bg-red-500'
                  )}
                />
                <span className="hidden sm:inline">{connected ? '已连接' : '未连接'}</span>
              </span>
              <span>
                {filteredLogs.length}/{logs.length}
              </span>
            </div>
          </div>

          <CollapsibleContent className="w-full space-y-2">
            {/* 级别筛选 */}
            <div className="flex flex-col gap-2 sm:flex-row sm:gap-2">
              <Select value={levelFilter} onValueChange={handleLevelFilterChange}>
                <SelectTrigger className="h-8 w-full text-xs sm:w-28">
                  <SelectValue placeholder="日志级别" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">全部级别</SelectItem>
                  <SelectItem value="DEBUG">DEBUG 及以上</SelectItem>
                  <SelectItem value="INFO">INFO 及以上</SelectItem>
                  <SelectItem value="WARNING">WARN 及以上</SelectItem>
                  <SelectItem value="ERROR">ERROR 及以上</SelectItem>
                  <SelectItem value="CRITICAL">CRITICAL</SelectItem>
                </SelectContent>
              </Select>

              <Button
                variant="outline"
                size="sm"
                onClick={resetFilters}
                className="h-8 w-full sm:w-auto"
                title="重置筛选"
              >
                <X className="h-3.5 w-3.5 sm:mr-1" />
                <span className="text-xs">重置</span>
              </Button>
            </div>

            {/* 时间筛选 */}
            <div className="flex flex-col gap-2 sm:flex-row sm:gap-2">
              <Popover>
                <PopoverTrigger asChild>
                  <Button
                    variant="outline"
                    size="sm"
                    className={cn(
                      'h-8 w-full justify-start text-left text-xs font-normal sm:w-36',
                      !dateFrom && 'text-muted-foreground'
                    )}
                  >
                    <CalendarIcon className="mr-1.5 h-3.5 w-3.5" />
                    {dateFrom ? format(dateFrom, 'yyyy-MM-dd') : '起始日期'}
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

              <Popover>
                <PopoverTrigger asChild>
                  <Button
                    variant="outline"
                    size="sm"
                    className={cn(
                      'h-8 w-full justify-start text-left text-xs font-normal sm:w-36',
                      !dateTo && 'text-muted-foreground'
                    )}
                  >
                    <CalendarIcon className="mr-1.5 h-3.5 w-3.5" />
                    {dateTo ? format(dateTo, 'yyyy-MM-dd') : '结束日期'}
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
                  onClick={clearDateFilter}
                  className="h-8 w-full sm:w-auto"
                >
                  <X className="h-3.5 w-3.5 sm:mr-1" />
                  <span className="text-xs">清除日期</span>
                </Button>
              )}
            </div>

            {/* 显示设置 */}
            <div className="border-border/50 flex flex-col gap-2 border-t pt-2 sm:flex-row sm:items-center sm:gap-3">
              {/* 字号调整 */}
              <div className="flex items-center gap-2">
                <div className="text-muted-foreground flex items-center gap-1.5 text-xs">
                  <Type className="h-3.5 w-3.5" />
                  <span>字号</span>
                </div>
                <div className="flex items-center gap-1">
                  {(['xs', 'sm', 'base'] as const).map((size) => (
                    <Button
                      key={size}
                      variant={fontSize === size ? 'default' : 'outline'}
                      size="sm"
                      onClick={() => handleFontSizeChange(size)}
                      className="h-7 w-7 p-0 text-xs"
                    >
                      {fontSizeConfig[size].label}
                    </Button>
                  ))}
                </div>
              </div>

              {/* 行间距调整 */}
              <div className="flex max-w-[200px] flex-1 items-center gap-2">
                <span className="text-muted-foreground text-xs whitespace-nowrap">行距</span>
                <Slider
                  value={[lineSpacing]}
                  onValueChange={handleLineSpacingChange}
                  min={LINE_SPACING_MIN}
                  max={LINE_SPACING_MAX}
                  step={1}
                  className="flex-1"
                />
                <span className="text-muted-foreground w-7 text-xs">{lineSpacing}px</span>
              </div>

              {/* 列宽调整 */}
              <div className="flex max-w-[220px] flex-1 items-center gap-2">
                <span className="text-muted-foreground text-xs whitespace-nowrap">列宽</span>
                <Slider
                  value={[columnWidthExtra]}
                  onValueChange={handleColumnWidthExtraChange}
                  min={COLUMN_WIDTH_EXTRA_MIN}
                  max={COLUMN_WIDTH_EXTRA_MAX}
                  step={4}
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
    <div className="flex h-full flex-col overflow-hidden bg-background">
      {toolbarPortal}

      {/* 日志终端 - 占据剩余所有空间 */}
      <div className="min-h-[260px] flex-1 px-2 pb-2 sm:px-3 sm:pb-3 lg:px-4 lg:pt-2 lg:pb-4">
        <Card
          className="h-full overflow-hidden border-[#24170f]/70 dark:border-[#1d120c]/80"
          style={{ backgroundColor: '#633312' }}
        >
          <div
            ref={parentRef}
            className={cn(
              'h-full overflow-auto selection:bg-[#5a3924] selection:text-[#fff2df]',
              // 自定义滚动条样式
              '[&::-webkit-scrollbar]:w-2.5',
              '[&::-webkit-scrollbar-track]:bg-transparent',
              '[&::-webkit-scrollbar-thumb]:bg-border [&::-webkit-scrollbar-thumb]:rounded-full',
              '[&::-webkit-scrollbar-thumb:hover]:bg-muted-foreground/50'
            )}
          >
            <div
              style={{
                height: `${rowVirtualizer.getTotalSize()}px`,
                width: '100%',
                position: 'relative',
                fontFamily:
                  'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace',
              }}
            >
              {filteredLogs.length === 0 ? (
                <div className="text-muted-foreground flex h-32 items-center justify-center text-sm">
                  {logs.length === 0 ? '暂无日志' : '无匹配日志'}
                </div>
              ) : (
                rowVirtualizer.getVirtualItems().map((virtualRow) => {
                  const log = filteredLogs[virtualRow.index]
                  const timestampText = formatLogTimestamp(log.timestamp)
                  const levelText = formatLogLevel(log.level)
                  const moduleTextStyle = getModuleTextStyle(log)
                  return (
                    <div
                      key={virtualRow.key}
                      className={cn(
                        'hover:bg-[#43230c]/75 border-b border-[#3b200b]/40',
                        fontSizeConfig[fontSize].class
                      )}
                      style={{
                        position: 'absolute',
                        top: 0,
                        left: 0,
                        width: '100%',
                        transform: `translateY(${virtualRow.start}px)`,
                        paddingTop: `${lineSpacing / 2}px`,
                        paddingBottom: `${lineSpacing / 2}px`,
                      }}
                    >
                      {/* 移动端：垂直布局 */}
                      <div className="flex flex-col gap-0.5 sm:hidden">
                        {/* 第一行：时间戳和级别 */}
                        <div className="flex items-center gap-2">
                          <span className="text-[10px] text-gray-500 dark:text-gray-600">
                            {timestampText}
                          </span>
                          <span
                            className={cn('text-[10px] font-semibold', getLevelColor(log.level))}
                          >
                            [{levelText}]
                          </span>
                        </div>
                        {/* 第二行：模块名 */}
                        <div
                          className={cn(
                            'truncate text-[10px]',
                            !moduleTextStyle && 'text-cyan-400 dark:text-cyan-500'
                          )}
                          style={moduleTextStyle}
                        >
                          {getModuleDisplayName(log)}
                        </div>
                        {/* 第三行：消息内容 */}
                        <div
                          className={cn(
                            'text-[10px] break-words whitespace-pre-wrap',
                            !moduleTextStyle && 'text-gray-300 dark:text-gray-400'
                          )}
                          style={moduleTextStyle}
                        >
                          {log.message}
                        </div>
                      </div>

                      {/* 平板/桌面端：水平布局 */}
                      <div className={cn('hidden items-start sm:flex', logColumnLayout.gapClass)}>
                        {/* 时间戳 */}
                        <span
                          className={cn(
                            'flex-shrink-0 text-gray-500 dark:text-gray-600',
                            logColumnLayout.timestampClass
                          )}
                          style={{ width: timestampWidth }}
                        >
                          {timestampText}
                        </span>

                        {/* 日志级别 */}
                        <span
                          className={cn(
                            'flex-shrink-0 font-semibold',
                            logColumnLayout.levelClass,
                            getLevelColor(log.level)
                          )}
                          style={{ width: levelWidth }}
                        >
                          [{levelText}]
                        </span>

                        {/* 模块名 */}
                        <span
                          className={cn(
                            'flex-shrink-0 truncate',
                            logColumnLayout.moduleClass,
                            !moduleTextStyle && 'text-cyan-400 dark:text-cyan-500'
                          )}
                          style={{ ...moduleTextStyle, width: moduleWidth }}
                        >
                          {getModuleDisplayName(log)}
                        </span>

                        {/* 消息内容 */}
                        <span
                          className={cn(
                            'flex-1 break-words whitespace-pre-wrap',
                            !moduleTextStyle && 'text-gray-300 dark:text-gray-400'
                          )}
                          style={moduleTextStyle}
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

function getLevelColor(level: LogEntry['level']) {
  switch (level) {
    case 'DEBUG':
      return 'text-gray-400 dark:text-gray-500'
    case 'INFO':
      return 'text-blue-400 dark:text-blue-500'
    case 'WARNING':
      return 'text-yellow-400 dark:text-yellow-500'
    case 'ERROR':
      return 'text-red-400 dark:text-red-500'
    case 'CRITICAL':
      return 'text-red-600 dark:text-red-400 font-bold'
    default:
      return 'text-gray-300 dark:text-gray-400'
  }
}

interface LogViewerPageProps {
  defaultTab?: LogViewerTab
}

export function LogViewerPage({ defaultTab }: LogViewerPageProps) {
  const [activeTab, setActiveTab] = useState<LogViewerTab>(
    () => defaultTab ?? loadStoredLogViewerTab()
  )
  const [topbarTabsRoot, setTopbarTabsRoot] = useState<HTMLElement | null>(null)
  const [topbarTabsCompact, setTopbarTabsCompact] = useState(false)
  const topbarTabsCompactRef = useRef(false)
  const [reasoningToolbarVisible, setReasoningToolbarVisible] = useState(activeTab === 'reasoning')
  const [showSwitchHint, setShowSwitchHint] = useState(() =>
    typeof window === 'undefined'
      ? false
      : localStorage.getItem(LOG_VIEWER_SWITCH_HINT_DISMISSED_KEY) !== 'true'
  )
  const toolbarContainerId = 'log-terminal-toolbar'
  const topbarTabsContainerId = 'log-viewer-topbar-tabs'
  const reasoningTopbarActionsContainerId = 'reasoning-topbar-actions'

  useEffect(() => {
    const frameId = requestAnimationFrame(() => {
      setTopbarTabsRoot(document.getElementById(topbarTabsContainerId))
    })

    return () => cancelAnimationFrame(frameId)
  }, [])

  useEffect(() => {
    topbarTabsCompactRef.current = topbarTabsCompact
  }, [topbarTabsCompact])

  useEffect(() => {
    if (!topbarTabsRoot) return

    let frameId: number | null = null

    const updateCompactState = () => {
      if (frameId !== null) {
        cancelAnimationFrame(frameId)
      }
      cancelAnimationFrame(frameId)
      frameId = requestAnimationFrame(() => {
        const workspaceTabs = document.querySelector('[data-dashboard-workspace-tabs="true"]')
        const workspaceTabsMeasure = document.querySelector(
          '[data-dashboard-workspace-tabs-measure="true"]'
        )
        const measureEl = topbarTabsRoot.querySelector('[data-log-viewer-switcher-measure="true"]')
        if (
          !(workspaceTabs instanceof HTMLElement) ||
          !(workspaceTabsMeasure instanceof HTMLElement) ||
          !(measureEl instanceof HTMLElement)
        ) {
          setTopbarTabsCompact(false)
          return
        }

        const rootRect = topbarTabsRoot.getBoundingClientRect()
        const measureRect = measureEl.getBoundingClientRect()
        const workspaceRect = workspaceTabs.getBoundingClientRect()
        const workspaceMeasureRect = workspaceTabsMeasure.getBoundingClientRect()
        const fullWorkspaceTabsLeft = workspaceRect.right - workspaceMeasureRect.width
        const gap = fullWorkspaceTabsLeft - (rootRect.left + measureRect.width)
        const threshold = topbarTabsCompactRef.current
          ? TOPBAR_SWITCH_EXPAND_GAP
          : TOPBAR_SWITCH_COMPACT_GAP
        setTopbarTabsCompact(gap < threshold)
      })
    }

    updateCompactState()

    const resizeObserver = new ResizeObserver(() => {
      updateCompactState()
    })

    resizeObserver.observe(topbarTabsRoot)
    const workspaceTabs = document.querySelector('[data-dashboard-workspace-tabs="true"]')
    if (workspaceTabs instanceof HTMLElement) {
      resizeObserver.observe(workspaceTabs)
    }
    const workspaceTabsMeasure = document.querySelector(
      '[data-dashboard-workspace-tabs-measure="true"]'
    )
    if (workspaceTabsMeasure instanceof HTMLElement) {
      resizeObserver.observe(workspaceTabsMeasure)
    }

    return () => {
      if (frameId !== null) {
        cancelAnimationFrame(frameId)
      }
      resizeObserver.disconnect()
    }
  }, [activeTab, reasoningToolbarVisible, topbarTabsRoot])

  const renderTopbarSwitcherMeasure = () => {
    const showReasoningRefresh = activeTab === 'reasoning' && !reasoningToolbarVisible

    return (
      <div
        data-log-viewer-switcher-measure="true"
        aria-hidden="true"
        className="pointer-events-none invisible absolute top-0 left-0 flex items-center gap-2 opacity-0"
      >
        <div className="bg-muted inline-flex h-9 items-center justify-center rounded-lg p-1">
          <div className="flex items-center gap-1.5 px-3 py-1 text-sm font-medium">
            <Terminal className="h-4 w-4" />
            <span>终端</span>
          </div>
          <div className="flex items-center gap-1.5 px-3 py-1 text-sm font-medium">
            <BrainCircuit className="h-4 w-4" />
            <span>推理过程</span>
          </div>
          <div className="flex items-center gap-1.5 px-3 py-1 text-sm font-medium">
            <BarChart3 className="h-4 w-4" />
            <span>详细统计</span>
          </div>
        </div>
        {showReasoningRefresh && <div className="h-9 w-9" />}
      </div>
    )
  }

  const renderTabSwitcher = (includeTopbarActions = false, compact = false) => {
    const labelClassName = includeTopbarActions && compact ? 'sr-only' : undefined

    return (
      <div className="flex min-w-0 items-center gap-2">
        <TabsList
          className={cn(
            'bg-muted/80 inline-flex h-9 items-center justify-start rounded-lg p-1',
            reasoningToolbarVisible && 'border-primary/20'
          )}
        >
          <TabsTrigger value="terminal" className="gap-1.5" aria-label="终端">
            <Terminal className="h-4 w-4" />
            <span className={labelClassName}>终端</span>
          </TabsTrigger>
          <TabsTrigger value="reasoning" className="gap-1.5" aria-label="推理过程">
            <BrainCircuit className="h-4 w-4" />
            <span className={labelClassName}>推理过程</span>
          </TabsTrigger>
          <TabsTrigger value="statistics" className="gap-1.5" aria-label="详细统计">
            <BarChart3 className="h-4 w-4" />
            <span className={labelClassName}>详细统计</span>
          </TabsTrigger>
        </TabsList>
        {includeTopbarActions && (
          <div id={reasoningTopbarActionsContainerId} className="flex items-center gap-1" />
        )}
      </div>
    )
  }
  const topbarTabsPortal = topbarTabsRoot
    ? createPortal(
        <>
          {renderTabSwitcher(true, topbarTabsCompact)}
          {renderTopbarSwitcherMeasure()}
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
      className="flex h-full flex-col overflow-hidden"
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
          <div className="text-muted-foreground flex items-center justify-between gap-3 text-xs">
            <span>已将「推理过程」和「详细统计」移至此处。</span>
            <Button
              variant="ghost"
              size="sm"
              className="h-6 px-2 text-xs"
              onClick={dismissSwitchHint}
            >
              我知道了
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
      <TabsContent value="reasoning" className="m-0 min-h-0 flex-1 overflow-hidden">
        <ReasoningProcessPage
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
