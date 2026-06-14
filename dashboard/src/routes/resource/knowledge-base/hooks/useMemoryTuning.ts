/**
 * useMemoryTuning —— 长期记忆「检索调优」领域 hook（页面逻辑下沉切片）。
 *
 * 收编调优相关的服务端状态与交互：
 * - 调优配置（profile/toml）与调优任务列表（tasks）走 useQuery，仅在调优面板激活时拉取（enabled: active）；
 * - 调优参数（objective/intensity/sampleSize/topKEval）以本地 state 维护；
 * - submitTuningTask 创建任务后刷新列表；applyBestTask 应用最佳轮次后刷新 profile + 任务列表，
 *   并回调 onRuntimeChanged 通知运行时配置重拉（原页面同时 setRuntimeConfig）。
 *
 * 读失败原由 loadTuningPanel 弹 toast；迁移后查询读失败按 query.ts 约定不弹全局 toast，
 *   由 tuningErrorText 局部呈现；写操作（创建/应用）保留原中文 toast 文案。
 */
import { useCallback, useMemo, useState } from 'react'

import { useQuery } from '@tanstack/react-query'

import { useToast } from '@/hooks/use-toast'
import {
  applyBestMemoryTuningProfile,
  createMemoryTuningTask,
  getMemoryTuningProfile,
  getMemoryTuningTasks,
  type MemoryTaskPayload,
} from '@/lib/memory-api'

export interface UseMemoryTuningOptions {
  /** 调优面板是否激活；非激活时不拉取 profile / tasks */
  active: boolean
  /** 应用最佳参数后回调，通知运行时配置重拉（原页面同时刷新 runtimeConfig） */
  onRuntimeChanged?: () => Promise<void> | void
}

export interface UseMemoryTuningResult {
  tuningObjective: string
  setTuningObjective: React.Dispatch<React.SetStateAction<string>>
  tuningIntensity: string
  setTuningIntensity: React.Dispatch<React.SetStateAction<string>>
  tuningSampleSize: string
  setTuningSampleSize: React.Dispatch<React.SetStateAction<string>>
  tuningTopKEval: string
  setTuningTopKEval: React.Dispatch<React.SetStateAction<string>>
  submitTuningTask: () => Promise<void>
  creatingTuning: boolean
  tuningProfile: Record<string, unknown>
  tuningProfileToml: string
  tuningTasks: MemoryTaskPayload[]
  applyBestTask: (taskId: string) => Promise<void>
  /** 调优数据读取错误文案（查询失败时局部呈现） */
  tuningErrorText: string
}

export function useMemoryTuning({ active, onRuntimeChanged }: UseMemoryTuningOptions): UseMemoryTuningResult {
  const { toast } = useToast()

  const [tuningObjective, setTuningObjective] = useState('precision_priority')
  const [tuningIntensity, setTuningIntensity] = useState('standard')
  const [tuningSampleSize, setTuningSampleSize] = useState('24')
  const [tuningTopKEval, setTuningTopKEval] = useState('20')
  const [creatingTuning, setCreatingTuning] = useState(false)

  // 调优配置：仅在面板激活时拉取
  const profileQuery = useQuery({
    queryKey: ['memory-tuning', 'profile'],
    queryFn: () => getMemoryTuningProfile(),
    enabled: active,
  })
  // 调优任务列表：仅在面板激活时拉取
  const tasksQuery = useQuery({
    queryKey: ['memory-tuning', 'tasks'],
    queryFn: () => getMemoryTuningTasks(20),
    enabled: active,
  })

  const tuningProfile = useMemo(
    () => profileQuery.data?.profile ?? {},
    [profileQuery.data?.profile],
  )
  const tuningProfileToml = profileQuery.data?.toml ?? ''
  const tuningTasks = useMemo(() => tasksQuery.data?.items ?? [], [tasksQuery.data?.items])

  const tuningError = profileQuery.error ?? tasksQuery.error
  const tuningErrorText = tuningError
    ? tuningError instanceof Error
      ? tuningError.message
      : '加载调优数据失败'
    : ''

  const submitTuningTask = useCallback(async () => {
    try {
      setCreatingTuning(true)
      await createMemoryTuningTask({
        objective: tuningObjective,
        intensity: tuningIntensity,
        sample_size: Number(tuningSampleSize),
        top_k_eval: Number(tuningTopKEval),
      })
      await tasksQuery.refetch()
      toast({ title: '调优任务已创建', description: '新的检索调优任务已经进入队列' })
    } catch (error) {
      toast({
        title: '创建调优任务失败',
        description: error instanceof Error ? error.message : '未知错误',
        variant: 'destructive',
      })
    } finally {
      setCreatingTuning(false)
    }
  }, [tasksQuery, toast, tuningIntensity, tuningObjective, tuningSampleSize, tuningTopKEval])

  const applyBestTask = useCallback(
    async (taskId: string) => {
      try {
        await applyBestMemoryTuningProfile(taskId)
        // 应用后刷新 profile + 任务列表，并通知运行时配置重拉
        await Promise.all([profileQuery.refetch(), tasksQuery.refetch(), onRuntimeChanged?.()])
        toast({ title: '最佳参数已应用', description: `任务 ${taskId} 的最佳轮次已经写入运行时` })
      } catch (error) {
        toast({
          title: '应用最佳参数失败',
          description: error instanceof Error ? error.message : '未知错误',
          variant: 'destructive',
        })
      }
    },
    [onRuntimeChanged, profileQuery, tasksQuery, toast],
  )

  return {
    tuningObjective,
    setTuningObjective,
    tuningIntensity,
    setTuningIntensity,
    tuningSampleSize,
    setTuningSampleSize,
    tuningTopKEval,
    setTuningTopKEval,
    submitTuningTask,
    creatingTuning,
    tuningProfile,
    tuningProfileToml,
    tuningTasks,
    applyBestTask,
    tuningErrorText,
  }
}
