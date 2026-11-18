export function formatNumber(value: number, locale: string): string {
  const absoluteValue = Math.abs(value)
  if (absoluteValue >= 1_000_000) {
    return `${new Intl.NumberFormat(locale, { maximumFractionDigits: 2 }).format(value / 1_000_000)}M`
  }
  if (absoluteValue >= 1_000) {
    return `${new Intl.NumberFormat(locale, { maximumFractionDigits: 2 }).format(value / 1_000)}K`
  }
  return new Intl.NumberFormat(locale, { maximumFractionDigits: 2 }).format(value)
}

export function formatCacheRate(value: number | null): string {
  return value === null ? 'N/A' : `${(value * 100).toFixed(2)}%`
}
