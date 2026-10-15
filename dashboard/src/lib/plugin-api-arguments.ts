import type { APIArguments } from '@/lib/plugin-webui'

/** Validate untrusted structured-clone/URL input before it reaches the authenticated client. */
export function validateAPIArguments(
  value: unknown,
  maxBytes = 65536
): asserts value is APIArguments {
  if (
    !value ||
    typeof value !== 'object' ||
    Array.isArray(value) ||
    Object.getPrototypeOf(value) !== Object.prototype
  )
    throw new Error('Arguments must be an object')
  if (Object.keys(value).length > 30) throw new Error('Too many arguments')
  const pending: Array<{ value: unknown; depth: number }> = [{ value, depth: 0 }]
  let count = 0
  while (pending.length) {
    const { value: item, depth } = pending.pop()!
    if (++count > 20000 || depth > 12) throw new Error('JSON exceeds depth or element limits')
    if (item === null || typeof item === 'boolean' || typeof item === 'string') continue
    if (typeof item === 'number' && Number.isFinite(item)) continue
    if (Array.isArray(item)) {
      if (count + pending.length + item.length > 20000) throw new Error('Too many JSON elements')
      pending.push(...item.map((value) => ({ value, depth: depth + 1 })))
      continue
    }
    if (typeof item !== 'object' || Object.getPrototypeOf(item) !== Object.prototype)
      throw new Error('Arguments must contain only JSON values')
    const values = Object.values(item)
    if (count + pending.length + values.length > 20000) throw new Error('Too many JSON elements')
    pending.push(...values.map((value) => ({ value, depth: depth + 1 })))
  }
  if (new TextEncoder().encode(JSON.stringify(value)).byteLength > maxBytes)
    throw new Error(`Arguments exceed ${maxBytes / 1024} KiB`)
}

export function readPluginPageState(): APIArguments {
  const raw = new URLSearchParams(window.location.search).get('state')
  if (raw === null) return {}
  if (raw.length > 4096) throw new Error('Page state exceeds 4 KiB')
  const state: unknown = JSON.parse(raw)
  validateAPIArguments(state, 4096)
  return state
}
