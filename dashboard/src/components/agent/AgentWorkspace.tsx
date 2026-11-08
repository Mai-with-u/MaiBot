import { BrainCircuit, Check, ChevronRight, CircleAlert, Clock3, MessageSquare, Send, Wrench } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { Alert, AlertDescription } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { StatusLight, type LightStatus } from '@/components/ui/status-light'
import { PageFrame } from '@/components/ui/surface'
import { type AgentApi, type AgentEvent } from '@/lib/agent-api'
import { cn } from '@/lib/utils'


function itemText(item: AgentEvent['items'][number]) {
  return [...(item.summary_parts ?? []), ...(item.text_parts ?? []),
    ...(item.parts ?? []).filter(part => part.type === 'text').map(part => part.text)].filter(Boolean).join('\n')
}

function mergeTextEvents(events: AgentEvent[]): AgentEvent[] {
  const merged: AgentEvent[] = []
  for (const event of events) {
    const item = event.items[0]
    const previous = merged[merged.length - 1]
    const previousItem = previous?.items[0]
    // ACP 的连续文本事件是流式增量，直接拼接；用户输入、工具和消息类型切换都会分段。
    if (event.kind === 'step' && event.items.length === 1 &&
      (item.item_type === 'ReasoningItem' || item.item_type === 'AssistantMessageItem') &&
      (item.parts ?? []).every(part => part.type === 'text') &&
      previous?.kind === 'step' && previous.items.length === 1 &&
      previousItem?.item_type === item.item_type && previousItem.logical_turn_id === item.logical_turn_id &&
      (previousItem.parts ?? []).every(part => part.type === 'text')) {
      merged[merged.length - 1] = {
        ...previous,
        items: [{ ...previousItem, summary_parts: [], parts: [], text_parts: [itemText(previousItem) + itemText(item)] }],
      }
    } else {
      merged.push(event)
    }
  }
  return merged
}

function HistoryItem({ item }: { item: AgentEvent['items'][number] }) {
  const { t } = useTranslation()
  if (item.tool_call || item.item_type === 'FunctionCallOutputItem' || item.display_kind === 'tool_result') {
    const failed = item.success === false
    return <details className={cn('group rounded-lg border bg-muted/20 text-sm', failed && 'border-destructive/30 bg-destructive/5')}>
      <summary className="flex cursor-pointer list-none items-center gap-2 px-3 py-2">
        {item.tool_call ? <Wrench className="size-3.5 text-muted-foreground" /> : failed ? <CircleAlert className="size-3.5 text-destructive" /> : <Check className="size-3.5 text-muted-foreground" />}
        <span className="min-w-0 flex-1 truncate font-mono text-xs">{item.tool_call?.func_name ?? item.tool_name}</span>
        <span className="text-xs text-muted-foreground">{t(item.tool_call ? 'chat.agent.toolCall' : failed ? 'chat.agent.failed' : 'chat.agent.toolResult')}</span>
        <ChevronRight className="size-3.5 text-muted-foreground transition-transform group-open:rotate-90" />
      </summary>
      <pre className="max-h-80 overflow-auto whitespace-pre-wrap break-words border-t px-3 py-2 text-xs leading-relaxed">{item.tool_call ? JSON.stringify(item.tool_call.args, null, 2) : item.output}</pre>
    </details>
  }
  const text = itemText(item)
  if (!text) return null
  if (item.item_type === 'ReasoningItem') {
    // 思考默认折叠，只露出一行预览；展开后完整显示，避免长推理把回复挤到屏幕外。
    const preview = text.split('\n').find(line => line.trim()) ?? ''
    return <details data-agent-thought="true" className="group rounded-lg border bg-muted/20 text-sm">
      <summary className="flex cursor-pointer list-none items-center gap-2 px-3 py-2 text-muted-foreground">
        <ChevronRight className="size-3.5 shrink-0 transition-transform group-open:rotate-90" />
        <BrainCircuit className="size-3.5 shrink-0" />
        <span className="shrink-0 text-xs font-medium">{t('chat.agent.thought')}</span>
        <span className="min-w-0 flex-1 truncate text-xs group-open:hidden">{preview}</span>
      </summary>
      <p className="whitespace-pre-wrap break-words border-t px-3 py-2 font-mono text-xs leading-6 text-muted-foreground">{text}</p>
    </details>
  }
  return <div className="whitespace-pre-wrap break-words text-sm leading-7">{text}</div>
}

function stateLight(state: string, failed: boolean): LightStatus {
  if (failed) return 'error'
  if (state === 'thinking' || state === 'tool') return 'mixed'
  if (state === 'paused') return 'off'
  return state === 'idle' ? 'off' : 'on'
}
function AgentHistory({ submitted, api }: { submitted: AgentEvent[]; api: AgentApi }) {
  const { t } = useTranslation()
  const [events, setEvents] = useState<AgentEvent[]>([])
  const [queued, setQueued] = useState<AgentEvent[]>([])
  const [error, setError] = useState('')
  const [state, setState] = useState('idle')
  const [pending, setPending] = useState(false)
  const [resuming, setResuming] = useState(false)
  const scroll = useRef<HTMLDivElement>(null)
  const follow = useRef(true)
  useEffect(() => {
    if (follow.current && scroll.current) scroll.current.scrollTop = scroll.current.scrollHeight
  }, [events, queued, submitted, pending])
  useEffect(() => {
    const abort = new AbortController()
    let after = 0
    let timer: ReturnType<typeof setTimeout>
    const tick = async () => {
      try {
        const result = await api.events(after, abort.signal)
        if (!abort.signal.aborted) {
          if (result.events.length) {
            setEvents(current => [...current, ...result.events])
            after = result.events[result.events.length - 1].index + 1
          }
          setQueued(result.queued)
          setState(result.state)
          setPending(result.pending)
          setError(result.error)
        }
      } catch (cause) {
        if (!abort.signal.aborted) setError(String(cause))
      } finally {
        if (!abort.signal.aborted) timer = setTimeout(() => void tick(), 1000)
      }
    }
    void tick()
    return () => { abort.abort(); clearTimeout(timer) }
  }, [api])
  const committed = new Set(events.map(event => event.id))
  const visible = mergeTextEvents([...new Map([...submitted, ...queued, ...events].map(event => [event.id, event] as const)).values()]
    .sort((a, b) => Date.parse(a.time) - Date.parse(b.time)))
  return <div className="flex min-h-0 flex-1 flex-col">
    <div role="status" className="flex items-center gap-2 border-b bg-muted/10 px-5 py-2.5 text-xs text-muted-foreground">
      <StatusLight status={stateLight(state, Boolean(error))} decorative />
      {t(`chat.agent.${state}`)}
      <Badge variant="outline">ACP</Badge>
      {state === 'paused' && <Button size="sm" variant="outline" disabled={resuming} onClick={async () => {
        setResuming(true)
        try {
          const result = await api.resume(new AbortController().signal)
          setState(result.agent_state)
          setError(result.error)
        } catch (cause) { setError(String(cause)) }
        finally { setResuming(false) }
      }}>{t('chat.agent.resume')}</Button>}
      <span className="ml-auto tabular-nums">{t('chat.agent.eventCount', { count: visible.length })}</span>
    </div>
    <div ref={scroll} className="min-h-0 flex-1 overflow-y-auto px-4 py-6 md:px-8" onScroll={event => {
      const element = event.currentTarget
      follow.current = element.scrollHeight - element.scrollTop - element.clientHeight < 80
    }}>
      <div className="mx-auto max-w-4xl">
        {error && <Alert variant="destructive" className="mb-4"><CircleAlert className="size-4" /><AlertDescription className="break-words">{error}</AlertDescription></Alert>}
        {!visible.length && !error && <div className="flex min-h-60 flex-col items-center justify-center gap-3 text-muted-foreground"><BrainCircuit className="size-8 opacity-40" /><p className="text-sm">{t('chat.agent.noEvents')}</p></div>}
        {visible.map(event => {
          const input = event.kind === 'user'
          const Icon = input ? MessageSquare : BrainCircuit
          return <article key={event.id} className="relative border-l border-border/70 pb-7 pl-6 last:border-transparent">
            <span className={cn('absolute -left-3 top-0 flex size-6 items-center justify-center rounded-full border bg-background', input && 'text-primary')}><Icon className="size-3" /></span>
            <header className="mb-2 flex items-center gap-3 pt-0.5 text-xs text-muted-foreground">
              <span className={cn('font-medium', input ? 'text-primary' : 'text-foreground')}>{t(input ? 'chat.agent.userMessage' : event.kind === 'system' ? 'chat.agent.systemEvent' : 'chat.agent.stepEvent')}</span>
              {input && !committed.has(event.id) && <span className="rounded-full bg-muted px-2 py-0.5">{t('chat.agent.queued')}</span>}
              <time dateTime={event.time} className="ml-auto flex items-center gap-1 tabular-nums"><Clock3 className="size-3" />{new Date(event.time).toLocaleString()}</time>
            </header>
            <div className={cn('space-y-2', input && 'rounded-xl border border-primary/15 bg-primary/5 px-4 py-3')}>
              {event.items.map((item, index) => <HistoryItem key={index} item={item} />)}
            </div>
          </article>
        })}
        {pending && state !== 'paused' && <div className="flex items-center gap-2 pl-6 text-sm text-muted-foreground"><span className="size-1.5 animate-pulse rounded-full bg-primary" />{t('chat.agent.running')}</div>}
      </div>
    </div>
  </div>
}

export function AgentWorkspace({ api, titleKey }: { api: AgentApi; titleKey: string }) {
  const { t } = useTranslation()
  const messageKey = 'chat.agent.message'
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [submitted, setSubmitted] = useState<AgentEvent[]>([])
  return <PageFrame className="flex h-full min-h-0 flex-col">
    <header className="flex flex-wrap items-center gap-2 border-b p-4">
      <h1 className="mr-auto font-semibold">{t(titleKey)}</h1>
    </header>
    <AgentHistory submitted={submitted} api={api} />
    <form className="flex gap-2 border-t p-4" onSubmit={async event => {
      event.preventDefault()
      if (!text.trim() || busy) return
      setBusy(true)
      setError('')
      try {
        const { id, backend } = await api.message(text)
        setSubmitted(current => [...current, {
          id, backend, index: -1, time: new Date().toISOString(), kind: 'user',
          items: [{ item_type: 'UserMessageItem', parts: [{ type: 'text', text }] }],
        }])
        setText('')
      }
      catch (cause) { setError(String(cause)) }
      finally { setBusy(false) }
    }}>
      <Input value={text} onChange={event => setText(event.target.value)} disabled={busy} placeholder={t(messageKey)} aria-label={t(messageKey)} />
      <Button type="submit" disabled={busy || !text.trim()}><Send className="mr-1.5 size-4" />{t('chat.agent.send')}</Button>
    </form>
    {error && <p role="alert" className="px-4 pb-3 text-sm text-destructive">{error}</p>}
  </PageFrame>
}
