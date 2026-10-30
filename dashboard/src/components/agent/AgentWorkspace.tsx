import { BrainCircuit, Check, ChevronRight, CircleAlert, Clock3, MessageSquare, Monitor, Send, Wrench } from 'lucide-react'
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
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
        <span className="text-xs text-muted-foreground">{t(item.tool_call ? 'chat.computer.toolCall' : failed ? 'chat.computer.failed' : 'chat.computer.toolResult')}</span>
        <ChevronRight className="size-3.5 text-muted-foreground transition-transform group-open:rotate-90" />
      </summary>
      <pre className="max-h-80 overflow-auto whitespace-pre-wrap break-words border-t px-3 py-2 text-xs leading-relaxed">{item.tool_call ? JSON.stringify(item.tool_call.args, null, 2) : item.output}</pre>
    </details>
  }
  const text = itemText(item)
  if (!text) return null
  const reasoning = item.item_type === 'ReasoningItem'
  return <div className={cn('whitespace-pre-wrap break-words text-sm leading-7', reasoning && 'rounded-lg bg-muted/30 px-3 py-2 text-muted-foreground')}>
    {reasoning && <p className="mb-1 flex items-center gap-1.5 text-xs font-medium"><BrainCircuit className="size-3.5" />{t('chat.computer.thought')}</p>}
    {text}
  </div>
}

function AgentHistory({ submitted, api, onDesktopEnabled }: { submitted: AgentEvent[]; api: AgentApi; onDesktopEnabled: (enabled: boolean) => void }) {
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
          onDesktopEnabled(result.desktop_enabled)
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
  }, [api, onDesktopEnabled])
  const working = state === 'thinking' || state === 'tool'
  const committed = new Set(events.map(event => event.id))
  const visible = mergeTextEvents([...new Map([...submitted, ...queued, ...events].map(event => [event.id, event] as const)).values()]
    .sort((a, b) => Date.parse(a.time) - Date.parse(b.time)))
  return <div className="flex min-h-0 flex-1 flex-col">
    <div role="status" className="flex items-center gap-2 border-b bg-muted/10 px-5 py-2.5 text-xs text-muted-foreground">
      <span className={cn('size-1.5 rounded-full bg-muted-foreground/40', working && 'animate-pulse bg-primary')} />
      {t(`chat.computer.${state}`)}
      <span className="rounded bg-muted px-2 py-0.5">ACP</span>
      {state === 'paused' && <Button size="sm" variant="outline" disabled={resuming} onClick={async () => {
        setResuming(true)
        try {
          const result = await api.resume(new AbortController().signal)
          setState(result.agent_state)
          setError(result.error)
        } catch (cause) { setError(String(cause)) }
        finally { setResuming(false) }
      }}>{t('chat.computer.resume')}</Button>}
      <span className="ml-auto tabular-nums">{t('chat.computer.eventCount', { count: visible.length })}</span>
    </div>
    <div ref={scroll} className="min-h-0 flex-1 overflow-y-auto px-4 py-6 md:px-8" onScroll={event => {
      const element = event.currentTarget
      follow.current = element.scrollHeight - element.scrollTop - element.clientHeight < 80
    }}>
      <div className="mx-auto max-w-4xl">
        {error && <p role="alert" className="mb-4 flex items-center gap-2 rounded-lg border border-destructive/20 bg-destructive/5 p-3 text-sm text-destructive"><CircleAlert className="size-4 shrink-0" />{error}</p>}
        {!visible.length && !error && <div className="flex min-h-60 flex-col items-center justify-center gap-3 text-muted-foreground"><BrainCircuit className="size-8 opacity-40" /><p className="text-sm">{t('chat.computer.noEvents')}</p></div>}
        {visible.map(event => {
          const input = event.kind === 'user'
          const Icon = input ? MessageSquare : BrainCircuit
          return <article key={event.id} className="relative border-l border-border/70 pb-7 pl-6 last:border-transparent">
            <span className={cn('absolute -left-3 top-0 flex size-6 items-center justify-center rounded-full border bg-background', input && 'text-primary')}><Icon className="size-3" /></span>
            <header className="mb-2 flex items-center gap-3 pt-0.5 text-xs text-muted-foreground">
              <span className={cn('font-medium', input ? 'text-primary' : 'text-foreground')}>{t(input ? 'chat.computer.userMessage' : event.kind === 'system' ? 'chat.computer.systemEvent' : 'chat.computer.stepEvent')}</span>
              {input && !committed.has(event.id) && <span className="rounded-full bg-muted px-2 py-0.5">{t('chat.computer.queued')}</span>}
              <time dateTime={event.time} className="ml-auto flex items-center gap-1 tabular-nums"><Clock3 className="size-3" />{new Date(event.time).toLocaleString()}</time>
            </header>
            <div className={cn('space-y-2', input && 'rounded-xl border border-primary/15 bg-primary/5 px-4 py-3')}>
              {event.items.map((item, index) => <HistoryItem key={index} item={item} />)}
            </div>
          </article>
        })}
        {pending && state !== 'paused' && <div className="flex items-center gap-2 pl-6 text-sm text-muted-foreground"><span className="size-1.5 animate-pulse rounded-full bg-primary" />{t('chat.computer.running')}</div>}
      </div>
    </div>
  </div>
}

export function AgentWorkspace({ api, titleKey, desktopView }: { api: AgentApi; titleKey: string; desktopView?: ReactNode }) {
  const { t } = useTranslation()
  const messageKey = 'chat.computer.message'
  const [desktopEnabled, setDesktopEnabled] = useState(false)
  const [desktop, setDesktop] = useState(false)
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [submitted, setSubmitted] = useState<AgentEvent[]>([])
  return <PageFrame className="flex h-full min-h-0 flex-col">
    <header className="flex flex-wrap items-center gap-2 border-b p-4">
      <h1 className="mr-auto font-semibold">{t(titleKey)}</h1>
      {desktopView && desktopEnabled && <Button size="sm" variant={desktop ? 'ghost' : 'secondary'} aria-pressed={!desktop} onClick={() => setDesktop(false)}><BrainCircuit className="mr-1.5 size-4" />{t('chat.computer.reasoning')}</Button>}
      {desktopView && desktopEnabled && <Button size="sm" variant={desktop ? 'secondary' : 'ghost'} aria-pressed={desktop} onClick={() => setDesktop(true)}><Monitor className="mr-1.5 size-4" />{t('chat.computerDesktop')}</Button>}
    </header>
    {desktop && desktopView ? desktopView : <AgentHistory submitted={submitted} api={api} onDesktopEnabled={setDesktopEnabled} />}
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
        setDesktop(false)
        setText('')
      }
      catch (cause) { setError(String(cause)) }
      finally { setBusy(false) }
    }}>
      <Input value={text} onChange={event => setText(event.target.value)} disabled={busy} placeholder={t(messageKey)} aria-label={t(messageKey)} />
      <Button type="submit" disabled={busy || !text.trim()}><Send className="mr-1.5 size-4" />{t('chat.computer.send')}</Button>
    </form>
    {error && <p role="alert" className="px-4 pb-3 text-sm text-destructive">{error}</p>}
  </PageFrame>
}
