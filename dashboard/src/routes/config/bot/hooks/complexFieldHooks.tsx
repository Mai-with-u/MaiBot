import { useEffect, useRef, useState, type CSSProperties, type PointerEvent } from 'react'

import { AlertCircle, Check, ChevronDown, ChevronUp, ExternalLink, GripVertical, Plus, Trash2 } from 'lucide-react'

import { Avatar, AvatarFallback, AvatarImage } from '@/components/ui/avatar'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from '@/components/ui/command'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Slider } from '@/components/ui/slider'
import { Switch } from '@/components/ui/switch'
import { Textarea } from '@/components/ui/textarea'
import { fieldTitleClassName } from '@/components/dynamic-form/fieldStyle'
import { getChatStreams, resolveChatTargets, type ChatStream, type ChatTargetResolveRequest } from '@/lib/chat-management-api'
import { getBotConfigCached } from '@/lib/config-api'
import { useResolvedAvatarUrl } from '@/lib/avatar-url'
import type { FieldHookComponent } from '@/lib/field-hooks'
import type { ConfigSchema } from '@/types/config-schema'

import { createJsonFieldHook } from './JsonFieldHookFactory'
import { createListItemEditorHook } from './ListItemEditorHookFactory'

type ExpressionRuleType = 'group' | 'private'

interface ExpressionGroupTarget {
  platform: string
  item_id: string
  rule_type: ExpressionRuleType
}

interface ExpressionGroupValue {
  targets: ExpressionGroupTarget[]
}

interface PlatformAccountRow {
  platform: string
  account: string
}

interface LearningRuleEditorProps {
  emptyText: string
  items: Record<string, unknown>[]
  onAddItem: (item?: Record<string, unknown>) => void
  onItemFieldChange: (index: number, fieldName: string, fieldValue: unknown) => void
  onRemoveItem: (index: number) => void
  usePlatformSelect?: boolean
}

interface PlatformSelectOption {
  label: string
  value: string
}

type LearningScopeKind = 'chat' | 'default' | 'global' | 'platform' | 'platformDefault' | 'target'
type GroupScopeKind = 'chat' | 'global' | 'platform' | 'target'

const PLATFORM_ACCOUNT_ROW_GRID_CLASS =
  'grid gap-2 rounded-md border bg-muted/20 p-3 sm:grid-cols-[minmax(0,5.5rem)_minmax(0,8.5rem)_2.5rem] md:grid-cols-[minmax(0,6rem)_minmax(0,9.5rem)_2.5rem]'

const LEARNING_ITEM_FALLBACK_SCHEMA: ConfigSchema = {
  className: 'LearningItem',
  classDoc: '学习规则',
  fields: [
    {
      name: 'platform',
      type: 'string',
      label: {
        zh_CN: '平台',
        en_US: 'Platform',
        ja_JP: 'プラットフォーム',
      },
      description: '平台，与 ID 一起留空表示全局。',
      required: false,
      default: '',
      'x-widget': 'input',
      'x-icon': 'wifi',
    },
    {
      name: 'item_id',
      type: 'string',
      label: {
        zh_CN: '聊天流 ID',
        en_US: 'Chat stream ID',
        ja_JP: 'チャットストリーム ID',
      },
      description: '要单独配置的群号或用户 ID；留空表示默认规则。',
      required: false,
      default: '',
      'x-widget': 'input',
      'x-icon': 'hash',
    },
    {
      name: 'type',
      type: 'select',
      label: {
        zh_CN: '聊天类型',
        en_US: 'Chat type',
        ja_JP: 'チャット種別',
      },
      description: '这条规则作用于群聊还是私聊。',
      required: false,
      default: 'group',
      options: ['group', 'private'],
      'x-widget': 'select',
      'x-icon': 'users',
      'x-option-descriptions': {
        group: '群聊',
        private: '私聊',
      },
    },
    {
      name: 'use',
      type: 'boolean',
      label: {
        zh_CN: '使用',
        en_US: 'Use',
        ja_JP: '使用',
      },
      description: '是否在这个聊天里使用已学到的内容。',
      required: false,
      default: true,
      'x-widget': 'switch',
      'x-icon': 'message-square',
    },
    {
      name: 'learn',
      type: 'boolean',
      label: {
        zh_CN: '学习',
        en_US: 'Learn',
        ja_JP: '学習',
      },
      description: '是否从这个聊天里继续学习新内容。',
      required: false,
      default: true,
      'x-widget': 'switch',
      'x-icon': 'graduation-cap',
    },
  ],
}

interface TimelineSegment {
  left: number
  width: number
}

interface TalkRuleTimelineItem {
  groupKey: string
  groupLabel: string
  itemId: string
  platform: string
  ruleType: string
  index: number
  rawTime: string
  scopeLabel: string
  title: string
  timeLabel: string
  value: number
  invalidTime: boolean
  range: TimelineRange | null
  segments: TimelineSegment[]
}

interface TalkRuleTimelineGroup {
  key: string
  label: string
  itemId: string
  platform: string
  ruleType: string
  scopeLabel: string
  hasFallback: boolean
  hasWildcard: boolean
  items: TalkRuleTimelineItem[]
}

interface TimelineRange {
  end: number
  start: number
}

const DAY_MINUTES = 24 * 60
const TIMELINE_TICKS = [0, 3, 6, 9, 12, 15, 18, 21, 24]
const TIMELINE_DRAG_STEP_MINUTES = 5

const ruleTypeLabel = (rule: unknown) => {
  if (rule === 'private') return '私聊'
  if (rule === 'group') return '群聊'
  return rule ? String(rule) : '未指定'
}

const learningFlagLabel = (item: Record<string, unknown>) => {
  const flags: string[] = []
  if (item.use) flags.push('使用')
  if (item.learn) flags.push('学习')
  return flags.length ? flags.join(' / ') : '使用和学习均关闭'
}

const platformLabel = (item: Record<string, unknown>) => {
  const platform = typeof item.platform === 'string' ? item.platform.trim() : ''
  const itemId = typeof item.item_id === 'string' ? item.item_id.trim() : ''
  if (!platform && !itemId) return '全局'
  if (!platform) return itemId
  if (!itemId) return platform
  return `${platform}:${itemId}`
}

const talkTargetScopeLabel = (item: Record<string, unknown>) => {
  const platform = typeof item.platform === 'string' ? item.platform.trim() : ''
  const itemId = typeof item.item_id === 'string' ? item.item_id.trim() : ''
  if (!platform && !itemId) return '全局'
  if (platform === '*' || itemId === '*') return '通配'
  if (!platform || !itemId) return '默认'
  return '精确'
}

const talkRuleTargetValues = (item: Record<string, unknown>) => {
  const platform = typeof item.platform === 'string' ? item.platform.trim() : ''
  const itemId = typeof item.item_id === 'string' ? item.item_id.trim() : ''
  const ruleType = typeof item.rule_type === 'string' ? item.rule_type.trim() : 'group'
  return { platform, itemId, ruleType }
}

const talkRuleGroupKey = (item: Record<string, unknown>) => {
  const { platform, itemId, ruleType } = talkRuleTargetValues(item)
  return `${platform}\u0000${itemId}\u0000${ruleType}`
}

const learningScopeLabel = (item: Record<string, unknown>) => {
  const platform = typeof item.platform === 'string' ? item.platform.trim() : ''
  const itemId = typeof item.item_id === 'string' ? item.item_id.trim() : ''
  if (!platform && !itemId) return '全局默认'
  if (platform === '*' && itemId === '*') return '全部聊天'
  if (platform === '*') return `任意平台:${itemId || '留空'}`
  if (itemId === '*') return `${platform || '留空'}:全部目标`
  if (platform && !itemId) return `${platform}:平台兜底`
  if (!platform || !itemId) return `${platform || '留空'}:${itemId || '留空'}`
  return `${platform}:${itemId}`
}

const talkRuleGroupLabel = (item: Record<string, unknown>) => {
  return `${platformLabel(item)} · ${ruleTypeLabel(talkRuleTargetValues(item).ruleType)}`
}

const normalizeSpecialTextValue = (value: unknown) => {
  return typeof value === 'string' ? value.trim() : ''
}

const parsePlatformName = (value: unknown) => {
  const text = typeof value === 'string' ? value.trim() : ''
  if (!text) return ''
  const separatorIndex = text.indexOf(':')
  return separatorIndex >= 0 ? text.slice(0, separatorIndex).trim() : text
}

const buildDefinedPlatformOptions = (config: Record<string, unknown>): PlatformSelectOption[] => {
  const botConfig =
    config.bot && typeof config.bot === 'object' && !Array.isArray(config.bot)
      ? (config.bot as Record<string, unknown>)
      : {}
  const options: PlatformSelectOption[] = []
  const knownValues = new Set<string>()
  const addPlatform = (value: unknown) => {
    const platform = parsePlatformName(value)
    if (!platform || knownValues.has(platform)) {
      return
    }
    knownValues.add(platform)
    options.push({ label: platform, value: platform })
  }

  addPlatform(botConfig.platform)
  if (Array.isArray(botConfig.platforms)) {
    botConfig.platforms.forEach((platform) => addPlatform(platform))
  }

  return options
}

const withCurrentPlatformOption = (
  options: PlatformSelectOption[],
  currentPlatform: string,
): PlatformSelectOption[] => {
  const normalizedPlatform = currentPlatform.trim()
  if (!normalizedPlatform || options.some((option) => option.value === normalizedPlatform)) {
    return options
  }

  return [
    ...options,
    {
      label: `${normalizedPlatform}（当前值）`,
      value: normalizedPlatform,
    },
  ]
}

const LEARNING_SCOPE_OPTIONS: Array<{
  description: string
  kind: LearningScopeKind
  title: string
}> = [
  {
    kind: 'default',
    title: '默认兜底',
    description: '没有更具体规则命中时使用',
  },
  {
    kind: 'global',
    title: '全局通配',
    description: '所有平台和聊天流都会命中',
  },
  {
    kind: 'platform',
    title: '平台通配',
    description: '某个平台下的全部聊天流都会命中',
  },
  {
    kind: 'platformDefault',
    title: '平台兜底',
    description: '该平台没有更具体规则时使用',
  },
  {
    kind: 'chat',
    title: '指定聊天流',
    description: '指定平台里的一个具体聊天',
  },
]

const GROUP_SCOPE_OPTIONS: Array<{
  description: string
  kind: GroupScopeKind
  title: string
}> = [
  {
    kind: 'global',
    title: '全局通配',
    description: '当前聊天类型下全部聊天流都加入同组',
  },
  {
    kind: 'platform',
    title: '指定平台',
    description: '某个平台下的全部聊天流',
  },
  {
    kind: 'chat',
    title: '指定聊天流',
    description: '指定平台里的一个具体聊天',
  },
]

const EXACT_GROUP_SCOPE_OPTIONS: Array<{
  description: string
  kind: GroupScopeKind
  title: string
}> = [
  {
    kind: 'chat',
    title: '指定聊天流',
    description: '精确选择一个平台里的具体聊天',
  },
]

const resolveLearningScopeKind = (item: Record<string, unknown>): LearningScopeKind => {
  const platform = normalizeSpecialTextValue(item.platform)
  const itemId = normalizeSpecialTextValue(item.item_id)
  if (!platform && !itemId) return 'default'
  if (platform === '*' && itemId === '*') return 'global'
  if (platform === '*' && itemId && itemId !== '*') return 'target'
  if (platform && platform !== '*' && !itemId) return 'platformDefault'
  if (platform && platform !== '*' && itemId === '*') return 'platform'
  return 'chat'
}

const resolveLearningPlatformValue = (
  item: Record<string, unknown>,
  platformOptions: PlatformSelectOption[],
) => {
  const platform = normalizeSpecialTextValue(item.platform)
  if (platform && platform !== '*') {
    return platform
  }
  return platformOptions[0]?.value ?? ''
}

const buildLearningRulePatch = (
  scopeKind: LearningScopeKind,
  item: Record<string, unknown>,
  platformOptions: PlatformSelectOption[],
) => {
  const platform = resolveLearningPlatformValue(item, platformOptions)
  const itemId = normalizeSpecialTextValue(item.item_id)

  switch (scopeKind) {
    case 'default':
      return { platform: '', item_id: '' }
    case 'global':
      return { platform: '*', item_id: '*' }
    case 'platform':
      return { platform, item_id: '*' }
    case 'platformDefault':
      return { platform, item_id: '' }
    case 'target':
      return {
        platform: '*',
        item_id: itemId && itemId !== '*' ? itemId : '',
      }
    case 'chat':
      return {
        platform,
        item_id: itemId && itemId !== '*' ? itemId : '',
      }
  }
}

const resolveGroupScopeKind = (target: ExpressionGroupTarget): GroupScopeKind => {
  const platform = normalizeSpecialTextValue(target.platform)
  const itemId = normalizeSpecialTextValue(target.item_id)
  if (platform === '*' && itemId === '*') return 'global'
  if (platform === '*' && itemId && itemId !== '*') return 'target'
  if (platform && platform !== '*' && itemId === '*') return 'platform'
  return 'chat'
}

const groupScopeLabel = (target: ExpressionGroupTarget) => {
  const platform = normalizeSpecialTextValue(target.platform)
  const itemId = normalizeSpecialTextValue(target.item_id)
  if (platform === '*' && itemId === '*') return '全部聊天流'
  if (platform === '*') return `任意平台:${itemId || '未填写'}`
  if (itemId === '*') return `${platform || '未填写'}:全部目标`
  if (!platform || !itemId) return `${platform || '未填写'}:${itemId || '未填写'}`
  return `${platform}:${itemId}`
}

const chatStreamTargetId = (chat: ChatStream) => {
  return chat.chat_type === 'group'
    ? chat.group_id || chat.target_id
    : chat.user_id || chat.target_id
}

const chatStreamOptionKey = (chat: ChatStream) => {
  const targetId = chatStreamTargetId(chat)
  return [chat.platform, targetId, chat.chat_type].map((value) => encodeURIComponent(value)).join('::')
}

const expressionTargetOptionKey = (target: ExpressionGroupTarget) => {
  return [
    normalizeSpecialTextValue(target.platform),
    normalizeSpecialTextValue(target.item_id),
    target.rule_type,
  ].map((value) => encodeURIComponent(value)).join('::')
}

const chatStreamTypeLabel = (chatType: string) => {
  return chatType === 'private' ? '私聊' : '群聊'
}

const chatStreamSelectLabel = (chat: ChatStream) => {
  const targetId = chatStreamTargetId(chat)
  const name = chat.display_name || targetId || chat.session_id
  return `${name} · ${chatStreamTypeLabel(chat.chat_type)} · ${chat.platform}:${targetId}`
}

const findSharedMemoryChatStream = (chats: ChatStream[], target: ExpressionGroupTarget) => {
  const selectedKey = expressionTargetOptionKey(target)
  return chats.find((chat) => chatStreamOptionKey(chat) === selectedKey)
}

const normalizeSharedMemoryChatStreams = (chats: ChatStream[]) => {
  const optionMap = new Map<string, ChatStream>()
  chats.forEach((chat) => {
    const targetId = chatStreamTargetId(chat)
    if (!chat.platform || !targetId || !['group', 'private'].includes(chat.chat_type)) {
      return
    }
    optionMap.set(chatStreamOptionKey(chat), chat)
  })

  return Array.from(optionMap.values()).sort((left, right) => {
    const typeOrder = left.chat_type === right.chat_type ? 0 : left.chat_type === 'group' ? -1 : 1
    if (typeOrder !== 0) return typeOrder
    return chatStreamSelectLabel(left).localeCompare(chatStreamSelectLabel(right), 'zh-Hans-CN')
  })
}

const resolveGroupPlatformValue = (
  target: ExpressionGroupTarget,
  platformOptions: PlatformSelectOption[],
) => {
  const platform = normalizeSpecialTextValue(target.platform)
  if (platform && platform !== '*') {
    return platform
  }
  return platformOptions[0]?.value ?? 'qq'
}

const buildGroupTargetPatch = (
  scopeKind: GroupScopeKind,
  target: ExpressionGroupTarget,
  platformOptions: PlatformSelectOption[],
) => {
  const platform = resolveGroupPlatformValue(target, platformOptions)
  const itemId = normalizeSpecialTextValue(target.item_id)

  switch (scopeKind) {
    case 'global':
      return { platform: '*', item_id: '*' }
    case 'platform':
      return { platform, item_id: '*' }
    case 'target':
      return {
        platform: '*',
        item_id: itemId && itemId !== '*' ? itemId : '',
      }
    case 'chat':
      return {
        platform,
        item_id: itemId && itemId !== '*' ? itemId : '',
      }
  }
}

function LearningPlatformControl({
  invalid = false,
  onChange,
  platformOptions,
  usePlatformSelect,
  value,
}: {
  invalid?: boolean
  onChange: (value: string) => void
  platformOptions: PlatformSelectOption[]
  usePlatformSelect: boolean
  value: string
}) {
  const choices = withCurrentPlatformOption(platformOptions, value)

  if (usePlatformSelect) {
    return (
      <Select
        disabled={choices.length === 0}
        value={value}
        onValueChange={onChange}
      >
        <SelectTrigger className={`h-8 min-w-0 ${invalid ? 'border-destructive focus:ring-destructive' : ''}`}>
          <SelectValue placeholder={choices.length > 0 ? '选择平台' : '未定义平台'} />
        </SelectTrigger>
        <SelectContent>
          {choices.map((choice) => (
            <SelectItem key={choice.value} value={choice.value}>
              {choice.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    )
  }

  return (
    <Input
      className={`h-8 min-w-0 ${invalid ? 'border-destructive focus-visible:ring-destructive' : ''}`}
      value={value}
      placeholder="qq"
      onChange={(event) => onChange(event.target.value)}
    />
  )
}

type ChatTargetResolutionState =
  | { status: 'idle' | 'loading' | 'missing' | 'error'; session?: undefined }
  | { status: 'found'; session: ChatStream }

const CHAT_TARGET_RESOLUTION_CACHE = new Map<string, ChatTargetResolutionState>()
let chatTargetResolutionQueue: Array<{
  key: string
  request: ChatTargetResolveRequest
  reject: (error: unknown) => void
  resolve: (state: ChatTargetResolutionState) => void
}> = []
let chatTargetResolutionTimer: number | null = null

const chatTargetResolutionKey = (platform: string, itemId: string, ruleType: ExpressionRuleType) => {
  return `${platform}\u0000${itemId}\u0000${ruleType}`
}

function enqueueChatTargetResolution(
  request: ChatTargetResolveRequest,
): Promise<ChatTargetResolutionState> {
  const key = chatTargetResolutionKey(request.platform, request.item_id, request.rule_type as ExpressionRuleType)
  const cachedState = CHAT_TARGET_RESOLUTION_CACHE.get(key)
  if (cachedState) {
    return Promise.resolve(cachedState)
  }

  return new Promise((resolve, reject) => {
    chatTargetResolutionQueue.push({ key, request, resolve, reject })
    if (chatTargetResolutionTimer !== null) {
      return
    }

    chatTargetResolutionTimer = window.setTimeout(() => {
      const pendingQueue = chatTargetResolutionQueue
      chatTargetResolutionQueue = []
      chatTargetResolutionTimer = null

      const uniqueRequests: ChatTargetResolveRequest[] = []
      const uniqueKeyIndex = new Map<string, number>()
      pendingQueue.forEach((entry) => {
        if (uniqueKeyIndex.has(entry.key)) {
          return
        }
        uniqueKeyIndex.set(entry.key, uniqueRequests.length)
        uniqueRequests.push(entry.request)
      })

      resolveChatTargets(uniqueRequests)
        .then((results) => {
          pendingQueue.forEach((entry) => {
            const result = results[uniqueKeyIndex.get(entry.key) ?? -1]
            const state: ChatTargetResolutionState =
              result?.found && result.session
                ? { status: 'found', session: result.session }
                : { status: 'missing' }
            CHAT_TARGET_RESOLUTION_CACHE.set(entry.key, state)
            entry.resolve(state)
          })
        })
        .catch((error: unknown) => {
          pendingQueue.forEach((entry) => entry.reject(error))
        })
    }, 40)
  })
}

function useExactChatTargetResolution(
  platform: string,
  itemId: string,
  ruleType: ExpressionRuleType,
  enabled: boolean,
): ChatTargetResolutionState {
  const [state, setState] = useState<ChatTargetResolutionState>({ status: 'idle' })

  useEffect(() => {
    let ignored = false
    const normalizedPlatform = platform.trim()
    const normalizedItemId = itemId.trim()
    if (!enabled || !normalizedPlatform || !normalizedItemId) {
      window.setTimeout(() => {
        if (!ignored) setState({ status: 'idle' })
      }, 0)
      return () => {
        ignored = true
      }
    }

    const timer = window.setTimeout(() => {
      setState({ status: 'loading' })
      enqueueChatTargetResolution({
        platform: normalizedPlatform,
        item_id: normalizedItemId,
        rule_type: ruleType,
      })
        .then((resultState) => {
          if (ignored) return
          setState(resultState)
        })
        .catch(() => {
          if (!ignored) setState({ status: 'error' })
        })
    }, 250)

    return () => {
      ignored = true
      window.clearTimeout(timer)
    }
  }, [enabled, itemId, platform, ruleType])

  return state
}

function ChatTargetResolutionPreview({
  state,
}: {
  state: ChatTargetResolutionState
}) {
  if (state.status === 'idle') {
    return null
  }

  if (state.status === 'loading') {
    return <div className="text-xs text-muted-foreground">正在验证聊天流...</div>
  }

  if (state.status === 'missing') {
    return (
      <div className="flex items-center gap-1.5 text-xs text-destructive">
        <AlertCircle className="h-3.5 w-3.5" />
        无效的聊天流
      </div>
    )
  }

  if (state.status === 'error') {
    return (
      <div className="flex items-center gap-1.5 text-xs text-destructive">
        <AlertCircle className="h-3.5 w-3.5" />
        聊天流验证失败
      </div>
    )
  }

  if (state.status === 'found') {
    return <ResolvedChatTargetInfo chat={state.session} />
  }

  return null
}

function ResolvedChatTargetInfo({ chat }: { chat: ChatStream }) {
  const targetId = chat.chat_type === 'group' ? chat.group_id || chat.target_id : chat.user_id || chat.target_id
  const avatarUrl = useResolvedAvatarUrl(chat.platform, targetId, chat.chat_type === 'group' ? 'group' : 'user')
  const fallbackText = (chat.display_name || targetId || chat.platform || '?').slice(0, 1)

  return (
    <div className="flex min-w-0 items-center gap-2 text-xs text-muted-foreground">
      <Avatar className="h-5 w-5">
        {avatarUrl && <AvatarImage src={avatarUrl} alt={`${chat.display_name} 的头像`} />}
        <AvatarFallback className="text-[10px]">{fallbackText}</AvatarFallback>
      </Avatar>
      <span className="min-w-0 truncate text-foreground">{chat.display_name}</span>
      <span className="shrink-0 font-mono text-[11px] text-muted-foreground">
        {chat.platform}:{chat.target_id}
      </span>
    </div>
  )
}

function LearningScopeFields({
  chatTargetState,
  item,
  onFieldChange,
  platformOptions,
  scopeKind,
  usePlatformSelect,
}: {
  chatTargetState: ChatTargetResolutionState
  item: Record<string, unknown>
  onFieldChange: (fieldName: string, fieldValue: unknown) => void
  platformOptions: PlatformSelectOption[]
  scopeKind: LearningScopeKind
  usePlatformSelect: boolean
}) {
  const platform = normalizeSpecialTextValue(item.platform)
  const itemId = normalizeSpecialTextValue(item.item_id)
  const platformValue = platform && platform !== '*' ? platform : platformOptions[0]?.value ?? ''
  const invalidChatTarget = chatTargetState.status === 'missing' || chatTargetState.status === 'error'

  if (scopeKind === 'default' || scopeKind === 'global') {
    return null
  }

  if (scopeKind === 'target') {
    return (
      <div className="flex min-w-0 items-center gap-2">
        <Label className="shrink-0 whitespace-nowrap text-[11px] leading-none text-muted-foreground">聊天流 ID</Label>
        <Input
          className="h-8 w-40 min-w-0 font-mono"
          value={itemId === '*' ? '' : itemId}
          placeholder="群号或用户 ID"
          onChange={(event) => onFieldChange('item_id', event.target.value)}
        />
      </div>
    )
  }

  return (
    <div className="min-w-0 space-y-1.5">
      <div className="flex min-w-0 flex-wrap items-center gap-3">
        <div className="flex min-w-0 items-center gap-2">
          <Label className="shrink-0 whitespace-nowrap text-[11px] leading-none text-muted-foreground">平台</Label>
          <div className="w-28 min-w-0">
            <LearningPlatformControl
              invalid={invalidChatTarget}
              value={platformValue}
              platformOptions={platformOptions}
              usePlatformSelect={usePlatformSelect}
              onChange={(nextPlatform) => onFieldChange('platform', nextPlatform)}
            />
          </div>
        </div>
        {scopeKind === 'chat' && (
          <div className="flex min-w-0 items-center gap-2">
            <Label className="shrink-0 whitespace-nowrap text-[11px] leading-none text-muted-foreground">聊天流 ID</Label>
            <Input
              className={`h-8 w-40 min-w-0 font-mono ${invalidChatTarget ? 'border-destructive focus-visible:ring-destructive' : ''}`}
              value={itemId === '*' ? '' : itemId}
              placeholder="群号或用户 ID"
              onChange={(event) => onFieldChange('item_id', event.target.value)}
            />
          </div>
        )}
      </div>
    </div>
  )
}

function GroupScopeFields({
  chatTargetState,
  onFieldChange,
  platformOptions,
  scopeKind,
  target,
  usePlatformSelect,
}: {
  chatTargetState: ChatTargetResolutionState
  onFieldChange: (patch: Partial<ExpressionGroupTarget>) => void
  platformOptions: PlatformSelectOption[]
  scopeKind: GroupScopeKind
  target: ExpressionGroupTarget
  usePlatformSelect: boolean
}) {
  const platform = normalizeSpecialTextValue(target.platform)
  const itemId = normalizeSpecialTextValue(target.item_id)
  const platformValue = platform && platform !== '*' ? platform : platformOptions[0]?.value ?? 'qq'
  const invalidChatTarget = chatTargetState.status === 'missing' || chatTargetState.status === 'error'

  if (scopeKind === 'global') {
    return null
  }

  if (scopeKind === 'target') {
    return (
      <div className="flex min-w-0 items-center gap-2">
        <Label className="shrink-0 whitespace-nowrap text-[11px] leading-none text-muted-foreground">聊天流 ID</Label>
        <Input
          className="h-8 w-40 min-w-0 font-mono"
          value={itemId === '*' ? '' : itemId}
          placeholder="群号或用户 ID"
          onChange={(event) => onFieldChange({ item_id: event.target.value })}
        />
      </div>
    )
  }

  return (
    <div className="min-w-0 space-y-1.5">
      <div className="flex min-w-0 flex-wrap items-center gap-3">
        <div className="flex min-w-0 items-center gap-2">
          <Label className="shrink-0 whitespace-nowrap text-[11px] leading-none text-muted-foreground">平台</Label>
          <div className="w-28 min-w-0">
            <LearningPlatformControl
              invalid={invalidChatTarget}
              value={platformValue}
              platformOptions={platformOptions}
              usePlatformSelect={usePlatformSelect}
              onChange={(nextPlatform) => onFieldChange({ platform: nextPlatform })}
            />
          </div>
        </div>
        {scopeKind === 'chat' && (
          <div className="flex min-w-0 items-center gap-2">
            <Label className="shrink-0 whitespace-nowrap text-[11px] leading-none text-muted-foreground">聊天流 ID</Label>
            <Input
              className={`h-8 w-40 min-w-0 font-mono ${invalidChatTarget ? 'border-destructive focus-visible:ring-destructive' : ''}`}
              value={itemId === '*' ? '' : itemId}
              placeholder="群号或用户 ID"
              onChange={(event) => onFieldChange({ item_id: event.target.value })}
            />
          </div>
        )}
      </div>
    </div>
  )
}

function SharedMemoryChatStreamSelect({
  chats,
  onSelect,
  target,
}: {
  chats: ChatStream[]
  onSelect: (patch: Partial<ExpressionGroupTarget>) => void
  target: ExpressionGroupTarget
}) {
  const [open, setOpen] = useState(false)
  const [search, setSearch] = useState('')
  const selectedChat = findSharedMemoryChatStream(chats, target)
  const normalizedSearch = search.trim().toLocaleLowerCase()
  const matchedChats = chats.filter((chat) => {
    if (!normalizedSearch) return true
    return [
      chat.display_name,
      chat.platform,
      chatStreamTargetId(chat),
      chatStreamTypeLabel(chat.chat_type),
      chat.session_id,
      chat.group_name,
      chat.user_nickname,
      chat.user_cardname,
    ]
      .filter(Boolean)
      .join(' ')
      .toLocaleLowerCase()
      .includes(normalizedSearch)
  })
  const resultLimit = 50
  const allGroups = matchedChats.filter((chat) => chat.chat_type === 'group')
  const allPrivateChats = matchedChats.filter((chat) => chat.chat_type === 'private')
  const groups = allGroups.slice(0, resultLimit)
  const privateChats = allPrivateChats.slice(0, resultLimit)
  const isResultLimited = groups.length < allGroups.length || privateChats.length < allPrivateChats.length

  const renderOption = (chat: ChatStream) => {
    const selected = selectedChat ? chatStreamOptionKey(selectedChat) === chatStreamOptionKey(chat) : false
    return (
      <CommandItem
        key={chatStreamOptionKey(chat)}
        value={chatStreamOptionKey(chat)}
        onSelect={() => {
          onSelect({
            platform: chat.platform,
            item_id: chatStreamTargetId(chat),
            rule_type: chat.chat_type,
          })
          setOpen(false)
          setSearch('')
        }}
      >
        <Check className={`h-4 w-4 ${selected ? 'opacity-100' : 'opacity-0'}`} />
        <span className="min-w-0 truncate">{chatStreamSelectLabel(chat)}</span>
      </CommandItem>
    )
  }

  return (
    <div className="flex min-w-0 items-center gap-2">
      <Label className="shrink-0 whitespace-nowrap text-[11px] leading-none text-muted-foreground">
        选择群聊/私聊
      </Label>
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <Button
            type="button"
            variant="outline"
            role="combobox"
            aria-expanded={open}
            aria-label="选择群聊或私聊"
            className="h-8 min-w-0 flex-1 justify-between font-normal"
          >
            {selectedChat ? (
              <span className="min-w-0 truncate">{chatStreamSelectLabel(selectedChat)}</span>
            ) : (
              <span className="min-w-0 truncate text-muted-foreground">
                {chats.length > 0 ? '搜索或选择已知聊天流' : '暂无已知聊天流'}
              </span>
            )}
            <ChevronDown className="ml-2 h-4 w-4 shrink-0 opacity-50" />
          </Button>
        </PopoverTrigger>
        <PopoverContent
          align="start"
          className="p-0"
          style={{ width: 'var(--radix-popover-trigger-width)' }}
        >
          <Command shouldFilter={false}>
            <CommandInput
              value={search}
              onValueChange={setSearch}
              placeholder="搜索群名、私聊名、群号或用户 ID..."
            />
            <CommandList className="max-h-[280px]">
              <CommandEmpty>未找到匹配的聊天流</CommandEmpty>
              {groups.length > 0 && (
                <CommandGroup heading="群聊">
                  {groups.map(renderOption)}
                </CommandGroup>
              )}
              {privateChats.length > 0 && (
                <CommandGroup heading="私聊">
                  {privateChats.map(renderOption)}
                </CommandGroup>
              )}
              {isResultLimited && (
                <div className="border-t px-3 py-2 text-xs text-muted-foreground">
                  群聊和私聊各最多显示 {resultLimit} 个匹配项，请输入关键词缩小范围。
                </div>
              )}
            </CommandList>
          </Command>
        </PopoverContent>
      </Popover>
    </div>
  )
}

function SharedMemoryManualFields({
  onChange,
  target,
}: {
  onChange: (patch: Partial<ExpressionGroupTarget>) => void
  target: ExpressionGroupTarget
}) {
  const platform = normalizeSpecialTextValue(target.platform)
  const itemId = normalizeSpecialTextValue(target.item_id)

  return (
    <div className="rounded-md border bg-muted/20 p-2.5">
      <div className="grid min-w-0 gap-2 sm:grid-cols-[minmax(8rem,0.8fr)_minmax(10rem,1fr)_minmax(8rem,0.7fr)]">
        <div className="min-w-0 space-y-1">
          <Label className="text-[11px] leading-none text-muted-foreground">平台</Label>
          <Input
            className="h-8 min-w-0"
            value={platform}
            placeholder="qq"
            onChange={(event) => onChange({ platform: event.target.value })}
          />
        </div>
        <div className="min-w-0 space-y-1">
          <Label className="text-[11px] leading-none text-muted-foreground">群号或用户 ID</Label>
          <Input
            className="h-8 min-w-0 font-mono"
            value={itemId === '*' ? '' : itemId}
            placeholder="群号或用户 ID"
            onChange={(event) => onChange({ item_id: event.target.value })}
          />
        </div>
        <div className="min-w-0 space-y-1">
          <Label className="text-[11px] leading-none text-muted-foreground">聊天类型</Label>
          <Select
            value={target.rule_type}
            onValueChange={(value) => onChange({ rule_type: normalizeExpressionRuleType(value) })}
          >
            <SelectTrigger aria-label="选择聊天类型" className="h-8 min-w-0">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="group">群聊</SelectItem>
              <SelectItem value="private">私聊</SelectItem>
            </SelectContent>
          </Select>
        </div>
      </div>
    </div>
  )
}

function AddGroupTargetDialog({
  open,
  onAdd,
  onOpenChange,
  scopeOptions,
  title,
}: {
  open: boolean
  onAdd: (scopeKind: GroupScopeKind, ruleType: ExpressionRuleType) => void
  onOpenChange: (open: boolean) => void
  scopeOptions: Array<{
    description: string
    kind: GroupScopeKind
    title: string
  }>
  title: string
}) {
  const [selectedScope, setSelectedScope] = useState<GroupScopeKind>('chat')
  const [ruleType, setRuleType] = useState<ExpressionRuleType>('group')
  const fallbackScope = scopeOptions.find((option) => option.kind === 'chat')?.kind ?? scopeOptions[0]?.kind ?? 'chat'
  const activeScope = scopeOptions.some((option) => option.kind === selectedScope) ? selectedScope : fallbackScope

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent style={{ '--dialog-width': '44rem' } as CSSProperties}>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
        </DialogHeader>
        <div className="space-y-3">
          <div className="grid gap-2 md:grid-cols-2">
            {scopeOptions.map((option) => (
              <button
                key={option.kind}
                type="button"
                className={`rounded-md border p-3 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${
                  activeScope === option.kind
                    ? 'border-primary bg-primary text-primary-foreground'
                    : 'bg-muted/20 hover:bg-muted/40'
                }`}
                onClick={() => setSelectedScope(option.kind)}
              >
                <span className="block text-sm font-semibold">{option.title}</span>
                <span className="mt-1 block text-xs leading-5 opacity-75">{option.description}</span>
              </button>
            ))}
          </div>
          <div className="max-w-40 space-y-1">
            <Label className="text-[11px] leading-none text-muted-foreground">聊天类型</Label>
            <Select value={ruleType} onValueChange={(nextRuleType) => setRuleType(normalizeExpressionRuleType(nextRuleType))}>
              <SelectTrigger className="h-8">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="group">群聊</SelectItem>
                <SelectItem value="private">私聊</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </div>
        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button type="button" onClick={() => onAdd(activeScope, ruleType)}>
            添加
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function AddLearningRuleDialog({
  open,
  onAdd,
  onOpenChange,
}: {
  open: boolean
  onAdd: (scopeKind: LearningScopeKind, ruleType: ExpressionRuleType) => void
  onOpenChange: (open: boolean) => void
}) {
  const [selectedScope, setSelectedScope] = useState<LearningScopeKind>('chat')
  const [ruleType, setRuleType] = useState<ExpressionRuleType>('group')

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent style={{ '--dialog-width': '48rem' } as CSSProperties}>
        <DialogHeader>
          <DialogTitle>添加学习规则</DialogTitle>
          <DialogDescription>选择规则作用范围和聊天类型。添加后范围与类型不可直接修改，需要删除后重新添加。</DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
            {LEARNING_SCOPE_OPTIONS.map((option) => (
              <button
                key={option.kind}
                type="button"
                className={`rounded-md border p-3 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${
                  selectedScope === option.kind
                    ? 'border-primary bg-primary text-primary-foreground'
                    : 'bg-muted/20 hover:bg-muted/40'
                }`}
                onClick={() => setSelectedScope(option.kind)}
              >
                <span className="block text-sm font-semibold">{option.title}</span>
                <span className="mt-1 block text-xs leading-5 opacity-75">{option.description}</span>
              </button>
            ))}
          </div>
          <div className="max-w-40 space-y-1">
            <Label className="text-[11px] leading-none text-muted-foreground">聊天类型</Label>
            <Select value={ruleType} onValueChange={(nextRuleType) => setRuleType(normalizeExpressionRuleType(nextRuleType))}>
              <SelectTrigger className="h-8">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="group">群聊</SelectItem>
                <SelectItem value="private">私聊</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </div>
        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button type="button" onClick={() => onAdd(selectedScope, ruleType)}>
            添加
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function LearningRuleItem({
  index,
  item,
  onItemFieldChange,
  onRemoveItem,
  platformOptions,
  usePlatformSelect,
}: {
  index: number
  item: Record<string, unknown>
  onItemFieldChange: (index: number, fieldName: string, fieldValue: unknown) => void
  onRemoveItem: (index: number) => void
  platformOptions: PlatformSelectOption[]
  usePlatformSelect: boolean
}) {
  const scopeLabel = learningScopeLabel(item)
  const ruleType = normalizeExpressionRuleType(item.type)
  const scopeKind = resolveLearningScopeKind(item)
  const platform = normalizeSpecialTextValue(item.platform)
  const itemId = normalizeSpecialTextValue(item.item_id)
  const platformValue = platform && platform !== '*' ? platform : platformOptions[0]?.value ?? ''
  const chatTargetState = useExactChatTargetResolution(platformValue, itemId, ruleType, scopeKind === 'chat')
  const updateScopeField = (fieldName: string, fieldValue: unknown) => {
    onItemFieldChange(index, fieldName, fieldValue)
    if (fieldName === 'platform' && scopeKind === 'platform') {
      onItemFieldChange(index, 'item_id', '*')
    }
  }

  return (
    <div className="space-y-2 rounded-md border bg-muted/20 p-2.5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex min-w-0 flex-wrap items-center gap-1.5">
          <span className="truncate text-sm font-semibold">
            {scopeLabel}
          </span>
          <Badge variant="secondary" className="px-1.5 py-0 text-[11px]">
            {ruleTypeLabel(ruleType)}
          </Badge>
          {scopeKind === 'chat' && <ChatTargetResolutionPreview state={chatTargetState} />}
        </div>
        <Button
          type="button"
          size="icon"
          variant="ghost"
          className="h-7 w-7 text-destructive hover:text-destructive"
          aria-label={`删除学习规则 ${index + 1}`}
          title="删除"
          onClick={() => onRemoveItem(index)}
        >
          <Trash2 className="h-4 w-4" />
        </Button>
      </div>

      <div className="space-y-2">
        <div className="grid min-w-0 items-center gap-2 lg:grid-cols-[max-content_minmax(13rem,14rem)]">
          <div className="min-w-0">
            {scopeKind === 'default' || scopeKind === 'global' ? (
              <div className="rounded-md border bg-background/70 px-2.5 py-2 text-xs text-muted-foreground">
                当前范围不需要填写平台或聊天流 ID。
              </div>
            ) : (
              <LearningScopeFields
                chatTargetState={chatTargetState}
                item={item}
                onFieldChange={updateScopeField}
                platformOptions={platformOptions}
                scopeKind={scopeKind}
                usePlatformSelect={usePlatformSelect}
              />
            )}
          </div>
          <div className="grid min-w-0 grid-cols-2 gap-1.5">
            <div className="flex min-w-0 items-center justify-between gap-2 rounded-md border bg-background/70 px-2 py-1.5">
              <Label className="whitespace-nowrap text-xs">使用</Label>
              <Switch
                className="shrink-0"
                checked={Boolean(item.use)}
                onCheckedChange={(checked) => onItemFieldChange(index, 'use', checked)}
              />
            </div>
            <div className="flex min-w-0 items-center justify-between gap-2 rounded-md border bg-background/70 px-2 py-1.5">
              <Label className="whitespace-nowrap text-xs">学习</Label>
              <Switch
                className="shrink-0"
                checked={Boolean(item.learn)}
                onCheckedChange={(checked) => onItemFieldChange(index, 'learn', checked)}
              />
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

function LearningRuleEditor({
  emptyText,
  items,
  onAddItem,
  onItemFieldChange,
  onRemoveItem,
  usePlatformSelect = false,
}: LearningRuleEditorProps) {
  const [definedPlatformOptions, setDefinedPlatformOptions] = useState<PlatformSelectOption[]>([])
  const [showAddDialog, setShowAddDialog] = useState(false)

  useEffect(() => {
    if (!usePlatformSelect) {
      return
    }

    let disposed = false
    getBotConfigCached()
      .then((config) => {
        if (!disposed) {
          setDefinedPlatformOptions(buildDefinedPlatformOptions(config))
        }
      })
      .catch((error: unknown) => {
        console.error('加载黑话学习配置平台列表失败:', error)
      })

    return () => {
      disposed = true
    }
  }, [usePlatformSelect])

  const getPlatformOptions = (platform: unknown) =>
    withCurrentPlatformOption(
      definedPlatformOptions,
      typeof platform === 'string' ? platform : '',
    )
  const createLearningRule = (scopeKind: LearningScopeKind, ruleType: ExpressionRuleType) => {
    const baseRule = {
      type: ruleType,
      use: true,
      learn: true,
    }
    return {
      ...baseRule,
      ...buildLearningRulePatch(scopeKind, baseRule, definedPlatformOptions),
    }
  }

  const addRule = (scopeKind: LearningScopeKind, ruleType: ExpressionRuleType) => {
    onAddItem(createLearningRule(scopeKind, ruleType))
    setShowAddDialog(false)
  }

  return (
    <div className="space-y-2">
      {items.length === 0 ? (
        <div className="rounded-md border border-dashed bg-muted/30 px-4 py-5 text-center text-sm text-muted-foreground">
          {emptyText}
        </div>
      ) : (
        <div className="space-y-2">
          {items.map((item, index) => (
            <LearningRuleItem
              key={index}
              index={index}
              item={item}
              onItemFieldChange={onItemFieldChange}
              onRemoveItem={onRemoveItem}
              platformOptions={getPlatformOptions(item.platform)}
              usePlatformSelect={usePlatformSelect}
            />
          ))}
        </div>
      )}

      <Button
        type="button"
        size="sm"
        variant="outline"
        className="h-8 w-full"
        onClick={() => setShowAddDialog(true)}
      >
        <Plus className="mr-1 h-4 w-4" />
        添加学习规则
      </Button>
      <AddLearningRuleDialog
        open={showAddDialog}
        onAdd={addRule}
        onOpenChange={setShowAddDialog}
      />
    </div>
  )
}

function ExpressionGroupMemberItem({
  chatStreamOptions = [],
  groupIndex,
  groupLabel,
  isSharedMemoryGroup = false,
  member,
  memberIndex,
  onRemoveMember,
  onUpdateMember,
  platformOptions,
  scopeKind,
  usePlatformSelect,
}: {
  chatStreamOptions?: ChatStream[]
  groupIndex: number
  groupLabel: string
  isSharedMemoryGroup?: boolean
  member: ExpressionGroupTarget
  memberIndex: number
  onRemoveMember: (groupIndex: number, memberIndex: number) => void
  onUpdateMember: (groupIndex: number, memberIndex: number, patch: Partial<ExpressionGroupTarget>) => void
  platformOptions: PlatformSelectOption[]
  scopeKind: GroupScopeKind
  usePlatformSelect: boolean
}) {
  const platform = normalizeSpecialTextValue(member.platform)
  const itemId = normalizeSpecialTextValue(member.item_id)
  const platformValue = platform && platform !== '*' ? platform : platformOptions[0]?.value ?? 'qq'
  const chatTargetState = useExactChatTargetResolution(platformValue, itemId, member.rule_type, scopeKind === 'chat')
  const sharedMemoryChat = isSharedMemoryGroup ? findSharedMemoryChatStream(chatStreamOptions, member) : undefined
  const hasSharedMemoryTarget = isSharedMemoryGroup && Boolean(platform && itemId)
  const unmatchedSharedMemoryTarget = isSharedMemoryGroup && hasSharedMemoryTarget && !sharedMemoryChat
  const unmatchedSharedMemoryTargetKey = unmatchedSharedMemoryTarget ? expressionTargetOptionKey(member) : ''
  const openedUnmatchedTargetKeyRef = useRef(unmatchedSharedMemoryTargetKey)
  const [manualEditing, setManualEditing] = useState(unmatchedSharedMemoryTarget)
  const showSharedMemoryManualFields = isSharedMemoryGroup && manualEditing

  useEffect(() => {
    if (!isSharedMemoryGroup) {
      return
    }
    if (!unmatchedSharedMemoryTargetKey) {
      openedUnmatchedTargetKeyRef.current = ''
      setManualEditing(false)
      return
    }
    if (openedUnmatchedTargetKeyRef.current !== unmatchedSharedMemoryTargetKey) {
      openedUnmatchedTargetKeyRef.current = unmatchedSharedMemoryTargetKey
      setManualEditing(true)
    }
  }, [isSharedMemoryGroup, unmatchedSharedMemoryTargetKey])

  const updateScopeField = (patch: Partial<ExpressionGroupTarget>) => {
    onUpdateMember(groupIndex, memberIndex, {
      ...patch,
      ...(patch.platform !== undefined && scopeKind === 'platform'
        ? { item_id: '*' }
        : {}),
      ...(patch.item_id !== undefined &&
      scopeKind === 'chat' &&
      (!normalizeSpecialTextValue(member.platform) || member.platform === '*')
        ? { platform: resolveGroupPlatformValue(member, platformOptions) }
        : {}),
    })
  }

  return (
    <div className="space-y-2 rounded-md bg-background/80 px-2.5 py-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex min-w-0 flex-wrap items-center gap-1.5">
          {isSharedMemoryGroup ? (
            sharedMemoryChat ? (
              <>
                <span className="max-w-[24rem] truncate text-sm font-semibold">
                  {sharedMemoryChat.display_name || chatStreamTargetId(sharedMemoryChat)}
                </span>
                <Badge variant="secondary" className="px-1.5 py-0 text-[11px]">
                  {chatStreamTypeLabel(sharedMemoryChat.chat_type)}
                </Badge>
                <span className="max-w-[18rem] truncate font-mono text-[11px] text-muted-foreground">
                  {sharedMemoryChat.platform}:{chatStreamTargetId(sharedMemoryChat)}
                </span>
              </>
            ) : (
              <>
                <span className="truncate text-sm font-semibold text-muted-foreground">
                  {hasSharedMemoryTarget ? `${platform}:${itemId}` : '未选择聊天流'}
                </span>
                <Badge variant="secondary" className="px-1.5 py-0 text-[11px]">
                  {hasSharedMemoryTarget ? '未匹配' : '待选择'}
                </Badge>
              </>
            )
          ) : (
            <>
              <span className="truncate text-sm font-semibold">
                {groupScopeLabel(member)}
              </span>
              <Badge variant="secondary" className="px-1.5 py-0 text-[11px]">
                {ruleTypeLabel(member.rule_type)}
              </Badge>
              {scopeKind === 'chat' && <ChatTargetResolutionPreview state={chatTargetState} />}
            </>
          )}
        </div>
        <Button
          type="button"
          size="icon"
          variant="ghost"
          className="h-7 w-7 text-destructive hover:text-destructive"
          aria-label={`删除${groupLabel} ${groupIndex + 1} 的成员 ${memberIndex + 1}`}
          title="删除成员"
          onClick={() => onRemoveMember(groupIndex, memberIndex)}
        >
          <Trash2 className="h-4 w-4" />
        </Button>
      </div>

      {isSharedMemoryGroup ? (
        <div className="space-y-2">
          <div className="flex min-w-0 flex-col gap-2 sm:flex-row sm:items-center">
            <div className="min-w-0 flex-1">
              <SharedMemoryChatStreamSelect
                chats={chatStreamOptions}
                target={member}
                onSelect={(patch) => {
                  setManualEditing(false)
                  updateScopeField(patch)
                }}
              />
            </div>
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="h-8 shrink-0"
              onClick={() => setManualEditing((current) => !current)}
            >
              {showSharedMemoryManualFields ? '收起手动填写' : '手动填写'}
            </Button>
          </div>
          {showSharedMemoryManualFields && (
            <SharedMemoryManualFields
              target={member}
              onChange={updateScopeField}
            />
          )}
        </div>
      ) : scopeKind === 'global' ? (
        <div className="rounded-md border bg-background/70 px-2.5 py-2 text-xs text-muted-foreground">
          当前范围不需要填写平台或聊天流 ID。
        </div>
      ) : (
        <GroupScopeFields
          chatTargetState={chatTargetState}
          target={member}
          onFieldChange={updateScopeField}
          platformOptions={platformOptions}
          scopeKind={scopeKind}
          usePlatformSelect={usePlatformSelect}
        />
      )}
    </div>
  )
}

const parseTimelineMinute = (value: string) => {
  const match = /^(\d{1,2}):(\d{1,2})$/.exec(value.trim())
  if (!match) return null

  const hour = Number(match[1])
  const minute = Number(match[2])
  if (!Number.isInteger(hour) || !Number.isInteger(minute)) return null
  if (hour < 0 || hour > 23 || minute < 0 || minute > 59) return null

  return hour * 60 + minute
}

const formatTimelineMinute = (minute: number) => {
  const normalizedMinute = Math.max(0, Math.min(DAY_MINUTES - 1, Math.round(minute)))
  const hour = Math.floor(normalizedMinute / 60)
  const minuteInHour = normalizedMinute % 60
  return `${hour.toString().padStart(2, '0')}:${minuteInHour.toString().padStart(2, '0')}`
}

const parseTalkTimeRange = (time: string): TimelineRange | null => {
  const [startRaw, endRaw, extra] = time.trim().split('-')
  const start = startRaw ? parseTimelineMinute(startRaw) : null
  const end = endRaw ? parseTimelineMinute(endRaw) : null
  if (extra !== undefined || start === null || end === null) {
    return null
  }
  return { start, end }
}

const formatTalkTimeRange = (range: TimelineRange) => {
  return `${formatTimelineMinute(range.start)}-${formatTimelineMinute(range.end)}`
}

const getTimelineMinuteFromClient = (clientX: number, timelineElement: HTMLElement) => {
  const rect = timelineElement.getBoundingClientRect()
  const relativeX = Math.max(0, Math.min(rect.width, clientX - rect.left))
  const rawMinute = (relativeX / rect.width) * (DAY_MINUTES - 1)
  return Math.round(rawMinute / TIMELINE_DRAG_STEP_MINUTES) * TIMELINE_DRAG_STEP_MINUTES
}

const fullDaySegment = (): TimelineSegment => ({ left: 0, width: 100 })

const segmentFromMinutes = (start: number, end: number): TimelineSegment => ({
  left: (start / DAY_MINUTES) * 100,
  width: Math.max(((end - start) / DAY_MINUTES) * 100, 0.18),
})

const parseTalkTimeSegments = (time: string): { invalid: boolean; label: string; segments: TimelineSegment[] } => {
  const normalizedTime = time.trim()
  if (!normalizedTime) {
    return { invalid: false, label: '兜底', segments: [fullDaySegment()] }
  }
  if (normalizedTime === '*') {
    return { invalid: false, label: '强制全天', segments: [fullDaySegment()] }
  }

  const range = parseTalkTimeRange(normalizedTime)
  if (!range) {
    return { invalid: true, label: '时间格式错误', segments: [fullDaySegment()] }
  }
  const { end, start } = range

  if (start <= end) {
    return { invalid: false, label: normalizedTime, segments: [segmentFromMinutes(start, end + 1)] }
  }

  return {
    invalid: false,
    label: `${normalizedTime} 跨夜`,
    segments: [segmentFromMinutes(start, DAY_MINUTES), segmentFromMinutes(0, end + 1)],
  }
}

const normalizeTalkValue = (value: unknown) => {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return Math.max(0, Math.min(1, value))
  }
  if (typeof value === 'string') {
    const parsedValue = Number(value)
    if (Number.isFinite(parsedValue)) {
      return Math.max(0, Math.min(1, parsedValue))
    }
  }
  return 0
}

const talkValueColor = (value: number) => {
  if (value <= 0.25) return 'bg-sky-500'
  if (value <= 0.5) return 'bg-emerald-500'
  if (value <= 0.75) return 'bg-amber-500'
  return 'bg-rose-500'
}

const buildTalkTimelineItems = (items: Record<string, unknown>[]): TalkRuleTimelineItem[] => {
  return items.map((item, index) => {
    const time = typeof item.time === 'string' ? item.time : ''
    const parsedTime = parseTalkTimeSegments(time)
    const range = time.trim() && time.trim() !== '*' ? parseTalkTimeRange(time) : null
    const value = normalizeTalkValue(item.value)
    const scopeLabel = talkTargetScopeLabel(item)
    const { platform, itemId, ruleType } = talkRuleTargetValues(item)
    return {
      groupKey: talkRuleGroupKey(item),
      groupLabel: talkRuleGroupLabel(item),
      itemId,
      platform,
      ruleType,
      index,
      rawTime: time.trim(),
      scopeLabel,
      title: `轨道 ${index + 1}`,
      timeLabel: parsedTime.label,
      value,
      invalidTime: parsedTime.invalid,
      range,
      segments: parsedTime.segments,
    }
  })
}

const groupTalkTimelineItems = (items: TalkRuleTimelineItem[]): TalkRuleTimelineGroup[] => {
  const groups: TalkRuleTimelineGroup[] = []
  const groupMap = new Map<string, TalkRuleTimelineGroup>()

  for (const item of items) {
    const existingGroup = groupMap.get(item.groupKey)
    if (existingGroup) {
      existingGroup.items.push(item)
      continue
    }

    const nextGroup: TalkRuleTimelineGroup = {
      key: item.groupKey,
      label: item.groupLabel,
      itemId: item.itemId,
      platform: item.platform,
      ruleType: item.ruleType,
      scopeLabel: item.scopeLabel,
      hasFallback: item.rawTime === '',
      hasWildcard: item.rawTime === '*',
      items: [item],
    }
    groups.push(nextGroup)
    groupMap.set(item.groupKey, nextGroup)
  }

  for (const group of groups) {
    group.hasFallback = group.items.some((item) => item.rawTime === '')
    group.hasWildcard = group.items.some((item) => item.rawTime === '*')
  }

  return groups.map((group) => ({
    ...group,
    items: [...group.items].sort((a, b) => {
      const priority = (item: TalkRuleTimelineItem) => {
        if (item.rawTime === '') return 3
        if (item.rawTime === '*') return 1
        return 2
      }
      const priorityDiff = priority(a) - priority(b)
      return priorityDiff || a.index - b.index
    }),
  }))
}

const createTalkRuleForGroup = (
  group: Pick<TalkRuleTimelineGroup, 'itemId' | 'platform' | 'ruleType'>,
  time: string,
) => ({
  platform: group.platform,
  item_id: group.itemId,
  rule_type: group.ruleType || 'group',
  time,
  value: 0.5,
})

const normalizeTalkRuleItems = (
  items: Record<string, unknown>[],
  context?: { addedIndex?: number; changedIndex?: number },
) => {
  const preferredIndex = context?.changedIndex ?? context?.addedIndex
  const specialTimeOwner = new Map<string, number>()
  const duplicateIndexes = new Set<number>()

  items.forEach((item, index) => {
    const rawTime = typeof item.time === 'string' ? item.time.trim() : ''
    if (rawTime !== '' && rawTime !== '*') {
      return
    }

    const ownerKey = `${talkRuleGroupKey(item)}\u0000${rawTime}`
    const existingOwner = specialTimeOwner.get(ownerKey)
    if (existingOwner === undefined) {
      specialTimeOwner.set(ownerKey, index)
      return
    }

    if (index === preferredIndex) {
      duplicateIndexes.add(existingOwner)
      specialTimeOwner.set(ownerKey, index)
      return
    }

    duplicateIndexes.add(index)
  })

  if (duplicateIndexes.size === 0) {
    return items
  }

  return items.map((item, index) =>
    duplicateIndexes.has(index)
      ? {
          ...item,
          time: '00:00-23:59',
        }
      : item,
  )
}

const moveTalkRuleItem = (items: Record<string, unknown>[], fromIndex: number, toIndex: number) => {
  if (fromIndex === toIndex || fromIndex < 0 || toIndex < 0) {
    return items
  }
  if (fromIndex >= items.length || toIndex >= items.length) {
    return items
  }

  const nextItems = [...items]
  const [movedItem] = nextItems.splice(fromIndex, 1)
  nextItems.splice(toIndex, 0, movedItem)
  return nextItems
}

function TalkValueTimelineOverview({
  items,
  onAddItem,
  onItemFieldChange,
  onItemsChange,
  onRemoveItem,
}: {
  items: Record<string, unknown>[]
  onAddItem: (item?: Record<string, unknown>) => void
  onItemFieldChange: (index: number, fieldName: string, fieldValue: unknown) => void
  onItemsChange: (
    items: Record<string, unknown>[],
    context?: { addedIndex?: number; changedIndex?: number },
  ) => void
  onRemoveItem: (index: number) => void
}) {
  const timelineItems = buildTalkTimelineItems(items)
  const timelineGroups = groupTalkTimelineItems(timelineItems)
  const dragFrameRef = useRef<number | null>(null)
  const draggingTrackRef = useRef<{ groupKey: string; index: number } | null>(null)
  const lastDragTimeRef = useRef<string | null>(null)
  const pendingDragRef = useRef<{
    clientX: number
    edge: 'end' | 'start'
    item: TalkRuleTimelineItem
    timelineElement: HTMLElement
  } | null>(null)
  if (timelineItems.length === 0) {
    return null
  }

  const commitRangeUpdate = (
    item: TalkRuleTimelineItem,
    edge: 'end' | 'start',
    timelineElement: HTMLElement,
    clientX: number,
  ) => {
    if (!item.range) {
      return
    }

    const nextMinute = getTimelineMinuteFromClient(clientX, timelineElement)
    const nextRange =
      edge === 'start'
        ? { ...item.range, start: nextMinute }
        : { ...item.range, end: nextMinute }
    const nextTime = formatTalkTimeRange(nextRange)
    if (lastDragTimeRef.current === nextTime) {
      return
    }

    lastDragTimeRef.current = nextTime
    onItemFieldChange(item.index, 'time', nextTime)
  }

  const scheduleRangeUpdate = (
    event: PointerEvent<HTMLElement>,
    item: TalkRuleTimelineItem,
    edge: 'end' | 'start',
  ) => {
    const timelineElement = event.currentTarget.closest('[data-talk-timeline-track]')
    if (!(timelineElement instanceof HTMLElement)) {
      return
    }

    pendingDragRef.current = {
      clientX: event.clientX,
      edge,
      item,
      timelineElement,
    }

    if (dragFrameRef.current !== null) {
      return
    }

    dragFrameRef.current = window.requestAnimationFrame(() => {
      dragFrameRef.current = null
      const pendingDrag = pendingDragRef.current
      if (!pendingDrag) {
        return
      }
      commitRangeUpdate(
        pendingDrag.item,
        pendingDrag.edge,
        pendingDrag.timelineElement,
        pendingDrag.clientX,
      )
    })
  }

  const startRangeDrag = (
    event: PointerEvent<HTMLElement>,
    item: TalkRuleTimelineItem,
    edge: 'end' | 'start',
  ) => {
    event.preventDefault()
    lastDragTimeRef.current = null
    event.currentTarget.setPointerCapture(event.pointerId)
    scheduleRangeUpdate(event, item, edge)
  }

  const reorderTrack = (targetItem: TalkRuleTimelineItem) => {
    const draggingTrack = draggingTrackRef.current
    if (!draggingTrack || draggingTrack.groupKey !== targetItem.groupKey) {
      return
    }
    if (draggingTrack.index === targetItem.index || !targetItem.range) {
      return
    }

    const nextItems = moveTalkRuleItem(items, draggingTrack.index, targetItem.index)
    onItemsChange(nextItems, { changedIndex: targetItem.index })
    draggingTrackRef.current = null
  }

  return (
    <div className="overflow-hidden rounded-lg border bg-muted/20">
      <div className="border-b bg-background/70 px-3 py-2">
        <div className="flex flex-wrap items-center gap-2">
          <h5 className="text-sm font-semibold">时间轴视图</h5>
          <Badge variant="secondary">聊天区域 {timelineGroups.length}</Badge>
          <Badge variant="outline">轨道 {timelineItems.length}</Badge>
        </div>
      </div>
      <div className="overflow-x-auto">
        <div className="min-w-[540px] p-2.5">
          <div className="grid grid-cols-[5.5rem_minmax(16rem,1fr)_6.5rem] gap-2 pb-1.5 text-[11px] text-muted-foreground">
            <div>轨道</div>
            <div className="relative h-4 px-1">
              {TIMELINE_TICKS.map((hour) => (
                <span
                  key={hour}
                  className="absolute -translate-x-1/2"
                  style={{ left: `${(hour / 24) * 100}%` }}
                >
                  {hour.toString().padStart(2, '0')}:00
                </span>
              ))}
            </div>
            <div>频率</div>
          </div>
          <div className="space-y-2.5">
            {timelineGroups.map((group) => (
              <div key={group.key} className="overflow-hidden rounded-lg border bg-card/60">
                <div className="flex flex-wrap items-center gap-1.5 border-b bg-background/70 px-2 py-1.5">
                  <div className="min-w-0 flex-1 truncate text-xs font-semibold">{group.label}</div>
                  <Badge variant="secondary">{group.scopeLabel}</Badge>
                  <Badge variant="outline">{group.items.length} 轨道</Badge>
                  <div className="ml-auto flex flex-wrap gap-1">
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      className="h-7 px-2 text-xs"
                      onClick={() => onAddItem(createTalkRuleForGroup(group, '00:00-23:59'))}
                    >
                      <Plus className="mr-1 h-3 w-3" />
                      时间段
                    </Button>
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      className="h-7 px-2 text-xs"
                      disabled={group.hasFallback}
                      onClick={() => onAddItem(createTalkRuleForGroup(group, ''))}
                    >
                      <Plus className="mr-1 h-3 w-3" />
                      兜底
                    </Button>
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      className="h-7 px-2 text-xs"
                      disabled={group.hasWildcard}
                      onClick={() => onAddItem(createTalkRuleForGroup(group, '*'))}
                    >
                      <Plus className="mr-1 h-3 w-3" />
                      *
                    </Button>
                  </div>
                </div>
                <div className="space-y-1.5 p-2">
                  {group.items.map((item, trackIndex) => (
                    <div
                      key={item.index}
                      className="grid min-h-12 grid-cols-[5.5rem_minmax(16rem,1fr)_6.5rem] items-center gap-2 rounded-md bg-muted/25 px-2 py-1.5"
                      onDragOver={(event) => {
                        if (item.range && draggingTrackRef.current?.groupKey === item.groupKey) {
                          event.preventDefault()
                        }
                      }}
                      onDrop={(event) => {
                        event.preventDefault()
                        reorderTrack(item)
                      }}
                    >
                      <div className="flex min-w-0 items-center gap-2">
                        <button
                          type="button"
                          draggable={Boolean(item.range)}
                          disabled={!item.range}
                          className="inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:cursor-not-allowed disabled:opacity-35"
                          aria-label={`拖动${group.label}轨道 ${trackIndex + 1} 调整顺序`}
                          onDragEnd={() => {
                            draggingTrackRef.current = null
                          }}
                          onDragStart={(event) => {
                            if (!item.range) {
                              event.preventDefault()
                              return
                            }
                            draggingTrackRef.current = {
                              groupKey: item.groupKey,
                              index: item.index,
                            }
                            event.dataTransfer.effectAllowed = 'move'
                          }}
                        >
                          <GripVertical className="h-3.5 w-3.5" />
                        </button>
                        <div className="min-w-0">
                          <div className="truncate text-xs font-medium">轨道 {trackIndex + 1}</div>
                        <div
                          className={item.invalidTime ? 'text-[11px] text-destructive' : 'text-[11px] text-muted-foreground'}
                        >
                          {item.timeLabel}
                        </div>
                        </div>
                      </div>
                      <div
                        className="relative h-7 rounded-md border bg-background"
                        data-talk-timeline-track
                      >
                        {TIMELINE_TICKS.slice(1, -1).map((hour) => (
                          <span
                            key={hour}
                            className="absolute top-0 h-full border-l border-dashed border-muted-foreground/20"
                            style={{ left: `${(hour / 24) * 100}%` }}
                          />
                        ))}
                        {item.segments.map((segment, segmentIndex) => (
                          <span
                            key={segmentIndex}
                            className={`absolute top-1/2 h-4 -translate-y-1/2 rounded-sm ${talkValueColor(item.value)} ${
                              item.invalidTime ? 'opacity-35' : 'opacity-85'
                            }`}
                            style={{
                              left: `${segment.left}%`,
                              width: `${segment.width}%`,
                            }}
                            title={`${item.timeLabel} · 频率 ${item.value.toFixed(2)}`}
                          />
                        ))}
                        {item.range && !item.invalidTime && (
                          <>
                            <button
                              type="button"
                              className="absolute top-1/2 h-6 w-2 -translate-x-1/2 -translate-y-1/2 rounded-sm border border-background bg-foreground/80 shadow-sm cursor-ew-resize"
                              style={{ left: `${(item.range.start / DAY_MINUTES) * 100}%` }}
                              aria-label={`调整${group.label}轨道 ${trackIndex + 1} 开始时间`}
                              onPointerDown={(event) => startRangeDrag(event, item, 'start')}
                              onPointerMove={(event) => {
                                if (event.currentTarget.hasPointerCapture(event.pointerId)) {
                                  scheduleRangeUpdate(event, item, 'start')
                                }
                              }}
                              onPointerUp={(event) => {
                                pendingDragRef.current = null
                                event.currentTarget.releasePointerCapture(event.pointerId)
                              }}
                            />
                            <button
                              type="button"
                              className="absolute top-1/2 h-6 w-2 -translate-x-1/2 -translate-y-1/2 rounded-sm border border-background bg-foreground/80 shadow-sm cursor-ew-resize"
                              style={{ left: `${((item.range.end + 1) / DAY_MINUTES) * 100}%` }}
                              aria-label={`调整${group.label}轨道 ${trackIndex + 1} 结束时间`}
                              onPointerDown={(event) => startRangeDrag(event, item, 'end')}
                              onPointerMove={(event) => {
                                if (event.currentTarget.hasPointerCapture(event.pointerId)) {
                                  scheduleRangeUpdate(event, item, 'end')
                                }
                              }}
                              onPointerUp={(event) => {
                                pendingDragRef.current = null
                                event.currentTarget.releasePointerCapture(event.pointerId)
                              }}
                            />
                          </>
                        )}
                      </div>
                      <div className="grid grid-cols-[minmax(0,1fr)_1.75rem] items-center gap-1.5">
                        <div>
                          <Slider
                            value={[item.value]}
                            min={0}
                            max={1}
                            step={0.01}
                            onValueChange={(values) => onItemFieldChange(item.index, 'value', values[0])}
                            data-dashboard-slider="config"
                            data-dashboard-slider-value-format="fixed-2"
                          />
                        </div>
                        <Button
                          type="button"
                          size="icon"
                          variant="ghost"
                          className="h-7 w-7 text-destructive hover:text-destructive"
                          aria-label={`删除${group.label} 轨道 ${trackIndex + 1}`}
                          onClick={() => onRemoveItem(item.index)}
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </Button>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}

function TalkValueGroupedRuleEditor({
  emptyText,
  items,
  onItemFieldChange,
  onItemsChange,
  onRemoveItem,
}: {
  emptyText: string
  items: Record<string, unknown>[]
  onItemFieldChange: (index: number, fieldName: string, fieldValue: unknown) => void
  onItemsChange: (
    items: Record<string, unknown>[],
    context?: { addedIndex?: number; changedIndex?: number },
  ) => void
  onRemoveItem: (index: number) => void
}) {
  const timelineGroups = groupTalkTimelineItems(buildTalkTimelineItems(items))
  if (timelineGroups.length === 0) {
    return (
      <div className="rounded-md border border-dashed border-muted-foreground/25 bg-muted/10 p-6 text-center text-sm text-muted-foreground">
        {emptyText}
      </div>
    )
  }

  const updateGroupField = (
    group: TalkRuleTimelineGroup,
    fieldName: 'item_id' | 'platform' | 'rule_type',
    fieldValue: string,
  ) => {
    const groupIndexes = new Set(group.items.map((item) => item.index))
    const nextItems = items.map((item, index) => {
      if (!groupIndexes.has(index)) {
        return item
      }
      return {
        ...item,
        [fieldName]: fieldValue,
      }
    })
    onItemsChange(nextItems, { changedIndex: group.items[0]?.index })
  }

  return (
    <div className="space-y-4">
      {timelineGroups.map((group) => (
        <div key={group.key} className="space-y-4 rounded-lg border bg-card/40 p-4">
          <div className="flex flex-wrap items-center gap-2">
            <div className="min-w-0 flex-1">
              <div className="truncate text-sm font-semibold">{group.label}</div>
              <div className="text-xs text-muted-foreground">{group.items.length} 条轨道</div>
            </div>
            <Badge variant="secondary">{group.scopeLabel}</Badge>
          </div>

          <div className="grid gap-3 md:grid-cols-3">
            <div className="space-y-2">
              <Label className="text-xs font-medium">平台</Label>
              <Input
                value={group.platform}
                placeholder="留空表示全局，* 表示通配"
                onChange={(event) => updateGroupField(group, 'platform', event.target.value)}
              />
            </div>
            <div className="space-y-2">
              <Label className="text-xs font-medium">聊天流 ID</Label>
              <Input
                value={group.itemId}
                placeholder="留空表示全局，* 表示通配"
                onChange={(event) => updateGroupField(group, 'item_id', event.target.value)}
              />
            </div>
            <div className="space-y-2">
              <Label className="text-xs font-medium">聊天类型</Label>
              <Select
                value={group.ruleType === 'private' ? 'private' : 'group'}
                onValueChange={(value) => updateGroupField(group, 'rule_type', value)}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="group">群聊</SelectItem>
                  <SelectItem value="private">私聊</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>

          <div className="space-y-2">
            {group.items.map((item, trackIndex) => {
              const isFallback = item.rawTime === ''
              const isWildcard = item.rawTime === '*'
              const canUseFallback = isFallback || !group.hasFallback
              const canUseWildcard = isWildcard || !group.hasWildcard

              return (
                <div
                  key={item.index}
                  className="grid gap-3 rounded-md border bg-muted/20 p-3 lg:grid-cols-[7rem_minmax(16rem,1fr)_14rem_2.5rem]"
                >
                  <div className="flex items-center">
                    <span className="text-sm font-medium">轨道 {trackIndex + 1}</span>
                  </div>
                  <div className="space-y-2">
                    <div className="grid grid-cols-3 gap-2">
                      <Button
                        type="button"
                        size="sm"
                        variant={isFallback ? 'default' : 'outline'}
                        disabled={!canUseFallback}
                        onClick={() => onItemFieldChange(item.index, 'time', '')}
                      >
                        兜底
                      </Button>
                      <Button
                        type="button"
                        size="sm"
                        variant={!isFallback && !isWildcard ? 'default' : 'outline'}
                        onClick={() =>
                          onItemFieldChange(
                            item.index,
                            'time',
                            !isFallback && !isWildcard ? item.rawTime : '00:00-23:59',
                          )
                        }
                      >
                        时间段
                      </Button>
                      <Button
                        type="button"
                        size="sm"
                        variant={isWildcard ? 'default' : 'outline'}
                        disabled={!canUseWildcard}
                        onClick={() => onItemFieldChange(item.index, 'time', '*')}
                      >
                        *
                      </Button>
                    </div>
                    <Input
                      value={!isFallback && !isWildcard ? item.rawTime : ''}
                      disabled={isFallback || isWildcard}
                      placeholder="HH:MM-HH:MM"
                      onChange={(event) => onItemFieldChange(item.index, 'time', event.target.value)}
                    />
                  </div>
                  <div className="space-y-2">
                    <Label className="text-xs font-medium">发言频率</Label>
                    <div className="flex items-center gap-2">
                      <Slider
                        value={[item.value]}
                        min={0}
                        max={1}
                        step={0.01}
                        onValueChange={(values) => onItemFieldChange(item.index, 'value', values[0])}
                        data-dashboard-slider="config"
                        data-dashboard-slider-value-format="fixed-2"
                      />
                      <Input
                        type="number"
                        min={0}
                        max={1}
                        step={0.01}
                        value={item.value}
                        onChange={(event) => {
                          const nextValue = Number(event.target.value)
                          if (Number.isFinite(nextValue)) {
                            onItemFieldChange(item.index, 'value', Math.max(0, Math.min(1, nextValue)))
                          }
                        }}
                        className="h-8 w-20 text-right"
                      />
                    </div>
                  </div>
                  <div className="flex items-center justify-end">
                    <Button
                      type="button"
                      size="icon"
                      variant="ghost"
                      className="text-destructive hover:text-destructive"
                      aria-label={`删除${group.label}轨道 ${trackIndex + 1}`}
                      onClick={() => onRemoveItem(item.index)}
                    >
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      ))}
    </div>
  )
}

type TalkValueEditorMode = 'grouped' | 'timeline'

const createEmptyTalkRuleDraft = () => ({
  itemId: '',
  platform: '',
  ruleType: 'group',
})

function TalkValueRuleEditor({
  emptyText,
  items,
  onAddItem,
  onItemFieldChange,
  onItemsChange,
  onRemoveItem,
}: {
  emptyText: string
  items: Record<string, unknown>[]
  onAddItem: (item?: Record<string, unknown>) => void
  onItemFieldChange: (index: number, fieldName: string, fieldValue: unknown) => void
  onItemsChange: (
    items: Record<string, unknown>[],
    context?: { addedIndex?: number; changedIndex?: number },
  ) => void
  onRemoveItem: (index: number) => void
}) {
  const [mode, setMode] = useState<TalkValueEditorMode>('timeline')
  const [addDialogOpen, setAddDialogOpen] = useState(false)
  const [addDraft, setAddDraft] = useState(createEmptyTalkRuleDraft)
  const canSubmitAddRule = addDraft.platform.trim().length > 0 && addDraft.itemId.trim().length > 0

  const handleAddDialogOpenChange = (open: boolean) => {
    setAddDialogOpen(open)
    if (!open) {
      setAddDraft(createEmptyTalkRuleDraft())
    }
  }

  const handleSubmitAddRule = () => {
    if (!canSubmitAddRule) {
      return
    }

    onAddItem({
      platform: addDraft.platform.trim(),
      item_id: addDraft.itemId.trim(),
      rule_type: addDraft.ruleType,
      time: '00:00-23:59',
      value: 0.5,
    })
    handleAddDialogOpenChange(false)
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Button
          type="button"
          size="sm"
          variant="outline"
          onClick={() => setAddDialogOpen(true)}
        >
          <Plus className="mr-1 h-4 w-4" />
          添加发言频率规则
        </Button>
        <div className="inline-flex rounded-md border bg-background p-1">
          <Button
            type="button"
            size="sm"
            variant={mode === 'timeline' ? 'default' : 'ghost'}
            onClick={() => setMode('timeline')}
          >
            可视化轨道
          </Button>
          <Button
            type="button"
            size="sm"
            variant={mode === 'grouped' ? 'default' : 'ghost'}
            onClick={() => setMode('grouped')}
          >
            合并编辑
          </Button>
        </div>
      </div>
      <Dialog open={addDialogOpen} onOpenChange={handleAddDialogOpenChange}>
        <DialogContent className="sm:max-w-md" confirmOnEnter>
          <DialogHeader>
            <DialogTitle>添加发言频率规则</DialogTitle>
            <DialogDescription>
              先指定平台、聊天流 ID 和聊天类型，再创建该聊天区域的默认时间段轨道。
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-2">
              <Label className="text-xs font-medium">平台</Label>
              <Input
                value={addDraft.platform}
                placeholder="例如 qq"
                onChange={(event) =>
                  setAddDraft((current) => ({
                    ...current,
                    platform: event.target.value,
                  }))
                }
              />
            </div>
            <div className="space-y-2">
              <Label className="text-xs font-medium">聊天流 ID</Label>
              <Input
                value={addDraft.itemId}
                placeholder="群号或用户 ID"
                onChange={(event) =>
                  setAddDraft((current) => ({
                    ...current,
                    itemId: event.target.value,
                  }))
                }
              />
            </div>
            <div className="space-y-2">
              <Label className="text-xs font-medium">聊天类型</Label>
              <Select
                value={addDraft.ruleType}
                onValueChange={(value) =>
                  setAddDraft((current) => ({
                    ...current,
                    ruleType: value,
                  }))
                }
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="group">群聊</SelectItem>
                  <SelectItem value="private">私聊</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => handleAddDialogOpenChange(false)}
            >
              取消
            </Button>
            <Button
              type="button"
              data-dialog-action="confirm"
              disabled={!canSubmitAddRule}
              onClick={handleSubmitAddRule}
            >
              添加
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      {mode === 'timeline' ? (
        <TalkValueTimelineOverview
          items={items}
          onAddItem={onAddItem}
          onItemFieldChange={onItemFieldChange}
          onItemsChange={onItemsChange}
          onRemoveItem={onRemoveItem}
        />
      ) : (
        <TalkValueGroupedRuleEditor
          emptyText={emptyText}
          items={items}
          onItemFieldChange={onItemFieldChange}
          onItemsChange={onItemsChange}
          onRemoveItem={onRemoveItem}
        />
      )}
    </div>
  )
}

const truncate = (text: string, max = 32) => {
  if (text.length <= max) return text
  return `${text.slice(0, max)}…`
}

const collectStringList = (value: unknown): string[] => {
  if (!Array.isArray(value)) return []
  return value
    .map((item) => (typeof item === 'string' ? item.trim() : ''))
    .filter((item) => item.length > 0)
}

const normalizeExpressionRuleType = (value: unknown): ExpressionRuleType => {
  return value === 'private' ? 'private' : 'group'
}

const normalizeExpressionTarget = (value: unknown): ExpressionGroupTarget => {
  const source =
    value && typeof value === 'object'
      ? (value as Record<string, unknown>)
      : {}
  return {
    platform:
      typeof source.platform === 'string' ? source.platform.trim() : 'qq',
    item_id:
      typeof source.item_id === 'string' ? source.item_id.trim() : '',
    rule_type: normalizeExpressionRuleType(source.rule_type ?? source.type),
  }
}

const normalizeExpressionGroups = (value: unknown): ExpressionGroupValue[] => {
  if (!Array.isArray(value)) return []
  return value.map((item) => {
    const source =
      item && typeof item === 'object'
        ? (item as Record<string, unknown>)
        : {}
    let rawMembers: unknown[] = []
    if (Array.isArray(source.targets)) {
      rawMembers = source.targets
    } else if (Array.isArray(source.expression_groups)) {
      rawMembers = source.expression_groups
    } else if (Array.isArray(source.jargon_groups)) {
      rawMembers = source.jargon_groups
    } else if (Array.isArray(source.behavior_groups)) {
      rawMembers = source.behavior_groups
    }
    const members = rawMembers.map(normalizeExpressionTarget)
    return { targets: members }
  })
}

const createExpressionTarget = (
  scopeKind: GroupScopeKind,
  platformOptions: PlatformSelectOption[],
  ruleType: ExpressionRuleType = 'group',
): ExpressionGroupTarget => {
  const baseTarget: ExpressionGroupTarget = {
    platform: platformOptions[0]?.value ?? 'qq',
    item_id: '',
    rule_type: ruleType,
  }
  return {
    ...baseTarget,
    ...buildGroupTargetPatch(scopeKind, baseTarget, platformOptions),
  }
}

const normalizePlatformAccounts = (value: unknown): string[] => {
  if (!Array.isArray(value)) return []
  return value.map((item) => String(item ?? ''))
}

const parsePlatformAccount = (value: string): PlatformAccountRow => {
  const separatorIndex = value.indexOf(':')
  if (separatorIndex < 0) {
    return { platform: '', account: value }
  }
  return {
    platform: value.slice(0, separatorIndex),
    account: value.slice(separatorIndex + 1),
  }
}

const formatPlatformAccount = (row: PlatformAccountRow): string => {
  const platform = row.platform.trim()
  const account = row.account.trim()
  if (!platform) return account
  if (!account) return `${platform}:`
  return `${platform}:${account}`
}

interface StringListHookOptions {
  addLabel: string
  emptyText: string
  label: string
  multiline?: boolean
  placeholder?: string
}

function createStringListHook(options: StringListHookOptions): FieldHookComponent {
  return ({ onChange, schema, value }) => {
    const items = Array.isArray(value) ? value.map((item) => String(item ?? '')) : []

    const updateItems = (nextItems: string[]) => {
      onChange?.(nextItems)
    }

    const addItem = () => {
      updateItems([...items, ''])
    }

    const removeItem = (itemIndex: number) => {
      updateItems(items.filter((_, index) => index !== itemIndex))
    }

    const updateItem = (itemIndex: number, nextValue: string) => {
      updateItems(items.map((item, index) => (index === itemIndex ? nextValue : item)))
    }

    const InputComponent = options.multiline ? Textarea : Input

    return (
      <div className="space-y-3">
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
          <Label className={fieldTitleClassName(schema, 'text-[15px] leading-6')}>
            {options.label}
          </Label>
          <Button type="button" size="sm" variant="outline" onClick={addItem}>
            <Plus className="mr-2 h-4 w-4" />
            {options.addLabel}
          </Button>
        </div>

        {items.length === 0 ? (
          <div className="rounded-md border border-dashed bg-muted/30 px-4 py-5 text-center text-sm text-muted-foreground">
            {options.emptyText}
          </div>
        ) : (
          <div className="space-y-2">
            {items.map((item, itemIndex) => (
              <div
                key={itemIndex}
                className="grid gap-2 rounded-md border bg-muted/20 p-3 sm:grid-cols-[minmax(0,1fr)_2.5rem]"
              >
                <InputComponent
                  value={item}
                  placeholder={options.placeholder}
                  onChange={(event) => updateItem(itemIndex, event.target.value)}
                  {...(options.multiline ? { rows: 2 } : {})}
                />
                <div className="flex items-start justify-end">
                  <Button
                    type="button"
                    size="icon"
                    variant="ghost"
                    aria-label={`删除${options.label} ${itemIndex + 1}`}
                    onClick={() => removeItem(itemIndex)}
                  >
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    )
  }
}

export const AliasNamesHook = createStringListHook({
  addLabel: '添加别名',
  emptyText: '暂无别名。',
  label: '别名',
  placeholder: '小麦',
})

export const MultipleReplyStyleHook = createStringListHook({
  addLabel: '添加表达风格',
  emptyText: '暂无备用表达风格。',
  label: '备用表达风格',
  multiline: true,
  placeholder: '输入一种备用表达风格',
})

export const ChatTalkValueRulesHook = createListItemEditorHook({
  addLabel: '添加发言频率规则',
  addButtonPlacement: 'none',
  collapseWhen: ({ parentValues }) => parentValues?.enable_talk_value_rules === false,
  collapsedText: '动态发言频率规则未启用，规则列表已折叠。展开后仍可查看或编辑已有规则。',
  expandLabel: '展开规则',
  collapseLabel: '折叠规则',
  helperText: '可按平台/聊天流/时段分别配置发言频率。平台和聊天流都空表示全局；只填平台或聊天流表示对应默认值；* 表示通配覆盖。时间留空表示兜底，* 表示强制全天。',
  emptyText: '尚未配置任何规则，将使用全局默认频率。',
  collapseButtonDisplay: 'icon',
  fieldRows: [
    ['platform', 'item_id', 'rule_type'],
    ['time', 'value'],
  ],
  normalizeItems: normalizeTalkRuleItems,
  renderItems: ({
    emptyText,
    items,
    onAddItem,
    onItemFieldChange,
    onItemsChange,
    onRemoveItem,
  }) => (
    <TalkValueRuleEditor
      emptyText={emptyText}
      items={items}
      onAddItem={onAddItem}
      onItemFieldChange={onItemFieldChange}
      onItemsChange={onItemsChange}
      onRemoveItem={onRemoveItem}
    />
  ),
  itemTitle: (item) => {
    const rawTime = typeof item.time === 'string' ? item.time.trim() : ''
    const time = rawTime === '' ? '兜底' : rawTime === '*' ? '强制全天' : rawTime
    const value =
      typeof item.value === 'number' ? item.value.toFixed(2) : '—'
    return `${platformLabel(item)} · ${ruleTypeLabel(item.rule_type)} · ${time} · 频率 ${value}`
  },
})

export const ChatPromptsHook = createListItemEditorHook({
  addLabel: '添加额外 Prompt',
  helperText: '为指定平台和聊天流添加额外提示。platform、item_id 和 prompt 同时留空时表示空条目；填写任意一项后这三项都需要填写。',
  emptyText: '尚未配置任何聊天额外 Prompt。',
  addButtonPlacement: 'top',
  fieldRows: [
    ['platform', 'item_id'],
    ['rule_type'],
  ],
  fieldSchemaOverrides: {
    item_id: {
      'x-input-width': '6.5rem',
      'x-layout': 'inline-right',
    },
    platform: {
      'x-input-width': '5.5rem',
      'x-layout': 'inline-right',
    },
    prompt: {
      'x-textarea-min-height': 38,
      'x-textarea-rows': 1,
    },
    rule_type: {
      'x-input-width': '5.5rem',
      'x-layout': 'inline-right',
    },
  },
  iconName: 'file-text',
  itemTitle: (item) => {
    return `${platformLabel(item)} · ${ruleTypeLabel(item.rule_type)}`
  },
})

export const ExpressionLearningListHook = createListItemEditorHook({
  addLabel: '添加学习规则',
  addButtonPlacement: 'none',
  infoText: '可以单独为每个聊天开启学习和使用；平台和聊天流 ID 都留空表示全局默认，只填平台表示平台兜底，* 表示通配。',
  emptyText: '尚未配置任何学习规则。',
  fallbackNestedSchema: LEARNING_ITEM_FALLBACK_SCHEMA,
  renderItems: ({
    emptyText,
    items,
    onAddItem,
    onItemFieldChange,
    onRemoveItem,
  }) => (
    <LearningRuleEditor
      emptyText={emptyText}
      items={items}
      onAddItem={onAddItem}
      onItemFieldChange={onItemFieldChange}
      onRemoveItem={onRemoveItem}
    />
  ),
  itemTitle: (item) => {
    return `${learningScopeLabel(item)} · ${ruleTypeLabel(item.type)} · ${learningFlagLabel(item)}`
  },
})

export const JargonLearningListHook = createListItemEditorHook({
  addLabel: '添加黑话学习规则',
  addButtonPlacement: 'none',
  infoText: '可以单独为每个聊天开启黑话学习和使用；平台和聊天流 ID 都留空表示全局默认，只填平台表示平台兜底，* 表示通配。平台下拉来自基础设置中已定义的平台。',
  emptyText: '尚未配置任何黑话学习规则。',
  fallbackNestedSchema: LEARNING_ITEM_FALLBACK_SCHEMA,
  renderItems: ({
    emptyText,
    items,
    onAddItem,
    onItemFieldChange,
    onRemoveItem,
  }) => (
    <LearningRuleEditor
      emptyText={emptyText}
      items={items}
      onAddItem={onAddItem}
      onItemFieldChange={onItemFieldChange}
      onRemoveItem={onRemoveItem}
      usePlatformSelect
    />
  ),
  itemTitle: (item) => {
    return `${learningScopeLabel(item)} · ${ruleTypeLabel(item.type)} · ${learningFlagLabel(item)}`
  },
})

export const BehaviorLearningListHook = createListItemEditorHook({
  addLabel: '添加行为学习规则',
  addButtonPlacement: 'none',
  infoText: '可以单独为每个聊天开启行为经验的学习和使用；平台和聊天流 ID 都留空表示全局默认，只填平台表示平台兜底，* 表示通配。',
  emptyText: '尚未配置任何行为学习规则。',
  fallbackNestedSchema: LEARNING_ITEM_FALLBACK_SCHEMA,
  renderItems: ({
    emptyText,
    items,
    onAddItem,
    onItemFieldChange,
    onRemoveItem,
  }) => (
    <LearningRuleEditor
      emptyText={emptyText}
      items={items}
      onAddItem={onAddItem}
      onItemFieldChange={onItemFieldChange}
      onRemoveItem={onRemoveItem}
    />
  ),
  itemTitle: (item) => {
    return `${learningScopeLabel(item)} · ${ruleTypeLabel(item.type)} · ${learningFlagLabel(item)}`
  },
})

export const FocusWhitelistHook = createListItemEditorHook({
  addLabel: '添加 Focus 白名单',
  infoText: '配置后只有命中的聊天流会进入 Focus；留空表示所有符合聊天类型开关的聊天都可进入 Focus。',
  emptyText: '尚未配置 Focus 白名单。',
  fallbackNestedSchema: LEARNING_ITEM_FALLBACK_SCHEMA,
  fieldRows: [['platform', 'item_id', 'type']],
  itemTitle: (item) => {
    return `${platformLabel(item)} · ${ruleTypeLabel(item.type)}`
  },
})

export const HiddenFieldHook: FieldHookComponent = () => null

export const BotPlatformAccountsHook: FieldHookComponent = ({
  onChange,
  onParentChange,
  parentValues,
  value,
}) => {
  const primaryPlatform = typeof value === 'string' ? value : ''
  const qqAccountValue = parentValues?.qq_account
  const qqAccount =
    typeof qqAccountValue === 'string' || typeof qqAccountValue === 'number'
      ? String(qqAccountValue)
      : ''
  const platforms = normalizePlatformAccounts(parentValues?.platforms)
  const rows = platforms.map(parsePlatformAccount)

  const updateRows = (nextRows: PlatformAccountRow[]) => {
    onParentChange?.('platforms', nextRows.map(formatPlatformAccount))
  }

  const addRow = () => {
    updateRows([...rows, { platform: '', account: '' }])
  }

  const removeRow = (rowIndex: number) => {
    updateRows(rows.filter((_, index) => index !== rowIndex))
  }

  const updateRow = (rowIndex: number, patch: Partial<PlatformAccountRow>) => {
    updateRows(rows.map((row, index) => (index === rowIndex ? { ...row, ...patch } : row)))
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between gap-2">
        <div className="min-w-0 space-y-1">
          <Label className="text-[15px] font-semibold leading-6">平台账号</Label>
        </div>
        <Button
          type="button"
          size="icon"
          variant="outline"
          className="shrink-0"
          aria-label="添加平台"
          title="添加平台"
          onClick={addRow}
        >
          <Plus className="h-4 w-4" />
        </Button>
      </div>

      <div className="space-y-2">
        <div className={PLATFORM_ACCOUNT_ROW_GRID_CLASS}>
          <div className="min-w-0 space-y-1">
            <Label className="text-xs">平台</Label>
            <Input
              className="min-w-0"
              value={primaryPlatform}
              placeholder="qq"
              onChange={(event) => onChange?.(event.target.value)}
            />
          </div>
          <div className="min-w-0 space-y-1">
            <Label className="text-xs">账号</Label>
            <Input
              className="min-w-0 font-mono"
              value={qqAccount}
              placeholder="2814567326"
              onChange={(event) => onParentChange?.('qq_account', event.target.value)}
            />
          </div>
          <div className="flex shrink-0 items-end justify-end">
            <span className="rounded-md bg-primary/10 px-2 py-1 text-xs font-medium text-primary">
              主
            </span>
          </div>
        </div>

        {rows.map((row, rowIndex) => (
          <div
            key={rowIndex}
            className={PLATFORM_ACCOUNT_ROW_GRID_CLASS}
          >
            <div className="min-w-0 space-y-1">
              <Label className="text-xs">平台</Label>
              <Input
                className="min-w-0"
                value={row.platform}
                placeholder="wx"
                onChange={(event) => updateRow(rowIndex, { platform: event.target.value })}
              />
            </div>
            <div className="min-w-0 space-y-1">
              <Label className="text-xs">账号</Label>
              <Input
                className="min-w-0 font-mono"
                value={row.account}
                placeholder="114514"
                onChange={(event) => updateRow(rowIndex, { account: event.target.value })}
              />
            </div>
            <div className="flex shrink-0 items-end justify-end">
              <Button
                type="button"
                size="icon"
                variant="ghost"
                aria-label={`删除其他平台 ${rowIndex + 1}`}
                onClick={() => removeRow(rowIndex)}
              >
                <Trash2 className="h-4 w-4" />
              </Button>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

export const KeywordRulesHook = createListItemEditorHook({
  addLabel: '添加关键词规则',
  helperText: '匹配命中后会把反应提示作为额外上下文。关键词至少填写一条。',
  emptyText: '尚未添加任何关键词规则。',
  visibleFields: ['keywords', 'reaction'],
  normalizeItems: (items) =>
    items.map((item) => ({
      ...item,
      regex: [],
    })),
  itemTitle: (item) => {
    const keywords = collectStringList(item.keywords)
    const reaction =
      typeof item.reaction === 'string' ? item.reaction.trim() : ''
    const left = keywords.length
      ? `关键词 ${keywords.length} 条`
      : '未配置关键词'
    const right = reaction ? `→ ${truncate(reaction)}` : '→ 未填写反应'
    return `${left} ${right}`
  },
})

export const RegexRulesHook = createListItemEditorHook({
  addLabel: '添加正则规则',
  helperText: '正则模式按 Python 语法编写，命中时把反应提示作为额外上下文。',
  emptyText: '尚未添加任何正则规则。',
  visibleFields: ['regex', 'reaction'],
  normalizeItems: (items) =>
    items.map((item) => ({
      ...item,
      keywords: [],
    })),
  itemTitle: (item) => {
    const regex = collectStringList(item.regex)
    const reaction =
      typeof item.reaction === 'string' ? item.reaction.trim() : ''
    const left = regex.length
      ? `正则 ${regex.length} 条`
      : '未配置正则'
    const right = reaction ? `→ ${truncate(reaction)}` : '→ 未填写反应'
    return `${left} ${right}`
  },
})

export const ExpressionGroupsHook: FieldHookComponent = ({ fieldPath, onChange, onParentChange, parentValues, schema, value }) => {
  const groups = normalizeExpressionGroups(value)
  const isJargonGroup = fieldPath?.includes('jargon') ?? false
  const isBehaviorGroup = fieldPath?.includes('behavior') ?? false
  const isSharedMemoryGroup = fieldPath?.includes('shared_memory_groups') ?? false
  const isFocusGroup = fieldPath?.includes('focus_groups') ?? false
  const displaysAsSection =
    isSharedMemoryGroup &&
    Boolean(schema && 'x-display-as-section' in schema && schema['x-display-as-section'])
  const globalMemorySharingEnabled =
    isSharedMemoryGroup && parentValues?.global_memory_sharing_enabled === true
  const groupLabel = isSharedMemoryGroup
    ? '共享记忆组'
    : isFocusGroup
      ? 'Focus 共享组'
      : isBehaviorGroup
        ? '行为共享组'
        : isJargonGroup
          ? '黑话共享组'
          : '表达共享组'
  const learnedContentLabel = isBehaviorGroup ? '行为经验' : isJargonGroup ? '黑话' : '表达方式'
  const supportsWildcardTargets = !isSharedMemoryGroup
  const groupScopeOptions = supportsWildcardTargets ? GROUP_SCOPE_OPTIONS : EXACT_GROUP_SCOPE_OPTIONS
  const helperText = isSharedMemoryGroup
    ? '把几个群聊或私聊放进同一组后，麦麦在其中任意一个聊天里回忆长期记忆时，会一起参考同组聊天的记忆；新产生的内容仍记在原来的聊天里。'
    : isFocusGroup
      ? '配置后只有同组聊天流共享 Focus，不同组可以分别进入 Focus。'
    : `每个共享组内的聊天流会共享已学习的${learnedContentLabel}。`
  const [collapsedGroups, setCollapsedGroups] = useState<Set<number>>(() =>
    isSharedMemoryGroup ? new Set(Array.from({ length: groups.length }, (_, index) => index)) : new Set()
  )
  const [definedPlatformOptions, setDefinedPlatformOptions] = useState<PlatformSelectOption[]>([])
  const [chatStreamOptions, setChatStreamOptions] = useState<ChatStream[]>([])
  const [showAddGroupPanel, setShowAddGroupPanel] = useState(false)
  const [addingMemberGroupIndex, setAddingMemberGroupIndex] = useState<number | null>(null)

  useEffect(() => {
    if (!isSharedMemoryGroup) {
      return
    }

    if (globalMemorySharingEnabled) {
      const allGroupIndexes = new Set(Array.from({ length: groups.length }, (_, index) => index))
      setCollapsedGroups(allGroupIndexes)
      setShowAddGroupPanel(false)
      setAddingMemberGroupIndex(null)
      return
    }

    setCollapsedGroups((current) => {
      const next = new Set<number>()
      current.forEach((index) => {
        if (index < groups.length) {
          next.add(index)
        }
      })
      return next
    })
  }, [globalMemorySharingEnabled, groups.length, isSharedMemoryGroup])

  useEffect(() => {
    if (!isSharedMemoryGroup) {
      return
    }

    let disposed = false
    getChatStreams()
      .then((chats) => {
        if (!disposed) {
          setChatStreamOptions(normalizeSharedMemoryChatStreams(chats))
        }
      })
      .catch((error: unknown) => {
        console.error('加载共享记忆组聊天流列表失败:', error)
      })

    return () => {
      disposed = true
    }
  }, [isSharedMemoryGroup])

  useEffect(() => {
    if (!isJargonGroup) {
      return
    }

    let disposed = false
    getBotConfigCached()
      .then((config) => {
        if (!disposed) {
          setDefinedPlatformOptions(buildDefinedPlatformOptions(config))
        }
      })
      .catch((error: unknown) => {
        console.error('加载黑话共享组平台列表失败:', error)
      })

    return () => {
      disposed = true
    }
  }, [isJargonGroup])

  const updateGroups = (nextGroups: ExpressionGroupValue[]) => {
    onChange?.(
      nextGroups.map((group) => ({
        targets: group.targets.map((target) => ({
          platform: target.platform,
          item_id: target.item_id,
          rule_type: target.rule_type,
        })),
      }))
    )
  }

  const addGroup = (scopeKind: GroupScopeKind, ruleType: ExpressionRuleType) => {
    if (globalMemorySharingEnabled) return

    updateGroups([...groups, { targets: [createExpressionTarget(scopeKind, definedPlatformOptions, ruleType)] }])
    setShowAddGroupPanel(false)
  }

  const toggleGroupCollapsed = (groupIndex: number) => {
    if (globalMemorySharingEnabled) return

    setCollapsedGroups((current) => {
      const next = new Set(current)
      if (next.has(groupIndex)) {
        next.delete(groupIndex)
      } else {
        next.add(groupIndex)
      }
      return next
    })
  }

  const removeGroup = (groupIndex: number) => {
    updateGroups(groups.filter((_, index) => index !== groupIndex))
  }

  const addMember = (groupIndex: number, scopeKind: GroupScopeKind, ruleType: ExpressionRuleType) => {
    if (globalMemorySharingEnabled) return

    updateGroups(
      groups.map((group, index) =>
        index === groupIndex
          ? {
              targets: [
                ...group.targets,
                createExpressionTarget(scopeKind, definedPlatformOptions, ruleType),
              ],
            }
          : group
      )
    )
    setAddingMemberGroupIndex(null)
  }

  const removeMember = (groupIndex: number, memberIndex: number) => {
    updateGroups(
      groups.map((group, index) =>
        index === groupIndex
          ? {
              targets: group.targets.filter(
                (_, currentMemberIndex) => currentMemberIndex !== memberIndex
              ),
            }
          : group
      )
    )
  }

  const updateMember = (
    groupIndex: number,
    memberIndex: number,
    patch: Partial<ExpressionGroupTarget>
  ) => {
    updateGroups(
      groups.map((group, index) =>
        index === groupIndex
          ? {
              targets: group.targets.map(
                (member, currentMemberIndex) =>
                  currentMemberIndex === memberIndex
                    ? { ...member, ...patch }
                    : member
              ),
            }
          : group
      )
    )
  }

  const getPlatformOptions = (platform: string) =>
    withCurrentPlatformOption(definedPlatformOptions, platform)

  return (
    <div className={displaysAsSection ? 'space-y-3' : 'space-y-3 rounded-lg border bg-card p-4 sm:p-5'}>
      {isSharedMemoryGroup && (
        <div className="flex flex-col gap-3 rounded-md border bg-muted/20 px-3 py-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="space-y-1">
            <Label className="text-sm font-medium">全局共享记忆</Label>
            <p className="text-xs text-muted-foreground">
              开启后普通记忆查询会在所有聊天流范围内检索。
            </p>
          </div>
          <Switch
            aria-label="全局共享记忆"
            checked={globalMemorySharingEnabled}
            onCheckedChange={(checked) => onParentChange?.('global_memory_sharing_enabled', checked)}
          />
        </div>
      )}
      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
        <div className="space-y-1">
          {!displaysAsSection && <h3 className="text-base font-semibold">{groupLabel}</h3>}
          <p className="text-sm text-muted-foreground">
            {helperText}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {isSharedMemoryGroup && (
            <Button asChild size="sm" variant="outline" className="h-8">
              <a href="/chat-management?view=groups&kind=memory">
                <ExternalLink className="h-3.5 w-3.5" />
                聊天管理
              </a>
            </Button>
          )}
          <Button
            type="button"
            size="icon"
            variant="outline"
            aria-label={`添加${groupLabel}`}
            title={globalMemorySharingEnabled ? '全局共享开启时不需要配置共享组' : `添加${groupLabel}`}
            disabled={globalMemorySharingEnabled}
            onClick={() => {
              if (isSharedMemoryGroup) {
                addGroup('chat', 'group')
                return
              }
              setShowAddGroupPanel(true)
            }}
          >
            <Plus className="h-4 w-4" />
          </Button>
        </div>
      </div>

      <AddGroupTargetDialog
        open={showAddGroupPanel && !globalMemorySharingEnabled && !isSharedMemoryGroup}
        title={`选择${groupLabel}的第一个成员`}
        onAdd={addGroup}
        onOpenChange={(open) => {
          if (globalMemorySharingEnabled) {
            setShowAddGroupPanel(false)
            return
          }
          setShowAddGroupPanel(open)
        }}
        scopeOptions={groupScopeOptions}
      />

      {groups.length === 0 ? (
        <div className="rounded-md border border-dashed bg-muted/30 px-4 py-8 text-center text-sm text-muted-foreground">
          {globalMemorySharingEnabled
            ? '全局共享已开启，暂不需要配置共享记忆组。'
            : isSharedMemoryGroup
              ? `暂无${groupLabel}，点击上方按钮添加后可直接选择群聊或私聊。`
              : `暂无${groupLabel}，点击上方按钮选择第一个成员。`}
        </div>
      ) : (
        <div className="space-y-2">
          {groups.map((group, groupIndex) => {
            const isCollapsed = globalMemorySharingEnabled || collapsedGroups.has(groupIndex)

            return (
              <div
                key={groupIndex}
                className="space-y-2 rounded-md border bg-muted/20 p-2.5 sm:p-3"
              >
                <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-sm font-medium">
                      {groupLabel} {groupIndex + 1}
                    </span>
                    <Badge variant="secondary">
                      {group.targets.length} 个成员
                    </Badge>
                  </div>
                  <div className="flex gap-2">
                    <Button
                      type="button"
                      size="icon"
                      variant="outline"
                      className="h-8 w-8"
                      aria-label={`添加${groupLabel} ${groupIndex + 1} 的成员`}
                      title={globalMemorySharingEnabled ? '全局共享开启时共享组已折叠' : '添加成员'}
                      disabled={globalMemorySharingEnabled}
                      onClick={() =>
                        isSharedMemoryGroup
                          ? addMember(groupIndex, 'chat', 'group')
                          : setAddingMemberGroupIndex((currentGroupIndex) =>
                              currentGroupIndex === groupIndex ? null : groupIndex,
                            )
                      }
                    >
                      <Plus className="h-4 w-4" />
                    </Button>
                    <Button
                      type="button"
                      size="icon"
                      variant="ghost"
                      className="h-8 w-8"
                      aria-label={isCollapsed ? `展开${groupLabel} ${groupIndex + 1}` : `折叠${groupLabel} ${groupIndex + 1}`}
                      title={globalMemorySharingEnabled ? '全局共享开启时共享组已折叠' : isCollapsed ? '展开' : '折叠'}
                      disabled={globalMemorySharingEnabled}
                      onClick={() => toggleGroupCollapsed(groupIndex)}
                    >
                      {isCollapsed ? (
                        <ChevronDown className="h-4 w-4" />
                      ) : (
                        <ChevronUp className="h-4 w-4" />
                      )}
                    </Button>
                    <Button
                      type="button"
                      size="icon"
                      variant="ghost"
                      className="h-8 w-8"
                      aria-label={`删除${groupLabel} ${groupIndex + 1}`}
                      title={globalMemorySharingEnabled ? '全局共享开启时共享组已折叠' : `删除${groupLabel} ${groupIndex + 1}`}
                      disabled={globalMemorySharingEnabled}
                      onClick={() => removeGroup(groupIndex)}
                    >
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </div>
                </div>

                {!isCollapsed && (group.targets.length === 0 ? (
                  <div className="rounded-md bg-background/70 px-3 py-4 text-sm text-muted-foreground">
                    这个{groupLabel}还没有成员。
                  </div>
                ) : (
                  <div className="space-y-1.5">
                    {group.targets.map((member, memberIndex) => {
                      const scopeKind = supportsWildcardTargets ? resolveGroupScopeKind(member) : 'chat'
                      const platformOptions = getPlatformOptions(member.platform)

                      return (
                        <ExpressionGroupMemberItem
                          chatStreamOptions={chatStreamOptions}
                          key={`${groupIndex}-${memberIndex}`}
                          groupIndex={groupIndex}
                          groupLabel={groupLabel}
                          isSharedMemoryGroup={isSharedMemoryGroup}
                          member={member}
                          memberIndex={memberIndex}
                          onRemoveMember={removeMember}
                          onUpdateMember={updateMember}
                          platformOptions={platformOptions}
                          scopeKind={scopeKind}
                          usePlatformSelect={isJargonGroup}
                        />
                      )
                    })}
                  </div>
                ))}
              </div>
            )
          })}
        </div>
      )}
      <AddGroupTargetDialog
        open={addingMemberGroupIndex !== null && !globalMemorySharingEnabled && !isSharedMemoryGroup}
        title={`选择${groupLabel} ${
          addingMemberGroupIndex === null ? '' : addingMemberGroupIndex + 1
        } 的新成员`}
        onAdd={(scopeKind, ruleType) => {
          if (addingMemberGroupIndex === null) return
          addMember(addingMemberGroupIndex, scopeKind, ruleType)
        }}
        onOpenChange={(open) => {
          if (globalMemorySharingEnabled || !open) setAddingMemberGroupIndex(null)
        }}
        scopeOptions={groupScopeOptions}
      />
    </div>
  )
}

export const JargonGroupsHook = ExpressionGroupsHook

export const BehaviorGroupsHook = ExpressionGroupsHook

export const BehaviorFocusGroupsHook = ExpressionGroupsHook

export const AMemorixSharedMemoryGroupsHook = ExpressionGroupsHook

export const MCPRootItemsHook = createJsonFieldHook({
  emptyValue: [],
  helperText: 'MCP Roots 条目为对象数组，使用 JSON 编辑。',
  placeholder: '[\n  {\n    "enabled": true,\n    "uri": "file:///Users/example/project",\n    "name": "project-root"\n  }\n]',
})

export const MCPServersHook = createJsonFieldHook({
  emptyValue: [],
  helperText: 'MCP 服务器配置结构较复杂，使用 JSON 编辑。',
  placeholder: '[\n  {\n    "name": "example-server",\n    "enabled": true,\n    "transport": "stdio",\n    "command": "uvx",\n    "args": ["example-server"],\n    "env": {},\n    "url": "",\n    "headers": {},\n    "http_timeout_seconds": 30.0,\n    "read_timeout_seconds": 300.0,\n    "authorization": {\n      "mode": "none",\n      "bearer_token": ""\n    }\n  }\n]',
})
