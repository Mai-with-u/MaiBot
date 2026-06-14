/**
 * usePluginLifecycle —— 插件「更新 / 卸载」生命周期领域 hook（页面逻辑下沉）。
 *
 * 收编 plugin-config 列表页的两条破坏性流程：
 * - 更新/升级：openUpdateDialog → handleConfirmUpdate（取仓库地址 → updatePlugin → 进度态）；
 * - 卸载：openDeleteDialog → handleConfirmDelete（uninstallPlugin → 进度态）。
 *
 * 进度态由两路驱动：本地写入（准备/成功/失败的合成 PluginLoadProgress）与 WS 实时推送
 * （pluginProgressClient.subscribe，订阅在本 hook 的 useEffect 内、cleanup 退订，参照
 * knowledge-base useImportQueue 写法）。成功后回调 onChanged 让列表刷新。
 *
 * 仓库地址解析（getPluginRepositoryUrl）依赖列表 hook 的市场信息，故由调用方注入。
 */
import { useCallback, useEffect, useState } from 'react'

import { uninstallPlugin, updatePlugin } from '@/lib/plugin-api'
import type { InstalledPlugin, PluginLoadProgress } from '@/lib/plugin-api'
import { useToast } from '@/hooks/use-toast'
import { pluginProgressClient } from '@/lib/plugin-progress-client'

export interface UsePluginLifecycleOptions {
  /** 解析插件仓库地址（依赖市场信息，由列表 hook 提供） */
  getPluginRepositoryUrl: (plugin: InstalledPlugin) => string | undefined
  /** 更新/卸载成功后回调（刷新列表） */
  onChanged: () => Promise<void> | void
  /** 标记当前正在操作的插件 id（与列表的启停共享，禁用列表行按钮） */
  setActingPluginId: (id: string | null) => void
}

export function usePluginLifecycle(options: UsePluginLifecycleOptions) {
  const { getPluginRepositoryUrl, onChanged, setActingPluginId } = options
  const { toast } = useToast()

  // ---- 卸载流程态 ----
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false)
  const [deletingPlugin, setDeletingPlugin] = useState<InstalledPlugin | null>(null)
  const [deleteProgress, setDeleteProgress] = useState<PluginLoadProgress | null>(null)

  // ---- 更新流程态 ----
  const [updateDialogOpen, setUpdateDialogOpen] = useState(false)
  const [updatingPlugin, setUpdatingPlugin] = useState<InstalledPlugin | null>(null)
  const [updateProgress, setUpdateProgress] = useState<PluginLoadProgress | null>(null)

  // WS 实时进度订阅：仅在订阅时对应当前正在更新/卸载的插件才写入进度
  useEffect(() => {
    let unsubscribe: (() => Promise<void>) | null = null
    let disposed = false

    void pluginProgressClient.subscribe((progress) => {
      if (disposed) {
        return
      }
      if (progress.operation === 'uninstall' && deletingPlugin && progress.plugin_id === deletingPlugin.id) {
        setDeleteProgress(progress)
      }
      if (progress.operation === 'update' && updatingPlugin && progress.plugin_id === updatingPlugin.id) {
        setUpdateProgress(progress)
      }
    }).then((cleanup) => {
      if (disposed) {
        void cleanup()
        return
      }
      unsubscribe = cleanup
    })

    return () => {
      disposed = true
      if (unsubscribe) {
        void unsubscribe()
      }
    }
  }, [deletingPlugin, updatingPlugin])

  const stopPluginActionEvent = useCallback((event: React.MouseEvent<HTMLElement>) => {
    event.preventDefault()
    event.stopPropagation()
  }, [])

  // ---- 更新/升级 ----
  const openUpdatePluginDialog = useCallback((plugin: InstalledPlugin, event: React.MouseEvent<HTMLButtonElement>) => {
    stopPluginActionEvent(event)
    setUpdatingPlugin(plugin)
    setUpdateProgress(null)
    setUpdateDialogOpen(true)
  }, [stopPluginActionEvent])

  const closeUpdatePluginDialog = useCallback(() => {
    if (updateProgress?.stage === 'loading') {
      return
    }
    setUpdateDialogOpen(false)
    setUpdatingPlugin(null)
    setUpdateProgress(null)
  }, [updateProgress])

  const handleConfirmUpdatePlugin = useCallback(async () => {
    if (!updatingPlugin) return

    const repositoryUrl = getPluginRepositoryUrl(updatingPlugin)
    if (!repositoryUrl) {
      setUpdateProgress({
        operation: 'update',
        stage: 'error',
        progress: 0,
        message: '插件清单中没有仓库地址，无法更新/升级',
        error: '插件清单中没有仓库地址，无法更新/升级',
        plugin_id: updatingPlugin.id,
        total_plugins: 1,
        loaded_plugins: 0,
      })
      return
    }

    setActingPluginId(updatingPlugin.id)
    setUpdateProgress({
      operation: 'update',
      stage: 'loading',
      progress: 0,
      message: `正在准备更新 ${updatingPlugin.manifest.name}`,
      plugin_id: updatingPlugin.id,
      total_plugins: 1,
      loaded_plugins: 0,
    })
    try {
      await updatePlugin(updatingPlugin.id, repositoryUrl, 'main')
      toast({
        title: '更新插件成功',
        description: `${updatingPlugin.manifest.name} 已完成更新/升级`
      })
      setUpdateProgress({
        operation: 'update',
        stage: 'success',
        progress: 100,
        message: `${updatingPlugin.manifest.name} 已完成更新/升级`,
        plugin_id: updatingPlugin.id,
        total_plugins: 1,
        loaded_plugins: 1,
      })
      await onChanged()
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : '未知错误'
      setUpdateProgress({
        operation: 'update',
        stage: 'error',
        progress: 0,
        message: errorMessage,
        error: errorMessage,
        plugin_id: updatingPlugin.id,
        total_plugins: 1,
        loaded_plugins: 0,
      })
      toast({
        title: '更新插件失败',
        description: errorMessage,
        variant: 'destructive'
      })
    } finally {
      setActingPluginId(null)
    }
  }, [getPluginRepositoryUrl, onChanged, setActingPluginId, toast, updatingPlugin])

  // ---- 卸载 ----
  const openDeletePluginDialog = useCallback((plugin: InstalledPlugin, event: React.MouseEvent<HTMLButtonElement>) => {
    stopPluginActionEvent(event)
    setDeletingPlugin(plugin)
    setDeleteProgress(null)
    setDeleteDialogOpen(true)
  }, [stopPluginActionEvent])

  const closeDeletePluginDialog = useCallback(() => {
    if (deleteProgress?.stage === 'loading') {
      return
    }
    setDeleteDialogOpen(false)
    setDeletingPlugin(null)
    setDeleteProgress(null)
  }, [deleteProgress])

  const handleConfirmDeletePlugin = useCallback(async () => {
    if (!deletingPlugin) return

    setActingPluginId(deletingPlugin.id)
    setDeleteProgress({
      operation: 'uninstall',
      stage: 'loading',
      progress: 0,
      message: `正在准备删除 ${deletingPlugin.manifest.name}`,
      plugin_id: deletingPlugin.id,
      total_plugins: 1,
      loaded_plugins: 0,
    })
    try {
      await uninstallPlugin(deletingPlugin.id)
      toast({
        title: '删除插件成功',
        description: `${deletingPlugin.manifest.name} 已删除`
      })
      setDeleteProgress({
        operation: 'uninstall',
        stage: 'success',
        progress: 100,
        message: `${deletingPlugin.manifest.name} 已删除`,
        plugin_id: deletingPlugin.id,
        total_plugins: 1,
        loaded_plugins: 1,
      })
      await onChanged()
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : '未知错误'
      setDeleteProgress({
        operation: 'uninstall',
        stage: 'error',
        progress: 0,
        message: errorMessage,
        error: errorMessage,
        plugin_id: deletingPlugin.id,
        total_plugins: 1,
        loaded_plugins: 0,
      })
      toast({
        title: '删除插件失败',
        description: errorMessage,
        variant: 'destructive'
      })
    } finally {
      setActingPluginId(null)
    }
  }, [deletingPlugin, onChanged, setActingPluginId, toast])

  return {
    // 卸载
    deleteDialogOpen,
    setDeleteDialogOpen,
    deletingPlugin,
    deleteProgress,
    openDeletePluginDialog,
    closeDeletePluginDialog,
    handleConfirmDeletePlugin,
    // 更新
    updateDialogOpen,
    setUpdateDialogOpen,
    updatingPlugin,
    updateProgress,
    openUpdatePluginDialog,
    closeUpdatePluginDialog,
    handleConfirmUpdatePlugin,
  }
}
