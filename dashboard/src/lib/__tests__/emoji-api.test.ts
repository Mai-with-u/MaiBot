import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError, backendApi } from '@/lib/http'

import {
  banEmoji,
  batchDeleteEmojis,
  cleanupThumbnailCache,
  clearAllThumbnailCache,
  deleteEmoji,
  getEmojiBatchUploadUrl,
  getEmojiDetail,
  getEmojiList,
  getEmojiOriginalUrl,
  getEmojiStats,
  getEmojiThumbnailUrl,
  getEmojiUploadUrl,
  getThumbnailCacheStats,
  preheatThumbnailCache,
  registerEmoji,
  updateEmoji,
} from '../emoji-api'

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
const patchMock = vi.mocked(backendApi.patch)
const deleteMock = vi.mocked(backendApi.delete)

beforeEach(() => {
  getMock.mockReset()
  postMock.mockReset()
  patchMock.mockReset()
  deleteMock.mockReset()
})

describe('getEmojiList', () => {
  it('把筛选与排序参数完整传入列表接口', async () => {
    const response = { success: true, total: 1, page: 2, page_size: 10, data: [] }
    getMock.mockResolvedValue(response)

    await expect(
      getEmojiList({
        page: 2,
        page_size: 10,
        search: '猫',
        is_registered: true,
        is_banned: false,
        status: 'adopted',
        format: 'gif',
        sort_by: 'usage_count',
        sort_order: 'desc',
      })
    ).resolves.toBe(response)

    expect(getMock).toHaveBeenCalledWith('/api/webui/emoji/list', {
      query: {
        page: 2,
        page_size: 10,
        search: '猫',
        is_registered: true,
        is_banned: false,
        status: 'adopted',
        format: 'gif',
        sort_by: 'usage_count',
        sort_order: 'desc',
      },
      errorMessage: '获取表情包列表失败',
    })
  })

  it('空字符串与 0 会被归一化为 undefined，布尔筛选原样透传', async () => {
    getMock.mockResolvedValue({ success: true, total: 0, page: 1, page_size: 20, data: [] })

    await getEmojiList({ page: 0, search: '', is_registered: false, is_banned: true })

    expect(getMock).toHaveBeenCalledWith('/api/webui/emoji/list', {
      query: {
        page: undefined,
        page_size: undefined,
        search: undefined,
        is_registered: false,
        is_banned: true,
        status: undefined,
        format: undefined,
        sort_by: undefined,
        sort_order: undefined,
      },
      errorMessage: '获取表情包列表失败',
    })
  })

  it('后端返回错误时向上抛出 ApiError', async () => {
    getMock.mockRejectedValue(new ApiError('获取表情包列表失败', { status: 500 }))

    await expect(getEmojiList({})).rejects.toBeInstanceOf(ApiError)
  })
})

describe('getEmojiDetail', () => {
  it('按 ID 拼接详情路径并原样返回响应', async () => {
    const response = { success: true, data: { id: 5 } }
    getMock.mockResolvedValue(response)

    await expect(getEmojiDetail(5)).resolves.toBe(response)
    expect(getMock).toHaveBeenCalledWith('/api/webui/emoji/5', {
      errorMessage: '获取表情包详情失败',
    })
  })

  it('后端返回 404 时向上抛出 ApiError', async () => {
    getMock.mockRejectedValue(new ApiError('获取表情包详情失败', { status: 404 }))

    await expect(getEmojiDetail(999)).rejects.toMatchObject({ status: 404 })
  })
})

describe('updateEmoji', () => {
  it('以 PATCH 提交更新字段', async () => {
    const response = { success: true, message: '已更新' }
    patchMock.mockResolvedValue(response)

    await expect(updateEmoji(5, { description: '开心', is_banned: false })).resolves.toBe(response)
    expect(patchMock).toHaveBeenCalledWith('/api/webui/emoji/5', {
      body: { description: '开心', is_banned: false },
      errorMessage: '更新表情包失败',
    })
  })
})

describe('deleteEmoji', () => {
  it('以 DELETE 按 ID 删除表情包', async () => {
    const response = { success: true, message: '已删除' }
    deleteMock.mockResolvedValue(response)

    await expect(deleteEmoji(7)).resolves.toBe(response)
    expect(deleteMock).toHaveBeenCalledWith('/api/webui/emoji/7', {
      errorMessage: '删除表情包失败',
    })
  })

  it('后端返回错误时向上抛出 ApiError', async () => {
    deleteMock.mockRejectedValue(new ApiError('删除表情包失败', { status: 500 }))

    await expect(deleteEmoji(7)).rejects.toBeInstanceOf(ApiError)
  })
})

describe('getEmojiStats', () => {
  it('从统计接口读取汇总数据', async () => {
    const response = { success: true, data: { total: 10 } }
    getMock.mockResolvedValue(response)

    await expect(getEmojiStats()).resolves.toBe(response)
    expect(getMock).toHaveBeenCalledWith('/api/webui/emoji/stats/summary', {
      errorMessage: '获取统计数据失败',
    })
  })
})

describe('registerEmoji / banEmoji', () => {
  it('registerEmoji 以 POST 请求注册接口', async () => {
    const response = { success: true, message: '已注册' }
    postMock.mockResolvedValue(response)

    await expect(registerEmoji(3)).resolves.toBe(response)
    expect(postMock).toHaveBeenCalledWith('/api/webui/emoji/3/register', {
      errorMessage: '注册表情包失败',
    })
  })

  it('banEmoji 以 POST 请求封禁接口', async () => {
    const response = { success: true, message: '已封禁' }
    postMock.mockResolvedValue(response)

    await expect(banEmoji(4)).resolves.toBe(response)
    expect(postMock).toHaveBeenCalledWith('/api/webui/emoji/4/ban', {
      errorMessage: '封禁表情包失败',
    })
  })
})

describe('URL 构造函数', () => {
  it('getEmojiThumbnailUrl 默认返回缩略图地址', () => {
    expect(getEmojiThumbnailUrl(8)).toBe('/api/webui/emoji/8/thumbnail')
  })

  it('getEmojiThumbnailUrl 传入 original=true 时返回原图地址', () => {
    expect(getEmojiThumbnailUrl(8, true)).toBe('/api/webui/emoji/8/thumbnail?original=true')
  })

  it('getEmojiOriginalUrl 始终返回原图地址', () => {
    expect(getEmojiOriginalUrl(9)).toBe('/api/webui/emoji/9/thumbnail?original=true')
  })

  it('getEmojiUploadUrl / getEmojiBatchUploadUrl 返回上传端点', () => {
    expect(getEmojiUploadUrl()).toBe('/api/webui/emoji/upload')
    expect(getEmojiBatchUploadUrl()).toBe('/api/webui/emoji/batch/upload')
  })
})

describe('batchDeleteEmojis', () => {
  it('把 ID 列表作为请求体提交批量删除', async () => {
    const response = {
      success: true,
      message: '完成',
      deleted_count: 2,
      failed_count: 1,
      failed_ids: [3],
    }
    postMock.mockResolvedValue(response)

    await expect(batchDeleteEmojis([1, 2, 3])).resolves.toBe(response)
    expect(postMock).toHaveBeenCalledWith('/api/webui/emoji/batch/delete', {
      body: { emoji_ids: [1, 2, 3] },
      errorMessage: '批量删除失败',
    })
  })

  it('后端返回错误时向上抛出 ApiError', async () => {
    postMock.mockRejectedValue(new ApiError('批量删除失败', { status: 500 }))

    await expect(batchDeleteEmojis([1])).rejects.toMatchObject({ status: 500 })
  })
})

describe('缩略图缓存管理', () => {
  it('getThumbnailCacheStats 读取缓存统计', async () => {
    const response = {
      success: true,
      cache_dir: '/cache',
      total_count: 10,
      total_size_mb: 1.5,
      emoji_count: 12,
      coverage_percent: 83.3,
    }
    getMock.mockResolvedValue(response)

    await expect(getThumbnailCacheStats()).resolves.toBe(response)
    expect(getMock).toHaveBeenCalledWith('/api/webui/emoji/thumbnail-cache/stats', {
      errorMessage: '获取缩略图缓存统计失败',
    })
  })

  it('cleanupThumbnailCache 以 POST 触发清理', async () => {
    const response = { success: true, message: '完成', cleaned_count: 2, kept_count: 8 }
    postMock.mockResolvedValue(response)

    await expect(cleanupThumbnailCache()).resolves.toBe(response)
    expect(postMock).toHaveBeenCalledWith('/api/webui/emoji/thumbnail-cache/cleanup', {
      errorMessage: '清理缩略图缓存失败',
    })
  })

  it('preheatThumbnailCache 默认预热 100 个', async () => {
    const response = {
      success: true,
      message: '完成',
      generated_count: 5,
      skipped_count: 95,
      failed_count: 0,
    }
    postMock.mockResolvedValue(response)

    await expect(preheatThumbnailCache()).resolves.toBe(response)
    expect(postMock).toHaveBeenCalledWith('/api/webui/emoji/thumbnail-cache/preheat', {
      query: { limit: 100 },
      errorMessage: '预热缩略图缓存失败',
    })
  })

  it('preheatThumbnailCache 可传入自定义预热数量', async () => {
    postMock.mockResolvedValue({
      success: true,
      message: '完成',
      generated_count: 0,
      skipped_count: 0,
      failed_count: 0,
    })

    await preheatThumbnailCache(500)

    expect(postMock).toHaveBeenCalledWith('/api/webui/emoji/thumbnail-cache/preheat', {
      query: { limit: 500 },
      errorMessage: '预热缩略图缓存失败',
    })
  })

  it('clearAllThumbnailCache 以 DELETE 清空缓存', async () => {
    const response = { success: true, message: '已清空', cleaned_count: 10, kept_count: 0 }
    deleteMock.mockResolvedValue(response)

    await expect(clearAllThumbnailCache()).resolves.toBe(response)
    expect(deleteMock).toHaveBeenCalledWith('/api/webui/emoji/thumbnail-cache/clear', {
      errorMessage: '清空缩略图缓存失败',
    })
  })

  it('缓存接口失败时向上抛出 ApiError', async () => {
    getMock.mockRejectedValue(new ApiError('获取缩略图缓存统计失败', { status: 503 }))

    await expect(getThumbnailCacheStats()).rejects.toMatchObject({ status: 503 })
  })
})
