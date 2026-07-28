import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError, backendApi } from '@/lib/http'

import {
  getAllLogs,
  getChatList,
  getChatLogs,
  getLogDetail,
  getPlannerOverview,
  getPlannerStats,
  getReplierOverview,
  getReplyChatLogs,
  getReplyLogDetail,
} from '../planner-api'

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

beforeEach(() => {
  getMock.mockReset()
})

describe('getPlannerOverview', () => {
  it('请求规划器总览端点并透传响应', async () => {
    const response = { total_chats: 2, total_plans: 10, chats: [] }
    getMock.mockResolvedValue(response)

    await expect(getPlannerOverview()).resolves.toBe(response)
    expect(getMock).toHaveBeenCalledWith('/api/planner/overview', {
      errorMessage: '获取规划器总览失败',
    })
  })

  it('后端失败时向上抛出 ApiError', async () => {
    getMock.mockRejectedValue(new ApiError('获取规划器总览失败', { status: 500 }))

    await expect(getPlannerOverview()).rejects.toBeInstanceOf(ApiError)
    getMock.mockRejectedValue(new ApiError('获取规划器总览失败', { status: 500 }))
    await expect(getPlannerOverview()).rejects.toMatchObject({ status: 500 })
  })
})

describe('getChatLogs', () => {
  it('只传 chatId 时使用默认分页参数且不携带搜索词', async () => {
    const response = { data: [], total: 0, page: 1, page_size: 20, chat_id: 'chat-1' }
    getMock.mockResolvedValue(response)

    await expect(getChatLogs('chat-1')).resolves.toBe(response)

    const [path, options] = getMock.mock.calls[0]
    expect(path).toBe('/api/planner/chat/chat-1/logs')
    expect(options?.query?.page).toBe(1)
    expect(options?.query?.page_size).toBe(20)
    expect(options?.query?.search).toBeUndefined()
    expect(options?.errorMessage).toBe('获取规划日志列表失败')
  })

  it('显式分页与搜索词作为 query 参数传递', async () => {
    getMock.mockResolvedValue({ data: [], total: 0, page: 3, page_size: 50, chat_id: 'chat-2' })

    await getChatLogs('chat-2', 3, 50, '发言')

    expect(getMock).toHaveBeenCalledWith('/api/planner/chat/chat-2/logs', {
      query: { page: 3, page_size: 50, search: '发言' },
      errorMessage: '获取规划日志列表失败',
    })
  })

  it('空字符串搜索词被转换为 undefined 跳过', async () => {
    getMock.mockResolvedValue({ data: [], total: 0, page: 1, page_size: 20, chat_id: 'chat-3' })

    await getChatLogs('chat-3', 1, 20, '')

    const [, options] = getMock.mock.calls[0]
    expect(options?.query?.search).toBeUndefined()
  })
})

describe('getLogDetail', () => {
  it('chatId 与文件名拼接到路径中请求详情', async () => {
    const response = {
      type: 'plan',
      chat_id: 'chat-1',
      timestamp: 1700000000,
      prompt: 'p',
      reasoning: 'r',
      raw_output: 'o',
      actions: [],
      timing: { prompt_build_ms: 1, llm_duration_ms: 2, total_plan_ms: 3, loop_start_time: 0 },
      extra: null,
    }
    getMock.mockResolvedValue(response)

    await expect(getLogDetail('chat-1', 'plan_001.json')).resolves.toBe(response)
    expect(getMock).toHaveBeenCalledWith('/api/planner/log/chat-1/plan_001.json', {
      errorMessage: '获取规划日志详情失败',
    })
  })

  it('日志不存在时向上抛出 ApiError', async () => {
    getMock.mockRejectedValue(new ApiError('获取规划日志详情失败', { status: 404 }))

    await expect(getLogDetail('chat-1', 'missing.json')).rejects.toMatchObject({ status: 404 })
  })
})

describe('getPlannerStats', () => {
  it('请求兼容旧接口的统计端点', async () => {
    const response = {
      total_chats: 1,
      total_plans: 5,
      avg_plan_time_ms: 120,
      avg_llm_time_ms: 80,
      recent_plans: [],
    }
    getMock.mockResolvedValue(response)

    await expect(getPlannerStats()).resolves.toBe(response)
    expect(getMock).toHaveBeenCalledWith('/api/planner/stats', {
      errorMessage: '获取规划器统计失败',
    })
  })
})

describe('getAllLogs', () => {
  it('默认请求第一页每页 20 条', async () => {
    const response = { data: [], total: 0, page: 1, page_size: 20 }
    getMock.mockResolvedValue(response)

    await expect(getAllLogs()).resolves.toBe(response)
    expect(getMock).toHaveBeenCalledWith('/api/planner/all-logs', {
      query: { page: 1, page_size: 20 },
      errorMessage: '获取规划日志失败',
    })
  })

  it('显式分页参数透传给 query', async () => {
    getMock.mockResolvedValue({ data: [], total: 0, page: 4, page_size: 10 })

    await getAllLogs(4, 10)

    expect(getMock).toHaveBeenCalledWith('/api/planner/all-logs', {
      query: { page: 4, page_size: 10 },
      errorMessage: '获取规划日志失败',
    })
  })
})

describe('getChatList', () => {
  it('请求聊天列表端点并返回 chat_id 数组', async () => {
    getMock.mockResolvedValue(['chat-a', 'chat-b'])

    await expect(getChatList()).resolves.toEqual(['chat-a', 'chat-b'])
    expect(getMock).toHaveBeenCalledWith('/api/planner/chats', {
      errorMessage: '获取聊天列表失败',
    })
  })
})

describe('getReplierOverview', () => {
  it('请求回复器总览端点并透传响应', async () => {
    const response = { total_chats: 1, total_replies: 6, chats: [] }
    getMock.mockResolvedValue(response)

    await expect(getReplierOverview()).resolves.toBe(response)
    expect(getMock).toHaveBeenCalledWith('/api/replier/overview', {
      errorMessage: '获取回复器总览失败',
    })
  })
})

describe('getReplyChatLogs', () => {
  it('分页与搜索词作为 query 参数传递', async () => {
    const response = { data: [], total: 0, page: 2, page_size: 30, chat_id: 'chat-r' }
    getMock.mockResolvedValue(response)

    await expect(getReplyChatLogs('chat-r', 2, 30, '回复')).resolves.toBe(response)
    expect(getMock).toHaveBeenCalledWith('/api/replier/chat/chat-r/logs', {
      query: { page: 2, page_size: 30, search: '回复' },
      errorMessage: '获取回复日志列表失败',
    })
  })

  it('省略搜索词时不携带 search 参数', async () => {
    getMock.mockResolvedValue({ data: [], total: 0, page: 1, page_size: 20, chat_id: 'chat-r' })

    await getReplyChatLogs('chat-r')

    const [, options] = getMock.mock.calls[0]
    expect(options?.query?.page).toBe(1)
    expect(options?.query?.page_size).toBe(20)
    expect(options?.query?.search).toBeUndefined()
  })
})

describe('getReplyLogDetail', () => {
  it('chatId 与文件名拼接到路径中请求回复详情', async () => {
    const response = {
      type: 'reply',
      chat_id: 'chat-r',
      timestamp: 1700000001,
      prompt: 'p',
      output: 'o',
      processed_output: ['o'],
      model: 'demo-model',
      reasoning: '',
      think_level: 0,
      timing: { prompt_ms: 1, overall_ms: 5, timing_logs: [], llm_ms: 3, almost_zero: '0' },
      error: null,
      success: true,
    }
    getMock.mockResolvedValue(response)

    await expect(getReplyLogDetail('chat-r', 'reply_001.json')).resolves.toBe(response)
    expect(getMock).toHaveBeenCalledWith('/api/replier/log/chat-r/reply_001.json', {
      errorMessage: '获取回复日志详情失败',
    })
  })

  it('后端失败时向上抛出 ApiError', async () => {
    getMock.mockRejectedValue(new ApiError('获取回复日志详情失败', { status: 500 }))

    await expect(getReplyLogDetail('chat-r', 'x.json')).rejects.toBeInstanceOf(ApiError)
  })
})
