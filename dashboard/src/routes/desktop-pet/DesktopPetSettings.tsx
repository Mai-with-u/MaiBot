import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { DesktopPetCurrentTaskField } from '@/components/DesktopPetCurrentTaskField'
import { DesktopPetSkinField } from '@/components/DesktopPetSkinField'
import { DynamicConfigForm } from '@/components/dynamic-form'
import { BoardModule, BoardScreen } from '@/components/ui/board'
import { Button } from '@/components/ui/button'
import { useToast } from '@/hooks/use-toast'
import { getBotConfig, getBotConfigSchema, updateBotConfigSection } from '@/lib/config-api'
import { FieldHookRegistry } from '@/lib/field-hooks'

const settingsQueryKey = ['desktop-pet', 'settings'] as const
const settingsFieldHooks = new FieldHookRegistry()
settingsFieldHooks.register('desktop_pet.skin_id', DesktopPetSkinField)
settingsFieldHooks.register('desktop_pet.show_current_task', DesktopPetCurrentTaskField)

export function DesktopPetSettings({ onSaved }: { onSaved: () => void }) {
  const { t } = useTranslation()
  const { toast } = useToast()
  const queryClient = useQueryClient()
  const [changes, setChanges] = useState<Record<string, unknown>>({})
  const settingsQuery = useQuery({
    queryKey: settingsQueryKey,
    queryFn: async () => {
      const [config, botSchema] = await Promise.all([getBotConfig(), getBotConfigSchema()])
      const schema = botSchema.nested?.desktop_pet
      const values = config.desktop_pet
      if (!schema || !values || typeof values !== 'object' || Array.isArray(values)) {
        throw new Error(t('observation.pet.settingsUnavailable'))
      }
      return { schema, values: values as Record<string, unknown> }
    },
  })
  const settings = settingsQuery.data
  const save = useMutation({
    mutationFn: (patch: Record<string, unknown>) =>
      updateBotConfigSection('desktop_pet', patch, t('observation.pet.settingsSaveFailed')),
    onSuccess: (_result, patch) => {
      if (settings) {
        queryClient.setQueryData(settingsQueryKey, { ...settings, values: { ...settings.values, ...patch } })
      }
      setChanges({})
      toast({ title: t('observation.pet.settingsSaved') })
      onSaved()
    },
    meta: { suppressErrorToast: true },
  })

  return (
    <BoardModule id="desktop-pet-settings" code="SETTINGS" title={t('observation.pet.settings')} span={12}>
      <BoardScreen className="space-y-3 p-3">
        {settingsQuery.isError && (
          <div role="alert" className="flex flex-wrap items-center justify-between gap-2">
            <p className="text-destructive text-sm">{settingsQuery.error.message}</p>
            <Button type="button" size="sm" variant="outline" onClick={() => void settingsQuery.refetch()} disabled={settingsQuery.isFetching}>
              {t('common.retry')}
            </Button>
          </div>
        )}
        {settings && (
          <form className="space-y-3" onSubmit={(event) => {
            event.preventDefault()
            if (!save.isPending && Object.keys(changes).length > 0) save.mutate(changes)
          }}>
            <fieldset disabled={save.isPending} className="min-w-0">
              <DynamicConfigForm
                schema={settings.schema}
                values={{ ...settings.values, ...changes }}
                basePath="desktop_pet"
                hooks={settingsFieldHooks}
                onChange={(field, value) => {
                  setChanges((previous) => {
                    const next = { ...previous }
                    if (Object.is(settings.values[field], value)) delete next[field]
                    else next[field] = value
                    return next
                  })
                }}
              />
            </fieldset>
            {save.isError && <p role="alert" className="text-destructive text-sm">{save.error.message}</p>}
            <div className="flex justify-end">
              <Button type="submit" disabled={save.isPending || Object.keys(changes).length === 0}>
                {t(save.isPending ? 'observation.pet.settingsSaving' : 'common.save')}
              </Button>
            </div>
          </form>
        )}
        {settingsQuery.isPending && <p role="status" className="text-muted-foreground text-sm">{t('common.loading')}</p>}
      </BoardScreen>
    </BoardModule>
  )
}
