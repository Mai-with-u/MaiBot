import type { ConfigSchema } from '@/types/config-schema'

export interface TabGroup {
  id: string
  label: string
  order: number
  sections: string[]
}

/**
 * 从 schema 的 nested 字段解析出 tab 分组信息。
 * - 有 uiLabel 且无 uiParent → 独立 tab
 * - 有 uiParent → 递归找到最终 host，并归入对应 tab
 */
export function buildTabGroupsFromSchema(schema: ConfigSchema): TabGroup[] {
  const nested = schema.nested || {}
  const nestedEntries = Object.entries(nested)
  const hosts = new Map<string, TabGroup>()

  const resolveHostId = (fieldName: string, visited: Set<string> = new Set()): string | null => {
    if (visited.has(fieldName)) {
      return null
    }

    const fieldSchema = nested[fieldName]
    if (!fieldSchema) {
      return null
    }

    if (!fieldSchema.uiParent) {
      return fieldSchema.uiLabel ? fieldName : null
    }

    visited.add(fieldName)
    return resolveHostId(fieldSchema.uiParent, visited)
  }

  for (const [fieldName, fieldSchema] of nestedEntries) {
    if (fieldSchema.uiLabel && !fieldSchema.uiParent) {
      hosts.set(fieldName, {
        id: fieldName,
        label: fieldSchema.uiLabel,
        order: fieldSchema.uiOrder ?? Number.POSITIVE_INFINITY,
        sections: [fieldName],
      })
    }
  }

  for (const [fieldName] of nestedEntries) {
    const hostId = resolveHostId(fieldName)
    if (!hostId || hostId === fieldName) {
      continue
    }

    const parent = hosts.get(hostId)
    if (parent && !parent.sections.includes(fieldName)) {
      parent.sections.push(fieldName)
    }
  }

  return Array.from(hosts.values()).sort((a, b) => {
    const orderDelta = a.order - b.order
    if (orderDelta !== 0) {
      return orderDelta
    }
    return a.label.localeCompare(b.label, 'zh-CN')
  })
}

