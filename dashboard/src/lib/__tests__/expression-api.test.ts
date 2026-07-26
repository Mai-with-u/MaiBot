import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError, backendApi } from '@/lib/http'

import {
  approveExpressionReviewLog,
  batchDeleteExpressions,
  batchReviewExpressions,
  clearExpressions,
  createExpression,
  deleteExpression,
  exportExpressions,
  getChatList,
  getExpressionChatTargets,
  getExpressionClusterMembers,
  getExpressionClusters,
  getExpressionDetail,
  getExpressionGroups,
  getExpressionList,
  getExpressionReviewLogs,
  getExpressionStats,
  getReviewList,
  getReviewStats,
  importExpressions,
  importLegacyExpressions,
  previewLegacyExpressionImport,
  previewLegacyExpressionImportFile,
  updateExpression,
  updateExpressionReviewStatus,
} from '../expression-api'

// 只替换 backendApi 的请求方法，保留真实的 ApiError / requireSuccess，
// 以便验证业务级 success 标记解包与 throw 契约的真实行为
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

describe('getChatList', () => {
  it('默认不带 include_legacy 参数并解包 data 字段', async () => {
    const chats = [{ chat_id: 'c1', chat_name: '测试群' }]
    getMock.mockResolvedValue({ success: true, data: chats })

    await expect(getChatList()).resolves.toBe(chats)
    expect(getMock).toHaveBeenCalledWith('/api/webui/expression/chats', {
      query: { include_legacy: undefined },
      errorMessage: '获取聊天列表失败',
    })
  })

  it('include_legacy 为 true 时透传到 query', async () => {
    getMock.mockResolvedValue({ success: true, data: [] })

    await getChatList({ include_legacy: true })
    expect(getMock).toHaveBeenCalledWith('/api/webui/expression/chats', {
      query: { include_legacy: true },
      errorMessage: '获取聊天列表失败',
    })
  })

  it('业务级 success 为 false 时抛出 ApiError（优先使用后端 message）', async () => {
    getMock.mockResolvedValue({ success: false, message: '数据库不可用' })

    await expect(getChatList()).rejects.toBeInstanceOf(ApiError)
    await expect(getChatList()).rejects.toMatchObject({ message: '数据库不可用' })
  })
})

describe('getExpressionChatTargets', () => {
  it('从 chat-targets 端点读取并解包 data 字段', async () => {
    const chats = [{ chat_id: 'c2', chat_name: '目标群' }]
    getMock.mockResolvedValue({ success: true, data: chats })

    await expect(getExpressionChatTargets({ include_legacy: true })).resolves.toBe(chats)
    expect(getMock).toHaveBeenCalledWith('/api/webui/expression/chat-targets', {
      query: { include_legacy: true },
      errorMessage: '获取导入目标聊天流失败',
    })
  })
})

describe('getExpressionGroups', () => {
  it('从 groups 端点读取并解包 data 字段', async () => {
    const groups = [{ group_id: 1, chat_ids: ['c1'] }]
    getMock.mockResolvedValue({ success: true, data: groups })

    await expect(getExpressionGroups()).resolves.toBe(groups)
    expect(getMock).toHaveBeenCalledWith('/api/webui/expression/groups', {
      query: { include_legacy: undefined },
      errorMessage: '获取表达共享组失败',
    })
  })
})

describe('getExpressionList', () => {
  it('空串与 falsy 参数被归一化为 undefined，数组参数原样透传，返回完整分页响应', async () => {
    const response = { success: true, total: 1, page: 2, page_size: 50, data: [] }
    getMock.mockResolvedValue(response)

    await expect(
      getExpressionList({
        page: 2,
        page_size: 50,
        search: '',
        chat_id: '',
        chat_ids: ['c1', 'c2'],
        include_legacy: false,
        review_filter: 'all',
        sort_by: 'time',
      })
    ).resolves.toBe(response)

    expect(getMock).toHaveBeenCalledWith('/api/webui/expression/list', {
      query: {
        page: 2,
        page_size: 50,
        search: undefined,
        chat_id: undefined,
        include_legacy: undefined,
        review_filter: 'all',
        sort_by: 'time',
        chat_ids: ['c1', 'c2'],
      },
      errorMessage: '获取表达方式列表失败',
    })
  })

  it('业务级失败时抛出 ApiError', async () => {
    getMock.mockResolvedValue({ success: false, message: '查询失败' })

    await expect(getExpressionList({})).rejects.toMatchObject({ message: '查询失败' })
  })

  it('HTTP 层失败时向上抛出 ApiError', async () => {
    getMock.mockRejectedValue(new ApiError('获取表达方式列表失败', { status: 500 }))

    await expect(getExpressionList({ page: 1 })).rejects.toMatchObject({ status: 500 })
  })
})

describe('exportExpressions', () => {
  it('把 chat_id 与 ids 作为请求体提交到导出接口并透传响应', async () => {
    const response = { success: true, data: [] }
    postMock.mockResolvedValue(response)

    await expect(exportExpressions({ chat_id: 'c1', ids: [1, 2] })).resolves.toBe(response)
    expect(postMock).toHaveBeenCalledWith('/api/webui/expression/export', {
      body: { chat_id: 'c1', ids: [1, 2] },
      errorMessage: '导出表达方式失败',
    })
  })
})

describe('importExpressions', () => {
  it('把目标聊天与表达数组作为请求体提交到导入接口', async () => {
    const expressions = [
      {
        situation: '打招呼',
        style: '哈喽',
        content_list: '[]',
        count: 1,
        last_active_time: null,
        create_time: null,
        checked: false,
        modified_by: null,
      },
    ]
    const response = { success: true, imported: 1 }
    postMock.mockResolvedValue(response)

    await expect(importExpressions({ chat_id: 'c1', expressions })).resolves.toBe(response)
    expect(postMock).toHaveBeenCalledWith('/api/webui/expression/import', {
      body: { chat_id: 'c1', expressions },
      errorMessage: '导入表达方式失败',
    })
  })
})

describe('clearExpressions', () => {
  it('把 chat_id 提交到 clear 接口', async () => {
    const response = { success: true, deleted: 3 }
    postMock.mockResolvedValue(response)

    await expect(clearExpressions({ chat_id: 'c1' })).resolves.toBe(response)
    expect(postMock).toHaveBeenCalledWith('/api/webui/expression/clear', {
      body: { chat_id: 'c1' },
      errorMessage: '清除表达方式失败',
    })
  })
})

describe('previewLegacyExpressionImport', () => {
  it('把 db_path 提交到旧版导入预览接口', async () => {
    const response = { success: true, chats: [] }
    postMock.mockResolvedValue(response)

    await expect(previewLegacyExpressionImport({ db_path: '/tmp/old.db' })).resolves.toBe(response)
    expect(postMock).toHaveBeenCalledWith('/api/webui/expression/legacy-import/preview', {
      body: { db_path: '/tmp/old.db' },
      errorMessage: '预览旧版导入失败',
    })
  })
})

describe('previewLegacyExpressionImportFile', () => {
  it('把上传文件包进 FormData 提交到 preview-file 接口', async () => {
    const response = { success: true, chats: [] }
    postMock.mockResolvedValue(response)
    const file = new File(['db-bytes'], 'legacy.db')

    await expect(previewLegacyExpressionImportFile(file)).resolves.toBe(response)

    const [path, options] = postMock.mock.calls[0]
    expect(path).toBe('/api/webui/expression/legacy-import/preview-file')
    expect(options?.errorMessage).toBe('预览旧版导入失败')
    expect(options?.body).toBeInstanceOf(FormData)
    const formData = options?.body as FormData
    expect(formData.get('file')).toBe(file)
  })
})

describe('importLegacyExpressions', () => {
  it('把 db_path 与聊天映射提交到旧版导入接口', async () => {
    const mappings = [{ old_chat_id: 'old-1', target_chat_ids: ['c1'] }]
    const response = { success: true, imported: 5 }
    postMock.mockResolvedValue(response)

    await expect(importLegacyExpressions({ db_path: '/tmp/old.db', mappings })).resolves.toBe(
      response
    )
    expect(postMock).toHaveBeenCalledWith('/api/webui/expression/legacy-import/import', {
      body: { db_path: '/tmp/old.db', mappings },
      errorMessage: '旧版导入失败',
    })
  })
})

describe('getExpressionDetail', () => {
  it('按 ID 拼接详情 URL 并解包 data 字段', async () => {
    const detail = { id: 42, situation: '打招呼' }
    getMock.mockResolvedValue({ success: true, data: detail })

    await expect(getExpressionDetail(42)).resolves.toBe(detail)
    expect(getMock).toHaveBeenCalledWith('/api/webui/expression/42', {
      errorMessage: '获取表达方式详情失败',
    })
  })

  it('业务级失败时抛出 ApiError', async () => {
    getMock.mockResolvedValue({ success: false, message: '表达方式不存在' })

    await expect(getExpressionDetail(404)).rejects.toMatchObject({ message: '表达方式不存在' })
  })
})

describe('createExpression', () => {
  it('提交创建请求体并解包 data 字段', async () => {
    const created = { id: 1, situation: '打招呼', style: '哈喽' }
    postMock.mockResolvedValue({ success: true, data: created })

    await expect(
      createExpression({ situation: '打招呼', style: '哈喽', chat_id: 'c1' })
    ).resolves.toBe(created)
    expect(postMock).toHaveBeenCalledWith('/api/webui/expression/', {
      body: { situation: '打招呼', style: '哈喽', chat_id: 'c1' },
      errorMessage: '创建表达方式失败',
    })
  })
})

describe('updateExpression', () => {
  it('按 ID 发起 PATCH 并返回 data 字段', async () => {
    const updated = { id: 7, style: '嗨' }
    patchMock.mockResolvedValue({ success: true, data: updated })

    await expect(updateExpression(7, { style: '嗨' })).resolves.toBe(updated)
    expect(patchMock).toHaveBeenCalledWith('/api/webui/expression/7', {
      body: { style: '嗨' },
      errorMessage: '更新表达方式失败',
    })
  })

  it('响应缺少 data 字段时退化为返回空对象', async () => {
    patchMock.mockResolvedValue({ success: true })

    await expect(updateExpression(7, { style: '嗨' })).resolves.toEqual({})
  })
})

describe('updateExpressionReviewStatus', () => {
  it('把 approved 标记提交到 review-status 端点并返回更新后的表达', async () => {
    const updated = { id: 9, checked: true }
    patchMock.mockResolvedValue({ success: true, data: updated })

    await expect(updateExpressionReviewStatus(9, true)).resolves.toBe(updated)
    expect(patchMock).toHaveBeenCalledWith('/api/webui/expression/9/review-status', {
      body: { approved: true },
      errorMessage: '更新表达方式审核状态失败',
    })
  })

  it('成功响应缺少 data 时仍抛出 ApiError', async () => {
    patchMock.mockResolvedValue({ success: true })

    await expect(updateExpressionReviewStatus(9, false)).rejects.toBeInstanceOf(ApiError)
    await expect(updateExpressionReviewStatus(9, false)).rejects.toMatchObject({
      message: '更新表达方式审核状态失败',
    })
  })

  it('业务级失败时抛出携带后端 message 的 ApiError', async () => {
    patchMock.mockResolvedValue({ success: false, message: '状态更新被拒绝' })

    await expect(updateExpressionReviewStatus(9, true)).rejects.toMatchObject({
      message: '状态更新被拒绝',
    })
  })
})

describe('deleteExpression', () => {
  it('按 ID 发起 DELETE 并在成功时返回空对象', async () => {
    deleteMock.mockResolvedValue({ success: true })

    await expect(deleteExpression(3)).resolves.toEqual({})
    expect(deleteMock).toHaveBeenCalledWith('/api/webui/expression/3', {
      errorMessage: '删除表达方式失败',
    })
  })

  it('业务级失败时抛出 ApiError', async () => {
    deleteMock.mockResolvedValue({ success: false, message: '删除被拒绝' })

    await expect(deleteExpression(3)).rejects.toMatchObject({ message: '删除被拒绝' })
  })
})

describe('batchDeleteExpressions', () => {
  it('把 ID 数组包成 ids 请求体提交到批量删除接口', async () => {
    postMock.mockResolvedValue({ success: true })

    await expect(batchDeleteExpressions([1, 2, 3])).resolves.toEqual({})
    expect(postMock).toHaveBeenCalledWith('/api/webui/expression/batch/delete', {
      body: { ids: [1, 2, 3] },
      errorMessage: '批量删除表达方式失败',
    })
  })
})

describe('getExpressionStats', () => {
  it('从 stats/summary 端点读取并解包 data 字段', async () => {
    const stats = { total: 10, checked: 4 }
    getMock.mockResolvedValue({ success: true, data: stats })

    await expect(getExpressionStats({ include_legacy: true })).resolves.toBe(stats)
    expect(getMock).toHaveBeenCalledWith('/api/webui/expression/stats/summary', {
      query: { include_legacy: true },
      errorMessage: '获取统计数据失败',
    })
  })
})

describe('getExpressionClusters', () => {
  it('读取聚类摘要并返回完整响应', async () => {
    const response = { success: true, clusters: [], total: 0 }
    getMock.mockResolvedValue(response)

    await expect(getExpressionClusters()).resolves.toBe(response)
    expect(getMock).toHaveBeenCalledWith('/api/webui/expression/clusters', {
      errorMessage: '获取表达聚类失败',
    })
  })
})

describe('getExpressionClusterMembers', () => {
  it('按聚类 ID 拼接成员 URL，空 profile_marker 归一化为 undefined', async () => {
    const response = { success: true, members: [] }
    getMock.mockResolvedValue(response)

    await expect(getExpressionClusterMembers({ cluster_id: 5, profile_marker: '' })).resolves.toBe(
      response
    )
    expect(getMock).toHaveBeenCalledWith('/api/webui/expression/clusters/5/members', {
      query: { profile_marker: undefined },
      errorMessage: '获取表达聚类成员失败',
    })
  })
})

describe('getReviewStats', () => {
  it('直接透传审核统计响应（不做 success 解包）', async () => {
    const response = { total: 5, unchecked: 2 }
    getMock.mockResolvedValue(response)

    await expect(getReviewStats()).resolves.toBe(response)
    expect(getMock).toHaveBeenCalledWith('/api/webui/expression/review/stats', {
      errorMessage: '获取审核统计失败',
    })
  })
})

describe('getReviewList', () => {
  it('归一化分页与筛选参数后请求审核列表', async () => {
    const response = { success: true, total: 0, data: [] }
    getMock.mockResolvedValue(response)

    await expect(
      getReviewList({
        page: 1,
        page_size: 10,
        filter_type: 'unchecked',
        order: 'random',
        search: '',
        chat_id: 'c1',
        exclude_ids: [8, 9],
      })
    ).resolves.toBe(response)

    expect(getMock).toHaveBeenCalledWith('/api/webui/expression/review/list', {
      query: {
        page: 1,
        page_size: 10,
        filter_type: 'unchecked',
        order: 'random',
        search: undefined,
        chat_id: 'c1',
        exclude_ids: [8, 9],
      },
      errorMessage: '获取审核列表失败',
    })
  })

  it('业务级失败时抛出 ApiError', async () => {
    getMock.mockResolvedValue({ success: false })

    await expect(getReviewList({})).rejects.toMatchObject({ message: '获取审核列表失败' })
  })
})

describe('batchReviewExpressions', () => {
  it('把审核项数组包成 items 请求体提交', async () => {
    const items = [{ id: 1, approved: true }]
    const response = { success: true, processed: 1 }
    postMock.mockResolvedValue(response)

    await expect(batchReviewExpressions(items)).resolves.toBe(response)
    expect(postMock).toHaveBeenCalledWith('/api/webui/expression/review/batch', {
      body: { items },
      errorMessage: '批量审核失败',
    })
  })
})

describe('getExpressionReviewLogs', () => {
  it('passed=false 原样保留，limit 为 0 时归一化为 undefined', async () => {
    const response = { success: true, logs: [] }
    getMock.mockResolvedValue(response)

    await expect(getExpressionReviewLogs({ limit: 0, passed: false, chat_id: '' })).resolves.toBe(
      response
    )
    expect(getMock).toHaveBeenCalledWith('/api/webui/expression/review/logs', {
      query: { limit: undefined, passed: false, chat_id: undefined },
      errorMessage: '获取 AI 审核记录失败',
    })
  })
})

describe('approveExpressionReviewLog', () => {
  it('按审核记录 ID 拼接 approve URL 发起 POST', async () => {
    const response = { success: true }
    postMock.mockResolvedValue(response)

    await expect(approveExpressionReviewLog('log-1')).resolves.toBe(response)
    expect(postMock).toHaveBeenCalledWith('/api/webui/expression/review/logs/log-1/approve', {
      errorMessage: '恢复表达方式失败',
    })
  })

  it('HTTP 层失败时向上抛出 ApiError', async () => {
    postMock.mockRejectedValue(new ApiError('恢复表达方式失败', { status: 404 }))

    await expect(approveExpressionReviewLog('log-x')).rejects.toMatchObject({ status: 404 })
  })
})
