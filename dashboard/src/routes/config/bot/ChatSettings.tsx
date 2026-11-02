import { DynamicConfigForm } from '@/components/dynamic-form'
import { ConfigSectionDialog } from '@/components/dynamic-form/ConfigSectionDialog'
import { DashboardFolderPanel, DashboardFolderTabBar } from '@/components/ui/dashboard-tabs'
import { Tabs, TabsContent, TabsTrigger } from '@/components/ui/tabs'
import { fieldHooks } from '@/lib/field-hooks'
import { collectChatSettings, type ChatField } from '@/lib/chat-settings-schema'
import type { ConfigSchema, FieldSchema } from '@/types/config-schema'
import '@/styles/chat-settings.css'

type ChatScope = 'group' | 'private'

interface ChatGroup {
  title: string
  fields: string[]
  titleSwitch?: string
}

// 面板分两栏；未列出的字段落在第一组，新增配置不会从页面上消失。
const CHAT_PANELS: Record<ChatScope, ChatGroup[][]> = {
  group: [
    [{
      title: '回复节奏',
      fields: [
        'max_context_size',
        'talk_value',
        'message_debounce_mode',
        'group_quote_frequency',
        'no_action_backoff_base_seconds',
        'no_action_backoff_cap_seconds',
        'no_action_backoff_start_count',
        'no_action_backoff_bypass_pending_count',
      ],
    }],
    [
      { title: '触发回复', fields: ['mentioned_bot_reply', 'inevitable_at_reply', 'enable_reply_at', 'planner_message_steering'] },
      { title: '动态发言规则', titleSwitch: 'enable_talk_value_rules', fields: ['talk_value_rules'] },
    ],
  ],
  private: [
    [{ title: '回复节奏', fields: ['max_private_context_size', 'private_message_debounce_mode', 'private_quote_frequency'] }],
    [{ title: '暂停回复', titleSwitch: 'private_reply_paused', fields: ['private_reply_pause_rules'] }],
  ],
}

// 文件夹标签已经说明了范围，名称里不再重复「群聊 / 私聊」。
const withoutScopePrefix = (label: FieldSchema['label']): FieldSchema['label'] => {
  const strip = (text: string) => text.replace(/^(群聊|私聊)/, '')
  if (typeof label === 'string') return strip(label)
  return label && Object.fromEntries(Object.entries(label).map(([language, text]) => [language, strip(text)]))
}

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
  const renderField = ({ field, schema: owner, path }: ChatField, displayField: FieldSchema = field) => {
    const fieldPath = ['chat', ...path, field.name].join('.')
    const sectionValues = path.reduce<Record<string, unknown>>((current, key) => current[key] as Record<string, unknown>, values)
    const form = (
      <DynamicConfigForm
        schema={{ ...owner, fields: [{ ...displayField, advanced: false }], nested: owner.nested?.[field.name] ? { [field.name]: owner.nested[field.name] } : {} }}
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
  const hasCommon = entries.some(({ field }) => field['x-chat-settings']!.scope === 'common')
  const scopeEntries = entries.filter(({ field }) => field['x-chat-settings']!.scope === scope)
  const panels = CHAT_PANELS[scope]
  const listedFields = new Set(panels.flat().flatMap((group) => [...group.fields, group.titleSwitch]))
  const unlistedEntries = scopeEntries.filter(({ field }) => !listedFields.has(field.name))
  const findEntry = (name: string | undefined) => scopeEntries.find(({ field }) => field.name === name)
  const renderRow = (entry: ChatField) => (
    <div key={[...entry.path, entry.field.name].join('.')} data-chat-settings-row="true">
      {renderField(entry, { ...entry.field, label: withoutScopePrefix(entry.field.label) })}
    </div>
  )

  return (
    <Tabs value={scope} onValueChange={(value) => onScopeChange(value as ChatScope)} className="w-full">
      <DashboardFolderTabBar
        data-config-page-folder-tabs={quick ? undefined : 'true'}
        aria-label="聊天类型"
        trailing={hasCommon && (
          <div data-chat-settings-globals="true">
            {entries.filter(({ field }) => field['x-chat-settings']!.scope === 'common').map((entry) => (
              <div key={[...entry.path, entry.field.name].join('.')} className="min-w-0">{renderField(entry)}</div>
            ))}
          </div>
        )}
      >
        <TabsTrigger value="group">群聊</TabsTrigger>
        <TabsTrigger value="private">私聊</TabsTrigger>
      </DashboardFolderTabBar>
      <TabsContent key={scope} value={scope} className="mt-0">
        <DashboardFolderPanel data-chat-settings-panel="true">
          {panels.map((column, columnIndex) => (
            <div key={columnIndex} data-chat-settings-column="true">
              {column.map((group, groupIndex) => {
                const titleSwitch = findEntry(group.titleSwitch)
                const groupEntries = [
                  ...group.fields.flatMap((name) => findEntry(name) ?? []),
                  ...(columnIndex === 0 && groupIndex === 0 ? unlistedEntries : []),
                ]
                if (!titleSwitch && groupEntries.length === 0) return null
                return (
                  <section key={group.title} data-chat-settings-group="true">
                    <h3 data-chat-settings-heading="true">
                      {group.title}
                      {titleSwitch && renderField(titleSwitch)}
                    </h3>
                    {groupEntries.map(renderRow)}
                  </section>
                )
              })}
            </div>
          ))}
        </DashboardFolderPanel>
      </TabsContent>
    </Tabs>
  )
}
