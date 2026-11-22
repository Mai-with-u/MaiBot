import { useRef, useState, type ReactElement } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from '@tanstack/react-router'
import { ChevronsUpDown, Play, RotateCw, Square, Wrench } from 'lucide-react'

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuPortal,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { useToast } from '@/hooks/use-toast'

import { controlHostBot, listHostBots, selectHostBot, type BotAction } from './api'
import { useHostRuntime } from './state'

const actionIcons = { start: Play, stop: Square, restart: RotateCw }

export function BotSwitcher({ children, submenu = false }: { children: ReactElement; submenu?: boolean }) {
  const { t } = useTranslation()
  const { toast } = useToast()
  const runtime = useHostRuntime()
  const [switching, setSwitching] = useState(false)
  const inFlight = useRef(new Set<string>())
  const [pending, setPending] = useState<Record<string, BotAction>>({})
  if (!runtime.host) return null

  const current = runtime.bots.find((bot) => bot.bot_id === runtime.selected_bot_id)
  const label = `${t('hostManager.multiBotManagement')} · ${t('hostManager.currentBot')}: ${current?.name ?? t('hostManager.noSelection')}`
  const select = async (id: string) => {
    if (switching || id === runtime.selected_bot_id) return
    setSwitching(true)
    try {
      await selectHostBot(id)
    } catch (reason) {
      toast({ variant: 'destructive', description: reason instanceof Error ? reason.message : String(reason) })
      setSwitching(false)
    }
  }

  async function control(id: string, action: BotAction) {
    if (inFlight.current.has(id)) return
    inFlight.current.add(id)
    setPending((current) => ({ ...current, [id]: action }))
    try {
      const result = await controlHostBot(id, action)
      if (result.status === 'failed') throw new Error(result.error || t('hostManager.operationFailed'))
    } catch (reason) {
      toast({ variant: 'destructive', description: reason instanceof Error ? reason.message : String(reason) })
    } finally {
      try {
        await listHostBots()
      } catch {
        toast({ variant: 'destructive', description: t('hostManager.refreshFailed') })
      } finally {
        inFlight.current.delete(id)
        setPending((current) => {
          const next = { ...current }
          delete next[id]
          return next
        })
      }
    }
  }

  const botOptions = (
    <>
      <DropdownMenuRadioGroup value={runtime.selected_bot_id ?? ''} onValueChange={(id) => void select(id)}>
        {runtime.bots.map((bot) => {
          const toggle = bot.pid === null ? 'start' : 'stop'
          const busy = switching || Boolean(pending[bot.bot_id]) || ['starting', 'migrating', 'stopping'].includes(bot.status)
          const statusKey = bot.status === 'crash_loop' ? 'crashLoop' : bot.status
          return (
            <div key={bot.bot_id} className="flex items-center gap-1">
              <DropdownMenuRadioItem value={bot.bot_id} disabled={switching} className="min-w-0 flex-1">
                <span className="min-w-0 truncate">{bot.name}</span>
                <span className="text-muted-foreground ml-auto shrink-0 pl-3 text-xs" aria-live="polite">
                  {pending[bot.bot_id] ? `${t(`hostManager.${pending[bot.bot_id]}`)}…` : t(`hostManager.status.${statusKey}`)}
                </span>
              </DropdownMenuRadioItem>
              {([toggle, 'restart'] as const).map((action) => {
                const Icon = actionIcons[action]
                const actionLabel = `${t(`hostManager.${action}`)} · ${bot.name}`
                return (
                  <DropdownMenuItem key={action} aria-label={actionLabel} title={actionLabel}
                    className="size-7 shrink-0 cursor-pointer justify-center p-0 text-muted-foreground"
                    disabled={busy || (action !== 'stop' && bot.initialization !== 'initialized')}
                    onSelect={(event) => { event.preventDefault(); void control(bot.bot_id, action) }}>
                    <Icon aria-hidden="true" className="size-3.5" strokeWidth={1.5} />
                  </DropdownMenuItem>
                )
              })}
            </div>
          )
        })}
      </DropdownMenuRadioGroup>
      <DropdownMenuSeparator />
      <DropdownMenuItem asChild>
        <Link to="/bots" className="gap-3">
          <Wrench aria-hidden="true" className="h-4 w-4" />
          <span>{t('hostManager.managePage')}</span>
        </Link>
      </DropdownMenuItem>
    </>
  )

  if (submenu) {
    return (
      <DropdownMenuSub>
        <DropdownMenuSubTrigger disabled={switching} aria-label={label} title={label} className="gap-3 px-3 py-2">
          <ChevronsUpDown aria-hidden="true" />
          <span>{t('hostManager.multiBotManagement')}</span>
        </DropdownMenuSubTrigger>
        <DropdownMenuPortal>
          <DropdownMenuSubContent sideOffset={8} className="z-[60] max-h-80 max-w-[calc(100vw-2rem)] overflow-y-auto">
            {botOptions}
          </DropdownMenuSubContent>
        </DropdownMenuPortal>
      </DropdownMenuSub>
    )
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild disabled={switching} aria-label={label} title={label}>
        {children}
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="max-h-80 max-w-[calc(100vw-2rem)] overflow-y-auto">
        {botOptions}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
