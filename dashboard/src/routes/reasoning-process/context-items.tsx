/* eslint-disable react-refresh/only-export-components -- 本模块同时导出 Item 展示组件及其共享格式化函数。 */

import type { CSSProperties } from 'react'
import { Avatar, AvatarFallback, AvatarImage } from '@/components/ui/avatar'
import { Badge } from '@/components/ui/badge'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import { ChevronDown } from 'lucide-react'
import { formatChatAccountLabel } from '@/lib/chat-display'
import type {
  ContextItemSnapshot,
  ReasoningPromptFile,
  ReasoningPromptMessageAvatar,
  ReasoningPromptSessionInfo,
} from '@/lib/reasoning-process-api'
import { cn } from '@/lib/utils'
import {
  isRecord,
  stringifyPromptContent,
  stringifyStructuredValue,
  type StructuredPromptPayload,
} from './schema'

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
  llm_error: 'LLM 请求异常',
  planner: '规划器',
  reply_effect_judge: '回复效果评估',
  replyer: '回复器',
  timing_gate: '时机判断',
}

export function formatStageName(stage: string): string {
  return STAGE_LABELS[stage] ?? stage
}

function getStructuredPromptMessageRoleStyle(
  role?: string,
  isBotSelf = false
): {
  label: string
  containerClassName: string
  badgeClassName: string
} {
  const normalizedRole = String(role || '')
    .trim()
    .toLowerCase()
  if (isBotSelf) {
    return {
      label: role || 'user',
      containerClassName:
        'border-orange-300/70 bg-orange-50/75 dark:border-orange-700/60 dark:bg-orange-950/25',
      badgeClassName:
        'border-orange-400/70 bg-orange-100/85 text-orange-900 dark:border-orange-700 dark:bg-orange-950 dark:text-orange-100',
    }
  }
  const roleStyles: Record<
    string,
    { label: string; containerClassName: string; badgeClassName: string }
  > = {
    system: {
      label: 'system',
      containerClassName:
        'border-cyan-300/70 bg-cyan-50/70 dark:border-cyan-700/60 dark:bg-cyan-950/25',
      badgeClassName:
        'border-cyan-400/70 bg-cyan-100/80 text-cyan-900 dark:border-cyan-700 dark:bg-cyan-950 dark:text-cyan-100',
    },
    user: {
      label: 'user',
      containerClassName:
        'border-emerald-300/70 bg-emerald-50/70 dark:border-emerald-700/60 dark:bg-emerald-950/25',
      badgeClassName:
        'border-emerald-400/70 bg-emerald-100/80 text-emerald-900 dark:border-emerald-700 dark:bg-emerald-950 dark:text-emerald-100',
    },
    assistant: {
      label: 'assistant',
      containerClassName:
        'border-amber-300/70 bg-amber-50/70 dark:border-amber-700/60 dark:bg-amber-950/25',
      badgeClassName:
        'border-amber-400/70 bg-amber-100/80 text-amber-900 dark:border-amber-700 dark:bg-amber-950 dark:text-amber-100',
    },
    tool: {
      label: 'tool',
      containerClassName:
        'border-violet-300/70 bg-violet-50/70 dark:border-violet-700/60 dark:bg-violet-950/25',
      badgeClassName:
        'border-violet-400/70 bg-violet-100/80 text-violet-900 dark:border-violet-700 dark:bg-violet-950 dark:text-violet-100',
    },
    reasoning: {
      label: 'reasoning',
      containerClassName:
        'border-indigo-300/70 bg-indigo-50/70 dark:border-indigo-700/60 dark:bg-indigo-950/25',
      badgeClassName:
        'border-indigo-400/70 bg-indigo-100/80 text-indigo-900 dark:border-indigo-700 dark:bg-indigo-950 dark:text-indigo-100',
    },
    function_call: {
      label: 'function call',
      containerClassName:
        'border-fuchsia-300/70 bg-fuchsia-50/70 dark:border-fuchsia-700/60 dark:bg-fuchsia-950/25',
      badgeClassName:
        'border-fuchsia-400/70 bg-fuchsia-100/80 text-fuchsia-900 dark:border-fuchsia-700 dark:bg-fuchsia-950 dark:text-fuchsia-100',
    },
  }
  if (roleStyles[normalizedRole]) return roleStyles[normalizedRole]
  if (normalizedRole.startsWith('provider_')) {
    return {
      label: normalizedRole.replace('_', ' '),
      containerClassName:
        'border-sky-300/70 bg-sky-50/70 dark:border-sky-700/60 dark:bg-sky-950/25',
      badgeClassName:
        'border-sky-400/70 bg-sky-100/80 text-sky-900 dark:border-sky-700 dark:bg-sky-950 dark:text-sky-100',
    }
  }
  return {
    label: role || '未知角色',
    containerClassName: 'bg-muted/30',
    badgeClassName: 'bg-background/80',
  }
}

export type ParsedMessageTagBlock = {
  type: 'message'
  attrs: Record<string, string>
  body: string
}

export type ParsedTextBlock = {
  type: 'text'
  text: string
}

export type ParsedNaturalTextBlock = ParsedMessageTagBlock | ParsedTextBlock

export type ToolParameterView = {
  name: string
  type: string
  description: string
  required: boolean
  enumValues: string[]
  defaultValue: string
}

export type ToolDefinitionView = {
  name: string
  type: string
  description: string
  parameters: ToolParameterView[]
  raw: unknown
}

export type ReasoningPromptMessageAvatarMap = Record<string, ReasoningPromptMessageAvatar>

export type ReasoningHeaderMeta = {
  sessionId: string
  callId: string
  remainingText: string
}

export function getContextItemRole(item: ContextItemSnapshot): string {
  const roles: Record<string, string> = {
    SystemMessageItem: 'system',
    UserMessageItem: 'user',
    AssistantMessageItem: 'assistant',
    ReasoningItem: 'reasoning',
    FunctionCallItem: 'function_call',
    FunctionCallOutputItem: 'tool',
    ProviderActivityItem: 'provider_activity',
    ProviderOpaqueItem: 'provider_opaque',
  }
  return roles[item.item_type] || item.item_type
}

export function getContextItemReadableText(item: ContextItemSnapshot): string {
  if (['SystemMessageItem', 'UserMessageItem', 'AssistantMessageItem'].includes(item.item_type)) {
    return stringifyPromptContent(item.parts)
  }
  if (item.item_type === 'ReasoningItem') {
    const parts = item.summary_parts?.length ? item.summary_parts : item.text_parts
    return (parts ?? []).join('\n')
  }
  if (item.item_type === 'FunctionCallOutputItem') return String(item.output || '')
  if (item.item_type === 'ProviderActivityItem' || item.item_type === 'ProviderOpaqueItem') {
    return String(item.display_summary || '')
  }
  return ''
}

export function getContextItemToolCalls(item: ContextItemSnapshot): unknown[] {
  if (item.item_type !== 'FunctionCallItem' || !isRecord(item.tool_call)) return []
  const extraContent = isRecord(item.tool_call.extra_content) ? item.tool_call.extra_content : {}
  return [
    {
      id: item.tool_call.call_id,
      function: {
        name: item.tool_call.func_name,
        arguments: item.tool_call.args ?? {},
      },
      source: extraContent.tool_call_source,
      source_label: extraContent.tool_call_source_label,
      extra_content: extraContent,
    },
  ]
}

export function buildStructuredPromptCopyText(payload: StructuredPromptPayload | null): string {
  if (!payload) return ''

  const sections: string[] = []
  const metadataLines: string[] = []
  if (payload.request?.kind) metadataLines.push(`请求类型：${payload.request.kind}`)
  if (payload.request?.selection_reason)
    metadataLines.push(`选择原因：${payload.request.selection_reason}`)
  if (payload.metadata?.model_name) metadataLines.push(`模型：${payload.metadata.model_name}`)
  if (typeof payload.metadata?.duration_ms === 'number')
    metadataLines.push(`耗时：${payload.metadata.duration_ms} ms`)
  if (metadataLines.length > 0) sections.push(`[元信息]\n${metadataLines.join('\n')}`)

  if (payload.generation_attempts.length > 0) {
    sections.push(`[Provider 调用链]\n${stringifyStructuredValue(payload.generation_attempts)}`)
  }

  if (payload.output_items.length > 0) {
    const outputSections = payload.output_items.map((item, index) => {
      const text = getContextItemReadableText(item)
      const toolCalls = getContextItemToolCalls(item)
      const details =
        text ||
        (toolCalls.length ? stringifyStructuredValue(toolCalls) : stringifyStructuredValue(item))
      return `#${index + 1} ${item.item_type}\n${details}`
    })
    sections.push(
      `[${payload.presentation?.output_title || '输出 Items'}]\n${outputSections.join('\n\n')}`
    )
  }

  const messageSections = payload.request_items.map((item, index) => {
    const text = getContextItemReadableText(item)
    const toolCalls = getContextItemToolCalls(item)
    const details =
      text ||
      (toolCalls.length ? stringifyStructuredValue(toolCalls) : stringifyStructuredValue(item))
    return `#${index + 1} ${item.item_type}\n${details}`
  })
  if (messageSections.length > 0) sections.push(`[请求 Items]\n${messageSections.join('\n\n')}`)

  if (payload.tool_definitions?.length) {
    sections.push(`[工具定义]\n${stringifyStructuredValue(payload.tool_definitions)}`)
  }

  return sections.join(`\n\n${'='.repeat(80)}\n\n`)
}

export type ToolCallDisplayItem = {
  id: string
  name: string
  arguments: unknown
  source: string
  sourceLabel: string
}

export function normalizeToolCallForDisplay(toolCall: unknown): ToolCallDisplayItem {
  const toolRecord = isRecord(toolCall) ? toolCall : {}
  const functionRecord = isRecord(toolRecord.function) ? toolRecord.function : {}
  const extraContent = isRecord(toolRecord.extra_content) ? toolRecord.extra_content : {}
  const rawSource = String(
    toolRecord.source || toolRecord.tool_call_source || extraContent.tool_call_source || ''
  ).trim()
  const normalizedSource = rawSource.toLowerCase()
  const sourceLabel =
    normalizedSource === 'reasoning'
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

export function getToolCallSourceClassName(source: string): string {
  if (source === 'reasoning') {
    return 'border-teal-500/45 bg-teal-500/10 text-teal-700 dark:text-teal-300'
  }
  if (source === 'response') {
    return 'border-amber-500/45 bg-amber-500/10 text-amber-700 dark:text-amber-300'
  }
  if (source === 'provider') {
    return 'border-sky-500/45 bg-sky-500/10 text-sky-700 dark:text-sky-300'
  }
  return 'border-muted-foreground/30 bg-muted/40 text-muted-foreground'
}

export function formatSchemaType(schema: Record<string, unknown>): string {
  const rawType = schema.type
  if (Array.isArray(rawType)) return rawType.map(String).join(' | ')
  if (typeof rawType === 'string') return rawType
  if (isRecord(schema.items)) return `${formatSchemaType(schema.items)}[]`
  if (schema.enum) return 'enum'
  return 'unknown'
}

export function formatSchemaValue(value: unknown): string {
  if (value === undefined) return ''
  if (typeof value === 'string') return value
  return JSON.stringify(value)
}

export function toStringList(value: unknown): string[] {
  if (!Array.isArray(value)) return []
  return value.map((item) => String(item))
}

export function normalizeToolDefinition(toolDefinition: unknown): ToolDefinitionView {
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

export function normalizeDisplayName(name: string): string {
  return name.trim().toLowerCase()
}

export function extractBotSelfNames(prompt: StructuredPromptPayload | null): Set<string> {
  const names = new Set<string>(['麦麦'])

  for (const item of prompt?.request_items ?? []) {
    if (item.item_type !== 'SystemMessageItem') continue
    const content = getContextItemReadableText(item)
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

export function getFirstMessageTagAttrs(text: string): Record<string, string> {
  const match = text.match(/<message\b([^>]*)>/i)
  return match ? parseMessageTagAttributes(match[1] ?? '') : {}
}

export function isBotSelfContextItem(
  item: ContextItemSnapshot,
  botSelfNames: Set<string>
): boolean {
  if (item.item_type !== 'UserMessageItem') return false

  const text = getContextItemReadableText(item)
  const user = getFirstMessageTagAttrs(text).user
  return Boolean(user && botSelfNames.has(normalizeDisplayName(user)))
}

export function formatSessionType(chatType: string): string {
  if (chatType === 'group') return '群聊'
  if (chatType === 'private') return '私聊'
  return '未知类型'
}

export function getSessionDisplayName(
  sessionName: string,
  sessionInfo?: ReasoningPromptSessionInfo,
  fallbackName?: string | null
): string {
  return sessionInfo?.display_name || fallbackName || sessionName
}

export function getSessionSubtitle(sessionInfo?: ReasoningPromptSessionInfo): string {
  if (!sessionInfo) return ''

  const parts = []
  if (sessionInfo.platform) {
    parts.push(`${sessionInfo.platform} · ${formatSessionType(sessionInfo.chat_type)}`)
  }
  if (sessionInfo.account_id) {
    parts.push(formatChatAccountLabel(sessionInfo.account_id))
  }
  if (sessionInfo.resolved_session_id) {
    parts.push(`会话 ${sessionInfo.resolved_session_id.slice(0, 8)}`)
  } else {
    parts.push('未解析到真实会话')
  }
  return parts.join(' · ')
}

export function extractReasoningHeaderMeta(text?: string): ReasoningHeaderMeta {
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

export function getReasoningRecordTitle(
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

export function formatPromptPreviewText(previewText: string): string {
  return previewText.replace(/^动作[：:]\s*/, '')
}

export function buildAvatarFallbackText(displayName: string, userId: string): string {
  const normalizedName = displayName.trim()
  if (normalizedName) return normalizedName.slice(0, 1).toUpperCase()
  const normalizedUserId = userId.trim()
  return normalizedUserId ? normalizedUserId.slice(-2) : '用'
}

export function decodeSimpleHtmlEntity(value: string): string {
  return value
    .replace(/&quot;/g, '"')
    .replace(/&apos;/g, "'")
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&amp;/g, '&')
}

export function parseMessageTagAttributes(rawAttributes: string): Record<string, string> {
  const attrs: Record<string, string> = {}
  const attributePattern = /([A-Za-z_][\w:-]*)\s*=\s*"([^"]*)"/g
  for (const match of rawAttributes.matchAll(attributePattern)) {
    attrs[match[1]] = decodeSimpleHtmlEntity(match[2])
  }
  return attrs
}

export function parseNaturalTextBlocks(text: string): ParsedNaturalTextBlock[] {
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
    const body = text
      .slice(bodyStart, nextStart)
      .replace(/<\/message>\s*$/i, '')
      .trim()
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

  return blocks.filter((block) =>
    block.type === 'message' ? block.body || Object.keys(block.attrs).length > 0 : block.text.trim()
  )
}

export function renderMessageTagMeta(
  attrs: Record<string, string>,
  avatarMap: ReasoningPromptMessageAvatarMap
) {
  const user = attrs.user || ''
  const time = attrs.time || ''
  const msgId = attrs.msg_id || ''
  const chatId = attrs.chat_id || ''
  const avatar = msgId ? avatarMap[msgId] : undefined
  const avatarLabel = avatar?.display_name || user || avatar?.user_id || '用户'

  return (
    <div className="text-muted-foreground mb-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs">
      {avatar && (
        <Avatar className="bg-background h-6 w-6 shrink-0 border">
          {avatar.avatar_url && (
            <AvatarImage src={avatar.avatar_url} alt={`${avatarLabel} 的头像`} />
          )}
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

export function NaturalLanguageText({
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
          <div key={`message-${index}`} className="border-primary/60 border-l-2 pl-2">
            {renderMessageTagMeta(block.attrs, avatarMap)}
            <pre className={baseClassName}>{block.body || '空消息'}</pre>
          </div>
        )
      })}
    </div>
  )
}

export function ToolCallsCollapsible({ toolCalls }: { toolCalls: unknown[] }) {
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
            <div
              key={`${toolCall.id || toolCall.name}-${index}`}
              className="bg-background/70 rounded-md border p-2.5"
            >
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant="secondary" className="font-mono">
                  {toolCall.name}
                </Badge>
                {toolCall.sourceLabel && (
                  <Badge
                    variant="outline"
                    className={cn(
                      'px-1.5 py-0 text-[11px]',
                      getToolCallSourceClassName(toolCall.source)
                    )}
                  >
                    {toolCall.sourceLabel}
                  </Badge>
                )}
                {toolCall.id && (
                  <span className="text-muted-foreground font-mono text-[11px]">{toolCall.id}</span>
                )}
              </div>
              <pre className="bg-muted/20 mt-2 rounded-md border p-2 font-mono text-xs leading-5 whitespace-pre-wrap">
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

export function ContextItemCard({
  item,
  index,
  avatarMap,
  botSelfNames,
}: {
  item: ContextItemSnapshot
  index: number
  avatarMap: ReasoningPromptMessageAvatarMap
  botSelfNames: Set<string>
}) {
  const role = getContextItemRole(item)
  const roleStyle = getStructuredPromptMessageRoleStyle(
    role,
    isBotSelfContextItem(item, botSelfNames)
  )
  const readableText = getContextItemReadableText(item)
  const toolCalls = getContextItemToolCalls(item)
  const callId =
    item.item_type === 'FunctionCallItem' && isRecord(item.tool_call)
      ? String(item.tool_call.call_id || '')
      : String(item.call_id || '')

  return (
    <article
      className={cn('space-y-2 rounded-md border p-2.5 sm:p-3', roleStyle.containerClassName)}
    >
      <div className="flex min-w-0 flex-wrap items-center gap-1.5">
        <Badge variant="outline">#{index + 1}</Badge>
        <Badge variant="outline" className={cn('font-mono', roleStyle.badgeClassName)}>
          {roleStyle.label}
        </Badge>
        <span className="text-muted-foreground font-mono text-[11px]">{item.item_type}</span>
        {item.meta.logical_turn_id && (
          <Badge
            variant="outline"
            className="max-w-48 truncate font-mono text-[10px]"
            title={item.meta.logical_turn_id}
          >
            turn {item.meta.logical_turn_id}
          </Badge>
        )}
        {item.phase && <Badge variant="outline">phase: {item.phase}</Badge>}
        {item.representation && <Badge variant="outline">{item.representation}</Badge>}
        {item.status && <Badge variant="outline">{item.status}</Badge>}
        <span
          className="text-muted-foreground ml-auto max-w-full truncate font-mono text-[10px]"
          title={item.meta.item_id}
        >
          {item.meta.item_id}
        </span>
      </div>

      {callId && (
        <div className="text-muted-foreground font-mono text-[11px]">call_id: {callId}</div>
      )}
      {readableText && <NaturalLanguageText text={readableText} avatarMap={avatarMap} />}
      {toolCalls.length > 0 && <ToolCallsCollapsible toolCalls={toolCalls} />}
      {item.item_type === 'ProviderActivityItem' && item.details && item.details.length > 0 && (
        <pre className="bg-muted/20 max-h-64 overflow-auto rounded-md border p-2.5 font-mono text-xs leading-5 whitespace-pre-wrap">
          {item.details.join('\n')}
        </pre>
      )}
      {!readableText && toolCalls.length === 0 && (
        <p className="text-muted-foreground text-xs">此 Item 没有可见文本；完整字段见下方 JSON。</p>
      )}

      <Collapsible className="bg-background/50 rounded-md border">
        <CollapsibleTrigger asChild>
          <button
            type="button"
            className="hover:bg-muted/50 flex w-full items-center justify-between gap-2 px-2.5 py-1.5 text-left text-xs transition-colors [&[data-state=open]>svg]:rotate-180"
          >
            <span>完整 Item JSON</span>
            <ChevronDown className="h-3.5 w-3.5 shrink-0 transition-transform" />
          </button>
        </CollapsibleTrigger>
        <CollapsibleContent className="border-t">
          <pre className="max-h-96 overflow-auto p-2.5 font-mono text-xs leading-5 whitespace-pre-wrap">
            {JSON.stringify(item, null, 2)}
          </pre>
        </CollapsibleContent>
      </Collapsible>
    </article>
  )
}

export function ContextItemTimeline({
  title,
  items,
  avatarMap,
  botSelfNames,
}: {
  title: string
  items: ContextItemSnapshot[]
  avatarMap: ReasoningPromptMessageAvatarMap
  botSelfNames: Set<string>
}) {
  return (
    <section className="space-y-2 rounded-md border p-2.5 sm:p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-semibold">{title}</span>
        <Badge variant="secondary">{items.length} Items</Badge>
      </div>
      {items.length === 0 ? (
        <p className="text-muted-foreground text-xs">没有 Items。</p>
      ) : (
        <div className="space-y-2">
          {items.map((item, index) => (
            <ContextItemCard
              key={`${item.meta.item_id}-${index}`}
              item={item}
              index={index}
              avatarMap={avatarMap}
              botSelfNames={botSelfNames}
            />
          ))}
        </div>
      )}
    </section>
  )
}

export function ToolDefinitionsCollapsible({ toolDefinitions }: { toolDefinitions: unknown[] }) {
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
            <div
              key={`${tool.name}-${index}`}
              className="bg-background/60 rounded-md border p-2.5 sm:p-3"
            >
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
                              className="border-destructive/50 text-destructive px-1.5 py-0 text-[11px]"
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
