import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import {
  Database,
  Loader2,
  RefreshCw,
  RotateCcw,
  SlidersHorizontal,
  Upload,
  CheckCircle2,
  CircleAlert,
  FolderOpen,
  HardDrive,
  X,
} from 'lucide-react'

import { MemoryDeleteDialog } from '@/components/memory/MemoryDeleteDialog'
import { MemoryEpisodeManager } from '@/components/memory/MemoryEpisodeManager'
import { MemoryMaintenanceManager } from '@/components/memory/MemoryMaintenanceManager'
import { MemoryMiniTabs } from '@/components/memory/MemoryMiniTabs'
import { MemoryProfileManager } from '@/components/memory/MemoryProfileManager'
import { MemoryTimelineManager } from '@/components/memory/MemoryTimelineManager'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Label } from '@/components/ui/label'
import { Tabs, TabsContent } from '@/components/ui/tabs'
import { Textarea } from '@/components/ui/textarea'
import { ThinkingIllustration } from '@/components/ui/thinking-illustration'
import { useToast } from '@/hooks/use-toast'
import { cn } from '@/lib/utils'
import {
  getMemoryImportChatTargets,
  type MemoryImportChatTargetPayload,
  type MemoryTimelineJumpTargetPayload,
} from '@/lib/memory-api'

import { useImportForm } from './knowledge-base/hooks/useImportForm'
import { useImportQueue } from './knowledge-base/hooks/useImportQueue'
import { useMemoryDelete } from './knowledge-base/hooks/useMemoryDelete'
import { useMemoryFeedback } from './knowledge-base/hooks/useMemoryFeedback'
import { useMemoryRuntimeConfig } from './knowledge-base/hooks/useMemoryRuntimeConfig'
import { useMemoryTuning } from './knowledge-base/hooks/useMemoryTuning'
import { DeleteTab } from './knowledge-base/tabs/DeleteTab'
import { FeedbackTab } from './knowledge-base/tabs/FeedbackTab'
import { ImportTab } from './knowledge-base/tabs/ImportTab'
import { TuningTab } from './knowledge-base/tabs/TuningTab'
import { KnowledgeGraphPage } from './knowledge-graph'

const MEMORY_QUICK_START_DISMISSED_KEY = 'memory-quick-start-dismissed'
type MemoryConsoleTab = 'graph' | 'timeline' | 'import' | 'tuning' | 'episodes' | 'profiles' | 'maintenance' | 'delete' | 'feedback'
type LoadableMemoryTab = Extract<MemoryConsoleTab, 'timeline' | 'import' | 'tuning' | 'delete' | 'feedback'>

const MEMORY_CONSOLE_TABS: MemoryConsoleTab[] = [
  'graph',
  'timeline',
  'import',
  'tuning',
  'episodes',
  'profiles',
  'maintenance',
  'delete',
  'feedback',
]

interface KnowledgeBaseDeepLinkState {
  tab: MemoryConsoleTab
  chatId?: string
  timeStart?: number
  timeEnd?: number
  episodeId?: string
  source?: string
  personId?: string
  taskId?: number
  operationId?: string
  maintenanceTarget?: string
}

function parseOptionalTimestampQuery(value: string | null): number | undefined {
  if (!value) {
    return undefined
  }
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : undefined
}

function readKnowledgeBaseDeepLink(): KnowledgeBaseDeepLinkState {
  if (typeof window === 'undefined') {
    return { tab: 'graph' }
  }
  const params = new URLSearchParams(window.location.search)
  const tabParam = params.get('tab') as MemoryConsoleTab | null
  const tab = tabParam && MEMORY_CONSOLE_TABS.includes(tabParam) ? tabParam : 'graph'
  const taskId = parseOptionalTimestampQuery(params.get('task_id'))
  return {
    tab,
    chatId: params.get('chat_id') || undefined,
    timeStart: parseOptionalTimestampQuery(params.get('from') ?? params.get('time_start')),
    timeEnd: parseOptionalTimestampQuery(params.get('to') ?? params.get('time_end')),
    episodeId: params.get('episode_id') || undefined,
    source: params.get('source') || undefined,
    personId: params.get('person_id') || undefined,
    taskId: taskId ? Math.floor(taskId) : undefined,
    operationId: params.get('operation_id') || undefined,
    maintenanceTarget: params.get('target') || undefined,
  }
}

function updateKnowledgeBaseDeepLink(tab: MemoryConsoleTab, updates: Record<string, string | number | undefined>) {
  if (typeof window === 'undefined') {
    return
  }
  const params = new URLSearchParams()
  params.set('tab', tab)
  Object.entries(updates).forEach(([key, value]) => {
    if (value !== undefined && String(value).trim()) {
      params.set(key, String(value))
    }
  })
  const nextUrl = `${window.location.pathname}?${params.toString()}${window.location.hash}`
  window.history.replaceState(null, '', nextUrl)
}

function readJumpParam(target: MemoryTimelineJumpTargetPayload, key: string): string {
  const value = target.params?.[key]
  if (value === undefined || value === null) {
    return ''
  }
  return String(value)
}

function readJumpNumber(target: MemoryTimelineJumpTargetPayload, key: string): number | undefined {
  const value = Number(readJumpParam(target, key))
  return Number.isFinite(value) ? value : undefined
}

export function KnowledgeBasePage() {
  const { toast } = useToast()
  const deepLinkRef = useRef<KnowledgeBaseDeepLinkState>(readKnowledgeBaseDeepLink())
  const [activeTab, setActiveTab] = useState<MemoryConsoleTab>(deepLinkRef.current.tab)
  const [quickStartVisible, setQuickStartVisible] = useState(() => {
    if (typeof window === 'undefined') {
      return true
    }
    return window.localStorage.getItem(MEMORY_QUICK_START_DISMISSED_KEY) !== 'true'
  })
  const [visitedMemoryTabs, setVisitedMemoryTabs] = useState<Set<MemoryConsoleTab>>(() => new Set(['graph', deepLinkRef.current.tab]))
  const [tabLoading, setTabLoading] = useState<Partial<Record<LoadableMemoryTab, boolean>>>({})
  const loadedPanelDataRef = useRef<Set<LoadableMemoryTab>>(new Set())
  const [timelineInitialChatId] = useState(deepLinkRef.current.chatId ?? '')
  const [timelineInitialTimeStart] = useState<number | undefined>(deepLinkRef.current.timeStart)
  const [timelineInitialTimeEnd] = useState<number | undefined>(deepLinkRef.current.timeEnd)
  const [episodeInitialTarget, setEpisodeInitialTarget] = useState({
    episodeId: deepLinkRef.current.episodeId ?? '',
    source: deepLinkRef.current.source ?? '',
    timeStart: deepLinkRef.current.timeStart,
    timeEnd: deepLinkRef.current.timeEnd,
  })
  const [profileInitialPersonId, setProfileInitialPersonId] = useState(deepLinkRef.current.personId ?? '')
  const [maintenanceInitialTarget, setMaintenanceInitialTarget] = useState(deepLinkRef.current.maintenanceTarget ?? '')

  // 聊天流列表供审计时间线面板使用（导入面板的聊天流由 useImportForm 自管）
  const [importChatTargets, setImportChatTargets] = useState<MemoryImportChatTargetPayload[]>([])
  const importQueue = useImportQueue({
    active: activeTab === 'import',
    // 重试沿用表单当前公共参数作 overrides（拆分前 retry 直接读这些 state）
    buildRetryOverrides: () => importForm.buildCommonImportPayload(),
  })
  const importForm = useImportForm({
    active: activeTab === 'import',
    onCreated: (taskId) => importQueue.afterCreated(taskId),
  })

  // 运行时配置：服务于概览区/图谱，默认即拉取（非懒加载）；自检与向量重建一并下沉
  const memoryRuntime = useMemoryRuntimeConfig()
  const { runtimeConfig } = memoryRuntime

  // 删除领域：来源/操作列表懒加载、操作详情、源选择、删除预览-执行（usePendingOperation）、恢复
  const memoryDelete = useMemoryDelete({
    active: activeTab === 'delete',
    initialSourceSearch: deepLinkRef.current.source ?? '',
    initialOperationSearch: deepLinkRef.current.operationId ?? '',
    initialOperationId: deepLinkRef.current.operationId ?? '',
  })

  // 纠错领域：纠错历史懒加载、任务详情、行为日志分页、回退；回退后刷新来源与运行时配置
  const memoryFeedback = useMemoryFeedback({
    active: activeTab === 'feedback',
    initialSearch: deepLinkRef.current.taskId ? String(deepLinkRef.current.taskId) : '',
    initialTaskId: deepLinkRef.current.taskId ?? 0,
    onRuntimeChanged: () => memoryRuntime.refreshRuntimeConfig(),
    onSourcesChanged: () => memoryDelete.refreshSources(),
  })

  // 调优领域：调优配置/任务列表懒加载、调优参数、创建任务、应用最佳；应用后刷新运行时配置
  const memoryTuning = useMemoryTuning({
    active: activeTab === 'tuning',
    onRuntimeChanged: () => memoryRuntime.refreshRuntimeConfig(),
  })

  const setPanelLoading = useCallback((tab: LoadableMemoryTab, value: boolean) => {
    setTabLoading((current) => ({ ...current, [tab]: value }))
  }, [])

  const loadChatTargets = useCallback(async () => {
    const chatTargetsResult = await getMemoryImportChatTargets()
    setImportChatTargets(chatTargetsResult.data ?? [])
    return chatTargetsResult.data ?? []
  }, [])

  const loadTimelinePanel = useCallback(async (force = false) => {
    if (!force && loadedPanelDataRef.current.has('timeline')) {
      return
    }
    try {
      setPanelLoading('timeline', true)
      await loadChatTargets()
      loadedPanelDataRef.current.add('timeline')
    } catch (error) {
      toast({
        title: '加载审计聊天流失败',
        description: error instanceof Error ? error.message : '未知错误',
        variant: 'destructive',
      })
    } finally {
      setPanelLoading('timeline', false)
    }
  }, [loadChatTargets, setPanelLoading, toast])

  // tuning/delete/feedback 数据已下沉到各领域 hook（useQuery enabled:active 懒加载），
  // 此处仅保留 timeline 面板的命令式加载（聊天流列表）
  const loadActiveTabData = useCallback(async (tab: MemoryConsoleTab, force = false) => {
    switch (tab) {
      case 'timeline':
        await loadTimelinePanel(force)
        break
      default:
        break
    }
  }, [loadTimelinePanel])

  const switchMemoryTab = useCallback((tab: MemoryConsoleTab, query: Record<string, string | number | undefined> = {}) => {
    setActiveTab(tab)
    updateKnowledgeBaseDeepLink(tab, query)
  }, [])

  const handleTimelineJump = useCallback((target: MemoryTimelineJumpTargetPayload) => {
    const tab = target.tab as MemoryConsoleTab
    if (!MEMORY_CONSOLE_TABS.includes(tab)) {
      return
    }

    if (tab === 'episodes') {
      const episodeId = readJumpParam(target, 'episode_id')
      const source = readJumpParam(target, 'source')
      const timeStart = readJumpNumber(target, 'time_start')
      const timeEnd = readJumpNumber(target, 'time_end')
      setEpisodeInitialTarget({
        episodeId,
        source,
        timeStart,
        timeEnd,
      })
      switchMemoryTab('episodes', { episode_id: episodeId, source, time_start: timeStart, time_end: timeEnd })
      return
    }

    if (tab === 'profiles') {
      const personId = readJumpParam(target, 'person_id')
      setProfileInitialPersonId(personId)
      switchMemoryTab('profiles', { person_id: personId })
      return
    }

    if (tab === 'feedback') {
      const taskId = Math.floor(readJumpNumber(target, 'task_id') ?? 0)
      if (taskId > 0) {
        memoryFeedback.setSelectedFeedbackTaskId(taskId)
        memoryFeedback.setFeedbackSearch(String(taskId))
        memoryFeedback.setFeedbackActionLogPage(1)
      }
      switchMemoryTab('feedback', { task_id: taskId > 0 ? taskId : undefined })
      // 纠错数据由 useMemoryFeedback 自管加载（enabled:active），切到该 tab 即触发拉取
      return
    }

    if (tab === 'delete') {
      const operationId = readJumpParam(target, 'operation_id')
      const source = readJumpParam(target, 'source')
      const paragraphHash = readJumpParam(target, 'paragraph_hash')
      if (operationId) {
        memoryDelete.setSelectedOperationId(operationId)
        memoryDelete.setOperationSearch(operationId)
        switchMemoryTab('delete', { operation_id: operationId })
      } else {
        memoryDelete.setSourceSearch(source || paragraphHash)
        memoryDelete.setOperationSearch(source || paragraphHash)
        switchMemoryTab('delete', { source: source || undefined })
      }
      // 删除数据由 useMemoryDelete 自管加载（enabled:active），切到该 tab 即触发拉取
      return
    }

    if (tab === 'maintenance') {
      const targetText = readJumpParam(target, 'target')
      setMaintenanceInitialTarget(targetText)
      switchMemoryTab('maintenance', { target: targetText })
      return
    }

    switchMemoryTab(tab)
  }, [memoryDelete, memoryFeedback, switchMemoryTab])

  const loadPage = useCallback(async () => {
    try {
      await memoryRuntime.refreshRuntimeConfig()
      await loadActiveTabData(activeTab, true)
    } catch (error) {
      toast({
        title: '加载长期记忆控制台失败',
        description: error instanceof Error ? error.message : '未知错误',
        variant: 'destructive',
      })
    }
  }, [activeTab, loadActiveTabData, memoryRuntime, toast])

  useEffect(() => {
    setVisitedMemoryTabs((current) => {
      if (current.has(activeTab)) {
        return current
      }
      const next = new Set(current)
      next.add(activeTab)
      return next
    })
    void loadActiveTabData(activeTab)
  }, [activeTab, loadActiveTabData])

  const runtimeBadges = useMemo(() => {
    if (!runtimeConfig) {
      return []
    }
    return [
      {
        label: '运行状态',
        value: runtimeConfig.runtime_ready ? '就绪' : '未就绪',
        description: runtimeConfig.embedding_degraded ? 'Embedding 降级运行' : '运行时检查通过',
        icon: runtimeConfig.runtime_ready ? CheckCircle2 : CircleAlert,
        className: runtimeConfig.runtime_ready ? 'border-emerald-500/20 bg-emerald-500/5' : 'border-amber-500/20 bg-amber-500/5',
        iconClassName: runtimeConfig.runtime_ready ? 'text-emerald-500' : 'text-amber-500',
      },
      {
        label: 'Embedding 维度',
        value: String(runtimeConfig.embedding_dimension),
        description: runtimeConfig.relation_vectors_enabled ? '关系向量已启用' : '关系向量未启用',
        icon: HardDrive,
        className: 'border-sky-500/20 bg-sky-500/5',
        iconClassName: 'text-sky-500',
      },
      {
        label: '数据目录',
        value: runtimeConfig.data_dir,
        description: '长期记忆存储位置',
        icon: FolderOpen,
        className: 'border-violet-500/20 bg-violet-500/5',
        iconClassName: 'text-violet-500',
      },
    ]
  }, [runtimeConfig])

  const dismissQuickStart = useCallback(() => {
    window.localStorage.setItem(MEMORY_QUICK_START_DISMISSED_KEY, 'true')
    setQuickStartVisible(false)
  }, [])

  const shouldRenderMemoryTab = (tab: MemoryConsoleTab) => activeTab === tab || visitedMemoryTabs.has(tab)
  const shouldShowPanelFallback = (tab: LoadableMemoryTab) => !loadedPanelDataRef.current.has(tab)
  const renderPanelFallback = (tab: LoadableMemoryTab) => (
    <TabsContent value={tab} className="space-y-4">
      <div className="flex min-h-[240px] items-center justify-center rounded-xl border bg-background/70 text-sm text-muted-foreground">
        <ThinkingIllustration size={tabLoading[tab] ? 'md' : 'sm'} />
      </div>
    </TabsContent>
  )

  if (memoryRuntime.runtimeLoading) {
    return (
      <div className="flex h-full items-center justify-center">
        <div className="rounded-xl border bg-background px-6 py-4 shadow-sm">
          <ThinkingIllustration size="lg" />
        </div>
      </div>
    )
  }

  return (
    <div className="flex h-full flex-col bg-background">
      <div className="flex-1 overflow-auto">
        <div className="memory-console-density mx-auto flex w-full max-w-[1800px] flex-col gap-4 px-4 py-4 xl:px-5">
          <div className="hidden">
            <Button variant="outline" size="sm" onClick={() => void loadPage()}>
              <RefreshCw className="mr-2 h-4 w-4" />
              刷新数据
            </Button>
          </div>
          {/* 运行时状态条 —— 紧凑、常驻、一眼看完 */}
          {runtimeBadges.length > 0 ? (
            <div className="rounded-xl border border-border/60 bg-card/60 p-3 shadow-sm backdrop-blur">
              <div className="mb-2 flex items-center justify-end gap-2">
                {runtimeConfig?.vector_rebuild_required ? (
                  <Button
                    variant="destructive"
                    size="sm"
                    className="h-6 px-2 text-[11px]"
                    onClick={() => void memoryRuntime.openVectorRebuildDialog()}
                    disabled={memoryRuntime.vectorRebuilding}
                  >
                    <RotateCcw className={cn('mr-1 h-3 w-3', memoryRuntime.vectorRebuilding && 'animate-spin')} />
                    重建向量
                  </Button>
                ) : null}
                <Button
                  variant="outline"
                  size="sm"
                  className="h-6 px-2 text-[11px]"
                  onClick={() => void loadPage()}
                >
                  <RefreshCw className="mr-1 h-3 w-3" />
                  刷新数据
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-6 px-2 text-[11px]"
                  onClick={() => void memoryRuntime.refreshSelfCheck()}
                  disabled={memoryRuntime.refreshingCheck}
                >
                  <RefreshCw className={cn('mr-1 h-3 w-3', memoryRuntime.refreshingCheck && 'animate-spin')} />
                  自检
                </Button>
              </div>
              <div className="grid grid-cols-3 gap-1.5 sm:gap-2">
                {runtimeBadges.map((item) => (
                  <div
                    key={item.label}
                    className={cn(
                      'min-w-0 overflow-hidden rounded-lg border px-2 py-1.5 transition-colors sm:flex sm:items-center sm:gap-2 sm:px-2.5',
                      item.className,
                    )}
                  >
                    <div className="mb-1 w-fit flex-none rounded-md border bg-background/70 p-1 shadow-sm sm:mb-0">
                      <item.icon className={cn('h-3.5 w-3.5', item.iconClassName)} />
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-[10px] font-medium leading-tight text-muted-foreground">
                        {item.label}
                      </div>
                      <div className="truncate text-xs font-semibold leading-tight" title={item.value}>
                        {item.value}
                      </div>
                      <div className="mt-0.5 hidden truncate text-[10px] text-muted-foreground xl:block">
                        {item.description}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ) : null}

          <Dialog open={memoryRuntime.vectorRebuildDialogOpen} onOpenChange={memoryRuntime.setVectorRebuildDialogOpen}>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>重建全部向量</DialogTitle>
                <DialogDescription>
                  将使用当前 embedding 配置重新生成段落、实体和已启用的关系向量，期间检索会临时降级（会对嵌入模型造成大量请求！）
                </DialogDescription>
              </DialogHeader>
              <div className="space-y-3 text-sm">
                <Alert variant={runtimeConfig?.vector_rebuild_required ? 'destructive' : 'default'}>
                  <AlertDescription>
                    {runtimeConfig?.vector_rebuild_message || '这个操作会替换现有向量库，适合更换 embedding 模型或维度后执行。'}
                  </AlertDescription>
                </Alert>
                <div className="grid gap-2 sm:grid-cols-3">
                  {(['paragraphs', 'entities', 'relations'] as const).map((key) => (
                    <div key={key} className="rounded-lg border bg-muted/30 p-3">
                      <div className="text-xs text-muted-foreground">
                        {key === 'paragraphs' ? '段落' : key === 'entities' ? '实体' : '关系'}
                      </div>
                      <div className="mt-1 text-xl font-semibold">{memoryRuntime.vectorRebuildPreview?.[key] ?? '-'}</div>
                    </div>
                  ))}
                </div>
              </div>
              <DialogFooter>
                <Button variant="outline" onClick={() => memoryRuntime.setVectorRebuildDialogOpen(false)} disabled={memoryRuntime.vectorRebuilding}>
                  取消
                </Button>
                <Button variant="destructive" onClick={() => void memoryRuntime.confirmVectorRebuild()} disabled={memoryRuntime.vectorRebuilding}>
                  <RotateCcw className={cn('mr-2 h-4 w-4', memoryRuntime.vectorRebuilding && 'animate-spin')} />
                  确认重建
                </Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>

          {/* 快速开始 Hero —— 给新用户明确的"先做什么" */}
          {quickStartVisible && (
            <div className="relative overflow-hidden rounded-xl border border-primary/20 bg-gradient-to-br from-primary/10 via-primary/5 to-transparent p-4 pr-11 shadow-sm">
              <Button
                type="button"
                variant="ghost"
                size="icon"
                className="absolute right-3 top-3 h-7 w-7 text-muted-foreground hover:text-foreground"
                onClick={dismissQuickStart}
                aria-label="关闭快速开始"
                title="关闭快速开始"
              >
                <X className="h-4 w-4" />
              </Button>
              <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
              <div className="space-y-1.5 lg:max-w-sm">
                <div className="text-[11px] font-medium uppercase tracking-[0.18em] text-primary">
                  快速开始
                </div>
                <h2 className="text-lg font-semibold leading-tight">先从这三件事入手</h2>
                <p className="text-sm text-muted-foreground">
                  不知道该做什么？挑一个最常用的入口，下面的标签页里有更详细的设置。
                </p>
              </div>
              <div className="grid w-full gap-2 sm:grid-cols-3 lg:max-w-3xl">
                <button
                  type="button"
                  onClick={() => switchMemoryTab('import')}
                  className="group flex items-start gap-2 rounded-lg border border-border/70 bg-background/80 p-3 text-left transition hover:border-primary/50 hover:bg-background hover:shadow-md"
                >
                  <div className="flex-none rounded-lg bg-primary/10 p-2 text-primary transition-transform group-hover:scale-105">
                    <Upload className="h-4 w-4" />
                  </div>
                  <div className="min-w-0">
                    <div className="text-sm font-semibold">导入资料</div>
                    <div className="mt-0.5 text-xs leading-relaxed text-muted-foreground">
                      把文件、聊天记录写进记忆库
                    </div>
                  </div>
                </button>
                <button
                  type="button"
                  onClick={() => switchMemoryTab('tuning')}
                  className="group flex items-start gap-2 rounded-lg border border-border/70 bg-background/80 p-3 text-left transition hover:border-primary/50 hover:bg-background hover:shadow-md"
                >
                  <div className="flex-none rounded-lg bg-amber-500/10 p-2 text-amber-500 transition-transform group-hover:scale-105">
                    <SlidersHorizontal className="h-4 w-4" />
                  </div>
                  <div className="min-w-0">
                    <div className="text-sm font-semibold">检索调优</div>
                    <div className="mt-0.5 text-xs leading-relaxed text-muted-foreground">
                      让回忆变得更准、更聪明
                    </div>
                  </div>
                </button>
                <button
                  type="button"
                  onClick={() => switchMemoryTab('graph')}
                  className="group flex items-start gap-2 rounded-lg border border-border/70 bg-background/80 p-3 text-left transition hover:border-primary/50 hover:bg-background hover:shadow-md"
                >
                  <div className="flex-none rounded-lg bg-violet-500/10 p-2 text-violet-500 transition-transform group-hover:scale-105">
                    <Database className="h-4 w-4" />
                  </div>
                  <div className="min-w-0">
                    <div className="text-sm font-semibold">打开图谱</div>
                    <div className="mt-0.5 text-xs leading-relaxed text-muted-foreground">
                      可视化已存的实体和关系
                    </div>
                  </div>
                </button>
              </div>
              </div>
            </div>
          )}

          <Tabs
            value={activeTab}
            onValueChange={(value) => switchMemoryTab(value as MemoryConsoleTab)}
            className="space-y-3"
          >
            <div className="-mx-4 border-b border-border/40 px-4 pb-1.5 pt-0 xl:-mx-5 xl:px-5">
              <div className="flex flex-wrap items-center gap-2">
                <MemoryMiniTabs
                  items={[
                    { value: 'graph', label: '图谱', description: '实体关系图与证据视图' },
                    { value: 'timeline', label: '审计时间线', description: '核对聊天流记忆变动' },
                    { value: 'tuning', label: '调优', description: '检索策略调优' },
                    { value: 'episodes', label: '情景记忆', description: '查看和重建情景记忆' },
                    { value: 'profiles', label: '人物画像', description: '查询和维护人物画像' },
                  ]}
                  className="h-[30px] w-fit max-w-full"
                  triggerClassName="px-3"
                />
                <MemoryMiniTabs
                  items={[
                    { value: 'import', label: '导入', description: '创建并管理导入任务' },
                    { value: 'maintenance', label: '维护', description: '回收站与记忆状态维护' },
                    { value: 'delete', label: '删除', description: '批量删除与历史回溯' },
                    { value: 'feedback', label: '纠错历史', description: '查看反馈与回滚' },
                  ]}
                  className="h-[30px] w-fit max-w-full"
                  triggerClassName="px-3"
                />
              </div>
            </div>

            <TabsContent value="graph" className="h-[calc(100vh-132px)] min-h-[820px] overflow-hidden rounded-2xl border border-border/60 bg-background shadow-sm">
              <KnowledgeGraphPage embedded onOpenConsole={() => switchMemoryTab('import')} />
            </TabsContent>

            {shouldRenderMemoryTab('timeline') && (shouldShowPanelFallback('timeline') ? renderPanelFallback('timeline') : (
            <TabsContent value="timeline" className="space-y-4">
              <MemoryTimelineManager
                chatTargets={importChatTargets}
                initialChatId={timelineInitialChatId}
                initialTimeStart={timelineInitialTimeStart}
                initialTimeEnd={timelineInitialTimeEnd}
                onJump={handleTimelineJump}
              />
            </TabsContent>
            ))}

            {/* 导入面板的数据由 useImportQueue/useImportForm 自管加载（useQuery enabled:active），
                不再走 loadedPanelDataRef 懒加载门控；表单即时可交互，任务列表异步填充 */}
            {shouldRenderMemoryTab('import') && (
            <ImportTab queue={importQueue} form={importForm} />
            )}

            {/* 调优面板数据由 useMemoryTuning 自管加载（enabled:active），不再走懒加载占位门控 */}
            {shouldRenderMemoryTab('tuning') && (
            <TuningTab tuning={memoryTuning} />
            )}

            <TabsContent value="episodes" className="space-y-4">
              {shouldRenderMemoryTab('episodes') ? (
                <MemoryEpisodeManager
                  initialEpisodeId={episodeInitialTarget.episodeId}
                  initialSource={episodeInitialTarget.source}
                  initialTimeStart={episodeInitialTarget.timeStart}
                  initialTimeEnd={episodeInitialTarget.timeEnd}
                />
              ) : null}
            </TabsContent>

            <TabsContent value="profiles" className="space-y-4">
              {shouldRenderMemoryTab('profiles') ? <MemoryProfileManager initialPersonId={profileInitialPersonId} /> : null}
            </TabsContent>

            <TabsContent value="maintenance" className="space-y-4">
              {shouldRenderMemoryTab('maintenance') ? <MemoryMaintenanceManager initialTarget={maintenanceInitialTarget} /> : null}
            </TabsContent>

            {/* 删除面板数据由 useMemoryDelete 自管加载（enabled:active），不再走懒加载占位门控 */}
            {shouldRenderMemoryTab('delete') && (
            <DeleteTab delete={memoryDelete} />
            )}

            {/* 纠错面板数据由 useMemoryFeedback 自管加载（enabled:active），不再走懒加载占位门控 */}
            {shouldRenderMemoryTab('feedback') && (
            <FeedbackTab feedback={memoryFeedback} />
            )}
          </Tabs>
        </div>
      </div>

      <MemoryDeleteDialog
        open={memoryDelete.deleteDialogOpen}
        onOpenChange={memoryDelete.closeDeleteDialog}
        title={memoryDelete.deleteDialogTitle}
        description={memoryDelete.deleteDialogDescription}
        preview={memoryDelete.deletePreview}
        result={memoryDelete.deleteResult}
        loadingPreview={memoryDelete.deletePreviewLoading}
        executing={memoryDelete.deleteExecuting}
        restoring={memoryDelete.deleteRestoring}
        error={memoryDelete.deletePreviewError}
        onExecute={() => void memoryDelete.executePendingDelete()}
        onRestore={() => void (memoryDelete.deleteResult?.operation_id ? memoryDelete.restoreDeleteOperation(memoryDelete.deleteResult.operation_id) : Promise.resolve())}
      />

      <Dialog open={memoryFeedback.feedbackRollbackDialogOpen} onOpenChange={memoryFeedback.setFeedbackRollbackDialogOpen}>
        <DialogContent className="max-w-lg" confirmOnEnter>
          <DialogHeader>
            <DialogTitle>回退本次纠错</DialogTitle>
            <DialogDescription>
              这会恢复旧关系状态、隐藏本次纠错写入的段落，并重新触发 Episode / Profile 的异步修复。
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div className="rounded-lg border bg-muted/20 p-3 text-sm">
              <div className="font-medium break-words">{memoryFeedback.selectedFeedbackResolved?.query_text || '无查询文本'}</div>
              <div className="mt-1 font-mono text-[11px] break-all text-muted-foreground">
                {memoryFeedback.selectedFeedbackResolved?.query_tool_id}
              </div>
            </div>
            <div className="space-y-2">
              <Label htmlFor="feedback-rollback-reason">回退原因</Label>
              <Textarea
                id="feedback-rollback-reason"
                value={memoryFeedback.feedbackRollbackReason}
                onChange={(event) => memoryFeedback.setFeedbackRollbackReason(event.target.value)}
                placeholder="可选，建议填写本次人工回退原因"
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => memoryFeedback.setFeedbackRollbackDialogOpen(false)} disabled={memoryFeedback.feedbackRollingBack}>
              取消
            </Button>
            <Button onClick={() => void memoryFeedback.executeFeedbackRollback()} disabled={memoryFeedback.feedbackRollingBack}>
              {memoryFeedback.feedbackRollingBack ? (
                <>
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  回退中
                </>
              ) : (
                <>
                  <RotateCcw className="mr-2 h-4 w-4" />
                  确认回退
                </>
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
