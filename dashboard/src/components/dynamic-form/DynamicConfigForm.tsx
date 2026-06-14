import { Link } from '@tanstack/react-router'
import { ChevronDown, ChevronUp, Sparkles } from 'lucide-react'
import * as React from 'react'

import { Button } from '@/components/ui/button'
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { Separator } from '@/components/ui/separator'
import { resolveLocalizedText } from '@/lib/config-label'
import { fieldHooks, type FieldHookRegistry } from '@/lib/field-hooks'
import { cn } from '@/lib/utils'
import type { ConfigSchema, FieldSchema } from '@/types/config-schema'

import { DynamicField } from './DynamicField'

export interface DynamicConfigFormProps {
  schema: ConfigSchema
  values: Record<string, unknown>
  onChange: (field: string, value: unknown) => void
  basePath?: string
  hooks?: FieldHookRegistry
  /** 嵌套层级：0 = tab 内容层，1 = section 内容层，2+ = 更深嵌套 */
  level?: number
  advancedVisible?: boolean
  sectionColumns?: 1 | 2
}

function buildFieldPath(basePath: string, fieldName: string) {
  return basePath ? `${basePath}.${fieldName}` : fieldName
}

function resolveSectionTitle(schema: ConfigSchema) {
  return schema.uiLabel || schema.classDoc || schema.className
}

function resolveFieldSectionTitle(field: FieldSchema) {
  return resolveLocalizedText(field.label, undefined, field.name)
}

function orderInlineFields(schema: ConfigSchema, fields: FieldSchema[]) {
  if (schema.className !== 'bot') {
    return fields
  }

  const priorityByName = new Map([
    ['nickname', 0],
    ['platform', 1],
  ])

  return fields
    .map((field, index) => ({ field, index }))
    .sort((left, right) => {
      const leftPriority = priorityByName.get(left.field.name) ?? 100
      const rightPriority = priorityByName.get(right.field.name) ?? 100
      return leftPriority - rightPriority || left.index - right.index
    })
    .map(({ field }) => field)
}

function countSectionItems(schema: ConfigSchema) {
  return schema.fields.length + Object.keys(schema.nested ?? {}).length
}

function shouldShowSectionCollapse(sectionKey: string, schema: ConfigSchema) {
  return sectionKey === 'a_memorix' && countSectionItems(schema) > 1
}

const CHAT_TALK_RULE_FIELD_NAMES = new Set(['enable_talk_value_rules', 'talk_value_rules'])

export function AdvancedSettingsButton({
  active,
  onClick,
}: {
  active: boolean
  onClick: () => void
}) {
  return (
    <Button
      type="button"
      variant={active ? 'default' : 'outline'}
      size="sm"
      onClick={onClick}
    >
      高级设置
    </Button>
  )
}

function PromptGeneratorEntryCard() {
  return (
    <Link
      to="/config/prompt-generator"
      className="group flex items-start gap-3 rounded-lg border bg-muted/20 p-3 text-left transition-colors hover:bg-muted/40"
    >
      <span className="mt-0.5 inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md border bg-background text-primary">
        <Sparkles className="h-4 w-4" aria-hidden="true" />
      </span>
      <span className="min-w-0 space-y-1">
        <span className="block text-sm font-semibold text-foreground group-hover:text-primary">人设生成器</span>
        <span className="block text-xs leading-5 text-muted-foreground">
          根据人格设定生成或调整麦麦的人设描述。
        </span>
      </span>
    </Link>
  )
}

function DynamicConfigSection({
  advancedVisible,
  basePath,
  children,
  collapsedByDefault,
  collapsible = true,
  hooks,
  level,
  nestedSchema,
  onChange,
  sectionKey,
  sectionTitle,
  values,
}: {
  advancedVisible: boolean
  basePath: string
  children?: React.ReactNode
  collapsedByDefault?: boolean
  collapsible?: boolean
  hooks: FieldHookRegistry
  level: number
  nestedSchema: ConfigSchema
  onChange: (field: string, value: unknown) => void
  sectionKey: string
  sectionTitle: string
  values: Record<string, unknown>
}) {
  const [collapsed, setCollapsed] = React.useState(Boolean(collapsible && collapsedByDefault))
  const contentId = React.useId()
  const contentVisible = !collapsible || !collapsed

  return (
    <Card className="min-w-0">
      <CardHeader className={contentVisible ? 'border-b border-border/50 pb-3' : 'pb-3'}>
        <div className="flex items-start justify-between gap-3">
          <div className="space-y-1">
            <div className="flex items-center gap-2">
              <CardTitle className="text-base text-primary">{sectionTitle}</CardTitle>
            </div>
          </div>
          {collapsible && (
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="h-7 w-7 px-0"
              aria-label={collapsed ? '展开' : '收起'}
              aria-controls={contentId}
              aria-expanded={!collapsed}
              title={collapsed ? '展开' : '收起'}
              onClick={() => setCollapsed((current) => !current)}
            >
              {collapsed ? (
                <ChevronDown className="h-4 w-4" aria-hidden="true" />
              ) : (
                <ChevronUp className="h-4 w-4" aria-hidden="true" />
              )}
            </Button>
          )}
        </div>
      </CardHeader>
      {contentVisible && (
        <CardContent id={contentId} className="pt-3">
          {children ?? (
            <div className="space-y-3">
              <DynamicConfigForm
                schema={nestedSchema}
                values={values}
                onChange={(field, value) => onChange(`${sectionKey}.${field}`, value)}
                basePath={basePath}
                hooks={hooks}
                level={level}
                advancedVisible={advancedVisible}
                sectionColumns={1}
              />
              {sectionKey === 'personality' && <PromptGeneratorEntryCard />}
            </div>
          )}
        </CardContent>
      )}
    </Card>
  )
}

function NestedDynamicConfigSection({
  advancedVisible,
  basePath,
  collapsedByDefault,
  collapsible = true,
  hooks,
  level,
  nestedSchema,
  onChange,
  sectionTitle,
  values,
}: {
  advancedVisible: boolean
  basePath: string
  collapsedByDefault?: boolean
  collapsible?: boolean
  hooks: FieldHookRegistry
  level: number
  nestedSchema: ConfigSchema
  onChange: (field: string, value: unknown) => void
  sectionTitle: string
  values: Record<string, unknown>
}) {
  const [collapsed, setCollapsed] = React.useState(Boolean(collapsible && collapsedByDefault))
  const contentId = React.useId()
  const contentVisible = !collapsible || !collapsed

  return (
    <Card className="min-w-0 border-border/70 bg-muted/20 shadow-none">
      <CardHeader className={contentVisible ? 'border-b border-border/50 px-3 py-2.5' : 'px-3 py-2.5'}>
        <div className="flex items-start justify-between gap-3">
          <div className="space-y-1">
            <div className="flex items-center gap-2">
              <CardTitle className="text-sm text-primary">{sectionTitle}</CardTitle>
            </div>
          </div>
          {collapsible && (
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="h-7 w-7 px-0"
              aria-label={collapsed ? '展开' : '收起'}
              aria-controls={contentId}
              aria-expanded={!collapsed}
              title={collapsed ? '展开' : '收起'}
              onClick={() => setCollapsed((current) => !current)}
            >
              {collapsed ? (
                <ChevronDown className="h-4 w-4" aria-hidden="true" />
              ) : (
                <ChevronUp className="h-4 w-4" aria-hidden="true" />
              )}
            </Button>
          )}
        </div>
      </CardHeader>
      {contentVisible && (
        <CardContent id={contentId} className="px-3 pb-3 pt-3">
          <DynamicConfigForm
            schema={nestedSchema}
            values={values}
            onChange={onChange}
            basePath={basePath}
            hooks={hooks}
            level={level}
            advancedVisible={advancedVisible}
            sectionColumns={1}
          />
        </CardContent>
      )}
    </Card>
  )
}

/**
 * DynamicConfigForm - 动态配置表单组件
 *
 * 根据 ConfigSchema 渲染表单字段，支持：
 * 1. Hook 系统：通过 FieldHookRegistry 自定义字段渲染
 *    - replace 模式：完全替换默认渲染
 *    - wrapper 模式：包装默认渲染（通过 children 传递）
 * 2. 嵌套 schema：递归渲染 schema.nested 中的子配置
 * 3. 高级设置：由栏目标题右侧按钮控制显示
 */
export const DynamicConfigForm: React.FC<DynamicConfigFormProps> = ({
  schema,
  values,
  onChange,
  basePath = '',
  hooks = fieldHooks,
  level = 0,
  advancedVisible,
  sectionColumns = 1,
}) => {
  const resolvedAdvancedVisible = advancedVisible ?? false

  const fieldMap = React.useMemo(
    () => new Map(schema.fields.map((field) => [field.name, field])),
    [schema.fields],
  )

  const renderField = (field: FieldSchema) => {
    const fieldPath = buildFieldPath(basePath, field.name)
    const nestedSchema = schema.nested?.[field.name]

    if (hooks.has(fieldPath)) {
      const hookEntry = hooks.get(fieldPath)
      if (!hookEntry) return null
      if (hookEntry.type === 'hidden') return null

      const HookComponent = hookEntry.component

      if (hookEntry.type === 'replace') {
        return (
          <HookComponent
            fieldPath={fieldPath}
            value={values[field.name]}
            onChange={(v) => onChange(field.name, v)}
            onParentChange={onChange}
            schema={field}
            nestedSchema={nestedSchema}
            parentValues={values}
            advancedVisible={resolvedAdvancedVisible}
          />
        )
      }

      return (
        <HookComponent
          fieldPath={fieldPath}
          value={values[field.name]}
          onChange={(v) => onChange(field.name, v)}
          onParentChange={onChange}
          schema={field}
          nestedSchema={nestedSchema}
          parentValues={values}
          advancedVisible={resolvedAdvancedVisible}
        >
          <DynamicField
            schema={field}
            value={values[field.name]}
            onChange={(v) => onChange(field.name, v)}
            fieldPath={fieldPath}
          />
        </HookComponent>
      )
    }

    return (
      <DynamicField
        schema={field}
        value={values[field.name]}
        onChange={(v) => onChange(field.name, v)}
        fieldPath={fieldPath}
      />
    )
  }

  const shouldRenderFieldInline = (field: FieldSchema) => {
    const fieldPath = buildFieldPath(basePath, field.name)
    if (hooks.get(fieldPath)?.type === 'hidden') {
      return false
    }

    if (field['x-display-as-section']) {
      return false
    }

    if (!schema.nested?.[field.name]) {
      return true
    }

    return hooks.get(fieldPath)?.type === 'replace'
  }

  const schemaHasVisibleContent = React.useCallback(
    function hasVisibleContent(targetSchema: ConfigSchema, targetBasePath: string): boolean {
      const targetFields = targetSchema.fields ?? []
      const hasVisibleInlineField = targetFields.some((field) => {
        const fieldPath = buildFieldPath(targetBasePath, field.name)
        const hookEntry = hooks.get(fieldPath)

        if (hookEntry?.type === 'hidden') {
          return false
        }

        if (targetSchema.nested?.[field.name] && hookEntry?.type !== 'replace') {
          return false
        }

        return resolvedAdvancedVisible || !field.advanced
      })

      if (hasVisibleInlineField) {
        return true
      }

      return Object.entries(targetSchema.nested ?? {}).some(([key, nestedSchema]) => {
        const nestedField = targetFields.find((field) => field.name === key)
        const nestedFieldPath = buildFieldPath(targetBasePath, key)
        const hookEntry = hooks.get(nestedFieldPath)

        if (hookEntry?.type === 'hidden') {
          return false
        }

        if (nestedField?.advanced && !resolvedAdvancedVisible) {
          return false
        }

        if (hookEntry?.type === 'replace') {
          return true
        }

        return hasVisibleContent(nestedSchema, nestedFieldPath)
      })
    },
    [hooks, resolvedAdvancedVisible],
  )

  const inlineFields = orderInlineFields(schema, schema.fields.filter(shouldRenderFieldInline))
  const inlineNestedFieldNames = new Set(
    inlineFields
      .filter((field) => Boolean(schema.nested?.[field.name]))
      .map((field) => field.name),
  )
  const normalFields = inlineFields.filter((field) => !field.advanced)
  const visibleFields = resolvedAdvancedVisible
    ? inlineFields
    : normalFields

  const groupFieldsByRow = (fields: FieldSchema[]) => {
    const rows: FieldSchema[][] = []
    let currentRow: FieldSchema[] = []
    let currentRowKey: string | undefined

    for (const field of fields) {
      const rowKey = field['x-row']
      if (rowKey && rowKey === currentRowKey) {
        currentRow.push(field)
        continue
      }

      if (currentRow.length > 0) {
        rows.push(currentRow)
      }

      currentRow = [field]
      currentRowKey = rowKey
    }

    if (currentRow.length > 0) {
      rows.push(currentRow)
    }

    return rows
  }

  const renderRows = (rows: FieldSchema[][]) => (
    <>
      {rows.map((row) => {
        const rowKey = row[0]['x-row']
        const isVisualImageCompressionRow = rowKey === 'visual-image-compression'

        return row.length > 1 ? (
            <div
              key={row.map((field) => field.name).join('|')}
              data-config-row={rowKey}
              className={cn(
                "grid min-w-0 gap-3 py-0.5",
                isVisualImageCompressionRow
                  ? "grid-cols-[minmax(0,0.8fr)_minmax(0,1fr)_minmax(0,1.1fr)] items-center"
                  : "md:grid-cols-[repeat(auto-fit,minmax(min(18rem,100%),1fr))]",
              )}
            >
              {row.map((field) => (
                <div key={field.name} className="min-w-0">{renderField(field)}</div>
              ))}
            </div>
          ) : (
            <div key={row[0].name} className="min-w-0 py-0.5">{renderField(row[0])}</div>
          )
      })}
    </>
  )

  const renderFieldList = (fields: FieldSchema[]) => (
    <>
      {groupFieldsByRow(fields).map((row, index) => (
        <React.Fragment key={row.map((field) => field.name).join('|')}>
          {index > 0 && <Separator className="my-1.5 bg-border/50" />}
          {renderRows([row])}
        </React.Fragment>
      ))}
    </>
  )

  const renderVisibleFields = () => {
    if (basePath !== 'chat') {
      return renderFieldList(visibleFields)
    }

    const talkRuleFields = visibleFields.filter((field) => CHAT_TALK_RULE_FIELD_NAMES.has(field.name))
    if (talkRuleFields.length === 0) {
      return renderFieldList(visibleFields)
    }

    const commonFields = visibleFields.filter((field) => !CHAT_TALK_RULE_FIELD_NAMES.has(field.name))
    if (commonFields.length === 0) {
      return renderFieldList(talkRuleFields)
    }

    return (
      <div className="grid min-w-0 items-start gap-4 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <div className="min-w-0">
          {renderFieldList(commonFields)}
        </div>
        <div className="min-w-0">
          {renderFieldList(talkRuleFields)}
        </div>
      </div>
    )
  }

  return (
    <div className="min-w-0 space-y-4">
      {visibleFields.length > 0 && (
        <div>
          {renderVisibleFields()}
        </div>
      )}

      {schema.nested &&
        (() => {
          const nestedSections = Object.entries(schema.nested)
          .filter(([key]) => !inlineNestedFieldNames.has(key))
          .map(([key, nestedSchema]) => {
          const nestedField = fieldMap.get(key)
          const nestedFieldPath = buildFieldPath(basePath, key)

          if (hooks.has(nestedFieldPath)) {
            const hookEntry = hooks.get(nestedFieldPath)
            if (!hookEntry) return null
            if (hookEntry.type === 'hidden') return null
            if (nestedField?.advanced && !resolvedAdvancedVisible) return null
            if (
              hookEntry.type !== 'replace' &&
              nestedSchema &&
              !schemaHasVisibleContent(nestedSchema, nestedFieldPath)
            ) {
              return null
            }

            const HookComponent = hookEntry.component
            if (hookEntry.type === 'replace') {
              const hookContent = (
                <HookComponent
                  fieldPath={nestedFieldPath}
                  value={values[key]}
                  onChange={(v) => onChange(key, v)}
                  onParentChange={onChange}
                  schema={nestedField ?? nestedSchema}
                  nestedSchema={nestedSchema}
                  parentValues={values}
                  advancedVisible={resolvedAdvancedVisible}
                />
              )

              if (nestedField?.['x-display-as-section']) {
                return (
                  <DynamicConfigSection
                    key={key}
                    advancedVisible={resolvedAdvancedVisible}
                    collapsedByDefault={Boolean(nestedField['x-collapsed-by-default'])}
                    collapsible={false}
                    nestedSchema={{
                      ...nestedSchema,
                      classDoc: resolveFieldSectionTitle(nestedField),
                    }}
                    values={{}}
                    onChange={onChange}
                    basePath={nestedFieldPath}
                    hooks={hooks}
                    level={level + 1}
                    sectionKey={key}
                    sectionTitle={resolveFieldSectionTitle(nestedField)}
                  >
                    {hookContent}
                  </DynamicConfigSection>
                )
              }

              return <div key={key} className="min-w-0">{hookContent}</div>
            }

            return (
              <div key={key} className="min-w-0">
                <HookComponent
                  fieldPath={nestedFieldPath}
                  value={values[key]}
                  onChange={(v) => onChange(key, v)}
                  onParentChange={onChange}
                  schema={nestedField ?? nestedSchema}
                  nestedSchema={nestedSchema}
                  parentValues={values}
                  advancedVisible={resolvedAdvancedVisible}
                >
                  <DynamicConfigForm
                    schema={nestedSchema}
                    values={(values[key] as Record<string, unknown>) || {}}
                    onChange={(field, value) => onChange(`${key}.${field}`, value)}
                    basePath={nestedFieldPath}
                    hooks={hooks}
                    level={level + 1}
                    advancedVisible={resolvedAdvancedVisible}
                    sectionColumns={1}
                  />
                </HookComponent>
              </div>
            )
          }

          const sectionTitle = resolveSectionTitle(nestedSchema)
          if (!schemaHasVisibleContent(nestedSchema, nestedFieldPath)) {
            return null
          }

          if (level === 0) {
            return (
              <DynamicConfigSection
                key={key}
                advancedVisible={resolvedAdvancedVisible}
                collapsedByDefault={Boolean(nestedField?.['x-collapsed-by-default'])}
                collapsible={shouldShowSectionCollapse(key, nestedSchema)}
                nestedSchema={nestedSchema}
                values={(values[key] as Record<string, unknown>) || {}}
                onChange={onChange}
                basePath={nestedFieldPath}
                hooks={hooks}
                level={level + 1}
                sectionKey={key}
                sectionTitle={sectionTitle}
              />
            )
          }

          return (
            <NestedDynamicConfigSection
              key={key}
              advancedVisible={resolvedAdvancedVisible}
              basePath={nestedFieldPath}
              collapsedByDefault={Boolean(nestedField?.['x-collapsed-by-default'])}
              collapsible={false}
              hooks={hooks}
              level={level + 1}
              nestedSchema={nestedSchema}
              onChange={(field, value) => onChange(`${key}.${field}`, value)}
              sectionTitle={sectionTitle}
              values={(values[key] as Record<string, unknown>) || {}}
            />
          )
        })

          const visibleNestedSections = nestedSections.filter(
            (section): section is React.ReactElement => Boolean(section),
          )

          if (level === 0 && sectionColumns === 2 && visibleNestedSections.length > 1) {
            const leftColumnSections = visibleNestedSections.filter((_, index) => index % 2 === 0)
            const rightColumnSections = visibleNestedSections.filter((_, index) => index % 2 === 1)

            return (
              <div className="grid min-w-0 gap-3 md:grid-cols-2">
                <div className="min-w-0 space-y-3">
                  {leftColumnSections.map((section) => (
                    <React.Fragment key={section.key}>
                      {section}
                    </React.Fragment>
                  ))}
                </div>
                <div className="min-w-0 space-y-3">
                  {rightColumnSections.map((section) => (
                    <React.Fragment key={section.key}>
                      {section}
                    </React.Fragment>
                  ))}
                </div>
              </div>
            )
          }

          return visibleNestedSections
        })()}
    </div>
  )
}
