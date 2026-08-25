import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Check,
  ChevronDown,
  Loader2,
  Plus,
  RefreshCw,
  RotateCcw,
  Save,
  Search,
  Trash2,
} from 'lucide-react'

import { Alert, AlertDescription } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Checkbox } from '@/components/ui/checkbox'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { ScrollArea } from '@/components/ui/scroll-area'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { Textarea } from '@/components/ui/textarea'
import { ThinkingIllustration } from '@/components/ui/thinking-illustration'
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
import { getPersonList } from '@/lib/person-api'
import { cn } from '@/lib/utils'
import type { PersonInfo } from '@/types/person'

function formatMemoryTime(timestamp?: number | null): string {
  if (!timestamp) {
    return '-'
  }
  const normalized = timestamp > 1_000_000_000_000 ? timestamp : timestamp * 1000
  const value = new Date(normalized)
  if (Number.isNaN(value.getTime())) {
    return '-'
  }
  return value.toLocaleString('zh-CN', {
    hour12: false,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function parseAliasText(value: string): string[] {
  const aliases: string[] = []
  const seen = new Set<string>()
  for (const item of value.split(/[\n,，]/)) {
    const alias = item.trim()
    const key = alias.toLocaleLowerCase()
    if (!alias || seen.has(key)) {
      continue
    }
    seen.add(key)
    aliases.push(alias)
  }
  return aliases
}

function parsePositiveInt(value: string, fallback: number): number {
  const parsed = Number(value)
  if (!Number.isInteger(parsed) || parsed <= 0) {
    return fallback
  }
  return parsed
}

function stringifyOverride(value: MemoryProfileItemPayload['manual_override']): string {
  if (!value) {
    return ''
  }
  if (typeof value === 'string') {
    return value
  }
  const text = value.override_text ?? value.text
  if (typeof text === 'string') {
    return text
  }
  return JSON.stringify(value, null, 2)
}

function resolveProfileText(
  queryResult: MemoryProfileQueryPayload | null,
  selectedProfile: MemoryProfileItemPayload | null
): string {
  if (typeof queryResult?.profile_text === 'string') {
    return queryResult.profile_text
  }
  const queryProfile = queryResult?.profile
  if (
    queryProfile &&
    typeof queryProfile === 'object' &&
    typeof queryProfile.profile_text === 'string'
  ) {
    return queryProfile.profile_text
  }
  return selectedProfile?.profile_text ?? ''
}

function evidenceTypeLabel(type?: string): string {
  switch (type) {
    case 'relation':
      return '关系'
    case 'person_fact':
      return '人物事实'
    case 'chat_summary':
      return '聊天摘要'
    case 'paragraph':
      return '段落'
    default:
      return type || '未知'
  }
}

function formatEvidenceScore(item: MemoryProfileEvidenceItemPayload): string {
  const confidence = Number(item.confidence)
  if (Number.isFinite(confidence)) {
    return `置信度 ${confidence.toFixed(2)}`
  }
  const score = Number(item.score)
  if (Number.isFinite(score)) {
    return `分数 ${score.toFixed(2)}`
  }
  return '-'
}

export interface MemoryProfileManagerProps {
  initialPersonId?: string
}

type ProfileQueryMode = 'exact' | 'fuzzy'
type AccountMatchStatus = 'idle' | 'loading' | 'matched' | 'unmatched' | 'error'

export function MemoryProfileManager({ initialPersonId = '' }: MemoryProfileManagerProps) {
  const { toast } = useToast()
  const [profiles, setProfiles] = useState<MemoryProfileItemPayload[]>([])
  const [profileListMode, setProfileListMode] = useState<'library' | 'search'>('library')
  const [selectedPersonId, setSelectedPersonId] = useState('')
  const [queryPersonId, setQueryPersonId] = useState('')
  const [queryKeyword, setQueryKeyword] = useState('')
  const [queryPlatform, setQueryPlatform] = useState('')
  const [queryUserId, setQueryUserId] = useState('')
  const [queryLimit, setQueryLimit] = useState('12')
  const [queryMode, setQueryMode] = useState<ProfileQueryMode>('exact')
  const [accountMatchStatus, setAccountMatchStatus] = useState<AccountMatchStatus>('idle')
  const [matchedPerson, setMatchedPerson] = useState<PersonInfo | null>(null)
  const [forceRefresh, setForceRefresh] = useState(false)
  const [showAdvancedPersonId, setShowAdvancedPersonId] = useState(false)
  const [showRawProfilePayload, setShowRawProfilePayload] = useState(false)
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
  const initialPersonAppliedRef = useRef('')
  const aliasRequestIdRef = useRef(0)
  const accountMatchRequestIdRef = useRef(0)

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
      if (!selectedPersonId && nextItems.length > 0) {
        setSelectedPersonId(nextItems[0].person_id)
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
  }, [selectedPersonId, toast])

  useEffect(() => {
    if (initialLoadedRef.current) {
      return
    }
    initialLoadedRef.current = true
    void loadProfiles()
  }, [loadProfiles])

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
          limit: parsePositiveInt(queryLimit, 12),
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
    [queryLimit, toast]
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

  useEffect(() => {
    const cleanPlatform = queryPlatform.trim()
    const cleanUserId = queryUserId.trim()
    const requestId = ++accountMatchRequestIdRef.current

    if (queryMode !== 'exact' || !cleanPlatform || !cleanUserId) {
      setMatchedPerson(null)
      setAccountMatchStatus('idle')
      return
    }

    setMatchedPerson(null)
    setAccountMatchStatus('loading')
    const timer = window.setTimeout(() => {
      void getPersonList({
        page: 1,
        page_size: 1,
        platform: cleanPlatform,
        user_id: cleanUserId,
      })
        .then((payload) => {
          if (requestId !== accountMatchRequestIdRef.current) {
            return
          }
          const person = payload.data[0] ?? null
          setMatchedPerson(person)
          setAccountMatchStatus(person ? 'matched' : 'unmatched')
        })
        .catch(() => {
          if (requestId !== accountMatchRequestIdRef.current) {
            return
          }
          setMatchedPerson(null)
          setAccountMatchStatus('error')
        })
    }, 300)

    return () => window.clearTimeout(timer)
  }, [queryMode, queryPlatform, queryUserId])

  const submitQuery = useCallback(async () => {
    const directPersonId = queryMode === 'exact' && showAdvancedPersonId ? queryPersonId.trim() : ''
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
    if (queryMode === 'exact' && !directPersonId && !hasAccountLocator) {
      toast({
        title: '请输入查询条件',
        description: '请填写平台和用户账号，或在高级查询中输入 person_id。',
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
        personId: directPersonId,
        personKeyword: '',
        platform: cleanPlatform,
        userId: cleanUserId,
        limit: parsePositiveInt(queryLimit, 12),
        forceRefresh,
      })
      if (payload.success === false) {
        throw new Error(String(payload.error ?? '人物画像查询失败'))
      }
      setQueryResult(payload)
      const nextPersonId = String(
        payload.person_id ?? payload.profile?.person_id ?? directPersonId ?? ''
      )
      const searchPayload = await searchMemoryProfiles({
        personId: nextPersonId || directPersonId,
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
        setQueryPersonId(nextPersonId)
        await loadProfileEvidence(nextPersonId, { forceRefresh })
      } else if (nextItems.length > 0) {
        setSelectedPersonId(nextItems[0].person_id)
        await loadProfileEvidence(nextItems[0].person_id)
      }
      toast({
        title: '人物画像查询完成',
        description: forceRefresh ? '已请求强制刷新画像。' : '已获取画像结果。',
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
  }, [
    forceRefresh,
    loadProfileEvidence,
    queryKeyword,
    queryLimit,
    queryMode,
    queryPersonId,
    queryPlatform,
    queryUserId,
    showAdvancedPersonId,
    toast,
  ])

  useEffect(() => {
    const cleanPersonId = initialPersonId.trim()
    if (!cleanPersonId || cleanPersonId === initialPersonAppliedRef.current) {
      return
    }
    initialPersonAppliedRef.current = cleanPersonId
    setShowAdvancedPersonId(true)
    setQueryPersonId(cleanPersonId)
    setSelectedPersonId(cleanPersonId)
    setQueryResult(null)
    setProfileEvidence(null)
    setShowAutoProfile(false)

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
            limit: parsePositiveInt(queryLimit, 12),
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
  }, [initialPersonId, loadProfileEvidence, queryLimit, toast])

  const selectProfile = useCallback((personId: string) => {
    setSelectedPersonId(personId)
    setQueryResult(null)
    setProfileEvidence(null)
    setShowAutoProfile(false)
  }, [])

  const saveOverride = useCallback(async () => {
    const personId = selectedPersonId || queryPersonId.trim()
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
  }, [loadProfileEvidence, loadProfiles, overrideText, queryPersonId, selectedPersonId, toast])

  const deleteOverride = useCallback(async () => {
    const personId = selectedPersonId || queryPersonId.trim()
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
  }, [loadProfileEvidence, loadProfiles, queryPersonId, selectedPersonId, toast])

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
          limit: parsePositiveInt(queryLimit, 12),
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
    [activePersonId, loadProfileEvidence, loadProfiles, queryLimit, toast]
  )

  return (
    <div className="grid items-start gap-4 xl:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Search className="h-4 w-4" />
            人物画像查询
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <Label>查询方式</Label>
            <div role="group" aria-label="查询方式" className="grid grid-cols-2 gap-2">
              <Button
                type="button"
                variant={queryMode === 'exact' ? 'default' : 'outline'}
                aria-pressed={queryMode === 'exact'}
                className="w-full"
                onClick={() => setQueryMode('exact')}
              >
                精确查询
              </Button>
              <Button
                type="button"
                variant={queryMode === 'fuzzy' ? 'default' : 'outline'}
                aria-pressed={queryMode === 'fuzzy'}
                className="w-full"
                onClick={() => setQueryMode('fuzzy')}
              >
                模糊查询
              </Button>
            </div>
          </div>

          {queryMode === 'exact' ? (
            <>
              <div className="grid gap-3 md:grid-cols-3">
                <div className="space-y-2">
                  <Label htmlFor="profile-platform">平台</Label>
                  <Input
                    id="profile-platform"
                    value={queryPlatform}
                    onChange={(event) => setQueryPlatform(event.target.value)}
                    placeholder="例如 qq、telegram、webui"
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="profile-user-id">用户账号</Label>
                  <Input
                    id="profile-user-id"
                    value={queryUserId}
                    onChange={(event) => setQueryUserId(event.target.value)}
                    placeholder="输入平台侧 user_id"
                  />
                </div>
                <div className="space-y-2">
                  <Label>匹配用户</Label>
                  <div
                    aria-live="polite"
                    className="bg-muted/10 flex min-h-10 items-center border px-3 py-2"
                  >
                    {accountMatchStatus === 'loading' ? (
                      <span className="text-muted-foreground flex items-center gap-2 text-sm">
                        <Loader2 className="h-4 w-4 animate-spin" />
                        匹配中
                      </span>
                    ) : matchedPerson ? (
                      <div className="min-w-0">
                        <div className="truncate text-sm font-medium">
                          {matchedPerson.person_name || matchedPerson.nickname || matchedPerson.user_id}
                        </div>
                        <div className="text-muted-foreground truncate font-mono text-xs">
                          {matchedPerson.person_id}
                        </div>
                      </div>
                    ) : (
                      <span className="text-muted-foreground text-sm">
                        {accountMatchStatus === 'error' ? '匹配失败' : '无用户'}
                      </span>
                    )}
                  </div>
                </div>
              </div>
              <div className="grid gap-3 md:grid-cols-2">
                <div className="space-y-2">
                  <Label htmlFor="profile-limit">证据数量</Label>
                  <Input
                    id="profile-limit"
                    type="number"
                    value={queryLimit}
                    onChange={(event) => setQueryLimit(event.target.value)}
                  />
                </div>
                <div className="flex items-center gap-2 self-end pb-2">
                  <Checkbox
                    id="profile-force-refresh"
                    checked={forceRefresh}
                    onCheckedChange={(value) => setForceRefresh(Boolean(value))}
                  />
                  <Label htmlFor="profile-force-refresh" className="text-sm font-normal">
                    强制刷新画像
                  </Label>
                </div>
              </div>
            </>
          ) : (
            <div className="grid gap-3 md:grid-cols-2">
              <div className="space-y-2">
                <Label htmlFor="profile-keyword">人物关键词</Label>
                <Input
                  id="profile-keyword"
                  value={queryKeyword}
                  onChange={(event) => setQueryKeyword(event.target.value)}
                  placeholder="输入人物名称或画像关键词"
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="profile-limit">证据数量</Label>
                <Input
                  id="profile-limit"
                  type="number"
                  value={queryLimit}
                  onChange={(event) => setQueryLimit(event.target.value)}
                />
              </div>
            </div>
          )}

          {queryMode === 'exact' ? (
            <Collapsible
              open={showAdvancedPersonId}
              onOpenChange={setShowAdvancedPersonId}
              className="bg-muted/10 rounded-lg border"
            >
              <CollapsibleTrigger asChild>
                <Button variant="ghost" className="flex h-10 w-full justify-between px-3">
                  <span>高级查询</span>
                  <ChevronDown
                    className={cn(
                      'h-4 w-4 transition-transform',
                      showAdvancedPersonId && 'rotate-180'
                    )}
                  />
                </Button>
              </CollapsibleTrigger>
              <CollapsibleContent className="space-y-2 border-t px-3 py-3">
                <Label htmlFor="profile-person-id">person_id</Label>
                <Input
                  id="profile-person-id"
                  value={queryPersonId}
                  onChange={(event) => setQueryPersonId(event.target.value)}
                  placeholder="调试或后台管理时直接输入"
                />
              </CollapsibleContent>
            </Collapsible>
          ) : null}

          {selectedPersonId || queryPersonId ? (
            <div className="bg-muted/20 rounded-lg border px-3 py-2 text-sm">
              <div className="text-muted-foreground">当前定位 person_id</div>
              <div className="mt-1 font-mono text-xs break-all">
                {selectedPersonId || queryPersonId}
              </div>
            </div>
          ) : null}

          <div className="flex flex-wrap gap-2">
            <Button onClick={() => void submitQuery()} disabled={querying}>
              <Search className="mr-2 h-4 w-4" />
              查询人物画像
            </Button>
            <Button variant="outline" onClick={() => void loadProfiles()} disabled={loading}>
              <RefreshCw className={cn('mr-2 h-4 w-4', loading && 'animate-spin')} />
              查看画像库
            </Button>
          </div>

          {profileListMode === 'library' ? (
            <div className="bg-muted/10 rounded-lg border px-3 py-2">
              <div className="text-sm font-medium">画像库</div>
              <div className="text-muted-foreground mt-1 text-xs">
                系统中已生成的最新人物画像快照，按更新时间排序。
              </div>
            </div>
          ) : null}

          <ScrollArea
            aria-label="人物画像列表"
            className="max-h-[clamp(32.5rem,70vh,52rem)]"
            viewportClassName="max-h-[clamp(32.5rem,70vh,52rem)]"
          >
            <Table>
              <TableHeader className="bg-background sticky top-0">
                <TableRow>
                  <TableHead>人物</TableHead>
                  <TableHead>版本</TableHead>
                  <TableHead>更新时间</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {profiles.length > 0 ? (
                  profiles.map((item) => (
                    <TableRow
                      key={item.person_id}
                      className={cn(
                        'cursor-pointer',
                        selectedPersonId === item.person_id && 'bg-muted/60'
                      )}
                      onClick={() => selectProfile(item.person_id)}
                    >
                      <TableCell>
                        <div className="font-medium break-all">
                          {item.person_name || item.person_id}
                        </div>
                        {item.person_name ? (
                          <div className="text-muted-foreground mt-0.5 font-mono text-xs break-all">
                            {item.person_id}
                          </div>
                        ) : null}
                        <div className="mt-1 flex flex-wrap gap-1">
                          {item.has_manual_override ? (
                            <Badge variant="secondary">画像覆写</Badge>
                          ) : null}
                          {item.source_note &&
                          item.source_note !== 'sdk_memory_kernel.memory_profile_admin.query' ? (
                            <Badge variant="outline">{item.source_note}</Badge>
                          ) : null}
                        </div>
                      </TableCell>
                      <TableCell>{Number(item.profile_version ?? 0)}</TableCell>
                      <TableCell>{formatMemoryTime(item.updated_at)}</TableCell>
                    </TableRow>
                  ))
                ) : (
                  <TableRow>
                    <TableCell colSpan={3} className="text-muted-foreground text-center">
                      {loading ? (
                        <ThinkingIllustration size="sm" className="mx-auto" />
                      ) : profileListMode === 'search' ? (
                        '没有匹配的人物画像'
                      ) : (
                        '还没有人物画像快照'
                      )}
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
          </ScrollArea>
        </CardContent>
      </Card>

      <div className="space-y-4">
        <Card>
          <CardHeader>
            <CardTitle>画像详情</CardTitle>
            <CardDescription>展示当前快照、查询结果、支撑证据和原始响应。</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {querying ? (
              <div className="text-muted-foreground flex items-center gap-2 text-sm">
                <Loader2 className="h-4 w-4 animate-spin" />
                正在查询人物画像
              </div>
            ) : null}
            {selectedProfile || queryResult ? (
              <>
                <div className="flex flex-wrap gap-2">
                  <Badge variant="outline">
                    {selectedPersonId || String(queryResult?.person_id ?? '未选择')}
                  </Badge>
                  {selectedProfile?.expires_at ? (
                    <Badge variant="secondary">
                      过期时间 {formatMemoryTime(selectedProfile.expires_at)}
                    </Badge>
                  ) : null}
                  {currentProfileEvidence?.has_manual_override ? (
                    <Badge variant="secondary">当前展示使用画像覆写</Badge>
                  ) : null}
                </div>
                {currentProfileEvidence?.has_manual_override ? (
                  <div className="flex flex-wrap gap-2">
                    <Button
                      type="button"
                      variant={!showAutoProfile ? 'default' : 'outline'}
                      size="sm"
                      onClick={() => setShowAutoProfile(false)}
                    >
                      画像覆写
                    </Button>
                    <Button
                      type="button"
                      variant={showAutoProfile ? 'default' : 'outline'}
                      size="sm"
                      onClick={() => setShowAutoProfile(true)}
                    >
                      自动画像
                    </Button>
                  </div>
                ) : null}
                <Textarea
                  value={displayedProfileText}
                  readOnly
                  className="min-h-[180px]"
                  placeholder="当前没有画像文本"
                />

                <div className="rounded-lg border">
                  <div className="flex items-center justify-between gap-3 border-b px-3 py-2">
                    <div>
                      <div className="text-sm font-medium">支撑证据</div>
                      <div className="text-muted-foreground text-xs">
                        {currentProfileEvidence?.evidence_count ?? 0}{' '}
                        条证据；纠错后会自动刷新自动画像。
                      </div>
                    </div>
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      disabled={!activePersonId || evidenceLoading}
                      onClick={() =>
                        void loadProfileEvidence(activePersonId, { forceRefresh: true })
                      }
                    >
                      <RefreshCw
                        className={cn('mr-2 h-4 w-4', evidenceLoading && 'animate-spin')}
                      />
                      刷新证据
                    </Button>
                  </div>
                  <ScrollArea className="h-[300px]">
                    <Table>
                      <TableHeader className="bg-background sticky top-0">
                        <TableRow>
                          <TableHead>类型</TableHead>
                          <TableHead>内容</TableHead>
                          <TableHead>来源</TableHead>
                          <TableHead>分数</TableHead>
                          <TableHead>操作</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {(currentProfileEvidence?.evidence ?? []).length > 0 ? (
                          (currentProfileEvidence?.evidence ?? []).map((item) => {
                            const evidenceKey = String(item.evidence_key ?? item.hash ?? '')
                            const isCorrecting = correctingEvidenceKey === evidenceKey
                            return (
                              <TableRow key={evidenceKey}>
                                <TableCell>
                                  <Badge variant="outline">
                                    {evidenceTypeLabel(item.evidence_type)}
                                  </Badge>
                                </TableCell>
                                <TableCell className="max-w-[320px]">
                                  <div className="line-clamp-3 text-sm">{item.content || '-'}</div>
                                  {item.hash ? (
                                    <div className="text-muted-foreground mt-1 font-mono text-xs break-all">
                                      {item.hash}
                                    </div>
                                  ) : null}
                                </TableCell>
                                <TableCell className="max-w-[220px]">
                                  <div className="text-muted-foreground line-clamp-2 text-xs">
                                    {item.source || item.source_type || '-'}
                                  </div>
                                </TableCell>
                                <TableCell className="text-xs whitespace-nowrap">
                                  {formatEvidenceScore(item)}
                                </TableCell>
                                <TableCell>
                                  {item.deletable ? (
                                    <Button
                                      type="button"
                                      variant="outline"
                                      size="sm"
                                      disabled={Boolean(correctingEvidenceKey)}
                                      onClick={() => void correctEvidence(item)}
                                    >
                                      {isCorrecting ? (
                                        <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                      ) : (
                                        <Trash2 className="mr-2 h-4 w-4" />
                                      )}
                                      纠错并刷新
                                    </Button>
                                  ) : (
                                    <span className="text-muted-foreground text-xs">
                                      {item.not_deletable_reason || '不可操作'}
                                    </span>
                                  )}
                                </TableCell>
                              </TableRow>
                            )
                          })
                        ) : (
                          <TableRow>
                            <TableCell colSpan={5} className="text-muted-foreground text-center">
                              {evidenceLoading ? '正在加载画像证据' : '当前没有可展示的支撑证据'}
                            </TableCell>
                          </TableRow>
                        )}
                      </TableBody>
                    </Table>
                  </ScrollArea>
                </div>

                <Collapsible
                  open={showRawProfilePayload}
                  onOpenChange={setShowRawProfilePayload}
                  className="bg-muted/10 rounded-lg border"
                >
                  <CollapsibleTrigger asChild>
                    <Button variant="ghost" className="flex h-10 w-full justify-between px-3">
                      <span>原始响应 JSON</span>
                      <ChevronDown
                        className={cn(
                          'h-4 w-4 transition-transform',
                          showRawProfilePayload && 'rotate-180'
                        )}
                      />
                    </Button>
                  </CollapsibleTrigger>
                  <CollapsibleContent className="border-t">
                    <pre className="max-h-72 overflow-auto p-3 text-xs break-words whitespace-pre-wrap">
                      {JSON.stringify(
                        currentProfileEvidence ?? queryResult ?? selectedProfile ?? {},
                        null,
                        2
                      )}
                    </pre>
                  </CollapsibleContent>
                </Collapsible>
              </>
            ) : (
              <div className="bg-muted/20 text-muted-foreground rounded-lg border border-dashed p-6 text-center text-sm">
                选择一个人物或执行查询后查看详情。
              </div>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>别名维护</CardTitle>
            <CardDescription>
              可信身份字段会自动生效；共同出现实体必须人工确认后才能加入别名。
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            {!activePersonId.trim() ? (
              <Alert>
                <AlertDescription>请选择或输入 person_id 后再维护别名。</AlertDescription>
              </Alert>
            ) : null}
            <div className="text-muted-foreground flex flex-wrap items-center gap-2 text-xs">
              <Badge variant={profileAliases?.has_override ? 'secondary' : 'outline'}>
                {profileAliases?.has_override ? '人工别名生效中' : '可信自动别名生效中'}
              </Badge>
              {aliasLoading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
            </div>
            {(profileAliases?.derived_aliases ?? []).length > 0 ? (
              <div className="space-y-2">
                <Label>可信自动别名</Label>
                <div className="flex flex-wrap gap-1.5">
                  {(profileAliases?.derived_aliases ?? []).map((alias) => (
                    <Badge key={alias} variant="outline">
                      {alias}
                    </Badge>
                  ))}
                </div>
              </div>
            ) : null}
            {(profileAliases?.suggested_aliases ?? []).length > 0 ? (
              <div className="space-y-2">
                <div>
                  <Label>待确认候选</Label>
                  <p className="text-muted-foreground mt-1 text-xs">
                    来自共同出现实体，不会自动参与检索。
                  </p>
                </div>
                <div className="flex flex-wrap gap-1.5">
                  {(profileAliases?.suggested_aliases ?? []).map((alias) => {
                    const included = parseAliasText(aliasText).some(
                      (item) => item.toLocaleLowerCase() === alias.toLocaleLowerCase()
                    )
                    return (
                      <Button
                        key={alias}
                        type="button"
                        variant="outline"
                        size="sm"
                        onClick={() => addSuggestedAlias(alias)}
                        disabled={included || aliasLoading || aliasSaving}
                      >
                        {included ? (
                          <Check className="mr-1.5 h-3.5 w-3.5" />
                        ) : (
                          <Plus className="mr-1.5 h-3.5 w-3.5" />
                        )}
                        {included ? '已加入' : '加入'} {alias}
                      </Button>
                    )
                  })}
                </div>
              </div>
            ) : null}
            <div className="space-y-2">
              <Label htmlFor="profile-aliases">当前有效别名</Label>
              <Textarea
                id="profile-aliases"
                value={aliasText}
                onChange={(event) => setAliasText(event.target.value)}
                className="min-h-[120px]"
                placeholder="每行填写一个别名"
                disabled={!activePersonId.trim() || aliasLoading}
              />
            </div>
            <div className="flex flex-wrap gap-2">
              <Button
                onClick={() => void saveAliases()}
                disabled={!activePersonId.trim() || aliasLoading || aliasSaving}
              >
                {aliasSaving ? (
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                ) : (
                  <Save className="mr-2 h-4 w-4" />
                )}
                保存别名
              </Button>
              <Button
                variant="outline"
                onClick={() => void restoreDerivedAliases()}
                disabled={!profileAliases?.has_override || aliasSaving}
              >
                <RotateCcw className="mr-2 h-4 w-4" />
                恢复可信自动别名
              </Button>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>画像覆写</CardTitle>
            <CardDescription>
              用人工画像固定展示结果；留空保存表示清空文本但保留画像覆写记录。
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            {!selectedPersonId && !queryPersonId.trim() ? (
              <Alert>
                <AlertDescription>请选择或输入 person_id 后再编辑画像覆写。</AlertDescription>
              </Alert>
            ) : null}
            {selectedDisplayName ? (
              <div className="text-muted-foreground text-sm">
                当前编辑对象：{selectedDisplayName}
              </div>
            ) : null}
            <Textarea
              value={overrideText}
              onChange={(event) => setOverrideText(event.target.value)}
              className="min-h-[180px]"
              placeholder="输入希望固定使用的人物画像文本"
            />
            <div className="flex flex-wrap gap-2">
              <Button onClick={() => void saveOverride()} disabled={saving}>
                <Save className="mr-2 h-4 w-4" />
                保存画像覆写
              </Button>
              <Button
                variant="outline"
                onClick={() => void deleteOverride()}
                disabled={saving || (!selectedPersonId && !queryPersonId.trim())}
              >
                <Trash2 className="mr-2 h-4 w-4" />
                删除画像覆写
              </Button>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
