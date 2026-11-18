import { type ReactNode, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { DataPanel } from '@/components/ui/surface'

import { archiveHostBot, cloneHostBot, type HostBot, renameHostBot } from './api'

export function BotOperations({
  bot,
  leadingActions,
  onChanged,
}: {
  bot: HostBot
  leadingActions?: ReactNode
  onChanged: () => Promise<void>
}) {
  const { t } = useTranslation()
  const [action, setAction] = useState<'rename' | 'clone' | 'archive' | null>(null)
  const [name, setName] = useState('')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')

  async function apply() {
    if (!action || busy) return
    setBusy(true)
    setMessage('')
    try {
      if (action === 'rename') await renameHostBot(bot.bot_id, name.trim())
      if (action === 'clone') await cloneHostBot(bot.bot_id, name.trim())
      if (action === 'archive') await archiveHostBot(bot.bot_id)
      setAction(null)
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error))
    } finally {
      await onChanged()
      setBusy(false)
    }
  }

  return (
    <div className="space-y-2.5 pt-0.5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        {leadingActions && <div className="flex flex-wrap items-center gap-1.5">{leadingActions}</div>}
        <div className="flex flex-wrap items-center gap-1.5">
          {(['rename', 'clone', 'archive'] as const).map((value) => (
            <Button
              key={value}
              size="sm"
              variant="outline"
              aria-pressed={action === value}
              disabled={busy}
              onClick={() => {
                setAction((current) => (current === value ? null : value))
                setName(value === 'rename' ? bot.name : '')
                setMessage('')
              }}
            >
              {t(`hostOperations.${value}`)}
            </Button>
          ))}
        </div>
      </div>
      {action && (
        <DataPanel className="space-y-2.5 p-3">
          <p className="text-muted-foreground text-xs leading-relaxed">
            {t(`hostOperations.${action === 'archive' && !bot.owned ? 'unregister' : action}Hint`)}
          </p>
          <div className="flex flex-wrap items-center gap-2">
            {action !== 'archive' && (
              <Input
                aria-label={t('hostManager.botName')}
                value={name}
                maxLength={100}
                disabled={busy}
                className="h-8 min-w-[10rem] flex-1 text-xs"
                onChange={(event) => setName(event.target.value)}
              />
            )}
            <Button
              size="sm"
              variant={action === 'archive' ? 'destructive' : 'default'}
              disabled={busy || (action !== 'archive' && !name.trim())}
              onClick={() => void apply()}
            >
              {t(busy ? 'hostOperations.working' : 'hostOperations.confirm')}
            </Button>
            <Button size="sm" variant="outline" disabled={busy} onClick={() => setAction(null)}>
              {t('hostOperations.cancel')}
            </Button>
          </div>
        </DataPanel>
      )}
      {message && (
        <p role="alert" className="text-destructive text-xs">
          {message}
        </p>
      )}
    </div>
  )
}
