import { useEffect, useState } from 'react'
import { useMutation, useQueries, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate, useSearch } from '@tanstack/react-router'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Progress } from '@/components/ui/progress'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { ThinkingIllustration } from '@/components/ui/thinking-illustration'
import {
  ArrowLeft,
  Download,
  ExternalLink,
  CheckCircle2,
  AlertCircle,
  Loader2,
  Trash2,
  RefreshCw,
  User,
  Package,
  Shield,
  Globe,
  Tag,
  GitBranch,
  Info,
} from 'lucide-react'
import { useToast } from '@/hooks/use-toast'
import { pluginProgressClient } from '@/lib/plugin-progress-client'
import { buildLocalPluginInfo } from '@/lib/plugin-info'
import type { PluginInfo } from '@/types/plugin'
import {
  checkGitStatus,
  getMaimaiVersion,
  isPluginCompatible,
  installPlugin,
  uninstallPlugin,
  updatePlugin,
  checkPluginInstalled,
  fetchPluginList,
  getCachedPluginList,
  fetchPluginDetail,
  fetchPluginDocument,
  getInstalledPluginVersion,
  getInstalledPlugins,
  type InstalledPlugin,
  type GitStatus,
  type MaimaiVersion,
  type PluginLoadProgress,
} from '@/lib/plugin-api'
import { describeRejectedReleaseError, shortenReleaseReasons } from '@/lib/plugin-api/release-reasons'
import { MarkdownRenderer } from '@/components/markdown-renderer'
import { PluginStats } from '@/components/plugin-stats'
import { recordPluginDownload } from '@/lib/plugin-stats'
import { PluginIcon } from './plugins/PluginIcon'
import { getPluginTypeLabel } from './plugins/types'

function formatPluginUpdateDate(timestamp?: string): string {
  const date = timestamp ? new Date(timestamp) : null
  if (!date || !Number.isFinite(date.getTime())) return '时间未知'
  return date.toLocaleDateString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit' })
}

interface PluginDetailPageProps {
  embedded?: boolean
  mode?: 'page' | 'dialog' | 'panel'
  onClose?: () => void
  onInstalledPluginsChange?: (installed: InstalledPlugin[]) => void
  pluginId?: string
  /** 进入动画期间推迟文档请求与渲染，避免 Markdown 解析抢占滑动帧。 */
  deferContent?: boolean
  initialPlugin?: PluginInfo
  initialRuntime?: {
    gitStatus: GitStatus
    maimaiVersion: MaimaiVersion
    installedPlugins: InstalledPlugin[]
  }
}

export function PluginDetailPage({
  embedded = false,
  mode = 'page',
  onClose,
  onInstalledPluginsChange,
  pluginId: pluginIdProp,
  deferContent = false,
  initialPlugin,
  initialRuntime,
}: PluginDetailPageProps) {
  const navigate = useNavigate()
  const pluginsRoute: '/plugins' | '/plugins/embed' = embedded ? '/plugins/embed' : '/plugins'
  const search = useSearch({ strict: false }) as { pluginId?: string }
  const { toast } = useToast()
  const queryClient = useQueryClient()
  const pluginId = pluginIdProp ?? search.pluginId
  const [selection, setSelection] = useState<{ pluginId?: string; version: string } | null>(null)
  const [operationProgress, setOperationProgress] = useState<PluginLoadProgress | null>(null)
  const [progressConnectionError, setProgressConnectionError] = useState<string | null>(null)

  // 与插件市场共用进度通道，只接收本详情页发起的同一插件、同一种操作。
  useEffect(() => {
    let disposed = false
    let unsubscribe: (() => Promise<void>) | undefined
    void pluginProgressClient.subscribe((progress) => {
      if (disposed) return
      setOperationProgress((current) => (
        current?.stage === 'loading'
        && current.plugin_id === progress.plugin_id
        && current.operation === progress.operation
          ? progress
          : current
      ))
    }).then((cleanup) => {
      if (disposed) void cleanup()
      else unsubscribe = cleanup
    }).catch((error: unknown) => {
      if (!disposed) setProgressConnectionError(
        error instanceof Error ? error.message : '无法连接插件进度通道'
      )
    })
    return () => {
      disposed = true
      void unsubscribe?.()
    }
  }, [])

  const beginOperation = (target: PluginInfo, operation: 'install' | 'update' | 'uninstall') => {
    const label = { install: '安装', update: '更新', uninstall: '卸载' }[operation]
    setOperationProgress({
      operation, plugin_id: target.id, stage: 'loading', progress: 0,
      message: `正在准备${label} ${target.manifest.name}`,
      total_plugins: 1, loaded_plugins: 0,
    })
  }
  const finishOperation = (target: PluginInfo, operation: 'install' | 'update' | 'uninstall', error?: unknown) => {
    const label = { install: '安装', update: '更新', uninstall: '卸载' }[operation]
    setOperationProgress((current) => {
      if (!current || current.plugin_id !== target.id || current.operation !== operation) return current
      return error === undefined
        ? { ...current, stage: 'success', progress: 100, loaded_plugins: 1, message: `${label}完成`, error: undefined }
        : { ...current, stage: 'error', message: `${label}失败`, error: error instanceof Error ? error.message : String(error) }
    })
  }
  const isDialog = mode === 'dialog'
  const isPanel = mode === 'panel'
  const containerClassName = isPanel
    ? 'flex h-full min-h-0 flex-col gap-4 p-3 sm:p-4 max-lg:h-auto'
    : isDialog
      ? 'space-y-4 sm:space-y-5 p-4 sm:p-5'
      : 'space-y-4 sm:space-y-6 p-4 sm:p-6'
  const detailScrollClassName = isPanel
    ? 'min-h-0 flex-1 max-lg:flex-none max-lg:overflow-visible max-lg:[&>[data-dashboard-scrollbar]]:hidden'
    : isDialog
      ? 'h-[min(68vh,720px)]'
      : 'h-[calc(100vh-200px)] sm:h-[calc(100vh-220px)]'
  const handleBack = () => {
    if (onClose) {
      onClose()
      return
    }
    navigate({ to: pluginsRoute })
  }

  // 插件详情：从市场列表中筛出当前 pluginId；失败由 query 的 error 状态局部呈现
  const pluginQuery = useQuery({
    queryKey: ['plugin-detail', pluginId],
    enabled: !!pluginId,
    staleTime: 5 * 60 * 1000,
    // 市场已有的元数据先用于首屏；完整详情仍独立请求并暴露错误。
    placeholderData: initialPlugin?.id === pluginId ? initialPlugin : undefined,
    queryFn: async () => {
      // 从兼容列表进入详情时直接获取该插件，避免先补下载全部插件列表。
      const cachedPlugin = initialPlugin?.id === pluginId ? initialPlugin : getCachedPluginList(true)?.find(
        (p) => p.id === pluginId || p.marketplace_id === pluginId
      )
      if (cachedPlugin) return fetchPluginDetail(cachedPlugin)
      const list = await fetchPluginList()
      const foundPlugin = list.find((p) => p.id === pluginId || p.marketplace_id === pluginId)
      if (foundPlugin) {
        return fetchPluginDetail(foundPlugin)
      }

      const installed = await getInstalledPlugins()
      const installedPlugin = installed.find((p) => p.id === pluginId || p.manifest?.id === pluginId)
      if (installedPlugin) {
        return buildLocalPluginInfo(installedPlugin)
      }

      throw new Error('未找到该插件')
    },
  })
  const plugin = pluginQuery.data ?? null
  const versionSelection = selection?.pluginId === pluginId ? selection : null
  const releaseCatalog = plugin?.releases
  // 最新更新时间取全部发布版本中的最大日期，不随安装版本选择变化。
  const pluginUpdateTimestamps = [
    plugin?.updated_at,
    ...(releaseCatalog?.versions.map((release) => release.published_at) ?? []),
  ]
    .filter((timestamp): timestamp is string => Boolean(timestamp))
    .map((timestamp) => Date.parse(timestamp))
    .filter(Number.isFinite)
  const latestPluginUpdate = pluginUpdateTimestamps.length > 0
    ? new Date(Math.max(...pluginUpdateTimestamps)).toISOString()
    : undefined
  const selectedRelease = releaseCatalog?.versions.find(
    (release) => release.version === (versionSelection?.version || releaseCatalog.recommended_version)
  )
  // 缺少插件 ID 时沿用原文案；其余加载失败由 query 的 error 呈现
  const pluginError = !pluginId
    ? '缺少插件 ID'
    : pluginQuery.isError
      ? (pluginQuery.error instanceof Error ? pluginQuery.error.message : '加载失败')
      : null

  // 运行时信息（Git 状态、麦麦版本、已安装列表）并行加载；
  // 安装状态必须加载成功才展示操作入口，避免查询失败被误判为未安装。
  const [gitStatusQuery, maimaiVersionQuery, installedPluginsQuery] = useQueries({
    queries: [
      {
        queryKey: ['plugin-git-status'],
        queryFn: () => checkGitStatus(),
        initialData: initialRuntime?.gitStatus,
        initialDataUpdatedAt: 0,
        staleTime: 30_000,
      },
      {
        queryKey: ['plugin-maimai-version'],
        queryFn: () => getMaimaiVersion(),
        initialData: initialRuntime?.maimaiVersion,
        initialDataUpdatedAt: 0,
        staleTime: 30_000,
      },
      {
        queryKey: ['plugin-installed-list'],
        queryFn: () => getInstalledPlugins({ forceRefresh: true }),
        initialData: initialRuntime?.installedPlugins,
        initialDataUpdatedAt: 0,
        staleTime: 30_000,
      },
    ],
  })

  const gitStatus: GitStatus | null = gitStatusQuery.data ?? null
  const maimaiVersion: MaimaiVersion | null = maimaiVersionQuery.data ?? null
  const installedPlugins = installedPluginsQuery.data ?? []
  const loading = !!pluginId && !pluginError && (pluginQuery.isPending || installedPluginsQuery.isPending)
  const error = pluginError || (installedPluginsQuery.isError ? installedPluginsQuery.error.message : null)

  // 由已安装列表派生安装状态与已安装版本（纯函数，不再用本地 state）
  const isInstalled = plugin ? checkPluginInstalled(plugin.id, installedPlugins) : false
  const installedVersion = plugin ? getInstalledPluginVersion(plugin.id, installedPlugins) : undefined
  const releaseRequest = releaseCatalog?.mode === 'releases'
    ? { version: versionSelection?.version || 'latest' }
    : plugin?.source === 'local' ? null : undefined

  const readmeQuery = useQuery({
    queryKey: ['plugin-readme', plugin?.id, isInstalled, selectedRelease?.commit],
    enabled: !!plugin && !deferContent && !pluginQuery.isPlaceholderData && installedPluginsQuery.isSuccess,
    staleTime: 5 * 60 * 1000,
    queryFn: ({ signal }) => fetchPluginDocument(plugin!.id, 'readme', {
      preferLocal: isInstalled && (!selectedRelease || selectedRelease.version === installedVersion),
      signal, branch: selectedRelease?.commit,
    }),
  })
  const readme = readmeQuery.isError ? '加载 README 失败' : (readmeQuery.data ?? '')
  const readmeLoading = deferContent || readmeQuery.isPending

  const changelogQuery = useQuery({
    queryKey: ['plugin-changelog', plugin?.id, isInstalled, plugin?.manifest.changelog, plugin?.changelog],
    enabled: !!plugin && !deferContent && !pluginQuery.isPlaceholderData && installedPluginsQuery.isSuccess,
    staleTime: 5 * 60 * 1000,
    queryFn: ({ signal }) => fetchPluginDocument(plugin!.id, 'changelog', { preferLocal: isInstalled, signal }),
  })
  const changelog = selectedRelease ? selectedRelease.release_notes : changelogQuery.isError ? '加载更新日志失败' : (changelogQuery.data ?? '')
  const changelogLoading = deferContent || (!selectedRelease && changelogQuery.isPending)

  // 任一写操作成功后，重新拉取已安装列表，并同步详情与外层市场的安装状态。
  const refreshInstalledPlugins = async () => {
    const installed = await getInstalledPlugins({ forceRefresh: true })
    queryClient.setQueryData(['plugin-installed-list'], installed)
    onInstalledPluginsChange?.(installed)
  }

  // 检查是否需要更新
  const needsUpdate = () => {
    if (!plugin || !isInstalled || !installedVersion) return false
    if (releaseCatalog?.mode === 'releases') {
      if (!selectedRelease) return false
      if (versionSelection) return selectedRelease.version !== installedVersion
      const target = selectedRelease.version.split('.').map(Number)
      const current = installedVersion.split('.').map(Number)
      return target.some((value, index) => value > current[index] && target.slice(0, index).every((part, i) => part === current[i]))
    }
    return installedVersion !== plugin.manifest.version
  }

  // 检查兼容性
  const checkCompatibility = () => {
    if (releaseCatalog?.sync_error) return false
    if (releaseCatalog?.mode === 'releases') return selectedRelease?.compatible ?? false
    if (!plugin || !maimaiVersion) return true
    return isPluginCompatible(
      plugin.manifest.host_application.min_version,
      plugin.manifest.host_application.max_version,
      maimaiVersion
    )
  }

  // 安装插件（失败由全局 mutation 错误 toast 呈现）
  const installMutation = useMutation({
    onMutate: ({ plugin }: { plugin: PluginInfo }) => beginOperation(plugin, 'install'),
    mutationFn: (vars: { plugin: PluginInfo }) => {
      const repositoryUrl =
        vars.plugin.manifest.repository_url || vars.plugin.manifest.urls?.repository || ''
      return releaseRequest === undefined
        ? installPlugin(vars.plugin.id, repositoryUrl, 'main')
        : installPlugin(vars.plugin.id, repositoryUrl, 'main', releaseRequest)
    },
    meta: { errorTitle: '安装失败' },
    onError: (error, vars) => finishOperation(vars.plugin, 'install', error),
    onSuccess: (_data, vars) => {
      finishOperation(vars.plugin, 'install')
      // 记录下载统计
      if (vars.plugin.manifest.id) {
        recordPluginDownload(vars.plugin.manifest.id).catch((err) => {
          console.warn('Failed to record download:', err)
        })
      }

      toast({
        title: '安装成功',
        description: `${vars.plugin.manifest.name} 已成功安装`,
      })

      // 重新加载安装状态
      return refreshInstalledPlugins()
    },
  })

  // 卸载插件（失败由全局 mutation 错误 toast 呈现）
  const uninstallMutation = useMutation({
    onMutate: ({ plugin }: { plugin: PluginInfo }) => beginOperation(plugin, 'uninstall'),
    mutationFn: (vars: { plugin: PluginInfo }) =>
      uninstallPlugin(vars.plugin.id),
    meta: { errorTitle: '卸载失败' },
    onError: (error, vars) => finishOperation(vars.plugin, 'uninstall', error),
    onSuccess: (_data, vars) => {
      finishOperation(vars.plugin, 'uninstall')
      toast({
        title: '卸载成功',
        description: `${vars.plugin.manifest.name} 已成功卸载`,
      })

      // 重新加载安装状态
      return refreshInstalledPlugins()
    },
  })

  // 更新插件（失败由全局 mutation 错误 toast 呈现）
  const updateMutation = useMutation({
    onMutate: ({ plugin }: { plugin: PluginInfo }) => beginOperation(plugin, 'update'),
    mutationFn: (vars: { plugin: PluginInfo }) => {
      const repositoryUrl =
        vars.plugin.manifest.repository_url || vars.plugin.manifest.urls?.repository || ''
      return releaseRequest === undefined
        ? updatePlugin(vars.plugin.id, repositoryUrl, 'main')
        : updatePlugin(vars.plugin.id, repositoryUrl, 'main', releaseRequest)
    },
    meta: { errorTitle: '更新失败' },
    onError: (error, vars) => finishOperation(vars.plugin, 'update', error),
    onSuccess: (data, vars) => {
      finishOperation(vars.plugin, 'update')
      toast({
        title: '更新成功',
        description: `${vars.plugin.manifest.name} 已从 ${data.old_version} 更新到 ${data.new_version}`,
      })

      // 重新加载安装状态
      return refreshInstalledPlugins()
    },
  })

  // 写操作互斥；按钮的忙碌文字分别由各自 mutation 决定。
  const operating =
    installMutation.isPending || uninstallMutation.isPending || updateMutation.isPending
  // 首屏元数据用于阅读，安装等操作要等完整详情到达后再开放。
  const detailPending = pluginQuery.isPlaceholderData

  // 安装插件
  const handleInstall = () => {
    if (!plugin || !gitStatus?.installed || detailPending || operating) return
    installMutation.mutate({ plugin })
  }

  // 卸载插件
  const handleUninstall = () => {
    if (!plugin || detailPending || operating) return
    uninstallMutation.mutate({ plugin })
  }

  // 更新插件
  const handleUpdate = () => {
    if (!plugin || !gitStatus?.installed || detailPending || operating) return
    updateMutation.mutate({ plugin })
  }



  if (loading) {
    return (
      <div className={containerClassName}>
        <div className="flex min-w-0 items-center gap-2">
          <Button 
            variant="outline"
            size="icon"
            onClick={handleBack}
            data-plugin-detail-back="true"
            className="shrink-0 [&_svg]:size-5"
            aria-label="返回插件市场"
          >
            <ArrowLeft className="h-5 w-5" />
          </Button>
          {!isDialog && (
            <div>
              <h1 className="text-lg! font-semibold! leading-snug!">{plugin?.manifest.name || '正在加载插件'}</h1>
            </div>
          )}
        </div>
        <div className="flex items-center justify-center py-12">
          <ThinkingIllustration size="lg" />
        </div>
      </div>
    )
  }

  if (error || !plugin) {
    return (
      <div className={containerClassName}>
        <div className="flex min-w-0 items-center gap-2">
          <Button 
            variant="outline"
            size="icon"
            onClick={handleBack}
            data-plugin-detail-back="true"
            className="shrink-0 [&_svg]:size-5"
            aria-label="返回插件市场"
          >
            <ArrowLeft className="h-5 w-5" />
          </Button>
          {!isDialog && (
            <div>
              <h1 className="text-lg! font-semibold! leading-snug!">{plugin?.manifest.name || '插件加载失败'}</h1>
            </div>
          )}
        </div>
        <Card className="p-6">
          <div className="flex flex-col items-center justify-center py-8 text-center">
            <AlertCircle className="h-12 w-12 text-destructive mb-4" />
            <h3 className="text-lg font-semibold mb-2">加载失败</h3>
            <p className="text-sm text-muted-foreground mb-4">{error}</p>
            <Button onClick={handleBack}>返回插件列表</Button>
          </div>
        </Card>
      </div>
    )
  }

  const isCompatible = checkCompatibility()
  const displayManifest = selectedRelease?.manifest || plugin.manifest
  const detailActionButtonClassName = 'h-10! flex-1 px-3 text-sm'
  const detailActionButtons = (
    <div className="flex min-w-0 flex-wrap items-center gap-2">
      {isInstalled ? (
        <>
          {needsUpdate() ? (
            <Button
              className={detailActionButtonClassName}
              disabled={!gitStatus?.installed || operating || detailPending || !isCompatible}
              onClick={handleUpdate}
              title={!gitStatus?.installed ? 'Git 未安装' : undefined}
            >
              {updateMutation.isPending ? (
                <>
                  <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                  更新中...
                </>
              ) : (
                <>
                  <RefreshCw className="h-4 w-4 mr-2" />
                  {versionSelection ? '安装所选版本' : '更新'}
                </>
              )}
            </Button>
          ) : null}
          <Button
            className={detailActionButtonClassName}
            variant="destructive"
            disabled={!gitStatus?.installed || operating || detailPending}
            onClick={handleUninstall}
            title={!gitStatus?.installed ? 'Git 未安装' : undefined}
          >
            {uninstallMutation.isPending ? (
              <>
                <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                卸载中...
              </>
            ) : (
              <>
                <Trash2 className="h-4 w-4 mr-2" />
                卸载
              </>
            )}
          </Button>
        </>
      ) : (
        <Button
          className={detailActionButtonClassName}
          disabled={!gitStatus?.installed || !isCompatible || operating || detailPending}
          onClick={handleInstall}
          title={
            !gitStatus?.installed
              ? 'Git 未安装'
              : !isCompatible
                ? `不兼容当前版本 (需要 ${plugin.manifest.host_application.min_version}${plugin.manifest.host_application.max_version ? ` - ${plugin.manifest.host_application.max_version}` : '+'}，当前 ${maimaiVersion?.version})`
                : undefined
          }
        >
          {installMutation.isPending ? (
            <>
              <Loader2 className="h-4 w-4 mr-2 animate-spin" />
              安装中...
            </>
          ) : (
            <>
              <Download className="h-4 w-4 mr-2" />
              安装
            </>
          )}
        </Button>
      )}
      {plugin.manifest.repository_url && (
        <Button asChild variant="outline" className="h-10! shrink-0 px-3 text-sm">
          <a href={plugin.manifest.repository_url} target="_blank" rel="noopener noreferrer">
            <GitBranch className="h-4 w-4" />
            仓库
          </a>
        </Button>
      )}
    </div>
  )

  return (
    <div className={containerClassName}>
      {/* 页面标题和返回按钮 */}
      <div className="flex shrink-0 flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div className="flex min-w-0 items-center gap-2">
          <Button 
            variant="outline"
            size="icon"
            onClick={handleBack}
            data-plugin-detail-back="true"
            className="shrink-0 [&_svg]:size-5"
            aria-label="返回插件市场"
            title="返回插件市场"
          >
            <ArrowLeft className="h-4 w-4" />
          </Button>
          {!isDialog && (
            <h1 className="min-w-0 truncate text-lg! font-semibold! leading-snug!" title={plugin.manifest.name}>
              {plugin.manifest.name}
            </h1>
          )}
        </div>
      </div>

      <ScrollArea
        className={detailScrollClassName}
        viewportClassName={isPanel ? 'max-lg:h-auto max-lg:!overflow-visible max-lg:overscroll-auto' : undefined}
        scrollbars="vertical"
      >
        <div className="space-y-6 pr-4">
          {/* 插件头部信息卡片 */}
          <Card data-plugin-detail-hero="true" className="grid min-w-0 grid-cols-1 lg:grid-cols-[minmax(0,1fr)_26rem]">
            <CardHeader className="min-w-0 p-5 sm:p-6">
              <div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-stretch">
                <div className="min-w-0 flex-1 space-y-2">
                  <div className="flex items-start gap-4">
                    <div data-plugin-detail-icon="true" className="shrink-0">
                      <PluginIcon
                        pluginId={plugin.id}
                        manifest={plugin.manifest}
                        installed={isInstalled}
                        marketplaceIconUrl={plugin.assets?.icon_64}
                        className="h-20 w-20"
                        iconClassName="h-7 w-7"
                      />
                    </div>
                    <div className="min-w-0 space-y-2">
                      <CardTitle className="break-words text-2xl">{plugin.manifest.name}</CardTitle>
                      <div className="flex items-center gap-3 flex-wrap">
                        <Badge variant="secondary" data-plugin-detail-version="true" className="text-sm">
                          v{selectedRelease?.version || plugin.manifest.version}
                        </Badge>
                        <Badge variant="outline" data-plugin-detail-type="true" className="text-sm">
                          {getPluginTypeLabel(plugin)}
                        </Badge>
                        {plugin.manifest.license && (
                          <Badge variant="outline" className="text-xs">{plugin.manifest.license}</Badge>
                        )}
                        {isInstalled && (
                          <Badge variant="default" className="text-sm">
                            <CheckCircle2 className="h-3 w-3 mr-1" />
                            已安装 {installedVersion && `(v${installedVersion})`}
                          </Badge>
                        )}
                        {needsUpdate() && (
                          <Badge variant="outline" className="text-sm border-orange-500 text-orange-500">
                            <RefreshCw className="h-3 w-3 mr-1" />
                            可更新
                          </Badge>
                        )}
                        {!isCompatible && (
                          <Badge variant="destructive" className="text-sm">
                            <AlertCircle className="h-3 w-3 mr-1" />
                            不兼容
                          </Badge>
                        )}
                      </div>
                      <CardDescription className="text-base">{plugin.manifest.description}</CardDescription>
                      <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
                        <span>作者 {plugin.manifest.author?.name || 'Unknown'}</span>
                        <span>最新更新 {formatPluginUpdateDate(latestPluginUpdate)}</span>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </CardHeader>
            <CardContent data-plugin-detail-release="true" className="min-w-0 space-y-3 border-t p-5 sm:p-6 lg:border-l lg:border-t-0">
              {releaseCatalog?.mode === 'releases' && !releaseCatalog.sync_error && (
                <label className="block min-w-0 space-y-2">
                  <span className="block text-xs text-muted-foreground">选择版本</span>
                  <select
                    aria-label="选择发布版本"
                    className="h-10 w-full min-w-0 rounded-md border border-input bg-background px-3 text-sm text-foreground"
                    value={selectedRelease?.version || ''}
                    disabled={operating || detailPending}
                    onChange={(event) => setSelection({ pluginId, version: event.target.value })}
                  >
                    {!selectedRelease && <option value="" disabled>没有兼容的稳定版本，请查看其他版本</option>}
                    {releaseCatalog.versions.map((release) => (
                      <option key={release.version} value={release.version} disabled={!release.compatible}>
                        {release.version}{release.version === releaseCatalog.recommended_version ? ' · 推荐' : ''}
                        {release.prerelease ? ' · 预发布' : ''}{release.yanked ? ' · 已撤回' : ''}
                        {!release.compatible ? ` · ${shortenReleaseReasons(release.reasons)}` : ''}
                        {` · 更新于 ${formatPluginUpdateDate(release.published_at)}`}
                      </option>
                    ))}
                    {releaseCatalog.rejected_releases?.map((release) => (
                      <option
                        key={`rejected:${release.tag}`}
                        value={`rejected:${release.tag}`}
                        disabled
                        className="text-muted-foreground"
                        title={describeRejectedReleaseError(release.error)}
                      >
                        {release.tag} · 校验失败 · {describeRejectedReleaseError(release.error)}
                      </option>
                    ))}
                  </select>
                </label>
              )}
              {detailActionButtons}
              {operationProgress?.plugin_id === plugin.id && (
                <div className="space-y-2 text-sm" role="status" aria-live="polite">
                  <div className="flex items-center justify-between gap-2">
                    <span className="min-w-0 break-words">{operationProgress.message}</span>
                    <span className="shrink-0 tabular-nums">{operationProgress.progress}%</span>
                  </div>
                  <Progress value={operationProgress.progress} aria-label="插件操作进度" />
                  {operationProgress.error && <p className="text-destructive break-words">{operationProgress.error}</p>}
                  {operating && progressConnectionError && (
                    <p className="text-destructive break-words">实时进度连接失败：{progressConnectionError}</p>
                  )}
                </div>
              )}
              <dl data-plugin-detail-compatibility="true" className="space-y-2 text-xs text-muted-foreground">
                <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
                  <dt>适配麦麦</dt>
                  <dd className="font-mono">{displayManifest.host_application.min_version} — {displayManifest.host_application.max_version || '不限'}</dd>
                </div>
                {displayManifest.sdk && (
                  <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
                    <dt>Plugin SDK</dt>
                    <dd className="font-mono">{displayManifest.sdk.min_version} — {displayManifest.sdk.max_version || '不限'}</dd>
                  </div>
                )}
              </dl>
              {releaseCatalog && (
                releaseCatalog.sync_error ? (
                  <p role="alert" className="text-sm text-destructive">版本同步失败：{releaseCatalog.sync_error}</p>
                ) : releaseCatalog.mode === 'branch' ? (
                  <p className="text-sm text-muted-foreground">该插件尚无可识别的发布版本，使用分支安装。</p>
                ) : (
                  <>
                    {versionSelection && installedVersion && selectedRelease?.version !== installedVersion && (
                      <p className="text-sm text-muted-foreground">切换版本会备份当前插件目录；降级不会回滚插件数据。</p>
                    )}
                    {!releaseCatalog.recommended_version && (
                      <p role="status" className="text-sm text-muted-foreground">没有兼容的稳定版本；可在列表中查看各版本不兼容的原因，或手动选择兼容的预发布版本。</p>
                    )}
                  </>
                )
              )}
            </CardContent>
          </Card>

          <div className="grid grid-cols-1 items-start gap-6 lg:grid-cols-3">
            {/* 左侧 - README 与更新日志共用卡片，通过标签切换，由外层详情容器统一滚动。 */}
            <div className="min-w-0 space-y-6 lg:col-span-2">
              <Tabs key={pluginId} defaultValue="readme" asChild>
                <Card data-plugin-detail-doc-tabs="true">
                  <CardHeader className="flex-row flex-wrap items-center justify-between gap-3 space-y-0">
                    <CardTitle className="text-lg">插件说明</CardTitle>
                    <TabsList aria-label="插件文档" className="ml-auto h-auto gap-2 bg-transparent p-0">
                      <TabsTrigger value="readme" size="sm">README</TabsTrigger>
                      <TabsTrigger value="changelog" size="sm">更新日志</TabsTrigger>
                    </TabsList>
                  </CardHeader>
                  <TabsContent value="readme" asChild>
                    <CardContent data-plugin-detail-doc="true">
                      {readmeLoading ? (
                        <div className="flex items-center justify-center py-12">
                          <ThinkingIllustration />
                        </div>
                      ) : readme ? (
                        <MarkdownRenderer content={readme} />
                      ) : (
                        <div className="text-center text-muted-foreground py-12">
                          暂无说明文档
                        </div>
                      )}
                    </CardContent>
                  </TabsContent>
                  <TabsContent value="changelog" asChild>
                    <CardContent data-plugin-detail-doc="true">
                      {changelogLoading ? (
                        <div className="flex items-center justify-center py-12">
                          <ThinkingIllustration />
                        </div>
                      ) : changelog ? (
                        <MarkdownRenderer content={changelog} />
                      ) : (
                        <div className="text-center text-muted-foreground py-12">
                          暂无更新日志
                        </div>
                      )}
                    </CardContent>
                  </TabsContent>
                </Card>
              </Tabs>
            </div>

            {/* 右侧 - 详细信息 */}
            <div className="min-w-0 space-y-6 lg:col-span-1">
              {/* 统计信息 */}
              <Card data-plugin-detail-stats="true">
                <CardHeader>
                  <CardTitle className="text-lg">统计信息</CardTitle>
                </CardHeader>
                <CardContent>
                  <p className="mb-3 text-xs text-muted-foreground">
                    最新更新：{formatPluginUpdateDate(latestPluginUpdate)}
                  </p>
                  {!deferContent && plugin.manifest.id && <PluginStats pluginId={plugin.manifest.id} />}
                </CardContent>
              </Card>

              {/* 基本信息 */}
              <Card>
                <CardHeader>
                  <CardTitle className="text-lg">基本信息</CardTitle>
                </CardHeader>
                <CardContent data-plugin-detail-info="true" className="space-y-4">
                  <div className="space-y-3">
                    <div className="flex items-start gap-2 text-sm">
                      <Tag className="h-4 w-4 shrink-0 text-muted-foreground mt-0.5" />
                      <span className="shrink-0 text-muted-foreground">插件 ID:</span>
                      <span className="min-w-0 break-all font-mono font-medium select-all">{plugin.id}</span>
                    </div>

                    <div className="flex items-center gap-2 text-sm">
                      <User className="h-4 w-4 text-muted-foreground" />
                      <span className="text-muted-foreground">作者:</span>
                      <span className="font-medium">{plugin.manifest.author?.name || 'Unknown'}</span>
                      {plugin.manifest.author?.url && (
                        <a
                          href={plugin.manifest.author.url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-primary hover:underline"
                        >
                          <ExternalLink className="h-3 w-3" />
                        </a>
                      )}
                    </div>

                    <div className="flex items-center gap-2 text-sm">
                      <Package className="h-4 w-4 text-muted-foreground" />
                      <span className="text-muted-foreground">版本:</span>
                      <span className="font-medium">v{selectedRelease?.version || plugin.manifest.version}</span>
                    </div>

                    <div className="flex items-center gap-2 text-sm">
                      <Info className="h-4 w-4 text-muted-foreground" />
                      <span className="text-muted-foreground">类型:</span>
                      <span className="font-medium">{getPluginTypeLabel(plugin)}</span>
                    </div>

                    <div className="flex items-center gap-2 text-sm">
                      <Shield className="h-4 w-4 text-muted-foreground" />
                      <span className="text-muted-foreground">许可证:</span>
                      <span className="font-medium">{plugin.manifest.license}</span>
                    </div>

                    {plugin.manifest.homepage_url && (
                      <div className="flex items-center gap-2 text-sm">
                        <Globe className="h-4 w-4 text-muted-foreground" />
                        <span className="text-muted-foreground">主页:</span>
                        <a
                          href={plugin.manifest.homepage_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-primary hover:underline flex items-center gap-1"
                        >
                          访问
                          <ExternalLink className="h-3 w-3" />
                        </a>
                      </div>
                    )}

                    {plugin.manifest.repository_url && (
                      <div className="flex items-center gap-2 text-sm">
                        <GitBranch className="h-4 w-4 text-muted-foreground" />
                        <span className="text-muted-foreground">仓库:</span>
                        <a
                          href={plugin.manifest.repository_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-primary hover:underline flex items-center gap-1"
                        >
                          GitHub
                          <ExternalLink className="h-3 w-3" />
                        </a>
                      </div>
                    )}

                    <div className="pt-2 border-t">
                      <div className="flex items-center gap-2 text-sm mb-2">
                        <Info className="h-4 w-4 text-muted-foreground" />
                        <span className="text-muted-foreground">支持版本:</span>
                      </div>
                      <div className="text-sm pl-6 font-medium">
                        {(selectedRelease?.manifest || plugin.manifest).host_application.min_version}
                        {(selectedRelease?.manifest || plugin.manifest).host_application.max_version
                          ? ` - ${(selectedRelease?.manifest || plugin.manifest).host_application.max_version}`
                          : ' - 最新版本'}
                      </div>
                    </div>
                  </div>
                </CardContent>
              </Card>

              {/* 类型和标签 */}
              {plugin.manifest.keywords && plugin.manifest.keywords.length > 0 && (
                <Card>
                  <CardHeader>
                    <CardTitle className="text-lg">标签</CardTitle>
                  </CardHeader>
                  <CardContent className="space-y-4">
                    <div>
                      <p className="text-sm text-muted-foreground mb-2">关键词</p>
                      <div data-plugin-detail-keywords="true" className="flex flex-wrap gap-2">
                        {plugin.manifest.keywords.map((keyword) => (
                          <Badge key={keyword} variant="outline" className="text-xs">
                            <Tag className="h-3 w-3 mr-1" />
                            {keyword}
                          </Badge>
                        ))}
                      </div>
                    </div>
                  </CardContent>
                </Card>
              )}
            </div>
          </div>
        </div>
      </ScrollArea>
    </div>
  )
}
