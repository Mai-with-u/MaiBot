/**
 * 麦麦观察首页 - 桌宠卡片
 *
 * 开启桌宠后出现在图表行。预览区是当前形象和最近一句气泡，下方是状态行和输入条：
 * 输入的话投递到主人聊天（和桌宠窗口里说话同一个会话），待办键列出未完成的待办。
 */
import { Link } from '@tanstack/react-router'
import { ArrowRight, ListTodo, Loader2, Send } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'

import { DesktopPetPreview } from '@/components/desktop-pet-preview'
import { BoardScreen } from '@/components/ui/board'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { StatusLight, type LightStatus } from '@/components/ui/status-light'
import {
  desktopPetCardState,
  sendDesktopPetMessage,
  setDesktopPetVisible,
  type DesktopPetCardState,
  type DesktopPetStatus,
} from '@/lib/desktop-pet-api'
import { buildSearchNavigationPath } from '@/lib/config-search-navigation'
import '@/styles/desktop-pet-card.css'

const MESSAGE_MAX_LENGTH = 2000

const LIGHTS: Record<DesktopPetCardState, LightStatus> = {
  idle: 'on',
  thinking: 'mixed',
  hidden: 'off',
  starting: 'mixed',
  waiting: 'mixed',
  error: 'error',
}

/** 卡片标题栏右侧：跳到 Bot 配置里的桌宠设置 */
export function DesktopPetCardActions() {
  const { t } = useTranslation()
  const url = new URL(buildSearchNavigationPath('/config/bot', 'desktop_pet.enabled'), window.location.origin)
  return (
    <Link to={url.pathname} search={Object.fromEntries(url.searchParams)} data-board-link="true">
      {t('observation.pet.settings')} <ArrowRight aria-hidden="true" />
    </Link>
  )
}

export function DesktopPetCardBody({
  status,
  onChanged,
}: {
  status: DesktopPetStatus
  /** 显示窗口或发出消息后重新读取状态 */
  onChanged: () => void
}) {
  const { t } = useTranslation()
  const state = desktopPetCardState(status)
  const [draft, setDraft] = useState('')
  const [sending, setSending] = useState(false)
  const [showing, setShowing] = useState(false)
  const [actionError, setActionError] = useState('')
  const composerEnabled = state === 'idle' || state === 'thinking' || state === 'hidden'
  const showFigure = composerEnabled
  const bubble = state === 'thinking' ? '· · ·' : state === 'idle' ? status.bubble : ''

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    const text = draft.trim()
    if (!text || sending) return
    setSending(true)
    setActionError('')
    try {
      await sendDesktopPetMessage(text)
      setDraft('')
      onChanged()
    } catch (error) {
      setActionError(error instanceof Error ? error.message : t('observation.pet.sendFailed'))
    } finally {
      setSending(false)
    }
  }

  const show = async () => {
    setShowing(true)
    setActionError('')
    try {
      await setDesktopPetVisible(true)
      onChanged()
    } catch (error) {
      setActionError(error instanceof Error ? error.message : t('observation.pet.showFailed'))
    } finally {
      setShowing(false)
    }
  }

  const center =
    state === 'waiting'
      ? { title: t('observation.pet.waitingTitle'), hint: t('observation.pet.waitingHint') }
      : state === 'starting'
        ? { title: t('observation.pet.startingTitle'), hint: '' }
        : state === 'error'
          ? {
              title: t('observation.pet.errorTitle'),
              hint: status.error || (status.supported ? '' : t('observation.pet.unsupported')),
            }
          : null

  const meta =
    state === 'thinking'
      ? t('observation.pet.thinkingMeta')
      : state === 'hidden'
        ? t('observation.pet.hiddenMeta')
        : ''

  return (
    <BoardScreen className="flex flex-col !p-0" >
      <div data-desktop-pet-stage="true" data-state={state} className="relative min-h-0 flex-1 overflow-hidden">
        {showFigure && (
          <div data-desktop-pet-figure="true" data-ghost={state === 'hidden' ? 'true' : 'false'}>
            <DesktopPetPreview
              skinId={status.skin_id}
              mood={state === 'thinking' ? 'thinking' : state === 'hidden' ? 'still' : 'idle'}
            />
          </div>
        )}
        {bubble && (
          <p data-desktop-pet-bubble="true" data-thinking={state === 'thinking' ? 'true' : 'false'}>
            {bubble}
          </p>
        )}
        {center && (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-1.5 px-4 text-center">
            <span
              className={state === 'error' ? 'text-destructive text-sm font-semibold' : 'text-sm font-semibold'}
            >
              {center.title}
            </span>
            {center.hint && (
              <span className="text-muted-foreground text-xs leading-relaxed break-words">{center.hint}</span>
            )}
          </div>
        )}
      </div>

      <div data-desktop-pet-footer="true" className="flex shrink-0 flex-col gap-2 px-3 pt-2 pb-3">
        <div className="flex min-w-0 items-center gap-2 text-xs">
          <StatusLight status={LIGHTS[state]} decorative />
          <span className="font-semibold whitespace-nowrap">{t(`observation.pet.state.${state}`)}</span>
          <span className="text-muted-foreground min-w-0 truncate">
            {meta || t(`observation.pet.mode.${status.mode}`)}
          </span>
          <span className="flex-1" />
          {state === 'hidden' && (
            <Button type="button" variant="outline" size="xs" aria-pressed={!showing} disabled={showing} onClick={() => void show()}>
              {showing && <Loader2 className="animate-spin" />}
              {t('observation.pet.show')}
            </Button>
          )}
        </div>

        {composerEnabled && (
          <form className="flex items-center gap-1.5" onSubmit={(event) => void submit(event)}>
            <Input
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              maxLength={MESSAGE_MAX_LENGTH}
              placeholder={t('observation.pet.inputPlaceholder')}
              aria-label={t('observation.pet.inputPlaceholder')}
              controlSize="sm"
              className="min-w-0 flex-1"
              disabled={sending}
            />
            <Button type="submit" size="sm" disabled={sending || !draft.trim()}>
              {sending ? <Loader2 className="animate-spin" /> : <Send />}
              {t('observation.pet.send')}
            </Button>
            <Popover>
              <PopoverTrigger asChild>
                <Button
                  type="button"
                  variant="outline"
                  size="icon-sm"
                  className="relative"
                  aria-label={t('observation.pet.todosLabel', { count: status.todos.pending })}
                  title={t('observation.pet.todos')}
                >
                  <ListTodo />
                  {status.todos.pending > 0 && (
                    <span data-desktop-pet-todo-badge="true" aria-hidden="true">
                      {status.todos.pending > 99 ? '99+' : status.todos.pending}
                    </span>
                  )}
                </Button>
              </PopoverTrigger>
              <PopoverContent align="end" className="w-72 p-0">
                <div className="border-b px-3 py-2 text-xs font-semibold">
                  {t('observation.pet.todos')} · {status.todos.pending}
                </div>
                {status.todos.items.length === 0 ? (
                  <p className="text-muted-foreground px-3 py-4 text-xs">{t('observation.pet.todosEmpty')}</p>
                ) : (
                  <ul className="max-h-64 overflow-y-auto text-sm">
                    {status.todos.items.map((item) => (
                      <li key={item.id} className="border-b border-dashed px-3 py-2 break-words last:border-b-0">
                        {item.text}
                        {item.due_at && (
                          <span className="text-muted-foreground mt-0.5 block font-mono text-[11px]">
                            {new Date(item.due_at * 1000).toLocaleString()}
                          </span>
                        )}
                      </li>
                    ))}
                  </ul>
                )}
                {status.todos.pending > status.todos.items.length && (
                  <p className="text-muted-foreground border-t px-3 py-2 text-[11px]">
                    {t('observation.pet.todosMore', { count: status.todos.pending - status.todos.items.length })}
                  </p>
                )}
              </PopoverContent>
            </Popover>
          </form>
        )}
        {actionError && (
          <p role="alert" className="text-destructive text-xs break-words">
            {actionError}
          </p>
        )}
      </div>
    </BoardScreen>
  )
}
