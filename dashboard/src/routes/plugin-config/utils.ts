/**
 * plugin-config 配置树读写工具：在可视化模式下按 section 路径定位/更新嵌套字段。
 * 从 plugin-config.tsx 抽出，供编辑器 hook 与 Section 渲染共用。
 */

export function getNestedRecord(config: Record<string, unknown>, path?: string): Record<string, unknown> | undefined {
  if (!path) {
    return undefined
  }
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

export function setNestedField(
  config: Record<string, unknown>,
  path: string,
  fieldName: string,
  value: unknown,
): Record<string, unknown> {
  const parts = path.split('.').filter(Boolean)
  const nextConfig: Record<string, unknown> = { ...config }
  let currentTarget = nextConfig
  let currentSource: Record<string, unknown> | undefined = config

  for (const part of parts) {
    const sourceValue: unknown = currentSource?.[part]
    const nextValue =
      sourceValue && typeof sourceValue === 'object' && !Array.isArray(sourceValue)
        ? { ...(sourceValue as Record<string, unknown>) }
        : {}
    currentTarget[part] = nextValue
    currentTarget = nextValue
    currentSource =
      sourceValue && typeof sourceValue === 'object' && !Array.isArray(sourceValue)
        ? (sourceValue as Record<string, unknown>)
        : undefined
  }

  currentTarget[fieldName] = value
  return nextConfig
}
