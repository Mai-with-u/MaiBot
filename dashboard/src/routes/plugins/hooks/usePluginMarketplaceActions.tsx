/**
 * usePluginMarketplaceActions —— 插件市场页操作领域 hook（页面逻辑下沉）。
 *
 * 收编安装对话框与插件写操作：
 * - 打开/关闭安装对话框；
 * - install / update / uninstall / like；
 * - 兼容性、状态徽章、是否可更新等 helpers。
 *
 * MarketplaceTab 内部的过滤、排序、surprise seed 不进入本 hook。
 */
import type { Dispatch, SetStateAction } from 'react'
import { useState } from 'react'
import { AlertCircle, CheckCircle2 } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { useToast } from '@/hooks/use-toast'
import {
  checkPluginInstalled,
  getInstalledPluginVersion,
  getInstalledPlugins,
  installPlugin,
  isPluginCompatible,
  uninstallPlugin,
  updatePlugin,
  type InstalledPlugin,
} from '@/lib/plugin-api'
import { likePlugin, recordPluginDownload, type PluginStatsData } from '@/lib/plugin-stats'

import type {
  GitStatus,
  MaimaiVersion,
  PluginInfo,
  PluginLoadProgress,
  PluginProgressById,
} from '../types'

export interface UsePluginMarketplaceActionsOptions {
  gitStatus: GitStatus | null
  maimaiVersion: MaimaiVersion | null
  pluginProgressById: PluginProgressById
  setPlugins: Dispatch<SetStateAction<PluginInfo[]>>
  setInstalledPlugins: Dispatch<SetStateAction<InstalledPlugin[]>>
  setPluginStats: Dispatch<SetStateAction<Record<string, PluginStatsData>>>
  setPluginProgress: (progress: PluginLoadProgress) => void
  setCompletedPluginProgress: (progress: PluginLoadProgress) => void
}

export function usePluginMarketplaceActions({
  gitStatus,
  maimaiVersion,
  pluginProgressById,
  setPlugins,
  setInstalledPlugins,
  setPluginStats,
  setPluginProgress,
  setCompletedPluginProgress,
}: UsePluginMarketplaceActionsOptions) {
  const { toast } = useToast()
  const [likingPluginIds, setLikingPluginIds] = useState<Set<string>>(() => new Set())

  // 安装对话框状态
  const [installDialogOpen, setInstallDialogOpen] = useState(false)
  const [installingPlugin, setInstallingPlugin] = useState<PluginInfo | null>(null)
  const installProgress = installingPlugin
    ? pluginProgressById[installingPlugin.id] ?? null
    : null

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
    if (!open && installProgress?.operation === 'install' && installProgress.stage === 'loading') {
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
    } catch (error) {
      toast({
        title: '点赞失败',
        description: error instanceof Error ? error.message : '无法提交点赞',
        variant: 'destructive',
      })
    } finally {
      setLikingPluginIds((currentIds) => {
        const nextIds = new Set(currentIds)
        nextIds.delete(pluginId)
        return nextIds
      })
    }
  }

  const refreshInstalledPlugin = async (plugin: PluginInfo) => {
    try {
      const installed = await getInstalledPlugins({ forceRefresh: true })
      setInstalledPlugins(installed)
      setPlugins((prevPlugins) =>
        prevPlugins.map((item) => {
          if (item.id !== plugin.id) {
            return item
          }
          return {
            ...item,
            installed: checkPluginInstalled(item.id, installed),
            installed_version: getInstalledPluginVersion(item.id, installed),
          }
        })
      )
    } catch (error) {
      toast({
        title: '刷新已安装插件失败',
        description: error instanceof Error ? error.message : '未知错误',
        variant: 'destructive',
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
      setPluginProgress({
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
      setCompletedPluginProgress({
        operation: 'install',
        stage: 'success',
        progress: 100,
        message: `${installingPlugin.manifest.name} 已成功安装`,
        plugin_id: installingPlugin.id,
        total_plugins: 1,
        loaded_plugins: 1,
      })

      await refreshInstalledPlugin(installingPlugin)
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
  const handleUninstall = async (plugin: PluginInfo) => {
    if (pluginProgressById[plugin.id]?.stage === 'loading') {
      return
    }

    try {
      await uninstallPlugin(plugin.id)

      toast({
        title: '卸载成功',
        description: `${plugin.manifest.name} 已成功卸载`,
      })

      await refreshInstalledPlugin(plugin)
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
    if (pluginProgressById[plugin.id]?.stage === 'loading') {
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

      await refreshInstalledPlugin(plugin)
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
  }

  return {
    installDialogOpen,
    installingPlugin,
    installProgress,
    likingPluginIds,
    openInstallDialog,
    handleInstallDialogOpenChange,
    handleInstall,
    handleUninstall,
    handleUpdate,
    handleLike,
    checkPluginCompatibility,
    getIncompatibleReason,
    getStatusBadge,
    needsUpdate,
  }
}
