import { describe, expect, it } from 'vitest'

import {
  formatLocalCacheBytes,
  formatLocalCacheCleanupDescription,
} from '../local-cache-image-utils'

describe('formatLocalCacheBytes', () => {
  it('非法或非正数输入统一返回 0 B', () => {
    expect(formatLocalCacheBytes(0)).toBe('0 B')
    expect(formatLocalCacheBytes(-1024)).toBe('0 B')
    expect(formatLocalCacheBytes(Number.NaN)).toBe('0 B')
    expect(formatLocalCacheBytes(Number.POSITIVE_INFINITY)).toBe('0 B')
  })

  it('小于 1024 时使用 B 单位且不带小数', () => {
    expect(formatLocalCacheBytes(1)).toBe('1 B')
    expect(formatLocalCacheBytes(512)).toBe('512 B')
    expect(formatLocalCacheBytes(1023)).toBe('1023 B')
  })

  it('KB 级别小于 10 时保留一位小数', () => {
    expect(formatLocalCacheBytes(1024)).toBe('1.0 KB')
    expect(formatLocalCacheBytes(1536)).toBe('1.5 KB')
  })

  it('数值大于等于 10 时省略小数', () => {
    expect(formatLocalCacheBytes(10 * 1024)).toBe('10 KB')
    expect(formatLocalCacheBytes(500 * 1024)).toBe('500 KB')
  })

  it('按 1024 进制换算 MB / GB / TB', () => {
    expect(formatLocalCacheBytes(5 * 1024 ** 2)).toBe('5.0 MB')
    expect(formatLocalCacheBytes(3 * 1024 ** 3)).toBe('3.0 GB')
    expect(formatLocalCacheBytes(2 * 1024 ** 4)).toBe('2.0 TB')
  })

  it('超过 TB 时钳制在最大单位 TB', () => {
    // 1 PB = 1024 TB，单位下标被 Math.min 限制在 TB
    expect(formatLocalCacheBytes(1024 ** 5)).toBe('1024 TB')
  })
})

describe('formatLocalCacheCleanupDescription', () => {
  it('空结果返回没有可清理的内容', () => {
    expect(formatLocalCacheCleanupDescription({})).toBe('没有可清理的内容。')
  })

  it('全部字段为零或 false 时视为无内容', () => {
    expect(
      formatLocalCacheCleanupDescription({
        removed_bytes: 0,
        removed_files: 0,
        removed_records: 0,
        vacuumed: false,
      })
    ).toBe('没有可清理的内容。')
  })

  it('仅删除文件时只输出文件数量', () => {
    expect(formatLocalCacheCleanupDescription({ removed_files: 3 })).toBe('删除 3 个文件。')
  })

  it('仅释放空间时输出格式化后的字节数', () => {
    expect(formatLocalCacheCleanupDescription({ removed_bytes: 2048 })).toBe('释放 2.0 KB。')
  })

  it('仅移除记录时只输出记录条数', () => {
    expect(formatLocalCacheCleanupDescription({ removed_records: 5 })).toBe('移除 5 条记录。')
  })

  it('vacuumed 为 true 且未提供 reclaimed_bytes 时按 0 B 展示', () => {
    expect(formatLocalCacheCleanupDescription({ vacuumed: true })).toBe('VACUUM 释放 0 B。')
  })

  it('多个部分按固定顺序用中文逗号拼接并以句号结尾', () => {
    expect(
      formatLocalCacheCleanupDescription({
        removed_bytes: 1536,
        removed_files: 2,
        removed_records: 4,
        reclaimed_bytes: 1024,
        vacuumed: true,
      })
    ).toBe('删除 2 个文件，释放 1.5 KB，移除 4 条记录，VACUUM 释放 1.0 KB。')
  })
})
