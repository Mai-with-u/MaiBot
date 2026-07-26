import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError, backendApi } from '@/lib/http'

import {
  deleteChatStream,
  deleteChatStreamPrompt,
  deleteChatStreamTalkFrequency,
  getChatStreamDetail,
  getChatStreams,
  resolveChatTarget,
  resolveChatTargets,
  updateChatStreamAdapterPolicy,
  updateChatStreamLearning,
  updateChatStreamTalkFrequency,
  upsertChatStreamPrompt,
} from '../chat-management-api'

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
const putMock = vi.mocked(backendApi.put)
const deleteMock = vi.mocked(backendApi.delete)

/** 构造一个最小的聊天流详情桩对象（仅用于引用相等断言） */
const detailStub = { session_id: 'sess-1', display_name: '测试群' }

beforeEach(() => {
  getMock.mockReset()
  postMock.mockReset()
  putMock.mockReset()
  deleteMock.mockReset()
})

describe('getChatStreams', () => {
  it('默认以 limit=1000 请求聊天流列表并返回 sessions', async () => {
    const sessions = [{ session_id: 's1' }, { session_id: 's2' }]
    getMock.mockResolvedValue({ success: true, sessions, total: 2 })

    await expect(getChatStreams()).resolves.toBe(sessions)
    expect(getMock).toHaveBeenCalledWith('/api/chat/sessions', {
      query: { limit: 1000 },
    })
  })

  it('可以传入自定义 limit', async () => {
    getMock.mockResolvedValue({ success: true, sessions: [] })

    await getChatStreams(50)

    expect(getMock).toHaveBeenCalledWith('/api/chat/sessions', {
      query: { limit: 50 },
    })
  })

  it('后端未返回 sessions 字段时回退为空数组', async () => {
    getMock.mockResolvedValue({ success: true })

    await expect(getChatStreams()).resolves.toEqual([])
  })
})

describe('resolveChatTargets', () => {
  it('把目标列表作为请求体提交到解析接口并返回 results', async () => {
    const targets = [{ platform: 'qq', item_id: '123', rule_type: 'group' }]
    const results = [{ found: true, session: { session_id: 's1' } }]
    postMock.mockResolvedValue({ success: true, results })

    await expect(resolveChatTargets(targets)).resolves.toBe(results)
    expect(postMock).toHaveBeenCalledWith('/api/chat/resolve-targets', {
      body: { targets },
      errorMessage: '解析聊天流失败',
    })
  })

  it('后端未返回 results 字段时回退为空数组', async () => {
    postMock.mockResolvedValue({ success: true })

    await expect(resolveChatTargets([])).resolves.toEqual([])
  })

  it('后端返回错误时向上抛出 ApiError', async () => {
    postMock.mockRejectedValue(new ApiError('解析聊天流失败', { status: 500 }))

    await expect(resolveChatTargets([])).rejects.toBeInstanceOf(ApiError)
  })
})

describe('resolveChatTarget', () => {
  it('把单目标包装为批量请求并展开首个结果', async () => {
    const session = { session_id: 's1' }
    postMock.mockResolvedValue({ success: true, results: [{ found: true, session }] })

    await expect(resolveChatTarget('qq', '123', 'group')).resolves.toEqual({
      success: true,
      found: true,
      session,
    })
    expect(postMock).toHaveBeenCalledWith('/api/chat/resolve-targets', {
      body: { targets: [{ platform: 'qq', item_id: '123', rule_type: 'group' }] },
      errorMessage: '解析聊天流失败',
    })
  })

  it('结果为空时返回 found=false 且 session=null', async () => {
    postMock.mockResolvedValue({ success: true, results: [] })

    await expect(resolveChatTarget('qq', '456', 'private')).resolves.toEqual({
      success: true,
      found: false,
      session: null,
    })
  })
})

describe('getChatStreamDetail', () => {
  it('对 sessionId 做 URL 编码并返回 detail', async () => {
    getMock.mockResolvedValue({ success: true, detail: detailStub })

    await expect(getChatStreamDetail('a/b c')).resolves.toBe(detailStub)
    expect(getMock).toHaveBeenCalledWith('/api/chat/sessions/a%2Fb%20c')
  })

  it('后端未返回 detail 时抛出「聊天流详情为空」', async () => {
    getMock.mockResolvedValue({ success: true })

    await expect(getChatStreamDetail('s1')).rejects.toThrow('聊天流详情为空')
  })
})

describe('updateChatStreamTalkFrequency', () => {
  it('以 PUT 提交发言频率规则并返回最新详情', async () => {
    const payload = { previous_time: '8:00', time: '9:00', value: 1 }
    putMock.mockResolvedValue({ success: true, detail: detailStub })

    await expect(updateChatStreamTalkFrequency('s1', payload)).resolves.toBe(detailStub)
    expect(putMock).toHaveBeenCalledWith('/api/chat/sessions/s1/talk-frequency', {
      body: payload,
      errorMessage: '保存发言频率失败',
    })
  })

  it('后端未返回 detail 时抛出「聊天流详情为空」', async () => {
    putMock.mockResolvedValue({ success: true })

    await expect(updateChatStreamTalkFrequency('s1', { time: '9:00', value: 1 })).rejects.toThrow(
      '聊天流详情为空'
    )
  })
})

describe('deleteChatStreamTalkFrequency', () => {
  it('以 DELETE 携带 time 查询参数删除规则并返回最新详情', async () => {
    deleteMock.mockResolvedValue({ success: true, detail: detailStub })

    await expect(deleteChatStreamTalkFrequency('s1', '9:00')).resolves.toBe(detailStub)
    expect(deleteMock).toHaveBeenCalledWith('/api/chat/sessions/s1/talk-frequency', {
      query: { time: '9:00' },
      errorMessage: '删除发言频率规则失败',
    })
  })

  it('后端未返回 detail 时抛出「聊天流详情为空」', async () => {
    deleteMock.mockResolvedValue({ success: true })

    await expect(deleteChatStreamTalkFrequency('s1', '9:00')).rejects.toThrow('聊天流详情为空')
  })
})

describe('updateChatStreamLearning', () => {
  it('按 kind 拼接学习配置路径并提交开关状态', async () => {
    putMock.mockResolvedValue({ success: true, detail: detailStub })

    await expect(
      updateChatStreamLearning('s1', 'jargon', { use: true, learn: false })
    ).resolves.toBe(detailStub)
    expect(putMock).toHaveBeenCalledWith('/api/chat/sessions/s1/learning/jargon', {
      body: { use: true, learn: false },
      errorMessage: '保存学习配置失败',
    })
  })

  it('后端未返回 detail 时抛出「聊天流详情为空」', async () => {
    putMock.mockResolvedValue({ success: true })

    await expect(
      updateChatStreamLearning('s1', 'expression', { use: true, learn: true })
    ).rejects.toThrow('聊天流详情为空')
  })
})

describe('upsertChatStreamPrompt', () => {
  it('未提供 index 时新增 Prompt，query 为 undefined', async () => {
    putMock.mockResolvedValue({ success: true, detail: detailStub })

    await expect(upsertChatStreamPrompt('s1', { prompt: '多用颜文字' })).resolves.toBe(detailStub)
    expect(putMock).toHaveBeenCalledWith('/api/chat/sessions/s1/prompts', {
      body: { prompt: '多用颜文字' },
      query: undefined,
      errorMessage: '保存聊天 Prompt 失败',
    })
  })

  it('提供 index 时以 query 指定要更新的 Prompt', async () => {
    putMock.mockResolvedValue({ success: true, detail: detailStub })

    await upsertChatStreamPrompt('s1', { prompt: '简短一点' }, 2)

    expect(putMock).toHaveBeenCalledWith('/api/chat/sessions/s1/prompts', {
      body: { prompt: '简短一点' },
      query: { index: 2 },
      errorMessage: '保存聊天 Prompt 失败',
    })
  })

  it('后端未返回 detail 时抛出「聊天流详情为空」', async () => {
    putMock.mockResolvedValue({ success: true })

    await expect(upsertChatStreamPrompt('s1', { prompt: 'x' })).rejects.toThrow('聊天流详情为空')
  })
})

describe('deleteChatStreamPrompt', () => {
  it('按 index 拼接删除路径并返回最新详情', async () => {
    deleteMock.mockResolvedValue({ success: true, detail: detailStub })

    await expect(deleteChatStreamPrompt('s1', 3)).resolves.toBe(detailStub)
    expect(deleteMock).toHaveBeenCalledWith('/api/chat/sessions/s1/prompts/3', {
      errorMessage: '删除聊天 Prompt 失败',
    })
  })

  it('后端未返回 detail 时抛出「聊天流详情为空」', async () => {
    deleteMock.mockResolvedValue({ success: true })

    await expect(deleteChatStreamPrompt('s1', 0)).rejects.toThrow('聊天流详情为空')
  })
})

describe('updateChatStreamAdapterPolicy', () => {
  it('以 PUT 提交适配器放行规则并返回最新详情', async () => {
    const payload = { adapter_id: 'ad-1', action: 'allow' as const }
    putMock.mockResolvedValue({ success: true, detail: detailStub })

    await expect(updateChatStreamAdapterPolicy('s1', payload)).resolves.toBe(detailStub)
    expect(putMock).toHaveBeenCalledWith('/api/chat/sessions/s1/adapters/policy', {
      body: payload,
      errorMessage: '保存适配器放行规则失败',
    })
  })

  it('后端未返回 detail 时抛出「聊天流详情为空」', async () => {
    putMock.mockResolvedValue({ success: true })

    await expect(
      updateChatStreamAdapterPolicy('s1', { adapter_id: 'ad-1', action: 'inherit' })
    ).rejects.toThrow('聊天流详情为空')
  })
})

describe('deleteChatStream', () => {
  it('对 sessionId 做 URL 编码并原样返回删除结果', async () => {
    const result = { success: true, session_id: 's 1', deleted_total: 5, items: [] }
    deleteMock.mockResolvedValue(result)

    await expect(deleteChatStream('s 1')).resolves.toBe(result)
    expect(deleteMock).toHaveBeenCalledWith('/api/chat/sessions/s%201', {
      errorMessage: '删除聊天流失败',
    })
  })

  it('后端返回错误时向上抛出 ApiError', async () => {
    deleteMock.mockRejectedValue(new ApiError('删除聊天流失败', { status: 404 }))

    await expect(deleteChatStream('s1')).rejects.toMatchObject({ status: 404 })
  })
})
