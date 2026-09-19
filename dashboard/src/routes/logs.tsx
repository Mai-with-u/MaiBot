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
  return Math.min(Math.max(value, min), max)
}

function normalizeStoredActiveTab(value: unknown): LogViewerTab | null {
  if (value === 'terminal' || value === 'reasoning' || value === 'statistics') {
    return value
  }
  return null
}

function getInitialActiveTab(): LogViewerTab {
  if (typeof window === 'undefined') {
    return 'terminal'
  }

  const storedTab = normalizeStoredActiveTab(localStorage.getItem(LOG_VIEWER_ACTIVE_TAB_KEY))
  if (storedTab) {
    return storedTab
  }

  const hash = window.location.hash.replace(/^#/, '')
  return normalizeStoredActiveTab(hash) ?? 'terminal'
}

function getLevelColor(level: LogEntry['level']) {
  switch (level) {
    case 'DEBUG':
      return 'text-gray-400 dark:text-gray-500'
    case 'INFO':
      return 'text-green-400 dark:text-green-500'
    case 'WARNING':
      return 'text-yellow-400 dark:text-yellow-500'
    case 'ERROR':
      return 'text-red-400 dark:text-red-500'
    case 'CRITICAL':
      return 'text-red-300 dark:text-red-400 font-bold'
    default:
      return 'text-gray-300 dark:text-gray-400'
  }
}

function formatLogModule(log: LogEntry) {
  if (log.line_no !== undefined) {
    return `${log.module}:${log.line_no}`
  }
  return log.module
}

interface TerminalViewProps {
  toolbarContainerId?: string
  toolbarVisible?: boolean
}

function TerminalView({
  toolbarContainerId = 'log-terminal-toolbar',
  toolbarVisible = true,
}: TerminalViewProps) {
  const [logs, setLogs] = useState<LogEntry[]>([])
  const [isConnected, setIsConnected] = useState(false)
  const [autoScroll, setAutoScroll] = useState<boolean>(() =>
    getSetting<boolean>('logAutoScroll', true)
  )
  const [search, setSearch] = useState('')
  const [levelFilter, setLevelFilter] = useState<LogLevelFilter>(() =>
    getSetting<LogLevelFilter>('logLevelFilter', 'INFO')
  )
  const [hiddenModules, setHiddenModules] = useState<Set<string>>(() => {
    const saved = getSetting<string>('logModuleFilter', 'all')
    if (saved === 'all') return new Set()
    try {
      const parsed = JSON.parse(saved)
      return Array.isArray(parsed) ? new Set(parsed) : new Set()
    } catch {
      return new Set()
    }
  })
  const [knownModules, setKnownModules] = useState<Map<string, string>>(() => new Map())
  const [dateRange, setDateRange] = useState<{
    from: Date | undefined
    to: Date | undefined
  }>({
    from: undefined,
    to: undefined,
  })
  const [filtersOpen, setFiltersOpen] = useState<boolean>(() =>
    getSetting<boolean>('logFiltersOpen', false)
  )
  const [fontSize, setFontSize] = useState<FontSize>(() =>
    getSetting<FontSize>('logFontSize', 'sm')
  )
  const [lineSpacing, setLineSpacing] = useState<number>(() =>
    getSetting<number>('logLineSpacing', 4)
  )
  const [columnWidthExtra, setColumnWidthExtra] = useState<number>(() =>
    getSetting<number>('logColumnWidthExtra', 0)
  )
  const [toolbarRoot, setToolbarRoot] = useState<HTMLElement | null>(null)

  const parentRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    setToolbarRoot(document.getElementById(toolbarContainerId))
  }, [toolbarContainerId])

  useEffect(() => {
    const handleLog = (log: LogEntry) => {
      setLogs((prev) => [...prev.slice(-999), log])
      setKnownModules((prev) => {
        if (!prev.has(log.module)) {
          const next = new Map(prev)
          next.set(log.module, log.module_name || log.module)
          return next
        }
        return prev
      })
    }

    const handleBatch = (batchLogs: LogEntry[]) => {
      setLogs((prev) => [...prev, ...batchLogs].slice(-1000))
      setKnownModules((prev) => {
        let changed = false
        const next = new Map(prev)
        for (const log of batchLogs) {
          if (!next.has(log.module)) {
            next.set(log.module, log.module_name || log.module)
            changed = true
          }
        }
        return changed ? next : prev
      })
    }

    const unsubscribeLog = logWebSocket.subscribe(handleLog)
    const unsubscribeBatch = logWebSocket.subscribeBatch(handleBatch)
    const unsubscribeStatus = logWebSocket.subscribeStatus(setIsConnected)

    logWebSocket.connect()

    return () => {
      unsubscribeLog()
      unsubscribeBatch()
      unsubscribeStatus()
    }
  }, [])

  const availableModules = useMemo(() => {
    return Array.from(knownModules.entries())
      .map(([id, displayName]) => ({ id, displayName }))
      .sort((a, b) => a.displayName.localeCompare(b.displayName, 'zh-CN'))
  }, [knownModules])

  const clearLogs = () => {
    setLogs([])
  }

  const exportLogs = () => {
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
    a.click()
    URL.revokeObjectURL(url)
  }

  const handleToggleAutoScroll = () => {
    const nextValue = !autoScroll
    setAutoScroll(nextValue)
    setSetting('logAutoScroll', nextValue)
  }

  const handleLevelFilterChange = useCallback((value: LogLevelFilter) => {
    setLevelFilter(value)
    setSetting('logLevelFilter', value)
  }, [])

  const persistHiddenModules = useCallback((nextHidden: Set<string>) => {
    const serialized =
      nextHidden.size === 0 ? 'all' : JSON.stringify(Array.from(nextHidden).sort())
    setHiddenModules(nextHidden)
    setSetting('logModuleFilter', serialized)
  }, [])

  const toggleModule = useCallback(
    (moduleId: string) => {
      const next = new Set(hiddenModules)
      if (next.has(moduleId)) {
        next.delete(moduleId)
      } else {
        next.add(moduleId)
      }
      persistHiddenModules(next)
    },
    [hiddenModules, persistHiddenModules]
  )

  const handleFiltersOpenChange = useCallback((open: boolean) => {
    setFiltersOpen(open)
    setSetting('logFiltersOpen', open)
  }, [])

  const handleFontSizeChange = (size: FontSize) => {
    setFontSize(size)
    setSetting('logFontSize', size)
  }

  const handleLineSpacingChange = ([val]: number[]) => {
    const clamped = clampNumber(val, LINE_SPACING_MIN, LINE_SPACING_MAX)
    setLineSpacing(clamped)
    setSetting('logLineSpacing', clamped)
  }

  const handleColumnWidthExtraChange = ([val]: number[]) => {
    const clamped = clampNumber(val, COLUMN_WIDTH_EXTRA_MIN, COLUMN_WIDTH_EXTRA_MAX)
    setColumnWidthExtra(clamped)
    setSetting('logColumnWidthExtra', clamped)
  }

  const handleSearchKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Escape' && search) {
      e.preventDefault()
      setSearch('')
    }
  }

  const clearDateRange = () => {
    setDateRange({ from: undefined, to: undefined })
  }

  const resetAllFilters = () => {
    setSearch('')
    setDateRange({ from: undefined, to: undefined })
    handleLevelFilterChange('INFO')
    persistHiddenModules(new Set())
  }

  const filteredLogs = useMemo(() => {
    return logs.filter((log) => {
      const matchesSearch =
        search === '' ||
        log.message.toLowerCase().includes(search.toLowerCase()) ||
        log.module.toLowerCase().includes(search.toLowerCase()) ||
        formatLogModule(log).toLowerCase().includes(search.toLowerCase())

      const matchesLevel =
        levelFilter === 'all' || levelPriority[log.level] >= levelPriority[levelFilter]

      const matchesModule = !hiddenModules.has(log.module)

      let matchesDate = true
      if (dateRange.from || dateRange.to) {
        const logDate = new Date(log.timestamp)
        if (dateRange.from) {
          const fromDate = new Date(dateRange.from)
          fromDate.setHours(0, 0, 0, 0)
          matchesDate = matchesDate && logDate >= fromDate
        }
        if (dateRange.to) {
          const toDate = new Date(dateRange.to)
          toDate.setHours(23, 59, 59, 999)
          matchesDate = matchesDate && logDate <= toDate
        }
      }

      return matchesSearch && matchesLevel && matchesModule && matchesDate
    })
  }, [logs, search, levelFilter, hiddenModules, dateRange])

  const dynamicRowHeight = fontSizeConfig[fontSize].rowHeight + lineSpacing
  const activeLayout = logColumnLayoutConfig[fontSize]
  const dynamicTimestampWidth = activeLayout.timestampWidth + columnWidthExtra
  const dynamicLevelWidth = activeLayout.levelWidth + Math.round(columnWidthExtra * 0.5)
  const dynamicModuleWidth = activeLayout.moduleWidth + columnWidthExtra

  const rowVirtualizer = useVirtualizer({
    count: filteredLogs.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => dynamicRowHeight,
    overscan: 50,
  })

  const isAutoScrollingRef = useRef(false)
  const prevLogsCountRef = useRef(filteredLogs.length)

  useEffect(() => {
    const el = parentRef.current
    if (!el) return

    const handleScroll = () => {
      if (isAutoScrollingRef.current) return
      const { scrollTop, scrollHeight, clientHeight } = el
      const distanceFromBottom = scrollHeight - scrollTop - clientHeight

      if (distanceFromBottom > 100 && autoScroll) {
        setAutoScroll(false)
      } else if (distanceFromBottom < 50 && !autoScroll) {
        setAutoScroll(true)
      }
    }

    el.addEventListener('scroll', handleScroll, { passive: true })
    return () => el.removeEventListener('scroll', handleScroll)
  }, [autoScroll])

  useEffect(() => {
    const hasNewLogs = filteredLogs.length > prevLogsCountRef.current
    prevLogsCountRef.current = filteredLogs.length

    if (autoScroll && filteredLogs.length > 0 && hasNewLogs) {
      isAutoScrollingRef.current = true
      rowVirtualizer.scrollToIndex(filteredLogs.length - 1, {
        align: 'end',
        behavior: 'auto',
      })
      requestAnimationFrame(() => {
        requestAnimationFrame(() => {
          isAutoScrollingRef.current = false
        })
      })
    }
  }, [filteredLogs.length, autoScroll, rowVirtualizer])

  const toolbarContent = (
    <Collapsible open={filtersOpen} onOpenChange={handleFiltersOpenChange}>
      <div className="grid w-full gap-2 lg:grid-cols-[minmax(0,1fr)_minmax(620px,760px)]">
        {/* 模块 Tag 选择栏 */}
        <div
          className={cn(
            'flex min-h-9 min-w-0 flex-wrap content-start items-start gap-1.5',
            'border-border/80 bg-background/35 overflow-y-auto border p-1.5',
            '[scrollbar-gutter:stable]',
            filtersOpen ? 'max-h-[104px] lg:h-0 lg:max-h-none lg:min-h-full' : 'h-10 max-h-10'
          )}
          aria-label="模块显示筛选"
        >
          {availableModules.map(({ id, displayName }) => {
            const isVisible = !hiddenModules.has(id)
            return (
              <button
                key={id}
                type="button"
                aria-pressed={isVisible}
                aria-label={`${isVisible ? '隐藏' : '显示'} ${displayName}`}
                title={`${isVisible ? '隐藏' : '显示'} ${displayName}（${id}）`}
                onClick={() => toggleModule(id)}
                className={cn(
                  'h-7 max-w-full border px-2 text-xs font-medium transition-colors',
                  'focus-visible:ring-ring focus-visible:ring-2 focus-visible:ring-offset-1 focus-visible:outline-none',
                  'active:translate-y-px',
                  isVisible
                    ? 'border-primary/60 bg-primary/10 text-foreground hover:bg-primary/20'
                    : 'border-border/60 bg-muted/30 text-muted-foreground/45 hover:text-muted-foreground'
                )}
              >
                <span className="block max-w-40 truncate">{displayName}</span>
              </button>
            )
          })}
        </div>

        {/* 控制按钮与搜索栏 */}
        <div className="flex min-w-0 flex-col gap-2">
          <div className="flex w-full flex-wrap items-center gap-1.5 lg:justify-end">
            <div className="relative min-w-[180px] flex-1 lg:max-w-64">
              <Search className="text-muted-foreground absolute top-1/2 left-2 h-3.5 w-3.5 -translate-y-1/2" />
              <Input
                placeholder="搜索日志..."
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                onKeyDown={handleSearchKeyDown}
                className="h-8 pr-8 pl-8 text-xs sm:text-sm"
              />
              {search && (
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  onClick={() => setSearch('')}
                  className="absolute top-1/2 right-0.5 h-7 w-7 -translate-y-1/2"
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
              onClick={handleToggleAutoScroll}
              className="h-8 px-2"
              title={autoScroll ? '自动滚动' : '已暂停'}
            >
              {autoScroll ? <Play className="h-3.5 w-3.5" /> : <Pause className="h-3.5 w-3.5" />}
              <span className="ml-1 text-xs">{autoScroll ? '滚动' : '暂停'}</span>
            </Button>

            <Button
              variant="outline"
              size="sm"
              onClick={clearLogs}
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
              onClick={exportLogs}
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

            <div className="text-muted-foreground ml-auto flex items-center gap-2 text-xs whitespace-nowrap lg:ml-1">
              <span className="flex items-center gap-1.5">
                <span
                  className={cn(
                    'h-2 w-2 rounded-full',
                    isConnected ? 'animate-pulse bg-green-500' : 'bg-red-500'
                  )}
                />
                {isConnected ? '已连接' : '未连接'}
              </span>
              <span>
                <span className="font-mono">
                  {filteredLogs.length} / {logs.length}
                </span>
                <span className="ml-1">条日志</span>
              </span>
            </div>
          </div>

          {/* 折叠的高级筛选 */}
          <CollapsibleContent className="w-full space-y-2">
            <div className="flex flex-col gap-2 sm:flex-row sm:gap-2">
              <Select
                value={levelFilter}
                onValueChange={(value) => handleLevelFilterChange(value as LogLevelFilter)}
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

              <Button
                variant="outline"
                size="sm"
                onClick={resetAllFilters}
                className="h-8 w-full sm:w-auto"
                title="重置筛选"
              >
                <X className="h-3.5 w-3.5 sm:mr-1" />
                <span className="text-xs">重置</span>
              </Button>
            </div>

            <div className="flex flex-col gap-2 sm:flex-row sm:gap-2">
              <Popover>
                <PopoverTrigger asChild>
                  <Button
                    variant="outline"
                    size="sm"
                    className={cn(
                      'h-8 w-full justify-start text-left font-normal sm:flex-1',
                      !dateRange.from && 'text-muted-foreground'
                    )}
                  >
                    <CalendarIcon className="mr-1.5 h-3.5 w-3.5" />
                    <span className="text-xs">
                      {dateRange.from ? (
                        format(dateRange.from, 'PP', { locale: zhCN })
                      ) : (
                        '开始日期'
                      )}
                    </span>
                  </Button>
                </PopoverTrigger>
                <PopoverContent className="w-auto p-0" align="start">
                  <Calendar
                    mode="single"
                    selected={dateRange.from}
                    onSelect={(date) => setDateRange((prev) => ({ ...prev, from: date }))}
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
                      'h-8 w-full justify-start text-left font-normal sm:flex-1',
                      !dateRange.to && 'text-muted-foreground'
                    )}
                  >
                    <CalendarIcon className="mr-1.5 h-3.5 w-3.5" />
                    <span className="text-xs">
                      {dateRange.to ? (
                        format(dateRange.to, 'PP', { locale: zhCN })
                      ) : (
                        '结束日期'
                      )}
                    </span>
                  </Button>
                </PopoverTrigger>
                <PopoverContent className="w-auto p-0" align="start">
                  <Calendar
                    mode="single"
                    selected={dateRange.to}
                    onSelect={(date) => setDateRange((prev) => ({ ...prev, to: date }))}
                    initialFocus
                    locale={zhCN}
                  />
                </PopoverContent>
              </Popover>

              {(dateRange.from || dateRange.to) && (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={clearDateRange}
                  className="h-8 w-full sm:w-auto"
                >
                  <X className="h-3.5 w-3.5 sm:mr-1" />
                  <span className="text-xs">清除</span>
                </Button>
              )}
            </div>

            {/* 布局与外观微调控制条 */}
            <div className="border-border/50 flex flex-col gap-2 border-t pt-2 sm:flex-row sm:items-center sm:gap-3">
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
                          <span className={cn('text-[10px] font-semibold', getLevelColor(log.level))}>
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
                          {formatLogModule(log)}
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
                      <div className={cn('hidden items-start sm:flex', activeLayout.gapClass)}>
                        <span
                          className={cn(
                            'flex-shrink-0 text-gray-500 dark:text-gray-600',
                            activeLayout.timestampClass
                          )}
                          style={{ width: dynamicTimestampWidth }}
                        >
                          {formattedTimestamp}
                        </span>
                        <span
                          className={cn(
                            'flex-shrink-0 font-semibold',
                            activeLayout.levelClass,
                            getLevelColor(log.level)
                          )}
                          style={{ width: dynamicLevelWidth }}
                        >
                          [{formattedLevel}]
                        </span>
                        <span
                          className={cn(
                            'flex-shrink-0 truncate',
                            activeLayout.moduleClass,
                            !moduleStyle && 'text-cyan-400 dark:text-cyan-500'
                          )}
                          style={{ ...moduleStyle, width: dynamicModuleWidth }}
                        >
                          {formatLogModule(log)}
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

export interface LogViewerPageProps {
  defaultTab?: LogViewerTab
}

export function LogViewerPage({ defaultTab }: LogViewerPageProps) {
  const [activeTab, setActiveTab] = useState<LogViewerTab>(() => defaultTab ?? getInitialActiveTab())
  const [topbarTabsRoot, setTopbarTabsRoot] = useState<HTMLElement | null>(null)
  const [compactSwitcher, setCompactSwitcher] = useState(false)
  const compactSwitcherRef = useRef(false)
  const [reasoningToolbarVisible, setReasoningToolbarVisible] = useState(activeTab === 'reasoning')
  const [showSwitchHint, setShowSwitchHint] = useState<boolean>(() => {
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
    compactSwitcherRef.current = compactSwitcher
  }, [compactSwitcher])

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
          setCompactSwitcher(false)
          return
        }

        const topbarRect = topbarTabsRoot.getBoundingClientRect()
        const switcherRect = switcherMeasure.getBoundingClientRect()
        const workspaceRect = workspaceTabs.getBoundingClientRect()
        const measureTabsRect = measureWorkspaceTabs.getBoundingClientRect()

        const availableSpace =
          workspaceRect.right - measureTabsRect.width - (topbarRect.left + switcherRect.width)
        const threshold = compactSwitcherRef.current
          ? TOPBAR_SWITCH_EXPAND_GAP
          : TOPBAR_SWITCH_COMPACT_GAP

        setCompactSwitcher(availableSpace < threshold)
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

  const renderTabSwitcher = (isTopbar = false, isCompact = false) => {
    const labelClass = isTopbar && isCompact ? 'sr-only' : undefined

    return (
      <div className="flex min-w-0 items-center gap-2">
        <TabsList
          data-log-viewer-switcher={isTopbar ? 'true' : undefined}
          data-log-viewer-switcher-compact={isTopbar && isCompact ? 'true' : undefined}
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
        {isTopbar && <div id={reasoningTopbarActionsContainerId} className="hidden items-center sm:flex" />}
      </div>
    )
  }

  const topbarTabsPortal = topbarTabsRoot
    ? createPortal(
        <>
          {renderTabSwitcher(true, compactSwitcher)}
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
        <TerminalView toolbarContainerId={toolbarContainerId} toolbarVisible={activeTab === 'terminal'} />
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
