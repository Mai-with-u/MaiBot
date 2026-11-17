/** 兼容旧链接的数字 QQ 号；不接受已经丢失精度的数字 ID。 */
export function migrationUserId(value: unknown): string | undefined {
  if (typeof value === 'string') return value
  if (typeof value === 'number' && Number.isSafeInteger(value) && value >= 0) return String(value)
  return undefined
}
