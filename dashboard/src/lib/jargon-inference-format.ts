/** Format a Unix timestamp in seconds in the browser locale, or show a dash when it is absent. */
export function inferenceTime(value?: number | null) {
  return value ? new Date(value * 1000).toLocaleString() : '—'
}
