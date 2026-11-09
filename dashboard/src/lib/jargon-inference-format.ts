export function inferenceTime(value?: number | null) {
  return value ? new Date(value * 1000).toLocaleString() : '—'
}
