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
  const [moduleFilter, setModuleFilter] = useState<string>(() => getSetting('logModuleFilter'))
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

  // 获取所有唯一的模块名（过滤掉空字符串）
  const uniqueModules = useMemo(() => {
    const modules = new Set(logs.map(log => log.module).filter(m => m && m.trim() !== ''))
    return Array.from(modules).sort()
  }, [logs])

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
    const logText = filteredLogs.map(log => 
      `${log.timestamp} [${log.level.padEnd(8)}] [${log.module}] ${log.message}`
    ).join('\n')
    
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

  const handleModuleFilterChange = useCallback((module: string) => {
    setModuleFilter(module)
    setSetting('logModuleFilter', module)
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

  const effectiveModuleFilter =
    moduleFilter === 'all' || uniqueModules.includes(moduleFilter) ? moduleFilter : 'all'

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
    handleModuleFilterChange('all')
  }

  // 过滤日志
  const filteredLogs = useMemo(() => {
    return logs.filter((log) => {
      // 搜索过滤
      const matchesSearch =
        searchQuery === '' ||
        log.message.toLowerCase().includes(searchQuery.toLowerCase()) ||
        log.module.toLowerCase().includes(searchQuery.toLowerCase())
      
      // 级别过滤：选择某个级别时显示该级别及以上的日志
      const matchesLevel =
        levelFilter === 'all' ||
        levelPriority[log.level] >= levelPriority[levelFilter]
      
      // 模块过滤
      const matchesModule = effectiveModuleFilter === 'all' || log.module === effectiveModuleFilter
      
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
  }, [logs, searchQuery, levelFilter, effectiveModuleFilter, dateFrom, dateTo])

  // 虚拟滚动配置 - 根据字号和行间距动态计算行高
  const estimatedRowHeight = fontSizeConfig[fontSize].rowHeight + lineSpacing
  const logColumnLayout = logColumnLayoutConfig[fontSize]
  const timestampWidth = logColumnLayout.timestampWidth + columnWidthExtra
  const levelWidth = logColumnLayout.levelWidth + Math.round(columnWidthExtra * 0.5)
  const moduleWidth = logColumnLayout.moduleWidth + columnWidthExtra
  
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

  const toolbarContent = (
    <Collapsible open={filtersOpen} onOpenChange={handleFiltersOpenChange}>
      <div className="flex w-full flex-col gap-2 lg:items-end">
        <div className="flex w-full flex-wrap items-center gap-1.5 lg:justify-end">
          <div className="relative min-w-[180px] flex-1 lg:max-w-64">
            <Search className="absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
            <Input
              placeholder="搜索日志..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              onKeyDown={handleSearchKeyDown}
              className="h-8 pl-8 pr-8 text-xs sm:text-sm"
            />
            {searchQuery && (
              <Button
                type="button"
                variant="ghost"
                size="icon"
                onClick={() => setSearchQuery('')}
                className="absolute right-0.5 top-1/2 h-7 w-7 -translate-y-1/2"
                title="清空搜索"
                aria-label="清空搜索"
              >
                <X className="h-3.5 w-3.5" />
              </Button>
            )}
          </div>

          <Button
            variant={autoScroll ? 'default' : 'outline'}
            size="sm"
            onClick={toggleAutoScroll}
            className="h-8 px-2"
            title={autoScroll ? '自动滚动' : '已暂停'}
          >
            {autoScroll ? (
              <Pause className="h-3.5 w-3.5" />
            ) : (
              <Play className="h-3.5 w-3.5" />
            )}
            <span className="ml-1 text-xs">{autoScroll ? '滚动' : '暂停'}</span>
          </Button>
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

          <div className="ml-auto flex items-center gap-2 whitespace-nowrap text-xs text-muted-foreground lg:ml-1">
            <span className="flex items-center gap-1.5">
              <span
                className={cn(
                  'h-2 w-2 rounded-full',
                  connected ? 'bg-green-500 animate-pulse' : 'bg-red-500'
                )}
              />
              {connected ? '已连接' : '未连接'}
            </span>
            <span>
              <span className="font-mono">{filteredLogs.length} / {logs.length}</span>
              <span className="ml-1">条日志</span>
            </span>
          </div>
        </div>

        <CollapsibleContent className="w-full space-y-2 lg:max-w-[760px]">
                {/* 级别和模块筛选 */}
                <div className="flex flex-col gap-2 sm:flex-row sm:gap-2">
                  <Select value={levelFilter} onValueChange={(value) => handleLevelFilterChange(value as LogLevelFilter)}>
                    <SelectTrigger className="w-full sm:flex-1 h-8 text-xs">
                      <Filter className="h-3.5 w-3.5 mr-1.5" />
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

                  <Select value={effectiveModuleFilter} onValueChange={handleModuleFilterChange}>
                    <SelectTrigger className="w-full sm:flex-1 h-8 text-xs">
                      <Filter className="h-3.5 w-3.5 mr-1.5" />
                      <SelectValue placeholder="模块" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="all">全部模块</SelectItem>
                      {uniqueModules.map(module => (
                        <SelectItem key={module} value={module}>
                          {module}
                        </SelectItem>
                      ))}
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
                          'w-full sm:flex-1 justify-start text-left font-normal h-8',
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

                  <Popover>
                    <PopoverTrigger asChild>
                      <Button
                        variant="outline"
                        size="sm"
                        className={cn(
                          'w-full sm:flex-1 justify-start text-left font-normal h-8',
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
                      onClick={clearDateFilter}
                      className="w-full sm:w-auto h-8"
                    >
                      <X className="h-3.5 w-3.5 sm:mr-1" />
                      <span className="text-xs">清除</span>
                    </Button>
                  )}
                </div>

                {/* 显示设置 */}
                <div className="flex flex-col gap-2 border-t border-border/50 pt-2 sm:flex-row sm:items-center sm:gap-3">
                  {/* 字号调整 */}
                  <div className="flex items-center gap-2">
                    <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
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
                  <div className="flex items-center gap-2 flex-1 max-w-[200px]">
                    <span className="text-xs text-muted-foreground whitespace-nowrap">行距</span>
                    <Slider
                      value={[lineSpacing]}
                      onValueChange={handleLineSpacingChange}
                      min={LINE_SPACING_MIN}
                      max={LINE_SPACING_MAX}
                      step={2}
                      className="flex-1"
                    />
                    <span className="text-xs text-muted-foreground w-7">{lineSpacing}px</span>
                  </div>

                  <div className="flex items-center gap-2 flex-1 max-w-[220px]">
                    <span className="text-xs text-muted-foreground whitespace-nowrap">列宽</span>
                    <Slider
                      value={[columnWidthExtra]}
                      onValueChange={handleColumnWidthExtraChange}
                      min={COLUMN_WIDTH_EXTRA_MIN}
                      max={COLUMN_WIDTH_EXTRA_MAX}
                      step={8}
                      className="flex-1"
                    />
                    <span className="text-xs text-muted-foreground w-9">+{columnWidthExtra}</span>
                  </div>

                </div>
              </CollapsibleContent>
      </div>
    </Collapsible>
  )

  const toolbarPortal =
    toolbarVisible && toolbarRoot ? createPortal(toolbarContent, toolbarRoot) : null

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden">
      {toolbarPortal}

      {/* 日志终端 - 占据剩余所有空间 */}
      <div className="min-h-0 flex-1 px-2 pb-2 sm:px-3 sm:pb-3 lg:px-4 lg:pt-2 lg:pb-4">
        <Card
          className="h-full overflow-hidden border-[#24170f]/70 dark:border-[#1d120c]/80"
          style={{ backgroundColor: '#633312' }}
        >
          <div 
            ref={parentRef}
            className={cn(
              "h-full overflow-auto selection:bg-[#5a3924] selection:text-[#fff2df]",
              // 自定义滚动条样式
              "[&::-webkit-scrollbar]:w-2.5",
              "[&::-webkit-scrollbar-track]:bg-transparent",
              "[&::-webkit-scrollbar-thumb]:bg-border [&::-webkit-scrollbar-thumb]:rounded-full",
              "[&::-webkit-scrollbar-thumb:hover]:bg-border/80"
            )}
            style={{ backgroundColor: '#211607' }}
          >
            <div
              className={cn(
                "p-2 sm:p-3 font-mono relative selection:bg-[#5a3924] selection:text-[#fff2df]",
                fontSizeConfig[fontSize].class
              )}
              style={{
                height: `${rowVirtualizer.getTotalSize()}px`,
                minHeight: '100%',
              }}
            >
              {filteredLogs.length === 0 ? (
                <div className="text-gray-500 dark:text-gray-600 text-center py-8 text-xs sm:text-sm">
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
                          <span className="text-gray-500 dark:text-gray-600 text-[10px]">
                            {timestampText}
                          </span>
                          <span
                            className={cn(
                              'font-semibold text-[10px]',
                              getLevelColor(log.level)
                            )}
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
                          {log.module}
                        </div>
                        {/* 第三行：消息内容 */}
                        <div
                          className={cn(
                            'whitespace-pre-wrap break-words text-[10px]',
                            !moduleTextStyle && 'text-gray-300 dark:text-gray-400'
                          )}
                          style={moduleTextStyle}
                        >
                          {log.message}
                        </div>
                      </div>

                      {/* 平板/桌面端：水平布局 */}
                      <div className={cn('hidden sm:flex items-start', logColumnLayout.gapClass)}>
                        {/* 时间戳 */}
                        <span
                          className={cn(
                            'text-gray-500 dark:text-gray-600 flex-shrink-0',
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
                          {log.module}
                        </span>

                        {/* 消息内容 */}
                        <span
                          className={cn(
                            'flex-1 whitespace-pre-wrap break-words',
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
  const [activeTab, setActiveTab] = useState<LogViewerTab>(() =>
    defaultTab ?? loadStoredLogViewerTab()
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
    localStorage.setItem(LOG_VIEWER_ACTIVE_TAB_KEY, activeTab)
  }, [activeTab])

  useEffect(() => {
    if (!topbarTabsRoot) return

    let frameId = 0
    const updateCompactState = () => {
      cancelAnimationFrame(frameId)
      frameId = requestAnimationFrame(() => {
        const workspaceTabs = document.querySelector('[data-dashboard-workspace-tabs="true"]')
        const workspaceTabsMeasure = document.querySelector('[data-dashboard-workspace-tabs-measure="true"]')
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
    window.addEventListener('resize', updateCompactState)

    const resizeObserver = new ResizeObserver(updateCompactState)
    resizeObserver.observe(document.body)
    resizeObserver.observe(topbarTabsRoot)

    const workspaceTabs = document.querySelector('[data-dashboard-workspace-tabs="true"]')
    if (workspaceTabs instanceof HTMLElement) {
      resizeObserver.observe(workspaceTabs)
    }
    const workspaceTabsMeasure = document.querySelector('[data-dashboard-workspace-tabs-measure="true"]')
    if (workspaceTabsMeasure instanceof HTMLElement) {
      resizeObserver.observe(workspaceTabsMeasure)
    }

    return () => {
      cancelAnimationFrame(frameId)
      window.removeEventListener('resize', updateCompactState)
      resizeObserver.disconnect()
    }
  }, [activeTab, reasoningToolbarVisible, topbarTabsRoot])

  const renderTopbarSwitcherMeasure = () => {
    const showReasoningRefresh = activeTab === 'reasoning' && !reasoningToolbarVisible

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
        {showReasoningRefresh && <div className="h-9 w-9" />}
      </div>
    )
  }

  const renderTabSwitcher = (includeTopbarActions = false, compact = false) => {
    const labelClassName = includeTopbarActions && compact ? 'sr-only' : undefined

    return (
      <div className="flex min-w-0 items-center gap-2">
        <TabsList
          data-log-viewer-switcher={includeTopbarActions ? 'true' : undefined}
          data-log-viewer-switcher-compact={includeTopbarActions && compact ? 'true' : undefined}
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
          <div id={reasoningTopbarActionsContainerId} className="hidden items-center sm:flex" />
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
    <Tabs value={activeTab} onValueChange={(value) => setActiveTab(value as LogViewerTab)} className="flex h-full min-h-0 flex-col overflow-hidden">
      {topbarTabsPortal}
      <div
        className={cn(
          'flex shrink-0 flex-wrap items-center justify-between gap-2 border-b px-3 py-1 lg:px-4',
          ((activeTab === 'reasoning' && !reasoningToolbarVisible) ||
            activeTab === 'statistics') &&
            'sm:hidden'
        )}
      >
        <div className="sm:hidden">{renderTabSwitcher()}</div>
        <div id={toolbarContainerId} className="flex min-w-0 flex-1 justify-end" />
      </div>
      {showSwitchHint && (
        <div className="shrink-0 border-b px-3 py-2 lg:px-4">
          <div className="flex items-start gap-2 rounded-md border border-primary/30 bg-primary/5 px-3 py-2 text-sm">
            <div className="flex min-w-0 flex-1 flex-wrap items-center gap-x-2 gap-y-1">
              <span className="font-medium text-foreground">小提示</span>
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
        <LogTerminalPane toolbarContainerId={toolbarContainerId} toolbarVisible={activeTab === 'terminal'} />
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
