import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { DesktopPetCurrentTaskField } from '@/components/DesktopPetCurrentTaskField'
import { DesktopPetSkinField } from '@/components/DesktopPetSkinField'
import { DynamicConfigForm } from '@/components/dynamic-form'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { BoardModule, BoardScreen } from '@/components/ui/board'
import { Button } from '@/components/ui/button'
import { getBotConfig, getBotConfigSchema, updateBotConfigSection } from '@/lib/config-api'
import { FieldHookRegistry } from '@/lib/field-hooks'
import { resolveFieldLabel } from '@/lib/config-label'
import { useAutoSave } from '@/routes/config/bot/hooks/useAutoSave'
import type { ConfigSchemaResponse } from '@/types/config-schema'

const settingsQueryKey = ['desktop-pet', 'settings'] as const
const settingsFieldHooks = new FieldHookRegistry()
settingsFieldHooks.register('desktop_pet.skin_id', (props) => <DesktopPetSkinField {...props} autoSave />)
settingsFieldHooks.register('desktop_pet.show_current_task', DesktopPetCurrentTaskField)

export function DesktopPetSettings({ onSaved }: { onSaved: () => void }) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [changes, setChanges] = useState<Record<string, unknown>>({})
  const changesRef = useRef(changes)
  const [autoSaving, setAutoSaving] = useState(false)
  const [hasUnsavedChanges, setHasUnsavedChanges] = useState(false)
  const [saveError, setSaveError] = useState<Error | null>(null)
  const settingsQuery = useQuery({
    queryKey: settingsQueryKey,
    queryFn: async () => {
      const [config, schemaResponse] = await Promise.all([getBotConfig(), getBotConfigSchema()])
      const { schema: botSchema } = schemaResponse as unknown as ConfigSchemaResponse
      const schema = botSchema?.nested?.desktop_pet
      const values = config.desktop_pet
      if (!schema || !values || typeof values !== 'object' || Array.isArray(values)) {
        throw new Error(t('observation.pet.settingsUnavailable'))
      }
      return { schema, values: values as Record<string, unknown> }
    },
  })
  const settings = settingsQuery.data
  const save = useMutation({
    scope: { id: 'desktop-pet-settings' },
    mutationFn: (patch: Record<string, unknown>) =>
      updateBotConfigSection('desktop_pet', patch, t('observation.pet.settingsSaveFailed')),
    onSuccess: (_result, patch) => {
      queryClient.setQueryData<typeof settings>(settingsQueryKey, (current) =>
        current && { ...current, values: { ...current.values, ...patch } }
      )
      const remaining = { ...changesRef.current }
      for (const [field, value] of Object.entries(patch)) {
        if (Object.is(remaining[field], value)) delete remaining[field]
      }
      changesRef.current = remaining
      setChanges(remaining)
      setSaveError(null)
      onSaved()
    },
    meta: { suppressErrorToast: true },
  })
  const { mutateAsync } = save
  const schema = settings?.schema
  const saveSection = useCallback(async (_sectionName: string, data: unknown) => {
    const patch = data as Record<string, unknown>
    for (const field of schema?.fields ?? []) {
      const value = patch[field.name]
      if (typeof value !== 'number') continue
      const label = resolveFieldLabel(field)
      if (!Number.isFinite(value) || (field.type === 'integer' && !Number.isInteger(value))) {
        throw new Error(t('observation.pet.settingsInvalidNumber', { label }))
      }
      if (field.minValue !== undefined && value < field.minValue) {
        throw new Error(t('observation.pet.settingsMinValue', { label, min: field.minValue }))
      }
      if (field.maxValue !== undefined && value > field.maxValue) {
        throw new Error(t('observation.pet.settingsMaxValue', { label, max: field.maxValue }))
      }
    }
    await mutateAsync(patch)
  }, [mutateAsync, schema, t])
  const { triggerAutoSave, saveNow } = useAutoSave(
    settingsQuery.isPending, setAutoSaving, setHasUnsavedChanges,
    { saveSection, onSaveError: setSaveError }
  )

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
          <form noValidate className="space-y-3" onSubmit={(event) => event.preventDefault()}>
            <fieldset className="min-w-0">
              <DynamicConfigForm
                schema={settings.schema}
                values={{ ...settings.values, ...changes }}
                basePath="desktop_pet"
                hooks={settingsFieldHooks}
                onChange={(field, value) => {
                  const currentValue = field in changesRef.current ? changesRef.current[field] : settings.values[field]
                  if (Object.is(currentValue, value)) return
                  const next = { ...changesRef.current, [field]: value }
                  changesRef.current = next
                  setChanges(next)
                  setSaveError(null)
                  if (field === 'skin_id') void saveNow('desktop_pet', next)
                  else triggerAutoSave('desktop_pet', next)
                }}
              />
            </fieldset>
            {saveError && <Alert variant="destructive"><AlertDescription>{saveError.message}</AlertDescription></Alert>}
            <div className="flex items-center justify-end gap-3">
              <span role="status" className="text-muted-foreground text-xs">
                {t(autoSaving ? 'observation.pet.settingsSaving' : saveError ? 'observation.pet.settingsSaveFailed' : hasUnsavedChanges ? 'observation.pet.settingsAutoSaveHint' : 'observation.pet.settingsSaved')}
              </span>
              {saveError && (
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  disabled={autoSaving}
                  onClick={() => void saveNow('desktop_pet', { ...changesRef.current })}
                >
                  {t('common.retry')}
                </Button>
              )}
            </div>
          </form>
        )}
        {settingsQuery.isPending && <p role="status" className="text-muted-foreground text-sm">{t('common.loading')}</p>}
      </BoardScreen>
    </BoardModule>
  )
}
