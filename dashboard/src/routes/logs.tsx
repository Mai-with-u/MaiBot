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
  DashboardFolderPanel,
  DashboardFolderTab,
  DashboardFolderTabBar,
} from '@/components/ui/dashboard-tabs'
import { FilterPanel, FilterPanelToggle } from '@/components/ui/filter-panel'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { Slider } from '@/components/ui/slider'
import { StatusLight } from '@/components/ui/status-light'
import { Tabs, TabsContent } from '@/components/ui/tabs'
import { useMediaQuery } from '@/hooks/use-media-query'
import { logWebSocket, type LogEntry } from '@/lib/log-websocket'
import { useVirtualizerUnmountGuard } from '@/lib/use-virtualizer-unmount-guard'
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
const LOG_VIEWER_ACTIVE_TAB_KEY = 'log-viewer-active-tab'

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
  statusContainerId?: string
  toolbarVisible: boolean
}

function LogTerminalPane({ toolbarContainerId, statusContainerId, toolbarVisible }: LogTerminalPaneProps) {
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
  // 手机上的筛选弹层与桌面端的展开状态分开，避免把弹层的开关记进设置
  const isMobileViewport = useMediaQuery('(max-width: 1023.98px)')
  const [mobileFilterOpen, setMobileFilterOpen] = useState(false)
  const [sourcesOpen, setSourcesOpen] = useState(false)
  const [toolbarRoot, setToolbarRoot] = useState<HTMLElement | null>(null)
  const [statusRoot, setStatusRoot] = useState<HTMLElement | null>(null)
  const parentRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    setToolbarRoot(document.getElementById(toolbarContainerId))
  }, [toolbarContainerId])

  useEffect(() => {
    setStatusRoot(statusContainerId ? document.getElementById(statusContainerId) : null)
  }, [statusContainerId])

  // 订阅全局 WebSocket 连接
  useEffect(() => {
    // 日志管理器会批量推送快照，避免每条日志都触发一次 React 更新。
    const unsubscribeLogs = logWebSocket.onLog((nextLogs) => {
      setLogs(nextLogs)
    })
    setLogs(logWebSocket.getAllLogs())

    // 订阅连接状态
    const unsubscribeConnection = logWebSocket.onConnectionChange((isConnected) => {
      setConnected(isConnected)
    })

    // 清理订阅
    return () => {
      unsubscribeLogs()
      unsubscribeConnection()
    }
  }, [])

  // 模块标识用于稳定过滤，后端下发的中文别名仅用于界面展示。
  const uniqueModules = useMemo(() => {
    const modules = new Map<string, string>()
    for (const log of logs) {
      const module = log.module.trim()
      if (module) {
        modules.set(module, getModuleDisplayName(log))
      }
    }
    return Array.from(modules, ([id, displayName]) => ({ id, displayName })).sort((a, b) =>
      a.displayName.localeCompare(b.displayName, 'zh-CN')
    )
  }, [logs])

  const hiddenModules = useMemo(() => {
    if (storedModuleFilter === 'all') return new Set<string>()

    try {
      const parsed = JSON.parse(storedModuleFilter)
      if (Array.isArray(parsed) && parsed.every((module) => typeof module === 'string')) {
        return new Set<string>(parsed)
      }
    } catch {
      // 兼容旧版单选设置：旧值代表只显示该模块。
    }

    if (!uniqueModules.some(({ id }) => id === storedModuleFilter)) return new Set<string>()

    return new Set(uniqueModules.filter(({ id }) => id !== storedModuleFilter).map(({ id }) => id))
  }, [storedModuleFilter, uniqueModules])

  // 日志级别颜色映射
  const getLevelColor = (level: LogEntry['level']) => {
    switch (level) {
      case 'DEBUG':
        return 'text-muted-foreground'
      case 'INFO':
        return 'text-blue-500 dark:text-blue-400'
      case 'WARNING':
        return 'text-yellow-600 dark:text-yellow-500'
      case 'ERROR':
        return 'text-red-600 dark:text-red-500'
      case 'CRITICAL':
        return 'text-red-700 dark:text-red-400 font-bold'
      default:
        return 'text-foreground'
    }
  }

  // 清空日志
  const handleClear = () => {
    logWebSocket.clearLogs() // 清空全局缓存
  }

  // 导出日志为 TXT 格式
  const handleExport = () => {
    // 格式化日志为文本
    const logText = filteredLogs
      .map((log) => `${log.timestamp} [${log.level.padEnd(8)}] [${log.module}] ${log.message}`)
      .join('\n')

    const dataBlob = new Blob([logText], { type: 'text/plain;charset=utf-8' })
    const url = URL.createObjectURL(dataBlob)
    const link = document.createElement('a')
    link.href = url
    link.download = `logs-${format(new Date(), 'yyyy-MM-dd-HHmmss')}.txt`
    link.click()
    URL.revokeObjectURL(url)
  }

  // 切换自动滚动
  const toggleAutoScroll = () => {
    const nextAutoScroll = !autoScroll
    setAutoScroll(nextAutoScroll)
    setSetting('logAutoScroll', nextAutoScroll)
  }

  const handleLevelFilterChange = useCallback((level: LogLevelFilter) => {
    setLevelFilter(level)
    setSetting('logLevelFilter', level)
  }, [])

  const saveHiddenModules = useCallback((modules: Set<string>) => {
    const value = modules.size === 0 ? 'all' : JSON.stringify(Array.from(modules).sort())
    setStoredModuleFilter(value)
    setSetting('logModuleFilter', value)
  }, [])

  const toggleModuleVisibility = useCallback(
    (module: string) => {
      const nextHiddenModules = new Set(hiddenModules)
      if (nextHiddenModules.has(module)) {
        nextHiddenModules.delete(module)
      } else {
        nextHiddenModules.add(module)
      }
      saveHiddenModules(nextHiddenModules)
    },
    [hiddenModules, saveHiddenModules]
  )

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
        if (dateFrom) {
          const fromDate = new Date(dateFrom)
          fromDate.setHours(0, 0, 0, 0)
          matchesDate = matchesDate && logDate >= fromDate
        }
        if (dateTo) {
          const toDate = new Date(dateTo)
          toDate.setHours(23, 59, 59, 999)
          matchesDate = matchesDate && logDate <= toDate
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
  useVirtualizerUnmountGuard(rowVirtualizer)

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

  // 级别、时间与显示设置：桌面端在工具栏下就地展开，手机上放进底部弹层
  const filterFields = (
    <>
            {/* 级别筛选 */}
            <div className="grid grid-cols-2 gap-2 sm:flex sm:flex-row sm:gap-2">
              <Select
                value={levelFilter}
                onValueChange={(value) => handleLevelFilterChange(value as LogLevelFilter)}
              >
                <SelectTrigger controlSize="sm" className="w-full text-xs sm:flex-1">
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
            <div className="grid grid-cols-2 gap-2 sm:flex sm:flex-row sm:gap-2">
              <Popover>
                <PopoverTrigger asChild>
                  <Button
                    variant="outline"
                    size="sm"
                    className={cn(
                      'h-8 w-full min-w-0 justify-start px-2 text-left font-normal sm:flex-1 sm:px-3',
                      !dateFrom && 'text-muted-foreground'
                    )}
                  >
                    <CalendarIcon className="mr-1.5 h-3.5 w-3.5 shrink-0" />
                    <span className="truncate text-xs">
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

              <Popover>
                <PopoverTrigger asChild>
                  <Button
                    variant="outline"
                    size="sm"
                    className={cn(
                      'h-8 w-full min-w-0 justify-start px-2 text-left font-normal sm:flex-1 sm:px-3',
                      !dateTo && 'text-muted-foreground'
                    )}
                  >
                    <CalendarIcon className="mr-1.5 h-3.5 w-3.5 shrink-0" />
                    <span className="truncate text-xs">
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
                  onClick={clearDateFilter}
                  className="col-span-2 h-8 w-full sm:w-auto"
                >
                  <X className="h-3.5 w-3.5 sm:mr-1" />
                  <span className="text-xs">清除</span>
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
                <div className="flex gap-1">
                  {(Object.keys(fontSizeConfig) as FontSize[]).map((size) => (
                    <Button
                      key={size}
                      variant={fontSize === size ? 'default' : 'outline'}
                      size="sm"
                      onClick={() => handleFontSizeChange(size)}
                      className="h-6 px-2 text-xs"
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
                  step={2}
                  className="flex-1"
                />
                <span className="text-muted-foreground w-7 text-xs">{lineSpacing}px</span>
              </div>

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
    </>
  )

  const visibleModuleCount = uniqueModules.filter(({ id }) => !hiddenModules.has(id)).length
  const selectedModules = useMemo(
    () => new Set(uniqueModules.filter(({ id }) => !hiddenModules.has(id)).map(({ id }) => id)),
    [uniqueModules, hiddenModules]
  )

  const toolbarContent = (
    <Collapsible open={filtersOpen} onOpenChange={handleFiltersOpenChange}>
      <div data-log-toolbar="true" className="flex w-full min-w-0 flex-col gap-2">
        <div className="flex w-full min-w-0 flex-wrap items-center gap-1.5">
          <div className="relative w-full min-w-[180px] sm:w-60">
            <Search className="text-muted-foreground absolute top-1/2 left-2 h-3.5 w-3.5 -translate-y-1/2" />
            <Input
              placeholder="搜索日志..."
              aria-label="搜索日志"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              onKeyDown={handleSearchKeyDown}
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

          <FilterPanelToggle
            label="来源"
            open={sourcesOpen}
            selectedCount={visibleModuleCount}
            total={uniqueModules.length}
            controls="log-source-panel"
            onOpenChange={setSourcesOpen}
          />

          {isMobileViewport ? (
            <Button
              variant="outline"
              size="sm"
              onClick={() => setMobileFilterOpen(true)}
              data-log-mobile-filter-button="true"
            >
              <Filter className="h-3.5 w-3.5" />
              筛选
            </Button>
          ) : (
            <CollapsibleTrigger asChild>
              <Button
                variant="outline"
                size="sm"
                aria-pressed={filtersOpen}
                title={filtersOpen ? '收起筛选' : '展开筛选'}
              >
                <Filter className="h-3.5 w-3.5" />
                筛选
                <span aria-hidden="true" className="font-mono font-normal">
                  {levelFilter === 'all' ? '全部级别' : `${levelFilter}+`}
                </span>
                {filtersOpen ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
              </Button>
            </CollapsibleTrigger>
          )}

          <span className="flex-1" />

          <Button
            variant="outline"
            size="sm"
            aria-pressed={autoScroll}
            onClick={toggleAutoScroll}
            title={autoScroll ? '正在跟随最新日志，点击暂停' : '已暂停，点击跟随最新日志'}
          >
            {autoScroll ? <Pause className="h-3.5 w-3.5" /> : <Play className="h-3.5 w-3.5" />}
            跟随最新
          </Button>
          <Button
            variant="outline"
            size="icon-sm"
            onClick={handleExport}
            disabled={filteredLogs.length === 0}
            title="导出日志"
            aria-label="导出"
          >
            <Download className="h-3.5 w-3.5" />
          </Button>
          <Button
            variant="outline"
            size="icon-sm"
            onClick={handleClear}
            disabled={logs.length === 0}
            title="清空日志"
            aria-label="清空"
          >
            <Trash2 className="h-3.5 w-3.5" />
          </Button>
        </div>

        {sourcesOpen && (
          <FilterPanel
            id="log-source-panel"
            title="来源"
            options={uniqueModules.map(({ id, displayName }) => ({
              id,
              label: displayName,
              title: `${displayName}（${id}）`,
            }))}
            selected={selectedModules}
            onToggle={toggleModuleVisibility}
            onSelectAll={() => saveHiddenModules(new Set())}
            onSelectNone={() => saveHiddenModules(new Set(uniqueModules.map(({ id }) => id)))}
          />
        )}

        {/* 手机上筛选改为底部弹层，这里只在桌面端就地展开 */}
        {!isMobileViewport && (
          <CollapsibleContent data-filter-panel="true" className="w-full space-y-2">
            {filterFields}
          </CollapsibleContent>
        )}
      </div>
      <Dialog open={mobileFilterOpen} onOpenChange={setMobileFilterOpen}>
        <DialogContent data-log-filter-sheet="true">
          <DialogHeader>
            <DialogTitle>筛选</DialogTitle>
            <DialogDescription className="sr-only">日志级别、时间范围与显示设置</DialogDescription>
          </DialogHeader>
          <div className="space-y-3">{filterFields}</div>
          <DialogFooter>
            <Button onClick={() => setMobileFilterOpen(false)}>完成</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Collapsible>
  )

  const toolbarPortal =
    toolbarVisible && toolbarRoot ? createPortal(toolbarContent, toolbarRoot) : null
  const statusPortal =
    toolbarVisible && statusRoot
      ? createPortal(
          <span data-log-status="true" className="flex items-center gap-2 text-xs whitespace-nowrap">
            <StatusLight status={connected ? 'on' : 'error'} decorative />
            {connected ? '已连接' : '未连接'}
            <span className="text-muted-foreground">
              <span className="font-mono">
                {filteredLogs.length} / {logs.length}
              </span>{' '}
              条
            </span>
          </span>,
          statusRoot
        )
      : null

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden">
      {toolbarPortal}
      {statusPortal}

      {/* 日志终端 - 占据剩余所有空间 */}
      <div className="min-h-0 flex-1">
        <Card
          data-log-terminal="true"
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
                  const timestampText = formatLogTimestamp(log.timestamp)
                  const levelText = formatLogLevel(log.level)
                  const moduleTextStyle = getModuleTextStyle(log)
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

interface LogViewerPageProps {
  defaultTab?: LogViewerTab
}

export function LogViewerPage({ defaultTab }: LogViewerPageProps) {
  const isMobileViewport = useMediaQuery('(max-width: 1023.98px)')
  const [desktopActiveTab, setActiveTab] = useState<LogViewerTab>(
    () => defaultTab ?? loadStoredLogViewerTab()
  )
  // 手机上三个入口各自展示对应页面，避免上次的桌面页签覆盖入口选择。
  const activeTab = isMobileViewport ? (defaultTab ?? 'terminal') : desktopActiveTab
  const [reasoningToolbarVisible, setReasoningToolbarVisible] = useState(activeTab === 'reasoning')
  const toolbarContainerId = 'log-terminal-toolbar'
  const statusContainerId = 'log-viewer-status'

  useEffect(() => {
    if (isMobileViewport) return
    localStorage.setItem(LOG_VIEWER_ACTIVE_TAB_KEY, activeTab)
  }, [activeTab, isMobileViewport])

  return (
    <Tabs
      value={activeTab}
      onValueChange={(value) => setActiveTab(value as LogViewerTab)}
      data-log-viewer="true"
      className="flex h-full min-h-0 flex-col overflow-hidden p-2 sm:p-3 lg:px-4 lg:pt-3 lg:pb-4"
    >
      {/* 终端、推理过程、详细统计是页面级切换：标签压在面板上沿；手机上三个入口各自独立，不显示标签行 */}
      <div className="shrink-0 max-lg:hidden">
        <DashboardFolderTabBar
          aria-label="日志视图"
          trailing={<div id={statusContainerId} className="flex items-center" />}
        >
          <DashboardFolderTab value="terminal" icon={<Terminal />}>
            终端
          </DashboardFolderTab>
          <DashboardFolderTab value="reasoning" icon={<BrainCircuit />}>
            推理过程
          </DashboardFolderTab>
          <DashboardFolderTab value="statistics" icon={<BarChart3 />}>
            详细统计
          </DashboardFolderTab>
        </DashboardFolderTabBar>
      </div>

      <DashboardFolderPanel
        data-log-viewer-panel="true"
        className="flex min-h-0 flex-1 flex-col gap-2 overflow-hidden p-2 sm:p-3"
      >
        <div
          className={cn(
            // 工具栏容器里没有内容时整行收起
            'flex shrink-0 items-center has-[>:empty]:hidden',
            activeTab === 'reasoning' && !reasoningToolbarVisible && 'hidden'
          )}
        >
          <div id={toolbarContainerId} className="flex min-w-0 flex-1" />
        </div>

        <TabsContent value="terminal" className="m-0 min-h-0 flex-1 overflow-hidden">
          <LogTerminalPane
            toolbarContainerId={toolbarContainerId}
            statusContainerId={statusContainerId}
            toolbarVisible={activeTab === 'terminal'}
          />
        </TabsContent>
        <TabsContent value="reasoning" className="m-0 min-h-0 flex-1 overflow-hidden">
          <ReasoningProcessPage
            embedded
            toolbarContainerId={toolbarContainerId}
            statusContainerId={statusContainerId}
            toolbarVisible={activeTab === 'reasoning'}
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
            <StatisticsPage
              embedded
              toolbarContainerId={toolbarContainerId}
              toolbarVisible={activeTab === 'statistics'}
            />
          </Suspense>
        </TabsContent>
      </DashboardFolderPanel>
    </Tabs>
  )
}

export function ReasoningLogViewerPage() {
  return <LogViewerPage defaultTab="reasoning" />
}

export function StatisticsLogViewerPage() {
  return <LogViewerPage defaultTab="statistics" />
}
