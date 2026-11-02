import { Fragment } from 'react'

import { DynamicConfigForm } from '@/components/dynamic-form'
import { ConfigSectionDialog } from '@/components/dynamic-form/ConfigSectionDialog'
import { DashboardTabBar, DashboardTabTrigger } from '@/components/ui/dashboard-tabs'
import { Separator } from '@/components/ui/separator'
import { Tabs, TabsContent } from '@/components/ui/tabs'
import { fieldHooks } from '@/lib/field-hooks'
import { collectChatSettings, type ChatField } from '@/lib/chat-settings-schema'
import type { ConfigSchema } from '@/types/config-schema'

type ChatScope = 'group' | 'private'
export function ChatSettings({ schema, values, onChange, scope, onScopeChange, advancedVisible, quick = false, openDialog, onDialogChange }: {
  schema: ConfigSchema
  values: Record<string, unknown>
  onChange: (path: string[], value: unknown) => void
  scope: ChatScope
  onScopeChange: (scope: ChatScope) => void
  advancedVisible: boolean
  quick?: boolean
  openDialog: string | null
  onDialogChange: (id: string | null) => void
}) {
  const entries = collectChatSettings(schema).filter(({ field }) =>
    quick ? field['x-chat-settings']!.quick : advancedVisible || !field.advanced
  )
  const renderField = ({ field, schema: owner, path }: ChatField) => {
    const fieldPath = ['chat', ...path, field.name].join('.')
    const sectionValues = path.reduce<Record<string, unknown>>((current, key) => current[key] as Record<string, unknown>, values)
    const form = (
      <DynamicConfigForm
        schema={{ ...owner, fields: [{ ...field, advanced: false }], nested: owner.nested?.[field.name] ? { [field.name]: owner.nested[field.name] } : {} }}
        values={sectionValues}
        onChange={(name, value) => onChange([...path, ...name.split('.')], value)}
        basePath={['chat', ...path].join('.')}
        hooks={fieldHooks}
        advancedVisible={advancedVisible}
        sectionColumns={1}
      />
    )
    const dialog = field['x-chat-settings']!.dialog
    return dialog ? (
      <ConfigSectionDialog label={dialog} open={openDialog === fieldPath} onOpenChange={(open) => onDialogChange(open ? fieldPath : null)}>
        {form}
      </ConfigSectionDialog>
    ) : form
  }
  const renderScope = (selectedScope: 'common' | ChatScope) => {
    const rows = new Map<number, ChatField[]>()
    for (const entry of entries) {
      const layout = entry.field['x-chat-settings']!
      if (layout.scope !== selectedScope) continue
      const row = rows.get(layout.row) ?? []
      row.push(entry)
      rows.set(layout.row, row)
    }
    return [...rows.entries()].sort(([a], [b]) => a - b).map(([row, fields], index) => (
      <Fragment key={row}>
        {index > 0 && <Separator />}
        <div className="grid grid-cols-1 items-center gap-x-6 gap-y-3 py-3 md:grid-cols-2">
          {fields.map((entry) => (
            <div key={[...entry.path, entry.field.name].join('.')} className={fields.length === 1 ? 'min-w-0 md:col-span-2' : 'min-w-0'}>
              {renderField(entry)}
            </div>
          ))}
        </div>
      </Fragment>
    ))
  }
  const hasCommon = entries.some(({ field }) => field['x-chat-settings']!.scope === 'common')
  return (
    <div className="space-y-3">
      {hasCommon && <><div>{renderScope('common')}</div><Separator /></>}
      <Tabs value={scope} onValueChange={(value) => onScopeChange(value as ChatScope)} className="w-full space-y-3">
        <DashboardTabBar variant="grid" className="grid-cols-2" aria-label="聊天类型">
          <DashboardTabTrigger value="group" className="text-base font-semibold">群聊</DashboardTabTrigger>
          <DashboardTabTrigger value="private" className="text-base font-semibold">私聊</DashboardTabTrigger>
        </DashboardTabBar>
        <TabsContent key={scope} value={scope} className="mt-0">{renderScope(scope)}</TabsContent>
      </Tabs>
    </div>
  )
}
