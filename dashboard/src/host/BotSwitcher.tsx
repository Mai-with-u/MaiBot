import { useState, type ReactElement } from 'react'
import { useTranslation } from 'react-i18next'
import { ChevronsUpDown } from 'lucide-react'

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuPortal,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { useToast } from '@/hooks/use-toast'

import { selectHostBot } from './api'
import { useHostRuntime } from './state'

export function BotSwitcher({ children, submenu = false }: { children: ReactElement; submenu?: boolean }) {
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

  const botOptions = (
    <DropdownMenuRadioGroup value={runtime.selected_bot_id ?? ''} onValueChange={(id) => void select(id)}>
      {runtime.bots.map((bot) => (
        <DropdownMenuRadioItem key={bot.bot_id} value={bot.bot_id} disabled={switching}>
          <span className="min-w-0 truncate">{bot.name}</span>
          <span className="text-muted-foreground ml-3 shrink-0 text-xs">{t(`hostManager.status.${bot.status}`)}</span>
        </DropdownMenuRadioItem>
      ))}
    </DropdownMenuRadioGroup>
  )

  if (submenu) {
    return (
      <DropdownMenuSub>
        <DropdownMenuSubTrigger disabled={switching} aria-label={label} title={label} className="gap-3 px-3 py-2">
          <ChevronsUpDown aria-hidden="true" />
          <span>{t('hostManager.currentBot')}</span>
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
