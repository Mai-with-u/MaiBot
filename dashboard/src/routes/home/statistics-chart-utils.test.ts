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
    requests: 0,
    cost: 0,
    tokens: 0,
    input_tokens: 0,
    output_tokens: 0,
    cache_hit_tokens: 0,
    cache_miss_tokens: 0,
  }
}

describe('statistics-chart-utils', () => {
  it('空序列按时间范围原样返回对应数组', () => {
    const hourlyData: TimeSeriesData[] = []
    const dailyData: TimeSeriesData[] = []
    const data = { hourly_data: hourlyData, daily_data: dailyData }

    expect(selectChartTimeSeries(data, 24)).toBe(hourlyData)
    expect(selectChartTimeSeries(data, 0)).toBe(dailyData)
    expect(selectChartTimeSeries(data, 48)).toBe(dailyData)
  })

  it('非法时间戳格式化为 Invalid Date，非 24 小时范围走日期轴', () => {
    expect(formatChartTimeAxis('not-a-date', 'en-US', 24)).toMatch(/Invalid Date/i)
    expect(formatChartTimeAxis('2026-07-28T13:45:00+08:00', 'zh-CN', 1)).not.toContain(':')
  })

  it('Token 轴覆盖 0、负数以及跨过 K/M 阈值的取值', () => {
    expect(formatTokenAxis(0, 'en-US')).toBe('0')
    expect(formatTokenAxis(-999, 'en-US')).toBe('-999')
    expect(formatTokenAxis(-1_550, 'en-US')).toBe('-1.6K')
    expect(formatTokenAxis(-1_250_000, 'en-US')).toBe('-1.3M')
    expect(formatTokenAxis(999.4, 'en-US')).toBe('999.4')
  })

  it('有数据时 24 小时取小时序列，其余取每日序列', () => {
    const hourlyData = [makePoint('2026-07-28T01:00:00+08:00')]
    const dailyData = [makePoint('2026-07-28T00:00:00+08:00')]

    expect(selectChartTimeSeries({ hourly_data: hourlyData, daily_data: dailyData }, 24)).toBe(
      hourlyData
    )
    expect(selectChartTimeSeries({ hourly_data: hourlyData, daily_data: dailyData }, 168)).toBe(
      dailyData
    )
  })
})
