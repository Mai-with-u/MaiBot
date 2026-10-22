import { startTransition, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useSearch } from '@tanstack/react-router'
import { useQueryClient } from '@tanstack/react-query'
import { useReducedMotion } from 'motion/react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import { ThinkingIllustration } from '@/components/ui/thinking-illustration'
import { AlertCircle, AlertTriangle, ArrowUpDown, Filter, Info, Loader2, Search, Wrench } from 'lucide-react'

import { RestartOverlay } from '@/components/restart-overlay'
import { useToast } from '@/hooks/use-toast'
import { RestartProvider } from '@/lib/restart-context'
import {
  checkGitStatus,
  connectPluginProgressWebSocket,
  fetchPluginList,
  getCachedPluginList,
  getInstalledPlugins,
  getMaimaiVersion,
  installPlugin,
  isPluginCompatible,
  uninstallPlugin,
  updatePlugin,
  type InstalledPlugin,
} from '@/lib/plugin-api'
import {
  getPluginUserStates,
  likePlugin,
  recordPluginDownload,
  type PluginStatsData,
  type PluginVoteState,
} from '@/lib/plugin-stats'
import { mergeInstalledPluginInfo } from '@/lib/plugin-info'
import { PLUGIN_MARKET_VIEW_STATE_KEY } from '@/lib/plugin-market-navigation'

import { PluginInstallDialog, type PluginInstallDialogHandle } from './PluginInstallDialog'
import { MarketplaceTab } from './MarketplaceTab'
import { filterMarketplacePlugins } from './marketplace-filter'
import { PluginSettingsDialog } from './PluginSettingsDialog'
import type {
  GitStatus,
  MaimaiVersion,
  MarketplaceSortKey,
  PluginInfo,
  PluginLoadProgress,
  PluginProgressById,
} from './types'
import { clearPluginProgress, mergePluginProgress, PLUGIN_TYPE_OPTIONS } from './types'
import { PluginDetailPage } from '../plugin-detail'

const PLUGIN_MARKET_COMPATIBLE_ONLY_KEY = 'plugins-market-compatible-only'
const PLUGIN_MARKET_SCROLL_TOP_KEY = 'plugins-market-scroll-top'
const MARKETPLACE_SORT_KEYS: MarketplaceSortKey[] = ['default', 'latest', 'downloads', 'likes', 'rating']

interface PluginMarketplaceViewState {
  searchQuery: string
  pluginTypeFilter: string
  marketplaceSortBy: MarketplaceSortKey
}

const DEFAULT_PLUGIN_MARKET_VIEW_STATE: PluginMarketplaceViewState = {
  searchQuery: '',
  pluginTypeFilter: 'all',
  marketplaceSortBy: 'default',
}

interface PluginMarketplacePageProps {
  embedded?: boolean
}

type PluginVoteOverlay = PluginVoteState & Partial<Pick<PluginStatsData, 'likes' | 'dislikes'>>

const buildPluginStatsMap = (
  pluginList: PluginInfo[]
): Record<string, PluginStatsData> => {
  const statsMap: Record<string, PluginStatsData> = {}

  for (const plugin of pluginList) {
    const stats = plugin.marketplace_stats
    if (!stats) {
      continue
    }

    const statsIds = [
      plugin.manifest?.id,
      stats.plugin_id,
    ].filter((id): id is string => Boolean(id))

    for (const statsId of statsIds) {
      statsMap[statsId] = stats
    }
  }

  return statsMap
}

const readPluginMarketplaceViewState = (): PluginMarketplaceViewState => {
  const savedState = sessionStorage.getItem(PLUGIN_MARKET_VIEW_STATE_KEY)
  if (!savedState) {
    return DEFAULT_PLUGIN_MARKET_VIEW_STATE
  }

  const parsed = JSON.parse(savedState) as Partial<PluginMarketplaceViewState>
  const pluginTypeFilter = typeof parsed.pluginTypeFilter === 'string'
    && (parsed.pluginTypeFilter === 'all' || PLUGIN_TYPE_OPTIONS.some(option => option.value === parsed.pluginTypeFilter))
    ? parsed.pluginTypeFilter
    : DEFAULT_PLUGIN_MARKET_VIEW_STATE.pluginTypeFilter
  const marketplaceSortBy = parsed.marketplaceSortBy
    && MARKETPLACE_SORT_KEYS.includes(parsed.marketplaceSortBy)
    ? parsed.marketplaceSortBy
    : DEFAULT_PLUGIN_MARKET_VIEW_STATE.marketplaceSortBy

  return {
    searchQuery: typeof parsed.searchQuery === 'string'
      ? parsed.searchQuery
      : DEFAULT_PLUGIN_MARKET_VIEW_STATE.searchQuery,
    pluginTypeFilter,
    marketplaceSortBy,
  }
}

// 插件市场页：只展示市场索引、安装状态和版本信息
export function PluginMarketplacePage({ embedded = false }: PluginMarketplacePageProps) {
  return (
    <RestartProvider>
      <PluginMarketplacePageContent embedded={embedded} />
    </RestartProvider>
  )
}

// 内部组件：实际内容
function PluginMarketplacePageContent({ embedded }: Required<PluginMarketplacePageProps>) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const scrollViewportRef = useRef<HTMLDivElement | null>(null)
  const scrollRestoredRef = useRef(false)
  const initialViewStateRef = useRef(readPluginMarketplaceViewState())
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [sourceRevision, setSourceRevision] = useState(0)
  const [restartNoticeVisible, setRestartNoticeVisible] = useState(
    () => localStorage.getItem('plugins-restart-notice-dismissed') !== 'true'
  )
  const [searchQuery, setSearchQuery] = useState(initialViewStateRef.current.searchQuery)
  const [pluginTypeFilter, setPluginTypeFilter] = useState(initialViewStateRef.current.pluginTypeFilter)
  const [marketplaceSortBy, setMarketplaceSortBy] = useState<MarketplaceSortKey>(
    initialViewStateRef.current.marketplaceSortBy
  )
  const [showCompatibleOnly, setShowCompatibleOnly] = useState(
    () => localStorage.getItem(PLUGIN_MARKET_COMPATIBLE_ONLY_KEY) !== 'false'
  )
  const [plugins, setPlugins] = useState<PluginInfo[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [gitStatus, setGitStatus] = useState<GitStatus | null>(null)
  const [marketplaceProgress, setMarketplaceProgress] = useState<PluginLoadProgress | null>(null)
  const [pluginProgressById, setPluginProgressById] = useState<PluginProgressById>({})
  const [maimaiVersion, setMaimaiVersion] = useState<MaimaiVersion | null>(null)
  const [installedPlugins, setInstalledPlugins] = useState<InstalledPlugin[]>([])
  const syncInstalledPlugins = useCallback((installed: InstalledPlugin[]) => {
    // 详情面板关闭后仍然挂载，刷新市场列表时也要同步它订阅的安装状态缓存。
    queryClient.setQueryData(['plugin-installed-list'], installed)
    setInstalledPlugins(installed)
  }, [queryClient])
  // 安装、更新、卸载统一刷新，避免各自维护一份状态合并逻辑。
  const refreshInstalledPlugins = useCallback(async () => {
    const installed = await getInstalledPlugins({ forceRefresh: true })
    syncInstalledPlugins(installed)
    setPlugins(current => mergeInstalledPluginInfo(current, installed))
  }, [syncInstalledPlugins])
  const [pluginStats, setPluginStats] = useState<Record<string, PluginStatsData>>({})
  const [likingPluginIds, setLikingPluginIds] = useState<Set<string>>(() => new Set())
  // 请求去重使用即时更新的集合，避免连点抢在 React 提交之前发出重复请求。
  const pendingLikesRef = useRef(new Set<string>())
  const operationStateRef = useRef({ pluginProgressById, pluginStats })
  useLayoutEffect(() => {
    operationStateRef.current = { pluginProgressById, pluginStats }
  }, [pluginProgressById, pluginStats])
  // 本机用户的点赞状态与本次会话内的点赞结果，单独存放而不写回 pluginStats：
  // pluginStats 同时是排序依据，点赞后保持不变才能避免卡片当场换位。
  const [voteOverlay, setVoteOverlay] = useState<Record<string, PluginVoteOverlay>>({})
  const displayPluginStats = useMemo(() => {
    const mergedStats: Record<string, PluginStatsData> = {}

    for (const [statsId, stats] of Object.entries(pluginStats)) {
      const overlay = voteOverlay[statsId] ?? voteOverlay[stats.plugin_id]
      mergedStats[statsId] = overlay ? { ...stats, ...overlay } : stats
    }
    // 统计摘要里没有记录的插件，点赞后也要能显示点赞数
    for (const [statsId, overlay] of Object.entries(voteOverlay)) {
      if (!mergedStats[statsId]) {
        mergedStats[statsId] = {
          plugin_id: statsId,
          likes: 0,
          dislikes: 0,
          downloads: 0,
          rating: 0,
          rating_count: 0,
          comment_count: 0,
          ...overlay,
        }
      }
    }

    return mergedStats
  }, [pluginStats, voteOverlay])
  
  // 安装弹窗自行管理开关，点击操作不再更新市场页状态。
  const installDialogRef = useRef<PluginInstallDialogHandle>(null)
  // 详情写入地址，支持从资讯直达，以及刷新后保留当前插件。
  const search = useSearch({ strict: false }) as { pluginId?: string }
  const routeDetailPluginId = search.pluginId || null
  const [detailPluginId, setVisibleDetailPluginId] = useState<string | null>(routeDetailPluginId)
  const [lastDetailPluginId, setLastDetailPluginId] = useState<string | null>(detailPluginId)
  const displayedDetailPluginId = detailPluginId ?? lastDetailPluginId
  const reducedMotion = useReducedMotion()
  const [deferDetailContent, setDeferDetailContent] = useState(false)
  const previousDetailPluginIdRef = useRef(routeDetailPluginId)
  const pendingDetailNavigationRef = useRef<{ pluginId: string | null } | null>(null)
  const marketplacePaneRef = useRef<HTMLDivElement>(null)
  const detailPaneRef = useRef<HTMLDivElement>(null)
  const syncDetailAddress = useCallback((pluginId: string | null) => {
    startTransition(() => {
      void navigate({
        to: embedded ? '/plugins/embed' : '/plugins',
        search: { pluginId: pluginId || undefined },
        replace: true,
      })
    })
  }, [embedded, navigate])
  const setDetailPluginId = useCallback((pluginId: string | null) => {
    // 先准备滑动状态，避免进入过程中解析文档；返回后保留最近详情以免集中销毁 DOM。
    setDeferDetailContent(pluginId !== null && !reducedMotion)
    if (pluginId) setLastDetailPluginId(pluginId)
    setVisibleDetailPluginId(pluginId)
    // 点击只更新当前视图，路由工作推迟到滑动结束，避免阻塞动画首帧。
    if (reducedMotion) syncDetailAddress(pluginId)
    else pendingDetailNavigationRef.current = { pluginId }
  }, [reducedMotion, syncDetailAddress])
  
  useLayoutEffect(() => {
    if (reducedMotion) {
      setDeferDetailContent(false)
      const pending = pendingDetailNavigationRef.current
      if (pending) {
        pendingDetailNavigationRef.current = null
        syncDetailAddress(pending.pluginId)
      }
    }
    if (previousDetailPluginIdRef.current === routeDetailPluginId) return
    previousDetailPluginIdRef.current = routeDetailPluginId
    if (detailPluginId === routeDetailPluginId) return
    // 外部链接、浏览器历史仍可驱动当前视图。
    pendingDetailNavigationRef.current = null
    setVisibleDetailPluginId(routeDetailPluginId)
    if (routeDetailPluginId) setLastDetailPluginId(routeDetailPluginId)
    setDeferDetailContent(routeDetailPluginId !== null && !reducedMotion)
  }, [detailPluginId, routeDetailPluginId, reducedMotion, syncDetailAddress])

  const handleDetail = useCallback((plugin: PluginInfo) => setDetailPluginId(plugin.id), [setDetailPluginId])

  const { toast } = useToast()
  const isFetchingMarketplace = marketplaceProgress?.stage === 'loading'
    && marketplaceProgress.operation === 'fetch'

  const setPluginProgress = useCallback((progress: PluginLoadProgress) => {
    setPluginProgressById((currentProgress) => mergePluginProgress(currentProgress, progress))
  }, [])

  const setCompletedPluginProgress = useCallback((progress: PluginLoadProgress) => {
    setPluginProgress(progress)
    if (!progress.plugin_id) {
      return
    }

    const completedPluginId = progress.plugin_id
    setTimeout(() => {
      setPluginProgressById((currentProgress) => (
        clearPluginProgress(currentProgress, completedPluginId, progress)
      ))
    }, 2000)
  }, [setPluginProgress])

  const dismissRestartNotice = () => {
    localStorage.setItem('plugins-restart-notice-dismissed', 'true')
    setRestartNoticeVisible(false)
  }

  // 「仅显示兼容」开关：在市场页内直接切换的兼容性筛选偏好，变化后写回 localStorage
  useEffect(() => {
    localStorage.setItem(PLUGIN_MARKET_COMPATIBLE_ONLY_KEY, String(showCompatibleOnly))
  }, [showCompatibleOnly])

  useEffect(() => {
    sessionStorage.setItem(
      PLUGIN_MARKET_VIEW_STATE_KEY,
      JSON.stringify({
        searchQuery,
        pluginTypeFilter,
        marketplaceSortBy,
      } satisfies PluginMarketplaceViewState)
    )
  }, [marketplaceSortBy, pluginTypeFilter, searchQuery])

  useEffect(() => {
    const viewport = scrollViewportRef.current
    if (!viewport) {
      return
    }

    const handleScroll = () => {
      sessionStorage.setItem(PLUGIN_MARKET_SCROLL_TOP_KEY, String(viewport.scrollTop))
    }

    viewport.addEventListener('scroll', handleScroll, { passive: true })
    return () => {
      viewport.removeEventListener('scroll', handleScroll)
    }
  }, [])

  useEffect(() => {
    if (scrollRestoredRef.current || loading || (showCompatibleOnly && !maimaiVersion)) {
      return
    }

    const viewport = scrollViewportRef.current
    if (!viewport) {
      return
    }

    const savedScrollTop = Number(sessionStorage.getItem(PLUGIN_MARKET_SCROLL_TOP_KEY) ?? 0)
    if (!Number.isFinite(savedScrollTop) || savedScrollTop <= 0) {
      scrollRestoredRef.current = true
      return
    }

    const frameId = requestAnimationFrame(() => {
      viewport.scrollTop = savedScrollTop
      scrollRestoredRef.current = true
    })

    return () => {
      cancelAnimationFrame(frameId)
    }
  }, [loading, plugins.length, showCompatibleOnly, maimaiVersion])

  // 统一管理 WebSocket 和数据加载
  useEffect(() => {
    let unsubscribeProgress: (() => Promise<void>) | null = null
    let isUnmounted = false

    const init = async () => {
      const cachedPluginList = getCachedPluginList(showCompatibleOnly)
      // 已点赞状态只影响按钮样式，不阻塞清单加载；本次会话内已产生的点赞结果优先
      void getPluginUserStates().then((userStates) => {
        if (!isUnmounted) {
          setVoteOverlay((currentOverlay) => ({ ...userStates, ...currentOverlay }))
        }
      })
      if (cachedPluginList?.length && !isUnmounted) {
        setPlugins(cachedPluginList)
        setPluginStats(buildPluginStatsMap(cachedPluginList))
        setLoading(false)
        console.info('加载插件市场中：展示缓存')
      } else {
        setPlugins([])
        setPluginStats({})
        setLoading(true)
      }

      const progressSubscription = connectPluginProgressWebSocket(
        (progress) => {
          if (isUnmounted) return
          
          if (progress.operation === 'fetch') {
            setMarketplaceProgress(progress)
          } else {
            setPluginProgressById((currentProgress) => mergePluginProgress(currentProgress, progress))
          }

          // 完成状态短暂保留在对应插件卡片上，不影响其他插件的进度。
          if (progress.stage === 'success' && progress.operation === 'fetch') {
            setTimeout(() => {
              if (!isUnmounted) {
                setMarketplaceProgress(null)
              }
            }, 2000)
          } else if (progress.stage === 'success' && progress.plugin_id) {
            const completedPluginId = progress.plugin_id
            setTimeout(() => {
              if (!isUnmounted) {
                setPluginProgressById((currentProgress) => (
                  clearPluginProgress(currentProgress, completedPluginId, progress)
                ))
              }
            }, 2000)
          } else if (progress.stage === 'error' && progress.operation === 'fetch') {
            setLoading(false)
            setError(progress.error || '加载失败')
          }
        },
        (error) => {
          console.error('WebSocket error:', error)
          if (!isUnmounted) {
            toast({
              title: 'WebSocket 连接失败',
              description: '无法实时显示加载进度',
              variant: 'destructive',
            })
          }
        }
      )
        .then((unsubscribe) => {
          if (isUnmounted) {
            void unsubscribe()
            return unsubscribe
          }

          unsubscribeProgress = unsubscribe
          return unsubscribe
        })
        .catch((error) => {
          console.error('WebSocket subscribe error:', error)
          return null
        })

      // 并发加载互不依赖的数据，避免 Git 检查、版本读取、市场清单和本地扫描串行拖慢页面。
      if (!isUnmounted) {
        try {
          if (!cachedPluginList?.length) {
            setLoading(true)
          }
          setError(null)
          // 所有信息源都读取公共市场列表，统计能力和可用数据随列表返回。
          // 清单失败时不等待统计请求，保证错误提示及时返回。
          const [gitStatus, maimaiVersion, marketPlugins, installed] = await Promise.all([
            checkGitStatus(),
            getMaimaiVersion().then((version) => {
              if (!isUnmounted) setMaimaiVersion(version)
              return version
            }),
            // 市场清单与安装状态查询失败时，统一交给外层错误处理。
            fetchPluginList({ compatibleOnly: showCompatibleOnly }),
            getInstalledPlugins(),
          ])
          if (isUnmounted) {
            return
          }

          setGitStatus(gitStatus)
          if (!gitStatus.installed) {
            toast({
              title: 'Git 未安装',
              description: gitStatus.error || '请先安装 Git 才能使用插件安装功能',
              variant: 'destructive',
            })
          }

          setMaimaiVersion(maimaiVersion)

          console.info('插件市场已获取')
          syncInstalledPlugins(installed)
          const mergedData = mergeInstalledPluginInfo(marketPlugins, installed)

          setPluginStats(buildPluginStatsMap(mergedData))
          setPlugins(mergedData)
          setLoading(false)
        } catch (error) {
          if (!isUnmounted) {
            const message = error instanceof Error ? error.message : '加载插件失败'
            setError(message)
            toast({ title: '加载失败', description: message, variant: 'destructive' })
          }
        } finally {
          if (!isUnmounted) {
            setLoading(false)
          }
        }
      }

      void progressSubscription
    }

    init()

    return () => {
      isUnmounted = true
      if (unsubscribeProgress) {
        void unsubscribeProgress()
      }
    }
  }, [toast, showCompatibleOnly, sourceRevision, syncInstalledPlugins])

  // 检查插件兼容性
  // 规则：
  // 1. manifest_version === 1 的插件在麦麦 >= 1.0.0 时一律视为不兼容（旧 manifest 已不再被宿主接受）；
  // 2. 否则若声明了 host_application 范围，则按版本范围判定。
  const checkPluginCompatibility = useCallback((plugin: PluginInfo): boolean => {
    if (plugin.releases?.sync_error) return false
    if (plugin.releases?.mode === 'releases') return Boolean(plugin.releases.recommended_version)
    if (!maimaiVersion) return true

    // manifest v1 在 1.0.0+ 麦麦上不再兼容
    const manifestVersion = plugin.manifest?.manifest_version ?? 1
    if (manifestVersion <= 1 && maimaiVersion.version_major >= 1) {
      return false
    }

    if (!plugin.manifest?.host_application) return true

    return isPluginCompatible(
      plugin.manifest.host_application.min_version,
      plugin.manifest.host_application.max_version,
      maimaiVersion
    )
  }, [maimaiVersion])

  // 不兼容原因（用于 UI 提示）
  const getIncompatibleReason = useCallback((plugin: PluginInfo): string | null => {
    if (plugin.releases?.sync_error) return `版本同步失败：${plugin.releases.sync_error}`
    if (plugin.releases?.mode === 'releases' && !plugin.releases.recommended_version) return '没有兼容的稳定版本，请在插件详情查看版本列表'
    if (!maimaiVersion) return null
    const manifestVersion = plugin.manifest?.manifest_version ?? 1
    if (manifestVersion <= 1 && maimaiVersion.version_major >= 1) {
      return `该插件使用旧版 manifest (v${manifestVersion})，已不被麦麦 ${maimaiVersion.version} 支持`
    }
    if (plugin.manifest?.host_application && !isPluginCompatible(
      plugin.manifest.host_application.min_version,
      plugin.manifest.host_application.max_version,
      maimaiVersion
    )) {
      const min = plugin.manifest.host_application.min_version || '未知'
      const max = plugin.manifest.host_application.max_version
      const range = max ? `${min} - ${max}` : `${min}+`
      return `不兼容当前版本 (需要 ${range}，当前 ${maimaiVersion.version})`
    }
    return null
  }, [maimaiVersion])

  // 检查是否需要更新（市场版本比已安装版本新）
  const needsUpdate = useCallback((plugin: PluginInfo): boolean => {
    if (!plugin.installed || !plugin.installed_version || !plugin.manifest?.version) {
      return false
    }
    // 不兼容的插件不允许更新
    if (!checkPluginCompatibility(plugin)) {
      return false
    }
    
    const installedVer = plugin.installed_version.trim()
    const marketVer = plugin.manifest.version.trim()
    
    if (installedVer === marketVer) return false
    
    const installedParts = installedVer.split('.').map(Number)
    const marketParts = marketVer.split('.').map(Number)
    
    // 比较主版本号、次版本号、修订号
    for (let i = 0; i < 3; i++) {
      if ((marketParts[i] || 0) > (installedParts[i] || 0)) {
        return true  // 市场版本更新
      } else if ((marketParts[i] || 0) < (installedParts[i] || 0)) {
        return false  // 本地版本更新
      }
    }
    
    return false
  }, [checkPluginCompatibility])

  // 获取插件状态徽章
  const getStatusBadge = useCallback((plugin: PluginInfo) => {
    // 优先显示兼容性状态（已安装但不兼容也需要提示，避免用户误以为可继续更新）
    if (maimaiVersion && !checkPluginCompatibility(plugin)) {
      return (
        <Badge variant="destructive" className="gap-1">
          <AlertCircle className="h-3 w-3" />
          不兼容
        </Badge>
      )
    }

    return null
  }, [maimaiVersion, checkPluginCompatibility])

  // 打开安装对话框
  const openInstallDialog = useCallback((plugin: PluginInfo) => {
    if (!gitStatus?.installed) {
      toast({
        title: '无法安装',
        description: 'Git 未安装',
        variant: 'destructive',
      })
      return
    }

    // 检查插件兼容性
    if (maimaiVersion && !checkPluginCompatibility(plugin)) {
      toast({
        title: '无法安装',
        description: getIncompatibleReason(plugin) ?? '插件与当前麦麦版本不兼容',
        variant: 'destructive',
      })
      return
    }

    installDialogRef.current?.open(plugin)
  }, [gitStatus, maimaiVersion, checkPluginCompatibility, getIncompatibleReason, toast])

  const handleLike = useCallback(async (plugin: PluginInfo) => {
    const pluginId = plugin.manifest?.id || plugin.id
    if (pendingLikesRef.current.has(pluginId)) {
      return
    }

    pendingLikesRef.current.add(pluginId)
    setLikingPluginIds((currentIds) => {
      const nextIds = new Set(currentIds)
      nextIds.add(pluginId)
      return nextIds
    })

    try {
      const result = await likePlugin(pluginId)

      if (!result.success) {
        toast({
          title: '点赞失败',
          description: result.error || '无法提交点赞',
          variant: 'destructive',
        })
        return
      }

      const nextOverlay: PluginVoteOverlay = {
        liked: result.liked === true,
        disliked: result.disliked === true,
      }
      if (result.likes !== undefined) {
        nextOverlay.likes = Number(result.likes)
      }
      if (result.dislikes !== undefined) {
        nextOverlay.dislikes = Number(result.dislikes)
      }
      const statsIds = [pluginId, plugin.manifest?.id, operationStateRef.current.pluginStats[pluginId]?.plugin_id]
        .filter((id): id is string => Boolean(id))

      setVoteOverlay((currentOverlay) => {
        const mergedOverlay = { ...currentOverlay }
        for (const statsId of statsIds) {
          mergedOverlay[statsId] = nextOverlay
        }
        return mergedOverlay
      })
    } finally {
      pendingLikesRef.current.delete(pluginId)
      setLikingPluginIds((currentIds) => {
        const nextIds = new Set(currentIds)
        nextIds.delete(pluginId)
        return nextIds
      })
    }
  }, [toast])

  // 安装插件处理
  const handleInstall = async (installingPlugin: PluginInfo, target: string) => {
    const releaseCatalog = installingPlugin.releases
    const isReleaseInstall = releaseCatalog?.mode === 'releases'
    if (!target || target.trim() === '') {
      toast({
        title: isReleaseInstall ? '请选择安装版本' : '分支名称不能为空',
        variant: 'destructive',
      })
      return
    }

    if (isReleaseInstall) {
      const release = releaseCatalog.versions.find((item) => item.version === target)
      if (releaseCatalog.sync_error || !release || release.yanked || !release.compatible) {
        toast({ title: '无法安装', description: '所选版本不可用或与当前麦麦不兼容', variant: 'destructive' })
        return
      }
    }

    try {
      setPluginProgress({
        operation: 'install',
        stage: 'loading',
        progress: 0,
        message: `正在准备安装 ${installingPlugin.manifest.name}`,
        plugin_id: installingPlugin.id,
        total_plugins: 1,
        loaded_plugins: 0,
      })

      // Release 明确传递所选版本；旧插件仍沿用分支安装。
      const repositoryUrl = installingPlugin.manifest.repository_url || installingPlugin.manifest.urls?.repository || ''
      if (isReleaseInstall) {
        await installPlugin(installingPlugin.id, repositoryUrl, 'main', { version: target })
      } else {
        await installPlugin(installingPlugin.id, repositoryUrl, target)
      }

      // 记录下载统计
      if (installingPlugin.manifest.id) {
        recordPluginDownload(installingPlugin.manifest.id).catch(err => {
          console.warn('Failed to record download:', err)
        })
      }
      
      toast({
        title: '安装成功',
        description: `${installingPlugin.manifest.name} 已成功安装`,
      })
      setCompletedPluginProgress({
        operation: 'install',
        stage: 'success',
        progress: 100,
        message: `${installingPlugin.manifest.name} 已成功安装`,
        plugin_id: installingPlugin.id,
        total_plugins: 1,
        loaded_plugins: 1,
      })
      
      await refreshInstalledPlugins()
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : '未知错误'
      setPluginProgress({
        operation: 'install',
        stage: 'error',
        progress: 0,
        message: errorMessage,
        error: errorMessage,
        plugin_id: installingPlugin.id,
        total_plugins: 1,
        loaded_plugins: 0,
      })
      toast({
        title: '安装失败',
        description: errorMessage,
        variant: 'destructive',
      })
    }
  }

  // 卸载插件处理
  const handleUninstall = useCallback(async (plugin: PluginInfo) => {
    if (operationStateRef.current.pluginProgressById[plugin.id]?.stage === 'loading') {
      return
    }

    try {
      await uninstallPlugin(plugin.id)

      toast({
        title: '卸载成功',
        description: `${plugin.manifest.name} 已成功卸载`,
      })

      await refreshInstalledPlugins()
    } catch (error) {
      toast({
        title: '卸载失败',
        description: error instanceof Error ? error.message : '未知错误',
        variant: 'destructive',
      })
    }
  }, [refreshInstalledPlugins, toast])

  // 更新插件处理
  const handleUpdate = useCallback(async (plugin: PluginInfo) => {
    if (operationStateRef.current.pluginProgressById[plugin.id]?.stage === 'loading') {
      return
    }

    if (!gitStatus?.installed) {
      toast({
        title: '无法更新',
        description: 'Git 未安装',
        variant: 'destructive',
      })
      return
    }

    // 不兼容的插件不允许更新
    if (maimaiVersion && !checkPluginCompatibility(plugin)) {
      toast({
        title: '无法更新',
        description: getIncompatibleReason(plugin) ?? '插件与当前麦麦版本不兼容',
        variant: 'destructive',
      })
      return
    }

    try {
      setPluginProgress({
        operation: 'update',
        stage: 'loading',
        progress: 0,
        message: `正在准备更新 ${plugin.manifest.name}`,
        plugin_id: plugin.id,
        total_plugins: 1,
        loaded_plugins: 0,
      })
      const updateResult = await updatePlugin(
        plugin.id,
        plugin.manifest.repository_url || plugin.manifest.urls?.repository || '',
        'main'
      )

      toast({
        title: '更新成功',
        description: `${plugin.manifest.name} 已从 ${updateResult.old_version} 更新到 ${updateResult.new_version}`,
      })
      setCompletedPluginProgress({
        operation: 'update',
        stage: 'success',
        progress: 100,
        message: `${plugin.manifest.name} 已完成更新`,
        plugin_id: plugin.id,
        total_plugins: 1,
        loaded_plugins: 1,
      })

      await refreshInstalledPlugins()
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : '未知错误'
      setPluginProgress({
        operation: 'update',
        stage: 'error',
        progress: 0,
        message: errorMessage,
        error: errorMessage,
        plugin_id: plugin.id,
        total_plugins: 1,
        loaded_plugins: 0,
      })
      toast({
        title: '更新失败',
        description: errorMessage,
        variant: 'destructive',
      })
    }
  }, [gitStatus, maimaiVersion, checkPluginCompatibility, getIncompatibleReason, toast, setPluginProgress, setCompletedPluginProgress, refreshInstalledPlugins])

  // 筛选只做一次，计数与列表使用同一数组；点赞和进度更新不触发重新筛选。
  // 兼容性依赖宿主版本；缓存也必须通过同一筛选，不能先展示全部再删掉不兼容项。
  const waitingForCompatibility = showCompatibleOnly && maimaiVersion === null
  const visiblePlugins = useMemo(() => waitingForCompatibility ? [] : filterMarketplacePlugins(
    plugins,
    { searchQuery, pluginTypeFilter, showCompatibleOnly },
    checkPluginCompatibility,
  ), [plugins, searchQuery, pluginTypeFilter, showCompatibleOnly, waitingForCompatibility, checkPluginCompatibility])

  const marketplaceContent = (
    <ScrollArea
      className="h-full max-lg:h-auto max-lg:overflow-visible max-lg:[&>[data-dashboard-scrollbar]]:hidden"
      viewportClassName="max-lg:h-auto max-lg:!overflow-visible max-lg:overscroll-auto"
      scrollbars="vertical"
      viewportRef={scrollViewportRef}
    >
      <div className="space-y-4 p-3 sm:p-4">
        {/* 安装提示 */}
        {restartNoticeVisible && (
          <Card className="border-blue-200 bg-blue-50 dark:bg-blue-950/20 dark:border-blue-900">
            <CardContent className="py-3!">
              <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                <div className="flex items-center gap-2">
                  <Info className="h-4 w-4 text-blue-600 flex-shrink-0" />
                  <p className="text-sm text-blue-800 dark:text-blue-200">
                    安装、卸载或更新插件后，部分插件需要<span className="font-semibold">重启麦麦</span>才能生效
                  </p>
                </div>
                <Button type="button" variant="outline" size="sm" onClick={dismissRestartNotice}>
                  我知道了
                </Button>
              </div>
            </CardContent>
          </Card>
        )}

        {/* Git 状态警告 */}
        {gitStatus && !gitStatus.installed && (
          <Card className="border-orange-600 bg-orange-50 dark:bg-orange-950/20">
            <CardHeader>
              <div className="flex items-center gap-3">
                <AlertTriangle className="h-5 w-5 text-orange-600" />
                <div>
                  <CardTitle className="text-lg text-orange-900 dark:text-orange-100">
                    Git 未安装
                  </CardTitle>
                  <CardDescription className="text-orange-800 dark:text-orange-200">
                    {gitStatus.error || '请先安装 Git 才能使用插件安装功能'}
                  </CardDescription>
                </div>
              </div>
            </CardHeader>
            <CardContent>
              <p className="text-sm text-orange-800 dark:text-orange-200">
                您可以从 <a href="https://git-scm.com/downloads" target="_blank" rel="noopener noreferrer" className="underline font-medium">git-scm.com</a> 下载并安装 Git。
                安装完成后，请重启麦麦应用。
              </p>
            </CardContent>
          </Card>
        )}

        {/* 搜索和筛选栏 */}
        <Card data-plugin-market-toolbar="true" className="p-4">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
            {/* 搜索框 */}
            <div className="relative w-full sm:max-w-md sm:flex-1">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
              <Input
                placeholder="搜索插件..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="pl-9"
              />
            </div>

            {/* 移动端筛选与排序同一行；桌面端展开为父级 flex 项 */}
            <div className="flex items-center gap-2 sm:contents">
              {/* 类型筛选 */}
              <Select value={pluginTypeFilter} onValueChange={setPluginTypeFilter}>
                <SelectTrigger
                  aria-label="类型筛选"
                  title="类型筛选"
                  className="min-w-0 flex-1 gap-2 px-3 sm:w-12 sm:flex-none sm:justify-center sm:gap-1 sm:px-2"
                >
                  <Filter className="h-4 w-4 shrink-0" />
                  <span className="min-w-0 flex-1 truncate text-left sm:sr-only">
                    <SelectValue placeholder="选择类型" />
                  </span>
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">全部类型</SelectItem>
                  {PLUGIN_TYPE_OPTIONS.map(option => (
                    <SelectItem key={option.value} value={option.value}>{option.label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>

              {/* 排序 */}
              <Select
                value={marketplaceSortBy}
                onValueChange={(value) => setMarketplaceSortBy(value as MarketplaceSortKey)}
              >
                <SelectTrigger
                  aria-label="排序"
                  title="排序"
                  className="min-w-0 flex-1 gap-2 px-3 sm:w-12 sm:flex-none sm:justify-center sm:gap-1 sm:px-2"
                >
                  <ArrowUpDown className="h-4 w-4 shrink-0" />
                  <span className="min-w-0 flex-1 truncate text-left sm:sr-only">
                    <SelectValue placeholder="排序" />
                  </span>
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="default">推荐排序</SelectItem>
                  <SelectItem value="latest">最新上架</SelectItem>
                  <SelectItem value="downloads">下载最多</SelectItem>
                  <SelectItem value="likes">点赞最多</SelectItem>
                  <SelectItem value="rating">评分最高</SelectItem>
                </SelectContent>
              </Select>
            </div>

            {/* 插件数量与右侧控制区在窄屏时分别占一行。 */}
            <Badge
              variant="outline"
              data-plugin-market-count-badge="true"
              className="h-9 border-input bg-transparent px-3 text-sm font-normal max-sm:!h-auto max-sm:!border-0 max-sm:!bg-transparent max-sm:!p-0 max-sm:!shadow-none"
            >
              全部插件 {visiblePlugins.length}
            </Badge>

            {/* 兼容筛选和设置组成横向控制区，开关与文字垂直居中。 */}
            <div data-plugin-market-controls="true" className="flex shrink-0 items-center justify-end gap-4 max-sm:border-t max-sm:pt-3 sm:ml-auto sm:border-l sm:pl-5">
              <div data-plugin-market-compat="true" className="flex items-center gap-3">
                <label
                  htmlFor="compatible-only-plugins"
                  className="cursor-pointer text-xs font-medium leading-none text-muted-foreground whitespace-nowrap"
                >
                  仅显示兼容
                </label>
                <Switch
                  id="compatible-only-plugins"
                  checked={showCompatibleOnly}
                  onCheckedChange={setShowCompatibleOnly}
                />
              </div>
              <Button
                type="button"
                variant="outline"
                size="icon"
                data-plugin-market-settings-button="true"
                className="shrink-0 [&_svg]:size-5"
                aria-label="插件市场设置"
                title="插件市场设置"
                onClick={() => setSettingsOpen(true)}
              >
                <Wrench />
              </Button>
            </div>

          </div>
          {isFetchingMarketplace && (
            <div
              data-plugin-market-loading="true"
              className="mt-3 flex min-w-0 items-center gap-2 rounded-md border bg-background/85 px-3 py-2 text-xs shadow-sm backdrop-blur"
              aria-live="polite"
            >
              <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-muted-foreground" />
              <span className="shrink-0 font-medium">加载插件市场</span>
              <span className="min-w-0 truncate text-muted-foreground">
                {marketplaceProgress?.message || '正在获取插件清单'}
              </span>
            </div>
          )}
        </Card>

        {/* 加载错误显示 */}
        {marketplaceProgress
          && marketplaceProgress.operation === 'fetch'
          && marketplaceProgress.stage === 'error'
          && marketplaceProgress.error && (
          <Card className="border-destructive bg-destructive/10">
            <CardHeader>
              <div className="flex items-center gap-3">
                <AlertTriangle className="h-5 w-5 text-destructive" />
                <div>
                  <CardTitle className="text-lg text-destructive">
                    加载失败
                  </CardTitle>
                  <CardDescription className="text-destructive/80">
                    {marketplaceProgress.error}
                  </CardDescription>
                </div>
              </div>
            </CardHeader>
          </Card>
        )}

        {/* 插件卡片网格 */}
        {loading || (waitingForCompatibility && !error) ? (
          <div className="flex items-center justify-center py-12">
            <ThinkingIllustration size="lg" />
          </div>
        ) : error ? (
          <Card className="p-6">
            <div className="flex flex-col items-center justify-center py-8 text-center">
              <AlertTriangle className="h-12 w-12 text-destructive mb-4" />
              <h3 className="text-lg font-semibold mb-2">加载失败</h3>
              <p className="text-sm text-muted-foreground mb-4">{error}</p>
              <Button onClick={() => window.location.reload()}>
                重新加载
              </Button>
            </div>
          </Card>
        ) : (
          <MarketplaceTab
            plugins={visiblePlugins}
            sortBy={marketplaceSortBy}
            gitStatus={gitStatus}
            maimaiVersion={maimaiVersion}
            pluginStats={displayPluginStats}
            sortPluginStats={pluginStats}
            pluginProgressById={pluginProgressById}
            likingPluginIds={likingPluginIds}
            onInstall={openInstallDialog}
            onLike={handleLike}
            onUpdate={handleUpdate}
            onUninstall={handleUninstall}
            onDetail={handleDetail}
            checkPluginCompatibility={checkPluginCompatibility}
            needsUpdate={needsUpdate}
            getStatusBadge={getStatusBadge}
            getIncompatibleReason={getIncompatibleReason}
          />
        )}
      </div>
    </ScrollArea>
  )

  return (
    <div className="relative h-full min-h-0 min-w-0 overflow-hidden max-lg:h-auto max-lg:overflow-x-clip max-lg:overflow-y-visible">
      <div
        className="flex h-full min-h-0 w-full will-change-transform transition-transform duration-200 ease-[cubic-bezier(0.22,1,0.36,1)] motion-reduce:transition-none max-lg:h-auto max-lg:items-start"
        style={{ transform: detailPluginId ? 'translate3d(-100%, 0, 0)' : 'translate3d(0, 0, 0)' }}
        onTransitionEnd={(event) => {
          if (event.target !== event.currentTarget || event.propertyName !== 'transform') return
          setDeferDetailContent(false)
          const pending = pendingDetailNavigationRef.current
          if (pending) {
            pendingDetailNavigationRef.current = null
            syncDetailAddress(pending.pluginId)
          }
          if (detailPluginId) {
            detailPaneRef.current?.focus({ preventScroll: true })
          } else if (lastDetailPluginId) {
            marketplacePaneRef.current?.focus({ preventScroll: true })
          }
        }}
      >
        <div
          ref={marketplacePaneRef}
          data-plugin-market-pane="true"
          className={`h-full w-full min-w-0 shrink-0 outline-none [contain:layout_paint] max-lg:[contain:none] ${detailPluginId ? 'max-lg:h-0 max-lg:overflow-hidden' : 'max-lg:h-auto'}`}
          tabIndex={-1}
          inert={detailPluginId !== null}
          aria-hidden={detailPluginId !== null}
        >
          {marketplaceContent}
        </div>
        <div
          ref={detailPaneRef}
          data-plugin-detail-pane="true"
          className={`h-full w-full min-w-0 shrink-0 outline-none [contain:layout_paint] max-lg:[contain:none] ${detailPluginId ? 'max-lg:h-auto' : 'max-lg:h-0 max-lg:overflow-hidden'}`}
          tabIndex={-1}
          inert={detailPluginId === null}
          aria-hidden={detailPluginId === null}
        >
          {displayedDetailPluginId ? (
            <PluginDetailPage
              embedded={embedded}
              mode="panel"
              deferContent={deferDetailContent}
              onClose={() => setDetailPluginId(null)}
              onInstalledPluginsChange={(installed) => {
                syncInstalledPlugins(installed)
                setPlugins((currentPlugins) => mergeInstalledPluginInfo(currentPlugins, installed))
              }}
              initialPlugin={plugins.find((plugin) => plugin.id === displayedDetailPluginId)}
              initialRuntime={gitStatus && maimaiVersion ? { gitStatus, maimaiVersion, installedPlugins } : undefined}
              pluginId={displayedDetailPluginId}
            />
          ) : null}
        </div>
      </div>
      {settingsOpen && (
        <PluginSettingsDialog
          onClose={() => setSettingsOpen(false)}
          onSaved={() => {
            setSettingsOpen(false)
            setSourceRevision((revision) => revision + 1)
          }}
        />
      )}
      {/* 安装对话框 */}
      <PluginInstallDialog
        ref={installDialogRef}
        progressById={pluginProgressById}
        onInstall={handleInstall}
      />
      <RestartOverlay />
    </div>
  )
}
