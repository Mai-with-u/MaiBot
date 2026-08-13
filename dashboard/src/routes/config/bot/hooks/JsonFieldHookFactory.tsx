import { useMemo, useState } from 'react'

import { Textarea } from '@/components/ui/textarea'
import { fieldTitleClassName } from '@/components/dynamic-form/fieldStyle'
import { resolveLocalizedText } from '@/lib/config-label'
import type { FieldHookComponent } from '@/lib/field-hooks'
import type { ConfigSchema, FieldSchema } from '@/types/config-schema'

interface JsonFieldHookOptions {
  emptyValue: unknown
  helperText: string
  placeholder: string
}

function resolveLabel(schema?: ConfigSchema | FieldSchema, fieldPath?: string): string {
  if (!schema) {
    return fieldPath?.split('.').at(-1) || 'JSON 配置'
  }
  if ('label' in schema && schema.label) {
    return resolveLocalizedText(schema.label, undefined, fieldPath?.split('.').at(-1) || 'JSON 配置')
  }
  if ('uiLabel' in schema && schema.uiLabel) {
    return schema.uiLabel
  }
  if ('classDoc' in schema && schema.classDoc) {
    return schema.classDoc
  }
  if ('className' in schema && schema.className) {
    return schema.className
  }
  return fieldPath?.split('.').at(-1) || 'JSON 配置'
}

function resolveDescription(schema?: ConfigSchema | FieldSchema): string {
  if (!schema) {
    return ''
  }
  if ('description' in schema) {
    return schema.description || ''
  }
  if ('classDoc' in schema) {
    return schema.classDoc || ''
  }
  return ''
}

export function createJsonFieldHook(options: JsonFieldHookOptions): FieldHookComponent {
  const JsonFieldHook: FieldHookComponent = ({ fieldPath, onChange, schema, value }) => {
    const normalizedValue = useMemo(() => {
      if (value === undefined) {
        return options.emptyValue
      }
      return value
    }, [value])

    const [editorValue, setEditorValue] = useState(() => JSON.stringify(normalizedValue, null, 2))
    const [errorMessage, setErrorMessage] = useState('')
    const [seenNormalizedValue, setSeenNormalizedValue] = useState(normalizedValue)
    if (seenNormalizedValue !== normalizedValue) {
      setSeenNormalizedValue(normalizedValue)
      setEditorValue(JSON.stringify(normalizedValue, null, 2))
      setErrorMessage('')
    }

    const label = resolveLabel(schema, fieldPath)
    const description = resolveDescription(schema)

    return (
      <div className="space-y-3 rounded-lg border bg-card p-4 sm:p-6">
        <div className="space-y-1">
          <h3 className={fieldTitleClassName(schema, 'text-base font-semibold')}>{label}</h3>
          {description && (
            <p className="text-sm text-muted-foreground whitespace-pre-line">{description}</p>
          )}
          <p className="text-xs text-muted-foreground">{options.helperText}</p>
        </div>

        <Textarea
          className="min-h-[220px] font-mono text-sm"
          placeholder={options.placeholder}
          value={editorValue}
          onChange={(event) => {
            const nextValue = event.target.value
            setEditorValue(nextValue)

            try {
              const parsed = JSON.parse(nextValue)
              setErrorMessage('')
              onChange?.(parsed)
            } catch (error) {
              setErrorMessage(error instanceof Error ? error.message : 'JSON 格式错误')
            }
          }}
        />

        {errorMessage ? (
          <p className="text-sm text-destructive">JSON 解析失败：{errorMessage}</p>
        ) : (
          <p className="text-sm text-muted-foreground">JSON 有效，修改会立即写回配置草稿。</p>
        )}
      </div>
    )
  }

  return JsonFieldHook
}
