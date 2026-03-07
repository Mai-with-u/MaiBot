import * as React from 'react'

import type { ConfigSchema, FieldSchema } from '@/types/config-schema'
import { fieldHooks, type FieldHookRegistry } from '@/lib/field-hooks'

import { DynamicField } from './DynamicField'

export interface DynamicConfigFormProps {
  schema: ConfigSchema
  values: Record<string, unknown>
  onChange: (field: string, value: unknown) => void
  hooks?: FieldHookRegistry
}

/**
 * DynamicConfigForm - 动态配置表单组件
 * 
 * 根据 ConfigSchema 渲染表单字段，支持：
 * 1. Hook 系统：通过 FieldHookRegistry 自定义字段渲染
 *    - replace 模式：完全替换默认渲染
 *    - wrapper 模式：包装默认渲染（通过 children 传递）
 * 2. 嵌套 schema：递归渲染 schema.nested 中的子配置
 * 3. 默认渲染：使用 DynamicField 组件
 */
export const DynamicConfigForm: React.FC<DynamicConfigFormProps> = ({
  schema,
  values,
  onChange,
  hooks = fieldHooks, // 默认使用全局单例
}) => {
  /**
   * 渲染单个字段
   * 检查是否有注册的 Hook，根据 Hook 类型选择渲染方式
   */
  const renderField = (field: FieldSchema) => {
    const fieldPath = field.name

    // 检查是否有注册的 Hook
    if (hooks.has(fieldPath)) {
      const hookEntry = hooks.get(fieldPath)
      if (!hookEntry) return null // Type guard（理论上不会发生）

      const HookComponent = hookEntry.component

      if (hookEntry.type === 'replace') {
        // replace 模式：完全替换默认渲染
        return (
          <HookComponent
            fieldPath={fieldPath}
            value={values[field.name]}
            onChange={(v) => onChange(field.name, v)}
          />
        )
      } else {
        // wrapper 模式：包装默认渲染
        return (
          <HookComponent
            fieldPath={fieldPath}
            value={values[field.name]}
            onChange={(v) => onChange(field.name, v)}
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
    }

    // 无 Hook，使用默认渲染
    return (
      <DynamicField
        schema={field}
        value={values[field.name]}
        onChange={(v) => onChange(field.name, v)}
        fieldPath={fieldPath}
      />
    )
  }

  return (
    <div className="space-y-4">
      {/* 渲染顶层字段 */}
      {schema.fields.map((field) => (
        <div key={field.name}>{renderField(field)}</div>
      ))}

      {/* 渲染嵌套 schema */}
      {schema.nested &&
        Object.entries(schema.nested).map(([key, nestedSchema]) => (
          <div key={key} className="mt-6 space-y-4">
            {/* 嵌套 schema 标题 */}
            <div className="border-b pb-2">
              <h3 className="text-lg font-semibold">{nestedSchema.className}</h3>
              {nestedSchema.classDoc && (
                <p className="text-sm text-muted-foreground">{nestedSchema.classDoc}</p>
              )}
            </div>

            {/* 递归渲染嵌套表单 */}
            <DynamicConfigForm
              schema={nestedSchema}
              values={(values[key] as Record<string, unknown>) || {}}
              onChange={(field, value) => onChange(`${key}.${field}`, value)}
              hooks={hooks}
            />
          </div>
        ))}
    </div>
  )
}
