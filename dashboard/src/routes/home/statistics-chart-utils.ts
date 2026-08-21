import type { DashboardData, TimeSeriesData } from './types'

export function formatChartTimeAxis(value: string, locale: string, timeRange: number): string {
  const date = new Date(value)
  if (timeRange === 24) {
    return date.toLocaleTimeString(locale, {
      hour: '2-digit',
      minute: '2-digit',
    })
  }
  return date.toLocaleDateString(locale, {
    month: '2-digit',
    day: '2-digit',
  })
}

export function formatTokenAxis(value: number, locale: string): string {
  const absoluteValue = Math.abs(value)
  if (absoluteValue >= 1_000_000) {
    return `${new Intl.NumberFormat(locale, { maximumFractionDigits: 1 }).format(value / 1_000_000)}M`
  }
  if (absoluteValue >= 1_000) {
    return `${new Intl.NumberFormat(locale, { maximumFractionDigits: 1 }).format(value / 1_000)}K`
  }
  return new Intl.NumberFormat(locale, { maximumFractionDigits: 1 }).format(value)
}

export function selectChartTimeSeries(
  data: Pick<DashboardData, 'hourly_data' | 'daily_data'>,
  timeRange: number
): TimeSeriesData[] {
  return timeRange === 24 ? data.hourly_data : data.daily_data
}
