import type { ConfigSchema, FieldSchema } from '@/types/config-schema'

export interface ChatField {
  field: FieldSchema
  schema: ConfigSchema
  path: string[]
}

/** 从原始 schema 收集展示字段，路径同时用于保存、搜索和弹窗定位。 */
export function collectChatSettings(schema: ConfigSchema, path: string[] = []): ChatField[] {
  return schema.fields.flatMap((field): ChatField[] => {
    if (field['x-chat-settings']) return [{ field, schema, path }]
    const nested = schema.nested?.[field.name]
    return field.type === 'object' && nested ? collectChatSettings(nested, [...path, field.name]) : []
  })
}
