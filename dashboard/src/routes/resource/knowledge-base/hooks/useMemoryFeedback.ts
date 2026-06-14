/**
 * useMemoryFeedback —— 长期记忆「反馈纠错历史」领域 hook（页面逻辑下沉切片）。
 *
 * 收编纠错相关的服务端状态与交互：
 * - 纠错历史列表（corrections）走 useQuery，仅在纠错面板激活时拉取（enabled: active）；
 * - 列表搜索/筛选/分页、任务详情、行为日志分页仍以本地 state + effect 维持
 *   （非标准 {items,total} 服务端分页，保留原命令式分页态，以最小行为变化为准）；
 * - 回退（rollback）：对话框收集原因后执行 rollbackMemoryFeedbackCorrection，成功后刷新列表与详情，
 *   并回调 onRuntimeChanged / onSourcesChanged 通知运行时配置与来源列表重拉（原页面同时刷新二者）。
 *
 * 读失败原由 loadFeedbackPanel 弹 toast；迁移后查询读失败按 query.ts 约定不弹全局 toast，由 feedbackErrorText
 *   局部呈现；写操作（回退）保留原中文 toast 文案。
 */
import { useCallback, useEffect, useMemo, useState } from 'react'

import { useQuery } from '@tanstack/react-query'

import { useToast } from '@/hooks/use-toast'
import {
  getMemoryFeedbackCorrection,
  getMemoryFeedbackCorrections,
  rollbackMemoryFeedbackCorrection,
  type MemoryFeedbackActionLogPayload,
  type MemoryFeedbackCorrectionDetailTaskPayload,
  type MemoryFeedbackCorrectionSummaryPayload,
} from '@/lib/memory-api'

import { FEEDBACK_ACTION_LOG_PAGE_SIZE, FEEDBACK_CORRECTION_FETCH_LIMIT, FEEDBACK_CORRECTION_PAGE_SIZE } from '../constants'
import {
  buildFeedbackImpactSummary,
  getFeedbackCorrectionPreview,
  summarizeFeedbackActionPayload,
} from '../utils'

export interface UseMemoryFeedbackOptions {
  /** 纠错面板是否激活；非激活时不拉取列表、不加载任务详情 */
  active: boolean
  /** 深链接初始值：搜索框（通常为 task_id 字符串） */
  initialSearch?: string
  /** 深链接初始值：选中的任务 ID */
  initialTaskId?: number
  /** 回退成功后回调，通知运行时配置重拉（原页面同时刷新 runtimeConfig） */
  onRuntimeChanged?: () => Promise<void> | void
  /** 回退成功后回调，通知来源列表重拉（原页面同时刷新 sources） */
  onSourcesChanged?: () => Promise<void> | void
}

export interface UseMemoryFeedbackResult {
  feedbackSearch: string
  setFeedbackSearch: React.Dispatch<React.SetStateAction<string>>
  feedbackStatusFilter: string
  setFeedbackStatusFilter: React.Dispatch<React.SetStateAction<string>>
  feedbackRollbackFilter: string
  setFeedbackRollbackFilter: React.Dispatch<React.SetStateAction<string>>
  filteredFeedbackCorrections: MemoryFeedbackCorrectionSummaryPayload[]
  feedbackCorrections: MemoryFeedbackCorrectionSummaryPayload[]
  pagedFeedbackCorrections: MemoryFeedbackCorrectionSummaryPayload[]
  feedbackPage: number
  setFeedbackPage: React.Dispatch<React.SetStateAction<number>>
  feedbackPageCount: number
  selectedFeedbackCorrection: MemoryFeedbackCorrectionSummaryPayload | null
  setSelectedFeedbackTaskId: React.Dispatch<React.SetStateAction<number>>
  selectedFeedbackResolved: MemoryFeedbackCorrectionDetailTaskPayload | null
  selectedFeedbackPreview: ReturnType<typeof getFeedbackCorrectionPreview>
  selectedFeedbackImpactSummary: string[]
  openFeedbackRollbackDialog: () => void
  feedbackRollingBack: boolean
  selectedFeedbackTaskLoading: boolean
  selectedFeedbackTaskError: string | null
  feedbackActionLogPage: number
  setFeedbackActionLogPage: React.Dispatch<React.SetStateAction<number>>
  feedbackActionLogPageCount: number
  feedbackActionLogSearch: string
  setFeedbackActionLogSearch: React.Dispatch<React.SetStateAction<string>>
  pagedFeedbackActionLogs: MemoryFeedbackActionLogPayload[]
  selectedFeedbackActionLogs: MemoryFeedbackActionLogPayload[]

  // 回退对话框相关
  feedbackRollbackDialogOpen: boolean
  setFeedbackRollbackDialogOpen: React.Dispatch<React.SetStateAction<boolean>>
  feedbackRollbackReason: string
  setFeedbackRollbackReason: React.Dispatch<React.SetStateAction<string>>
  executeFeedbackRollback: () => Promise<void>

  /** 纠错数据读取错误文案（查询失败时局部呈现） */
  feedbackErrorText: string
}

export function useMemoryFeedback({
  active,
  initialSearch = '',
  initialTaskId = 0,
  onRuntimeChanged,
  onSourcesChanged,
}: UseMemoryFeedbackOptions): UseMemoryFeedbackResult {
  const { toast } = useToast()

  // 纠错历史列表：仅在纠错面板激活时拉取
  const correctionsQuery = useQuery({
    queryKey: ['memory-feedback', 'corrections'],
    queryFn: () => getMemoryFeedbackCorrections({ limit: FEEDBACK_CORRECTION_FETCH_LIMIT }),
    enabled: active,
  })
  const feedbackCorrections = useMemo(
    () => correctionsQuery.data?.items ?? [],
    [correctionsQuery.data?.items],
  )
  const feedbackErrorText = correctionsQuery.error
    ? correctionsQuery.error instanceof Error
      ? correctionsQuery.error.message
      : '加载纠错历史失败'
    : ''

  const [feedbackSearch, setFeedbackSearch] = useState(initialSearch)
  const [feedbackStatusFilter, setFeedbackStatusFilter] = useState('all')
  const [feedbackRollbackFilter, setFeedbackRollbackFilter] = useState('all')
  const [feedbackPage, setFeedbackPage] = useState(1)
  const [selectedFeedbackTaskId, setSelectedFeedbackTaskId] = useState(initialTaskId)
  const [selectedFeedbackTaskDetail, setSelectedFeedbackTaskDetail] = useState<MemoryFeedbackCorrectionDetailTaskPayload | null>(null)
  const [selectedFeedbackTaskLoading, setSelectedFeedbackTaskLoading] = useState(false)
  const [selectedFeedbackTaskError, setSelectedFeedbackTaskError] = useState('')
  const [feedbackActionLogSearch, setFeedbackActionLogSearch] = useState('')
  const [feedbackActionLogPage, setFeedbackActionLogPage] = useState(1)
  const [feedbackRollbackDialogOpen, setFeedbackRollbackDialogOpen] = useState(false)
  const [feedbackRollbackReason, setFeedbackRollbackReason] = useState('')
  const [feedbackRollingBack, setFeedbackRollingBack] = useState(false)

  const filteredFeedbackCorrections = useMemo(() => {
    const keyword = feedbackSearch.trim().toLowerCase()
    return feedbackCorrections.filter((item) => {
      const taskStatus = String(item.task_status ?? '').trim().toLowerCase()
      const rollbackStatus = String(item.rollback_status ?? '').trim().toLowerCase()
      if (feedbackStatusFilter !== 'all' && taskStatus !== feedbackStatusFilter) {
        return false
      }
      if (feedbackRollbackFilter !== 'all' && rollbackStatus !== feedbackRollbackFilter) {
        return false
      }
      if (!keyword) {
        return true
      }
      return [
        item.query_tool_id,
        item.session_id,
        item.query_text,
        item.decision,
        item.task_status,
        item.rollback_status,
      ]
        .map((value) => String(value ?? '').toLowerCase())
        .some((value) => value.includes(keyword))
    })
  }, [feedbackCorrections, feedbackRollbackFilter, feedbackSearch, feedbackStatusFilter])

  const feedbackPageCount = Math.max(1, Math.ceil(filteredFeedbackCorrections.length / FEEDBACK_CORRECTION_PAGE_SIZE))
  const pagedFeedbackCorrections = useMemo(() => {
    const start = (feedbackPage - 1) * FEEDBACK_CORRECTION_PAGE_SIZE
    return filteredFeedbackCorrections.slice(start, start + FEEDBACK_CORRECTION_PAGE_SIZE)
  }, [feedbackPage, filteredFeedbackCorrections])

  const selectedFeedbackCorrection = useMemo(
    () => {
      const matchedCorrection = filteredFeedbackCorrections.find((item) => item.task_id === selectedFeedbackTaskId)
      if (matchedCorrection) {
        return matchedCorrection
      }
      if (selectedFeedbackTaskId > 0) {
        return {
          task_id: selectedFeedbackTaskId,
          query_tool_id: '',
          session_id: '',
          query_text: '',
          task_status: '',
          decision: '',
          decision_confidence: 0,
          feedback_message_count: 0,
          rollback_status: '',
          affected_counts: {},
        } satisfies MemoryFeedbackCorrectionSummaryPayload
      }
      return pagedFeedbackCorrections[0] ?? null
    },
    [filteredFeedbackCorrections, pagedFeedbackCorrections, selectedFeedbackTaskId],
  )

  // 筛选变化 → 重置页码
  useEffect(() => {
    setFeedbackPage(1)
  }, [feedbackSearch, feedbackStatusFilter, feedbackRollbackFilter])

  // 页码超界 → 回拉到末页
  useEffect(() => {
    if (feedbackPage > feedbackPageCount) {
      setFeedbackPage(feedbackPageCount)
    }
  }, [feedbackPage, feedbackPageCount])

  // 选中纠错与列表对齐（选中项落空时回退/清空）
  useEffect(() => {
    if (!selectedFeedbackCorrection) {
      if (selectedFeedbackTaskId) {
        setSelectedFeedbackTaskId(0)
      }
      setSelectedFeedbackTaskDetail(null)
      setSelectedFeedbackTaskError('')
      return
    }
    if (selectedFeedbackCorrection.task_id !== selectedFeedbackTaskId) {
      setSelectedFeedbackTaskId(selectedFeedbackCorrection.task_id)
    }
  }, [selectedFeedbackCorrection, selectedFeedbackTaskId])

  // 选中纠错任务详情加载（面板激活时）
  useEffect(() => {
    if (!active) {
      return
    }
    const taskId = selectedFeedbackCorrection?.task_id
    if (!taskId) {
      setSelectedFeedbackTaskDetail(null)
      setSelectedFeedbackTaskError('')
      return
    }

    let cancelled = false
    setSelectedFeedbackTaskLoading(true)
    setSelectedFeedbackTaskError('')

    void getMemoryFeedbackCorrection(taskId)
      .then((payload) => {
        if (cancelled) {
          return
        }
        if (!payload.success || !payload.task) {
          setSelectedFeedbackTaskDetail(null)
          setSelectedFeedbackTaskError(payload.error || '未能加载纠错任务详情')
          return
        }
        setSelectedFeedbackTaskDetail(payload.task)
      })
      .catch((error) => {
        if (cancelled) {
          return
        }
        setSelectedFeedbackTaskDetail(null)
        setSelectedFeedbackTaskError(error instanceof Error ? error.message : '未能加载纠错任务详情')
      })
      .finally(() => {
        if (!cancelled) {
          setSelectedFeedbackTaskLoading(false)
        }
      })

    return () => {
      cancelled = true
    }
  }, [active, selectedFeedbackCorrection?.task_id])

  const selectedFeedbackResolved = useMemo<MemoryFeedbackCorrectionDetailTaskPayload | null>(() => {
    if (!selectedFeedbackCorrection) {
      return null
    }
    if (selectedFeedbackTaskDetail?.task_id === selectedFeedbackCorrection.task_id) {
      return {
        ...selectedFeedbackCorrection,
        ...selectedFeedbackTaskDetail,
      } satisfies MemoryFeedbackCorrectionDetailTaskPayload
    }
    return selectedFeedbackTaskDetail ?? selectedFeedbackCorrection
  }, [selectedFeedbackCorrection, selectedFeedbackTaskDetail])
  const selectedFeedbackPreview = useMemo(
    () => getFeedbackCorrectionPreview(selectedFeedbackResolved),
    [selectedFeedbackResolved],
  )
  const selectedFeedbackImpactSummary = useMemo(
    () => buildFeedbackImpactSummary(selectedFeedbackResolved),
    [selectedFeedbackResolved],
  )

  const selectedFeedbackActionLogs: MemoryFeedbackActionLogPayload[] = useMemo(
    () => (Array.isArray(selectedFeedbackResolved?.action_logs) ? selectedFeedbackResolved.action_logs : []),
    [selectedFeedbackResolved?.action_logs],
  )
  const filteredFeedbackActionLogs = useMemo(() => {
    const keyword = feedbackActionLogSearch.trim().toLowerCase()
    if (!keyword) {
      return selectedFeedbackActionLogs
    }
    return selectedFeedbackActionLogs.filter((item) =>
      [
        item.action_type,
        item.target_hash,
        item.reason,
        summarizeFeedbackActionPayload(item.before_payload),
        summarizeFeedbackActionPayload(item.after_payload),
      ]
        .map((value) => String(value ?? '').toLowerCase())
        .some((value) => value.includes(keyword)),
    )
  }, [feedbackActionLogSearch, selectedFeedbackActionLogs])
  const feedbackActionLogPageCount = Math.max(
    1,
    Math.ceil(filteredFeedbackActionLogs.length / FEEDBACK_ACTION_LOG_PAGE_SIZE),
  )
  const pagedFeedbackActionLogs = useMemo(() => {
    const start = (feedbackActionLogPage - 1) * FEEDBACK_ACTION_LOG_PAGE_SIZE
    return filteredFeedbackActionLogs.slice(start, start + FEEDBACK_ACTION_LOG_PAGE_SIZE)
  }, [feedbackActionLogPage, filteredFeedbackActionLogs])

  useEffect(() => {
    setFeedbackActionLogPage(1)
  }, [selectedFeedbackTaskId, feedbackActionLogSearch])

  useEffect(() => {
    if (feedbackActionLogPage > feedbackActionLogPageCount) {
      setFeedbackActionLogPage(feedbackActionLogPageCount)
    }
  }, [feedbackActionLogPage, feedbackActionLogPageCount])

  const openFeedbackRollbackDialog = useCallback(() => {
    setFeedbackRollbackReason('')
    setFeedbackRollbackDialogOpen(true)
  }, [])

  const executeFeedbackRollback = useCallback(async () => {
    const taskId = selectedFeedbackResolved?.task_id
    if (!taskId) {
      return
    }
    try {
      setFeedbackRollingBack(true)
      const payload = await rollbackMemoryFeedbackCorrection(taskId, {
        requested_by: 'knowledge_base',
        reason: feedbackRollbackReason.trim(),
      })
      if (!payload.success) {
        throw new Error(payload.error || '回退失败')
      }
      toast({
        title: payload.already_rolled_back ? '该纠错已回退' : '纠错回退成功',
        description: `任务 ${taskId} 的回退结果已写入日志`,
      })
      setFeedbackRollbackDialogOpen(false)
      const [listPayload, detailPayload] = await Promise.all([
        correctionsQuery.refetch(),
        getMemoryFeedbackCorrection(taskId),
      ])
      void listPayload
      setSelectedFeedbackTaskDetail(detailPayload.task ?? null)
      // 回退会影响来源与运行时状态，回调通知对应领域 hook 重拉（原页面同时刷新 sources / runtimeConfig）
      await Promise.all([onSourcesChanged?.(), onRuntimeChanged?.()])
    } catch (error) {
      toast({
        title: '纠错回退失败',
        description: error instanceof Error ? error.message : '未知错误',
        variant: 'destructive',
      })
    } finally {
      setFeedbackRollingBack(false)
    }
  }, [correctionsQuery, feedbackRollbackReason, onRuntimeChanged, onSourcesChanged, selectedFeedbackResolved?.task_id, toast])

  return {
    feedbackSearch,
    setFeedbackSearch,
    feedbackStatusFilter,
    setFeedbackStatusFilter,
    feedbackRollbackFilter,
    setFeedbackRollbackFilter,
    filteredFeedbackCorrections,
    feedbackCorrections,
    pagedFeedbackCorrections,
    feedbackPage,
    setFeedbackPage,
    feedbackPageCount,
    selectedFeedbackCorrection,
    setSelectedFeedbackTaskId,
    selectedFeedbackResolved,
    selectedFeedbackPreview,
    selectedFeedbackImpactSummary,
    openFeedbackRollbackDialog,
    feedbackRollingBack,
    selectedFeedbackTaskLoading,
    selectedFeedbackTaskError,
    feedbackActionLogPage,
    setFeedbackActionLogPage,
    feedbackActionLogPageCount,
    feedbackActionLogSearch,
    setFeedbackActionLogSearch,
    pagedFeedbackActionLogs,
    selectedFeedbackActionLogs,
    feedbackRollbackDialogOpen,
    setFeedbackRollbackDialogOpen,
    feedbackRollbackReason,
    setFeedbackRollbackReason,
    executeFeedbackRollback,
    feedbackErrorText,
  }
}
