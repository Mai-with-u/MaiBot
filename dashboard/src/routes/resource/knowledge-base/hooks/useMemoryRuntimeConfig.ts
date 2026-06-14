/**
 * useMemoryRuntimeConfig —— 长期记忆「运行时配置」领域 hook（页面逻辑下沉切片）。
 *
 * 收编运行时状态相关的服务端状态与交互：
 * - 运行时配置（runtimeConfig）走 useQuery，默认即拉取（enabled: true）——它服务于概览区/图谱，
 *   原页面在初始化时加载，而非懒加载；
 * - 自检刷新（refreshSelfCheck）：触发后端自检并重拉运行时配置，结果走全局 toast；
 * - 向量重建预览-执行用 usePendingOperation：openVectorRebuildDialog 拉 dry-run 预览（setVectorRebuildPreview）
 *   后 submit，confirm 执行真重建并重拉运行时配置。
 *
 * 读失败本由 loadPage/initial effect 弹 toast；迁移后由 useQuery error + 局部 errorText 呈现
 *   （查询读失败遵循 query.ts 约定不弹全局 toast），写操作（自检/重建）保留原中文 toast 文案。
 */
import { useCallback, useMemo, useState } from 'react'

import { useQuery, useQueryClient } from '@tanstack/react-query'

import { useToast } from '@/hooks/use-toast'
import { usePendingOperation, type UsePendingOperationResult } from '@/hooks/usePendingOperation'
import {
  getMemoryRuntimeConfig,
  rebuildMemoryRuntimeVectors,
  refreshMemoryRuntimeSelfCheck,
  type MemoryRuntimeConfigPayload,
} from '@/lib/memory-api'

/** 向量重建待定操作的载荷：dry-run 预览已暂存，confirm 时执行真重建（无额外参数） */
interface VectorRebuildOperation {
  preview: Record<string, number> | null
}

export interface UseMemoryRuntimeConfigResult {
  runtimeConfig: MemoryRuntimeConfigPayload | null
  /** 运行时配置首次加载中（用于页面整体 loading 门控） */
  runtimeLoading: boolean
  /** 运行时配置读取错误文案（查询失败时局部呈现） */
  runtimeErrorText: string
  /** 重新拉取运行时配置（外部写操作后刷新概览区） */
  refreshRuntimeConfig: () => Promise<void>

  refreshingCheck: boolean
  refreshSelfCheck: () => Promise<void>

  vectorRebuildDialogOpen: boolean
  setVectorRebuildDialogOpen: (open: boolean) => void
  vectorRebuildPreview: Record<string, number> | null
  vectorRebuilding: boolean
  openVectorRebuildDialog: () => Promise<void>
  confirmVectorRebuild: () => Promise<void>
}

export function useMemoryRuntimeConfig(): UseMemoryRuntimeConfigResult {
  const { toast } = useToast()
  const queryClient = useQueryClient()

  // 运行时配置：服务于概览区/图谱，默认即拉取（沿用原页面初始化时加载、非懒加载的时机）
  const runtimeQuery = useQuery({
    queryKey: ['memory-runtime', 'config'],
    queryFn: () => getMemoryRuntimeConfig(),
  })
  const runtimeConfig = runtimeQuery.data ?? null
  const runtimeErrorText = runtimeQuery.error
    ? runtimeQuery.error instanceof Error
      ? runtimeQuery.error.message
      : '加载长期记忆运行状态失败'
    : ''

  const refreshRuntimeConfig = useCallback(async () => {
    await runtimeQuery.refetch()
  }, [runtimeQuery])

  const setRuntimeConfig = useCallback(
    (next: MemoryRuntimeConfigPayload) => {
      queryClient.setQueryData(['memory-runtime', 'config'], next)
    },
    [queryClient],
  )

  const [refreshingCheck, setRefreshingCheck] = useState(false)
  const refreshSelfCheck = useCallback(async () => {
    try {
      setRefreshingCheck(true)
      const payload = await refreshMemoryRuntimeSelfCheck()
      const nextRuntime = await getMemoryRuntimeConfig()
      setRuntimeConfig(nextRuntime)
      toast({
        title: payload.success ? '自检通过' : '自检未通过',
        description: payload.success ? '运行时状态正常' : '请检查 embedding 配置和外部服务连通性',
        variant: payload.success ? 'default' : 'destructive',
      })
    } catch (error) {
      toast({
        title: '运行时自检失败',
        description: error instanceof Error ? error.message : '未知错误',
        variant: 'destructive',
      })
    } finally {
      setRefreshingCheck(false)
    }
  }, [setRuntimeConfig, toast])

  // 向量重建预览-执行：用通用待定模块缓冲「dry-run 预览 → 对话框确认 → 真重建」
  const [vectorRebuildDialogOpen, setVectorRebuildDialogOpenState] = useState(false)
  const [vectorRebuildPreview, setVectorRebuildPreview] = useState<Record<string, number> | null>(null)
  const [vectorRebuilding, setVectorRebuilding] = useState(false)

  const vectorRebuildPendingOp: UsePendingOperationResult<VectorRebuildOperation> = usePendingOperation<VectorRebuildOperation>({
    onConfirm: async () => {
      try {
        setVectorRebuilding(true)
        const payload = await rebuildMemoryRuntimeVectors({ dry_run: false })
        const nextRuntime = await getMemoryRuntimeConfig()
        setRuntimeConfig(nextRuntime)
        setVectorRebuildDialogOpenState(false)
        toast({
          title: payload.success ? '向量重建完成' : '向量重建未完全成功',
          description: `已处理 ${payload.done ?? 0} 条，失败 ${payload.failed ?? 0} 条`,
          variant: payload.success ? 'default' : 'destructive',
        })
      } catch (error) {
        toast({
          title: '向量重建失败',
          description: error instanceof Error ? error.message : '未知错误',
          variant: 'destructive',
        })
      } finally {
        setVectorRebuilding(false)
      }
    },
  })

  const openVectorRebuildDialog = useCallback(async () => {
    try {
      setVectorRebuildDialogOpenState(true)
      setVectorRebuildPreview(null)
      const payload = await rebuildMemoryRuntimeVectors({ dry_run: true })
      const preview = payload.counts ?? null
      setVectorRebuildPreview(preview)
      // 预览完成后暂存待定操作，进入等待确认态
      vectorRebuildPendingOp.submit({ preview })
    } catch (error) {
      toast({
        title: '读取向量重建预览失败',
        description: error instanceof Error ? error.message : '未知错误',
        variant: 'destructive',
      })
    }
  }, [toast, vectorRebuildPendingOp])

  const confirmVectorRebuild = useCallback(async () => {
    await vectorRebuildPendingOp.confirm()
  }, [vectorRebuildPendingOp])

  // 对话框关闭时同步放弃待定操作，保持 dialogOpen 与 pending 一致
  const setVectorRebuildDialogOpen = useCallback(
    (open: boolean) => {
      setVectorRebuildDialogOpenState(open)
      if (!open) {
        vectorRebuildPendingOp.cancel()
        setVectorRebuildPreview(null)
      }
    },
    [vectorRebuildPendingOp],
  )

  return useMemo(
    () => ({
      runtimeConfig,
      runtimeLoading: runtimeQuery.isLoading,
      runtimeErrorText,
      refreshRuntimeConfig,
      refreshingCheck,
      refreshSelfCheck,
      vectorRebuildDialogOpen,
      setVectorRebuildDialogOpen,
      vectorRebuildPreview,
      vectorRebuilding,
      openVectorRebuildDialog,
      confirmVectorRebuild,
    }),
    [
      runtimeConfig,
      runtimeQuery.isLoading,
      runtimeErrorText,
      refreshRuntimeConfig,
      refreshingCheck,
      refreshSelfCheck,
      vectorRebuildDialogOpen,
      setVectorRebuildDialogOpen,
      vectorRebuildPreview,
      vectorRebuilding,
      openVectorRebuildDialog,
      confirmVectorRebuild,
    ],
  )
}
