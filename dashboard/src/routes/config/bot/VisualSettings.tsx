import type { CSSProperties } from 'react'

import { DynamicConfigForm } from '@/components/dynamic-form'
import { Card } from '@/components/ui/card'
import { fieldHooks } from '@/lib/field-hooks'
import type { ConfigSchema, FieldSchema } from '@/types/config-schema'
import '@/styles/chat-settings.css'

interface VisualRow {
  name: string
  label?: string
  unit?: string
}

interface VisualGroup {
  title: string
  path: string[]
  titleSwitch?: string
  rows: VisualRow[]
}

// 未列出的字段落在第一组，新增配置不会从页面上消失。
const VISUAL_GROUPS: VisualGroup[] = [
  {
    title: '图片识别',
    path: [],
    rows: [
      { name: 'image_quality' },
      { name: 'wait_image_recognize_max_time', unit: '秒' },
      { name: 'max_image_num', unit: '张' },
    ],
  },
  {
    title: '过大图片',
    path: [],
    titleSwitch: 'handle_oversized_images',
    rows: [
      { name: 'max_image_size_mb', label: '最大图片大小', unit: 'MB' },
      { name: 'oversized_image_handle_method', label: '处理方法' },
    ],
  },
  {
    title: '缓存自动清理',
    path: ['image_cache_cleanup'],
    titleSwitch: 'enabled',
    rows: [
      { name: 'check_interval_hours', label: '清理检查间隔', unit: '小时' },
      { name: 'image_file_retention_days', label: '图片文件保留', unit: '天' },
      { name: 'no_file_result_retention_days', label: '图片理解保留', unit: '天' },
    ],
  },
]

const OVERSIZED_METHOD_LABELS = { compress: '压缩', discard: '丢弃' }

// 只替换中文名称，其他语言继续用配置里的翻译。
const withChineseLabel = (label: FieldSchema['label'], text: string | undefined): FieldSchema['label'] => {
  if (!text) return label
  return label && typeof label === 'object' ? { ...label, zh_CN: text } : text
}

export function VisualSettings({ schema, values, onChange, advancedVisible }: {
  schema: ConfigSchema
  values: Record<string, unknown>
  onChange: (path: string[], value: unknown) => void
  advancedVisible: boolean
}) {
  const ownerAt = (path: string[]) => path.reduce<ConfigSchema | undefined>((owner, key) => owner?.nested?.[key], schema)
  const valuesAt = (path: string[]) =>
    path.reduce<Record<string, unknown>>((current, key) => (current[key] ?? {}) as Record<string, unknown>, values)
  const isVisible = (field: FieldSchema) => advancedVisible || !field.advanced

  const renderField = (group: VisualGroup, field: FieldSchema) => (
    <DynamicConfigForm
      schema={{ ...ownerAt(group.path)!, fields: [field], nested: {} }}
      values={valuesAt(group.path)}
      onChange={(name, value) => onChange([...group.path, ...name.split('.')], value)}
      basePath={['visual', ...group.path].join('.')}
      hooks={fieldHooks}
      advancedVisible={advancedVisible}
      sectionColumns={1}
    />
  )

  const renderRow = (group: VisualGroup, field: FieldSchema, row?: VisualRow) => (
    <div
      key={field.name}
      data-chat-settings-row="true"
      data-visual-unit={row?.unit ? 'true' : undefined}
      style={row?.unit ? { '--visual-unit': `"${row.unit}"` } as CSSProperties : undefined}
    >
      {renderField(group, {
        ...field,
        advanced: false,
        label: withChineseLabel(field.label, row?.label),
        'x-row': undefined,
        ...(field['x-widget'] === 'select'
          ? { 'x-option-labels': OVERSIZED_METHOD_LABELS }
          : { 'x-layout': 'inline-right' as const, 'x-input-width': '5.5rem' }),
      })}
    </div>
  )

  const listedRoot = new Set(
    VISUAL_GROUPS.filter((group) => group.path.length === 0)
      .flatMap((group) => [...group.rows.map((row) => row.name), group.titleSwitch])
  )
  const unlistedRoot = schema.fields.filter((field) => (
    !listedRoot.has(field.name) && !schema.nested?.[field.name] && isVisible(field)
  ))

  return (
    <Card data-chat-settings-panel="true" data-visual-settings="true">
      {VISUAL_GROUPS.map((group, groupIndex) => {
        const owner = ownerAt(group.path)
        if (!owner) return null
        const findField = (name: string | undefined) => owner.fields.find((field) => field.name === name)
        const titleSwitch = findField(group.titleSwitch)
        const listed = new Set([...group.rows.map((row) => row.name), group.titleSwitch])
        const switchedOff = titleSwitch && !valuesAt(group.path)[titleSwitch.name]
        return (
          <section key={group.title} data-chat-settings-column="true">
            <h3 data-chat-settings-heading="true">
              {group.title}
              {titleSwitch && renderField(group, { ...titleSwitch, advanced: false, 'x-row': undefined })}
            </h3>
            <div data-visual-rows-off={switchedOff ? 'true' : undefined}>
              {group.rows.flatMap((row) => {
                const field = findField(row.name)
                return field && isVisible(field) ? [renderRow(group, field, row)] : []
              })}
              {group.path.length > 0 && owner.fields
                .filter((field) => !listed.has(field.name) && isVisible(field))
                .map((field) => renderRow(group, field))}
              {groupIndex === 0 && unlistedRoot.map((field) => renderRow(group, field))}
            </div>
          </section>
        )
      })}
    </Card>
  )
}
