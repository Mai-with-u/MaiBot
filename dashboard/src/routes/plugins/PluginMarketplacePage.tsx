import { useState, useEffect } from 'react'
import { useNavigate } from '@tanstack/react-router'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { ThinkingIllustration } from '@/components/ui/thinking-illustration'
import { AlertCircle, AlertTriangle, ArrowUpDown, CheckCircle2, Filter, Info, Loader2, Search, Settings2 } from 'lucide-react'

import { RestartOverlay } from '@/components/restart-overlay'
import { useToast } from '@/hooks/use-toast'
import { RestartProvider } from '@/lib/restart-context'
import {
  checkGitStatus,
  checkPluginInstalled,
  connectPluginProgressWebSocket,
  fetchPluginList,
  getCachedPluginList,
  getInstalledPluginVersion,
  getInstalledPlugins,
  getMaimaiVersion,
  installPlugin,
  isPluginCompatible,
  uninstallPlugin,
  updatePlugin,
  type InstalledPlugin,
} from '@/lib/plugin-api'
import {
  getCachedPluginStatsSummary,
  getPluginStatsSummary,
  likePlugin,
  recordPluginDownload,
  type PluginStatsData,
} from '@/lib/plugin-stats'

import { InstallDialog } from './InstallDialog'
import { MarketplaceTab } from './MarketplaceTab'
import type { GitStatus, MaimaiVersion, MarketplaceSortKey, PluginInfo, PluginLoadProgress } from './types'
import { getPluginType, PLUGIN_TYPE_OPTIONS } from './types'

const PLUGIN_MARKET_COMPATIBLE_ONLY_KEY = 'plugins-market-compatible-only'

const resolvePluginStats = (
  plugin: PluginInfo,
  statsSummary: Record<string, PluginStatsData>
): PluginStatsData | undefined => {
  const statsIds = [
    plugin.manifest?.id,
  ].filter((id): id is string => Boolean(id))

  return statsIds.map(id => statsSummary[id]).find(Boolean)
}

const buildPluginStatsMap = (
  pluginList: PluginInfo[],
  statsSummary: Record<string, PluginStatsData>
): Record<string, PluginStatsData> => {
  const statsMap: Record<string, PluginStatsData> = {}

  for (const plugin of pluginList) {
    const stats = resolvePluginStats(plugin, statsSummary)
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

// 插件市场页：只展示市场索引、安装状态和版本信息
export function PluginMarketplacePage() {
  return (
    <RestartProvider>
      <PluginMarketplacePageContent />
    </RestartProvider>
  )
}

// 内部组件：实际内容
function PluginMarketplacePageContent() {
  const navigate = useNavigate()
  const [restartNoticeVisible, setRestartNoticeVisible] = useState(
    () => localStorage.getItem('plugins-restart-notice-dismissed') !== 'true'
  )
  const [searchQuery, setSearchQuery] = useState('')
  const [pluginTypeFilter, setPluginTypeFilter] = useState('all')
  const [marketplaceSortBy, setMarketplaceSortBy] = useState<MarketplaceSortKey>('default')
  const [showCompatibleOnly] = useState(
    () => localStorage.getItem(PLUGIN_MARKET_COMPATIBLE_ONLY_KEY) !== 'false'
  )
  const [hideInstalledPlugins, setHideInstalledPlugins] = useState(true)
  const [plugins, setPlugins] = useState<PluginInfo[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [gitStatus, setGitStatus] = useState<GitStatus | null>(null)
  const [loadProgress, setLoadProgress] = useState<PluginLoadProgress | null>(null)
  const [maimaiVersion, setMaimaiVersion] = useState<MaimaiVersion | null>(null)
  const [, setInstalledPlugins] = useState<InstalledPlugin[]>([])
  const [pluginStats, setPluginStats] = useState<Record<string, PluginStatsData>>({})
  const [likingPluginIds, setLikingPluginIds] = useState<Set<string>>(() => new Set())
  
  // 安装对话框状态
  const [installDialogOpen, setInstallDialogOpen] = useState(false)
  const [installingPlugin, setInstallingPlugin] = useState<PluginInfo | null>(null)
  
  const { toast } = useToast()
  const isFetchingMarketplace = loadProgress?.stage === 'loading' && loadProgress.operation === 'fetch'

  const dismissRestartNotice = () => {
    localStorage.setItem('plugins-restart-notice-dismissed', 'true')
    setRestartNoticeVisible(false)
  }

  const mergeInstalledPluginInfo = (
    marketPlugins: PluginInfo[],
    installed: InstalledPlugin[]
  ): PluginInfo[] => {
    const mergedData = marketPlugins.map(plugin => {
      const installedPlugin = installed.find(item => item.id === plugin.id || item.manifest?.id === plugin.id)
      const isInstalled = Boolean(installedPlugin) || checkPluginInstalled(plugin.id, installed)
      const installedVersion = installedPlugin?.manifest?.version ?? getInstalledPluginVersion(plugin.id, installed)

      return {
        ...plugin,
        installed: isInstalled,
        installed_version: installedVersion,
      }
    })

    for (const installedPlugin of installed) {
      const installedManifestId = installedPlugin.manifest?.id
      const existsInMarket = mergedData.some(
        p => p.id === installedPlugin.id || p.id === installedManifestId || p.manifest?.id === installedPlugin.id
      )
      if (!existsInMarket && installedPlugin.manifest) {
        const urls = installedPlugin.manifest.urls as PluginInfo['manifest']['urls'] | undefined
        // 添加本地安装但不在市场的插件
        mergedData.push({
          id: installedPlugin.id,
          manifest: {
            manifest_version: installedPlugin.manifest.manifest_version || 1,
            id: installedPlugin.manifest.id || installedPlugin.id,
            name: installedPlugin.manifest.name,
            version: installedPlugin.manifest.version,
            description: installedPlugin.manifest.description || '',
            author: installedPlugin.manifest.author,
            license: installedPlugin.manifest.license || 'Unknown',
            host_application: installedPlugin.manifest.host_application,
            homepage_url: installedPlugin.manifest.homepage_url || urls?.homepage,
            repository_url: installedPlugin.manifest.repository_url || urls?.repository,
            urls,
            keywords: installedPlugin.manifest.keywords || [],
            plugin_type: installedPlugin.manifest.plugin_type || 'extension',
            display: installedPlugin.manifest.display,
            default_locale: (installedPlugin.manifest.default_locale as string) || 'zh-CN',
            locales_path: installedPlugin.manifest.locales_path as string | undefined,
          },
          downloads: 0,
          rating: 0,
          review_count: 0,
          installed: true,
          installed_version: installedPlugin.manifest.version,
          source: 'local',
          stats_ids: [installedPlugin.manifest.id].filter(Boolean) as string[],
          published_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
        })
      }
    }

    return mergedData
  }

  // 统一管理 WebSocket 和数据加载
  useEffect(() => {
    let unsubscribeProgress: (() => Promise<void>) | null = null
    let isUnmounted = false

    const init = async () => {
      const cachedPluginList = getCachedPluginList()
      const cachedStatsSummary = getCachedPluginStatsSummary()
      if (cachedPluginList?.length && !isUnmounted) {
        setPlugins(cachedPluginList)
        if (cachedStatsSummary) {
          setPluginStats(buildPluginStatsMap(cachedPluginList, cachedStatsSummary))
        }
        setLoading(false)
      }

      const progressSubscription = connectPluginProgressWebSocket(
        (progress) => {
          if (isUnmounted) return
          
          setLoadProgress(progress)
          
          // 如果加载完成，清除进度
          if (progress.stage === 'success') {
            setTimeout(() => {
              if (!isUnmounted) {
                setLoadProgress(null)
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
          const [gitStatus, maimaiVersion, marketResult, installed] = await Promise.all([
            checkGitStatus(),
            getMaimaiVersion(),
            // 市场清单失败需保留原有「setError + toast + 中断」行为，故就地收敛为判别结果，避免 Promise.all 整体 reject
            fetchPluginList()
              .then((data) => ({ ok: true as const, data }))
              .catch((err) => ({ ok: false as const, error: err instanceof Error ? err.message : '加载失败' })),
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

          if (!marketResult.ok) {
            setError(marketResult.error)
            toast({
              title: '加载失败',
              description: marketResult.error,
              variant: 'destructive',
            })
            return
          }

          setInstalledPlugins(installed)
          const mergedData = mergeInstalledPluginInfo(marketResult.data, installed)

          if (cachedStatsSummary) {
            setPluginStats(buildPluginStatsMap(mergedData, cachedStatsSummary))
          }
          setPlugins(mergedData)

          getPluginStatsSummary({ forceRefresh: Boolean(cachedStatsSummary) })
            .then((statsSummary) => {
              if (!isUnmounted) {
                setPluginStats(buildPluginStatsMap(mergedData, statsSummary))
              }
            })
            .catch((statsError) => {
              console.warn('刷新插件统计失败:', statsError)
            })
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
  }, [toast])

  // 获取插件状态徽章
  const getStatusBadge = (plugin: PluginInfo) => {
    // 优先显示兼容性状态（已安装但不兼容也需要提示，避免用户误以为可继续更新）
    if (maimaiVersion && !checkPluginCompatibility(plugin)) {
      return (
        <Badge variant="destructive" className="gap-1">
          <AlertCircle className="h-3 w-3" />
          不兼容
        </Badge>
      )
    }
    
    if (plugin.installed) {
      // 版本比较：去除两边空格并进行比较
      const installedVer = plugin.installed_version?.trim()
      const marketVer = plugin.manifest.version?.trim()
      
      if (installedVer !== marketVer) {
        // 简单的版本比较：只有当市场版本比已安装版本新时才显示"可更新"
        // 如果本地版本更新（比如手动更新或市场数据过期），则显示"已安装"
        const installedParts = installedVer?.split('.').map(Number) || [0, 0, 0]
        const marketParts = marketVer?.split('.').map(Number) || [0, 0, 0]
        
        // 比较主版本号、次版本号、修订号
        for (let i = 0; i < 3; i++) {
          if ((marketParts[i] || 0) > (installedParts[i] || 0)) {
            // 市场版本更新
            return (
              <Badge variant="outline" className="gap-1 text-orange-600 border-orange-600">
                <AlertCircle className="h-3 w-3" />
                可更新
              </Badge>
            )
          } else if ((marketParts[i] || 0) < (installedParts[i] || 0)) {
            // 本地版本更新
            break
          }
        }
      }
      
      return (
        <Badge variant="default" className="gap-1">
          <CheckCircle2 className="h-3 w-3" />
          已安装
        </Badge>
      )
    }
    return null
  }

  // 检查插件兼容性
  // 规则：
  // 1. manifest_version === 1 的插件在麦麦 >= 1.0.0 时一律视为不兼容（旧 manifest 已不再被宿主接受）；
  // 2. 否则若声明了 host_application 范围，则按版本范围判定。
  const checkPluginCompatibility = (plugin: PluginInfo): boolean => {
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
  }

  // 不兼容原因（用于 UI 提示）
  const getIncompatibleReason = (plugin: PluginInfo): string | null => {
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
  }

  // 检查是否需要更新（市场版本比已安装版本新）
  const needsUpdate = (plugin: PluginInfo): boolean => {
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
  }

  // 打开安装对话框
  const openInstallDialog = (plugin: PluginInfo) => {
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

    setInstallingPlugin(plugin)
    setInstallDialogOpen(true)
  }

  const handleInstallDialogOpenChange = (open: boolean) => {
    if (!open && loadProgress?.operation === 'install' && loadProgress.stage === 'loading') {
      return
    }

    setInstallDialogOpen(open)
    if (!open) {
      setInstallingPlugin(null)
    }
  }

  const handleLike = async (plugin: PluginInfo) => {
    const pluginId = plugin.manifest?.id || plugin.id
    if (likingPluginIds.has(pluginId)) {
      return
    }

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

      setPluginStats((currentStats) => {
        const currentPluginStats = currentStats[pluginId] ?? currentStats[plugin.id] ?? {
          plugin_id: pluginId,
          likes: 0,
          dislikes: 0,
          downloads: plugin.downloads ?? 0,
          rating: plugin.rating ?? 0,
          rating_count: 0,
        }
        const nextPluginStats: PluginStatsData = {
          ...currentPluginStats,
          plugin_id: pluginId,
          likes: Number(result.likes ?? currentPluginStats.likes),
          dislikes: Number(result.dislikes ?? currentPluginStats.dislikes),
          liked: result.liked,
          disliked: result.disliked,
        }
        const nextStats = { ...currentStats }
        const statsIds = [pluginId, plugin.id, plugin.manifest?.id, currentPluginStats.plugin_id]
          .filter((id): id is string => Boolean(id))

        for (const statsId of statsIds) {
          nextStats[statsId] = nextPluginStats
        }

        return nextStats
      })
    } finally {
      setLikingPluginIds((currentIds) => {
        const nextIds = new Set(currentIds)
        nextIds.delete(pluginId)
        return nextIds
      })
    }
  }

  // 安装插件处理
  const handleInstall = async (branch: string) => {
    if (!installingPlugin) return

    if (!branch || branch.trim() === '') {
      toast({
        title: '分支名称不能为空',
        variant: 'destructive',
      })
      return
    }

    try {
      setLoadProgress({
        operation: 'install',
        stage: 'loading',
        progress: 0,
        message: `正在准备安装 ${installingPlugin.manifest.name}`,
        plugin_id: installingPlugin.id,
        total_plugins: 1,
        loaded_plugins: 0,
      })

      await installPlugin(
        installingPlugin.id,
        installingPlugin.manifest.repository_url || installingPlugin.manifest.urls?.repository || '',
        branch
      )

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
      setLoadProgress({
        operation: 'install',
        stage: 'success',
        progress: 100,
        message: `${installingPlugin.manifest.name} 已成功安装`,
        plugin_id: installingPlugin.id,
        total_plugins: 1,
        loaded_plugins: 1,
      })
      
      // 重新加载已安装插件列表
      const installed = await getInstalledPlugins({ forceRefresh: true })
      setInstalledPlugins(installed)

      // 重新合并已安装信息到插件列表
      setPlugins(prevPlugins =>
        prevPlugins.map(p => {
          if (p.id === installingPlugin.id) {
            const isInstalled = checkPluginInstalled(p.id, installed)
            const installedVersion = getInstalledPluginVersion(p.id, installed)
            
            return {
              ...p,
              installed: isInstalled,
              installed_version: installedVersion
            }
          }
          return p
        })
      )
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : '未知错误'
      setLoadProgress({
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
  const handleUninstall = async (plugin: PluginInfo) => {
    try {
      await uninstallPlugin(plugin.id)

      toast({
        title: '卸载成功',
        description: `${plugin.manifest.name} 已成功卸载`,
      })

      // 重新加载已安装插件列表
      const installed = await getInstalledPlugins({ forceRefresh: true })
      setInstalledPlugins(installed)

      // 重新合并已安装信息到插件列表
      setPlugins(prevPlugins =>
        prevPlugins.map(p => {
          if (p.id === plugin.id) {
            const isInstalled = checkPluginInstalled(p.id, installed)
            const installedVersion = getInstalledPluginVersion(p.id, installed)
            
            return {
              ...p,
              installed: isInstalled,
              installed_version: installedVersion
            }
          }
          return p
        })
      )
    } catch (error) {
      toast({
        title: '卸载失败',
        description: error instanceof Error ? error.message : '未知错误',
        variant: 'destructive',
      })
    }
  }

  // 更新插件处理
  const handleUpdate = async (plugin: PluginInfo) => {
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
      const updateResult = await updatePlugin(
        plugin.id,
        plugin.manifest.repository_url || plugin.manifest.urls?.repository || '',
        'main'
      )

      toast({
        title: '更新成功',
        description: `${plugin.manifest.name} 已从 ${updateResult.old_version} 更新到 ${updateResult.new_version}`,
      })

      // 重新加载已安装插件列表
      const installed = await getInstalledPlugins({ forceRefresh: true })
      setInstalledPlugins(installed)

      // 重新合并已安装信息到插件列表
      setPlugins(prevPlugins =>
        prevPlugins.map(p => {
          if (p.id === plugin.id) {
            const isInstalled = checkPluginInstalled(p.id, installed)
            const installedVersion = getInstalledPluginVersion(p.id, installed)
            
            return {
              ...p,
              installed: isInstalled,
              installed_version: installedVersion
            }
          }
          return p
        })
      )
    } catch (error) {
      toast({
        title: '更新失败',
        description: error instanceof Error ? error.message : '未知错误',
        variant: 'destructive',
      })
    }
  }

  // 过滤插件用于标签页统计
  const getFilteredPluginCount = () => {
    return plugins.filter(p => {
      if (!p.manifest) return false
      if (p.source === 'local') return false
      if (hideInstalledPlugins && p.installed) return false
      const matchesSearch = searchQuery === '' ||
        p.manifest.name?.toLowerCase().includes(searchQuery.toLowerCase()) ||
        p.manifest.description?.toLowerCase().includes(searchQuery.toLowerCase()) ||
        (p.manifest.keywords && p.manifest.keywords.some(k => k.toLowerCase().includes(searchQuery.toLowerCase())))
      const matchesType = pluginTypeFilter === 'all' || getPluginType(p) === pluginTypeFilter
      const matchesCompatibility = !showCompatibleOnly || 
        !maimaiVersion || 
        checkPluginCompatibility(p)

      return matchesSearch && matchesType && matchesCompatibility
    }).length
  }

  return (
    <ScrollArea className="h-full">
      <div className="space-y-6 p-4 sm:p-6">
        {/* 标题 */}
        <div
          data-plugin-market-header="true"
          className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-4"
        >
          <div>
            <h1 data-plugin-market-title="true" className="text-2xl sm:text-3xl font-bold">
              插件市场
            </h1>
          </div>
        </div>

        {/* 安装提示 */}
        {restartNoticeVisible && (
          <Card className="border-blue-200 bg-blue-50 dark:bg-blue-950/20 dark:border-blue-900">
            <CardContent className="py-3">
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
        <Card className="p-4">
          <div className="flex flex-col gap-4 sm:flex-row sm:items-center">
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

            {/* 类型筛选 */}
            <Select value={pluginTypeFilter} onValueChange={setPluginTypeFilter}>
              <SelectTrigger
                aria-label="类型筛选"
                title="类型筛选"
                className="w-full justify-center gap-1 px-2 sm:w-12"
              >
                <Filter className="h-4 w-4" />
                <span className="sr-only">
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
                className="w-full justify-center gap-1 px-2 sm:w-12"
              >
                <ArrowUpDown className="h-4 w-4" />
                <span className="sr-only">
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

            <Badge
              variant="outline"
              data-plugin-market-count-badge="true"
              className="h-9 border-input bg-transparent px-3 text-sm font-normal"
            >
              全部插件 {getFilteredPluginCount()}
            </Badge>

            <Button
              type="button"
              variant="outline"
              className="w-full sm:ml-auto sm:w-auto"
              onClick={() => navigate({ to: '/plugin-mirrors' })}
            >
              <Settings2 className="h-4 w-4 mr-2" />
              设置
            </Button>

            {/* 兼容性筛选 */}
            <div className="flex w-full flex-wrap items-center gap-x-2 gap-y-2 sm:w-auto sm:min-w-fit">
              <Checkbox
                id="hide-installed-plugins"
                checked={hideInstalledPlugins}
                onCheckedChange={(checked) => setHideInstalledPlugins(checked === true)}
              />
              <label
                htmlFor="hide-installed-plugins"
                className="cursor-pointer text-sm font-medium leading-none whitespace-nowrap peer-disabled:cursor-not-allowed peer-disabled:opacity-70"
              >
                排除已安装
              </label>
            </div>
          </div>
          {isFetchingMarketplace && (
            <div
              className="mt-3 flex min-w-0 items-center gap-2 rounded-md border bg-background/85 px-3 py-2 text-xs shadow-sm backdrop-blur"
              aria-live="polite"
            >
              <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-muted-foreground" />
              <span className="shrink-0 font-medium">加载插件市场</span>
              <span className="min-w-0 truncate text-muted-foreground">
                {loadProgress.message || '正在获取插件清单'}
              </span>
            </div>
          )}
        </Card>

        {/* 加载错误显示 */}
        {loadProgress
          && loadProgress.operation === 'fetch'
          && loadProgress.stage === 'error'
          && loadProgress.error && (
          <Card className="border-destructive bg-destructive/10">
            <CardHeader>
              <div className="flex items-center gap-3">
                <AlertTriangle className="h-5 w-5 text-destructive" />
                <div>
                  <CardTitle className="text-lg text-destructive">
                    加载失败
                  </CardTitle>
                  <CardDescription className="text-destructive/80">
                    {loadProgress.error}
                  </CardDescription>
                </div>
              </div>
            </CardHeader>
          </Card>
        )}

        {/* 插件卡片网格 */}
        {loading ? (
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
            plugins={plugins}
            searchQuery={searchQuery}
            pluginTypeFilter={pluginTypeFilter}
            showCompatibleOnly={showCompatibleOnly}
            hideInstalledPlugins={hideInstalledPlugins}
            sortBy={marketplaceSortBy}
            gitStatus={gitStatus}
            maimaiVersion={maimaiVersion}
            pluginStats={pluginStats}
            loadProgress={loadProgress}
            likingPluginIds={likingPluginIds}
            onInstall={openInstallDialog}
            onLike={handleLike}
            onUpdate={handleUpdate}
            onUninstall={handleUninstall}
            checkPluginCompatibility={checkPluginCompatibility}
            needsUpdate={needsUpdate}
            getStatusBadge={getStatusBadge}
            getIncompatibleReason={getIncompatibleReason}
          />
        )}

        {/* 安装对话框 */}
        <InstallDialog
          open={installDialogOpen}
          plugin={installingPlugin}
          loadProgress={loadProgress}
          onOpenChange={handleInstallDialogOpenChange}
          onInstall={handleInstall}
        />

        {/* 重启遮罩层 */}
        <RestartOverlay />
      </div>
    </ScrollArea>
  )
}
