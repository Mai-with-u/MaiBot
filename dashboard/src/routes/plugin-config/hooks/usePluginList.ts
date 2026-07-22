/**
 * usePluginList —— 插件列表页核心领域 hook（页面逻辑下沉）。
 *
 * 收编 plugin-config 列表侧的状态机：
 * - plugins 列表加载（loadPlugins）+ 深链接 ?plugin=/?tab= 自动选中；
 * - 搜索 / 「仅看有更新」过滤、去重、可见列表派生；
 * - 选中插件（openPluginConfig / closePluginConfig，含 URL replaceState）；
 * - 启停插件（performTogglePlugin）；
 * - 市场版本比对（checkPluginUpdates / fetchPluginList）+ 更新状态派生（getPluginUpdateState）；
 * - 各类加载/熔断状态派生（getPluginStatusMeta 等）与统计计数。
 *
 * config / sourceCode 等可编辑草稿不在此 hook —— 编辑器草稿见 usePluginConfigEditor；
 * 更新/卸载破坏性流程见 usePluginLifecycle（本 hook 仅提供 getPluginRepositoryUrl / loadPlugins 供其注入）。
 */
import { useEffect, useRef, useState } from 'react'

import {
  fetchPluginList,
  getInstalledPlugins,
  getMaimaiVersion,
  isPluginCompatible,
  togglePlugin,
} from '@/lib/plugin-api'
import type { InstalledPlugin, MaimaiVersion } from '@/lib/plugin-api'
import type { PluginInfo } from '@/types/plugin'
import { useToast } from '@/hooks/use-toast'

type PluginStatusIcon = 'loading' | 'warning' | 'circuit'

export interface PluginStatusMeta {
  dotClassName: string
  label: string
  badgeClassName?: string
  icon?: PluginStatusIcon
  showsBadge?: boolean
}

export interface PluginUpdateState {
  canUpdate: boolean
  hasUpdate: boolean
  latestVersion?: string
  title?: string
}

interface CompatibilityManifest {
  manifest_version: number
  host_application?: {
    min_version: string
    max_version?: string
  }
}

const VERSION_INCOMPATIBILITY_MARKERS = [
  'Host 版本不兼容',
  'SDK 版本不兼容',
  'Manifest 版本不兼容',
]

function isManifestCompatibleWithMaimai(
  manifest: CompatibilityManifest,
  maimaiVersion: MaimaiVersion
): boolean {
  if (manifest.manifest_version !== 2 || !manifest.host_application) {
    return false
  }

  return isPluginCompatible(
    manifest.host_application.min_version,
    manifest.host_application.max_version,
    maimaiVersion
  )
}

function getInitialPluginConfigTarget(): { pluginId: string | null; tabId: string | null } {
  if (typeof window === 'undefined') {
    return { pluginId: null, tabId: null }
  }

  const params = new URLSearchParams(window.location.search)
  return {
    pluginId: params.get('plugin'),
    tabId: params.get('tab'),
  }
}

function comparePluginVersions(currentVersion: string, latestVersion: string): number {
  const currentParts = currentVersion.trim().split('.').map(part => Number.parseInt(part, 10) || 0)
  const latestParts = latestVersion.trim().split('.').map(part => Number.parseInt(part, 10) || 0)
  const maxLength = Math.max(currentParts.length, latestParts.length)

  for (let index = 0; index < maxLength; index++) {
    const currentPart = currentParts[index] || 0
    const latestPart = latestParts[index] || 0
    if (latestPart > currentPart) return 1
    if (latestPart < currentPart) return -1
  }

  return 0
}

export function usePluginList() {
  const { toast } = useToast()
  // 深链接初始目标（仅首次渲染读取一次）
  const [initialTarget] = useState(getInitialPluginConfigTarget)

  const [plugins, setPlugins] = useState<InstalledPlugin[]>([])
  const [loading, setLoading] = useState(true)
  const [searchQuery, setSearchQuery] = useState('')
  const [showUpdateOnly, setShowUpdateOnly] = useState(false)
  const [selectedPlugin, setSelectedPlugin] = useState<InstalledPlugin | null>(null)
  const [selectedPluginTab, setSelectedPluginTab] = useState<string | undefined>(initialTarget.tabId ?? undefined)
  const [actingPluginId, setActingPluginId] = useState<string | null>(null)
  const [marketPluginsById, setMarketPluginsById] = useState<Record<string, PluginInfo>>({})
  const [maimaiVersion, setMaimaiVersion] = useState<MaimaiVersion | null>(null)
  const [checkingUpdates, setCheckingUpdates] = useState(false)
  const updateCheckStartedRef = useRef(false)

  const openPluginConfig = (plugin: InstalledPlugin, tabId?: string | null) => {
    setSelectedPlugin(plugin)
    setSelectedPluginTab(tabId ?? undefined)
    const params = new URLSearchParams({ plugin: plugin.id })
    if (tabId) {
      params.set('tab', tabId)
    }
    window.history.replaceState(null, '', `/plugin-config?${params.toString()}`)
  }

  const closePluginConfig = () => {
    setSelectedPlugin(null)
    setSelectedPluginTab(undefined)
    window.history.replaceState(null, '', '/plugin-config')
    void checkPluginUpdates()
  }

  const checkPluginUpdates = async () => {
    if (updateCheckStartedRef.current) {
      return
    }
    updateCheckStartedRef.current = true
    setCheckingUpdates(true)
    try {
      const [marketPlugins, currentMaimaiVersion] = await Promise.all([
        fetchPluginList(),
        getMaimaiVersion().catch((error) => {
          console.warn('获取麦麦版本信息失败，跳过插件更新兼容性检查:', error)
          return null
        }),
      ])
      const nextMarketPluginsById: Record<string, PluginInfo> = {}
      for (const marketPlugin of marketPlugins) {
        nextMarketPluginsById[marketPlugin.id] = marketPlugin
        if (marketPlugin.manifest.id) {
          nextMarketPluginsById[marketPlugin.manifest.id] = marketPlugin
        }
      }
      setMarketPluginsById(nextMarketPluginsById)
      setMaimaiVersion(currentMaimaiVersion?.version === '0.0.0' ? null : currentMaimaiVersion)
    } catch (error) {
      updateCheckStartedRef.current = false
      console.warn('加载插件市场版本信息失败:', error)
      setMarketPluginsById({})
      setMaimaiVersion(null)
    } finally {
      setCheckingUpdates(false)
    }
  }

  // 加载插件列表（含深链接自动选中）
  const loadPlugins = async () => {
    setLoading(true)
    try {
      const installed = await getInstalledPlugins()
      setPlugins(installed)
      if (!selectedPlugin && initialTarget.pluginId) {
        const targetPlugin = installed.find((plugin) => plugin.id === initialTarget.pluginId)
        if (targetPlugin) {
          openPluginConfig(targetPlugin, initialTarget.tabId)
        }
      }
    } catch (error) {
      toast({
        title: '加载插件列表失败',
        description: error instanceof Error ? error.message : '未知错误',
        variant: 'destructive'
      })
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadPlugins()
    if (!initialTarget.pluginId) {
      void checkPluginUpdates()
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const handleShowUpdateOnlyChange = (enabled: boolean) => {
    setShowUpdateOnly(enabled)
    if (enabled && !checkingUpdates && Object.keys(marketPluginsById).length === 0) {
      void checkPluginUpdates()
    }
  }

  // 过滤插件
  const filteredPlugins = plugins.filter(plugin => {
    const query = searchQuery.toLowerCase()
    return (
      plugin.id.toLowerCase().includes(query) ||
      plugin.manifest.name.toLowerCase().includes(query) ||
      plugin.manifest.description?.toLowerCase().includes(query)
    )
  })

  // 去重：如果有重复的 plugin.id，只保留第一个
  const uniqueFilteredPlugins = filteredPlugins.filter((plugin, index, self) =>
    index === self.findIndex((p) => p.id === plugin.id)
  )

  // 统计数据 / 状态派生
  const isPluginDisabled = (plugin: InstalledPlugin) => plugin.disabled === true || plugin.enabled === false
  const isPluginLoadSuccess = (plugin: InstalledPlugin) => !isPluginDisabled(plugin) && (
    plugin.load_status === 'success' || plugin.loaded === true
  )
  const isPluginLoading = (plugin: InstalledPlugin) => !isPluginDisabled(plugin) && plugin.load_status === 'loading'
  const isPluginCircuitOpen = (plugin: InstalledPlugin) => !isPluginDisabled(plugin) && plugin.circuit_status?.state === 'open'
  const isPluginCircuitHalfOpen = (plugin: InstalledPlugin) => !isPluginDisabled(plugin) && plugin.circuit_status?.state === 'half_open'
  const isPluginCircuitActive = (plugin: InstalledPlugin) => isPluginCircuitOpen(plugin) || isPluginCircuitHalfOpen(plugin)
  const isPluginLoadFailed = (plugin: InstalledPlugin) => (
    !isPluginDisabled(plugin)
    && !isPluginLoading(plugin)
    && !isPluginLoadSuccess(plugin)
  )
  const isPluginVersionIncompatible = (plugin: InstalledPlugin) => {
    if (!isPluginLoadFailed(plugin)) {
      return false
    }

    const loadError = plugin.load_error?.trim() || ''
    if (VERSION_INCOMPATIBILITY_MARKERS.some((marker) => loadError.includes(marker))) {
      return true
    }

    return maimaiVersion !== null && !isManifestCompatibleWithMaimai(plugin.manifest, maimaiVersion)
  }
  const installedCount = plugins.length
  const disabledCount = plugins.filter(isPluginDisabled).length
  const loadSuccessCount = plugins.filter(isPluginLoadSuccess).length
  const loadingCount = plugins.filter(isPluginLoading).length
  const circuitOpenCount = plugins.filter(isPluginCircuitOpen).length
  const circuitActiveCount = plugins.filter(isPluginCircuitActive).length
  const loadFailedCount = plugins.filter(isPluginLoadFailed).length
  const enabledCount = installedCount - disabledCount
  const loadTotalCount = loadSuccessCount + loadFailedCount + loadingCount + circuitActiveCount
  const loadSuccessPercent = loadTotalCount > 0 ? (loadSuccessCount / loadTotalCount) * 100 : 0
  const loadFailedPercent = loadTotalCount > 0 ? (loadFailedCount / loadTotalCount) * 100 : 0
  const loadingPercent = loadTotalCount > 0 ? (loadingCount / loadTotalCount) * 100 : 0
  const circuitPercent = loadTotalCount > 0 ? (circuitActiveCount / loadTotalCount) * 100 : 0
  const showsCircuitSummary = circuitOpenCount > 0
  const modernLoadSummaryLabel = [
    `加载成功 ${loadSuccessCount} 个`,
    `加载中 ${loadingCount} 个`,
    showsCircuitSummary ? `熔断中 ${circuitOpenCount} 个` : '',
    `加载失败 ${loadFailedCount} 个`,
  ].filter(Boolean).join('，')
  const futureRetroPluginSummaryLabel = [
    `已安装 ${installedCount} 个插件`,
    `已启用 ${enabledCount} 个`,
    `已禁用 ${disabledCount} 个`,
    `加载中 ${loadingCount} 个`,
    showsCircuitSummary ? `熔断中 ${circuitOpenCount} 个` : '',
    `启动失败 ${loadFailedCount} 个`,
  ].filter(Boolean).join('，')

  const getPluginStatusBarClassName = (plugin: InstalledPlugin) => {
    if (isPluginDisabled(plugin)) {
      return 'bg-muted-foreground/45'
    }
    if (isPluginCircuitOpen(plugin)) {
      return 'bg-orange-500'
    }
    if (isPluginCircuitHalfOpen(plugin)) {
      return 'bg-yellow-500'
    }
    if (isPluginLoading(plugin)) {
      return 'bg-sky-500'
    }
    if (isPluginLoadFailed(plugin)) {
      return 'bg-red-500'
    }
    return 'bg-emerald-500'
  }
  const getPluginStatusLabel = (plugin: InstalledPlugin) => {
    if (isPluginDisabled(plugin)) {
      return '已禁用'
    }
    if (isPluginCircuitOpen(plugin)) {
      const remainingSec = Math.ceil(plugin.circuit_status?.remaining_sec ?? 0)
      return remainingSec > 0 ? `熔断中 ${remainingSec}s` : '熔断中'
    }
    if (isPluginCircuitHalfOpen(plugin)) {
      return '半开测试'
    }
    if (isPluginLoading(plugin)) {
      return '加载中'
    }
    if (isPluginLoadFailed(plugin)) {
      return '启动失败'
    }
    return '已启用'
  }
  const getPluginStatusMeta = (plugin: InstalledPlugin): PluginStatusMeta => {
    if (isPluginDisabled(plugin)) {
      return { dotClassName: 'bg-muted-foreground/45', label: '已禁用' }
    }
    if (isPluginCircuitOpen(plugin)) {
      const remainingSec = Math.ceil(plugin.circuit_status?.remaining_sec ?? 0)
      return {
        dotClassName: 'bg-orange-500',
        label: remainingSec > 0 ? `熔断中 ${remainingSec}s` : '熔断中',
        badgeClassName: 'border-orange-600 text-orange-600',
        icon: 'circuit' as const,
      }
    }
    if (isPluginCircuitHalfOpen(plugin)) {
      return {
        dotClassName: 'bg-yellow-500',
        label: '半开测试',
        badgeClassName: 'border-yellow-600 text-yellow-700',
        icon: 'warning' as const,
      }
    }
    if (isPluginLoading(plugin)) {
      return {
        dotClassName: 'bg-sky-500',
        label: '加载中',
        badgeClassName: 'border-sky-600 text-sky-600',
        icon: 'loading' as const,
      }
    }
    if (isPluginLoadSuccess(plugin)) {
      return { dotClassName: 'bg-emerald-500', label: '加载成功', showsBadge: false }
    }
    return {
      dotClassName: 'bg-red-500',
      label: '加载失败',
      badgeClassName: 'border-red-600 text-red-600',
      icon: 'warning' as const,
    }
  }
  const getPluginRepositoryUrl = (plugin: InstalledPlugin): string | undefined => {
    const marketPlugin = marketPluginsById[plugin.id] || (plugin.manifest.id ? marketPluginsById[plugin.manifest.id] : undefined)
    const urls = plugin.manifest.urls as { repository?: string } | undefined
    return plugin.manifest.repository_url || urls?.repository || marketPlugin?.manifest.repository_url || marketPlugin?.manifest.urls?.repository
  }
  const getPluginUpdateState = (plugin: InstalledPlugin): PluginUpdateState => {
    if (checkingUpdates) {
      return { canUpdate: false, hasUpdate: false, title: '正在检查更新' }
    }

    const marketPlugin = marketPluginsById[plugin.id] || (plugin.manifest.id ? marketPluginsById[plugin.manifest.id] : undefined)
    if (!marketPlugin) {
      return { canUpdate: false, hasUpdate: false, title: '插件市场中没有找到该插件，无法判断新版本' }
    }

    if (!getPluginRepositoryUrl(plugin)) {
      return { canUpdate: false, hasUpdate: false, title: '插件清单中没有仓库地址，无法更新/升级' }
    }

    const currentVersion = plugin.manifest.version
    const latestVersion = marketPlugin.manifest.version
    if (comparePluginVersions(currentVersion, latestVersion) <= 0) {
      return { canUpdate: false, hasUpdate: false, latestVersion, title: '当前已是最新版本' }
    }

    if (maimaiVersion && !isManifestCompatibleWithMaimai(marketPlugin.manifest, maimaiVersion)) {
      return {
        canUpdate: false,
        hasUpdate: false,
        latestVersion,
        title: `插件市场最新版本 v${latestVersion} 与当前麦麦不兼容`,
      }
    }

    return { canUpdate: true, hasUpdate: true, latestVersion, title: `发现新版本 v${latestVersion}` }
  }

  const visiblePlugins = showUpdateOnly
    ? uniqueFilteredPlugins.filter((plugin) => getPluginUpdateState(plugin).hasUpdate)
    : uniqueFilteredPlugins

  // 列表内启停插件
  const performTogglePlugin = async (plugin: InstalledPlugin) => {
    setActingPluginId(plugin.id)
    try {
      const toggleResult = await togglePlugin(plugin.id)
      toast({
        title: toggleResult.enabled ? '插件已启动' : '插件已关闭',
        description: toggleResult.message || `${plugin.manifest.name} 状态已更新`
      })
      await loadPlugins()
    } catch (error) {
      toast({
        title: '切换插件状态失败',
        description: error instanceof Error ? error.message : '未知错误',
        variant: 'destructive'
      })
    } finally {
      setActingPluginId(null)
    }
  }

  return {
    // 列表 / 选中
    plugins,
    loading,
    selectedPlugin,
    selectedPluginTab,
    openPluginConfig,
    closePluginConfig,
    loadPlugins,
    // 搜索 / 过滤
    searchQuery,
    setSearchQuery,
    showUpdateOnly,
    setShowUpdateOnly: handleShowUpdateOnlyChange,
    visiblePlugins,
    // 启停
    actingPluginId,
    setActingPluginId,
    performTogglePlugin,
    // 市场版本 / 更新派生
    checkingUpdates,
    getPluginUpdateState,
    getPluginRepositoryUrl,
    // 状态派生
    isPluginDisabled,
    isPluginLoadFailed,
    isPluginVersionIncompatible,
    getPluginStatusBarClassName,
    getPluginStatusLabel,
    getPluginStatusMeta,
    // 统计计数
    installedCount,
    disabledCount,
    loadingCount,
    circuitOpenCount,
    loadFailedCount,
    enabledCount,
    loadSuccessCount,
    loadSuccessPercent,
    loadFailedPercent,
    loadingPercent,
    circuitPercent,
    showsCircuitSummary,
    modernLoadSummaryLabel,
    futureRetroPluginSummaryLabel,
  }
}
