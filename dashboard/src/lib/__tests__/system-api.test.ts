import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError, backendApi } from '@/lib/http'

import {
  ackUpdateNotice,
  cleanupLocalCache,
  deleteLocalCacheDataEntry,
  deleteLocalCacheImage,
  deleteLocalCacheImagesByDateRange,
  deleteLocalCacheImagesOlderThanRecentDays,
  deleteLocalCacheLogDirectory,
  getLocalCacheDataEntries,
  getLocalCacheDatabaseStats,
  getLocalCacheImagePreviewUrl,
  getLocalCacheImages,
  getLocalCacheLogDirectories,
  getLocalCacheStats,
  getMaiBotStatus,
  getUpdateNotice,
  restartMaiBot,
  vacuumLocalCacheDatabase,
} from '../system-api'

vi.mock('@/lib/http', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/http')>()
  return {
    ...actual,
    backendApi: {
      request: vi.fn(),
      get: vi.fn(),
      post: vi.fn(),
      put: vi.fn(),
      patch: vi.fn(),
      delete: vi.fn(),
    },
  }
})

const getMock = vi.mocked(backendApi.get)
const postMock = vi.mocked(backendApi.post)
const deleteMock = vi.mocked(backendApi.delete)

beforeEach(() => {
  getMock.mockReset()
  postMock.mockReset()
  deleteMock.mockReset()
})

describe('restartMaiBot', () => {
  it('POST 到重启接口并透传响应', async () => {
    const response = { success: true, message: '重启中' }
    postMock.mockResolvedValue(response)

    await expect(restartMaiBot()).resolves.toBe(response)
    expect(postMock).toHaveBeenCalledWith('/api/webui/system/restart', {
      errorMessage: '重启失败',
    })
  })

  it('后端失败时向上抛出 ApiError（重启期间不可达属预期失败）', async () => {
    postMock.mockRejectedValue(new ApiError('重启失败', { status: 502 }))

    await expect(restartMaiBot()).rejects.toBeInstanceOf(ApiError)
    await expect(restartMaiBot()).rejects.toMatchObject({ status: 502 })
  })
})

describe('getMaiBotStatus', () => {
  it('GET 运行状态接口并透传响应', async () => {
    const response = { running: true, uptime: 120, version: '0.11.0', start_time: '2026-07-26T00:00:00' }
    getMock.mockResolvedValue(response)

    await expect(getMaiBotStatus()).resolves.toBe(response)
    expect(getMock).toHaveBeenCalledWith('/api/webui/system/status', {
      errorMessage: '获取状态失败',
    })
  })
})

describe('getUpdateNotice', () => {
  it('默认不带 force 参数（undefined 由请求层跳过）', async () => {
    const response = {
      pending: false,
      current_version: '0.11.0',
      from_version: null,
      versions: [],
      content: '',
      incompatible_plugins: [],
    }
    getMock.mockResolvedValue(response)

    await expect(getUpdateNotice()).resolves.toBe(response)
    expect(getMock).toHaveBeenCalledWith('/api/webui/system/update-notice', {
      errorMessage: '获取更新公告失败',
      query: { force: undefined },
    })
  })

  it('force 为 true 时携带 force 查询参数', async () => {
    getMock.mockResolvedValue({})

    await getUpdateNotice(true)

    expect(getMock).toHaveBeenCalledWith('/api/webui/system/update-notice', {
      errorMessage: '获取更新公告失败',
      query: { force: true },
    })
  })
})

describe('ackUpdateNotice', () => {
  it('POST 到确认接口并透传响应', async () => {
    const response = { success: true, message: '已确认', version: '0.11.0' }
    postMock.mockResolvedValue(response)

    await expect(ackUpdateNotice()).resolves.toBe(response)
    expect(postMock).toHaveBeenCalledWith('/api/webui/system/update-notice/ack', {
      errorMessage: '确认更新公告失败',
    })
  })
})

describe('getLocalCacheImagePreviewUrl', () => {
  it('拼出带 target 与 relative_path 的预览地址', () => {
    expect(getLocalCacheImagePreviewUrl('images', 'a.png')).toBe(
      '/api/webui/system/local-cache/images/preview?target=images&relative_path=a.png'
    )
  })

  it('相对路径中的斜杠与空格被 URL 编码', () => {
    expect(getLocalCacheImagePreviewUrl('emoji', '2024/01/a b.png')).toBe(
      '/api/webui/system/local-cache/images/preview?target=emoji&relative_path=2024%2F01%2Fa+b.png'
    )
  })
})

describe('本地缓存统计', () => {
  it('getLocalCacheStats 请求本地缓存统计接口', async () => {
    const response = { directories: [], database: { files: [], tables: [] } }
    getMock.mockResolvedValue(response)

    await expect(getLocalCacheStats()).resolves.toBe(response)
    expect(getMock).toHaveBeenCalledWith('/api/webui/system/local-cache', {
      errorMessage: '获取本地缓存统计失败',
    })
  })

  it('getLocalCacheStats 失败时向上抛出 ApiError', async () => {
    getMock.mockRejectedValue(new ApiError('获取本地缓存统计失败', { status: 500 }))

    await expect(getLocalCacheStats()).rejects.toMatchObject({ status: 500 })
  })

  it('getLocalCacheDatabaseStats 请求数据库统计接口', async () => {
    const response = { files: [], tables: [], total_size: 0 }
    getMock.mockResolvedValue(response)

    await expect(getLocalCacheDatabaseStats()).resolves.toBe(response)
    expect(getMock).toHaveBeenCalledWith('/api/webui/system/local-cache/database', {
      errorMessage: '获取数据库统计失败',
    })
  })

  it('vacuumLocalCacheDatabase POST 到 vacuum 接口', async () => {
    const response = { success: true, message: 'ok', reclaimed_bytes: 1024 }
    postMock.mockResolvedValue(response)

    await expect(vacuumLocalCacheDatabase()).resolves.toBe(response)
    expect(postMock).toHaveBeenCalledWith('/api/webui/system/local-cache/database/vacuum', {
      errorMessage: '数据库 VACUUM 失败',
    })
  })
})

describe('data 目录浏览与删除', () => {
  it('getLocalCacheDataEntries 默认根目录时不携带 relative_path', async () => {
    getMock.mockResolvedValue({ success: true, data: [] })

    await getLocalCacheDataEntries()

    expect(getMock).toHaveBeenCalledWith('/api/webui/system/local-cache/data-entries', {
      query: { relative_path: undefined },
      errorMessage: '获取 data 目录失败',
    })
  })

  it('getLocalCacheDataEntries 携带子目录相对路径', async () => {
    getMock.mockResolvedValue({ success: true, data: [] })

    await getLocalCacheDataEntries('emoji/registed')

    expect(getMock).toHaveBeenCalledWith('/api/webui/system/local-cache/data-entries', {
      query: { relative_path: 'emoji/registed' },
      errorMessage: '获取 data 目录失败',
    })
  })

  it('deleteLocalCacheDataEntry 以 DELETE 提交相对路径', async () => {
    const response = { success: true, message: '已删除', removed_files: 2 }
    deleteMock.mockResolvedValue(response)

    await expect(deleteLocalCacheDataEntry('temp/cache.bin')).resolves.toBe(response)
    expect(deleteMock).toHaveBeenCalledWith('/api/webui/system/local-cache/data-entries', {
      body: { relative_path: 'temp/cache.bin' },
      errorMessage: '删除 data 条目失败',
    })
  })
})

describe('cleanupLocalCache', () => {
  it('默认参数：空表清单、全量模式、清理后 VACUUM', async () => {
    const response = { success: true, message: '清理完成' }
    postMock.mockResolvedValue(response)

    await expect(cleanupLocalCache('images')).resolves.toBe(response)
    expect(postMock).toHaveBeenCalledWith('/api/webui/system/local-cache/cleanup', {
      body: {
        target: 'images',
        tables: [],
        database_mode: 'all',
        older_than_days: null,
        vacuum_after_cleanup: true,
      },
      errorMessage: '清理本地缓存失败',
    })
  })

  it('显式选项覆盖默认值：按天数清理指定表且跳过 VACUUM', async () => {
    postMock.mockResolvedValue({ success: true })

    await cleanupLocalCache('database_logs', ['messages', 'images'], {
      database_mode: 'older_than_days',
      older_than_days: 30,
      vacuum_after_cleanup: false,
    })

    expect(postMock).toHaveBeenCalledWith('/api/webui/system/local-cache/cleanup', {
      body: {
        target: 'database_logs',
        tables: ['messages', 'images'],
        database_mode: 'older_than_days',
        older_than_days: 30,
        vacuum_after_cleanup: false,
      },
      errorMessage: '清理本地缓存失败',
    })
  })

  it('清理失败时向上抛出 ApiError', async () => {
    postMock.mockRejectedValue(new ApiError('清理本地缓存失败', { status: 500 }))

    await expect(cleanupLocalCache('log_files')).rejects.toBeInstanceOf(ApiError)
  })
})

describe('本地缓存图片', () => {
  it('getLocalCacheImages 使用默认分页参数（page 1 / page_size 40）', async () => {
    const response = { success: true, total: 0, data: [], date_groups: [] }
    getMock.mockResolvedValue(response)

    await expect(getLocalCacheImages({ target: 'images' })).resolves.toBe(response)
    expect(getMock).toHaveBeenCalledWith('/api/webui/system/local-cache/images', {
      query: {
        target: 'images',
        page: 1,
        page_size: 40,
        start_date: undefined,
        end_date: undefined,
      },
      errorMessage: '获取本地缓存图片列表失败',
    })
  })

  it('getLocalCacheImages 透传完整分页与日期筛选参数', async () => {
    getMock.mockResolvedValue({ success: true, data: [] })

    await getLocalCacheImages({
      target: 'emoji',
      page: 3,
      page_size: 20,
      start_date: '2026-07-01',
      end_date: '2026-07-26',
    })

    expect(getMock).toHaveBeenCalledWith('/api/webui/system/local-cache/images', {
      query: {
        target: 'emoji',
        page: 3,
        page_size: 20,
        start_date: '2026-07-01',
        end_date: '2026-07-26',
      },
      errorMessage: '获取本地缓存图片列表失败',
    })
  })

  it('deleteLocalCacheImage 以 DELETE 提交 target 与相对路径', async () => {
    const response = { success: true, message: '已删除', removed_files: 1 }
    deleteMock.mockResolvedValue(response)

    await expect(deleteLocalCacheImage('images', '2026/07/a.png')).resolves.toBe(response)
    expect(deleteMock).toHaveBeenCalledWith('/api/webui/system/local-cache/images', {
      body: { target: 'images', relative_path: '2026/07/a.png' },
      errorMessage: '删除本地缓存图片失败',
    })
  })

  it('deleteLocalCacheImagesByDateRange 以 date_range 模式提交日期区间', async () => {
    deleteMock.mockResolvedValue({ success: true })

    await deleteLocalCacheImagesByDateRange('images', '2026-07-01', '2026-07-26')

    expect(deleteMock).toHaveBeenCalledWith('/api/webui/system/local-cache/images/bulk', {
      body: {
        target: 'images',
        mode: 'date_range',
        start_date: '2026-07-01',
        end_date: '2026-07-26',
      },
      errorMessage: '按日期删除缓存失败',
    })
  })

  it('deleteLocalCacheImagesByDateRange 空日期归一化为 null', async () => {
    deleteMock.mockResolvedValue({ success: true })

    await deleteLocalCacheImagesByDateRange('emoji', '', '')

    expect(deleteMock).toHaveBeenCalledWith('/api/webui/system/local-cache/images/bulk', {
      body: {
        target: 'emoji',
        mode: 'date_range',
        start_date: null,
        end_date: null,
      },
      errorMessage: '按日期删除缓存失败',
    })
  })

  it('deleteLocalCacheImagesOlderThanRecentDays 以保留天数模式提交', async () => {
    deleteMock.mockResolvedValue({ success: true })

    await deleteLocalCacheImagesOlderThanRecentDays('emoji', 7)

    expect(deleteMock).toHaveBeenCalledWith('/api/webui/system/local-cache/images/bulk', {
      body: {
        target: 'emoji',
        mode: 'older_than_recent_days',
        keep_recent_days: 7,
      },
      errorMessage: '清理过期缓存失败',
    })
  })
})

describe('日志目录', () => {
  it('getLocalCacheLogDirectories 请求日志目录列表接口', async () => {
    const response = { success: true, total: 0, data: [] }
    getMock.mockResolvedValue(response)

    await expect(getLocalCacheLogDirectories()).resolves.toBe(response)
    expect(getMock).toHaveBeenCalledWith('/api/webui/system/local-cache/log-directories', {
      errorMessage: '获取日志目录列表失败',
    })
  })

  it('deleteLocalCacheLogDirectory 以 DELETE 提交目录相对路径', async () => {
    const response = { success: true, message: '已清理', removed_files: 5 }
    deleteMock.mockResolvedValue(response)

    await expect(deleteLocalCacheLogDirectory('app/2026-07')).resolves.toBe(response)
    expect(deleteMock).toHaveBeenCalledWith('/api/webui/system/local-cache/log-directories', {
      body: { relative_path: 'app/2026-07' },
      errorMessage: '清理日志目录失败',
    })
  })
})
