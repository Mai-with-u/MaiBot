import { useState, type ReactElement } from 'react'
import { useTranslation } from 'react-i18next'

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { useToast } from '@/hooks/use-toast'

import { selectHostBot } from './api'
import { useHostRuntime } from './state'

export function BotSwitcher({ children }: { children: ReactElement }) {
  const { t } = useTranslation()
  const { toast } = useToast()
  const runtime = useHostRuntime()
  const [switching, setSwitching] = useState(false)
  if (!runtime.host) return null

  const current = runtime.bots.find((bot) => bot.bot_id === runtime.selected_bot_id)
  const label = `${t('hostManager.currentBot')}: ${current?.name ?? t('hostManager.noSelection')}`
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

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild disabled={switching} aria-label={label} title={label}>
        {children}
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="max-h-80 max-w-[calc(100vw-2rem)] overflow-y-auto">
        <DropdownMenuRadioGroup value={runtime.selected_bot_id ?? ''} onValueChange={(id) => void select(id)}>
          {runtime.bots.map((bot) => (
            <DropdownMenuRadioItem key={bot.bot_id} value={bot.bot_id} disabled={switching}>
              <span className="min-w-0 truncate">{bot.name}</span>
              <span className="text-muted-foreground ml-3 shrink-0 text-xs">{t(`hostManager.status.${bot.status}`)}</span>
            </DropdownMenuRadioItem>
          ))}
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
