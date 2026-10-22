export const LOCAL_CACHE_IMAGE_PAGE_SIZE = 40

export type ImageDateFilters = {
  endDate: string
  startDate: string
}

export function formatLocalCacheBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) {
    return '0 B'
  }
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  const unitIndex = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1)
  const value = bytes / 1024 ** unitIndex
  return `${value.toFixed(value >= 10 || unitIndex === 0 ? 0 : 1)} ${units[unitIndex]}`
}

export function formatLocalCacheModifiedTime(timestamp: number): string {
  if (!Number.isFinite(timestamp) || timestamp <= 0) {
    return '-'
  }
  return new Date(timestamp * 1000).toLocaleString('zh-CN')
}

export function formatLocalCacheCleanupDescription(result: {
  removed_bytes?: number
  removed_files?: number
  removed_records?: number
  reclaimed_bytes?: number
  vacuumed?: boolean
}): string {
  const parts: string[] = []
  if (result.removed_files) {
    parts.push(`删除 ${result.removed_files} 个文件`)
  }
  if (result.removed_bytes) {
    parts.push(`释放 ${formatLocalCacheBytes(result.removed_bytes)}`)
  }
  if (result.removed_records) {
    parts.push(`移除 ${result.removed_records} 条记录`)
  }
  if (result.vacuumed) {
    parts.push(`VACUUM 释放 ${formatLocalCacheBytes(result.reclaimed_bytes ?? 0)}`)
  }
  return parts.length > 0 ? `${parts.join('，')}。` : '没有可清理的内容。'
}
