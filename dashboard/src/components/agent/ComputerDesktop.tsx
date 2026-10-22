import { useEffect, useRef, useState, type MouseEvent } from 'react'
import { useTranslation } from 'react-i18next'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import {
  controlComputer, getComputerFrame, getComputerStatus,
  inputComputer, pollComputerOperation, type ComputerStatus,
} from '@/lib/computer-api'
import { cn } from '@/lib/utils'

export function ComputerDesktop() {
  const { t } = useTranslation()
  const [status, setStatus] = useState<ComputerStatus | null>(null)
  const [frame, setFrame] = useState('')
  const [error, setError] = useState('')
  const [connectionError, setConnectionError] = useState('')
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const locked = useRef(false)
  const controller = useRef<AbortController | null>(null)
  const refresh = useRef<() => void>(() => {})

  useEffect(() => {
    const abort = new AbortController()
    controller.current = abort
    let timer: ReturnType<typeof setTimeout>
    let polling = false
    // 每次完成后再取下一帧，慢沙盒不会堆积截图请求；离开页面即停止轮询。
    const tick = async () => {
      if (polling || abort.signal.aborted) return
      polling = true
      clearTimeout(timer)
      try {
        const current = await getComputerStatus(abort.signal)
        setStatus(current)
        if (current.enabled && current.desktop_enabled && current.sandbox_running) {
          const image = await getComputerFrame(abort.signal)
          if (!abort.signal.aborted) setFrame(URL.createObjectURL(image))
        } else {
          setFrame('')
        }
        setConnectionError(current.error)
      } catch (cause) {
        if (!abort.signal.aborted) setConnectionError(String(cause))
      } finally {
        polling = false
        if (!abort.signal.aborted) timer = setTimeout(() => void tick(), 1000)
      }
    }
    refresh.current = () => void tick()
    void tick()
    return () => {
      abort.abort()
      clearTimeout(timer)
    }
  }, [])

  useEffect(() => () => { if (frame) URL.revokeObjectURL(frame) }, [frame])

  const run = async (action: (signal: AbortSignal) => Promise<void>) => {
    const signal = controller.current?.signal
    if (locked.current || !signal || signal.aborted) return
    locked.current = true
    setBusy(true)
    setError('')
    try {
      await action(signal)
      refresh.current()
    } catch (cause) {
      if (!signal.aborted) setError(String(cause))
    } finally {
      locked.current = false
      if (!signal.aborted) setBusy(false)
    }
  }

  const input = (actions: Record<string, unknown>[]) => run(async signal => {
    if (!status?.takeover) throw new Error(t('chat.computer.takeoverFirst'))
    let result = await inputComputer(actions, signal)
    while (result.status === 'running' && !signal.aborted) {
      // 查询原操作，不重复提交键鼠输入。
      await new Promise(resolve => setTimeout(resolve, 500))
      if (signal.aborted) return
      result = await pollComputerOperation(result.operation_id, signal)
    }
    if (!result.success) throw new Error(result.error || result.capture_error || JSON.stringify(result))
  })

  const click = (event: MouseEvent<HTMLImageElement>, button: 'left' | 'right') => {
    event.preventDefault()
    if (!status?.takeover || busy) return
    const image = event.currentTarget
    const bounds = image.getBoundingClientRect()
    void input([{
      action: 'click', button,
      x: Math.min(image.naturalWidth - 1, Math.max(0, Math.floor((event.clientX - bounds.left) * image.naturalWidth / bounds.width))),
      y: Math.min(image.naturalHeight - 1, Math.max(0, Math.floor((event.clientY - bounds.top) * image.naturalHeight / bounds.height))),
    }])
  }

  const state = !status ? 'connecting' : !status.enabled ? 'disabled' : !status.sandbox_running ? 'stopped'
    : status.takeover ? 'manual' : status.paused ? 'paused' : status.agent_state === 'idle' ? 'resting' : 'autonomous'

  return (
    <section className="flex min-h-0 flex-1 flex-col overflow-y-auto p-4 md:p-6">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <span className="mr-auto text-sm text-muted-foreground" role="status">
          {t(`chat.computer.${state}`)}{status?.pending_request ? ` · ${t('chat.computer.running')}` : ''}
        </span>
        {(['pause', 'takeover', 'resume', 'stop'] as const).map(operation => (
          <Button key={operation} size="sm" variant="outline" disabled={busy || !status?.enabled}
            onClick={() => void run(async signal => { setStatus(await controlComputer(operation, signal)) })}>
            {t(`chat.computer.${operation}`)}
          </Button>
        ))}
        <Button size="sm" variant="ghost" onClick={() => refresh.current()}>{t('chat.computer.refresh')}</Button>
      </div>
      <p className="mb-3 text-xs text-muted-foreground">{t('chat.computer.help')}</p>
      {(error || connectionError) && <p role="alert" className="mb-3 whitespace-pre-wrap break-words text-sm text-destructive">{error || connectionError}</p>}
      {frame ? (
        <img src={frame} alt={t('chat.computer.screen')} draggable={false}
          className={cn('block h-auto w-full rounded-lg border bg-muted', status?.takeover && 'cursor-crosshair ring-2 ring-primary')}
          onClick={event => click(event, 'left')} onContextMenu={event => click(event, 'right')} />
      ) : <div className="flex min-h-48 items-center justify-center rounded-lg border bg-muted/30 text-sm text-muted-foreground">{t(`chat.computer.${state}`)}</div>}
      <form className="my-3 flex flex-wrap gap-2" onSubmit={event => {
        event.preventDefault()
        if (text) void input([{ action: 'type', text }])
      }}>
        <Input className="min-w-40 flex-1" value={text} onChange={event => setText(event.target.value)}
          disabled={!status?.takeover || busy} aria-label={t('chat.computer.text')} placeholder={t('chat.computer.text')} />
        <Button type="submit" disabled={!status?.takeover || busy || !text}>{t('chat.computer.type')}</Button>
        {['ENTER', 'ESC'].map(key => <Button key={key} type="button" variant="outline" disabled={!status?.takeover || busy}
          onClick={() => void input([{ action: 'keys', keys: [key] }])}>{key === 'ENTER' ? t('chat.computer.enter') : 'Esc'}</Button>)}
      </form>
    </section>
  )
}
