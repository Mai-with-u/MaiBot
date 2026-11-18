import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group'
import { getInstalledPlugins, type InstalledPlugin } from '@/lib/plugin-api'
import { getPluginType } from '@/routes/plugins/types'

interface PlatformSetupFormProps {
  value: string | null
  onChange: (pluginId: string | null) => void
  disabled: boolean
}

export function PlatformSetupForm({ value, onChange, disabled }: PlatformSetupFormProps) {
  const { t } = useTranslation()
  const [adapters, setAdapters] = useState<InstalledPlugin[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [revision, setRevision] = useState(0)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    void getInstalledPlugins({ forceRefresh: true }).then((plugins) => {
      if (!cancelled) setAdapters(plugins.filter((plugin) => plugin.builtin && getPluginType(plugin) === 'adapter'))
    }).catch((cause: unknown) => {
      if (!cancelled) setError(cause instanceof Error ? cause.message : t('setupPage.platform.loadFailed'))
    }).finally(() => {
      if (!cancelled) setLoading(false)
    })
    return () => { cancelled = true }
  }, [revision, t])

  return (
    <div className="space-y-4">
      <RadioGroup
        aria-label={t('setupPage.steps.chatPlatform.title')}
        value={value ?? 'local'}
        onValueChange={(next) => onChange(next === 'local' ? null : next)}
        disabled={disabled}
        className="gap-3"
      >
        <Label htmlFor="setup-platform-local" className="flex cursor-pointer items-start gap-3 border p-4">
          <RadioGroupItem id="setup-platform-local" value="local" />
          <span className="space-y-1">
            <span className="block font-medium">{t('setupPage.platform.local')}</span>
            <span className="text-muted-foreground block text-sm font-normal">{t('setupPage.platform.localHint')}</span>
          </span>
        </Label>
        {adapters.map((plugin) => (
          <Label key={plugin.id} htmlFor={`setup-platform-${plugin.id}`} className="flex cursor-pointer items-start gap-3 border p-4">
            <RadioGroupItem id={`setup-platform-${plugin.id}`} value={plugin.id} />
            <span className="space-y-1">
              <span className="block font-medium">{plugin.manifest.name}</span>
              <span className="text-muted-foreground block text-sm font-normal">{plugin.manifest.description}</span>
            </span>
          </Label>
        ))}
      </RadioGroup>
      {loading && <p role="status" className="text-muted-foreground text-sm">{t('setupPage.platform.loading')}</p>}
      {error && (
        <div role="alert" className="space-y-2">
          <p className="text-destructive text-sm">{error}</p>
          <Button variant="outline" disabled={disabled} onClick={() => setRevision((current) => current + 1)}>
            {t('setupPage.platform.retry')}
          </Button>
        </div>
      )}
      {!loading && !error && adapters.length === 0 && (
        <p className="text-muted-foreground text-sm">{t('setupPage.platform.empty')}</p>
      )}
      {value !== null && <p className="text-muted-foreground text-sm">{t('setupPage.platform.connectHint')}</p>}
    </div>
  )
}
