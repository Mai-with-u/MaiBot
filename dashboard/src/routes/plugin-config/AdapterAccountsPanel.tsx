import { useEffect, useState } from 'react'
import { ChevronDown, Loader2 } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { useToast } from '@/hooks/use-toast'
import { getBotConfig, updateBotConfigSection } from '@/lib/config-api'
import { BotPlatformAccountsHook } from '../config/bot/hooks/complexFieldHooks'
import { getNestedRecord } from './utils'

function useAdapterAccountsEditor(enabled: boolean) {
  const { toast } = useToast()
  const [accounts, setAccounts] = useState<Record<string, unknown> | null>(null)
  const [savedAccounts, setSavedAccounts] = useState<Record<string, unknown> | null>(null)
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  const dirty = JSON.stringify(accounts) !== JSON.stringify(savedAccounts)

  useEffect(() => {
    if (!enabled) return
    let cancelled = false
    void getBotConfig().then((config) => {
      const bot = getNestedRecord(config, 'bot')
      if (!bot) throw new Error('配置中缺少 bot 节')
      // 只编辑和保存账号字段，避免覆盖昵称等其他基础设置。
      const initial = { platform: bot.platform, qq_account: bot.qq_account, platforms: bot.platforms }
      if (!cancelled) {
        setAccounts(initial)
        setSavedAccounts(initial)
      }
    }).catch((error: unknown) => {
      if (!cancelled) setError(error instanceof Error ? error.message : '读取平台账号失败')
    })
    return () => { cancelled = true }
  }, [enabled])

  const updateAccountField = (field: string, value: unknown) => {
    setAccounts((current) => current ? { ...current, [field]: value } : current)
  }

  const saveAccounts = async () => {
    if (!accounts) return
    setSaving(true)
    setError('')
    try {
      await updateBotConfigSection('bot', accounts)
      setSavedAccounts(accounts)
      toast({ title: '备用平台账号已保存' })
    } catch (error) {
      setError(error instanceof Error ? error.message : '保存平台账号失败')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="space-y-3">
      {error && <p className="text-destructive text-sm" role="alert">{error}</p>}
      {accounts ? (
        <>
          <fieldset disabled={saving} className="min-w-0 space-y-3">
            <BotPlatformAccountsHook
              fieldPath="bot.platform"
              value={accounts.platform}
              parentValues={accounts}
              onChange={(value) => updateAccountField('platform', value)}
              onParentChange={updateAccountField}
            />
          </fieldset>
          <div className="flex justify-end">
            <Button size="sm" disabled={!dirty || saving} onClick={() => void saveAccounts()}>
              {saving && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              保存备用账号
            </Button>
          </div>
        </>
      ) : !error && <p className="text-muted-foreground text-sm">正在读取平台账号…</p>}
    </div>
  )
}

export function AdapterAccountsPanel() {
  const [open, setOpen] = useState(false)
  const [initialized, setInitialized] = useState(false)
  // 编辑状态保存在浮层外，首次展开才读取账号，关闭后保留未保存内容。
  const editor = useAdapterAccountsEditor(initialized)

  return (
    <Popover
      open={open}
      onOpenChange={(nextOpen) => {
        setOpen(nextOpen)
        if (nextOpen) setInitialized(true)
      }}
    >
      <PopoverTrigger asChild>
        <Button variant="ghost" size="sm" className="shrink-0 gap-2">
          <span>平台账号</span>
          <ChevronDown className={`h-4 w-4 transition-transform ${open ? 'rotate-180' : ''}`} />
        </Button>
      </PopoverTrigger>
      <PopoverContent
        align="end"
        sideOffset={8}
        aria-label="平台账号"
        className="w-[min(32rem,calc(100vw-2rem))] max-h-[min(36rem,var(--radix-popover-content-available-height))] overflow-y-auto"
      >
        {editor}
      </PopoverContent>
    </Popover>
  )
}
