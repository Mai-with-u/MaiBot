import type { CSSProperties } from 'react'

import { DynamicConfigForm } from '@/components/dynamic-form'
import { Card } from '@/components/ui/card'
import { fieldHooks } from '@/lib/field-hooks'
import type { ConfigSchema, FieldSchema } from '@/types/config-schema'
import '@/styles/chat-settings.css'

type SectionValues = Record<string, unknown>

interface SettingsRow {
  name: string
  label?: string
  unit?: string
}

interface SettingsGroup {
  title: string
  section: string
  path: string[]
  titleSwitch?: string
  rows: SettingsRow[]
}

// 顶部一排能力开关；超长工具结果整理的阈值是它的附属项，排在同一格里。
const CAPABILITY_SWITCHES: Array<{ section: string; name: string; label?: string; children?: SettingsRow[] }> = [
  { section: 'capabilities', name: 'files' },
  {
    section: 'capabilities',
    name: 'tool_result_condense',
    children: [{ name: 'tool_result_condense_threshold', label: '整理阈值', unit: '字' }],
  },
  { section: 'capabilities', name: 'planner_subagent' },
  { section: 'voice', name: 'enable_asr', label: '语音识别' },
]

// 未列出的视觉字段落在第一组，新增配置不会从页面上消失。
const VISUAL_GROUPS: SettingsGroup[] = [
  {
    title: '图片识别',
    section: 'visual',
    path: [],
    rows: [
      { name: 'image_quality' },
      { name: 'wait_image_recognize_max_time', unit: '秒' },
      { name: 'max_image_num', unit: '张' },
    ],
  },
  {
    title: '过大图片',
    section: 'visual',
    path: [],
    titleSwitch: 'handle_oversized_images',
    rows: [
      { name: 'max_image_size_mb', label: '最大图片大小', unit: 'MB' },
      { name: 'oversized_image_handle_method', label: '处理方法' },
    ],
  },

]

const OVERSIZED_METHOD_LABELS = { compress: '压缩', discard: '丢弃' }

// 只替换中文名称，其他语言继续用配置里的翻译。
const withChineseLabel = (label: FieldSchema['label'], text: string | undefined): FieldSchema['label'] => {
  if (!text) return label
  return label && typeof label === 'object' ? { ...label, zh_CN: text } : text
}

/** 能力页：顶部一排能力开关，下面是视觉的三栏分组。 */
export function CapabilitySettings({ schemas, values, onChange, advancedVisible }: {
  schemas: Record<string, ConfigSchema | undefined>
  values: Record<string, SectionValues | null | undefined>
  onChange: (section: string, path: string[], value: unknown) => void
  advancedVisible: boolean
}) {
  const ownerAt = (section: string, path: string[]) =>
    path.reduce<ConfigSchema | undefined>((owner, key) => owner?.nested?.[key], schemas[section])
  const valuesAt = (section: string, path: string[]) =>
    path.reduce<SectionValues>((current, key) => (current[key] ?? {}) as SectionValues, values[section] ?? {})
  const isVisible = (field: FieldSchema) => advancedVisible || !field.advanced

  const renderField = (section: string, path: string[], field: FieldSchema) => (
    <DynamicConfigForm
      schema={{ ...ownerAt(section, path)!, fields: [field], nested: {} }}
      values={valuesAt(section, path)}
      onChange={(name, value) => onChange(section, [...path, ...name.split('.')], value)}
      basePath={[section, ...path].join('.')}
      hooks={fieldHooks}
      advancedVisible={advancedVisible}
      sectionColumns={1}
    />
  )

  const renderRow = (section: string, path: string[], field: FieldSchema, row?: SettingsRow) => (
    <div
      key={field.name}
      data-chat-settings-row="true"
      data-visual-unit={row?.unit ? 'true' : undefined}
      style={row?.unit ? { '--visual-unit': `"${row.unit}"` } as CSSProperties : undefined}
    >
      {renderField(section, path, {
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

  const findField = (section: string, path: string[], name: string | undefined) =>
    ownerAt(section, path)?.fields.find((field) => field.name === name)

  const listedSwitches = new Set(CAPABILITY_SWITCHES.flatMap((item) => [
    `${item.section}.${item.name}`,
    ...(item.children ?? []).map((child) => `${item.section}.${child.name}`),
  ]))
  const unlistedSwitches = (['capabilities', 'voice'] as const).flatMap((section) =>
    (schemas[section]?.fields ?? [])
      .filter((field) => !listedSwitches.has(`${section}.${field.name}`) && isVisible(field))
      .map((field) => ({ section, name: field.name }))
  )
  const switchCells = [...CAPABILITY_SWITCHES, ...unlistedSwitches].flatMap((item) => {
    const field = findField(item.section, [], item.name)
    return field && isVisible(field) ? [{ ...item, field }] : []
  })

  const listedVisualRoot = new Set(
    VISUAL_GROUPS.filter((group) => group.path.length === 0)
      .flatMap((group) => [...group.rows.map((row) => row.name), group.titleSwitch])
  )
  const visualSchema = schemas.visual
  const unlistedVisualRoot = (visualSchema?.fields ?? []).filter((field) => (
    !listedVisualRoot.has(field.name) && !visualSchema?.nested?.[field.name] && isVisible(field)
  ))

  return (
    <Card data-chat-settings-panel="true" data-visual-settings="true">
      {switchCells.length > 0 && (
        <div data-capability-switches="true">
          {switchCells.map((item) => (
            <div key={`${item.section}.${item.name}`} data-capability-cell="true">
              {renderField(item.section, [], {
                ...item.field,
                advanced: false,
                label: withChineseLabel(item.field.label, 'label' in item ? item.label : undefined),
                'x-row': undefined,
              })}
              {'children' in item && item.children?.flatMap((child) => {
                const field = findField(item.section, [], child.name)
                if (!field || !isVisible(field)) return []
                const parentOff = !valuesAt(item.section, [])[item.name]
                return [
                  <div key={child.name} data-capability-sub="true" data-visual-rows-off={parentOff ? 'true' : undefined}>
                    {renderRow(item.section, [], field, child)}
                  </div>,
                ]
              })}
            </div>
          ))}
        </div>
      )}
      {visualSchema && VISUAL_GROUPS.map((group, groupIndex) => {
        const owner = ownerAt(group.section, group.path)
        if (!owner) return null
        const titleSwitch = findField(group.section, group.path, group.titleSwitch)
        const listed = new Set([...group.rows.map((row) => row.name), group.titleSwitch])
        const switchedOff = titleSwitch && !valuesAt(group.section, group.path)[titleSwitch.name]
        return (
          <section key={group.title} data-chat-settings-column="true">
            <h3 data-chat-settings-heading="true">
              {group.title}
              {titleSwitch && renderField(group.section, group.path, { ...titleSwitch, advanced: false, 'x-row': undefined })}
            </h3>
            <div data-visual-rows-off={switchedOff ? 'true' : undefined}>
              {group.rows.flatMap((row) => {
                const field = findField(group.section, group.path, row.name)
                return field && isVisible(field) ? [renderRow(group.section, group.path, field, row)] : []
              })}
              {group.path.length > 0 && owner.fields
                .filter((field) => !listed.has(field.name) && isVisible(field))
                .map((field) => renderRow(group.section, group.path, field))}
              {groupIndex === 0 && unlistedVisualRoot.map((field) => renderRow(group.section, group.path, field))}
            </div>
          </section>
        )
      })}
    </Card>
  )
}
