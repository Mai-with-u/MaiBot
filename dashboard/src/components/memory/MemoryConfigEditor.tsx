import { useMemo, useState } from 'react'

import { ListFieldEditor } from '@/components/ListFieldEditor'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { DraftNumberInput } from '@/components/ui/draft-number-input'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Textarea } from '@/components/ui/textarea'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import type { ConfigFieldSchema, PluginConfigSchema } from '@/lib/plugin-api'

interface MemoryConfigEditorProps {
  schema: PluginConfigSchema
  config: Record<string, unknown>
  onChange: (nextConfig: Record<string, unknown>) => void
  disabled?: boolean
}

function getNestedRecord(config: Record<string, unknown>, path: string): Record<string, unknown> | undefined {
  const parts = path.split('.').filter(Boolean)
  let current: unknown = config

  for (const part of parts) {
    if (!current || typeof current !== 'object' || Array.isArray(current)) {
      return undefined
    }
    current = (current as Record<string, unknown>)[part]
  }

  if (!current || typeof current !== 'object' || Array.isArray(current)) {
    return undefined
  }

  return current as Record<string, unknown>
}

function setNestedField(
  config: Record<string, unknown>,
  path: string,
  fieldName: string,
  value: unknown,
): Record<string, unknown> {
  const parts = path.split('.').filter(Boolean)
  const nextConfig: Record<string, unknown> = { ...config }
  let target = nextConfig
  let source: Record<string, unknown> | undefined = config

  for (const part of parts) {
    const sourceValue: unknown = source?.[part]
    const nextValue =
      sourceValue && typeof sourceValue === 'object' && !Array.isArray(sourceValue)
        ? { ...(sourceValue as Record<string, unknown>) }
        : {}
    target[part] = nextValue
    target = nextValue
    source =
      sourceValue && typeof sourceValue === 'object' && !Array.isArray(sourceValue)
        ? (sourceValue as Record<string, unknown>)
        : undefined
  }

  target[fieldName] = value
  return nextConfig
}

function FieldRenderer({
  field,
  value,
  onChange,
  disabled,
}: {
  field: ConfigFieldSchema
  value: unknown
  onChange: (value: unknown) => void
  disabled?: boolean
}) {
  const [jsonDraft, setJsonDraft] = useState(
    typeof value === 'string' ? String(value) : JSON.stringify(value ?? field.default ?? {}, null, 2),
  )

  switch (field.ui_type) {
    case 'switch':
      return (
        <div className="flex items-center justify-between rounded-lg border bg-background px-4 py-3">
          <div className="space-y-1">
            <Label>{field.label}</Label>
            {field.hint && <p className="text-xs text-muted-foreground">{field.hint}</p>}
          </div>
          <Switch
            checked={Boolean(value ?? field.default)}
            onCheckedChange={onChange}
            disabled={disabled || field.disabled}
          />
        </div>
      )

    case 'number':
      return (
        <div className="space-y-2">
          <Label>{field.label}</Label>
          <DraftNumberInput
            value={value}
            defaultValue={field.default}
            onValueChange={onChange}
            min={field.min}
            max={field.max}
            step={field.step ?? 1}
            disabled={disabled || field.disabled}
            placeholder={field.placeholder}
          />
          {field.hint && <p className="text-xs text-muted-foreground">{field.hint}</p>}
        </div>
      )

    case 'select':
      return (
        <div className="space-y-2">
          <Label>{field.label}</Label>
          <Select
            value={String(value ?? field.default ?? '')}
            onValueChange={onChange}
            disabled={disabled || field.disabled}
          >
            <SelectTrigger>
              <SelectValue placeholder={field.placeholder ?? '请选择'} />
            </SelectTrigger>
            <SelectContent>
              {(field.choices ?? []).map((choice) => (
                <SelectItem key={String(choice)} value={String(choice)}>
                  {String(choice)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {field.hint && <p className="text-xs text-muted-foreground">{field.hint}</p>}
        </div>
      )

    case 'textarea':
      return (
        <div className="space-y-2">
          <Label>{field.label}</Label>
          <Textarea
            value={String(value ?? field.default ?? '')}
            onChange={(event) => onChange(event.target.value)}
            rows={field.rows ?? 4}
            placeholder={field.placeholder}
            disabled={disabled || field.disabled}
          />
          {field.hint && <p className="text-xs text-muted-foreground">{field.hint}</p>}
        </div>
      )

    case 'list':
      return (
        <div className="space-y-2">
          <Label>{field.label}</Label>
          <ListFieldEditor
            value={Array.isArray(value) ? value : (Array.isArray(field.default) ? field.default : [])}
            onChange={onChange as (value: unknown[]) => void}
            itemType={field.item_type}
            itemFields={field.item_fields}
            minItems={field.min_items}
            maxItems={field.max_items}
            placeholder={field.placeholder}
            disabled={disabled || field.disabled}
          />
          {field.hint && <p className="text-xs text-muted-foreground">{field.hint}</p>}
        </div>
      )

    case 'json':
      return (
        <div className="space-y-2">
          <Label>{field.label}</Label>
          <Textarea
            value={jsonDraft}
            rows={field.rows ?? 6}
            disabled={disabled || field.disabled}
            onChange={(event) => {
              const nextValue = event.target.value
              setJsonDraft(nextValue)
              try {
                onChange(JSON.parse(nextValue))
              } catch {
                // keep draft until valid JSON
              }
            }}
          />
          {field.hint && <p className="text-xs text-muted-foreground">{field.hint}</p>}
        </div>
      )

    default:
      return (
        <div className="space-y-2">
          <Label>{field.label}</Label>
          <Input
            value={String(value ?? field.default ?? '')}
            onChange={(event) => onChange(event.target.value)}
            disabled={disabled || field.disabled}
            placeholder={field.placeholder}
          />
          {field.hint && <p className="text-xs text-muted-foreground">{field.hint}</p>}
        </div>
      )
  }
}

function SectionCard({
  sectionName,
  schema,
  config,
  onChange,
  disabled,
}: {
  sectionName: string
  schema: PluginConfigSchema
  config: Record<string, unknown>
  onChange: (nextConfig: Record<string, unknown>) => void
  disabled?: boolean
}) {
  const section = schema.sections[sectionName]
  if (!section) {
    return null
  }

  const sectionValues = getNestedRecord(config, sectionName) ?? {}
  const orderedFields = Object.values(section.fields)
    .filter((field) => !field.hidden)
    .sort((left, right) => left.order - right.order)

  return (
    <Card>
      <CardHeader>
        <CardTitle>{section.title}</CardTitle>
        {section.description && <CardDescription>{section.description}</CardDescription>}
      </CardHeader>
      <CardContent className="space-y-4">
        {orderedFields.map((field) => (
          <FieldRenderer
            key={`${sectionName}.${field.name}`}
            field={field}
            value={sectionValues[field.name]}
            disabled={disabled}
            onChange={(value) => onChange(setNestedField(config, sectionName, field.name, value))}
          />
        ))}
      </CardContent>
    </Card>
  )
}

export function MemoryConfigEditor({ schema, config, onChange, disabled }: MemoryConfigEditorProps) {
  const tabs = useMemo(
    () => [...(schema.layout.tabs ?? [])].sort((left, right) => left.order - right.order),
    [schema.layout.tabs],
  )

  if (tabs.length === 0) {
    const orderedSections = Object.keys(schema.sections).sort(
      (left, right) => (schema.sections[left]?.order ?? 0) - (schema.sections[right]?.order ?? 0),
    )
    return (
      <div className="space-y-4">
        {orderedSections.map((sectionName) => (
          <SectionCard
            key={sectionName}
            sectionName={sectionName}
            schema={schema}
            config={config}
            onChange={onChange}
            disabled={disabled}
          />
        ))}
      </div>
    )
  }

  return (
    <Tabs defaultValue={tabs[0]?.id} className="space-y-4">
      <TabsList className="h-auto flex-wrap justify-start">
        {tabs.map((tab) => (
          <TabsTrigger key={tab.id} value={tab.id}>
            {tab.title}
          </TabsTrigger>
        ))}
      </TabsList>

      {tabs.map((tab) => (
        <TabsContent key={tab.id} value={tab.id} className="space-y-4">
          {tab.sections.map((sectionName) => (
            <SectionCard
              key={sectionName}
              sectionName={sectionName}
              schema={schema}
              config={config}
              onChange={onChange}
              disabled={disabled}
            />
          ))}
        </TabsContent>
      ))}
    </Tabs>
  )
}
