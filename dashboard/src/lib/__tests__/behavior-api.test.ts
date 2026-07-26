import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError, backendApi } from '@/lib/http'

import {
  debugBehaviorRetrieval,
  getBehaviorGraphData,
  getBehaviorPathDetail,
  listBehaviorChats,
  listBehaviorClusters,
  listBehaviorPaths,
} from '../behavior-api'
import type { BehaviorRetrievalDebugRequest } from '../behavior-api'

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

beforeEach(() => {
  getMock.mockReset()
  postMock.mockReset()
})

describe('listBehaviorChats', () => {
  it('从 chats 端点读取聊天列表并透传响应', async () => {
    const response = { success: true, data: [] }
    getMock.mockResolvedValue(response)

    await expect(listBehaviorChats()).resolves.toBe(response)
    expect(getMock).toHaveBeenCalledWith('/api/webui/behavior/chats')
  })

  it('后端失败时向上抛出 ApiError', async () => {
    getMock.mockRejectedValue(new ApiError('请求失败（HTTP 500）', { status: 500 }))

    await expect(listBehaviorChats()).rejects.toBeInstanceOf(ApiError)
  })
})

describe('listBehaviorPaths', () => {
  it('带完整筛选参数请求 paths 端点', async () => {
    const response = { success: true, total: 1, page: 2, page_size: 20, data: [] }
    getMock.mockResolvedValue(response)

    await expect(
      listBehaviorPaths({
        session_id: 'sess-1',
        search: '打招呼',
        enabled: 'true',
        actor_type: 'bot',
        learning_type: 'observed',
        sort_by: 'score',
        sort_order: 'desc',
        page: 2,
        page_size: 20,
      })
    ).resolves.toBe(response)

    expect(getMock).toHaveBeenCalledWith('/api/webui/behavior/paths', {
      query: {
        session_id: 'sess-1',
        search: '打招呼',
        enabled: 'true',
        actor_type: 'bot',
        learning_type: 'observed',
        sort_by: 'score',
        sort_order: 'desc',
        page: 2,
        page_size: 20,
      },
    })
  })

  it('空字符串筛选参数被转换为 undefined 跳过', async () => {
    getMock.mockResolvedValue({ success: true, total: 0, page: 1, page_size: 10, data: [] })

    await listBehaviorPaths({ session_id: '', search: '', enabled: '', page: 1, page_size: 10 })

    const [path, options] = getMock.mock.calls[0]
    expect(path).toBe('/api/webui/behavior/paths')
    expect(options?.query?.session_id).toBeUndefined()
    expect(options?.query?.search).toBeUndefined()
    expect(options?.query?.enabled).toBeUndefined()
    expect(options?.query?.actor_type).toBeUndefined()
    expect(options?.query?.page).toBe(1)
    expect(options?.query?.page_size).toBe(10)
  })

  it('后端失败时向上抛出 ApiError', async () => {
    getMock.mockRejectedValue(new ApiError('请求失败（HTTP 503）', { status: 503 }))

    await expect(listBehaviorPaths({})).rejects.toMatchObject({ status: 503 })
  })
})

describe('listBehaviorClusters', () => {
  it('带筛选参数请求 clusters 端点，空字符串参数被跳过', async () => {
    const response = { success: true, total: 3, page: 1, page_size: 50, data: [] }
    getMock.mockResolvedValue(response)

    await expect(
      listBehaviorClusters({
        session_id: 'sess-2',
        search: '',
        sort_by: 'score',
        page: 1,
        page_size: 50,
      })
    ).resolves.toBe(response)

    const [path, options] = getMock.mock.calls[0]
    expect(path).toBe('/api/webui/behavior/clusters')
    expect(options?.query?.session_id).toBe('sess-2')
    expect(options?.query?.search).toBeUndefined()
    expect(options?.query?.sort_by).toBe('score')
    expect(options?.query?.sort_order).toBeUndefined()
    expect(options?.query?.page).toBe(1)
    expect(options?.query?.page_size).toBe(50)
  })
})

describe('getBehaviorGraphData', () => {
  it('不传参数时默认不携带 session_id', async () => {
    const response = {
      success: true,
      data: {
        scene_cluster_network: { nodes: [], edges: [] },
        tag_network: { nodes: [], edges: [] },
      },
    }
    getMock.mockResolvedValue(response)

    await expect(getBehaviorGraphData()).resolves.toBe(response)

    const [path, options] = getMock.mock.calls[0]
    expect(path).toBe('/api/webui/behavior/graph-data')
    expect(options?.query?.session_id).toBeUndefined()
  })

  it('指定 session_id 时作为 query 参数传递', async () => {
    getMock.mockResolvedValue({
      success: true,
      data: {
        scene_cluster_network: { nodes: [], edges: [] },
        tag_network: { nodes: [], edges: [] },
      },
    })

    await getBehaviorGraphData({ session_id: 'sess-graph' })

    expect(getMock).toHaveBeenCalledWith('/api/webui/behavior/graph-data', {
      query: { session_id: 'sess-graph' },
    })
  })
})

describe('getBehaviorPathDetail', () => {
  it('pathId 拼接到路径中请求详情', async () => {
    const response = {
      success: true,
      data: {
        path: {} as never,
        scene_cluster: { id: 1, name: '闲聊', tags: [], source_count: 2, update_time: null },
        evidence: [],
        feedback: [],
        nodes: [],
        edges: [],
      },
    }
    getMock.mockResolvedValue(response)

    await expect(getBehaviorPathDetail(42)).resolves.toBe(response)
    expect(getMock).toHaveBeenCalledWith('/api/webui/behavior/paths/42')
  })

  it('路径不存在时向上抛出 ApiError', async () => {
    getMock.mockRejectedValue(new ApiError('请求失败（HTTP 404）', { status: 404 }))

    await expect(getBehaviorPathDetail(999)).rejects.toMatchObject({ status: 404 })
  })
})

describe('debugBehaviorRetrieval', () => {
  /** 构造一份最小可用的检索调试请求体 */
  function makeDebugRequest(): BehaviorRetrievalDebugRequest {
    return {
      session_id: 'sess-debug',
      include_global: true,
      retrieval_mode: 'direct',
      scene_text: '群里在聊晚饭吃什么',
      tag_clusters: [{ tag_name: '聊天', tag_aliases: ['闲聊'] }],
      need: { tag_name: '回应', tag_aliases: [] },
      other_traits: [],
      max_count: 5,
    }
  }

  it('把调试请求体原样 POST 到 retrieval-debug 端点', async () => {
    const payload = makeDebugRequest()
    const response = {
      success: true,
      data: {
        retrieval_mode: 'direct',
        descriptors: [],
        matched_clusters: [],
        candidate_scores: [],
        candidates: [],
        retrieval_debug: {},
      },
    }
    postMock.mockResolvedValue(response)

    await expect(debugBehaviorRetrieval(payload)).resolves.toBe(response)
    expect(postMock).toHaveBeenCalledWith('/api/webui/behavior/retrieval-debug', {
      body: payload,
    })
  })

  it('后端失败时向上抛出 ApiError', async () => {
    postMock.mockRejectedValue(new ApiError('请求失败（HTTP 500）', { status: 500 }))

    await expect(debugBehaviorRetrieval(makeDebugRequest())).rejects.toBeInstanceOf(ApiError)
  })
})
