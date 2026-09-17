/**
 * useMemoryProfileConsole —— 长期记忆「人物画像」领域 hook（页面逻辑下沉切片）。
 *
 * 收编人物画像相关的服务端状态与交互，供两个标签面板共享同一份状态：
 * - ProfileSearchPanel（记忆查询 → 人物画像子视图）：查询卡 + 详情卡；
 * - ProfileMaintenancePanel（记忆检修 → 画像维护子模式）：支撑证据 + 别名维护 + 画像覆写。
 *
 * 支撑证据的纠错与画像快照刷新都依赖「当前定位人物」与「证据数量」，因此这些状态必须由
 * 共同祖先持有；面板分居两个标签，故状态上提到本 hook，由页面实例化一次后传给两侧。
 *
 * active 用于懒加载：只有人物画像视图（查询侧或维护侧）可见时才拉取画像库与定位画像，
 * 与 useMemoryTuning / useMemoryCorrection 的 enabled: active 约定一致。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { useToast } from '@/hooks/use-toast'
import {
  correctMemoryProfileEvidence,
  deleteMemoryProfileAliases,
  deleteMemoryProfileOverride,
  getMemoryProfileAliases,
  getMemoryProfileEvidence,
  getMemoryProfiles,
  queryMemoryProfile,
  searchMemoryProfiles,
  setMemoryProfileAliases,
  setMemoryProfileOverride,
  type MemoryProfileAliasesPayload,
  type MemoryProfileEvidenceItemPayload,
  type MemoryProfileEvidencePayload,
  type MemoryProfileItemPayload,
  type MemoryProfileQueryPayload,
} from '@/lib/memory-api'

import {
  parseAliasText,
  parsePositiveInt,
  resolveProfileText,
  stringifyOverride,
} from '../profile-utils'

export type ProfileQueryMode = 'exact' | 'fuzzy'

/**
 * 查询画像时请求的证据采样量。
 * 该值会传导到重建画像时的证据采集宽度，但画像正文各小节合计只有约 25 条上限，
 * 12 已足以喂满，故不做成可调项；页面上可调的「证据数量」只决定检修侧展示多少条证据。
 */
const PROFILE_QUERY_EVIDENCE_LIMIT = 12

export interface UseMemoryProfileConsoleOptions {
  /** 人物画像视图是否可见；不可见时不拉取画像库，也不消费 initialPersonId */
  active: boolean
  /** 外部跳转带来的定位人物（记录详情、审计时间线） */
  initialPersonId?: string
  /**
   * 定位请求序号：每次外部跳转自增。
   * hook 常驻页面生命周期，单靠 initialPersonId 的值无法区分「重复定位同一个人」，
   * 也无法避免证据数量等依赖变化误触发定位，故用自增序号作为一次性定位的判据。
   */
  locateToken?: number
}

export interface UseMemoryProfileConsoleResult {
  // 查询参数与模式
  queryMode: ProfileQueryMode
  setQueryMode: React.Dispatch<React.SetStateAction<ProfileQueryMode>>
  queryPlatform: string
  setQueryPlatform: React.Dispatch<React.SetStateAction<string>>
  queryUserId: string
  setQueryUserId: React.Dispatch<React.SetStateAction<string>>
  queryKeyword: string
  setQueryKeyword: React.Dispatch<React.SetStateAction<string>>
  /** 检修侧展示的证据条数上限（同一位用户可能有多条证据，这里只决定列多少条） */
  evidenceLimit: string
  setEvidenceLimit: React.Dispatch<React.SetStateAction<string>>
  // 查询执行与候选列表
  submitQuery: () => Promise<void>
  loadProfiles: () => Promise<void>
  loading: boolean
  querying: boolean
  profileListMode: 'library' | 'search'
  profiles: MemoryProfileItemPayload[]
  selectProfile: (personId: string) => void
  // 当前定位与详情
  selectedPersonId: string
  selectedProfile: MemoryProfileItemPayload | null
  selectedDisplayName: string
  activePersonId: string
  queryResult: MemoryProfileQueryPayload | null
  profileEvidence: MemoryProfileEvidencePayload | null
  currentProfileEvidence: MemoryProfileEvidencePayload | null
  displayedProfileText: string
  showAutoProfile: boolean
  setShowAutoProfile: React.Dispatch<React.SetStateAction<boolean>>
  showRawProfilePayload: boolean
  setShowRawProfilePayload: React.Dispatch<React.SetStateAction<boolean>>
  /** 画像详情弹窗开关；详情不再常驻页面，与文字记录一样点击候选项才弹出 */
  detailOpen: boolean
  setDetailOpen: React.Dispatch<React.SetStateAction<boolean>>
  // 支撑证据
  evidenceLoading: boolean
  loadProfileEvidence: (
    personId: string,
    options?: { forceRefresh?: boolean }
  ) => Promise<MemoryProfileEvidencePayload | null>
  correctEvidence: (item: MemoryProfileEvidenceItemPayload) => Promise<void>
  correctingEvidenceKey: string
  // 别名维护
  profileAliases: MemoryProfileAliasesPayload | null
  aliasText: string
  setAliasText: React.Dispatch<React.SetStateAction<string>>
  aliasLoading: boolean
  aliasSaving: boolean
  saveAliases: () => Promise<void>
  addSuggestedAlias: (alias: string) => void
  restoreDerivedAliases: () => Promise<void>
  // 画像覆写
  overrideText: string
  setOverrideText: React.Dispatch<React.SetStateAction<string>>
  saving: boolean
  saveOverride: () => Promise<void>
  deleteOverride: () => Promise<void>
}

export function useMemoryProfileConsole({
  active,
  initialPersonId = '',
  locateToken = 0,
}: UseMemoryProfileConsoleOptions): UseMemoryProfileConsoleResult {
  const { toast } = useToast()
  const [profiles, setProfiles] = useState<MemoryProfileItemPayload[]>([])
  const [profileListMode, setProfileListMode] = useState<'library' | 'search'>('library')
  const [selectedPersonId, setSelectedPersonId] = useState('')
  const [queryKeyword, setQueryKeyword] = useState('')
  const [queryPlatform, setQueryPlatform] = useState('')
  const [queryUserId, setQueryUserId] = useState('')
  const [evidenceLimit, setEvidenceLimit] = useState('12')
  const [queryMode, setQueryMode] = useState<ProfileQueryMode>('exact')
  const [showRawProfilePayload, setShowRawProfilePayload] = useState(false)
  const [detailOpen, setDetailOpen] = useState(false)
  const [overrideText, setOverrideText] = useState('')
  const [aliasText, setAliasText] = useState('')
  const [profileAliases, setProfileAliases] = useState<MemoryProfileAliasesPayload | null>(null)
  const [queryResult, setQueryResult] = useState<MemoryProfileQueryPayload | null>(null)
  const [profileEvidence, setProfileEvidence] = useState<MemoryProfileEvidencePayload | null>(null)
  const [showAutoProfile, setShowAutoProfile] = useState(false)
  const [loading, setLoading] = useState(false)
  const [querying, setQuerying] = useState(false)
  const [saving, setSaving] = useState(false)
  const [aliasLoading, setAliasLoading] = useState(false)
  const [aliasSaving, setAliasSaving] = useState(false)
  const [evidenceLoading, setEvidenceLoading] = useState(false)
  const [correctingEvidenceKey, setCorrectingEvidenceKey] = useState('')
  const initialLoadedRef = useRef(false)
  const appliedLocateTokenRef = useRef(0)
  const aliasRequestIdRef = useRef(0)

  const selectedProfile = useMemo(
    () => profiles.find((item) => item.person_id === selectedPersonId) ?? null,
    [profiles, selectedPersonId]
  )
  const profileText = resolveProfileText(queryResult, selectedProfile)
  const selectedDisplayName =
    selectedProfile?.person_name || selectedPersonId || String(queryResult?.person_id ?? '未选择')
  const activePersonId =
    selectedPersonId || String(queryResult?.person_id ?? profileEvidence?.person_id ?? '')
  const profileEvidencePersonId = String(profileEvidence?.person_id ?? '').trim()
  const currentProfileEvidence =
    profileEvidencePersonId && profileEvidencePersonId === activePersonId.trim()
      ? profileEvidence
      : null
  const displayedProfileText =
    showAutoProfile && typeof currentProfileEvidence?.auto_profile_text === 'string'
      ? currentProfileEvidence.auto_profile_text
      : typeof currentProfileEvidence?.profile_text === 'string'
        ? currentProfileEvidence.profile_text
        : profileText

  const loadProfiles = useCallback(async () => {
    setLoading(true)
    try {
      const payload = await getMemoryProfiles(80)
      const nextItems = payload.items ?? []
      setProfiles(nextItems)
      setProfileListMode('library')
      if (nextItems.length > 0) {
        setSelectedPersonId((current) => current || nextItems[0].person_id)
      }
    } catch (error) {
      toast({
        title: '加载人物画像失败',
        description: error instanceof Error ? error.message : String(error),
        variant: 'destructive',
      })
    } finally {
      setLoading(false)
    }
  }, [toast])

  useEffect(() => {
    if (!active || initialLoadedRef.current) {
      return
    }
    initialLoadedRef.current = true
    void loadProfiles()
  }, [active, loadProfiles])

  useEffect(() => {
    setOverrideText(stringifyOverride(selectedProfile?.manual_override))
  }, [selectedProfile])

  const loadProfileEvidence = useCallback(
    async (personId: string, options?: { forceRefresh?: boolean }) => {
      const cleanPersonId = personId.trim()
      if (!cleanPersonId) {
        setProfileEvidence(null)
        return null
      }
      setEvidenceLoading(true)
      try {
        const payload = await getMemoryProfileEvidence({
          personId: cleanPersonId,
          limit: parsePositiveInt(evidenceLimit, PROFILE_QUERY_EVIDENCE_LIMIT),
          forceRefresh: Boolean(options?.forceRefresh),
        })
        if (payload.success === false) {
          throw new Error(String(payload.error ?? '画像证据查询失败'))
        }
        setProfileEvidence(payload)
        return payload
      } catch (error) {
        toast({
          title: '加载画像证据失败',
          description: error instanceof Error ? error.message : String(error),
          variant: 'destructive',
        })
        return null
      } finally {
        setEvidenceLoading(false)
      }
    },
    [evidenceLimit, toast]
  )

  const loadProfileAliases = useCallback(
    async (personId: string) => {
      const cleanPersonId = personId.trim()
      const requestId = ++aliasRequestIdRef.current
      if (!cleanPersonId) {
        setProfileAliases(null)
        setAliasText('')
        setAliasLoading(false)
        return null
      }
      setAliasLoading(true)
      setProfileAliases(null)
      setAliasText('')
      try {
        const payload = await getMemoryProfileAliases(cleanPersonId)
        if (!payload.success) {
          throw new Error(String(payload.error ?? '人物别名查询失败'))
        }
        if (requestId !== aliasRequestIdRef.current) {
          return null
        }
        setProfileAliases(payload)
        setAliasText((payload.effective_aliases ?? []).join('\n'))
        return payload
      } catch (error) {
        if (requestId !== aliasRequestIdRef.current) {
          return null
        }
        setProfileAliases(null)
        setAliasText('')
        toast({
          title: '加载人物别名失败',
          description: error instanceof Error ? error.message : String(error),
          variant: 'destructive',
        })
        return null
      } finally {
        if (requestId === aliasRequestIdRef.current) {
          setAliasLoading(false)
        }
      }
    },
    [toast]
  )

  useEffect(() => {
    if (!selectedPersonId || profileEvidencePersonId === selectedPersonId || queryResult) {
      return
    }
    void loadProfileEvidence(selectedPersonId)
  }, [loadProfileEvidence, profileEvidencePersonId, queryResult, selectedPersonId])

  useEffect(() => {
    if (!activePersonId.trim()) {
      aliasRequestIdRef.current += 1
      setProfileAliases(null)
      setAliasText('')
      setAliasLoading(false)
      return
    }
    void loadProfileAliases(activePersonId)
  }, [activePersonId, loadProfileAliases])

  const submitQuery = useCallback(async () => {
    const cleanKeyword = queryKeyword.trim()
    const cleanPlatform = queryPlatform.trim()
    const cleanUserId = queryUserId.trim()
    const hasAccountLocator = Boolean(cleanPlatform && cleanUserId)

    if (queryMode === 'fuzzy' && !cleanKeyword) {
      toast({
        title: '请输入查询条件',
        description: '请输入用于匹配人物名称或画像内容的关键词。',
        variant: 'destructive',
      })
      return
    }
    if (queryMode === 'exact' && !hasAccountLocator) {
      toast({
        title: '请输入查询条件',
        description: '请填写平台和用户账号。',
        variant: 'destructive',
      })
      return
    }

    setQuerying(true)
    try {
      if (queryMode === 'fuzzy') {
        const searchPayload = await searchMemoryProfiles({
          personKeyword: cleanKeyword,
          limit: 80,
        })
        const nextItems = searchPayload.items ?? []
        setProfiles(nextItems)
        setProfileListMode('search')
        setQueryResult(null)
        setProfileEvidence(null)
        setShowAutoProfile(false)
        setSelectedPersonId(nextItems[0]?.person_id ?? '')
        toast({
          title: '人物画像检索完成',
          description: `命中 ${nextItems.length} 个画像。`,
        })
        return
      }

      const payload = await queryMemoryProfile({
        personId: '',
        personKeyword: '',
        platform: cleanPlatform,
        userId: cleanUserId,
        limit: PROFILE_QUERY_EVIDENCE_LIMIT,
        forceRefresh: false,
      })
      if (payload.success === false) {
        throw new Error(String(payload.error ?? '人物画像查询失败'))
      }
      setQueryResult(payload)
      const nextPersonId = String(payload.person_id ?? payload.profile?.person_id ?? '')
      const searchPayload = await searchMemoryProfiles({
        personId: nextPersonId,
        personKeyword: '',
        platform: cleanPlatform,
        userId: cleanUserId,
        limit: 80,
      })
      const nextItems = searchPayload.items ?? []
      setProfiles(nextItems)
      setProfileListMode('search')
      if (nextPersonId) {
        setSelectedPersonId(nextPersonId)
        await loadProfileEvidence(nextPersonId)
      } else if (nextItems.length > 0) {
        setSelectedPersonId(nextItems[0].person_id)
        await loadProfileEvidence(nextItems[0].person_id)
      }
      toast({
        title: '人物画像查询完成',
        description: '已获取画像结果。',
      })
    } catch (error) {
      toast({
        title: '人物画像查询失败',
        description: error instanceof Error ? error.message : String(error),
        variant: 'destructive',
      })
    } finally {
      setQuerying(false)
    }
  }, [loadProfileEvidence, queryKeyword, queryMode, queryPlatform, queryUserId, toast])

  useEffect(() => {
    const cleanPersonId = initialPersonId.trim()
    // 定位只在外部跳转时发生一次：序号未变（含证据数量等依赖变化导致的 effect 重跑）直接跳过
    if (!active || !cleanPersonId || locateToken === appliedLocateTokenRef.current) {
      return
    }
    appliedLocateTokenRef.current = locateToken
    setSelectedPersonId(cleanPersonId)
    setQueryResult(null)
    setProfileEvidence(null)
    setShowAutoProfile(false)
    // 外部跳转（记录详情、审计时间线）的意图就是看这个人的画像，直接展开详情弹窗
    setDetailOpen(true)

    let cancelled = false
    const loadInitialProfile = async () => {
      setQuerying(true)
      try {
        const [queryPayload, searchPayload] = await Promise.all([
          queryMemoryProfile({
            personId: cleanPersonId,
            personKeyword: '',
            platform: '',
            userId: '',
            limit: PROFILE_QUERY_EVIDENCE_LIMIT,
            forceRefresh: false,
          }),
          searchMemoryProfiles({
            personId: cleanPersonId,
            limit: 80,
          }),
        ])
        if (cancelled) {
          return
        }
        setQueryResult(queryPayload)
        setProfiles(searchPayload.items ?? [])
        setProfileListMode('search')
        await loadProfileEvidence(cleanPersonId)
      } catch (error) {
        if (!cancelled) {
          toast({
            title: '定位人物画像失败',
            description: error instanceof Error ? error.message : String(error),
            variant: 'destructive',
          })
        }
      } finally {
        if (!cancelled) {
          setQuerying(false)
        }
      }
    }
    void loadInitialProfile()
    return () => {
      cancelled = true
    }
  }, [active, initialPersonId, loadProfileEvidence, locateToken, toast])

  const selectProfile = useCallback(
    (personId: string) => {
      // 重复点击同一人物（例如仅为了打开详情弹窗）不清空证据，
      // 否则弹窗会先闪回画像正文，等重新拉取证据后才切回真实内容
      if (personId !== selectedPersonId) {
        setQueryResult(null)
        setProfileEvidence(null)
        setShowAutoProfile(false)
      }
      setSelectedPersonId(personId)
      // 选中候选项即打开详情弹窗：详情按需查看，不再常驻占位
      setDetailOpen(true)
    },
    [selectedPersonId]
  )

  const saveOverride = useCallback(async () => {
    const personId = selectedPersonId
    if (!personId) {
      toast({
        title: '缺少人物 ID',
        description: '请选择或输入一个 person_id 后再保存画像覆写。',
        variant: 'destructive',
      })
      return
    }
    setSaving(true)
    try {
      await setMemoryProfileOverride({
        person_id: personId,
        override_text: overrideText,
        updated_by: 'knowledge_base',
        source: 'webui',
      })
      toast({ title: '人物画像覆写已保存' })
      await loadProfiles()
      await loadProfileEvidence(personId)
    } catch (error) {
      toast({
        title: '保存人物画像覆写失败',
        description: error instanceof Error ? error.message : String(error),
        variant: 'destructive',
      })
    } finally {
      setSaving(false)
    }
  }, [loadProfileEvidence, loadProfiles, overrideText, selectedPersonId, toast])

  const deleteOverride = useCallback(async () => {
    const personId = selectedPersonId
    if (!personId) {
      return
    }
    if (!window.confirm(`确认删除 ${personId} 的人物画像覆写？`)) {
      return
    }
    setSaving(true)
    try {
      await deleteMemoryProfileOverride(personId)
      setOverrideText('')
      toast({ title: '人物画像覆写已删除' })
      await loadProfiles()
      await loadProfileEvidence(personId)
    } catch (error) {
      toast({
        title: '删除人物画像覆写失败',
        description: error instanceof Error ? error.message : String(error),
        variant: 'destructive',
      })
    } finally {
      setSaving(false)
    }
  }, [loadProfileEvidence, loadProfiles, selectedPersonId, toast])

  const saveAliases = useCallback(async () => {
    const personId = activePersonId.trim()
    if (!personId) {
      toast({
        title: '缺少人物 ID',
        description: '请选择或输入一个 person_id 后再保存别名。',
        variant: 'destructive',
      })
      return
    }
    const aliases = parseAliasText(aliasText)
    if (aliases.length === 0) {
      toast({
        title: '别名不能为空',
        description: '至少保留一个用于画像检索的人物名称。',
        variant: 'destructive',
      })
      return
    }

    setAliasSaving(true)
    try {
      const payload = await setMemoryProfileAliases({
        person_id: personId,
        aliases,
        updated_by: 'knowledge_base',
        source: 'webui',
      })
      if (!payload.success) {
        throw new Error(String(payload.error ?? '人物别名保存失败'))
      }
      setProfileAliases(payload)
      setAliasText((payload.effective_aliases ?? aliases).join('\n'))
      toast({
        title: '人物别名已保存',
        description: payload.refresh_queued ? '人物画像已进入刷新队列。' : undefined,
      })
      await loadProfileEvidence(personId, { forceRefresh: true })
      await loadProfiles()
    } catch (error) {
      toast({
        title: '保存人物别名失败',
        description: error instanceof Error ? error.message : String(error),
        variant: 'destructive',
      })
    } finally {
      setAliasSaving(false)
    }
  }, [activePersonId, aliasText, loadProfileEvidence, loadProfiles, toast])

  const addSuggestedAlias = useCallback(
    (alias: string) => {
      const aliases = parseAliasText(aliasText)
      const key = alias.trim().toLocaleLowerCase()
      if (!key || aliases.some((item) => item.toLocaleLowerCase() === key)) {
        return
      }
      setAliasText([...aliases, alias.trim()].join('\n'))
    },
    [aliasText]
  )

  const restoreDerivedAliases = useCallback(async () => {
    const personId = activePersonId.trim()
    if (!personId || !profileAliases?.has_override) {
      return
    }
    if (!window.confirm(`确认恢复 ${personId} 的可信自动别名？`)) {
      return
    }
    setAliasSaving(true)
    try {
      const payload = await deleteMemoryProfileAliases(personId)
      if (!payload.success) {
        throw new Error(String(payload.error ?? '恢复自动别名失败'))
      }
      setProfileAliases(payload)
      setAliasText((payload.effective_aliases ?? []).join('\n'))
      toast({
        title: '已恢复可信自动别名',
        description: payload.refresh_queued ? '人物画像已进入刷新队列。' : undefined,
      })
      await loadProfileEvidence(personId, { forceRefresh: true })
      await loadProfiles()
    } catch (error) {
      toast({
        title: '恢复可信自动别名失败',
        description: error instanceof Error ? error.message : String(error),
        variant: 'destructive',
      })
    } finally {
      setAliasSaving(false)
    }
  }, [activePersonId, loadProfileEvidence, loadProfiles, profileAliases?.has_override, toast])

  const correctEvidence = useCallback(
    async (item: MemoryProfileEvidenceItemPayload) => {
      const personId = activePersonId.trim()
      const evidenceType = String(item.evidence_type ?? '').trim()
      const hash = String(item.hash ?? '').trim()
      if (!personId || !evidenceType || !hash) {
        return
      }
      if (!window.confirm('确认停用/删除这条支撑证据并刷新画像？')) {
        return
      }
      const evidenceKey = String(item.evidence_key ?? hash)
      setCorrectingEvidenceKey(evidenceKey)
      try {
        const payload = await correctMemoryProfileEvidence({
          person_id: personId,
          evidence_type: evidenceType,
          hash,
          requested_by: 'knowledge_base',
          reason: 'profile_evidence_correction',
          refresh: true,
          limit: parsePositiveInt(evidenceLimit, PROFILE_QUERY_EVIDENCE_LIMIT),
        })
        if (!payload.success) {
          throw new Error(String(payload.error ?? '画像证据纠错失败'))
        }
        if (payload.refreshed_evidence) {
          setProfileEvidence(payload.refreshed_evidence)
        } else {
          await loadProfileEvidence(personId, { forceRefresh: true })
        }
        await loadProfiles()
        toast({
          title: '画像证据已纠错',
          description: payload.operation_id
            ? `删除记录 ${payload.operation_id}`
            : '已刷新人物画像。',
        })
      } catch (error) {
        toast({
          title: '画像证据纠错失败',
          description: error instanceof Error ? error.message : String(error),
          variant: 'destructive',
        })
      } finally {
        setCorrectingEvidenceKey('')
      }
    },
    [activePersonId, evidenceLimit, loadProfileEvidence, loadProfiles, toast]
  )

  return {
    // 查询参数与模式
    queryMode,
    setQueryMode,
    queryPlatform,
    setQueryPlatform,
    queryUserId,
    setQueryUserId,
    queryKeyword,
    setQueryKeyword,
    evidenceLimit,
    setEvidenceLimit,
    // 查询执行与候选列表
    submitQuery,
    loadProfiles,
    loading,
    querying,
    profileListMode,
    profiles,
    selectProfile,
    // 当前定位与详情
    selectedPersonId,
    selectedProfile,
    selectedDisplayName,
    activePersonId,
    queryResult,
    profileEvidence,
    currentProfileEvidence,
    displayedProfileText,
    showAutoProfile,
    setShowAutoProfile,
    showRawProfilePayload,
    setShowRawProfilePayload,
    detailOpen,
    setDetailOpen,
    // 支撑证据
    evidenceLoading,
    loadProfileEvidence,
    correctEvidence,
    correctingEvidenceKey,
    // 别名维护
    profileAliases,
    aliasText,
    setAliasText,
    aliasLoading,
    aliasSaving,
    saveAliases,
    addSuggestedAlias,
    restoreDerivedAliases,
    // 画像覆写
    overrideText,
    setOverrideText,
    saving,
    saveOverride,
    deleteOverride,
  }
}
