import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ChevronDown, Loader2, RefreshCw, Save, Search, Trash2 } from 'lucide-react'

import { Alert, AlertDescription } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Checkbox } from '@/components/ui/checkbox'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Textarea } from '@/components/ui/textarea'
import { ThinkingIllustration } from '@/components/ui/thinking-illustration'
import { useToast } from '@/hooks/use-toast'
import {
  correctMemoryProfileEvidence,
  deleteMemoryProfileOverride,
  getMemoryProfileEvidence,
  getMemoryProfiles,
  queryMemoryProfile,
  searchMemoryProfiles,
  setMemoryProfileOverride,
  type MemoryProfileEvidenceItemPayload,
  type MemoryProfileEvidencePayload,
  type MemoryProfileItemPayload,
  type MemoryProfileQueryPayload,
} from '@/lib/memory-api'
import { cn } from '@/lib/utils'

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

function resolveProfileText(queryResult: MemoryProfileQueryPayload | null, selectedProfile: MemoryProfileItemPayload | null): string {
  if (typeof queryResult?.profile_text === 'string') {
    return queryResult.profile_text
  }
  const queryProfile = queryResult?.profile
  if (queryProfile && typeof queryProfile === 'object' && typeof queryProfile.profile_text === 'string') {
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
  const [forceRefresh, setForceRefresh] = useState(false)
  const [showAdvancedPersonId, setShowAdvancedPersonId] = useState(false)
  const [showRawProfilePayload, setShowRawProfilePayload] = useState(false)
  const [overrideText, setOverrideText] = useState('')
  const [queryResult, setQueryResult] = useState<MemoryProfileQueryPayload | null>(null)
  const [profileEvidence, setProfileEvidence] = useState<MemoryProfileEvidencePayload | null>(null)
  const [showAutoProfile, setShowAutoProfile] = useState(false)
  const [loading, setLoading] = useState(false)
  const [querying, setQuerying] = useState(false)
  const [saving, setSaving] = useState(false)
  const [evidenceLoading, setEvidenceLoading] = useState(false)
  const [correctingEvidenceKey, setCorrectingEvidenceKey] = useState('')
  const initialLoadedRef = useRef(false)
  const initialPersonAppliedRef = useRef('')

  const selectedProfile = useMemo(
    () => profiles.find((item) => item.person_id === selectedPersonId) ?? null,
    [profiles, selectedPersonId],
  )
  const profileText = resolveProfileText(queryResult, selectedProfile)
  const selectedDisplayName = selectedProfile?.person_name || selectedPersonId || String(queryResult?.person_id ?? '未选择')
  const activePersonId = selectedPersonId || queryPersonId.trim() || String(queryResult?.person_id ?? profileEvidence?.person_id ?? '')
  const profileEvidencePersonId = String(profileEvidence?.person_id ?? '').trim()
  const currentProfileEvidence = profileEvidencePersonId && profileEvidencePersonId === activePersonId.trim()
    ? profileEvidence
    : null
  const displayedProfileText = showAutoProfile && typeof currentProfileEvidence?.auto_profile_text === 'string'
    ? currentProfileEvidence.auto_profile_text
    : (typeof currentProfileEvidence?.profile_text === 'string' ? currentProfileEvidence.profile_text : profileText)

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

  const loadProfileEvidence = useCallback(async (personId: string, options?: { forceRefresh?: boolean }) => {
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
  }, [queryLimit, toast])

  useEffect(() => {
    if (!selectedPersonId || profileEvidencePersonId === selectedPersonId || queryResult) {
      return
    }
    void loadProfileEvidence(selectedPersonId)
  }, [loadProfileEvidence, profileEvidencePersonId, queryResult, selectedPersonId])

  const submitQuery = useCallback(async () => {
    const directPersonId = showAdvancedPersonId ? queryPersonId.trim() : ''
    const cleanKeyword = queryKeyword.trim()
    const cleanPlatform = queryPlatform.trim()
    const cleanUserId = queryUserId.trim()
    const hasAccountLocator = Boolean(cleanPlatform && cleanUserId)
    if (!directPersonId && !cleanKeyword && !hasAccountLocator) {
      toast({
        title: '请输入查询条件',
        description: '用户账号、关键词、或高级 person_id 至少填写一种。',
        variant: 'destructive',
      })
      return
    }
    setQuerying(true)
    try {
      if (!directPersonId && !hasAccountLocator) {
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
        personKeyword: cleanKeyword,
        platform: cleanPlatform,
        userId: cleanUserId,
        limit: parsePositiveInt(queryLimit, 12),
        forceRefresh,
      })
      if (payload.success === false) {
        throw new Error(String(payload.error ?? '人物画像查询失败'))
      }
      setQueryResult(payload)
      const nextPersonId = String(payload.person_id ?? payload.profile?.person_id ?? directPersonId ?? '')
      const searchPayload = await searchMemoryProfiles({
        personId: nextPersonId || directPersonId,
        personKeyword: cleanKeyword,
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
  }, [forceRefresh, loadProfileEvidence, queryKeyword, queryLimit, queryPersonId, queryPlatform, queryUserId, showAdvancedPersonId, toast])

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

  const correctEvidence = useCallback(async (item: MemoryProfileEvidenceItemPayload) => {
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
        description: payload.operation_id ? `删除记录 ${payload.operation_id}` : '已刷新人物画像。',
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
  }, [activePersonId, loadProfileEvidence, loadProfiles, queryLimit, toast])

  return (
    <div className="grid items-start gap-4 xl:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Search className="h-4 w-4" />
            人物画像查询
          </CardTitle>
          <CardDescription>按平台账号定位人物画像，可用关键词辅助检索；person_id 查询放在高级入口。</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-3 md:grid-cols-2">
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
              <Label htmlFor="profile-keyword">人物关键词</Label>
              <Input id="profile-keyword" value={queryKeyword} onChange={(event) => setQueryKeyword(event.target.value)} placeholder="可选" />
            </div>
            <div className="space-y-2">
              <Label htmlFor="profile-limit">证据数量</Label>
              <Input id="profile-limit" type="number" value={queryLimit} onChange={(event) => setQueryLimit(event.target.value)} />
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

          <Collapsible open={showAdvancedPersonId} onOpenChange={setShowAdvancedPersonId} className="rounded-lg border bg-muted/10">
            <CollapsibleTrigger asChild>
              <Button variant="ghost" className="flex h-10 w-full justify-between px-3">
                <span>高级查询</span>
                <ChevronDown className={cn('h-4 w-4 transition-transform', showAdvancedPersonId && 'rotate-180')} />
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

          {selectedPersonId || queryPersonId ? (
            <div className="rounded-lg border bg-muted/20 px-3 py-2 text-sm">
              <div className="text-muted-foreground">当前定位 person_id</div>
              <div className="mt-1 break-all font-mono text-xs">{selectedPersonId || queryPersonId}</div>
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

          <div className="rounded-lg border bg-muted/10 px-3 py-2">
            <div className="text-sm font-medium">{profileListMode === 'search' ? '检索结果' : '画像库'}</div>
            <div className="mt-1 text-xs text-muted-foreground">
              {profileListMode === 'search'
                ? '根据当前平台账号、关键词或 person_id 筛选出的画像候选。'
                : '系统中已生成的最新人物画像快照，按更新时间排序。'}
            </div>
          </div>

          <ScrollArea
            aria-label="人物画像列表"
            className="max-h-[clamp(32.5rem,70vh,52rem)]"
            viewportClassName="max-h-[clamp(32.5rem,70vh,52rem)]"
          >
            <Table>
              <TableHeader className="sticky top-0 bg-background">
                <TableRow>
                  <TableHead>人物</TableHead>
                  <TableHead>版本</TableHead>
                  <TableHead>更新时间</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {profiles.length > 0 ? profiles.map((item) => (
                  <TableRow
                    key={item.person_id}
                    className={cn('cursor-pointer', selectedPersonId === item.person_id && 'bg-muted/60')}
                    onClick={() => selectProfile(item.person_id)}
                  >
                    <TableCell>
                      <div className="font-medium break-all">{item.person_name || item.person_id}</div>
                      {item.person_name ? <div className="mt-0.5 font-mono text-xs text-muted-foreground break-all">{item.person_id}</div> : null}
                      <div className="mt-1 flex flex-wrap gap-1">
                        {item.has_manual_override ? <Badge variant="secondary">画像覆写</Badge> : null}
                        {item.source_note ? <Badge variant="outline">{item.source_note}</Badge> : null}
                      </div>
                    </TableCell>
                    <TableCell>{Number(item.profile_version ?? 0)}</TableCell>
                    <TableCell>{formatMemoryTime(item.updated_at)}</TableCell>
                  </TableRow>
                )) : (
                  <TableRow>
                    <TableCell colSpan={3} className="text-center text-muted-foreground">
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
              <div className="flex items-center gap-2 text-sm text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" />
                正在查询人物画像
              </div>
            ) : null}
            {selectedProfile || queryResult ? (
              <>
                <div className="flex flex-wrap gap-2">
                  <Badge variant="outline">{selectedPersonId || String(queryResult?.person_id ?? '未选择')}</Badge>
                  {selectedProfile?.expires_at ? <Badge variant="secondary">过期时间 {formatMemoryTime(selectedProfile.expires_at)}</Badge> : null}
                  {currentProfileEvidence?.has_manual_override ? <Badge variant="secondary">当前展示使用画像覆写</Badge> : null}
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
                <Textarea value={displayedProfileText} readOnly className="min-h-[180px]" placeholder="当前没有画像文本" />

                <div className="rounded-lg border">
                  <div className="flex items-center justify-between gap-3 border-b px-3 py-2">
                    <div>
                      <div className="text-sm font-medium">支撑证据</div>
                      <div className="text-xs text-muted-foreground">
                        {currentProfileEvidence?.evidence_count ?? 0} 条证据；纠错后会自动刷新自动画像。
                      </div>
                    </div>
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      disabled={!activePersonId || evidenceLoading}
                      onClick={() => void loadProfileEvidence(activePersonId, { forceRefresh: true })}
                    >
                      <RefreshCw className={cn('mr-2 h-4 w-4', evidenceLoading && 'animate-spin')} />
                      刷新证据
                    </Button>
                  </div>
                  <ScrollArea className="h-[300px]">
                    <Table>
                      <TableHeader className="sticky top-0 bg-background">
                        <TableRow>
                          <TableHead>类型</TableHead>
                          <TableHead>内容</TableHead>
                          <TableHead>来源</TableHead>
                          <TableHead>分数</TableHead>
                          <TableHead>操作</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {(currentProfileEvidence?.evidence ?? []).length > 0 ? (currentProfileEvidence?.evidence ?? []).map((item) => {
                          const evidenceKey = String(item.evidence_key ?? item.hash ?? '')
                          const isCorrecting = correctingEvidenceKey === evidenceKey
                          return (
                            <TableRow key={evidenceKey}>
                              <TableCell>
                                <Badge variant="outline">{evidenceTypeLabel(item.evidence_type)}</Badge>
                              </TableCell>
                              <TableCell className="max-w-[320px]">
                                <div className="line-clamp-3 text-sm">{item.content || '-'}</div>
                                {item.hash ? <div className="mt-1 font-mono text-xs text-muted-foreground break-all">{item.hash}</div> : null}
                              </TableCell>
                              <TableCell className="max-w-[220px]">
                                <div className="line-clamp-2 text-xs text-muted-foreground">{item.source || item.source_type || '-'}</div>
                              </TableCell>
                              <TableCell className="whitespace-nowrap text-xs">{formatEvidenceScore(item)}</TableCell>
                              <TableCell>
                                {item.deletable ? (
                                  <Button
                                    type="button"
                                    variant="outline"
                                    size="sm"
                                    disabled={Boolean(correctingEvidenceKey)}
                                    onClick={() => void correctEvidence(item)}
                                  >
                                    {isCorrecting ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Trash2 className="mr-2 h-4 w-4" />}
                                    纠错并刷新
                                  </Button>
                                ) : (
                                  <span className="text-xs text-muted-foreground">{item.not_deletable_reason || '不可操作'}</span>
                                )}
                              </TableCell>
                            </TableRow>
                          )
                        }) : (
                          <TableRow>
                            <TableCell colSpan={5} className="text-center text-muted-foreground">
                              {evidenceLoading ? '正在加载画像证据' : '当前没有可展示的支撑证据'}
                            </TableCell>
                          </TableRow>
                        )}
                      </TableBody>
                    </Table>
                  </ScrollArea>
                </div>

                <Collapsible open={showRawProfilePayload} onOpenChange={setShowRawProfilePayload} className="rounded-lg border bg-muted/10">
                  <CollapsibleTrigger asChild>
                    <Button variant="ghost" className="flex h-10 w-full justify-between px-3">
                      <span>原始响应 JSON</span>
                      <ChevronDown className={cn('h-4 w-4 transition-transform', showRawProfilePayload && 'rotate-180')} />
                    </Button>
                  </CollapsibleTrigger>
                  <CollapsibleContent className="border-t">
                    <pre className="max-h-72 overflow-auto p-3 text-xs break-words whitespace-pre-wrap">
                      {JSON.stringify(currentProfileEvidence ?? queryResult ?? selectedProfile ?? {}, null, 2)}
                    </pre>
                  </CollapsibleContent>
                </Collapsible>
              </>
            ) : (
              <div className="rounded-lg border border-dashed bg-muted/20 p-6 text-center text-sm text-muted-foreground">
                选择一个人物或执行查询后查看详情。
              </div>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>画像覆写</CardTitle>
            <CardDescription>用人工画像固定展示结果；留空保存表示清空文本但保留画像覆写记录。</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            {!selectedPersonId && !queryPersonId.trim() ? (
              <Alert>
                <AlertDescription>请选择或输入 person_id 后再编辑画像覆写。</AlertDescription>
              </Alert>
            ) : null}
            {selectedDisplayName ? <div className="text-sm text-muted-foreground">当前编辑对象：{selectedDisplayName}</div> : null}
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
              <Button variant="outline" onClick={() => void deleteOverride()} disabled={saving || (!selectedPersonId && !queryPersonId.trim())}>
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
