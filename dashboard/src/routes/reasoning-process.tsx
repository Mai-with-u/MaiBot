import { useNavigate } from '@tanstack/react-router'
import { type CSSProperties, useEffect, useMemo, useState } from 'react'
import { createPortal } from 'react-dom'
import {
  ArrowLeft,
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
  X,
} from 'lucide-react'

import { Avatar, AvatarFallback, AvatarImage } from '@/components/ui/avatar'
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Textarea } from '@/components/ui/textarea'
import { useToast } from '@/hooks/use-toast'
import { resolveApiPath } from '@/lib/api-base'
import { useAvatarFetchEnabled } from '@/lib/avatar-url'
import {
  getReasoningPromptFile,
  getReasoningPromptHtmlUrl,
  listReasoningPromptFiles,
  listReasoningPromptStages,
  replayReasoningPrompt,
  clearReasoningPromptStage,
  type ReasoningPromptFile,
  type ReasoningPromptMessageAvatar,
  type ReasoningReplayResponse,
  type ReasoningPromptSessionInfo,
  type ReasoningPromptStageInfo,
} from '@/lib/reasoning-process-api'
import { cn } from '@/lib/utils'

const PAGE_SIZE = 50
const AUTO_SESSION = 'auto'
const ALL_GROUP_SESSIONS = '__all_group_chats__'
const CORE_STAGE_NAMES = ['planner', 'replyer']
const REMOVED_STAGE_NAMES = ['timing_gate']
const NATURAL_LANGUAGE_TEXT_STYLE: CSSProperties = {
  fontFamily:
    "'Microsoft YaHei UI', 'Microsoft YaHei', 'PingFang SC', 'Noto Sans SC', system-ui, sans-serif",
}
const STAGE_LABELS: Record<string, string> = {
  behavior_consolidator: '行为整合',
  behavior_feedback: '行为反馈',
  behavior_learner: '行为学习',
  behavior_scenario_analyzer: '行为场景分析',
  behavior_selector: '行为选择',
  emotion: '表情包发送',
  expression_learner: '表达学习',
  expression_selection: '表达选择',
  expression_selector: '表达选择',
  jargon_learner: '黑话抽取',
  jargon_learning_update: '黑话含义推断',
  planner: '规划器',
  reply_effect_judge: '回复效果评估',
  replyer: '回复器',
  timing_gate: '时机判断',
}

type StageCategoryRow = {
  key: string
  label: string
  items: ReasoningPromptStageInfo[]
  collapsedByDefault?: boolean
}

type StructuredPromptMessage = {
  index?: number
  role?: string
  content?: unknown
  tool_call_id?: string
  tool_calls?: unknown[]
}

type StructuredPromptOutput = {
  title?: string
  content?: unknown
  tool_calls?: unknown[]
}

type StructuredPromptLlmCall = {
  inference_stage: string
  request?: {
    kind?: string
    selection_reason?: string
  }
  metadata?: {
    model_name?: string
    duration_ms?: number
  }
  messages?: StructuredPromptMessage[]
  output?: StructuredPromptOutput | null
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
  output?: StructuredPromptOutput | null
  tool_definitions?: unknown[]
  jargon_learning_calls?: StructuredPromptLlmCall[]
}

function getInitialSearchParams(): URLSearchParams {
  if (typeof window === 'undefined') return new URLSearchParams()
  return new URLSearchParams(window.location.search)
}

function getSafeInternalReturnTo(value: string | null): string {
  const normalized = value?.trim() ?? ''
  if (!normalized || !normalized.startsWith('/') || normalized.startsWith('//') || typeof window === 'undefined') {
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

type ReasoningHeaderMeta = {
  sessionId: string
  callId: string
  remainingText: string
}

function formatStageName(stage: string): string {
  return STAGE_LABELS[stage] ?? stage
}

function isLearnerStage(stage: string): boolean {
  return stage.includes('learner') || stage.includes('learning')
}

function buildStageCategoryRows(stageCards: ReasoningPromptStageInfo[]): StageCategoryRow[] {
  const stageInfoByName = new Map(stageCards.map((item) => [item.name, item]))
  const usedStageNames = new Set<string>()
  const takeNamedStages = (stageNames: string[]) => stageNames.flatMap((stageName) => {
    const item = stageInfoByName.get(stageName)
    if (!item) return []
    usedStageNames.add(stageName)
    return [item]
  })
  const takeMatchingStages = (predicate: (stage: string) => boolean) => stageCards.filter((item) => {
    if (usedStageNames.has(item.name) || !predicate(item.name)) return false
    usedStageNames.add(item.name)
    return true
  })

  const coreStages = takeNamedStages(CORE_STAGE_NAMES)
  const learnerStages = takeMatchingStages(isLearnerStage)
  const removedStages = takeNamedStages(REMOVED_STAGE_NAMES)
  const otherStages = takeMatchingStages(() => true)

  return [
    { key: 'core', label: '主流程', items: coreStages },
    { key: 'learners', label: '学习器', items: learnerStages },
    { key: 'others', label: '其余', items: otherStages },
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

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value && typeof value === 'object' && !Array.isArray(value))
}

function stringifyStructuredValue(value: unknown): string {
  if (typeof value === 'string') return value
  if (value === null || value === undefined) return ''
  return JSON.stringify(value, null, 2)
}

function stringifyPromptContent(value: unknown): string {
  if (typeof value === 'string') return value
  if (value === null || value === undefined) return ''
  if (!Array.isArray(value)) return stringifyStructuredValue(value)

  return value
    .map((item) => {
      if (typeof item === 'string') return item
      if (isRecord(item) && item.type === 'text' && typeof item.text === 'string') return item.text
      if (isRecord(item)) {
        const partType = String(item.type || '').trim().toLowerCase()
        if (['image', 'image_url', 'input_image'].includes(partType)) {
          const imageFormat = String(item.image_format || item.format || 'unknown').trim() || 'unknown'
          const sizeText = typeof item.size_bytes === 'number' ? ` ${item.size_bytes} B` : ''
          return `[图片 image/${imageFormat}${sizeText}]`
        }
      }
      return stringifyStructuredValue(item)
    })
    .filter(Boolean)
    .join('\n')
    .trim()
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

function formatAnonymousUserName(index: number): string {
  let value = index
  let suffix = ''
  do {
    suffix = String.fromCharCode(65 + (value % 26)) + suffix
    value = Math.floor(value / 26) - 1
  } while (value >= 0)
  return `用户${suffix}`
}

function getAnonymousUserName(rawName: unknown, nameMap: Map<string, string>, preferredName?: string): string {
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

  const userName = typeof value.user === 'string' ? getAnonymousUserName(value.user, nameMap) : undefined
  for (const [key, item] of Object.entries(value)) {
    if (typeof item === 'string') {
      if (key === 'user_name' || key === 'display_name' || key === 'session_display_name' || key === 'user') {
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
    .reduce((current, [name, anonymousName]) => current.replace(new RegExp(escapeRegExp(name), 'g'), anonymousName), text)
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
  return value
    .trim()
    .replace(/[\\/:*?"<>|]+/g, '_')
    .replace(/\s+/g, '_')
    .slice(0, 120) || 'reasoning-process'
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

function extractJargonInferenceStage(payload: StructuredPromptPayload, fallbackIndex: number): string {
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
    messages: payload.messages ?? [],
    output: payload.output ?? null,
  }))

  return {
    schema_version: 3,
    request: {
      kind: 'jargon_learning_update',
      selection_reason: `词条: ${displayTitle || '未知黑话'}\n包含 ${payloads.length} 次黑话含义推断调用。`,
    },
    metadata: payloads[0]?.metadata ?? {},
    messages: [],
    output: null,
    tool_definitions: [],
    jargon_learning_calls: jargonLearningCalls,
  }
}

function buildStructuredPromptCopyText(payload: StructuredPromptPayload | null): string {
  if (!payload) return ''

  const sections: string[] = []
  const metadataLines: string[] = []
  if (payload.request?.kind) metadataLines.push(`请求类型：${payload.request.kind}`)
  if (payload.request?.selection_reason) metadataLines.push(`选择原因：${payload.request.selection_reason}`)
  if (payload.metadata?.model_name) metadataLines.push(`模型：${payload.metadata.model_name}`)
  if (typeof payload.metadata?.duration_ms === 'number') metadataLines.push(`耗时：${payload.metadata.duration_ms} ms`)
  if (metadataLines.length > 0) sections.push(`[元信息]\n${metadataLines.join('\n')}`)

  if (payload.output) {
    const outputText = stringifyPromptContent(payload.output.content)
    const toolCallsText = payload.output.tool_calls?.length
      ? `\n\n[工具调用]\n${stringifyStructuredValue(payload.output.tool_calls)}`
      : ''
    if (outputText || toolCallsText) {
      sections.push(`[${payload.output.title || '输出结果'}]\n${outputText}${toolCallsText}`)
    }
  }

  const messageSections = (payload.messages ?? []).map((message, index) => {
    const role = message.role || 'unknown'
    const content = stringifyPromptContent(message.content)
    const toolCallId = message.tool_call_id ? `\ntool_call_id: ${message.tool_call_id}` : ''
    const toolCalls = message.tool_calls?.length
      ? `\ntool_calls:\n${stringifyStructuredValue(message.tool_calls)}`
      : ''
    return `#${message.index ?? index + 1} ${role}${toolCallId}${toolCalls}\n${content}`
  })
  if (messageSections.length > 0) sections.push(`[Prompt 消息]\n${messageSections.join('\n\n')}`)

  if (payload.tool_definitions?.length) {
    sections.push(`[工具定义]\n${stringifyStructuredValue(payload.tool_definitions)}`)
  }

  return sections.join(`\n\n${'='.repeat(80)}\n\n`)
}

type ToolCallDisplayItem = {
  id: string
  name: string
  arguments: unknown
  source: string
  sourceLabel: string
}

function normalizeToolCallForDisplay(toolCall: unknown): ToolCallDisplayItem {
  const toolRecord = isRecord(toolCall) ? toolCall : {}
  const functionRecord = isRecord(toolRecord.function) ? toolRecord.function : {}
  const extraContent = isRecord(toolRecord.extra_content) ? toolRecord.extra_content : {}
  const rawSource = String(toolRecord.source || toolRecord.tool_call_source || extraContent.tool_call_source || '').trim()
  const normalizedSource = rawSource.toLowerCase()
  const sourceLabel = normalizedSource === 'reasoning'
    ? '推理中调用'
    : normalizedSource === 'response'
      ? '正文调用'
      : String(toolRecord.source_label || toolRecord.tool_call_source_label || '').trim()
  return {
    id: String(toolRecord.id || toolRecord.call_id || ''),
    name: String(functionRecord.name || toolRecord.name || toolRecord.func_name || 'unknown'),
    arguments: functionRecord.arguments ?? toolRecord.arguments ?? toolRecord.args ?? {},
    source: normalizedSource,
    sourceLabel,
  }
}

function getToolCallSourceClassName(source: string): string {
  if (source === 'reasoning') {
    return 'border-teal-500/45 bg-teal-500/10 text-teal-700 dark:text-teal-300'
  }
  if (source === 'response') {
    return 'border-amber-500/45 bg-amber-500/10 text-amber-700 dark:text-amber-300'
  }
  return 'border-muted-foreground/30 bg-muted/40 text-muted-foreground'
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
    const content = stringifyPromptContent(message.content)
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

  const text = stringifyPromptContent(message.content)
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
  if (sessionInfo.platform) {
    parts.push(`${sessionInfo.platform} · ${formatSessionType(sessionInfo.chat_type)}`)
  }
  if (sessionInfo.resolved_session_id) {
    parts.push(`会话 ${sessionInfo.resolved_session_id.slice(0, 8)}`)
  } else {
    parts.push('未解析到真实会话')
  }
  return parts.join(' · ')
}

function extractReasoningHeaderMeta(text?: string): ReasoningHeaderMeta {
  const meta: ReasoningHeaderMeta = {
    sessionId: '',
    callId: '',
    remainingText: '',
  }
  if (!text) return meta

  const remainingLines: string[] = []
  for (const line of text.split(/\r?\n/)) {
    const normalizedLine = line.trim()
    const sessionMatch = normalizedLine.match(/^会话\s*ID[：:]\s*(.+)$/i)
    if (sessionMatch) {
      meta.sessionId = sessionMatch[1].trim()
      continue
    }

    const callMatch = normalizedLine.match(/^调用\s*ID[：:]\s*(.+)$/i)
    if (callMatch) {
      meta.callId = callMatch[1].trim()
      continue
    }

    remainingLines.push(line)
  }

  meta.remainingText = remainingLines.join('\n').trim()
  return meta
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
    item.display_title || item.stem,
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
  const displayToolCalls = toolCalls.map(normalizeToolCallForDisplay)

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
        <div className="space-y-2 p-2.5 sm:p-3">
          {displayToolCalls.map((toolCall, index) => (
            <div key={`${toolCall.id || toolCall.name}-${index}`} className="rounded-md border bg-background/70 p-2.5">
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant="secondary" className="font-mono">
                  {toolCall.name}
                </Badge>
                {toolCall.sourceLabel && (
                  <Badge
                    variant="outline"
                    className={cn('px-1.5 py-0 text-[11px]', getToolCallSourceClassName(toolCall.source))}
                  >
                    {toolCall.sourceLabel}
                  </Badge>
                )}
                {toolCall.id && (
                  <span className="text-muted-foreground font-mono text-[11px]">
                    {toolCall.id}
                  </span>
                )}
              </div>
              <pre className="mt-2 rounded-md border bg-muted/20 p-2 font-mono text-xs leading-5 whitespace-pre-wrap">
                {stringifyStructuredValue(toolCall.arguments)}
              </pre>
            </div>
          ))}
          <Collapsible className="rounded-md border">
            <CollapsibleTrigger asChild>
              <button
                type="button"
                className="hover:bg-muted/50 flex w-full items-center justify-between gap-2 px-2.5 py-1.5 text-left text-xs transition-colors [&[data-state=open]>svg]:rotate-180"
              >
                <span>完整工具调用 JSON</span>
                <ChevronDown className="h-3.5 w-3.5 shrink-0 transition-transform" />
              </button>
            </CollapsibleTrigger>
            <CollapsibleContent className="border-t">
              <pre className="p-2.5 font-mono text-xs leading-5 whitespace-pre-wrap">
                {JSON.stringify(toolCalls, null, 2)}
              </pre>
            </CollapsibleContent>
          </Collapsible>
        </div>
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

type EditableReplayMessage = {
  id: string
  role: string
  contentText: string
  originalContent: unknown
  tool_call_id?: string
  tool_calls?: unknown[]
}

function hasReplayableImageReference(value: Record<string, unknown>): boolean {
  if (typeof value.image_base64 === 'string' && value.image_base64.trim()) {
    return true
  }

  const rawImageUrl = isRecord(value.image_url) ? value.image_url.url : value.image_url
  if (typeof rawImageUrl === 'string' && rawImageUrl.startsWith('data:image/')) {
    return true
  }

  const imageReference = isRecord(value.image_reference) ? value.image_reference : {}
  return Boolean(
    (typeof value.image_path === 'string' && value.image_path.trim()) ||
      (typeof value.image_uri === 'string' && value.image_uri.trim()) ||
      (typeof imageReference.image_path === 'string' && imageReference.image_path.trim()) ||
      (typeof imageReference.image_uri === 'string' && imageReference.image_uri.trim())
  )
}

function hasUnreplayableImagePart(value: unknown): boolean {
  if (Array.isArray(value)) {
    return value.some(hasUnreplayableImagePart)
  }
  if (!isRecord(value)) {
    return false
  }

  const partType = String(value.type || '').trim().toLowerCase()
  if (['image', 'image_url', 'input_image'].includes(partType)) {
    return !hasReplayableImageReference(value)
  }

  return Object.values(value).some(hasUnreplayableImagePart)
}

function createEditableReplayMessages(prompt: StructuredPromptPayload | null): EditableReplayMessage[] {
  return (prompt?.messages ?? []).map((message, index) => {
    const shouldUseTextFallback = hasUnreplayableImagePart(message.content)
    const originalContent = shouldUseTextFallback ? stringifyPromptContent(message.content) : message.content ?? ''
    return {
      id: `${message.index ?? index + 1}-${message.role ?? 'unknown'}-${index}`,
      role: String(message.role || 'user'),
      contentText: typeof originalContent === 'string' ? originalContent : stringifyPromptContent(originalContent),
      originalContent,
      tool_call_id: message.tool_call_id,
      tool_calls: message.tool_calls,
    }
  })
}

function parseReplayMessageContent(contentText: string, originalContent: unknown): unknown {
  if (typeof originalContent === 'string' || originalContent === null || originalContent === undefined) {
    return contentText
  }

  const trimmedContent = contentText.trim()
  if (!trimmedContent) {
    return ''
  }

  try {
    return JSON.parse(trimmedContent) as unknown
  } catch {
    return contentText
  }
}

function formatReplayTokenSummary(result: ReasoningReplayResponse): string {
  const parts = [
    `输入 ${result.prompt_tokens}`,
    `输出 ${result.completion_tokens}`,
    `总计 ${result.total_tokens}`,
  ]
  if (result.prompt_cache_hit_tokens > 0 || result.prompt_cache_miss_tokens > 0) {
    parts.push(`缓存命中 ${result.prompt_cache_hit_tokens}`)
  }
  if (result.duration_ms > 0) {
    parts.push(`耗时 ${formatDurationMs(result.duration_ms)}`)
  }
  return parts.join(' · ')
}

function ReasoningReplayPanel({
  open,
  onClose,
  selected,
  selectedTitle,
  structuredPrompt,
}: {
  open: boolean
  onClose: () => void
  selected: ReasoningPromptFile | null
  selectedTitle: string
  structuredPrompt: StructuredPromptPayload | null
}) {
  const { toast } = useToast()
  const [modelName, setModelName] = useState('')
  const [temperature, setTemperature] = useState('')
  const [maxTokens, setMaxTokens] = useState('')
  const [messages, setMessages] = useState<EditableReplayMessage[]>([])
  const [result, setResult] = useState<ReasoningReplayResponse | null>(null)
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    if (!open) {
      return
    }

    setModelName(structuredPrompt?.metadata?.model_name || selected?.model_name || '')
    setTemperature('')
    setMaxTokens('')
    setMessages(createEditableReplayMessages(structuredPrompt))
    setResult(null)
  }, [open, selected, structuredPrompt])

  const updateMessage = (id: string, patch: Partial<EditableReplayMessage>) => {
    setMessages((current) =>
      current.map((message) => (message.id === id ? { ...message, ...patch } : message))
    )
  }

  const handleReplay = async () => {
    const normalizedModelName = modelName.trim()
    if (!normalizedModelName) {
      toast({
        title: '缺少模型名称',
        description: '请填写 model_config.toml 中已配置的模型名称。',
        variant: 'destructive',
      })
      return
    }
    if (messages.length === 0) {
      toast({
        title: '没有可重放的消息',
        description: '这条记录没有结构化 messages。',
        variant: 'destructive',
      })
      return
    }

    setSubmitting(true)
    setResult(null)
    try {
      const replayResult = await replayReasoningPrompt({
        source_path: selected?.json_path ?? null,
        stage: selected?.stage ?? structuredPrompt?.request?.kind ?? '',
        model_name: normalizedModelName,
        messages: messages.map((message) => ({
          role: message.role,
          content: parseReplayMessageContent(message.contentText, message.originalContent),
          ...(message.tool_call_id ? { tool_call_id: message.tool_call_id } : {}),
          ...(message.tool_calls && message.tool_calls.length > 0 ? { tool_calls: message.tool_calls } : {}),
        })),
        tool_definitions: (structuredPrompt?.tool_definitions ?? []).filter(isRecord),
        temperature: temperature.trim() ? Number(temperature) : null,
        max_tokens: maxTokens.trim() ? Number(maxTokens) : null,
      })
      setResult(replayResult)
      toast({
        title: replayResult.success ? '重放完成' : '重放失败',
        description: replayResult.error || formatReplayTokenSummary(replayResult),
        variant: replayResult.success ? 'default' : 'destructive',
      })
    } catch (err) {
      toast({
        title: '重放失败',
        description: err instanceof Error ? err.message : '请求重放接口失败',
        variant: 'destructive',
      })
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <aside
      className={cn(
        'bg-background min-h-0 flex-col overflow-hidden rounded-md border shadow-sm',
        open ? 'flex' : 'hidden'
      )}
      aria-hidden={!open}
    >
      <div className="flex min-h-14 items-center justify-between gap-3 border-b px-3 py-2 sm:px-4">
        <div className="min-w-0">
          <div className="text-sm font-semibold">重放推理请求</div>
          <div className="text-muted-foreground truncate text-xs">{selectedTitle}</div>
        </div>
        <Button
          variant="outline"
          size="sm"
          className="h-8 w-8 p-0"
          onClick={onClose}
          disabled={submitting}
          title="关闭重放边栏"
          aria-label="关闭重放边栏"
        >
          <X className="h-4 w-4" />
        </Button>
      </div>

      <div className="flex min-h-0 flex-1 flex-col">
        <div className="flex-shrink-0 border-b p-3 sm:p-4">
          <div className="grid gap-3">
            <div className="grid gap-2">
              <Label htmlFor="reasoning-replay-model">模型名称</Label>
              <Input
                id="reasoning-replay-model"
                value={modelName}
                onChange={(event) => setModelName(event.target.value)}
                placeholder="model_config.toml 中的模型名称"
              />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="reasoning-replay-temperature">温度</Label>
              <Input
                id="reasoning-replay-temperature"
                type="number"
                min={0}
                max={2}
                step={0.1}
                value={temperature}
                onChange={(event) => setTemperature(event.target.value)}
                placeholder="默认"
              />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="reasoning-replay-max-tokens">最大 Token</Label>
              <Input
                id="reasoning-replay-max-tokens"
                type="number"
                min={1}
                step={1}
                value={maxTokens}
                onChange={(event) => setMaxTokens(event.target.value)}
                placeholder="默认"
              />
            </div>
          </div>
          <Button
            className="mt-3 h-9 w-full gap-1.5"
            onClick={handleReplay}
            disabled={submitting || messages.length === 0}
          >
            {submitting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
            执行重放
          </Button>
        </div>

        <section className="flex-shrink-0 space-y-3 border-b p-3 sm:p-4">
          <div className="flex items-center justify-between gap-2">
            <div className="text-sm font-semibold">重放结果</div>
            {submitting && (
              <span className="text-muted-foreground inline-flex items-center gap-1.5 text-xs">
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                请求中
              </span>
            )}
          </div>
          {!result && !submitting ? (
            <div className="text-muted-foreground rounded-md border border-dashed px-3 py-4 text-sm">
              执行重放后，模型回复、推理内容和工具调用会显示在这里。
            </div>
          ) : null}
          {result && (
            <div className="space-y-3">
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant={result.success ? 'default' : 'destructive'}>
                  {result.success ? '完成' : '失败'}
                </Badge>
                <span className="text-muted-foreground text-xs">{result.model_name}</span>
              </div>
              <div className="text-muted-foreground text-xs leading-5">
                {formatReplayTokenSummary(result)}
              </div>
              {result.error && (
                <div className="border-destructive/30 bg-destructive/10 rounded-md border px-3 py-2 text-sm text-destructive">
                  {result.error}
                </div>
              )}
              <pre className="bg-muted/30 max-h-56 min-h-24 overflow-auto rounded-md border p-3 text-sm leading-6 whitespace-pre-wrap">
                {result.response || '空响应'}
              </pre>
              {result.reasoning && (
                <Collapsible className="rounded-md border">
                  <CollapsibleTrigger asChild>
                    <button
                      type="button"
                      className="flex w-full items-center justify-between px-3 py-2 text-left text-sm font-medium"
                    >
                      推理内容
                      <ChevronDown className="h-4 w-4" />
                    </button>
                  </CollapsibleTrigger>
                  <CollapsibleContent className="border-t">
                    <pre className="max-h-56 overflow-auto p-3 text-sm leading-6 whitespace-pre-wrap">
                      {result.reasoning}
                    </pre>
                  </CollapsibleContent>
                </Collapsible>
              )}
              {result.tool_calls && result.tool_calls.length > 0 && (
                <ToolCallsCollapsible toolCalls={result.tool_calls} />
              )}
            </div>
          )}
        </section>

        <ScrollArea className="min-h-0 flex-1">
          <div className="divide-y">
            {messages.map((message, index) => (
              <section key={message.id} className="p-3 sm:p-4">
                <div className="mb-2 flex flex-wrap items-center gap-2">
                  <Badge variant="outline">#{index + 1}</Badge>
                  <Select
                    value={message.role}
                    onValueChange={(value) => updateMessage(message.id, { role: value })}
                  >
                    <SelectTrigger className="h-8 w-[130px]">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="system">system</SelectItem>
                      <SelectItem value="user">user</SelectItem>
                      <SelectItem value="assistant">assistant</SelectItem>
                      <SelectItem value="tool">tool</SelectItem>
                    </SelectContent>
                  </Select>
                  {message.tool_call_id && (
                    <span className="text-muted-foreground text-xs">
                      tool_call_id: {message.tool_call_id}
                    </span>
                  )}
                  {message.tool_calls && message.tool_calls.length > 0 && (
                    <Badge variant="secondary">工具调用 {message.tool_calls.length}</Badge>
                  )}
                </div>
                <Textarea
                  value={message.contentText}
                  onChange={(event) => updateMessage(message.id, { contentText: event.target.value })}
                  minHeight={110}
                  maxHeight={360}
                  className="font-mono text-xs leading-5"
                />
              </section>
            ))}
          </div>
        </ScrollArea>
      </div>
    </aside>
  )
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
  const [collapsedStageRows, setCollapsedStageRows] = useState<Set<string>>(() => new Set(['removed']))
  const [error, setError] = useState<string | null>(null)
  const [browsingStage, setBrowsingStage] = useState(
    () => Boolean(initialSearchParams.get('stage') || initialSearchParams.get('session') || initialTargetStem)
  )
  const [toolbarRoot, setToolbarRoot] = useState<HTMLElement | null>(null)
  const [topbarActionsRoot, setTopbarActionsRoot] = useState<HTMLElement | null>(null)
  const [replayPanelOpen, setReplayPanelOpen] = useState(false)
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
  const hasToolbarContent = Boolean(returnTo)

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
          ? data.items.find((item) => item.stage === stage && item.session_id === data.selected_session && item.stem === targetStem)
            ?? data.items.find((item) => item.stem === targetStem)
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
        const loadedJsonFiles = await Promise.all(jsonPaths.map((path) => getReasoningPromptFile(path)))
        const data = loadedJsonFiles[0]
        const avatarEntries = avatarFetchEnabled
          ? await Promise.all(
              loadedJsonFiles.flatMap((file) =>
                Object.entries(file.message_avatars ?? {}).map(async ([messageId, avatar]) => [
                  messageId,
                  {
                    ...avatar,
                    avatar_url: avatar.avatar_url ? await resolveApiPath(avatar.avatar_url) : null,
                  },
                ] as const)
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
    const copyContent = textContent || buildStructuredPromptCopyText(structuredPrompt) || jsonContent
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
      const parsedContent = JSON.parse(jsonContent) as unknown
      const exportContent = eraseNicknameOnExport ? eraseReasoningNicknames(parsedContent) : parsedContent
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
        description: eraseNicknameOnExport ? '已将昵称抹去为用户A、用户B等占位名' : '已保留原始昵称',
      })
    } catch (err) {
      toast({
        title: '导出失败',
        description: err instanceof Error ? err.message : '当前内容不是有效 JSON',
        variant: 'destructive',
      })
    }
  }

  const selectedSessionInfo = selected ? sessionInfoByName.get(selected.session_id) : undefined
  const selectedTitle = selected ? getReasoningRecordTitle(selected, selectedSessionInfo) : '未选择记录'
  const botSelfNames = useMemo(() => extractBotSelfNames(structuredPrompt), [structuredPrompt])
  const previewTabMode = selected?.json_path ? 'structured' : selected?.text_path ? 'text' : selected?.html_path ? 'html' : null
  const headerMeta = useMemo(
    () => extractReasoningHeaderMeta(structuredPrompt?.request?.selection_reason),
    [structuredPrompt]
  )
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
  const renderReturnButton = () => returnTo ? (
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
                    <div className="text-muted-foreground truncate text-xs">
                      {sessionSubtitle}
                    </div>
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
          inToolbar ? 'min-w-0 flex-[1_1_220px] sm:min-w-[260px] sm:max-w-[520px]' : undefined
        )}
      >
        <Search className="text-muted-foreground pointer-events-none absolute top-1/2 left-3 h-4 w-4 -translate-y-1/2" />
        <Input
          value={search}
          onChange={(event) => resetToFirstPage(() => setSearch(event.target.value))}
          className={cn(controlClassName, 'pl-9')}
          placeholder="搜索会话显示名、真实会话、文件名或 replyer 回复内容"
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
  const toolbarPortal = embedded && toolbarVisible && toolbarRoot ? createPortal(toolbarContent, toolbarRoot) : null
  const topbarActionsPortal =
    embedded && toolbarVisible && topbarActionsRoot
      ? createPortal(browsingStage ? renderTypeButton(true) : renderRefreshButton('topbar'), topbarActionsRoot)
      : null
  const showBrowsingControlsInline = browsingStage && (!embedded || !toolbarVisible || !toolbarRoot)
  const renderStageCard = (item: ReasoningPromptStageInfo) => (
    <div
      key={item.name}
      className={cn(
        'group relative flex min-h-20 flex-col rounded-md border bg-background text-left shadow-sm',
        'transition-[border-color,background-color,box-shadow,transform] duration-150 ease-out',
        'hover:-translate-y-0.5 hover:border-primary/80 hover:bg-primary/5 hover:shadow-md',
        'focus-within:-translate-y-0.5 focus-within:border-primary/80 focus-within:bg-primary/5 focus-within:shadow-md'
      )}
    >
      <button
        type="button"
        onClick={() => enterStage(item.name)}
        className="flex min-h-20 flex-1 cursor-pointer flex-col justify-between rounded-md p-3 pr-10 text-left focus-visible:ring-ring focus-visible:ring-2 focus-visible:outline-none"
      >
        <div className="space-y-1.5">
          <div className="text-primary text-sm font-extrabold tracking-normal uppercase transition-colors group-hover:text-primary sm:text-base">
            {item.name}
          </div>
          <div className="text-foreground text-sm font-semibold transition-colors group-hover:text-primary">
            {formatStageName(item.name)}
          </div>
        </div>
        <div className="text-muted-foreground mt-2 text-xs transition-colors group-hover:text-foreground/80">
          {item.session_count} 个会话
          {item.latest_modified_at > 0 ? ` · 最新 ${formatTime(null, item.latest_modified_at)}` : ''}
        </div>
      </button>
      <Button
        type="button"
        variant="ghost"
        size="sm"
        className="absolute right-2 bottom-2 h-7 w-7 p-0 opacity-70 hover:bg-destructive/10 hover:text-destructive group-hover:opacity-100"
        title={`清空${formatStageName(item.name)}`}
        aria-label={`清空${formatStageName(item.name)}`}
        disabled={clearingStage === item.name}
        onClick={() => setPendingClearStage(item)}
      >
        {clearingStage === item.name ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Trash2 className="h-3.5 w-3.5" />}
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
        <section key={row.key} className="grid gap-2 sm:grid-cols-[72px_minmax(0,1fr)] sm:items-start">
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
              <ChevronDown className={cn('h-3.5 w-3.5 transition-transform', collapsed && '-rotate-90')} />
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
  const pendingClearStageDeleting = pendingClearStage ? clearingStage === pendingClearStage.name : false

  return (
    <div className={cn('flex h-full min-h-0 flex-col gap-2 overflow-hidden sm:gap-3', embedded ? 'p-0' : 'p-2 lg:p-4')}>
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
              {pendingClearStage?.session_count ? ` 当前包含 ${pendingClearStage.session_count} 个会话。` : ''}
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
            <p className="text-muted-foreground text-sm">浏览 logs/maisaka_prompt 下的 prompt 记录</p>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            {renderReturnButton()}
            {renderRefreshButton()}
          </div>
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
          <div
            className="bg-background flex h-[32vh] min-h-[180px] flex-col overflow-hidden rounded-md border transition-[height,min-height,opacity,transform,border-width] duration-300 ease-out lg:h-auto lg:min-h-0 lg:transition-[opacity,transform,border-width]"
          >
            <div className="text-muted-foreground flex h-8 flex-shrink-0 items-center justify-between border-b px-2.5 text-xs">
              <span>{total} 条记录</span>
              <span>
                第 {page} / {totalPages} 页
              </span>
            </div>
            {embedded && browsingStage && (
              <div className="flex flex-shrink-0 flex-col gap-2 border-b p-2">
                <div className="flex items-center gap-2">
                  <div className="min-w-0 flex-1">
                    {renderSessionSelect('sidebarRow')}
                  </div>
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
                    item.display_title || (item.stage === 'replyer' ? item.output_preview : item.action_preview)
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
              className="flex min-h-0 flex-1 flex-col"
            >
              <div className="relative min-h-0 flex-1 overflow-hidden">
                <ScrollArea className="h-full transition-transform duration-300 ease-out">
                  <div className="min-h-full">
                    <div className="flex min-h-12 flex-col gap-2 border-b px-3 py-2 sm:min-h-14 sm:px-4 sm:py-3 xl:flex-row xl:items-center xl:justify-between">
                      <div className="min-w-0 flex-1">
                        <div className="truncate text-sm font-medium">
                          {selectedTitle}
                        </div>
                        {(headerMeta.sessionId || headerMeta.callId) && (
                          <div className="text-muted-foreground mt-1 flex min-w-0 flex-wrap gap-x-3 gap-y-0.5 text-[11px] leading-4">
                            {headerMeta.sessionId && (
                              <span className="min-w-0 truncate">会话ID: {headerMeta.sessionId}</span>
                            )}
                            {headerMeta.callId && (
                              <span className="min-w-0 truncate">调用ID: {headerMeta.callId}</span>
                            )}
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
                              !(textContent || buildStructuredPromptCopyText(structuredPrompt) || jsonContent)
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
                            disabled={contentLoading || (structuredPrompt?.messages?.length ?? 0) === 0}
                            title="编辑消息并重放本次请求"
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
                              {structuredPrompt.jargon_learning_calls.map((llmCall, callIndex) => (
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

                                  <div className="space-y-2">
                                    {(llmCall.messages ?? []).map((message, messageIndex) => {
                                      const isBotSelfMessage = isBotSelfStructuredMessage(message, botSelfNames)
                                      const roleStyle = getStructuredPromptMessageRoleStyle(
                                        message.role,
                                        isBotSelfMessage
                                      )
                                      return (
                                        <div
                                          key={`${message.index ?? messageIndex}-${message.role ?? 'unknown'}`}
                                          className={cn(
                                            'relative rounded-md border px-2.5 pt-9 pb-2.5 sm:px-3 sm:pt-10 sm:pb-3',
                                            roleStyle.containerClassName
                                          )}
                                        >
                                          <div className="absolute top-1.5 left-1.5 flex flex-wrap items-center gap-1.5 sm:top-2 sm:left-2">
                                            <span className="text-muted-foreground px-1 text-[11px] font-semibold">
                                              输入 #{message.index ?? messageIndex + 1}
                                            </span>
                                            <span className="text-muted-foreground px-1 text-[11px] font-semibold">
                                              {roleStyle.label}
                                            </span>
                                            {message.tool_call_id && (
                                              <span className="text-muted-foreground text-xs">
                                                tool_call_id: {message.tool_call_id}
                                              </span>
                                            )}
                                          </div>
                                          <NaturalLanguageText
                                            text={stringifyPromptContent(message.content) || '空内容'}
                                            avatarMap={messageAvatarMap}
                                          />
                                          {message.tool_calls && message.tool_calls.length > 0 && (
                                            <ToolCallsCollapsible toolCalls={message.tool_calls} />
                                          )}
                                        </div>
                                      )
                                    })}
                                  </div>

                                  {llmCall.output && (
                                    <div className="rounded-md border p-2.5 sm:p-3">
                                      <Badge variant="outline" className="mb-2">
                                        {llmCall.output.title || '输出结果'}
                                      </Badge>
                                      <NaturalLanguageText
                                        text={stringifyPromptContent(llmCall.output.content) || '空输出'}
                                        avatarMap={messageAvatarMap}
                                      />
                                      {llmCall.output.tool_calls &&
                                        llmCall.output.tool_calls.length > 0 && (
                                          <ToolCallsCollapsible toolCalls={llmCall.output.tool_calls} />
                                        )}
                                    </div>
                                  )}
                                </div>
                              ))}
                            </div>
                          )}

                        {structuredPrompt.output && (
                          <div className="rounded-md border p-2.5 sm:p-3">
                            <Badge variant="secondary" className="mb-2">
                              {structuredPrompt.output.title || '输出结果'}
                            </Badge>
                            <NaturalLanguageText
                              text={stringifyPromptContent(structuredPrompt.output.content) || '空输出'}
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
                                  <span className="text-muted-foreground px-1 text-[11px] font-semibold">
                                    #{message.index ?? index + 1}
                                  </span>
                                  <span className="text-muted-foreground px-1 text-[11px] font-semibold">
                                    {roleStyle.label}
                                  </span>
                                  {message.tool_call_id && (
                                    <span className="text-muted-foreground text-xs">
                                      tool_call_id: {message.tool_call_id}
                                    </span>
                                  )}
                                </div>
                                <NaturalLanguageText
                                  text={stringifyPromptContent(message.content) || '空内容'}
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
              </div>
            </Tabs>
          </div>
          <ReasoningReplayPanel
            open={replayPanelOpen}
            onClose={() => setReplayPanelOpen(false)}
            selected={selected}
            selectedTitle={selectedTitle}
            structuredPrompt={structuredPrompt}
          />
        </div>
      )}
    </div>
  )
}
