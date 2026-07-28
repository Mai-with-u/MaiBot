import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError, backendApi } from '@/lib/http'
import type { JargonExportItem } from '@/types/jargon'

import {
  batchDeleteJargons,
  batchSetJargonStatus,
  createJargon,
  deleteJargon,
  exportJargons,
  getJargonChatList,
  getJargonDetail,
  getJargonList,
  getJargonStats,
  importJargons,
  updateJargon,
} from '../jargon-api'

// 只替换 backendApi 的请求方法，保留真实的 ApiError；
// 黑话 API 全部为透传契约（不做 success 解包），失败由请求层抛 ApiError
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

/** 构造一条完整的黑话导出项，便于导入用例复用 */
function buildExportItem(overrides: Partial<JargonExportItem> = {}): JargonExportItem {
  return {
    content: 'yyds',
    meaning: '永远的神',
    count: 3,
    is_jargon: true,
    is_complete: true,
    is_global: false,
    created_by: 'AI',
    ...overrides,
  }
}

describe('getJargonChatList', () => {
  it('把 include_empty 透传到 chats 端点并原样返回响应', async () => {
    const response = { success: true, data: [{ session_id: 's1', chat_name: '测试群' }] }
    getMock.mockResolvedValue(response)

    await expect(getJargonChatList({ include_empty: true })).resolves.toBe(response)
    expect(getMock).toHaveBeenCalledWith('/api/webui/jargon/chats', {
      query: { include_empty: true },
      errorMessage: '获取聊天列表失败',
    })
  })

  it('HTTP 层失败时向上抛出 ApiError', async () => {
    getMock.mockRejectedValue(new ApiError('获取聊天列表失败', { status: 500 }))

    await expect(getJargonChatList()).rejects.toBeInstanceOf(ApiError)
    await expect(getJargonChatList()).rejects.toMatchObject({ status: 500 })
  })
})

describe('getJargonList', () => {
  it('空串与 falsy 分页参数归一化为 undefined，布尔筛选原样保留', async () => {
    const response = { success: true, total: 0, data: [] }
    getMock.mockResolvedValue(response)

    await expect(
      getJargonList({
        page: 0,
        page_size: 20,
        search: '',
        session_id: 's1',
        jargon_status: 'pending',
        is_jargon: false,
        is_complete: true,
        is_global: false,
      })
    ).resolves.toBe(response)

    expect(getMock).toHaveBeenCalledWith('/api/webui/jargon/list', {
      query: {
        page: undefined,
        page_size: 20,
        search: undefined,
        session_id: 's1',
        jargon_status: 'pending',
        is_jargon: false,
        is_complete: true,
        is_global: false,
      },
      errorMessage: '获取黑话列表失败',
    })
  })
})

describe('getJargonDetail', () => {
  it('按 ID 拼接详情 URL', async () => {
    const response = { success: true, data: { id: 12, content: 'yyds' } }
    getMock.mockResolvedValue(response)

    await expect(getJargonDetail(12)).resolves.toBe(response)
    expect(getMock).toHaveBeenCalledWith('/api/webui/jargon/12', {
      errorMessage: '获取黑话详情失败',
    })
  })

  it('后端 404 时向上抛出 ApiError', async () => {
    getMock.mockRejectedValue(new ApiError('获取黑话详情失败', { status: 404 }))

    await expect(getJargonDetail(999)).rejects.toMatchObject({ status: 404 })
  })
})

describe('exportJargons', () => {
  it('未显式指定 include_chat_info 时默认置为 false', async () => {
    const response = { success: true, jargons: [] }
    postMock.mockResolvedValue(response)

    await expect(exportJargons({ ids: [1, 2] })).resolves.toBe(response)
    expect(postMock).toHaveBeenCalledWith('/api/webui/jargon/export', {
      body: { ids: [1, 2], include_chat_info: false },
      errorMessage: '导出黑话失败',
    })
  })

  it('显式指定 include_chat_info 时原样透传', async () => {
    postMock.mockResolvedValue({ success: true, jargons: [] })

    await exportJargons({ include_chat_info: true })
    expect(postMock).toHaveBeenCalledWith('/api/webui/jargon/export', {
      body: { ids: undefined, include_chat_info: true },
      errorMessage: '导出黑话失败',
    })
  })
})

describe('importJargons', () => {
  it('把导入请求体原样提交到导入接口', async () => {
    const request = {
      target_session_ids: ['s1', 's2'],
      jargons: [buildExportItem()],
      conflict_strategy: 'overwrite' as const,
    }
    const response = {
      success: true,
      message: '导入完成',
      imported_count: 1,
      skipped_count: 0,
      failed_count: 0,
    }
    postMock.mockResolvedValue(response)

    await expect(importJargons(request)).resolves.toBe(response)
    expect(postMock).toHaveBeenCalledWith('/api/webui/jargon/import', {
      body: request,
      errorMessage: '导入黑话失败',
    })
  })
})

describe('createJargon', () => {
  it('把创建请求体提交到根路径端点', async () => {
    const request = { content: 'yyds', meaning: '永远的神', is_global: true }
    const response = { success: true, message: '创建成功' }
    postMock.mockResolvedValue(response)

    await expect(createJargon(request)).resolves.toBe(response)
    expect(postMock).toHaveBeenCalledWith('/api/webui/jargon/', {
      body: request,
      errorMessage: '创建黑话失败',
    })
  })
})

describe('updateJargon', () => {
  it('按 ID 发起 PATCH 增量更新', async () => {
    const response = { success: true, message: '更新成功' }
    patchMock.mockResolvedValue(response)

    await expect(updateJargon(7, { meaning: '新含义', is_jargon: true })).resolves.toBe(response)
    expect(patchMock).toHaveBeenCalledWith('/api/webui/jargon/7', {
      body: { meaning: '新含义', is_jargon: true },
      errorMessage: '更新黑话失败',
    })
  })
})

describe('deleteJargon', () => {
  it('按 ID 发起 DELETE', async () => {
    const response = { success: true, message: '删除成功' }
    deleteMock.mockResolvedValue(response)

    await expect(deleteJargon(3)).resolves.toBe(response)
    expect(deleteMock).toHaveBeenCalledWith('/api/webui/jargon/3', {
      errorMessage: '删除黑话失败',
    })
  })
})

describe('batchDeleteJargons', () => {
  it('把 ID 数组包成 ids 请求体提交到批量删除接口', async () => {
    const response = { success: true, message: '批量删除成功' }
    postMock.mockResolvedValue(response)

    await expect(batchDeleteJargons([1, 2, 3])).resolves.toBe(response)
    expect(postMock).toHaveBeenCalledWith('/api/webui/jargon/batch/delete', {
      body: { ids: [1, 2, 3] },
      errorMessage: '批量删除黑话失败',
    })
  })
})

describe('getJargonStats', () => {
  it('从 stats/summary 端点读取统计数据', async () => {
    const response = { success: true, data: { total: 10, confirmed: 4 } }
    getMock.mockResolvedValue(response)

    await expect(getJargonStats()).resolves.toBe(response)
    expect(getMock).toHaveBeenCalledWith('/api/webui/jargon/stats/summary', {
      errorMessage: '获取黑话统计失败',
    })
  })
})

describe('batchSetJargonStatus', () => {
  it('通过 query 参数（而非请求体）提交 ids 与 is_jargon', async () => {
    const response = { success: true, message: '设置成功' }
    postMock.mockResolvedValue(response)

    await expect(batchSetJargonStatus([4, 5], false)).resolves.toBe(response)
    expect(postMock).toHaveBeenCalledWith('/api/webui/jargon/batch/set-jargon', {
      query: { ids: [4, 5], is_jargon: false },
      errorMessage: '批量设置黑话状态失败',
    })
  })

  it('HTTP 层失败时向上抛出 ApiError', async () => {
    postMock.mockRejectedValue(new ApiError('批量设置黑话状态失败', { status: 500 }))

    await expect(batchSetJargonStatus([1], true)).rejects.toMatchObject({ status: 500 })
  })
})
