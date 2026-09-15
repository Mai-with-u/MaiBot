import { useState } from 'react'
import { useMutation, useQueries, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate, useSearch } from '@tanstack/react-router'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { ScrollArea } from '@/components/ui/scroll-area'
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
  FileText,
} from 'lucide-react'
import { useToast } from '@/hooks/use-toast'
import { backendApi } from '@/lib/http'
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
  getInstalledPluginVersion,
  getInstalledPlugins,
  type InstalledPlugin,
  type GitStatus,
  type MaimaiVersion,
} from '@/lib/plugin-api'
import { MarkdownRenderer } from '@/components/markdown-renderer'
import { PluginStats } from '@/components/plugin-stats'
import { recordPluginDownload } from '@/lib/plugin-stats'
import { PluginIcon } from './plugins/PluginIcon'
import { getPluginTypeLabel } from './plugins/types'

function isAbortError(error: unknown): boolean {
  if (!(error instanceof Error)) {
    return false
  }

  const cause = error.cause
  return (
    error.name === 'AbortError'
    || (cause instanceof Error && cause.name === 'AbortError')
  )
}

function buildLocalPluginInfo(installedPlugin: InstalledPlugin): PluginInfo {
  const urls = installedPlugin.manifest.urls as PluginInfo['manifest']['urls'] | undefined

  return {
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
      changelog: installedPlugin.manifest.changelog,
      default_locale: (installedPlugin.manifest.default_locale as string) || 'zh-CN',
      locales_path: installedPlugin.manifest.locales_path as string | undefined,
    },
    downloads: 0,
    rating: 0,
    review_count: 0,
    installed: true,
    installed_version: installedPlugin.manifest.version,
    published_at: '',
    updated_at: '',
    changelog: installedPlugin.changelog ?? undefined,
    source: 'local',
  }
}

async function loadPluginReadme(
  plugin: PluginInfo,
  isInstalled: boolean,
  signal: AbortSignal,
  ref: string = 'main'
): Promise<string> {
  const repositoryUrl = plugin.manifest.repository_url
  if (!repositoryUrl) {
    return ''
  }

  // 如果插件已安装，优先尝试从本地读取 README。
  if (isInstalled) {
    try {
      const localResult = await backendApi.get<{ success: boolean; data?: string }>(
        `/api/webui/plugins/local-readme/${plugin.id}`,
        { signal }
      )

      if (localResult.success && localResult.data) {
        return localResult.data
      }
    } catch (error) {
      if (isAbortError(error)) {
        throw error
      }
      // 本地未读到时继续尝试远程 README。
    }
  }

  // 从 repository_url 解析仓库信息。
  const match = repositoryUrl.match(/github\.com\/([^/]+)\/([^/\s]+)/)
  if (!match) {
    return '无法解析仓库地址'
  }

  const [, owner, repo] = match
  const cleanRepo = repo.replace(/\.git$/, '')

  const result = await backendApi.post<{ success: boolean; data?: string }>(
    '/api/webui/plugins/fetch-raw',
    {
      body: {
        owner,
        repo: cleanRepo,
        branch: ref,
        file_path: 'README.md',
      },
      errorMessage: '获取 README 失败',
      signal,
    }
  )

  if (result.success && result.data) {
    return result.data
  }

  return '该插件暂无 README 文档'
}

function getInlineChangelog(changelog: string | undefined): string | null {
  if (!changelog?.trim()) {
    return null
  }

  const normalizedChangelog = changelog.trim()
  if (
    normalizedChangelog.includes('\n')
    || normalizedChangelog.startsWith('#')
    || normalizedChangelog.startsWith('- ')
  ) {
    return normalizedChangelog
  }

  return null
}

async function fetchUndeclaredRepositoryChangelog(
  owner: string,
  repo: string,
  signal: AbortSignal
): Promise<string> {
  const response = await fetch(
    `https://raw.githubusercontent.com/${owner}/${repo}/main/CHANGELOG.md`,
    { signal }
  )

  if (!response.ok) {
    return ''
  }

  return response.text()
}

async function loadPluginChangelog(
  plugin: PluginInfo,
  isInstalled: boolean,
  signal: AbortSignal
): Promise<string> {
  const inlineChangelog = getInlineChangelog(plugin.changelog)
  if (inlineChangelog) {
    return inlineChangelog
  }

  if (isInstalled) {
    try {
      const localResult = await backendApi.get<{ success: boolean; data?: string }>(
        `/api/webui/plugins/local-changelog/${plugin.id}`,
        { signal }
      )

      if (localResult.success && localResult.data) {
        return localResult.data
      }
    } catch (error) {
      if (isAbortError(error)) {
        throw error
      }
    }
  }

  const manifestChangelog = plugin.manifest.changelog?.trim() || plugin.changelog?.trim()
  if (manifestChangelog && /^https?:\/\//.test(manifestChangelog)) {
    const result = await backendApi.post<{ success: boolean; data?: string }>(
      '/api/webui/plugins/fetch-raw',
      {
        body: {
          owner: 'custom',
          repo: 'custom',
          branch: 'main',
          file_path: 'CHANGELOG.md',
          custom_url: manifestChangelog,
        },
        errorMessage: '获取更新日志失败',
        signal,
      }
    )

    return result.success && result.data ? result.data : ''
  }

  const repositoryUrl = plugin.manifest.repository_url
  if (!repositoryUrl || !manifestChangelog) {
    return ''
  }

  const match = repositoryUrl.match(/github\.com\/([^/]+)\/([^/\s]+)/)
  if (!match) {
    return ''
  }

  const [, owner, repo] = match
  const cleanRepo = repo.replace(/\.git$/, '')
  if (!manifestChangelog) {
    try {
      return await fetchUndeclaredRepositoryChangelog(owner, cleanRepo, signal)
    } catch (error) {
      if (isAbortError(error)) {
        throw error
      }
      return ''
    }
  }

  const result = await backendApi.post<{ success: boolean; data?: string }>(
    '/api/webui/plugins/fetch-raw',
    {
      body: {
        owner,
        repo: cleanRepo,
        branch: 'main',
        file_path: manifestChangelog,
      },
      errorMessage: '获取更新日志失败',
      signal,
    }
  )

  return result.success && result.data ? result.data : ''
}

interface PluginDetailPageProps {
  embedded?: boolean
  mode?: 'page' | 'dialog'
  onClose?: () => void
  pluginId?: string
}

export function PluginDetailPage({
  embedded = false,
  mode = 'page',
  onClose,
  pluginId: pluginIdProp,
}: PluginDetailPageProps) {
  const navigate = useNavigate()
  const pluginsRoute: '/plugins' | '/plugins/embed' = embedded ? '/plugins/embed' : '/plugins'
  const search = useSearch({ strict: false }) as { pluginId?: string }
  const { toast } = useToast()
  const queryClient = useQueryClient()
  const pluginId = pluginIdProp ?? search.pluginId
  const [selection, setSelection] = useState<{ pluginId?: string; version: string; pinned: boolean } | null>(null)
  const isDialog = mode === 'dialog'
  const containerClassName = isDialog
    ? 'space-y-4 sm:space-y-5 p-4 sm:p-5'
    : 'space-y-4 sm:space-y-6 p-4 sm:p-6'
  const detailScrollClassName = isDialog
    ? 'h-[min(68vh,720px)]'
    : 'h-[calc(100vh-200px)] sm:h-[calc(100vh-220px)]'
  const readmeScrollClassName = isDialog ? 'h-[min(48vh,540px)] pr-4' : 'h-[600px] pr-4'
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
    queryFn: async () => {
      const list = await fetchPluginList()
      const foundPlugin = list.find((p) => p.id === pluginId || p.marketplace_id === pluginId)
      if (foundPlugin) {
        return foundPlugin
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
  const selectedRelease = releaseCatalog?.versions.find(
    (release) => release.version === (versionSelection?.version || releaseCatalog.recommended_version)
  )
  // 缺少插件 ID 时沿用原文案；其余加载失败由 query 的 error 呈现
  const loading = pluginQuery.isPending && !!pluginId
  const error = !pluginId
    ? '缺少插件 ID'
    : pluginQuery.isError
      ? (pluginQuery.error instanceof Error ? pluginQuery.error.message : '加载失败')
      : null

  // 运行时信息（Git 状态、麦麦版本、已安装列表）并行加载；
  // 这些信息原本失败时静默降级（不打断页面），故用 data ?? 默认值，不强制报错
  const [gitStatusQuery, maimaiVersionQuery, installedPluginsQuery] = useQueries({
    queries: [
      {
        queryKey: ['plugin-git-status'],
        queryFn: () => checkGitStatus(),
      },
      {
        queryKey: ['plugin-maimai-version'],
        queryFn: () => getMaimaiVersion(),
      },
      {
        queryKey: ['plugin-installed-list'],
        queryFn: () => getInstalledPlugins({ forceRefresh: true }),
      },
    ],
  })

  const gitStatus: GitStatus | null = gitStatusQuery.data ?? null
  const maimaiVersion: MaimaiVersion | null = maimaiVersionQuery.data ?? null
  const installedPlugins = installedPluginsQuery.data ?? []

  // 由已安装列表派生安装状态与已安装版本（纯函数，不再用本地 state）
  const isInstalled = plugin ? checkPluginInstalled(plugin.id, installedPlugins) : false
  const installedVersion = plugin ? getInstalledPluginVersion(plugin.id, installedPlugins) : undefined
  const installedRelease = installedPlugins.find((item) => item.id === plugin?.id)?.release
  const releaseRequest = releaseCatalog?.mode === 'releases'
    ? { version: versionSelection?.version || 'latest', pinned: versionSelection?.pinned ?? installedRelease?.pinned ?? false }
    : plugin?.source === 'local' ? null : undefined

  const readmeQuery = useQuery({
    queryKey: ['plugin-readme', plugin?.id, isInstalled, selectedRelease?.commit],
    enabled: !!plugin && !installedPluginsQuery.isPending,
    staleTime: 5 * 60 * 1000,
    queryFn: ({ signal }) => loadPluginReadme(
      plugin!, isInstalled && (!selectedRelease || selectedRelease.version === installedVersion), signal, selectedRelease?.commit
    ),
  })
  const readme = readmeQuery.isError ? '加载 README 失败' : (readmeQuery.data ?? '')
  const readmeLoading = readmeQuery.isPending

  const changelogQuery = useQuery({
    queryKey: ['plugin-changelog', plugin?.id, isInstalled, plugin?.manifest.changelog, plugin?.changelog],
    enabled: !!plugin && !installedPluginsQuery.isPending,
    staleTime: 5 * 60 * 1000,
    queryFn: ({ signal }) => loadPluginChangelog(plugin!, isInstalled, signal),
  })
  const changelog = selectedRelease ? selectedRelease.release_notes : changelogQuery.isError ? '加载更新日志失败' : (changelogQuery.data ?? '')
  const changelogLoading = !selectedRelease && changelogQuery.isPending

  // 任一写操作成功后，重新拉取已安装列表（前缀失效）
  const invalidateInstalledPlugins = () =>
    queryClient.invalidateQueries({ queryKey: ['plugin-installed-list'] })

  // 检查是否需要更新
  const needsUpdate = () => {
    if (!plugin || !isInstalled || !installedVersion) return false
    if (releaseCatalog?.mode === 'releases') {
      if (!selectedRelease) return false
      if (versionSelection) return selectedRelease.version !== installedVersion || versionSelection.pinned !== (installedRelease?.pinned ?? false)
      if (installedRelease?.pinned) return false
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
    mutationFn: (vars: { plugin: PluginInfo }) => {
      const repositoryUrl =
        vars.plugin.manifest.repository_url || vars.plugin.manifest.urls?.repository || ''
      return releaseRequest === undefined
        ? installPlugin(vars.plugin.id, repositoryUrl, 'main')
        : installPlugin(vars.plugin.id, repositoryUrl, 'main', releaseRequest)
    },
    meta: { errorTitle: '安装失败' },
    onSuccess: (_data, vars) => {
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
      invalidateInstalledPlugins()
    },
  })

  // 卸载插件（失败由全局 mutation 错误 toast 呈现）
  const uninstallMutation = useMutation({
    mutationFn: (vars: { plugin: PluginInfo }) =>
      uninstallPlugin(vars.plugin.id),
    meta: { errorTitle: '卸载失败' },
    onSuccess: (_data, vars) => {
      toast({
        title: '卸载成功',
        description: `${vars.plugin.manifest.name} 已成功卸载`,
      })

      // 重新加载安装状态
      invalidateInstalledPlugins()
    },
  })

  // 更新插件（失败由全局 mutation 错误 toast 呈现）
  const updateMutation = useMutation({
    mutationFn: (vars: { plugin: PluginInfo }) => {
      const repositoryUrl =
        vars.plugin.manifest.repository_url || vars.plugin.manifest.urls?.repository || ''
      return releaseRequest === undefined
        ? updatePlugin(vars.plugin.id, repositoryUrl, 'main')
        : updatePlugin(vars.plugin.id, repositoryUrl, 'main', releaseRequest)
    },
    meta: { errorTitle: '更新失败' },
    onSuccess: (data, vars) => {
      toast({
        title: '更新成功',
        description: `${vars.plugin.manifest.name} 已从 ${data.old_version} 更新到 ${data.new_version}`,
      })

      // 重新加载安装状态
      invalidateInstalledPlugins()
    },
  })

  // 任一写操作进行中即视为操作中（保持按钮禁用 / loading 语义）
  const operating =
    installMutation.isPending || uninstallMutation.isPending || updateMutation.isPending

  // 安装插件
  const handleInstall = () => {
    if (!plugin || !gitStatus?.installed) return
    installMutation.mutate({ plugin })
  }

  // 卸载插件
  const handleUninstall = () => {
    if (!plugin) return
    uninstallMutation.mutate({ plugin })
  }

  // 更新插件
  const handleUpdate = () => {
    if (!plugin || !gitStatus?.installed) return
    updateMutation.mutate({ plugin })
  }



  if (loading) {
    return (
      <div className={containerClassName}>
        <div className="flex items-center gap-3">
          <Button 
            variant="ghost" 
            size="icon"
            onClick={handleBack}
          >
            <ArrowLeft className="h-5 w-5" />
          </Button>
          {!isDialog && (
            <div>
              <h1 className="text-2xl sm:text-3xl font-bold">插件详情</h1>
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
        <div className="flex items-center gap-3">
          <Button 
            variant="ghost" 
            size="icon"
            onClick={handleBack}
          >
            <ArrowLeft className="h-5 w-5" />
          </Button>
          {!isDialog && (
            <div>
              <h1 className="text-2xl sm:text-3xl font-bold">插件详情</h1>
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
  const detailActionButtonClassName = 'h-auto min-h-16 px-5 text-base'
  const detailActionButtons = (
    <div className="flex min-w-32 shrink-0 flex-col gap-2 sm:min-w-36 sm:flex-row">
      {isInstalled ? (
        <>
          {needsUpdate() ? (
            <Button
              className={detailActionButtonClassName}
              disabled={!gitStatus?.installed || operating || !isCompatible}
              onClick={handleUpdate}
              title={!gitStatus?.installed ? 'Git 未安装' : undefined}
            >
              {operating ? (
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
            disabled={!gitStatus?.installed || operating}
            onClick={handleUninstall}
            title={!gitStatus?.installed ? 'Git 未安装' : undefined}
          >
            {operating ? (
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
          disabled={!gitStatus?.installed || !isCompatible || operating}
          onClick={handleInstall}
          title={
            !gitStatus?.installed
              ? 'Git 未安装'
              : !isCompatible
                ? `不兼容当前版本 (需要 ${plugin.manifest.host_application.min_version}${plugin.manifest.host_application.max_version ? ` - ${plugin.manifest.host_application.max_version}` : '+'}，当前 ${maimaiVersion?.version})`
                : undefined
          }
        >
          {operating ? (
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
    </div>
  )

  return (
    <div className={containerClassName}>
      {/* 页面标题和返回按钮 */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <Button 
            variant="ghost" 
            size="icon"
            onClick={handleBack}
            className="shrink-0"
          >
            <ArrowLeft className="h-5 w-5" />
          </Button>
          {!isDialog && (
            <div>
              <h1 className="text-2xl sm:text-3xl font-bold">插件详情</h1>
              <p className="text-muted-foreground mt-1 sm:mt-2 text-sm sm:text-base">
                {plugin.manifest.name}
              </p>
            </div>
          )}
        </div>
      </div>

      <ScrollArea className={detailScrollClassName}>
        <div className="space-y-6 pr-4">
          {/* 插件头部信息卡片 */}
          <Card>
            <CardHeader>
              <div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-stretch">
                <div className="flex-1 space-y-2">
                  <div className="flex items-start gap-4">
                    <PluginIcon
                      pluginId={plugin.id}
                      manifest={plugin.manifest}
                      installed={isInstalled}
                      marketplaceIconUrl={plugin.assets?.icon_64}
                      className="h-14 w-14"
                      iconClassName="h-7 w-7"
                    />
                    <div className="min-w-0 space-y-2">
                      <div className="flex items-center gap-3 flex-wrap">
                        <CardTitle className="text-2xl">{plugin.manifest.name}</CardTitle>
                        <Badge variant="secondary" className="text-sm">
                          v{selectedRelease?.version || plugin.manifest.version}
                        </Badge>
                        <Badge variant="outline" className="text-sm">
                          {getPluginTypeLabel(plugin)}
                        </Badge>
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
                    </div>
                  </div>
                </div>
                {detailActionButtons}
              </div>
            </CardHeader>
          </Card>

          {releaseCatalog && (
            <Card>
              <CardHeader><CardTitle className="text-lg">安装版本</CardTitle></CardHeader>
              <CardContent className="space-y-3">
                {releaseCatalog.sync_error ? (
                  <p role="alert" className="text-sm text-destructive">版本同步失败：{releaseCatalog.sync_error}</p>
                ) : releaseCatalog.mode === 'branch' ? (
                  <p className="text-sm text-muted-foreground">该插件尚无可识别的发布版本，使用分支安装。</p>
                ) : (
                  <>
                    <label className="block space-y-2 text-sm">
                      <span>选择发布版本</span>
                      <select
                        aria-label="选择发布版本"
                        className="flex h-10 w-full rounded-md border border-input bg-background px-3 text-foreground"
                        value={selectedRelease?.version || ''}
                        disabled={operating}
                        onChange={(event) => setSelection({ pluginId, version: event.target.value, pinned: versionSelection?.pinned ?? installedRelease?.pinned ?? false })}
                      >
                        {!selectedRelease && <option value="" disabled>没有兼容的稳定版本，请查看其他版本</option>}
                        {releaseCatalog.versions.map((release) => (
                          <option key={release.version} value={release.version} disabled={!release.compatible}>
                            {release.version}{release.version === releaseCatalog.recommended_version ? ' · 推荐' : ''}
                            {release.prerelease ? ' · 预发布' : ''}{release.yanked ? ' · 已撤回' : ''}
                            {!release.compatible ? ` · ${release.reasons.join('；')}` : ''}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label className="flex items-center gap-2 text-sm">
                      <input type="checkbox" disabled={!selectedRelease || operating}
                        checked={versionSelection?.pinned ?? installedRelease?.pinned ?? false}
                        onChange={(event) => selectedRelease && setSelection({ pluginId, version: selectedRelease.version, pinned: event.target.checked })}
                      />
                      安装后锁定此版本，阻止自动更新
                    </label>
                    <p className="text-sm text-muted-foreground">
                      当前安装：{installedVersion || '未安装'}{installedRelease?.pinned ? '（已锁定）' : ''}；
                      推荐版本：{releaseCatalog.recommended_version || '暂无兼容稳定版本'}
                    </p>
                    {selectedRelease && (
                      <p className="text-sm text-muted-foreground">
                        适配麦麦 {selectedRelease.manifest.host_application.min_version} — {selectedRelease.manifest.host_application.max_version || '不限'}
                        ；SDK {selectedRelease.manifest.sdk?.min_version} — {selectedRelease.manifest.sdk?.max_version || '不限'}
                      </p>
                    )}
                    {versionSelection && installedVersion && selectedRelease?.version !== installedVersion && (
                      <p className="text-sm text-muted-foreground">切换版本会备份当前插件目录；降级不会回滚插件数据。</p>
                    )}
                    {!releaseCatalog.recommended_version && (
                      <p role="status" className="text-sm text-muted-foreground">没有兼容的稳定版本；可在列表中查看各版本不兼容的原因，或手动选择兼容的预发布版本。</p>
                    )}
                    {!!releaseCatalog.rejected_releases?.length && (
                      <details className="text-sm text-muted-foreground">
                        <summary>有 {releaseCatalog.rejected_releases.length} 个发布版本未通过校验</summary>
                        <ul className="mt-2 space-y-1">
                          {releaseCatalog.rejected_releases.map((item) => <li key={item.tag}>{item.tag}：{item.error}</li>)}
                        </ul>
                      </details>
                    )}
                  </>
                )}
              </CardContent>
            </Card>
          )}

          <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
            {/* 左侧 - 详细信息 */}
            <div className="lg:col-span-1 space-y-6">
              {/* 统计信息 */}
              <Card>
                <CardHeader>
                  <CardTitle className="text-lg">统计信息</CardTitle>
                </CardHeader>
                <CardContent>
                  {plugin.manifest.id && <PluginStats pluginId={plugin.manifest.id} />}
                </CardContent>
              </Card>

              {/* 基本信息 */}
              <Card>
                <CardHeader>
                  <CardTitle className="text-lg">基本信息</CardTitle>
                </CardHeader>
                <CardContent className="space-y-4">
                  <div className="space-y-3">
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
                      <div className="flex flex-wrap gap-2">
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

            {/* 右侧 - README */}
            <Card className="lg:col-span-2">
              <CardHeader>
                <CardTitle className="text-lg">插件说明</CardTitle>
              </CardHeader>
              <CardContent>
                <ScrollArea className={readmeScrollClassName}>
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
                </ScrollArea>
              </CardContent>
            </Card>

            <Card className="lg:col-span-2">
              <CardHeader>
                <CardTitle className="flex items-center gap-2 text-lg">
                  <FileText className="h-4 w-4 text-muted-foreground" />
                  更新日志
                </CardTitle>
              </CardHeader>
              <CardContent>
                <ScrollArea className="h-[min(36vh,420px)] pr-4">
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
                </ScrollArea>
              </CardContent>
            </Card>
          </div>
        </div>
      </ScrollArea>
    </div>
  )
}
