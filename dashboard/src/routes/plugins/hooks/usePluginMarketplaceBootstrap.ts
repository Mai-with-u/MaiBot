/**
 * usePluginMarketplaceBootstrap —— 插件市场页初始化领域 hook（页面逻辑下沉）。
 *
 * 收编首屏数据与进度通道：
 * - 缓存清单 / 统计 hydrate；
 * - WebSocket 加载进度（市场 fetch 与单插件 install/update）；
 * - Promise.all 并发 Git / 麦麦版本 / 市场清单 / 已安装列表；
 * - mergeInstalledPluginInfo 合并安装态；
 * - 统计 summary 刷新。
 *
 * 市场卡片的过滤、排序、surprise seed 仍在 MarketplaceTab，不进入本 hook。
 */
import { useEffect, useState } from 'react'

import { useToast } from '@/hooks/use-toast'
import {
  checkGitStatus,
  checkPluginInstalled,
  connectPluginProgressWebSocket,
  fetchPluginList,
  getCachedPluginList,
  getInstalledPluginVersion,
  getInstalledPlugins,
  getMaimaiVersion,
  type InstalledPlugin,
} from '@/lib/plugin-api'
import {
  getCachedPluginStatsSummary,
  getPluginStatsSummary,
  type PluginStatsData,
} from '@/lib/plugin-stats'

import {
  clearPluginProgress,
  mergePluginProgress,
  type GitStatus,
  type MaimaiVersion,
  type PluginInfo,
  type PluginLoadProgress,
  type PluginProgressById,
} from '../types'

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
          changelog: installedPlugin.manifest.changelog,
          default_locale: (installedPlugin.manifest.default_locale as string) || 'zh-CN',
          locales_path: installedPlugin.manifest.locales_path as string | undefined,
        },
        downloads: 0,
        rating: 0,
        review_count: 0,
        installed: true,
        installed_version: installedPlugin.manifest.version,
        source: 'local',
        changelog: installedPlugin.changelog ?? undefined,
        stats_ids: [installedPlugin.manifest.id].filter(Boolean) as string[],
        published_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      })
    }
  }

  return mergedData
}

export function usePluginMarketplaceBootstrap() {
  const { toast } = useToast()
  const [plugins, setPlugins] = useState<PluginInfo[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [gitStatus, setGitStatus] = useState<GitStatus | null>(null)
  const [marketplaceProgress, setMarketplaceProgress] = useState<PluginLoadProgress | null>(null)
  const [pluginProgressById, setPluginProgressById] = useState<PluginProgressById>({})
  const [maimaiVersion, setMaimaiVersion] = useState<MaimaiVersion | null>(null)
  const [, setInstalledPlugins] = useState<InstalledPlugin[]>([])
  const [pluginStats, setPluginStats] = useState<Record<string, PluginStatsData>>({})

  const setPluginProgress = (progress: PluginLoadProgress) => {
    setPluginProgressById((currentProgress) => mergePluginProgress(currentProgress, progress))
  }

  const setCompletedPluginProgress = (progress: PluginLoadProgress) => {
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
          const [gitStatus, maimaiVersion, marketResult, installedResult] = await Promise.all([
            checkGitStatus(),
            getMaimaiVersion(),
            // 市场清单失败需保留原有「setError + toast + 中断」行为，故就地收敛为判别结果，避免 Promise.all 整体 reject
            fetchPluginList()
              .then((data) => ({ ok: true as const, data }))
              .catch((err) => ({ ok: false as const, error: err instanceof Error ? err.message : '加载失败' })),
            // 已安装列表失败不能阻断市场卡片，失败走 toast 而不是把市场清单一起丢掉
            getInstalledPlugins()
              .then((data) => ({ ok: true as const, data }))
              .catch((err) => ({
                ok: false as const,
                error: err instanceof Error ? err.message : '加载已安装插件失败',
              })),
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

          const installed = installedResult.ok ? installedResult.data : []
          if (!installedResult.ok) {
            toast({
              title: '加载已安装插件失败',
              description: installedResult.error,
              variant: 'destructive',
            })
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

  return {
    plugins,
    setPlugins,
    loading,
    error,
    gitStatus,
    marketplaceProgress,
    pluginProgressById,
    maimaiVersion,
    pluginStats,
    setPluginStats,
    setInstalledPlugins,
    setPluginProgress,
    setCompletedPluginProgress,
  }
}
