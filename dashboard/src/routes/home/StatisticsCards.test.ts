import { describe, expect, it } from 'vitest'

import {
  formatChartTimeAxis,
  formatTokenAxis,
  selectChartTimeSeries,
} from './statistics-chart-utils'
import type { TimeSeriesData } from './types'

function makePoint(timestamp: string): TimeSeriesData {
  return {
    timestamp,
    online_seconds: 0,
    requests: 1,
    cost: 1,
    tokens: 1,
    input_tokens: 1,
    output_tokens: 1,
    cache_hit_tokens: 1,
    cache_miss_tokens: 1,
  }
}

describe('统计图表坐标格式', () => {
  it('24 小时使用小时数据，7 天和 30 天使用每日数据', () => {
    const hourlyData = [makePoint('2026-07-28T01:00:00+08:00')]
    const dailyData = [makePoint('2026-07-28T00:00:00+08:00')]
    const data = { hourly_data: hourlyData, daily_data: dailyData }

    expect(selectChartTimeSeries(data, 24)).toBe(hourlyData)
    expect(selectChartTimeSeries(data, 168)).toBe(dailyData)
    expect(selectChartTimeSeries(data, 720)).toBe(dailyData)
  })

  it('24 小时横轴只显示时间，7 天和 30 天横轴只显示日期', () => {
    const timestamp = '2026-07-28T13:45:00+08:00'

    expect(formatChartTimeAxis(timestamp, 'en-US', 24)).toContain(':')
    expect(formatChartTimeAxis(timestamp, 'en-US', 24)).not.toContain('/')
    expect(formatChartTimeAxis(timestamp, 'en-US', 168)).toContain('/')
    expect(formatChartTimeAxis(timestamp, 'en-US', 720)).toContain('/')
  })

  it('Token 纵轴使用最多一位小数的 K 和 M 缩写', () => {
    expect(formatTokenAxis(999, 'en-US')).toBe('999')
    expect(formatTokenAxis(1_000, 'en-US')).toBe('1K')
    expect(formatTokenAxis(1_550, 'en-US')).toBe('1.6K')
    expect(formatTokenAxis(1_000_000, 'en-US')).toBe('1M')
    expect(formatTokenAxis(1_250_000, 'en-US')).toBe('1.3M')
  })
})
