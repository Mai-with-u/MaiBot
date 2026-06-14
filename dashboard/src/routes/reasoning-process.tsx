import { type CSSProperties, useEffect, useMemo, useState } from 'react'
import { createPortal } from 'react-dom'
import {
  ArrowLeft,
  ChevronDown,
  Clock,
  Code2,
  Copy,
  Cpu,
  FileCode2,
  FileJson,
  FileText,
  Layers,
  RefreshCw,
  Search,
  Timer,
} from 'lucide-react'

import { Avatar, AvatarFallback, AvatarImage } from '@/components/ui/avatar'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import { Input } from '@/components/ui/input'
import { ScrollArea } from '@/components/ui/scroll-area'
import { ThinkingIllustration } from '@/components/ui/thinking-illustration'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { useToast } from '@/hooks/use-toast'
import { resolveApiPath } from '@/lib/api-base'
import { useAvatarFetchEnabled } from '@/lib/avatar-url'
import {
  getReasoningPromptFile,
  getReasoningPromptHtmlUrl,
  listReasoningPromptFiles,
  listReasoningPromptStages,
  type ReasoningPromptFile,
  type ReasoningPromptMessageAvatar,
  type ReasoningPromptSessionInfo,
  type ReasoningPromptStageInfo,
} from '@/lib/reasoning-process-api'
import { cn } from '@/lib/utils'

const PAGE_SIZE = 50
const AUTO_SESSION = 'auto'
const ALL_GROUP_SESSIONS = '__all_group_chats__'
const PRIMARY_STAGE_NAMES = ['timing_gate', 'planner', 'replyer']
const NATURAL_LANGUAGE_TEXT_STYLE: CSSProperties = {
  fontFamily:
    "'Microsoft YaHei UI', 'Microsoft YaHei', 'PingFang SC', 'Noto Sans SC', system-ui, sans-serif",
}
const STAGE_LABELS: Record<string, string> = {
  emotion: '情绪分析',
  expression_learner: '表达学习',
  planner: '规划器',
  reply_effect_judge: '回复效果评估',
  replyer: '回复器',
  timing_gate: '时机判断',
}

type StructuredPromptMessage = {
  index?: number
  role?: string
  content?: unknown
  content_text?: string
  tool_call_id?: string
  tool_calls?: unknown[]
}

type StructuredPromptPayload = {
  schema_version?: number
  request?: {
    kind?: string
    selection_reason?: string
  }
  metadata?: {
    model_name?: string
    duration_ms?: number
  }
  messages?: StructuredPromptMessage[]
  output?: {
    title?: string
    content?: unknown
    content_text?: string
    tool_calls?: unknown[]
  } | null
  tool_definitions?: unknown[]
  text_dump?: string
}

type ParsedMessageTagBlock = {
  type: 'message'
  attrs: Record<string, string>
  body: string
}

type ParsedTextBlock = {
  type: 'text'
  text: string
}

type ParsedNaturalTextBlock = ParsedMessageTagBlock | ParsedTextBlock

type ToolParameterView = {
  name: string
  type: string
  description: string
  required: boolean
  enumValues: string[]
  defaultValue: string
}

type ToolDefinitionView = {
  name: string
  type: string
  description: string
  parameters: ToolParameterView[]
  raw: unknown
}

type ReasoningPromptMessageAvatarMap = Record<string, ReasoningPromptMessageAvatar>

function formatStageName(stage: string): string {
  return STAGE_LABELS[stage] ?? stage
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

function getReasoningMetadataText(item: ReasoningPromptFile): string {
  const parts: string[] = []
  if (item.model_name) {
    parts.push(`模型：${item.model_name}`)
  }
  const durationText = formatDurationMs(item.duration_ms)
  if (durationText) {
    parts.push(`耗时：${durationText}`)
  }
  return parts.join(' · ')
}

function getStructuredPromptMessageRoleStyle(role?: string, isBotSelf = false): {
  label: string
  containerClassName: string
  badgeClassName: string
} {
  const normalizedRole = String(role || '').trim().toLowerCase()
  if (isBotSelf) {
    return {
      label: role || 'user',
      containerClassName: 'border-orange-300/70 bg-orange-50/75 dark:border-orange-700/60 dark:bg-orange-950/25',
      badgeClassName:
        'border-orange-400/70 bg-orange-100/85 text-orange-900 dark:border-orange-700 dark:bg-orange-950 dark:text-orange-100',
    }
  }
  if (normalizedRole === 'system') {
    return {
      label: 'system',
      containerClassName: 'border-cyan-300/70 bg-cyan-50/70 dark:border-cyan-700/60 dark:bg-cyan-950/25',
      badgeClassName: 'border-cyan-400/70 bg-cyan-100/80 text-cyan-900 dark:border-cyan-700 dark:bg-cyan-950 dark:text-cyan-100',
    }
  }
  if (normalizedRole === 'user') {
    return {
      label: 'user',
      containerClassName: 'border-emerald-300/70 bg-emerald-50/70 dark:border-emerald-700/60 dark:bg-emerald-950/25',
      badgeClassName:
        'border-emerald-400/70 bg-emerald-100/80 text-emerald-900 dark:border-emerald-700 dark:bg-emerald-950 dark:text-emerald-100',
    }
  }
  if (normalizedRole === 'assistant') {
    return {
      label: 'assistant',
      containerClassName: 'border-amber-300/70 bg-amber-50/70 dark:border-amber-700/60 dark:bg-amber-950/25',
      badgeClassName:
        'border-amber-400/70 bg-amber-100/80 text-amber-900 dark:border-amber-700 dark:bg-amber-950 dark:text-amber-100',
    }
  }
  if (normalizedRole === 'tool') {
    return {
      label: 'tool',
      containerClassName: 'border-violet-300/70 bg-violet-50/70 dark:border-violet-700/60 dark:bg-violet-950/25',
      badgeClassName:
        'border-violet-400/70 bg-violet-100/80 text-violet-900 dark:border-violet-700 dark:bg-violet-950 dark:text-violet-100',
    }
  }

  return {
    label: role || '未知角色',
    containerClassName: 'bg-muted/30',
    badgeClassName: 'bg-background/80',
  }
}

function stringifyStructuredValue(value: unknown): string {
  if (typeof value === 'string') return value
  if (value === null || value === undefined) return ''
  return JSON.stringify(value, null, 2)
}

function parseStructuredPrompt(content: string): StructuredPromptPayload | null {
  if (!content.trim()) return null
  try {
    const payload = JSON.parse(content) as unknown
    if (payload && typeof payload === 'object') return payload as StructuredPromptPayload
  } catch {
    return null
  }
  return null
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value && typeof value === 'object' && !Array.isArray(value))
}

function formatSchemaType(schema: Record<string, unknown>): string {
  const rawType = schema.type
  if (Array.isArray(rawType)) return rawType.map(String).join(' | ')
  if (typeof rawType === 'string') return rawType
  if (isRecord(schema.items)) return `${formatSchemaType(schema.items)}[]`
  if (schema.enum) return 'enum'
  return 'unknown'
}

function formatSchemaValue(value: unknown): string {
  if (value === undefined) return ''
  if (typeof value === 'string') return value
  return JSON.stringify(value)
}

function toStringList(value: unknown): string[] {
  if (!Array.isArray(value)) return []
  return value.map((item) => String(item))
}

function normalizeToolDefinition(toolDefinition: unknown): ToolDefinitionView {
  const toolRecord = isRecord(toolDefinition) ? toolDefinition : {}
  const functionRecord = isRecord(toolRecord.function) ? toolRecord.function : toolRecord
  const parametersRecord = isRecord(functionRecord.parameters) ? functionRecord.parameters : {}
  const propertiesRecord = isRecord(parametersRecord.properties) ? parametersRecord.properties : {}
  const requiredNames = new Set(toStringList(parametersRecord.required))

  const parameters = Object.entries(propertiesRecord).map(([name, rawSchema]) => {
    const schema = isRecord(rawSchema) ? rawSchema : {}
    return {
      name,
      type: formatSchemaType(schema),
      description: typeof schema.description === 'string' ? schema.description : '',
      required: requiredNames.has(name),
      enumValues: toStringList(schema.enum),
      defaultValue: formatSchemaValue(schema.default),
    }
  })

  return {
    name: typeof functionRecord.name === 'string' ? functionRecord.name : '未命名工具',
    type: typeof toolRecord.type === 'string' ? toolRecord.type : 'function',
    description: typeof functionRecord.description === 'string' ? functionRecord.description : '',
    parameters,
    raw: toolDefinition,
  }
}

function normalizeDisplayName(name: string): string {
  return name.trim().toLowerCase()
}

function extractBotSelfNames(prompt: StructuredPromptPayload | null): Set<string> {
  const names = new Set<string>(['麦麦'])

  for (const message of prompt?.messages ?? []) {
    if (String(message.role || '').toLowerCase() !== 'system') continue
    const content = message.content_text || stringifyStructuredValue(message.content)
    const focusMatch = content.match(/你需要关注\s+(.+?)\s+与用户/)
    const nameMatch = content.match(/你的名字是([^，。,.\n]+)/)
    const aliasMatch = content.match(/也有人叫你([^。\n]+)/)

    for (const match of [focusMatch, nameMatch]) {
      const name = match?.[1]?.trim()
      if (name) names.add(name)
    }

    if (aliasMatch?.[1]) {
      aliasMatch[1]
        .split(/[、,，]/)
        .map((alias) => alias.trim())
        .filter(Boolean)
        .forEach((alias) => names.add(alias))
    }
  }

  return new Set(Array.from(names).map(normalizeDisplayName).filter(Boolean))
}

function getFirstMessageTagAttrs(text: string): Record<string, string> {
  const match = text.match(/<message\b([^>]*)>/i)
  return match ? parseMessageTagAttributes(match[1] ?? '') : {}
}

function isBotSelfStructuredMessage(message: StructuredPromptMessage, botSelfNames: Set<string>): boolean {
  if (String(message.role || '').toLowerCase() !== 'user') return false

  const text = message.content_text || stringifyStructuredValue(message.content)
  const user = getFirstMessageTagAttrs(text).user
  return Boolean(user && botSelfNames.has(normalizeDisplayName(user)))
}

function formatSessionType(chatType: string): string {
  if (chatType === 'group') return '群聊'
  if (chatType === 'private') return '私聊'
  return '未知类型'
}

function getSessionDisplayName(
  sessionName: string,
  sessionInfo?: ReasoningPromptSessionInfo,
  fallbackName?: string | null
): string {
  return sessionInfo?.display_name || fallbackName || sessionName
}

function getSessionSubtitle(sessionInfo?: ReasoningPromptSessionInfo): string {
  if (!sessionInfo) return ''

  const parts = []
  if (sessionInfo.platform && sessionInfo.target_id) {
    parts.push(
      `${sessionInfo.platform} · ${formatSessionType(sessionInfo.chat_type)} · ${sessionInfo.target_id}`
    )
  }
  if (sessionInfo.resolved_session_id) {
    parts.push(`会话 ${sessionInfo.resolved_session_id.slice(0, 8)}`)
  } else {
    parts.push('未解析到真实会话')
  }
  return parts.join(' · ')
}

function getReasoningRecordTitle(
  item: ReasoningPromptFile,
  sessionInfo?: ReasoningPromptSessionInfo
): string {
  const platform = item.platform || sessionInfo?.platform || ''
  const chatType = item.chat_type || sessionInfo?.chat_type || ''
  const targetId = item.target_id || sessionInfo?.target_id || ''
  const parts = [
    formatStageName(item.stage),
    getSessionDisplayName(item.session_id, sessionInfo, item.session_display_name),
    item.stem,
  ]

  if (platform && chatType && targetId) {
    parts.push(platform, formatSessionType(chatType), targetId)
  }

  return parts.join('/')
}

function formatPromptPreviewText(previewText: string): string {
  return previewText.replace(/^动作[：:]\s*/, '')
}

function buildAvatarFallbackText(displayName: string, userId: string): string {
  const normalizedName = displayName.trim()
  if (normalizedName) return normalizedName.slice(0, 1).toUpperCase()
  const normalizedUserId = userId.trim()
  return normalizedUserId ? normalizedUserId.slice(-2) : '用'
}

function decodeSimpleHtmlEntity(value: string): string {
  return value
    .replace(/&quot;/g, '"')
    .replace(/&apos;/g, "'")
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&amp;/g, '&')
}

function parseMessageTagAttributes(rawAttributes: string): Record<string, string> {
  const attrs: Record<string, string> = {}
  const attributePattern = /([A-Za-z_][\w:-]*)\s*=\s*"([^"]*)"/g
  for (const match of rawAttributes.matchAll(attributePattern)) {
    attrs[match[1]] = decodeSimpleHtmlEntity(match[2])
  }
  return attrs
}

function parseNaturalTextBlocks(text: string): ParsedNaturalTextBlock[] {
  const messageTagPattern = /<message\b([^>]*)>/gi
  const matches = Array.from(text.matchAll(messageTagPattern))
  if (matches.length === 0) {
    return [{ type: 'text', text }]
  }

  const blocks: ParsedNaturalTextBlock[] = []
  let cursor = 0
  matches.forEach((match, index) => {
    const start = match.index ?? 0
    if (start > cursor) {
      blocks.push({ type: 'text', text: text.slice(cursor, start) })
    }

    const bodyStart = start + match[0].length
    const nextStart = matches[index + 1]?.index ?? text.length
    const body = text.slice(bodyStart, nextStart).replace(/<\/message>\s*$/i, '').trim()
    blocks.push({
      type: 'message',
      attrs: parseMessageTagAttributes(match[1] ?? ''),
      body,
    })
    cursor = nextStart
  })

  if (cursor < text.length) {
    blocks.push({ type: 'text', text: text.slice(cursor) })
  }

  return blocks.filter((block) => (block.type === 'message' ? block.body || Object.keys(block.attrs).length > 0 : block.text.trim()))
}

function renderMessageTagMeta(attrs: Record<string, string>, avatarMap: ReasoningPromptMessageAvatarMap) {
  const user = attrs.user || ''
  const time = attrs.time || ''
  const msgId = attrs.msg_id || ''
  const chatId = attrs.chat_id || ''
  const avatar = msgId ? avatarMap[msgId] : undefined
  const avatarLabel = avatar?.display_name || user || avatar?.user_id || '用户'

  return (
    <div className="text-muted-foreground mb-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs">
      {avatar && (
        <Avatar className="h-6 w-6 shrink-0 border bg-background">
          {avatar.avatar_url && <AvatarImage src={avatar.avatar_url} alt={`${avatarLabel} 的头像`} />}
          <AvatarFallback className="text-[10px]">
            {buildAvatarFallbackText(avatarLabel, avatar.user_id)}
          </AvatarFallback>
        </Avatar>
      )}
      {user && (
        <Badge variant="outline" className="px-1.5 py-0 text-[11px]">
          {user}
        </Badge>
      )}
      {time && <span>{time}</span>}
      {msgId && (
        <span className="max-w-full truncate" title={msgId}>
          msg {msgId}
        </span>
      )}
      {chatId && (
        <span className="max-w-full truncate" title={chatId}>
          chat {chatId}
        </span>
      )}
    </div>
  )
}

function NaturalLanguageText({
  text,
  avatarMap = {},
}: {
  text: string
  avatarMap?: ReasoningPromptMessageAvatarMap
}) {
  const blocks = parseNaturalTextBlocks(text)
  const baseClassName = 'text-foreground text-sm leading-6 whitespace-pre-wrap'
  if (blocks.length === 1 && blocks[0].type === 'text') {
    return (
      <pre className={baseClassName} style={NATURAL_LANGUAGE_TEXT_STYLE}>
        {blocks[0].text}
      </pre>
    )
  }

  return (
    <div className="space-y-2" style={NATURAL_LANGUAGE_TEXT_STYLE}>
      {blocks.map((block, index) => {
        if (block.type === 'text') {
          return (
            <pre key={`text-${index}`} className={baseClassName}>
              {block.text.trim()}
            </pre>
          )
        }

        return (
          <div key={`message-${index}`} className="border-primary/60 pl-2 border-l-2">
            {renderMessageTagMeta(block.attrs, avatarMap)}
            <pre className={baseClassName}>{block.body || '空消息'}</pre>
          </div>
        )
      })}
    </div>
  )
}

function ToolCallsCollapsible({ toolCalls }: { toolCalls: unknown[] }) {
  return (
    <Collapsible className="bg-background/60 mt-2 rounded-md border sm:mt-3">
      <CollapsibleTrigger asChild>
        <button
          type="button"
          className="hover:bg-muted/50 flex w-full items-center justify-between gap-2 px-2.5 py-2 text-left text-sm transition-colors sm:px-3 [&[data-state=open]>svg]:rotate-180"
        >
          <span className="font-medium">工具调用 · {toolCalls.length} 个</span>
          <ChevronDown className="h-4 w-4 shrink-0 transition-transform" />
        </button>
      </CollapsibleTrigger>
      <CollapsibleContent className="border-t">
        <pre className="p-2.5 font-mono text-base leading-7 font-semibold whitespace-pre-wrap sm:p-3">
          {JSON.stringify(toolCalls, null, 2)}
        </pre>
      </CollapsibleContent>
    </Collapsible>
  )
}

function ToolDefinitionsCollapsible({ toolDefinitions }: { toolDefinitions: unknown[] }) {
  const tools = toolDefinitions.map(normalizeToolDefinition)

  return (
    <Collapsible className="rounded-md border">
      <CollapsibleTrigger asChild>
        <button
          type="button"
          className="hover:bg-muted/50 flex w-full items-center justify-between gap-2 px-2.5 py-2 text-left text-sm transition-colors sm:px-3 [&[data-state=open]>svg]:rotate-180"
        >
          <span className="font-medium">工具定义 · {tools.length} 个</span>
          <ChevronDown className="h-4 w-4 shrink-0 transition-transform" />
        </button>
      </CollapsibleTrigger>
      <CollapsibleContent className="border-t">
        <div className="space-y-2 p-2 sm:p-3">
          {tools.map((tool, index) => (
            <div key={`${tool.name}-${index}`} className="bg-background/60 rounded-md border p-2.5 sm:p-3">
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant="secondary" className="font-mono">
                  {tool.name}
                </Badge>
                <span className="text-muted-foreground text-xs">{tool.type}</span>
              </div>
              {tool.description && (
                <p className="text-foreground mt-2 text-sm leading-6">{tool.description}</p>
              )}

              {tool.parameters.length > 0 ? (
                <div className="mt-3 space-y-2">
                  <div className="text-muted-foreground text-xs font-medium">参数</div>
                  <div className="space-y-1.5">
                    {tool.parameters.map((parameter) => (
                      <div key={parameter.name} className="rounded-md border px-2.5 py-2">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="font-mono text-sm font-semibold">{parameter.name}</span>
                          <Badge variant="outline" className="px-1.5 py-0 font-mono text-[11px]">
                            {parameter.type}
                          </Badge>
                          {parameter.required && (
                            <Badge
                              variant="outline"
                              className="border-destructive/50 px-1.5 py-0 text-[11px] text-destructive"
                            >
                              必填
                            </Badge>
                          )}
                        </div>
                        {parameter.description && (
                          <p className="text-muted-foreground mt-1 text-xs leading-5">
                            {parameter.description}
                          </p>
                        )}
                        {(parameter.enumValues.length > 0 || parameter.defaultValue) && (
                          <div className="text-muted-foreground mt-1 flex flex-wrap gap-x-3 gap-y-1 text-xs">
                            {parameter.enumValues.length > 0 && (
                              <span>可选值：{parameter.enumValues.join('、')}</span>
                            )}
                            {parameter.defaultValue && <span>默认：{parameter.defaultValue}</span>}
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              ) : (
                <div className="text-muted-foreground mt-3 text-xs">无参数</div>
              )}

              <Collapsible className="mt-3 rounded-md border">
                <CollapsibleTrigger asChild>
                  <button
                    type="button"
                    className="hover:bg-muted/50 flex w-full items-center justify-between gap-2 px-2.5 py-1.5 text-left text-xs transition-colors [&[data-state=open]>svg]:rotate-180"
                  >
                    <span>原始定义</span>
                    <ChevronDown className="h-3.5 w-3.5 shrink-0 transition-transform" />
                  </button>
                </CollapsibleTrigger>
                <CollapsibleContent className="border-t">
                  <pre className="p-2.5 font-mono text-xs leading-5 whitespace-pre-wrap">
                    {JSON.stringify(tool.raw, null, 2)}
                  </pre>
                </CollapsibleContent>
              </Collapsible>
            </div>
          ))}
        </div>
      </CollapsibleContent>
    </Collapsible>
  )
}

interface ReasoningProcessPageProps {
  embedded?: boolean
  toolbarContainerId?: string
  toolbarVisible?: boolean
}

export function ReasoningProcessPage({
  embedded = false,
  toolbarContainerId,
  toolbarVisible = true,
}: ReasoningProcessPageProps) {
  const { toast } = useToast()
  const [items, setItems] = useState<ReasoningPromptFile[]>([])
  const [stages, setStages] = useState<string[]>([])
  const [stageInfos, setStageInfos] = useState<ReasoningPromptStageInfo[]>([])
  const [sessions, setSessions] = useState<string[]>([])
  const [sessionInfos, setSessionInfos] = useState<ReasoningPromptSessionInfo[]>([])
  const [stage, setStage] = useState('planner')
  const [session, setSession] = useState(AUTO_SESSION)
  const [actionFilter, setActionFilter] = useState('')
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(1)
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
  const [error, setError] = useState<string | null>(null)
  const [browsingStage, setBrowsingStage] = useState(false)
  const [toolbarRoot, setToolbarRoot] = useState<HTMLElement | null>(null)

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const stageCards = useMemo(() => {
    if (stageInfos.length > 0) return stageInfos
    return stages.map((name) => ({ name, session_count: 0, latest_modified_at: 0 }))
  }, [stageInfos, stages])
  const primaryStageCards = useMemo(() => {
    const stageInfoByName = new Map(stageCards.map((item) => [item.name, item]))
    return PRIMARY_STAGE_NAMES.flatMap((name) => {
      const item = stageInfoByName.get(name)
      return item ? [item] : []
    })
  }, [stageCards])
  const secondaryStageCards = useMemo(() => {
    return stageCards.filter((item) => !PRIMARY_STAGE_NAMES.includes(item.name))
  }, [stageCards])
  const sessionInfoByName = useMemo(() => {
    return new Map(sessionInfos.map((item) => [item.name, item]))
  }, [sessionInfos])
  const structuredPrompt = useMemo(() => parseStructuredPrompt(jsonContent), [jsonContent])
  const avatarFetchEnabled = useAvatarFetchEnabled()

  useEffect(() => {
    setToolbarRoot(toolbarContainerId ? document.getElementById(toolbarContainerId) : null)
  }, [toolbarContainerId])

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
          page,
          pageSize: PAGE_SIZE,
        })
        if (ignore) return
        setItems(data.items)
        setStages(data.stages)
        setStageInfos(data.stage_infos ?? [])
        setSessions(data.sessions)
        setSessionInfos(data.session_infos ?? [])
        if (data.selected_session && data.selected_session !== session) {
          setSession(data.selected_session)
        }
        setTotal(data.total)
        setSelected((current) => {
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
  }, [actionFilter, browsingStage, page, refreshKey, search, session, stage])

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

      if (!selected?.json_path) {
        setJsonContent('')
        setMessageAvatarMap({})
        return
      }

      setJsonContent('')
      setMessageAvatarMap({})
      setContentLoading(true)
      try {
        const data = await getReasoningPromptFile(selected.json_path)
        const avatarEntries = avatarFetchEnabled
          ? await Promise.all(
              Object.entries(data.message_avatars ?? {}).map(async ([messageId, avatar]) => [
                messageId,
                {
                  ...avatar,
                  avatar_url: avatar.avatar_url ? await resolveApiPath(avatar.avatar_url) : null,
                },
              ] as const)
            )
          : []
        if (!ignore) {
          setJsonContent(data.content)
          setMessageAvatarMap(Object.fromEntries(avatarEntries))
        }
      } catch (err) {
        if (!ignore) {
          setJsonContent('')
          setMessageAvatarMap({})
          setTextContent((current) => current || (err instanceof Error ? err.message : '读取结构化内容失败'))
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

  async function handleCopyPrompt() {
    const copyContent = textContent || structuredPrompt?.text_dump || jsonContent
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

  const selectedSessionInfo = selected ? sessionInfoByName.get(selected.session_id) : undefined
  const selectedTitle = selected ? getReasoningRecordTitle(selected, selectedSessionInfo) : '未选择记录'
  const botSelfNames = useMemo(() => extractBotSelfNames(structuredPrompt), [structuredPrompt])
  const renderRefreshButton = () => (
    <Button
      variant="outline"
      size="sm"
      aria-label="刷新"
      title="刷新"
      onClick={() => setRefreshKey((current) => current + 1)}
      disabled={loading}
      className="h-9 w-9 shrink-0 p-0 sm:h-10 sm:w-10"
    >
      <RefreshCw className={cn('h-4 w-4', loading && 'animate-spin')} />
    </Button>
  )
  const renderBrowsingControls = (inToolbar = false) => (
    <>
      <Button
        variant="outline"
        size="sm"
        className="h-9 shrink-0 justify-start sm:h-10"
        onClick={() => setBrowsingStage(false)}
      >
        <ArrowLeft className="h-4 w-4" />
        类型
      </Button>

      <Select
        value={session}
        onValueChange={(value) => resetToFirstPage(() => setSession(value))}
        disabled={sessions.length === 0 && loading}
      >
        <SelectTrigger className={cn('h-9 sm:h-10', inToolbar ? 'w-full sm:w-[240px]' : undefined)}>
          <SelectValue placeholder="会话" />
        </SelectTrigger>
        <SelectContent>
          {session === AUTO_SESSION && (
            <SelectItem value={AUTO_SESSION}>自动选择最近会话</SelectItem>
          )}
          <SelectItem value={ALL_GROUP_SESSIONS}>全部群聊</SelectItem>
          {sessions.map((item) => {
            const sessionInfo = sessionInfoByName.get(item)
            return (
              <SelectItem key={item} value={item}>
                <div className="min-w-0">
                  <div className="truncate">{getSessionDisplayName(item, sessionInfo)}</div>
                  {sessionInfo && (
                    <div className="text-muted-foreground truncate text-xs">
                      {getSessionSubtitle(sessionInfo)}
                    </div>
                  )}
                </div>
              </SelectItem>
            )
          })}
        </SelectContent>
      </Select>

      <div className={cn('relative', inToolbar ? 'w-full sm:w-[140px]' : undefined)}>
        <Input
          value={actionFilter}
          onChange={(event) => resetToFirstPage(() => setActionFilter(event.target.value))}
          className="h-9 sm:h-10"
          placeholder="动作过滤"
        />
      </div>

      <div
        className={cn(
          'relative',
          inToolbar ? 'min-w-0 flex-[1_1_220px] sm:min-w-[260px] sm:max-w-[520px]' : undefined
        )}
      >
        <Search className="text-muted-foreground pointer-events-none absolute top-1/2 left-3 h-4 w-4 -translate-y-1/2" />
        <Input
          value={search}
          onChange={(event) => resetToFirstPage(() => setSearch(event.target.value))}
          className="h-9 pl-9 sm:h-10"
          placeholder="搜索会话显示名、真实会话、文件名或 replyer 回复内容"
        />
      </div>
    </>
  )
  const toolbarContent = (
    <div className="flex w-full min-w-0 flex-wrap items-center justify-start gap-2 sm:justify-end">
      {browsingStage && renderBrowsingControls(true)}
      {renderRefreshButton()}
    </div>
  )
  const toolbarPortal = embedded && toolbarVisible && toolbarRoot ? createPortal(toolbarContent, toolbarRoot) : null
  const showBrowsingControlsInline = browsingStage && (!embedded || !toolbarVisible || !toolbarRoot)
  const renderStageCard = (item: ReasoningPromptStageInfo, compact = false) => (
    <button
      key={item.name}
      type="button"
      onClick={() => enterStage(item.name)}
      className={cn(
        'flex flex-col justify-between rounded-md border text-left transition-colors',
        'hover:border-primary hover:bg-primary/10 focus-visible:ring-ring focus-visible:ring-2 focus-visible:outline-none',
        compact ? 'min-h-16 p-2.5 sm:min-h-20 sm:p-3' : 'min-h-24 p-3 sm:min-h-32 sm:p-4'
      )}
    >
      <div className={compact ? 'space-y-1.5' : 'space-y-2'}>
        <div
          className={cn(
            'text-primary font-extrabold tracking-normal uppercase',
            compact ? 'text-sm sm:text-base' : 'text-base sm:text-lg'
          )}
        >
          {item.name}
        </div>
        <div className={cn('text-foreground font-semibold', compact ? 'text-sm' : 'text-base')}>
          {formatStageName(item.name)}
        </div>
      </div>
      <div className={cn('text-muted-foreground text-xs', compact ? 'mt-2' : 'mt-4')}>
        {item.session_count} 个会话
        {item.latest_modified_at > 0 ? ` · 最新 ${formatTime(null, item.latest_modified_at)}` : ''}
      </div>
    </button>
  )

  return (
    <div className={cn('flex h-full min-h-0 flex-col gap-2 overflow-hidden sm:gap-3', embedded ? 'p-0' : 'p-2 lg:p-4')}>
      {toolbarPortal}

      {!embedded && (
        <div className="flex flex-shrink-0 items-start justify-between gap-3">
          <div>
            <h1 className="text-foreground text-xl font-semibold tracking-normal">推理过程</h1>
            <p className="text-muted-foreground text-sm">浏览 logs/maisaka_prompt 下的 prompt 记录</p>
          </div>
          {renderRefreshButton()}
        </div>
      )}

      {showBrowsingControlsInline && (
        <div className="grid flex-shrink-0 grid-cols-[auto_minmax(0,1fr)] gap-2 [&>div:last-child]:col-span-2 sm:grid-cols-[auto_minmax(220px,320px)_1fr] sm:[&>div:last-child]:col-span-1">
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
          <div className="flex h-10 flex-shrink-0 items-center gap-2 border-b px-3 sm:h-12 sm:px-4">
            <Layers className="text-muted-foreground h-4 w-4" />
            <div className="text-sm font-medium">选择推理类型</div>
          </div>
          <ScrollArea className="min-h-0 flex-1">
            <div className="space-y-3 p-2 sm:space-y-4 sm:p-3">
              {primaryStageCards.length > 0 && (
                <div className="grid grid-cols-3 gap-1.5 sm:gap-2 lg:gap-3">
                  {primaryStageCards.map((item) => renderStageCard(item, true))}
                </div>
              )}
              {secondaryStageCards.length > 0 && (
                <div className="border-t pt-3">
                  <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
                    {secondaryStageCards.map((item) => renderStageCard(item, true))}
                  </div>
                </div>
              )}
              {!loading && stageCards.length === 0 && (
                <div className="text-muted-foreground px-3 py-10 text-center text-sm">
                  没有找到推理过程类型
                </div>
              )}
            </div>
          </ScrollArea>
        </div>
      ) : (
        <div className="grid min-h-0 flex-1 grid-cols-1 gap-2 lg:grid-cols-[280px_1fr] lg:gap-3">
          <div className="bg-background flex h-[32vh] min-h-[180px] flex-col overflow-hidden rounded-md border lg:h-auto lg:min-h-0">
            <div className="text-muted-foreground flex h-10 flex-shrink-0 items-center justify-between border-b px-3 text-sm lg:h-11">
              <span>{total} 条记录</span>
              <span>
                第 {page} / {totalPages} 页
              </span>
            </div>
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
                    item.stage === 'replyer' ? item.output_preview : item.action_preview
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
            <Tabs
              value={activePreview}
              onValueChange={(value) => setActivePreview(value as 'structured' | 'text' | 'html')}
              className="min-h-0 flex-1"
            >
              <ScrollArea className="h-full">
                <div className="min-h-full">
                  <div className="flex min-h-12 flex-col gap-2 border-b px-3 py-2 sm:min-h-14 sm:px-4 sm:py-3 xl:flex-row xl:items-center xl:justify-between">
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-sm font-medium">
                        {selectedTitle}
                      </div>
                      {!selected && (
                        <div className="text-muted-foreground truncate text-xs">
                          从左侧列表选择一条记录
                        </div>
                      )}
                    </div>
                    {selected && (
                      <div className="text-muted-foreground flex min-w-0 flex-wrap items-center gap-2 text-xs">
                        <TabsList className="h-8 rounded-md">
                          <TabsTrigger
                            value="structured"
                            disabled={!selected?.json_path}
                            className="h-6 gap-1 px-2 text-xs"
                          >
                            <FileJson className="h-3.5 w-3.5" />
                            结构化
                          </TabsTrigger>
                          <TabsTrigger
                            value="text"
                            disabled={!selected?.text_path}
                            className="h-6 gap-1 px-2 text-xs"
                          >
                            <FileText className="h-3.5 w-3.5" />
                            文本
                          </TabsTrigger>
                          <TabsTrigger
                            value="html"
                            disabled={!selected?.html_path}
                            className="h-6 gap-1 px-2 text-xs"
                          >
                            <Code2 className="h-3.5 w-3.5" />
                            HTML
                          </TabsTrigger>
                        </TabsList>
                        <Button
                          variant="outline"
                          size="sm"
                          className="h-8 gap-1.5"
                          onClick={handleCopyPrompt}
                          disabled={
                            contentLoading ||
                            !(textContent || structuredPrompt?.text_dump || jsonContent)
                          }
                          title="复制完整 Prompt"
                        >
                          <Copy className="h-3.5 w-3.5" />
                          复制
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
                        {structuredPrompt.request?.selection_reason && (
                          <div className="rounded-md border p-2.5 sm:p-3">
                            <NaturalLanguageText
                              text={structuredPrompt.request.selection_reason}
                              avatarMap={messageAvatarMap}
                            />
                          </div>
                        )}

                        {structuredPrompt.output && (
                          <div className="rounded-md border p-2.5 sm:p-3">
                            <Badge variant="secondary" className="mb-2">
                              {structuredPrompt.output.title || '输出结果'}
                            </Badge>
                            <NaturalLanguageText
                              text={
                                structuredPrompt.output.content_text ||
                                stringifyStructuredValue(structuredPrompt.output.content) ||
                                '空输出'
                              }
                              avatarMap={messageAvatarMap}
                            />
                            {structuredPrompt.output.tool_calls &&
                              structuredPrompt.output.tool_calls.length > 0 && (
                                <ToolCallsCollapsible toolCalls={structuredPrompt.output.tool_calls} />
                              )}
                          </div>
                        )}

                        <div className="space-y-2">
                          {(structuredPrompt.messages ?? []).map((message, index) => {
                            const isBotSelfMessage = isBotSelfStructuredMessage(message, botSelfNames)
                            const roleStyle = getStructuredPromptMessageRoleStyle(
                              message.role,
                              isBotSelfMessage
                            )
                            return (
                              <div
                                key={`${message.index ?? index}-${message.role ?? 'unknown'}`}
                                className={cn('relative rounded-md border px-2.5 pt-9 pb-2.5 sm:px-3 sm:pt-10 sm:pb-3', roleStyle.containerClassName)}
                              >
                                <div className="absolute top-1.5 left-1.5 flex flex-wrap items-center gap-1.5 sm:top-2 sm:left-2">
                                  <Badge variant="outline" className="px-1.5 py-0 text-[11px]">
                                    #{message.index ?? index + 1}
                                  </Badge>
                                  <Badge
                                    variant="outline"
                                    className={cn('px-1.5 py-0 text-[11px]', roleStyle.badgeClassName)}
                                  >
                                    {roleStyle.label}
                                  </Badge>
                                  {message.tool_call_id && (
                                    <span className="text-muted-foreground text-xs">
                                      tool_call_id: {message.tool_call_id}
                                    </span>
                                  )}
                                </div>
                                <NaturalLanguageText
                                  text={
                                    message.content_text ||
                                    stringifyStructuredValue(message.content) ||
                                    '空内容'
                                  }
                                  avatarMap={messageAvatarMap}
                                />
                                {message.tool_calls && message.tool_calls.length > 0 && (
                                  <ToolCallsCollapsible toolCalls={message.tool_calls} />
                                )}
                              </div>
                            )
                          })}
                        </div>

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
            </Tabs>
          </div>
        </div>
      )}
    </div>
  )
}
