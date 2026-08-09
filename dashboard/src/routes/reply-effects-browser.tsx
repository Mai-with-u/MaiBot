import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  ArrowDown,
  ArrowUp,
  CircleGauge,
  Filter,
  LoaderCircle,
  MessageCircleReply,
  MessagesSquare,
  RotateCcw,
} from 'lucide-react'

import { Avatar, AvatarFallback, AvatarImage } from '@/components/ui/avatar'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { backendApi } from '@/lib/http'
import { cn } from '@/lib/utils'

interface BrowserFilters {
  sessions: [string, string][]
  strategies: string[]
  models: string[]
}

interface RecordItem {
  effect_id: string
  session_name: string
  status: string
  created_at: string
  strategy_primary: string
  model_name: string
  evaluation_version: number
  reply_text: string
  response_score: number | null
  reception_score: number | null
  conversation_score: number | null
  raw_score: number | null
  confidence: number
  evaluation_error: string
}

interface RecordList {
  items: RecordItem[]
  total: number
  next_cursor: number | null
}

interface DetailAssociation {
  effect_id: string
  attribution_type: string
  attribution_confidence: number
  stance_target: string
  stance: string
  contribution: string
  reason: string
  evidence_spans: string[]
  evaluator_confidence: number
}

interface DetailFollowup {
  message_id: string
  timestamp: string
  user_id?: string
  nickname: string
  cardname: string
  visible_text: string
  reply_to: string
  avatar_url?: string | null
  associations: DetailAssociation[]
}

interface ContextMessage {
  message_id: string
  source: string
  role: string
  timestamp: string
  text: string
  display_text?: string
  sender?: {
    user_id?: string
    nickname?: string
    cardname?: string
    display_name?: string
    platform?: string
    avatar_url?: string | null
  }
}

interface TimelineMessage extends ContextMessage {
  timeline_kind: 'context' | 'reply' | 'followup'
  associations?: DetailAssociation[]
}

interface EffectDetail {
  effect_id: string
  status: string
  created_at: string
  finalized_at: string
  finalize_reason: string
  evaluation_error: string
  confidence_note: string
  evaluation_version: number
  pre_activity_count: number
  pre_activity_bucket: string
  session?: { session_name?: string }
  target_user?: { nickname?: string; cardname?: string; user_id?: string }
  reply?: {
    target_message_id?: string
    reply_text?: string
    model_name?: string
    strategy_primary?: string
    strategy_secondary?: string[]
    strategy_confidence?: number
  }
  scores?: {
    response_score: number
    reception_score: number | null
    conversation_score: number
    raw_score: number
    confidence: number
    response_evidence_confidence: number
    reception_evidence_confidence: number
    conversation_evidence_confidence: number
  }
  context_snapshot?: ContextMessage[]
  followup_messages?: DetailFollowup[]
  followup_summary?: {
    total_count?: number
    associated_count?: number
    participant_count?: number
  }
}

const PAGE_SIZE = 30

const STRATEGY_NAMES: Record<string, string> = {
  answer: '信息回答',
  opinion: '观点表达',
  empathy: '共情支持',
  humor: '玩梗调侃',
  question: '追问引导',
  topic_start: '主动开题',
  acknowledgement: '简短接话',
  other: '其他',
}

const STATUS_NAMES: Record<string, string> = {
  pending: '等待结算',
  evaluating: '正在评估',
  finalized: '已完成',
  evaluation_failed: '评估失败',
}

const STANCE_NAMES: Record<string, string> = {
  appreciation: '认可',
  playful: '玩笑',
  neutral: '中性',
  confusion: '困惑',
  factual_correction: '事实纠正',
  rejection: '拒绝',
  bot_attack: '攻击 Bot',
}

const STANCE_TARGET_NAMES: Record<string, string> = {
  topic_or_third_party: '话题或第三方',
  bot_content: 'Bot 回复内容',
  bot_persona: 'Bot 本身',
}

const CONTRIBUTION_NAMES: Record<string, string> = {
  advance: '推进',
  maintain: '维持',
  acknowledge: '回应',
  close: '收束',
  unrelated: '无关',
  wrong_push: '错误推动',
}

const SORT_OPTIONS = [
  ['created_at', '评估时间'],
  ['raw_score', '原始总分'],
  ['response_score', '回应度'],
  ['reception_score', '接受度'],
  ['conversation_score', '推动度'],
  ['confidence', '置信度'],
] as const

function scoreText(value: number | null | undefined) {
  return value == null ? '—' : value.toFixed(1)
}

function confidenceText(value: number | null | undefined) {
  return value == null ? '—' : `${Math.round(value * 100)}%`
}

function strategyName(value: string | undefined) {
  return STRATEGY_NAMES[value ?? ''] ?? value ?? '未分类'
}

const LEGACY_CONTEXT_MESSAGE_PATTERN =
  /^(?:\d{2}:\d{2}:\d{2})?(?:\[msg_id:[^\]]+\])?\[([^\]]+)\]([\s\S]*)$/

function getContextMessagePresentation(message: ContextMessage) {
  const legacyMatch = message.text.match(LEGACY_CONTEXT_MESSAGE_PATTERN)
  const senderName =
    message.sender?.display_name ||
    message.sender?.cardname ||
    message.sender?.nickname ||
    legacyMatch?.[1] ||
    ''
  return {
    senderName,
    text: message.display_text ?? legacyMatch?.[2]?.trim() ?? message.text,
  }
}

function contextMessageRoleLabel(message: ContextMessage) {
  if (message.source === 'evaluated_reply') return '麦麦'
  if (message.source === 'followup') return '后续'
  if (message.source === 'user') return '用户'
  if (message.source === 'guided_reply') return '麦麦'
  if (message.role === 'reasoning') return '推理'
  if (message.role === 'assistant') return '助手'
  return '参考'
}

function contextMessageStyle(message: ContextMessage) {
  if (message.source === 'user' || message.source === 'followup') {
    return 'border-emerald-400/70 bg-emerald-50/45 dark:border-emerald-700 dark:bg-emerald-950/15'
  }
  if (message.source === 'guided_reply' || message.source === 'evaluated_reply') {
    return 'border-orange-400/70 bg-orange-50/45 dark:border-orange-700 dark:bg-orange-950/15'
  }
  if (message.role === 'reasoning') {
    return 'border-indigo-400/70 bg-indigo-50/45 dark:border-indigo-700 dark:bg-indigo-950/15'
  }
  return 'border-border bg-card/50'
}

function contextMessageBadgeStyle(message: ContextMessage) {
  if (message.source === 'user' || message.source === 'followup') {
    return 'border-emerald-400/60 bg-emerald-100/70 text-emerald-900 dark:border-emerald-700 dark:bg-emerald-950 dark:text-emerald-100'
  }
  if (message.source === 'guided_reply' || message.source === 'evaluated_reply') {
    return 'border-orange-400/60 bg-orange-100/70 text-orange-900 dark:border-orange-700 dark:bg-orange-950 dark:text-orange-100'
  }
  if (message.role === 'reasoning') {
    return 'border-indigo-400/60 bg-indigo-100/70 text-indigo-900 dark:border-indigo-700 dark:bg-indigo-950 dark:text-indigo-100'
  }
  return 'bg-background/80'
}

function avatarFallbackText(displayName: string, userId = '') {
  const normalizedName = displayName.trim()
  if (normalizedName) return normalizedName.slice(0, 1).toUpperCase()
  const normalizedUserId = userId.trim()
  return normalizedUserId ? normalizedUserId.slice(-2) : '用'
}

function ContextMessageRow({ message, isTarget }: { message: TimelineMessage; isTarget: boolean }) {
  const { senderName, text } = getContextMessagePresentation(message)
  const showAvatar = Boolean(senderName || message.sender?.avatar_url)

  return (
    <article
      className={cn(
        'border-l-2 px-3 py-3 sm:px-4',
        contextMessageStyle(message),
        isTarget && 'ring-primary/35 ring-1 ring-inset'
      )}
    >
      <div className="mb-1.5 flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
        {showAvatar && (
          <Avatar className="bg-background h-6 w-6 shrink-0 border">
            {message.sender?.avatar_url && (
              <AvatarImage src={message.sender.avatar_url} alt={`${senderName} 的头像`} />
            )}
            <AvatarFallback className="text-[10px]">
              {avatarFallbackText(senderName, message.sender?.user_id)}
            </AvatarFallback>
          </Avatar>
        )}
        {senderName && <span className="truncate text-xs font-medium">{senderName}</span>}
        <Badge
          variant="outline"
          className={cn('h-5 px-1.5 text-[10px]', contextMessageBadgeStyle(message))}
        >
          {contextMessageRoleLabel(message)}
        </Badge>
        {isTarget && <Badge className="h-5 px-1.5 text-[10px]">目标消息</Badge>}
        {message.timeline_kind === 'reply' && (
          <Badge className="h-5 px-1.5 text-[10px]">本次回复</Badge>
        )}
        {message.timestamp && (
          <span className="text-muted-foreground text-[11px] tabular-nums">
            {new Date(message.timestamp).toLocaleString()}
          </span>
        )}
      </div>
      <p className="line-clamp-6 text-sm leading-6 whitespace-pre-wrap">{text || '空消息'}</p>
      {message.timeline_kind === 'followup' &&
        (message.associations?.length ? (
          <div className="mt-3 space-y-2 border-t pt-3">
            {message.associations.map((association, index) => (
              <div
                key={`${association.effect_id}-${index}`}
                className="bg-muted/35 rounded-md p-3 text-xs"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <Badge variant="outline">
                    {STANCE_TARGET_NAMES[association.stance_target] ?? association.stance_target}
                  </Badge>
                  <Badge variant="outline">
                    {STANCE_NAMES[association.stance] ?? association.stance}
                  </Badge>
                  <Badge variant="outline">
                    {CONTRIBUTION_NAMES[association.contribution] ?? association.contribution}
                  </Badge>
                  <span className="text-muted-foreground">
                    归因 {confidenceText(association.attribution_confidence)}
                  </span>
                </div>
                {association.reason && <p className="mt-2 leading-5">{association.reason}</p>}
                {association.evidence_spans.length > 0 && (
                  <p className="text-muted-foreground mt-1 leading-5">
                    证据：{association.evidence_spans.join('；')}
                  </p>
                )}
              </div>
            ))}
          </div>
        ) : (
          <div className="text-muted-foreground mt-3 border-t pt-2 text-xs">
            评审未将这条消息关联到本次回复
          </div>
        ))}
    </article>
  )
}

function StatusBadge({ status }: { status: string }) {
  return (
    <Badge
      variant="outline"
      className={cn(
        'text-[11px] font-medium whitespace-nowrap',
        status === 'finalized' &&
          'border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300',
        status === 'pending' &&
          'border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-300',
        status === 'evaluating' && 'border-sky-500/30 bg-sky-500/10 text-sky-700 dark:text-sky-300',
        status === 'evaluation_failed' && 'border-destructive/30 bg-destructive/10 text-destructive'
      )}
    >
      {STATUS_NAMES[status] ?? status}
    </Badge>
  )
}

function DetailScore({ label, value }: { label: string; value: number | null | undefined }) {
  return (
    <div className="bg-card rounded-lg border p-3">
      <div className="text-muted-foreground text-xs">{label}</div>
      <div className="mt-1 text-xl font-bold tabular-nums">{scoreText(value)}</div>
    </div>
  )
}

function EvaluationDetail({ detail }: { detail: EffectDetail }) {
  const targetMessageId = detail.reply?.target_message_id
  const contextMessages = useMemo(() => {
    const messages = detail.context_snapshot ?? []
    const recentMessages = messages.slice(-20)
    const targetMessage = messages.find((message) => message.message_id === targetMessageId)
    if (targetMessage && !recentMessages.includes(targetMessage)) {
      return [targetMessage, ...recentMessages]
    }
    return recentMessages
  }, [detail.context_snapshot, targetMessageId])
  const timelineMessages = useMemo<TimelineMessage[]>(() => {
    const messages: TimelineMessage[] = contextMessages.map((message) => ({
      ...message,
      timeline_kind: 'context',
    }))
    messages.push({
      message_id: `effect:${detail.effect_id}`,
      source: 'evaluated_reply',
      role: 'assistant',
      timestamp: detail.created_at,
      text: detail.reply?.reply_text || '无可见回复文本',
      sender: { display_name: 'Bot' },
      timeline_kind: 'reply',
    })
    for (const followup of detail.followup_messages ?? []) {
      messages.push({
        message_id: followup.message_id,
        source: 'followup',
        role: 'user',
        timestamp: followup.timestamp,
        text: followup.visible_text || '无可见文本',
        sender: {
          user_id: followup.user_id,
          nickname: followup.nickname,
          cardname: followup.cardname,
          display_name: followup.cardname || followup.nickname || '未知用户',
          avatar_url: followup.avatar_url,
        },
        timeline_kind: 'followup',
        associations: followup.associations,
      })
    }
    return messages
  }, [contextMessages, detail.created_at, detail.effect_id, detail.followup_messages, detail.reply])
  const scores = detail.scores

  return (
    <div className="space-y-5">
      <section className="bg-muted/25 rounded-xl border p-4 sm:p-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="text-muted-foreground flex flex-wrap gap-x-4 gap-y-1 text-xs">
              <span>{detail.session?.session_name || '未知聊天流'}</span>
              <span>{new Date(detail.created_at).toLocaleString()}</span>
              <span>{detail.reply?.model_name || '未记录模型'}</span>
              <span>评估标准 v{detail.evaluation_version}</span>
            </div>
          </div>
          <StatusBadge status={detail.status} />
        </div>
        <div className="text-muted-foreground mt-3 flex flex-wrap gap-x-5 gap-y-1 border-t pt-3 text-xs">
          <span>策略：{strategyName(detail.reply?.strategy_primary)}</span>
          <span>策略置信度：{confidenceText(detail.reply?.strategy_confidence)}</span>
          <span>
            目标用户：
            {detail.target_user?.cardname || detail.target_user?.nickname || '未记录'}
          </span>
          <span>结算原因：{detail.finalize_reason || '尚未结算'}</span>
        </div>
      </section>

      {detail.evaluation_error && (
        <div className="border-destructive/30 bg-destructive/10 text-destructive rounded-lg border p-3 text-sm">
          {detail.evaluation_error}
        </div>
      )}

      <section>
        <div className="mb-3 flex items-center gap-2">
          <CircleGauge className="text-primary h-4 w-4" />
          <h3 className="text-sm font-semibold">评分结果</h3>
        </div>
        <div className="grid grid-cols-2 gap-3 xl:grid-cols-5">
          <DetailScore label="回应度" value={scores?.response_score} />
          <DetailScore label="情感接受度" value={scores?.reception_score} />
          <DetailScore label="聊天推动度" value={scores?.conversation_score} />
          <DetailScore label="原始总分" value={scores?.raw_score} />
          <div className="bg-card rounded-lg border p-3">
            <div className="text-muted-foreground text-xs">综合置信度</div>
            <div className="mt-1 text-xl font-bold tabular-nums">
              {confidenceText(scores?.confidence)}
            </div>
          </div>
        </div>
        {scores && (
          <div className="text-muted-foreground mt-3 flex flex-wrap gap-x-5 gap-y-1 text-xs">
            <span>回应证据 {confidenceText(scores.response_evidence_confidence)}</span>
            <span>
              {scores.reception_score == null
                ? '接受度：无针对 Bot 的情绪证据'
                : `接受证据 ${confidenceText(scores.reception_evidence_confidence)}`}
            </span>
            <span>推动证据 {confidenceText(scores.conversation_evidence_confidence)}</span>
            {detail.confidence_note && <span>{detail.confidence_note}</span>}
          </div>
        )}
      </section>

      <section>
        <div className="mb-3 flex items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <MessagesSquare className="text-primary h-4 w-4" />
            <h3 className="text-sm font-semibold">评估对话时间线</h3>
          </div>
          <span className="text-muted-foreground text-xs">
            上下文 {contextMessages.length} 条 / 本次回复 1 条 / 后续{' '}
            {detail.followup_messages?.length ?? 0} 条 / 关联{' '}
            {detail.followup_summary?.associated_count ?? 0} 条
          </span>
        </div>
        <div className="bg-muted/15 max-h-[36rem] overflow-y-auto rounded-xl border p-2">
          <div className="space-y-2">
            {timelineMessages.map((message, index) => (
              <ContextMessageRow
                key={`${message.timeline_kind}-${message.message_id || message.timestamp}-${index}`}
                message={message}
                isTarget={
                  message.timeline_kind === 'context' && message.message_id === targetMessageId
                }
              />
            ))}
          </div>
        </div>
        <p className="text-muted-foreground mt-2 text-xs">
          回复前 2 分钟有 {detail.pre_activity_count ?? 0} 条人类消息
        </p>
      </section>
    </div>
  )
}

export function ReplyEffectsBrowser({
  filters,
  refreshToken,
  onLoadingChange,
}: {
  filters?: BrowserFilters
  refreshToken: number
  onLoadingChange?: (loading: boolean) => void
}) {
  const [records, setRecords] = useState<RecordList | null>(null)
  const [detail, setDetail] = useState<EffectDetail | null>(null)
  const [selectedId, setSelectedId] = useState('')
  const [loading, setLoading] = useState(true)
  const [loadingMore, setLoadingMore] = useState(false)
  const [detailLoading, setDetailLoading] = useState(false)
  const [error, setError] = useState('')
  const [sessionId, setSessionId] = useState('')
  const [strategy, setStrategy] = useState('')
  const [modelName, setModelName] = useState('')
  const [status, setStatus] = useState('')
  const [minConfidence, setMinConfidence] = useState('')
  const [sortBy, setSortBy] = useState('created_at')
  const [sortOrder, setSortOrder] = useState<'asc' | 'desc'>('desc')
  const listRequestIdRef = useRef(0)
  const loadingMoreRef = useRef(false)

  const listQuery = useMemo(() => {
    const params = new URLSearchParams({
      limit: String(PAGE_SIZE),
      sort_by: sortBy,
      sort_order: sortOrder,
    })
    if (sessionId) params.set('session_id', sessionId)
    if (strategy) params.set('strategy', strategy)
    if (modelName) params.set('model_name', modelName)
    if (status) params.set('status', status)
    if (minConfidence) params.set('min_confidence', minConfidence)
    return params.toString()
  }, [minConfidence, modelName, sessionId, sortBy, sortOrder, status, strategy])

  const loadRecords = useCallback(
    async (cursor = 0, append = false) => {
      if (append && loadingMoreRef.current) return
      const requestId = append ? listRequestIdRef.current : ++listRequestIdRef.current
      if (append) {
        loadingMoreRef.current = true
        setLoadingMore(true)
      } else {
        loadingMoreRef.current = false
        setLoadingMore(false)
        setLoading(true)
      }
      setError('')
      try {
        const nextRecords = await backendApi.get<RecordList>(
          `/api/webui/reply-effects?${listQuery}&cursor=${cursor}`
        )
        if (requestId !== listRequestIdRef.current) return
        setRecords((current) => {
          if (!append || !current) return nextRecords
          const knownIds = new Set(current.items.map((item) => item.effect_id))
          return {
            ...nextRecords,
            items: [
              ...current.items,
              ...nextRecords.items.filter((item) => !knownIds.has(item.effect_id)),
            ],
          }
        })
        setSelectedId((current) => {
          if (append || nextRecords.items.some((item) => item.effect_id === current)) return current
          return nextRecords.items[0]?.effect_id ?? ''
        })
      } catch (requestError) {
        if (requestId === listRequestIdRef.current) {
          setError(requestError instanceof Error ? requestError.message : '加载评估列表失败')
        }
      } finally {
        if (append) {
          loadingMoreRef.current = false
          if (requestId === listRequestIdRef.current) setLoadingMore(false)
        } else if (requestId === listRequestIdRef.current) {
          setLoading(false)
        }
      }
    },
    [listQuery]
  )

  useEffect(() => {
    void loadRecords(0, false)
  }, [loadRecords, refreshToken])

  useEffect(() => {
    onLoadingChange?.(loading || loadingMore || detailLoading)
  }, [detailLoading, loading, loadingMore, onLoadingChange])

  useEffect(() => {
    if (!selectedId) {
      setDetail(null)
      return
    }
    let active = true
    setDetailLoading(true)
    backendApi
      .get<EffectDetail>(`/api/webui/reply-effects/${selectedId}`)
      .then((nextDetail) => {
        if (active) setDetail(nextDetail)
      })
      .catch((requestError: unknown) => {
        if (active) {
          setError(requestError instanceof Error ? requestError.message : '加载评估详情失败')
        }
      })
      .finally(() => {
        if (active) setDetailLoading(false)
      })
    return () => {
      active = false
    }
  }, [selectedId])

  const resetFilters = () => {
    setSessionId('')
    setStrategy('')
    setModelName('')
    setStatus('')
    setMinConfidence('')
    setSortBy('created_at')
    setSortOrder('desc')
  }

  const updateFilter = (setter: (value: string) => void, value: string) => {
    setter(value === 'all' ? '' : value)
  }

  return (
    <div className="flex min-w-0 flex-col gap-4">
      <section className="border-border/70 bg-card rounded-xl border p-3 shadow-sm">
        <div className="flex flex-col gap-3 2xl:flex-row 2xl:items-center">
          <div className="flex shrink-0 items-center justify-between gap-3 2xl:justify-start">
            <div className="flex items-center gap-2">
              <Filter className="text-primary h-4 w-4" />
              <h2 className="text-sm font-semibold">筛选与排序</h2>
            </div>
          </div>
          <div className="grid min-w-0 flex-1 grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-4 xl:grid-cols-7">
            <Select
              value={sessionId || 'all'}
              onValueChange={(value) => updateFilter(setSessionId, value)}
            >
              <SelectTrigger aria-label="浏览聊天流">
                <SelectValue placeholder="聊天流" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">全部聊天流</SelectItem>
                {filters?.sessions.map(([id, name]) => (
                  <SelectItem key={id} value={id}>
                    {name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Select
              value={status || 'all'}
              onValueChange={(value) => updateFilter(setStatus, value)}
            >
              <SelectTrigger aria-label="评估状态">
                <SelectValue placeholder="状态" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">全部状态</SelectItem>
                {Object.entries(STATUS_NAMES).map(([value, label]) => (
                  <SelectItem key={value} value={value}>
                    {label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Select
              value={strategy || 'all'}
              onValueChange={(value) => updateFilter(setStrategy, value)}
            >
              <SelectTrigger aria-label="浏览回复策略">
                <SelectValue placeholder="策略" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">全部策略</SelectItem>
                {Object.entries(STRATEGY_NAMES).map(([value, label]) => (
                  <SelectItem key={value} value={value}>
                    {label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Select
              value={modelName || 'all'}
              onValueChange={(value) => updateFilter(setModelName, value)}
            >
              <SelectTrigger aria-label="浏览回复模型">
                <SelectValue placeholder="模型" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">全部模型</SelectItem>
                {filters?.models.map((model) => (
                  <SelectItem key={model} value={model}>
                    {model}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Input
              type="number"
              min="0"
              max="1"
              step="0.1"
              placeholder="最低置信度（0～1）"
              aria-label="浏览最低置信度"
              value={minConfidence}
              onChange={(event) => setMinConfidence(event.target.value)}
            />
            <Select value={sortBy} onValueChange={setSortBy}>
              <SelectTrigger aria-label="评估排序字段">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {SORT_OPTIONS.map(([value, label]) => (
                  <SelectItem key={value} value={value}>
                    按{label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Button
              type="button"
              variant="outline"
              onClick={() => setSortOrder((value) => (value === 'desc' ? 'asc' : 'desc'))}
            >
              {sortOrder === 'desc' ? (
                <ArrowDown className="h-4 w-4" />
              ) : (
                <ArrowUp className="h-4 w-4" />
              )}
              {sortBy === 'created_at'
                ? sortOrder === 'desc'
                  ? '最新在前'
                  : '最早在前'
                : sortOrder === 'desc'
                  ? '从高到低'
                  : '从低到高'}
            </Button>
          </div>
          <Button type="button" variant="ghost" size="sm" onClick={resetFilters}>
            <RotateCcw className="h-3.5 w-3.5" />
            重置
          </Button>
        </div>
      </section>

      <div className="grid min-h-0 gap-4 lg:h-[calc(100vh-3rem)] lg:min-h-[720px] lg:grid-cols-[23rem_minmax(0,1fr)]">
        <aside className="bg-card flex h-[680px] min-h-0 flex-col overflow-hidden rounded-xl border shadow-sm lg:h-full">
          <div className="text-muted-foreground flex items-center justify-between border-b px-4 py-2 text-xs">
            <span>共 {records?.total ?? 0} 条评估</span>
            <span>已加载 {records?.items.length ?? 0} 条</span>
          </div>

          <div
            className="min-h-0 flex-1 overflow-y-auto p-2"
            onScroll={(event) => {
              const element = event.currentTarget
              const reachedBottom =
                element.scrollHeight - element.scrollTop - element.clientHeight < 120
              if (reachedBottom && records?.next_cursor != null && !loading && !loadingMore) {
                void loadRecords(records.next_cursor, true)
              }
            }}
          >
            {loading ? (
              <div className="text-muted-foreground flex h-40 items-center justify-center gap-2 text-sm">
                <LoaderCircle className="h-4 w-4 animate-spin" />
                加载评估记录…
              </div>
            ) : records?.items.length ? (
              <div className="space-y-1.5">
                {records.items.map((item) => (
                  <button
                    key={item.effect_id}
                    type="button"
                    onClick={() => {
                      setDetail(null)
                      setSelectedId(item.effect_id)
                    }}
                    className={cn(
                      'hover:bg-muted/50 w-full rounded-lg border px-3 py-3 text-left transition-colors',
                      selectedId === item.effect_id
                        ? 'border-primary/40 bg-primary/8'
                        : 'border-transparent'
                    )}
                  >
                    <div className="flex items-start justify-between gap-2">
                      <span className="text-muted-foreground text-[11px] tabular-nums">
                        {new Date(item.created_at).toLocaleString()}
                      </span>
                      <StatusBadge status={item.status} />
                    </div>
                    <div className="mt-1.5 line-clamp-2 text-sm leading-5 font-medium">
                      {item.reply_text || item.evaluation_error || '无可见回复文本'}
                    </div>
                    <div className="text-muted-foreground mt-1 truncate text-xs">
                      {item.session_name} · {strategyName(item.strategy_primary)}
                    </div>
                    <div className="mt-2 grid grid-cols-4 gap-1 text-center text-[11px] tabular-nums">
                      <span className="bg-muted/45 rounded px-1 py-1">
                        总 {scoreText(item.raw_score)}
                      </span>
                      <span className="bg-muted/45 rounded px-1 py-1">
                        回 {scoreText(item.response_score)}
                      </span>
                      <span className="bg-muted/45 rounded px-1 py-1">
                        接 {scoreText(item.reception_score)}
                      </span>
                      <span className="bg-muted/45 rounded px-1 py-1">
                        推 {scoreText(item.conversation_score)}
                      </span>
                    </div>
                  </button>
                ))}
              </div>
            ) : (
              <div className="text-muted-foreground flex h-40 flex-col items-center justify-center gap-2 text-sm">
                <MessageCircleReply className="h-5 w-5" />
                没有符合条件的评估记录
              </div>
            )}
            {loadingMore && (
              <div className="text-muted-foreground flex items-center justify-center gap-2 py-4 text-xs">
                <LoaderCircle className="h-3.5 w-3.5 animate-spin" />
                继续加载…
              </div>
            )}
          </div>
        </aside>

        <main className="bg-card h-[760px] min-w-0 overflow-hidden rounded-xl border shadow-sm lg:h-full">
          <div className="h-full overflow-y-auto p-4 sm:p-5">
            {error && (
              <div className="border-destructive/30 bg-destructive/10 text-destructive mb-4 rounded-lg border px-4 py-3 text-sm">
                {error}
              </div>
            )}
            {detailLoading && detail?.effect_id !== selectedId ? (
              <div className="text-muted-foreground flex h-80 items-center justify-center gap-2 text-sm">
                <LoaderCircle className="h-4 w-4 animate-spin" />
                加载评估详情…
              </div>
            ) : detail?.effect_id === selectedId ? (
              <EvaluationDetail detail={detail} />
            ) : (
              <div className="text-muted-foreground flex h-80 flex-col items-center justify-center gap-3 text-sm">
                <div className="bg-muted flex h-12 w-12 items-center justify-center rounded-full">
                  <MessageCircleReply className="h-5 w-5" />
                </div>
                从左侧选择一条评估记录
              </div>
            )}
          </div>
        </main>
      </div>
    </div>
  )
}
