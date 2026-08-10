import { useNavigate } from '@tanstack/react-router'
import { useEffect, useMemo, useState } from 'react'
import { createPortal } from 'react-dom'
import {
  AlertTriangle,
  ArrowLeft,
  CheckCircle2,
  ChevronDown,
  Clock,
  Code2,
  Copy,
  Cpu,
  Download,
  FileCode2,
  FileJson,
  FileText,
  Loader2,
  Play,
  RefreshCw,
  Search,
  Timer,
  Trash2,
} from 'lucide-react'

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Switch } from '@/components/ui/switch'
import { ThinkingIllustration } from '@/components/ui/thinking-illustration'
import { Select, SelectContent, SelectItem, SelectTrigger } from '@/components/ui/select'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { useToast } from '@/hooks/use-toast'
import { resolveApiPath } from '@/lib/api-base'
import { useAvatarFetchEnabled } from '@/lib/avatar-url'
import {
  getReasoningPromptFile,
  getReasoningPromptHtmlUrl,
  listReasoningPromptFiles,
  listReasoningPromptStages,
  clearReasoningPromptStage,
  type ReasoningPromptFile,
  type ReasoningPromptSessionInfo,
  type ReasoningPromptStageInfo,
} from '@/lib/reasoning-process-api'
import { cn } from '@/lib/utils'
import {
  isRecord,
  parseStructuredPrompt,
  type StructuredPromptPayload,
} from './reasoning-process/schema'
import {
  ContextItemTimeline,
  NaturalLanguageText,
  ToolDefinitionsCollapsible,
  buildStructuredPromptCopyText,
  extractBotSelfNames,
  extractReasoningHeaderMeta,
  formatStageName,
  formatPromptPreviewText,
  getReasoningRecordTitle,
  getSessionDisplayName,
  getSessionSubtitle,
  parseMessageTagAttributes,
  type ReasoningPromptMessageAvatarMap,
} from './reasoning-process/context-items'
import { GenerationAttemptTimeline } from './reasoning-process/generation-attempts'
import {
  ReasoningReplayPanel,
  ReplayItemEditorColumn,
  createBlankReplayItem,
  createEditableReplayItems,
  type EditableReplayItem,
} from './reasoning-process/replay-editor'

const PAGE_SIZE = 50
const AUTO_SESSION = 'auto'
const ALL_GROUP_SESSIONS = '__all_group_chats__'
const CORE_STAGE_NAMES = ['planner', 'replyer']
const LLM_ERROR_STAGE_NAMES = ['llm_error']
const REMOVED_STAGE_NAMES = ['timing_gate']

type StageCategoryRow = {
  key: string
  label: string
  items: ReasoningPromptStageInfo[]
  collapsedByDefault?: boolean
}

function getInitialSearchParams(): URLSearchParams {
  if (typeof window === 'undefined') return new URLSearchParams()
  return new URLSearchParams(window.location.search)
}

function getSafeInternalReturnTo(value: string | null): string {
  const normalized = value?.trim() ?? ''
  if (
    !normalized ||
    !normalized.startsWith('/') ||
    normalized.startsWith('//') ||
    typeof window === 'undefined'
  ) {
    return ''
  }

  try {
    const url = new URL(normalized, window.location.origin)
    if (url.origin !== window.location.origin) return ''
    return `${url.pathname}${url.search}${url.hash}`
  } catch {
    return ''
  }
}

function isLearnerStage(stage: string): boolean {
  return stage.includes('learner') || stage.includes('learning')
}

function buildStageCategoryRows(stageCards: ReasoningPromptStageInfo[]): StageCategoryRow[] {
  const stageInfoByName = new Map(stageCards.map((item) => [item.name, item]))
  const usedStageNames = new Set<string>()
  const takeNamedStages = (stageNames: string[]) =>
    stageNames.flatMap((stageName) => {
      const item = stageInfoByName.get(stageName)
      if (!item) return []
      usedStageNames.add(stageName)
      return [item]
    })
  const takeMatchingStages = (predicate: (stage: string) => boolean) =>
    stageCards.filter((item) => {
      if (usedStageNames.has(item.name) || !predicate(item.name)) return false
      usedStageNames.add(item.name)
      return true
    })

  const coreStages = takeNamedStages(CORE_STAGE_NAMES)
  const learnerStages = takeMatchingStages(isLearnerStage)
  const llmErrorStages = takeNamedStages(LLM_ERROR_STAGE_NAMES)
  const removedStages = takeNamedStages(REMOVED_STAGE_NAMES)
  const otherStages = takeMatchingStages(() => true)

  return [
    { key: 'core', label: '主流程', items: coreStages },
    { key: 'learners', label: '学习器', items: learnerStages },
    { key: 'others', label: '其余', items: otherStages },
    { key: 'llm-errors', label: 'LLM 请求', items: llmErrorStages, collapsedByDefault: true },
    { key: 'removed', label: '不再使用', items: removedStages, collapsedByDefault: true },
  ].filter((row) => row.items.length > 0)
}

function formatTime(timestamp: number | null, modifiedAt: number): string {
  const value = timestamp ? timestamp : modifiedAt * 1000
  return new Date(value).toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
}

function formatSize(size: number): string {
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / 1024 / 1024).toFixed(1)} MB`
}

function formatDurationMs(durationMs: number | null): string {
  if (durationMs === null || !Number.isFinite(durationMs)) return ''
  if (durationMs < 1000) return `${durationMs.toFixed(durationMs >= 100 ? 0 : 1)} ms`
  return `${(durationMs / 1000).toFixed(2)} s`
}

function formatTokenUsage(
  promptTokens: number | null | undefined,
  completionTokens: number | null | undefined,
  totalTokens: number | null | undefined
): string {
  if (promptTokens === null || promptTokens === undefined) return ''
  const parts = [`输入 ${promptTokens.toLocaleString('zh-CN')}`]
  if (completionTokens !== null && completionTokens !== undefined) {
    parts.push(`输出 ${completionTokens.toLocaleString('zh-CN')}`)
  }
  if (totalTokens !== null && totalTokens !== undefined) {
    parts.push(`总计 ${totalTokens.toLocaleString('zh-CN')}`)
  }
  return `${parts.join(' / ')} Token`
}

function formatLlmRequestStatus(status?: string): string {
  const labels: Record<string, string> = {
    failed: '本次失败',
    final_failed: '最终失败',
    retrying: '等待重试',
    switching_model: '切换模型',
    succeeded: '请求成功',
    succeeded_after_retry: '重试后成功',
  }
  return labels[status ?? ''] ?? status ?? '状态未知'
}

function getLlmRequestStatusStyle(status?: string): string {
  if (status === 'succeeded' || status === 'succeeded_after_retry') {
    return 'border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300'
  }
  if (status === 'retrying' || status === 'switching_model') {
    return 'border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-300'
  }
  return 'border-destructive/30 bg-destructive/10 text-destructive'
}

function formatSnapshotTime(value?: string): string {
  if (!value) return ''
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN')
}

function LlmErrorDetails({ prompt }: { prompt: StructuredPromptPayload }) {
  const attempts = prompt.generation_attempts
  const status = prompt.metadata?.status
  const succeeded = status === 'succeeded_after_retry'
  const summaryItems = [
    ['任务', prompt.request?.task_name],
    ['请求类型', prompt.request?.request_type || prompt.request?.kind],
    ['操作', prompt.request?.operation],
    ['客户端', prompt.metadata?.client_type],
    ['模型', prompt.metadata?.model_name],
    ['服务商', prompt.metadata?.provider_name],
    ['请求 ID', prompt.metadata?.request_id],
    ['创建时间', formatSnapshotTime(prompt.metadata?.created_at)],
    ['更新时间', formatSnapshotTime(prompt.metadata?.updated_at)],
  ].filter((item): item is [string, string] => Boolean(item[1]))

  return (
    <section className="space-y-3 rounded-md border p-2.5 sm:p-3" aria-label="LLM 请求异常详情">
      <div className="flex flex-wrap items-center gap-2">
        {succeeded ? (
          <CheckCircle2 className="h-4 w-4 text-emerald-600 dark:text-emerald-400" />
        ) : (
          <AlertTriangle className="text-destructive h-4 w-4" />
        )}
        <h3 className="text-sm font-semibold">请求结果</h3>
        <Badge variant="outline" className={getLlmRequestStatusStyle(status)}>
          {formatLlmRequestStatus(status)}
        </Badge>
        <span className="text-muted-foreground text-xs">{attempts.length} 次尝试</span>
      </div>

      {summaryItems.length > 0 && (
        <dl className="grid gap-x-4 gap-y-2 text-xs sm:grid-cols-2 xl:grid-cols-3">
          {summaryItems.map(([label, value]) => (
            <div key={label} className="min-w-0">
              <dt className="text-muted-foreground">{label}</dt>
              <dd className="mt-0.5 font-medium break-all">{value}</dd>
            </div>
          ))}
        </dl>
      )}

      <GenerationAttemptTimeline attempts={attempts} referenceRequestItems={prompt.request_items} />
    </section>
  )
}

function getReasoningMetadataText(item: ReasoningPromptFile): string {
  const parts: string[] = []
  if (item.model_name) {
    parts.push(`模型：${item.model_name}`)
  }
  const durationText = formatDurationMs(item.duration_ms)
  if (durationText) {
    parts.push(`耗时：${durationText}`)
  }
  const tokenText = formatTokenUsage(item.prompt_tokens, item.completion_tokens, item.total_tokens)
  if (tokenText) {
    parts.push(tokenText)
  }
  return parts.join(' · ')
}

function formatAnonymousUserName(index: number): string {
  let value = index
  let suffix = ''
  do {
    suffix = String.fromCharCode(65 + (value % 26)) + suffix
    value = Math.floor(value / 26) - 1
  } while (value >= 0)
  return `用户${suffix}`
}

function getAnonymousUserName(
  rawName: unknown,
  nameMap: Map<string, string>,
  preferredName?: string
): string {
  const nameKey = String(rawName ?? '')
  const existingName = nameMap.get(nameKey)
  if (existingName) return existingName

  const anonymousName = preferredName ?? formatAnonymousUserName(new Set(nameMap.values()).size)
  nameMap.set(nameKey, anonymousName)
  return anonymousName
}

function collectMessageTagNicknames(text: string, nameMap: Map<string, string>): void {
  const messageTagPattern = /<message\b([^>]*)>/gi
  for (const match of text.matchAll(messageTagPattern)) {
    const attrs = parseMessageTagAttributes(match[1] ?? '')
    const userName = attrs.user ? getAnonymousUserName(attrs.user, nameMap) : undefined
    if (attrs.group_card) {
      getAnonymousUserName(attrs.group_card, nameMap, userName)
    }
  }
}

function collectNicknameCandidates(value: unknown, nameMap: Map<string, string>): void {
  if (Array.isArray(value)) {
    value.forEach((item) => collectNicknameCandidates(item, nameMap))
    return
  }
  if (typeof value === 'string') {
    collectMessageTagNicknames(value, nameMap)
    return
  }
  if (!isRecord(value)) return

  const userName =
    typeof value.user === 'string' ? getAnonymousUserName(value.user, nameMap) : undefined
  for (const [key, item] of Object.entries(value)) {
    if (typeof item === 'string') {
      if (
        key === 'user_name' ||
        key === 'display_name' ||
        key === 'session_display_name' ||
        key === 'user'
      ) {
        getAnonymousUserName(item, nameMap)
      } else if (key === 'group_card') {
        getAnonymousUserName(item, nameMap, userName)
      }
    }
    collectNicknameCandidates(item, nameMap)
  }
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

function eraseNicknamesFromText(text: string, nameMap: Map<string, string>): string {
  return Array.from(nameMap.entries())
    .filter(([name]) => name.length > 0)
    .sort(([left], [right]) => right.length - left.length)
    .reduce(
      (current, [name, anonymousName]) =>
        current.replace(new RegExp(escapeRegExp(name), 'g'), anonymousName),
      text
    )
}

function eraseNicknames(value: unknown, nameMap = new Map<string, string>()): unknown {
  if (Array.isArray(value)) {
    return value.map((item) => eraseNicknames(item, nameMap))
  }
  if (typeof value === 'string') {
    return eraseNicknamesFromText(value, nameMap)
  }
  if (!isRecord(value)) return value

  return Object.fromEntries(
    Object.entries(value).map(([key, item]) => [key, eraseNicknames(item, nameMap)])
  )
}

function eraseReasoningNicknames(value: unknown): unknown {
  const nameMap = new Map<string, string>()
  collectNicknameCandidates(value, nameMap)
  return eraseNicknames(value, nameMap)
}

function sanitizeDownloadFilename(value: string): string {
  return (
    value
      .trim()
      .replace(/[\\/:*?"<>|]+/g, '_')
      .replace(/\s+/g, '_')
      .slice(0, 120) || 'reasoning-process'
  )
}

function downloadJsonFile(filename: string, data: unknown): void {
  const blob = new Blob([JSON.stringify(data, null, 2)], {
    type: 'application/json;charset=utf-8',
  })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}

function extractJargonInferenceStage(
  payload: StructuredPromptPayload,
  fallbackIndex: number
): string {
  const selectionReason = payload.request?.selection_reason ?? ''
  const stageLine = selectionReason
    .split('\n')
    .map((line) => line.trim())
    .find((line) => line.startsWith('推断阶段:'))
  if (stageLine) {
    return stageLine.split(':', 2)[1]?.trim() || `stage_${fallbackIndex + 1}`
  }
  return `stage_${fallbackIndex + 1}`
}

function combineJargonLearningUpdatePayloads(
  payloads: StructuredPromptPayload[],
  displayTitle: string
): StructuredPromptPayload {
  const jargonLearningCalls = payloads.map((payload, payloadIndex) => ({
    inference_stage: extractJargonInferenceStage(payload, payloadIndex),
    request: payload.request,
    metadata: payload.metadata,
    request_items: payload.request_items,
    output_items: payload.output_items,
    generation_attempts: payload.generation_attempts,
  }))

  return {
    schema_version: 6,
    request: {
      kind: 'jargon_learning_update',
      selection_reason: `词条: ${displayTitle || '未知黑话'}\n包含 ${payloads.length} 次黑话含义推断调用。`,
    },
    metadata: payloads[0]?.metadata ?? {},
    presentation: { output_title: '黑话含义推断结果' },
    request_items: [],
    output_items: [],
    generation_attempts: [],
    tool_definitions: [],
    jargon_learning_calls: jargonLearningCalls,
  }
}

interface ReasoningProcessPageProps {
  embedded?: boolean
  toolbarContainerId?: string
  toolbarVisible?: boolean
  topbarActionsContainerId?: string
  onToolbarContentVisibleChange?: (visible: boolean) => void
}

export function ReasoningProcessPage({
  embedded = false,
  toolbarContainerId,
  toolbarVisible = true,
  topbarActionsContainerId,
  onToolbarContentVisibleChange,
}: ReasoningProcessPageProps) {
  const { toast } = useToast()
  const navigate = useNavigate()
  const initialSearchParams = useMemo(getInitialSearchParams, [])
  const initialStage = initialSearchParams.get('stage')?.trim() || 'planner'
  const initialSession = initialSearchParams.get('session')?.trim() || AUTO_SESSION
  const initialTargetStem = initialSearchParams.get('stem')?.trim() || ''
  const returnTo = useMemo(
    () => getSafeInternalReturnTo(initialSearchParams.get('returnTo')),
    [initialSearchParams]
  )
  const [items, setItems] = useState<ReasoningPromptFile[]>([])
  const [stages, setStages] = useState<string[]>([])
  const [stageInfos, setStageInfos] = useState<ReasoningPromptStageInfo[]>([])
  const [sessions, setSessions] = useState<string[]>([])
  const [sessionInfos, setSessionInfos] = useState<ReasoningPromptSessionInfo[]>([])
  const [stage, setStage] = useState(initialStage)
  const [session, setSession] = useState(initialSession)
  const [actionFilter, setActionFilter] = useState('')
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(1)
  const [targetStem, setTargetStem] = useState(initialTargetStem)
  const [refreshKey, setRefreshKey] = useState(0)
  const [total, setTotal] = useState(0)
  const [selected, setSelected] = useState<ReasoningPromptFile | null>(null)
  const [textContent, setTextContent] = useState('')
  const [jsonContent, setJsonContent] = useState('')
  const [messageAvatarMap, setMessageAvatarMap] = useState<ReasoningPromptMessageAvatarMap>({})
  const [activePreview, setActivePreview] = useState<'structured' | 'text' | 'html'>('structured')
  const [htmlPreviewUrl, setHtmlPreviewUrl] = useState('')
  const [loading, setLoading] = useState(false)
  const [contentLoading, setContentLoading] = useState(false)
  const [clearingStage, setClearingStage] = useState<string | null>(null)
  const [pendingClearStage, setPendingClearStage] = useState<ReasoningPromptStageInfo | null>(null)
  const [collapsedStageRows, setCollapsedStageRows] = useState<Set<string>>(
    () => new Set(['llm-errors', 'removed'])
  )
  const [error, setError] = useState<string | null>(null)
  const [browsingStage, setBrowsingStage] = useState(() =>
    Boolean(
      initialSearchParams.get('stage') || initialSearchParams.get('session') || initialTargetStem
    )
  )
  const [toolbarRoot, setToolbarRoot] = useState<HTMLElement | null>(null)
  const [topbarActionsRoot, setTopbarActionsRoot] = useState<HTMLElement | null>(null)
  const [replayPanelOpen, setReplayPanelOpen] = useState(false)
  const [replayItems, setReplayItems] = useState<EditableReplayItem[]>([])
  const [eraseNicknameOnExport, setEraseNicknameOnExport] = useState(true)

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const stageCards = useMemo(() => {
    if (stageInfos.length > 0) return stageInfos
    return stages.map((name) => ({ name, session_count: 0, latest_modified_at: 0 }))
  }, [stageInfos, stages])
  const stageCategoryRows = useMemo(() => buildStageCategoryRows(stageCards), [stageCards])
  const sessionInfoByName = useMemo(() => {
    return new Map(sessionInfos.map((item) => [item.name, item]))
  }, [sessionInfos])
  const structuredPrompt = useMemo(() => parseStructuredPrompt(jsonContent), [jsonContent])
  const avatarFetchEnabled = useAvatarFetchEnabled()
  const hasToolbarContent = browsingStage || Boolean(returnTo)

  useEffect(() => {
    if (!replayPanelOpen) {
      setReplayItems([])
      return
    }

    setReplayItems(createEditableReplayItems(structuredPrompt))
  }, [replayPanelOpen, selected?.session_id, selected?.stage, selected?.stem, structuredPrompt])

  useEffect(() => {
    setToolbarRoot(toolbarContainerId ? document.getElementById(toolbarContainerId) : null)
  }, [toolbarContainerId])

  useEffect(() => {
    const frameId = requestAnimationFrame(() => {
      setTopbarActionsRoot(
        topbarActionsContainerId ? document.getElementById(topbarActionsContainerId) : null
      )
    })

    return () => cancelAnimationFrame(frameId)
  }, [topbarActionsContainerId, toolbarVisible])

  useEffect(() => {
    onToolbarContentVisibleChange?.(hasToolbarContent)
  }, [hasToolbarContent, onToolbarContentVisibleChange])

  useEffect(() => {
    if (!browsingStage || !selected) {
      setReplayPanelOpen(false)
    }
  }, [browsingStage, selected])

  useEffect(() => {
    let ignore = false

    async function loadStages() {
      setLoading(true)
      setError(null)
      try {
        const data = await listReasoningPromptStages()
        if (ignore) return
        setStages(data.stages)
        setStageInfos(data.stage_infos ?? [])
      } catch (err) {
        if (!ignore) setError(err instanceof Error ? err.message : '加载推理过程类型失败')
      } finally {
        if (!ignore) setLoading(false)
      }
    }

    if (!browsingStage) {
      void loadStages()
    }

    return () => {
      ignore = true
    }
  }, [browsingStage, refreshKey])

  useEffect(() => {
    let ignore = false

    async function loadFiles() {
      if (!browsingStage) return
      setLoading(true)
      setError(null)
      try {
        const data = await listReasoningPromptFiles({
          stage,
          session,
          action: actionFilter,
          search,
          targetStem,
          page,
          pageSize: PAGE_SIZE,
        })
        if (ignore) return
        const targetItem = targetStem
          ? (data.items.find(
              (item) =>
                item.stage === stage &&
                item.session_id === data.selected_session &&
                item.stem === targetStem
            ) ?? data.items.find((item) => item.stem === targetStem))
          : undefined
        setItems(data.items)
        setStages(data.stages)
        setStageInfos(data.stage_infos ?? [])
        setSessions(data.sessions)
        setSessionInfos(data.session_infos ?? [])
        if (data.selected_session && data.selected_session !== session) {
          setSession(data.selected_session)
        }
        if (data.page !== page) {
          setPage(data.page)
        }
        setTotal(data.total)
        setSelected((current) => {
          if (targetItem) {
            return targetItem
          }
          if (
            current &&
            data.items.some(
              (item) =>
                item.stem === current.stem &&
                item.stage === current.stage &&
                item.session_id === current.session_id
            )
          ) {
            return current
          }
          return null
        })
        if (targetItem) {
          setTargetStem('')
        }
      } catch (err) {
        if (!ignore) setError(err instanceof Error ? err.message : '加载推理过程失败')
      } finally {
        if (!ignore) setLoading(false)
      }
    }

    void loadFiles()
    return () => {
      ignore = true
    }
  }, [actionFilter, browsingStage, page, refreshKey, search, session, stage, targetStem])

  useEffect(() => {
    let ignore = false

    async function loadContent() {
      setMessageAvatarMap({})
      if (!selected?.text_path) {
        setTextContent('')
      } else {
        setContentLoading(true)
        try {
          const data = await getReasoningPromptFile(selected.text_path)
          if (!ignore) setTextContent(data.content)
        } catch (err) {
          if (!ignore) {
            setTextContent(err instanceof Error ? err.message : '读取文本失败')
          }
        } finally {
          if (!ignore) setContentLoading(false)
        }
      }

      const jsonPaths = selected?.related_json_paths?.length
        ? selected.related_json_paths
        : selected?.json_path
          ? [selected.json_path]
          : []

      if (jsonPaths.length === 0) {
        setJsonContent('')
        setMessageAvatarMap({})
        return
      }

      setJsonContent('')
      setMessageAvatarMap({})
      setContentLoading(true)
      try {
        const loadedJsonFiles = await Promise.all(
          jsonPaths.map((path) => getReasoningPromptFile(path))
        )
        const data = loadedJsonFiles[0]
        const avatarEntries = avatarFetchEnabled
          ? await Promise.all(
              loadedJsonFiles.flatMap((file) =>
                Object.entries(file.message_avatars ?? {}).map(
                  async ([messageId, avatar]) =>
                    [
                      messageId,
                      {
                        ...avatar,
                        avatar_url: avatar.avatar_url
                          ? await resolveApiPath(avatar.avatar_url)
                          : null,
                      },
                    ] as const
                )
              )
            )
          : []
        const loadedPayloads = loadedJsonFiles
          .map((file) => parseStructuredPrompt(file.content))
          .filter((payload): payload is StructuredPromptPayload => Boolean(payload))
        const combinedContent =
          selected?.stage === 'jargon_learning_update' && loadedPayloads.length > 1
            ? JSON.stringify(
                combineJargonLearningUpdatePayloads(
                  loadedPayloads,
                  selected?.display_title || selected?.action_preview || ''
                ),
                null,
                2
              )
            : data.content
        if (!ignore) {
          setJsonContent(combinedContent)
          setMessageAvatarMap(Object.fromEntries(avatarEntries))
        }
      } catch (err) {
        if (!ignore) {
          setJsonContent('')
          setMessageAvatarMap({})
          setTextContent(
            (current) => current || (err instanceof Error ? err.message : '读取结构化内容失败')
          )
        }
      } finally {
        if (!ignore) setContentLoading(false)
      }
    }

    async function loadHtmlPreviewUrl() {
      if (!selected?.html_path) {
        setHtmlPreviewUrl('')
        return
      }
      const url = await getReasoningPromptHtmlUrl(selected.html_path)
      if (!ignore) setHtmlPreviewUrl(url)
    }

    if (selected?.json_path) {
      setActivePreview('structured')
    } else if (selected?.html_path && !selected.text_path) {
      setActivePreview('html')
    } else {
      setActivePreview('text')
    }
    loadContent()
    loadHtmlPreviewUrl()
    return () => {
      ignore = true
    }
  }, [avatarFetchEnabled, selected])

  function resetToFirstPage(nextAction: () => void) {
    nextAction()
    setTargetStem('')
    setPage(1)
  }

  function enterStage(nextStage: string) {
    resetToFirstPage(() => {
      setStage(nextStage)
      setSession(AUTO_SESSION)
      setActionFilter('')
      setSearch('')
      setItems([])
      setSessions([])
      setSessionInfos([])
      setTotal(0)
      setSelected(null)
      setBrowsingStage(true)
    })
  }

  async function handleConfirmClearStage() {
    if (!pendingClearStage) return
    const stageName = pendingClearStage.name
    const label = formatStageName(stageName)

    setClearingStage(stageName)
    try {
      const result = await clearReasoningPromptStage(stageName)
      toast({
        title: '已清空推理过程',
        description: `${label}：删除 ${result.deleted_files} 个文件`,
      })
      setStageInfos((current) => current.filter((item) => item.name !== stageName))
      setStages((current) => current.filter((item) => item !== stageName))
      if (stage === stageName) {
        setItems([])
        setSessions([])
        setSessionInfos([])
        setTotal(0)
        setSelected(null)
        setBrowsingStage(false)
      }
      setRefreshKey((current) => current + 1)
      setPendingClearStage(null)
    } catch (err) {
      toast({
        title: '清空失败',
        description: err instanceof Error ? err.message : '请稍后再试',
        variant: 'destructive',
      })
    } finally {
      setClearingStage(null)
    }
  }

  async function handleCopyPrompt() {
    const copyContent =
      textContent || buildStructuredPromptCopyText(structuredPrompt) || jsonContent
    if (!copyContent || contentLoading) {
      toast({
        title: '暂无可复制内容',
        description: '请先选择一条包含 prompt 内容的记录',
        variant: 'destructive',
      })
      return
    }

    try {
      await navigator.clipboard.writeText(copyContent)
      toast({
        title: '已复制完整 Prompt',
        description: selected ? getReasoningRecordTitle(selected, selectedSessionInfo) : undefined,
      })
    } catch (err) {
      toast({
        title: '复制失败',
        description: err instanceof Error ? err.message : '请手动选择文本复制',
        variant: 'destructive',
      })
    }
  }

  function handleDownloadReasoningJson() {
    if (!jsonContent.trim() || contentLoading) {
      toast({
        title: '暂无可导出内容',
        description: '请先选择一条包含 JSON 的推理过程记录',
        variant: 'destructive',
      })
      return
    }

    try {
      const parsedContent = structuredPrompt ?? (JSON.parse(jsonContent) as unknown)
      const exportContent = eraseNicknameOnExport
        ? eraseReasoningNicknames(parsedContent)
        : parsedContent
      const filenameParts = [
        'reasoning',
        selected?.stage,
        selected?.session_display_name || selectedSessionInfo?.display_name || selected?.session_id,
        selected?.display_title || selected?.stem,
        eraseNicknameOnExport ? '匿名' : '',
      ].filter(Boolean)
      const filename = `${sanitizeDownloadFilename(filenameParts.join('-'))}.json`
      downloadJsonFile(filename, exportContent)
      toast({
        title: '已导出推理过程',
        description: eraseNicknameOnExport
          ? '已将昵称抹去为用户A、用户B等占位名'
          : '已保留原始昵称',
      })
    } catch (err) {
      toast({
        title: '导出失败',
        description: err instanceof Error ? err.message : '当前内容不是有效 JSON',
        variant: 'destructive',
      })
    }
  }

  const updateReplayItem = (id: string, patch: Partial<EditableReplayItem>) => {
    setReplayItems((current) =>
      current.map((item) => (item.id === id ? { ...item, ...patch } : item))
    )
  }

  const addReplayItem = () => {
    setReplayItems((current) => [...current, createBlankReplayItem()])
  }

  const deleteReplayItem = (id: string) => {
    setReplayItems((current) => current.filter((item) => item.id !== id))
  }

  const selectedSessionInfo = selected ? sessionInfoByName.get(selected.session_id) : undefined
  const selectedTitle = selected
    ? getReasoningRecordTitle(selected, selectedSessionInfo)
    : '未选择记录'
  const botSelfNames = useMemo(() => extractBotSelfNames(structuredPrompt), [structuredPrompt])
  const previewTabMode = selected?.json_path
    ? 'structured'
    : selected?.text_path
      ? 'text'
      : selected?.html_path
        ? 'html'
        : null
  const headerMeta = useMemo(
    () => extractReasoningHeaderMeta(structuredPrompt?.request?.selection_reason),
    [structuredPrompt]
  )
  const selectedTokenText = selected
    ? formatTokenUsage(selected.prompt_tokens, selected.completion_tokens, selected.total_tokens)
    : ''
  const renderRefreshButton = (variant: 'default' | 'topbar' | 'toolbar' = 'default') => (
    <Button
      variant="outline"
      size="sm"
      aria-label="刷新"
      title="刷新"
      onClick={() => setRefreshKey((current) => current + 1)}
      disabled={loading}
      className={cn(
        'shrink-0 p-0',
        variant === 'topbar' && 'h-9 w-9',
        variant === 'toolbar' && 'h-8 w-8',
        variant === 'default' && 'h-9 w-9 sm:h-10 sm:w-10'
      )}
    >
      <RefreshCw className={cn('h-4 w-4', loading && 'animate-spin')} />
    </Button>
  )
  const renderReturnButton = () =>
    returnTo ? (
      <Button
        variant="outline"
        size="sm"
        className="h-9 shrink-0 gap-1.5 sm:h-10"
        onClick={() => navigate({ to: returnTo })}
        title="返回麦麦观察"
      >
        <ArrowLeft className="h-4 w-4" />
        返回观察
      </Button>
    ) : null
  const renderTypeButton = (compact = false) => (
    <Button
      variant="outline"
      size="sm"
      className={cn('shrink-0 justify-start', compact ? 'h-9' : 'h-9 sm:h-10')}
      onClick={() => setBrowsingStage(false)}
    >
      <ArrowLeft className="h-4 w-4" />
      类型
    </Button>
  )
  const renderSessionSelect = (placement: 'toolbar' | 'sidebar' | 'sidebarRow' = 'toolbar') => {
    const inToolbar = placement === 'toolbar'
    const controlClassName = inToolbar ? 'h-8' : 'h-9 sm:h-10'
    const selectedSessionLabel =
      session === AUTO_SESSION
        ? '自动选择最近会话'
        : session === ALL_GROUP_SESSIONS
          ? '全部群聊'
          : getSessionDisplayName(session, sessionInfoByName.get(session))

    return (
      <Select
        value={session}
        onValueChange={(value) => resetToFirstPage(() => setSession(value))}
        disabled={sessions.length === 0 && loading}
      >
        <SelectTrigger
          className={cn(
            controlClassName,
            inToolbar && 'w-full sm:w-[240px]',
            placement === 'sidebarRow' && 'w-full'
          )}
        >
          <span className="truncate">{selectedSessionLabel || '会话'}</span>
        </SelectTrigger>
        <SelectContent>
          {session === AUTO_SESSION && (
            <SelectItem value={AUTO_SESSION} textValue="自动选择最近会话">
              自动选择最近会话
            </SelectItem>
          )}
          <SelectItem value={ALL_GROUP_SESSIONS} textValue="全部群聊">
            全部群聊
          </SelectItem>
          {sessions.map((item) => {
            const sessionInfo = sessionInfoByName.get(item)
            const sessionSubtitle = getSessionSubtitle(sessionInfo)
            const sessionDisplayName = getSessionDisplayName(item, sessionInfo)
            return (
              <SelectItem key={item} value={item} textValue={sessionDisplayName}>
                <div className="min-w-0">
                  <div className="truncate">{sessionDisplayName}</div>
                  {sessionSubtitle && (
                    <div className="text-muted-foreground truncate text-xs">{sessionSubtitle}</div>
                  )}
                </div>
              </SelectItem>
            )
          })}
        </SelectContent>
      </Select>
    )
  }

  const renderBrowsingFilters = (placement: 'toolbar' | 'sidebar' = 'toolbar') => {
    const inToolbar = placement === 'toolbar'
    const controlClassName = inToolbar ? 'h-8' : 'h-9 sm:h-10'

    return (
      <>
        <div className={cn('relative', inToolbar ? 'w-full sm:w-[140px]' : undefined)}>
          <Input
            value={actionFilter}
            onChange={(event) => resetToFirstPage(() => setActionFilter(event.target.value))}
            className={controlClassName}
            placeholder="动作过滤"
          />
        </div>

        <div
          className={cn(
            'relative',
            inToolbar ? 'min-w-0 flex-[1_1_220px] sm:max-w-[520px] sm:min-w-[260px]' : undefined
          )}
        >
          <Search className="text-muted-foreground pointer-events-none absolute top-1/2 left-3 h-4 w-4 -translate-y-1/2" />
          <Input
            value={search}
            onChange={(event) => resetToFirstPage(() => setSearch(event.target.value))}
            className={cn(controlClassName, 'pl-9')}
            placeholder="搜索会话、文件名、模型或记录摘要"
          />
        </div>
      </>
    )
  }
  const renderBrowsingControls = (inToolbar = false) => (
    <>
      {renderTypeButton(inToolbar)}
      {renderBrowsingFilters('toolbar')}
    </>
  )
  const toolbarContent = (
    <div className="flex w-full min-w-0 flex-wrap items-center justify-start gap-1.5 sm:justify-end">
      {renderReturnButton()}
      {!embedded && browsingStage && renderBrowsingControls(true)}
      {!embedded && renderRefreshButton('default')}
    </div>
  )
  const toolbarPortal =
    embedded && toolbarVisible && toolbarRoot ? createPortal(toolbarContent, toolbarRoot) : null
  const topbarActionsPortal =
    embedded && toolbarVisible && !browsingStage && topbarActionsRoot
      ? createPortal(renderRefreshButton('topbar'), topbarActionsRoot)
      : null
  const showBrowsingControlsInline = browsingStage && (!embedded || !toolbarVisible || !toolbarRoot)
  const renderStageCard = (item: ReasoningPromptStageInfo) => (
    <div
      key={item.name}
      className={cn(
        'group bg-background relative flex min-h-20 flex-col rounded-md border text-left shadow-sm',
        'transition-[border-color,background-color,box-shadow,transform] duration-150 ease-out',
        'hover:border-primary/80 hover:bg-primary/5 hover:-translate-y-0.5 hover:shadow-md',
        'focus-within:border-primary/80 focus-within:bg-primary/5 focus-within:-translate-y-0.5 focus-within:shadow-md'
      )}
    >
      <button
        type="button"
        onClick={() => enterStage(item.name)}
        className="focus-visible:ring-ring flex min-h-20 flex-1 cursor-pointer flex-col justify-between rounded-md p-3 pr-10 text-left focus-visible:ring-2 focus-visible:outline-none"
      >
        <div className="space-y-1.5">
          <div className="text-primary group-hover:text-primary text-sm font-extrabold tracking-normal uppercase transition-colors sm:text-base">
            {item.name}
          </div>
          <div className="text-foreground group-hover:text-primary text-sm font-semibold transition-colors">
            {formatStageName(item.name)}
          </div>
        </div>
        <div className="text-muted-foreground group-hover:text-foreground/80 mt-2 text-xs transition-colors">
          {item.session_count} 个会话
          {item.latest_modified_at > 0
            ? ` · 最新 ${formatTime(null, item.latest_modified_at)}`
            : ''}
        </div>
      </button>
      <Button
        type="button"
        variant="ghost"
        size="sm"
        className="hover:bg-destructive/10 hover:text-destructive absolute right-2 bottom-2 h-7 w-7 p-0 opacity-70 group-hover:opacity-100"
        title={`清空${formatStageName(item.name)}`}
        aria-label={`清空${formatStageName(item.name)}`}
        disabled={clearingStage === item.name}
        onClick={() => setPendingClearStage(item)}
      >
        {clearingStage === item.name ? (
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
        ) : (
          <Trash2 className="h-3.5 w-3.5" />
        )}
      </Button>
    </div>
  )

  const renderStageRow = (row: StageCategoryRow) => {
    const collapsed = collapsedStageRows.has(row.key)
    const setRowOpen = (open: boolean) => {
      setCollapsedStageRows((current) => {
        const next = new Set(current)
        if (open) {
          next.delete(row.key)
        } else {
          next.add(row.key)
        }
        return next
      })
    }

    if (!row.collapsedByDefault) {
      return (
        <section
          key={row.key}
          className="grid gap-2 sm:grid-cols-[72px_minmax(0,1fr)] sm:items-start"
        >
          <div className="text-muted-foreground px-1 pt-1 text-xs font-medium sm:pt-3">
            {row.label}
          </div>
          <div className="grid gap-2 sm:grid-cols-3 lg:grid-cols-4 2xl:grid-cols-5">
            {row.items.map((item) => renderStageCard(item))}
          </div>
        </section>
      )
    }

    return (
      <Collapsible key={row.key} open={!collapsed} onOpenChange={setRowOpen}>
        <section className="grid gap-2 sm:grid-cols-[72px_minmax(0,1fr)] sm:items-start">
          <CollapsibleTrigger asChild>
            <button
              type="button"
              className="text-muted-foreground hover:text-foreground flex items-center gap-1 px-1 pt-1 text-left text-xs font-medium transition-colors sm:pt-3"
            >
              <ChevronDown
                className={cn('h-3.5 w-3.5 transition-transform', collapsed && '-rotate-90')}
              />
              {row.label}
              <span className="text-muted-foreground/80">({row.items.length})</span>
            </button>
          </CollapsibleTrigger>
          <CollapsibleContent>
            <div className="grid gap-2 sm:grid-cols-3 lg:grid-cols-4 2xl:grid-cols-5">
              {row.items.map((item) => renderStageCard(item))}
            </div>
          </CollapsibleContent>
        </section>
      </Collapsible>
    )
  }

  const pendingClearStageLabel = pendingClearStage ? formatStageName(pendingClearStage.name) : ''
  const pendingClearStageDeleting = pendingClearStage
    ? clearingStage === pendingClearStage.name
    : false

  return (
    <div
      className={cn(
        'flex h-full min-h-0 flex-col gap-2 overflow-hidden sm:gap-3',
        embedded ? 'p-0' : 'p-2 lg:p-4'
      )}
    >
      {toolbarPortal}
      {topbarActionsPortal}

      <AlertDialog
        open={Boolean(pendingClearStage)}
        onOpenChange={(open) => {
          if (!open && !pendingClearStageDeleting) {
            setPendingClearStage(null)
          }
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>清空推理过程记录</AlertDialogTitle>
            <AlertDialogDescription>
              将清空「{pendingClearStageLabel}」下的全部推理过程日志。
              {pendingClearStage?.session_count
                ? ` 当前包含 ${pendingClearStage.session_count} 个会话。`
                : ''}
              此操作不可撤销。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={pendingClearStageDeleting}>取消</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              disabled={pendingClearStageDeleting}
              onClick={(event) => {
                event.preventDefault()
                void handleConfirmClearStage()
              }}
            >
              {pendingClearStageDeleting ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
              确认清空
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {!embedded && (
        <div className="flex flex-shrink-0 items-start justify-between gap-3">
          <div>
            <h1 className="text-foreground text-xl font-semibold tracking-normal">推理过程</h1>
            <p className="text-muted-foreground text-sm">
              浏览 logs/maisaka_prompt 下的 prompt 记录
            </p>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            {renderReturnButton()}
            {renderRefreshButton()}
          </div>
        </div>
      )}

      {showBrowsingControlsInline && (
        <div className="grid flex-shrink-0 grid-cols-[auto_minmax(0,1fr)] gap-2 sm:grid-cols-[auto_minmax(220px,320px)_1fr] [&>div:last-child]:col-span-2 sm:[&>div:last-child]:col-span-1">
          {renderBrowsingControls()}
        </div>
      )}

      {error && (
        <div className="border-destructive/30 bg-destructive/10 text-destructive rounded-md border px-3 py-2 text-sm">
          {error}
        </div>
      )}

      {!browsingStage ? (
        <div className="bg-background flex min-h-0 flex-1 flex-col overflow-hidden rounded-md">
          <ScrollArea className="min-h-0 flex-1">
            <div className="space-y-4 p-2 sm:p-3">
              {stageCategoryRows.map(renderStageRow)}
              {!loading && stageCards.length === 0 && (
                <div className="text-muted-foreground px-3 py-10 text-center text-sm">
                  没有找到推理过程类型
                </div>
              )}
            </div>
          </ScrollArea>
        </div>
      ) : (
        <div
          className={cn(
            'grid min-h-0 flex-1 grid-cols-1 gap-2 transition-[gap,grid-template-columns] duration-300 ease-out lg:gap-3',
            replayPanelOpen
              ? 'lg:grid-cols-[280px_minmax(0,1fr)_420px] xl:grid-cols-[300px_minmax(0,1fr)_460px]'
              : 'lg:grid-cols-[280px_minmax(0,1fr)]'
          )}
        >
          <div className="bg-background flex h-[32vh] min-h-[180px] flex-col overflow-hidden rounded-md border transition-[height,min-height,opacity,transform,border-width] duration-300 ease-out lg:h-auto lg:min-h-0 lg:transition-[opacity,transform,border-width]">
            <div className="text-muted-foreground flex h-8 flex-shrink-0 items-center justify-between border-b px-2.5 text-xs">
              <span>{total} 条记录</span>
              <span>
                第 {page} / {totalPages} 页
              </span>
            </div>
            {embedded && browsingStage && (
              <div className="flex flex-shrink-0 flex-col gap-2 border-b p-2">
                <div className="flex items-center gap-2">
                  {renderTypeButton(true)}
                  <div className="min-w-0 flex-1">{renderSessionSelect('sidebarRow')}</div>
                  {renderRefreshButton('toolbar')}
                </div>
                {renderBrowsingFilters('sidebar')}
              </div>
            )}
            <ScrollArea className="min-h-0 flex-1">
              <div className="space-y-1 p-1.5 sm:p-2">
                {items.map((item) => {
                  const active =
                    selected?.stage === item.stage &&
                    selected?.session_id === item.session_id &&
                    selected?.stem === item.stem
                  const durationText = formatDurationMs(item.duration_ms)
                  const metadataText = getReasoningMetadataText(item)
                  const rawPreviewText =
                    item.display_title ||
                    (item.stage === 'replyer' ? item.output_preview : item.action_preview)
                  const previewText = rawPreviewText ? formatPromptPreviewText(rawPreviewText) : ''
                  return (
                    <button
                      key={`${item.stage}/${item.session_id}/${item.stem}`}
                      type="button"
                      onClick={() => setSelected(item)}
                      className={cn(
                        'flex w-full flex-col gap-1.5 rounded-md border px-2.5 py-2 text-left text-sm transition-colors sm:gap-2 sm:px-3',
                        active
                          ? 'border-primary bg-primary/10 text-foreground'
                          : 'hover:border-border hover:bg-muted/60 border-transparent'
                      )}
                    >
                      <div className="flex items-start justify-between gap-2">
                        <div className="min-w-0 flex-1">
                          {(item.has_behavior_choice_insert || previewText) && (
                            <div className="flex min-w-0 items-start gap-1.5">
                              {item.has_behavior_choice_insert && (
                                <span
                                  className="mt-1.5 h-2 w-2 shrink-0 rounded-full bg-violet-500"
                                  title="包含行为表现参考"
                                  aria-label="包含行为表现参考"
                                />
                              )}
                              {previewText && (
                                <div
                                  className="text-foreground line-clamp-2 min-w-0 text-sm font-medium"
                                  title={previewText}
                                >
                                  {previewText}
                                </div>
                              )}
                            </div>
                          )}
                        </div>
                        <span className="text-muted-foreground flex shrink-0 items-center gap-1 text-xs">
                          <Clock className="h-3.5 w-3.5" />
                          {formatTime(item.timestamp, item.modified_at)}
                        </span>
                      </div>
                      {metadataText && (
                        <div
                          className="text-muted-foreground flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 text-xs"
                          title={metadataText}
                        >
                          {item.model_name && (
                            <span className="inline-flex min-w-0 items-center gap-1">
                              <Cpu className="h-3.5 w-3.5 shrink-0" />
                              <span className="truncate">{item.model_name}</span>
                            </span>
                          )}
                          {durationText && (
                            <span className="inline-flex items-center gap-1">
                              <Timer className="h-3.5 w-3.5 shrink-0" />
                              {durationText}
                            </span>
                          )}
                          {item.prompt_tokens !== null && (
                            <span className="inline-flex items-center gap-1">
                              {formatTokenUsage(
                                item.prompt_tokens,
                                item.completion_tokens,
                                item.total_tokens
                              )}
                            </span>
                          )}
                          <span className="shrink-0">{formatSize(item.size)}</span>
                        </div>
                      )}
                    </button>
                  )
                })}
                {!loading && items.length === 0 && (
                  <div className="text-muted-foreground px-3 py-10 text-center text-sm">
                    没有找到推理过程记录
                  </div>
                )}
              </div>
            </ScrollArea>
            <div className="flex h-11 flex-shrink-0 items-center justify-between border-t px-3 lg:h-12">
              <Button
                variant="outline"
                size="sm"
                disabled={page <= 1 || loading}
                onClick={() => setPage((current) => Math.max(1, current - 1))}
              >
                上一页
              </Button>
              <Button
                variant="outline"
                size="sm"
                disabled={page >= totalPages || loading}
                onClick={() => setPage((current) => Math.min(totalPages, current + 1))}
              >
                下一页
              </Button>
            </div>
          </div>

          <div className="bg-background flex min-h-0 flex-col overflow-hidden rounded-md border">
            {replayPanelOpen ? (
              <ReplayItemEditorColumn
                selectedTitle={selectedTitle}
                items={replayItems}
                updateItem={updateReplayItem}
                addItem={addReplayItem}
                deleteItem={deleteReplayItem}
                onClose={() => setReplayPanelOpen(false)}
              />
            ) : (
              <Tabs
                value={activePreview}
                onValueChange={(value) => setActivePreview(value as 'structured' | 'text' | 'html')}
                className="flex min-h-0 flex-1 flex-col"
              >
                <div className="relative min-h-0 flex-1 overflow-hidden">
                  <ScrollArea className="h-full transition-transform duration-300 ease-out">
                    <div className="min-h-full">
                      <div className="flex min-h-12 flex-col gap-2 border-b px-3 py-2 sm:min-h-14 sm:px-4 sm:py-3 xl:flex-row xl:items-center xl:justify-between">
                        <div className="min-w-0 flex-1">
                          <div className="truncate text-sm font-medium">{selectedTitle}</div>
                          {(headerMeta.sessionId || headerMeta.callId || selectedTokenText) && (
                            <div className="text-muted-foreground mt-1 flex min-w-0 flex-wrap gap-x-3 gap-y-0.5 text-[11px] leading-4">
                              {headerMeta.sessionId && (
                                <span className="min-w-0 truncate">
                                  会话ID: {headerMeta.sessionId}
                                </span>
                              )}
                              {headerMeta.callId && (
                                <span className="min-w-0 truncate">
                                  调用ID: {headerMeta.callId}
                                </span>
                              )}
                              {selectedTokenText && <span>{selectedTokenText}</span>}
                            </div>
                          )}
                          {!selected && (
                            <div className="text-muted-foreground truncate text-xs">
                              从左侧列表选择一条记录
                            </div>
                          )}
                        </div>
                        {selected && (
                          <div className="text-muted-foreground flex min-w-0 flex-wrap items-center gap-2 text-xs">
                            <TabsList className="h-8 rounded-md">
                              {previewTabMode === 'structured' && (
                                <TabsTrigger value="structured" className="h-6 gap-1 px-2 text-xs">
                                  <FileJson className="h-3.5 w-3.5" />
                                  结构化
                                </TabsTrigger>
                              )}
                              {previewTabMode === 'text' && (
                                <TabsTrigger value="text" className="h-6 gap-1 px-2 text-xs">
                                  <FileText className="h-3.5 w-3.5" />
                                  文本
                                </TabsTrigger>
                              )}
                              {selected.html_path && (
                                <TabsTrigger value="html" className="h-6 gap-1 px-2 text-xs">
                                  <Code2 className="h-3.5 w-3.5" />
                                  HTML
                                </TabsTrigger>
                              )}
                            </TabsList>
                            <Button
                              variant="outline"
                              size="sm"
                              className="h-8 gap-1.5"
                              onClick={handleCopyPrompt}
                              disabled={
                                contentLoading ||
                                !(
                                  textContent ||
                                  buildStructuredPromptCopyText(structuredPrompt) ||
                                  jsonContent
                                )
                              }
                              title="复制完整 Prompt"
                            >
                              <Copy className="h-3.5 w-3.5" />
                              复制
                            </Button>
                            <Popover>
                              <PopoverTrigger asChild>
                                <Button
                                  variant="outline"
                                  size="sm"
                                  className="h-8 gap-1.5"
                                  disabled={contentLoading || !jsonContent.trim()}
                                  title="导出当前 JSON"
                                >
                                  <Download className="h-3.5 w-3.5" />
                                  导出
                                </Button>
                              </PopoverTrigger>
                              <PopoverContent align="end" className="w-72">
                                <div className="space-y-3">
                                  <div>
                                    <div className="text-sm font-semibold">导出推理过程</div>
                                    <div className="text-muted-foreground mt-1 text-xs leading-5">
                                      下载当前记录的 JSON。
                                    </div>
                                  </div>
                                  <div className="flex items-center justify-between gap-3 rounded-md border px-3 py-2">
                                    <Label
                                      htmlFor="reasoning-export-erase-nickname"
                                      className="cursor-pointer text-sm font-medium"
                                    >
                                      抹去昵称
                                    </Label>
                                    <Switch
                                      id="reasoning-export-erase-nickname"
                                      checked={eraseNicknameOnExport}
                                      onCheckedChange={setEraseNicknameOnExport}
                                    />
                                  </div>
                                  <Button
                                    className="h-8 w-full gap-1.5"
                                    size="sm"
                                    onClick={handleDownloadReasoningJson}
                                    disabled={contentLoading || !jsonContent.trim()}
                                  >
                                    <Download className="h-3.5 w-3.5" />
                                    下载 JSON
                                  </Button>
                                </div>
                              </PopoverContent>
                            </Popover>
                            <Button
                              variant="outline"
                              size="sm"
                              className="h-8 gap-1.5"
                              onClick={() => setReplayPanelOpen(true)}
                              disabled={
                                contentLoading ||
                                (structuredPrompt?.request_items.length ?? 0) === 0
                              }
                              title="编辑 Items 并重放本次请求"
                            >
                              <Play className="h-3.5 w-3.5" />
                              重放
                            </Button>
                            {selected.text_path && (
                              <span className="inline-flex items-center gap-1">
                                <FileText className="h-3.5 w-3.5" />
                                txt
                              </span>
                            )}
                            {selected.json_path && (
                              <span className="inline-flex items-center gap-1">
                                <FileJson className="h-3.5 w-3.5" />
                                json
                              </span>
                            )}
                            {selected.html_path && (
                              <span className="inline-flex items-center gap-1">
                                <FileCode2 className="h-3.5 w-3.5" />
                                html
                              </span>
                            )}
                          </div>
                        )}
                      </div>

                      <TabsContent value="structured" className="m-0">
                        {contentLoading ? (
                          <div className="flex min-h-[360px] items-center justify-center p-4">
                            <ThinkingIllustration />
                          </div>
                        ) : structuredPrompt ? (
                          <div className="space-y-2 p-2 sm:space-y-3 sm:p-3">
                            {selected?.stage === 'llm_error' && (
                              <LlmErrorDetails prompt={structuredPrompt} />
                            )}

                            {headerMeta.remainingText && (
                              <div className="rounded-md border p-2.5 sm:p-3">
                                <NaturalLanguageText
                                  text={headerMeta.remainingText}
                                  avatarMap={messageAvatarMap}
                                />
                              </div>
                            )}

                            {structuredPrompt.jargon_learning_calls &&
                              structuredPrompt.jargon_learning_calls.length > 0 && (
                                <div className="space-y-3">
                                  {structuredPrompt.jargon_learning_calls.map(
                                    (llmCall, callIndex) => (
                                      <div
                                        key={`${llmCall.inference_stage}-${callIndex}`}
                                        className="space-y-2 rounded-md border p-2.5 sm:p-3"
                                      >
                                        <div className="flex flex-wrap items-center gap-2">
                                          <Badge variant="secondary">
                                            #{callIndex + 1} {llmCall.inference_stage}
                                          </Badge>
                                          {llmCall.metadata?.model_name && (
                                            <span className="text-muted-foreground inline-flex items-center gap-1 text-xs">
                                              <Cpu className="h-3.5 w-3.5" />
                                              {llmCall.metadata.model_name}
                                            </span>
                                          )}
                                          {typeof llmCall.metadata?.duration_ms === 'number' && (
                                            <span className="text-muted-foreground inline-flex items-center gap-1 text-xs">
                                              <Timer className="h-3.5 w-3.5" />
                                              {formatDurationMs(llmCall.metadata.duration_ms)}
                                            </span>
                                          )}
                                        </div>

                                        <ContextItemTimeline
                                          title="请求 Items"
                                          items={llmCall.request_items}
                                          avatarMap={messageAvatarMap}
                                          botSelfNames={botSelfNames}
                                        />

                                        <GenerationAttemptTimeline
                                          attempts={llmCall.generation_attempts}
                                          referenceRequestItems={llmCall.request_items}
                                        />

                                        <ContextItemTimeline
                                          title="输出 Items"
                                          items={llmCall.output_items}
                                          avatarMap={messageAvatarMap}
                                          botSelfNames={botSelfNames}
                                        />
                                      </div>
                                    )
                                  )}
                                </div>
                              )}

                            <ContextItemTimeline
                              title="请求 Items"
                              items={structuredPrompt.request_items}
                              avatarMap={messageAvatarMap}
                              botSelfNames={botSelfNames}
                            />

                            <GenerationAttemptTimeline
                              attempts={structuredPrompt.generation_attempts}
                              referenceRequestItems={structuredPrompt.request_items}
                            />

                            <ContextItemTimeline
                              title={structuredPrompt.presentation?.output_title || '输出 Items'}
                              items={structuredPrompt.output_items}
                              avatarMap={messageAvatarMap}
                              botSelfNames={botSelfNames}
                            />

                            {structuredPrompt.tool_definitions &&
                              structuredPrompt.tool_definitions.length > 0 && (
                                <ToolDefinitionsCollapsible
                                  toolDefinitions={structuredPrompt.tool_definitions}
                                />
                              )}
                          </div>
                        ) : (
                          <div className="text-muted-foreground flex min-h-[360px] items-center justify-center text-sm">
                            没有结构化内容
                          </div>
                        )}
                      </TabsContent>

                      <TabsContent value="text" className="m-0">
                        {contentLoading ? (
                          <div className="flex min-h-[360px] items-center justify-center p-4">
                            <ThinkingIllustration />
                          </div>
                        ) : (
                          <pre className="text-foreground min-h-[280px] p-3 font-mono text-xs leading-5 break-words whitespace-pre-wrap sm:min-h-[360px] sm:p-4">
                            {textContent || '没有文本内容'}
                          </pre>
                        )}
                      </TabsContent>

                      <TabsContent value="html" className="m-0">
                        {selected?.html_path && htmlPreviewUrl ? (
                          <iframe
                            title="推理过程 HTML 预览"
                            src={htmlPreviewUrl}
                            sandbox=""
                            className="h-[58vh] min-h-[320px] w-full border-0 bg-white sm:h-[70vh] sm:min-h-[420px]"
                          />
                        ) : (
                          <div className="text-muted-foreground flex min-h-[360px] items-center justify-center text-sm">
                            没有 HTML 预览
                          </div>
                        )}
                      </TabsContent>
                    </div>
                  </ScrollArea>
                </div>
              </Tabs>
            )}
          </div>
          <ReasoningReplayPanel
            open={replayPanelOpen}
            onClose={() => setReplayPanelOpen(false)}
            selected={selected}
            selectedTitle={selectedTitle}
            structuredPrompt={structuredPrompt}
            items={replayItems}
          />
        </div>
      )}
    </div>
  )
}
