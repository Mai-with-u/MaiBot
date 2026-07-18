/**
 * 系统控制 API
 *
 * 请求样板（认证、解析、错误格式化）由 @/lib/http 的请求客户端承担；
 * 本文件只声明 endpoint、业务错误文案与响应类型。
 * 公开函数保持 throw 契约：失败时抛出 ApiError，由调用方自行 catch
 * （例如重启期间后端短暂不可达导致的预期失败）。
 */
import { backendApi } from '@/lib/http'

/**
 * 重启麦麦主程序
 */
export async function restartMaiBot(): Promise<{ success: boolean; message: string }> {
  return backendApi.post<{ success: boolean; message: string }>('/api/webui/system/restart', {
    errorMessage: '重启失败',
  })
}

/**
 * 检查麦麦运行状态
 */
export async function getMaiBotStatus(): Promise<{
  running: boolean
  uptime: number
  version: string
  start_time: string
}> {
  return backendApi.get<{
    running: boolean
    uptime: number
    version: string
    start_time: string
  }>('/api/webui/system/status', {
    errorMessage: '获取状态失败',
  })
}

export interface UpdateNoticeResponse {
  pending: boolean
  current_version: string
  from_version: string | null
  versions: string[]
  content: string
  incompatible_plugins: IncompatiblePluginNotice[]
}

export type PluginUpdateCheckStatus =
  | 'checking'
  | 'available'
  | 'unavailable'
  | 'not_found'
  | 'check_failed'

export interface IncompatiblePluginNotice {
  plugin_id: string
  name: string
  installed_version: string
  host_min_version: string
  host_max_version: string
  update_status: PluginUpdateCheckStatus
  update_version: string | null
}

export interface UpdateNoticeAckResponse {
  success: boolean
  message: string
  version: string
}

export async function getUpdateNotice(): Promise<UpdateNoticeResponse> {
  return backendApi.get<UpdateNoticeResponse>('/api/webui/system/update-notice', {
    errorMessage: '获取更新公告失败',
  })
}

export async function ackUpdateNotice(): Promise<UpdateNoticeAckResponse> {
  return backendApi.post<UpdateNoticeAckResponse>('/api/webui/system/update-notice/ack', {
    errorMessage: '确认更新公告失败',
  })
}

export interface CacheDirectoryStats {
  key: string
  label: string
  path: string
  exists: boolean
  file_count: number
  total_size: number
  db_records: number
}

export interface DatabaseFileStats {
  path: string
  exists: boolean
  size: number
}

export interface DatabaseTableStats {
  name: string
  rows: number
  size: number
  size_source: 'dbstat' | 'estimated'
  label: string
  category: string
  description: string
  cleanup_supported: boolean
  cleanup_date_column: string | null
}

export interface DatabaseStorageStats {
  files: DatabaseFileStats[]
  tables: DatabaseTableStats[]
  total_size: number
  page_size: number
  page_count: number
  freelist_count: number
  free_size: number
}

export interface LocalCacheStats {
  directories: CacheDirectoryStats[]
  database: DatabaseStorageStats
}

export type LocalCacheImageTarget = 'images' | 'emoji'

export interface LocalCacheImageItem {
  relative_path: string
  file_name: string
  full_path: string
  size: number
  modified_time: number
  format: string
  db_id: number | null
  image_hash: string | null
  description: string
  is_registered: boolean | null
  is_banned: boolean | null
  no_file_flag: boolean | null
}

export interface LocalCacheImageListResponse {
  success: boolean
  target: LocalCacheImageTarget
  total: number
  page: number
  page_size: number
  total_size: number
  data: LocalCacheImageItem[]
  date_groups: LocalCacheImageDateGroup[]
}

export interface LocalCacheImageDateGroup {
  date: string
  file_count: number
  total_size: number
}

export interface LocalCacheLogDirectoryItem {
  relative_path: string
  name: string
  full_path: string
  depth: number
  file_count: number
  total_size: number
  modified_time: number
  root_files_only: boolean
}

export interface LocalCacheLogDirectoryListResponse {
  success: boolean
  total: number
  data: LocalCacheLogDirectoryItem[]
}

export interface LocalCacheDataEntry {
  relative_path: string
  name: string
  full_path: string
  kind: 'file' | 'directory'
  file_count: number
  total_size: number
  modified_time: number
  protected: boolean
  protection_reason: string | null
}

export interface LocalCacheDataEntriesResponse {
  success: boolean
  root_path: string
  relative_path: string
  current_path: string
  parent_path: string | null
  file_count: number
  total_size: number
  total: number
  data: LocalCacheDataEntry[]
}

export interface LocalCacheCleanupResult {
  success: boolean
  message: string
  target: 'images' | 'emoji' | 'log_files' | 'database_logs' | 'data'
  removed_files: number
  removed_bytes: number
  removed_records: number
  vacuumed: boolean
  database_size_before: number | null
  database_size_after: number | null
  reclaimed_bytes: number
}

export type LocalCacheCleanupTarget = 'images' | 'emoji' | 'log_files' | 'database_logs'
export type DatabaseCleanupMode = 'all' | 'older_than_days'
export type DatabaseCleanupTable = string
export type LogCleanupTable = DatabaseCleanupTable

export interface LocalCacheDatabaseVacuumResult {
  success: boolean
  message: string
  database_size_before: number
  database_size_after: number
  reclaimed_bytes: number
  checkpoint_busy: number
  checkpoint_log: number
  checkpointed: number
}

export function getLocalCacheImagePreviewUrl(target: LocalCacheImageTarget, relativePath: string): string {
  const query = new URLSearchParams({
    target,
    relative_path: relativePath,
  })
  return `/api/webui/system/local-cache/images/preview?${query.toString()}`
}

export async function getLocalCacheStats(): Promise<LocalCacheStats> {
  return backendApi.get<LocalCacheStats>('/api/webui/system/local-cache', {
    errorMessage: '获取本地缓存统计失败',
  })
}

export async function getLocalCacheDatabaseStats(): Promise<DatabaseStorageStats> {
  return backendApi.get<DatabaseStorageStats>('/api/webui/system/local-cache/database', {
    errorMessage: '获取数据库统计失败',
  })
}

export async function vacuumLocalCacheDatabase(): Promise<LocalCacheDatabaseVacuumResult> {
  return backendApi.post<LocalCacheDatabaseVacuumResult>(
    '/api/webui/system/local-cache/database/vacuum',
    {
      errorMessage: '数据库 VACUUM 失败',
    }
  )
}

export async function getLocalCacheDataEntries(relativePath = ''): Promise<LocalCacheDataEntriesResponse> {
  return backendApi.get<LocalCacheDataEntriesResponse>(
    '/api/webui/system/local-cache/data-entries',
    {
      query: { relative_path: relativePath || undefined },
      errorMessage: '获取 data 目录失败',
    }
  )
}

export async function deleteLocalCacheDataEntry(relativePath: string): Promise<LocalCacheCleanupResult> {
  return backendApi.delete<LocalCacheCleanupResult>('/api/webui/system/local-cache/data-entries', {
    body: { relative_path: relativePath },
    errorMessage: '删除 data 条目失败',
  })
}

export async function cleanupLocalCache(
  target: LocalCacheCleanupTarget,
  tables: DatabaseCleanupTable[] = [],
  options: {
    database_mode?: DatabaseCleanupMode
    older_than_days?: number
    vacuum_after_cleanup?: boolean
  } = {}
): Promise<LocalCacheCleanupResult> {
  return backendApi.post<LocalCacheCleanupResult>('/api/webui/system/local-cache/cleanup', {
    body: {
      target,
      tables,
      database_mode: options.database_mode ?? 'all',
      older_than_days: options.older_than_days ?? null,
      vacuum_after_cleanup: options.vacuum_after_cleanup ?? true,
    },
    errorMessage: '清理本地缓存失败',
  })
}

export async function getLocalCacheImages(params: {
  target: LocalCacheImageTarget
  page?: number
  page_size?: number
  start_date?: string
  end_date?: string
}): Promise<LocalCacheImageListResponse> {
  return backendApi.get<LocalCacheImageListResponse>('/api/webui/system/local-cache/images', {
    query: {
      target: params.target,
      page: params.page ?? 1,
      page_size: params.page_size ?? 40,
      start_date: params.start_date || undefined,
      end_date: params.end_date || undefined,
    },
    errorMessage: '获取本地缓存图片列表失败',
  })
}

export async function deleteLocalCacheImage(
  target: LocalCacheImageTarget,
  relativePath: string
): Promise<LocalCacheCleanupResult> {
  return backendApi.delete<LocalCacheCleanupResult>('/api/webui/system/local-cache/images', {
    body: { target, relative_path: relativePath },
    errorMessage: '删除本地缓存图片失败',
  })
}

export async function deleteLocalCacheImagesByDateRange(
  target: LocalCacheImageTarget,
  startDate: string,
  endDate: string
): Promise<LocalCacheCleanupResult> {
  return backendApi.delete<LocalCacheCleanupResult>('/api/webui/system/local-cache/images/bulk', {
    body: {
      target,
      mode: 'date_range',
      start_date: startDate || null,
      end_date: endDate || null,
    },
    errorMessage: '按日期删除缓存失败',
  })
}

export async function deleteLocalCacheImagesOlderThanRecentDays(
  target: LocalCacheImageTarget,
  keepRecentDays: 1 | 7 | 30
): Promise<LocalCacheCleanupResult> {
  return backendApi.delete<LocalCacheCleanupResult>('/api/webui/system/local-cache/images/bulk', {
    body: {
      target,
      mode: 'older_than_recent_days',
      keep_recent_days: keepRecentDays,
    },
    errorMessage: '清理过期缓存失败',
  })
}

export async function getLocalCacheLogDirectories(): Promise<LocalCacheLogDirectoryListResponse> {
  return backendApi.get<LocalCacheLogDirectoryListResponse>(
    '/api/webui/system/local-cache/log-directories',
    {
      errorMessage: '获取日志目录列表失败',
    }
  )
}

export async function deleteLocalCacheLogDirectory(relativePath: string): Promise<LocalCacheCleanupResult> {
  return backendApi.delete<LocalCacheCleanupResult>(
    '/api/webui/system/local-cache/log-directories',
    {
      body: { relative_path: relativePath },
      errorMessage: '清理日志目录失败',
    }
  )
}
