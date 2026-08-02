import { useCallback, useEffect, useMemo, useState } from 'react'

import { useQuery } from '@tanstack/react-query'

import { useToast } from '@/hooks/use-toast'
import {
  executeMemoryCorrection,
  getMemoryImportChatTargets,
  getMemoryCorrectionPlan,
  getMemoryCorrectionPlans,
  previewMemoryCorrection,
  rollbackMemoryCorrectionPlan,
  type MemoryImportChatTargetPayload,
  type MemoryCorrectionPlanPayload,
  type MemoryCorrectionPreviewContentPayload,
  type MemoryCorrectionPreviewPayload,
  type MemoryCorrectionScope,
  type MemoryRuntimeConfigPayload,
} from '@/lib/memory-api'

import { MEMORY_CORRECTION_FETCH_LIMIT, MEMORY_CORRECTION_PAGE_SIZE } from '../constants'

export interface UseMemoryCorrectionOptions {
  active: boolean
  runtimeConfig?: MemoryRuntimeConfigPayload | null
  initialPlanId?: string
  initialPersonId?: string
  initialChatId?: string
  onRuntimeChanged?: () => Promise<void> | void
  onSourcesChanged?: () => Promise<void> | void
}

export interface UseMemoryCorrectionResult {
  requestText: string
  setRequestText: React.Dispatch<React.SetStateAction<string>>
  scope: MemoryCorrectionScope
  setScope: React.Dispatch<React.SetStateAction<MemoryCorrectionScope>>
  personId: string
  setPersonId: React.Dispatch<React.SetStateAction<string>>
  personKeyword: string
  setPersonKeyword: React.Dispatch<React.SetStateAction<string>>
  chatId: string
  setChatId: React.Dispatch<React.SetStateAction<string>>
  candidateLimit: string
  setCandidateLimit: React.Dispatch<React.SetStateAction<string>>
  candidateLimitMax: number | null
  correctionReason: string
  setCorrectionReason: React.Dispatch<React.SetStateAction<string>>
  planSearch: string
  setPlanSearch: React.Dispatch<React.SetStateAction<string>>
  planStatusFilter: string
  setPlanStatusFilter: React.Dispatch<React.SetStateAction<string>>
  planScopeFilter: string
  setPlanScopeFilter: React.Dispatch<React.SetStateAction<string>>
  plans: MemoryCorrectionPlanPayload[]
  filteredPlans: MemoryCorrectionPlanPayload[]
  pagedPlans: MemoryCorrectionPlanPayload[]
  planPage: number
  setPlanPage: React.Dispatch<React.SetStateAction<number>>
  planPageCount: number
  selectedPlanId: string
  setSelectedPlanId: React.Dispatch<React.SetStateAction<string>>
  selectedPlan: MemoryCorrectionPlanPayload | null
  selectedPreview: MemoryCorrectionPreviewContentPayload | null
  selectedPlanLoading: boolean
  selectedPlanError: string
  chatTargets: MemoryImportChatTargetPayload[]
  chatTargetsLoading: boolean
  chatTargetsErrorText: string
  correctionErrorText: string
  previewPayload: MemoryCorrectionPreviewPayload | null
  previewing: boolean
  executingPlanId: string
  rollingBackPlanId: string
  submitPreview: () => Promise<void>
  executePlan: (planId?: string) => Promise<void>
  rollbackPlan: (planId?: string) => Promise<void>
  refreshPlans: () => Promise<void>
}

function normalizePositiveInteger(value: unknown): number | null {
  const parsed = Number(value)
  if (!Number.isFinite(parsed) || parsed < 1) {
    return null
  }
  return Math.floor(parsed)
}

function readNestedValue(source: unknown, path: string[]): unknown {
  let current = source
  for (const key of path) {
    if (!current || typeof current !== 'object') {
      return undefined
    }
    current = (current as Record<string, unknown>)[key]
  }
  return current
}

function resolveConfiguredCandidateLimit(
  runtimeConfig?: MemoryRuntimeConfigPayload | null
): number | null {
  return (
    normalizePositiveInteger(runtimeConfig?.fuzzy_modify_candidate_limit) ??
    normalizePositiveInteger(
      readNestedValue(runtimeConfig?.config, ['integration', 'fuzzy_modify_candidate_limit'])
    ) ??
    normalizePositiveInteger(
      readNestedValue(runtimeConfig?.config, [
        'a_memorix',
        'integration',
        'fuzzy_modify_candidate_limit',
      ])
    )
  )
}

function formatCandidateLimit(value: number | null): string {
  return value === null ? '' : String(value)
}

function parseCandidateLimit(value: string, configuredLimit: number | null): number | undefined {
  if (!value.trim()) {
    return configuredLimit ?? undefined
  }
  const parsed = Number(value)
  if (!Number.isFinite(parsed)) {
    return configuredLimit ?? undefined
  }
  const normalized = Math.max(1, Math.floor(parsed))
  return configuredLimit === null ? normalized : Math.min(configuredLimit, normalized)
}

function getPlanSearchText(plan: MemoryCorrectionPlanPayload): string {
  return [
    plan.plan_id,
    plan.request_text,
    plan.scope,
    plan.status,
    plan.target_person_id,
    plan.target_chat_id,
    plan.requested_by,
    plan.reason,
    plan.plan?.reason,
    plan.preview?.reason,
  ]
    .map((value) => String(value ?? '').toLowerCase())
    .join('\n')
}

export function useMemoryCorrection({
  active,
  runtimeConfig,
  initialPlanId = '',
  initialPersonId = '',
  initialChatId = '',
  onRuntimeChanged,
  onSourcesChanged,
}: UseMemoryCorrectionOptions): UseMemoryCorrectionResult {
  const { toast } = useToast()
  const configuredCandidateLimit = useMemo(
    () => resolveConfiguredCandidateLimit(runtimeConfig),
    [runtimeConfig]
  )

  const plansQuery = useQuery({
    queryKey: ['memory-correction', 'plans'],
    queryFn: () => getMemoryCorrectionPlans({ limit: MEMORY_CORRECTION_FETCH_LIMIT }),
    enabled: active,
  })
  const chatTargetsQuery = useQuery({
    queryKey: ['memory-correction', 'chat-targets'],
    queryFn: getMemoryImportChatTargets,
    enabled: active,
    staleTime: 60_000,
  })

  const [requestText, setRequestText] = useState('')
  const [scope, setScope] = useState<MemoryCorrectionScope>('person_profile')
  const [personId, setPersonId] = useState(initialPersonId)
  const [personKeyword, setPersonKeyword] = useState('')
  const [chatId, setChatId] = useState(initialChatId)
  const [candidateLimit, setCandidateLimitValue] = useState(() =>
    formatCandidateLimit(configuredCandidateLimit)
  )
  const [candidateLimitEdited, setCandidateLimitEdited] = useState(false)
  const [correctionReason, setCorrectionReason] = useState('')
  const [planSearch, setPlanSearch] = useState(initialPlanId || initialPersonId || initialChatId)
  const [planStatusFilter, setPlanStatusFilter] = useState('all')
  const [planScopeFilter, setPlanScopeFilter] = useState('all')
  const [planPage, setPlanPage] = useState(1)
  const [selectedPlanId, setSelectedPlanId] = useState(initialPlanId)
  const [selectedPlanDetail, setSelectedPlanDetail] = useState<MemoryCorrectionPlanPayload | null>(
    null
  )
  const [selectedPlanLoading, setSelectedPlanLoading] = useState(false)
  const [selectedPlanError, setSelectedPlanError] = useState('')
  const [previewPayload, setPreviewPayload] = useState<MemoryCorrectionPreviewPayload | null>(null)
  const [previewing, setPreviewing] = useState(false)
  const [executingPlanId, setExecutingPlanId] = useState('')
  const [rollingBackPlanId, setRollingBackPlanId] = useState('')

  const setCandidateLimit = useCallback<React.Dispatch<React.SetStateAction<string>>>(
    (nextValue) => {
      setCandidateLimitEdited(true)
      setCandidateLimitValue((current) =>
        typeof nextValue === 'function' ? nextValue(current) : nextValue
      )
    },
    []
  )

  useEffect(() => {
    if (!candidateLimitEdited) {
      setCandidateLimitValue(formatCandidateLimit(configuredCandidateLimit))
    }
  }, [candidateLimitEdited, configuredCandidateLimit])

  const plans = useMemo(
    () => (plansQuery.data?.items ?? []).filter((plan) => Boolean(plan?.plan_id)),
    [plansQuery.data?.items]
  )
  const correctionErrorText = plansQuery.error
    ? plansQuery.error instanceof Error
      ? plansQuery.error.message
      : '加载记忆修正计划失败'
    : ''
  const chatTargets = useMemo(
    () => chatTargetsQuery.data?.data ?? [],
    [chatTargetsQuery.data?.data]
  )
  const chatTargetsErrorText = chatTargetsQuery.error
    ? chatTargetsQuery.error instanceof Error
      ? chatTargetsQuery.error.message
      : '加载聊天流列表失败'
    : ''

  const filteredPlans = useMemo(() => {
    const keyword = planSearch.trim().toLowerCase()
    return plans.filter((plan) => {
      const status = String(plan.status ?? '')
        .trim()
        .toLowerCase()
      const itemScope = String(plan.scope ?? '')
        .trim()
        .toLowerCase()
      if (planStatusFilter !== 'all' && status !== planStatusFilter) {
        return false
      }
      if (planScopeFilter !== 'all' && itemScope !== planScopeFilter) {
        return false
      }
      if (!keyword) {
        return true
      }
      return getPlanSearchText(plan).includes(keyword)
    })
  }, [planScopeFilter, planSearch, planStatusFilter, plans])

  const planPageCount = Math.max(1, Math.ceil(filteredPlans.length / MEMORY_CORRECTION_PAGE_SIZE))
  const pagedPlans = useMemo(() => {
    const start = (planPage - 1) * MEMORY_CORRECTION_PAGE_SIZE
    return filteredPlans.slice(start, start + MEMORY_CORRECTION_PAGE_SIZE)
  }, [filteredPlans, planPage])

  useEffect(() => {
    setPlanPage(1)
  }, [planSearch, planScopeFilter, planStatusFilter])

  useEffect(() => {
    if (planPage > planPageCount) {
      setPlanPage(planPageCount)
    }
  }, [planPage, planPageCount])

  const selectedPlanFromList = useMemo(() => {
    if (selectedPlanId) {
      return (
        filteredPlans.find((plan) => plan.plan_id === selectedPlanId) ??
        plans.find((plan) => plan.plan_id === selectedPlanId) ??
        null
      )
    }
    return pagedPlans[0] ?? null
  }, [filteredPlans, pagedPlans, plans, selectedPlanId])

  useEffect(() => {
    if (!selectedPlanFromList) {
      if (selectedPlanId && !initialPlanId) {
        setSelectedPlanId('')
      }
      setSelectedPlanDetail(null)
      setSelectedPlanError('')
      return
    }
    if (!selectedPlanId) {
      setSelectedPlanId(selectedPlanFromList.plan_id)
    }
  }, [initialPlanId, selectedPlanFromList, selectedPlanId])

  useEffect(() => {
    if (!active) {
      return
    }
    const planId = selectedPlanFromList?.plan_id || selectedPlanId
    if (!planId) {
      setSelectedPlanDetail(null)
      setSelectedPlanError('')
      return
    }

    let cancelled = false
    setSelectedPlanLoading(true)
    setSelectedPlanError('')

    void getMemoryCorrectionPlan(planId)
      .then((payload) => {
        if (cancelled) {
          return
        }
        if (!payload.success || !payload.plan) {
          setSelectedPlanDetail(null)
          setSelectedPlanError(payload.error || '未能加载记忆修正计划详情')
          return
        }
        setSelectedPlanDetail(payload.plan)
      })
      .catch((error) => {
        if (cancelled) {
          return
        }
        setSelectedPlanDetail(null)
        setSelectedPlanError(error instanceof Error ? error.message : '未能加载记忆修正计划详情')
      })
      .finally(() => {
        if (!cancelled) {
          setSelectedPlanLoading(false)
        }
      })

    return () => {
      cancelled = true
    }
  }, [active, selectedPlanFromList?.plan_id, selectedPlanId])

  const selectedPlan = useMemo<MemoryCorrectionPlanPayload | null>(() => {
    if (
      selectedPlanDetail &&
      selectedPlanFromList &&
      selectedPlanDetail.plan_id === selectedPlanFromList.plan_id
    ) {
      return {
        ...selectedPlanFromList,
        ...selectedPlanDetail,
      }
    }
    return selectedPlanDetail ?? selectedPlanFromList
  }, [selectedPlanDetail, selectedPlanFromList])

  const selectedPreview = useMemo<MemoryCorrectionPreviewContentPayload | null>(() => {
    if (
      previewPayload?.plan_id &&
      previewPayload.plan_id === selectedPlan?.plan_id &&
      previewPayload.preview
    ) {
      return previewPayload.preview
    }
    return selectedPlan?.preview ?? null
  }, [previewPayload, selectedPlan?.plan_id, selectedPlan?.preview])

  const refreshPlans = useCallback(async () => {
    await plansQuery.refetch()
  }, [plansQuery])

  const warnSyncFailure = useCallback(
    (title: string, error?: unknown) => {
      toast({
        title,
        description: error instanceof Error ? error.message : '请手动刷新后确认最新状态',
        variant: 'destructive',
      })
    },
    [toast]
  )

  const submitPreview = useCallback(async () => {
    const trimmedRequest = requestText.trim()
    if (!trimmedRequest) {
      toast({
        title: '缺少修正内容',
        description: '请填写需要修正的记忆内容',
        variant: 'destructive',
      })
      return
    }
    if (scope === 'person_profile' && !personId.trim() && !personKeyword.trim()) {
      toast({
        title: '缺少人物定位信息',
        description: '人物画像修正需要填写人物 ID 或人物关键词',
        variant: 'destructive',
      })
      return
    }

    try {
      setPreviewing(true)
      const payload = await previewMemoryCorrection({
        request_text: trimmedRequest,
        scope,
        person_id: personId.trim() || undefined,
        person_keyword: personKeyword.trim() || undefined,
        chat_id: chatId.trim() || undefined,
        limit: parseCandidateLimit(candidateLimit, configuredCandidateLimit),
        requested_by: 'knowledge_base',
        reason: correctionReason.trim(),
      })
      setPreviewPayload(payload)
      if (!payload.success) {
        throw new Error(payload.error || '生成记忆修正预览失败')
      }
      const planId = payload.plan_id || payload.plan?.plan_id || ''
      if (planId) {
        setSelectedPlanId(planId)
        setPlanSearch(planId)
      }
      toast({
        title: '已生成记忆修正预览',
        description: planId ? `计划 ${planId} 等待确认` : '请检查预览结果后再确认执行',
      })
      try {
        await plansQuery.refetch()
        if (planId) {
          const detailPayload = await getMemoryCorrectionPlan(planId)
          setSelectedPlanDetail(detailPayload.plan ?? null)
        }
      } catch (syncError) {
        warnSyncFailure('预览已生成，但界面同步失败', syncError)
      }
    } catch (error) {
      toast({
        title: '生成记忆修正预览失败',
        description: error instanceof Error ? error.message : '未知错误',
        variant: 'destructive',
      })
    } finally {
      setPreviewing(false)
    }
  }, [
    candidateLimit,
    chatId,
    configuredCandidateLimit,
    correctionReason,
    personId,
    personKeyword,
    plansQuery,
    requestText,
    scope,
    toast,
    warnSyncFailure,
  ])

  const executePlan = useCallback(
    async (planId = selectedPlan?.plan_id || '') => {
      const targetPlanId = planId.trim()
      if (!targetPlanId) {
        return
      }
      try {
        setExecutingPlanId(targetPlanId)
        const payload = await executeMemoryCorrection({
          plan_id: targetPlanId,
          confirmed: true,
          requested_by: 'knowledge_base',
          reason: correctionReason.trim(),
        })
        if (!payload.success) {
          throw new Error(payload.error || '执行记忆修正失败')
        }
        toast({
          title: '记忆修正已执行',
          description: `计划 ${targetPlanId} 已写入执行结果`,
        })
        if (payload.plan) {
          setSelectedPlanDetail(payload.plan)
        } else {
          try {
            const detailPayload = await getMemoryCorrectionPlan(targetPlanId)
            setSelectedPlanDetail(detailPayload.plan ?? null)
          } catch (syncError) {
            warnSyncFailure('执行已完成，但详情同步失败', syncError)
          }
        }
        const syncResults = await Promise.allSettled([
          plansQuery.refetch(),
          onSourcesChanged?.(),
          onRuntimeChanged?.(),
        ])
        const failedSync = syncResults.find((result) => result.status === 'rejected')
        if (failedSync?.status === 'rejected') {
          warnSyncFailure('执行已完成，但界面同步未完全成功', failedSync.reason)
        }
      } catch (error) {
        toast({
          title: '执行记忆修正失败',
          description: error instanceof Error ? error.message : '未知错误',
          variant: 'destructive',
        })
      } finally {
        setExecutingPlanId('')
      }
    },
    [
      correctionReason,
      onRuntimeChanged,
      onSourcesChanged,
      plansQuery,
      selectedPlan?.plan_id,
      toast,
      warnSyncFailure,
    ]
  )

  const rollbackPlan = useCallback(
    async (planId = selectedPlan?.plan_id || '') => {
      const targetPlanId = planId.trim()
      if (!targetPlanId) {
        return
      }
      try {
        setRollingBackPlanId(targetPlanId)
        const payload = await rollbackMemoryCorrectionPlan(targetPlanId, {
          requested_by: 'knowledge_base',
          reason: correctionReason.trim(),
        })
        if (!payload.success) {
          throw new Error(payload.error || '回滚记忆修正失败')
        }
        toast({
          title: '记忆修正已回滚',
          description: `计划 ${targetPlanId} 的回滚结果已写入日志`,
        })
        if (payload.plan) {
          setSelectedPlanDetail(payload.plan)
        } else {
          try {
            const detailPayload = await getMemoryCorrectionPlan(targetPlanId)
            setSelectedPlanDetail(detailPayload.plan ?? null)
          } catch (syncError) {
            warnSyncFailure('回滚已完成，但详情同步失败', syncError)
          }
        }
        const syncResults = await Promise.allSettled([
          plansQuery.refetch(),
          onSourcesChanged?.(),
          onRuntimeChanged?.(),
        ])
        const failedSync = syncResults.find((result) => result.status === 'rejected')
        if (failedSync?.status === 'rejected') {
          warnSyncFailure('回滚已完成，但界面同步未完全成功', failedSync.reason)
        }
      } catch (error) {
        toast({
          title: '回滚记忆修正失败',
          description: error instanceof Error ? error.message : '未知错误',
          variant: 'destructive',
        })
      } finally {
        setRollingBackPlanId('')
      }
    },
    [
      correctionReason,
      onRuntimeChanged,
      onSourcesChanged,
      plansQuery,
      selectedPlan?.plan_id,
      toast,
      warnSyncFailure,
    ]
  )

  return {
    requestText,
    setRequestText,
    scope,
    setScope,
    personId,
    setPersonId,
    personKeyword,
    setPersonKeyword,
    chatId,
    setChatId,
    candidateLimit,
    setCandidateLimit,
    candidateLimitMax: configuredCandidateLimit,
    correctionReason,
    setCorrectionReason,
    planSearch,
    setPlanSearch,
    planStatusFilter,
    setPlanStatusFilter,
    planScopeFilter,
    setPlanScopeFilter,
    plans,
    filteredPlans,
    pagedPlans,
    planPage,
    setPlanPage,
    planPageCount,
    selectedPlanId,
    setSelectedPlanId,
    selectedPlan,
    selectedPreview,
    selectedPlanLoading,
    selectedPlanError,
    chatTargets,
    chatTargetsLoading: chatTargetsQuery.isLoading,
    chatTargetsErrorText,
    correctionErrorText,
    previewPayload,
    previewing,
    executingPlanId,
    rollingBackPlanId,
    submitPreview,
    executePlan,
    rollbackPlan,
    refreshPlans,
  }
}
