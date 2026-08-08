import { beforeEach, describe, expect, it, vi } from 'vitest'

import { resolveApiPath } from '@/lib/api-base'
import { ApiError, backendApi } from '@/lib/http'

import {
  clearReasoningPromptStage,
  getReasoningPromptFile,
  getReasoningPromptHtmlUrl,
  listReasoningPromptFiles,
  listReasoningPromptStages,
  replayReasoningPrompt,
} from '../reasoning-process-api'
import type { ReasoningReplayRequest } from '../reasoning-process-api'

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

// getReasoningPromptHtmlUrl 依赖 resolveApiPath 解析 Electron/浏览器差异，测试中桩掉；
// 其余导出（如 getApiBaseUrl）被 @/lib/http 的实例模块引用，必须保留原实现
vi.mock('@/lib/api-base', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api-base')>()
  return {
    ...actual,
    resolveApiPath: vi.fn(),
  }
})

const getMock = vi.mocked(backendApi.get)
const postMock = vi.mocked(backendApi.post)
const deleteMock = vi.mocked(backendApi.delete)
const resolveApiPathMock = vi.mocked(resolveApiPath)

beforeEach(() => {
  getMock.mockReset()
  postMock.mockReset()
  deleteMock.mockReset()
  resolveApiPathMock.mockReset()
})

describe('listReasoningPromptFiles', () => {
  it('不传筛选参数时使用默认值，并以 no-store 缓存模式请求', async () => {
    const response = {
      items: [],
      total: 0,
      page: 1,
      page_size: 50,
      stages: [],
      stage_infos: [],
      sessions: [],
      session_infos: [],
      selected_session: 'auto',
    }
    getMock.mockResolvedValue(response)

    await expect(listReasoningPromptFiles({})).resolves.toBe(response)
    expect(getMock).toHaveBeenCalledWith('/api/webui/reasoning-process/files', {
      query: {
        stage: 'planner',
        session: 'auto',
        action: '',
        search: '',
        target_stem: '',
        page: 1,
        page_size: 50,
      },
      cache: 'no-store',
      errorMessage: '加载推理过程失败',
    })
  })

  it('显式筛选参数按后端命名（target_stem / page_size）映射到 query', async () => {
    getMock.mockResolvedValue({
      items: [],
      total: 0,
      page: 2,
      page_size: 10,
      stages: [],
      stage_infos: [],
      sessions: [],
      session_infos: [],
      selected_session: 'sess-1',
    })

    await listReasoningPromptFiles({
      stage: 'replyer',
      session: 'sess-1',
      action: 'reply',
      search: '晚饭',
      targetStem: 'stem-1',
      page: 2,
      pageSize: 10,
    })

    expect(getMock).toHaveBeenCalledWith('/api/webui/reasoning-process/files', {
      query: {
        stage: 'replyer',
        session: 'sess-1',
        action: 'reply',
        search: '晚饭',
        target_stem: 'stem-1',
        page: 2,
        page_size: 10,
      },
      cache: 'no-store',
      errorMessage: '加载推理过程失败',
    })
  })

  it('后端失败时向上抛出 ApiError', async () => {
    getMock.mockRejectedValue(new ApiError('加载推理过程失败', { status: 500 }))

    await expect(listReasoningPromptFiles({})).rejects.toBeInstanceOf(ApiError)
  })
})

describe('listReasoningPromptStages', () => {
  it('以 no-store 缓存模式读取阶段列表', async () => {
    const response = {
      stages: ['planner'],
      stage_infos: [{ name: 'planner', session_count: 1, latest_modified_at: 1700000000 }],
    }
    getMock.mockResolvedValue(response)

    await expect(listReasoningPromptStages()).resolves.toBe(response)
    expect(getMock).toHaveBeenCalledWith('/api/webui/reasoning-process/stages', {
      cache: 'no-store',
      errorMessage: '加载推理过程类型失败',
    })
  })
})

describe('clearReasoningPromptStage', () => {
  it('stage 名称经 encodeURIComponent 编码后拼入 DELETE 路径', async () => {
    const response = { stage: 'sub/stage', deleted_files: 3 }
    deleteMock.mockResolvedValue(response)

    await expect(clearReasoningPromptStage('sub/stage')).resolves.toBe(response)
    expect(deleteMock).toHaveBeenCalledWith('/api/webui/reasoning-process/stages/sub%2Fstage', {
      errorMessage: '清空推理过程失败',
    })
  })

  it('清空失败时向上抛出 ApiError', async () => {
    deleteMock.mockRejectedValue(new ApiError('清空推理过程失败', { status: 500 }))

    await expect(clearReasoningPromptStage('planner')).rejects.toMatchObject({ status: 500 })
  })
})

describe('getReasoningPromptFile', () => {
  it('文件路径作为 query 参数并以 no-store 缓存模式读取', async () => {
    const response = {
      path: '/data/planner/a.txt',
      content: '推理内容',
      size: 12,
      modified_at: 1700000000,
      model_name: 'demo-model',
      duration_ms: 88,
      message_avatars: {},
    }
    getMock.mockResolvedValue(response)

    await expect(getReasoningPromptFile('/data/planner/a.txt')).resolves.toBe(response)
    expect(getMock).toHaveBeenCalledWith('/api/webui/reasoning-process/file', {
      query: { path: '/data/planner/a.txt' },
      cache: 'no-store',
      errorMessage: '读取推理过程文件失败',
    })
  })

  it('文件不存在时向上抛出 ApiError', async () => {
    getMock.mockRejectedValue(new ApiError('读取推理过程文件失败', { status: 404 }))

    await expect(getReasoningPromptFile('/data/missing.txt')).rejects.toMatchObject({ status: 404 })
  })
})

describe('getReasoningPromptHtmlUrl', () => {
  it('文件路径编码进 html 端点 query 并交由 resolveApiPath 解析', async () => {
    resolveApiPathMock.mockResolvedValue(
      'http://backend:8000/api/webui/reasoning-process/html?path=%2Fdata%2Fa%20b.html'
    )

    await expect(getReasoningPromptHtmlUrl('/data/a b.html')).resolves.toBe(
      'http://backend:8000/api/webui/reasoning-process/html?path=%2Fdata%2Fa%20b.html'
    )
    expect(resolveApiPathMock).toHaveBeenCalledWith(
      '/api/webui/reasoning-process/html?path=%2Fdata%2Fa%20b.html'
    )
  })
})

describe('replayReasoningPrompt', () => {
  /** 构造一份最小可用的重放请求体 */
  function makeReplayRequest(): ReasoningReplayRequest {
    return {
      source_path: '/data/planner/a.json',
      stage: 'planner',
      model_name: 'demo-model',
      item_schema_version: 1,
      request_items: [
        {
          item_type: 'UserMessageItem',
          meta: {
            item_id: 'user-1',
            logical_turn_id: null,
            timestamp: '2026-08-05T00:00:00',
          },
          parts: [{ type: 'text', text: '你好' }],
        },
      ],
      temperature: 0.7,
      max_tokens: 1024,
    }
  }

  it('把重放请求体原样 POST 到 replay 端点', async () => {
    const payload = makeReplayRequest()
    const response = {
      success: true,
      output_items: [],
      model_name: 'demo-model',
      prompt_tokens: 10,
      completion_tokens: 5,
      total_tokens: 15,
      prompt_cache_hit_tokens: 0,
      prompt_cache_miss_tokens: 10,
      duration_ms: 300,
    }
    postMock.mockResolvedValue(response)

    await expect(replayReasoningPrompt(payload)).resolves.toBe(response)
    expect(postMock).toHaveBeenCalledWith('/api/webui/reasoning-process/replay', {
      body: payload,
      errorMessage: '重放推理请求失败',
    })
  })

  it('重放失败时向上抛出 ApiError', async () => {
    postMock.mockRejectedValue(new ApiError('重放推理请求失败', { status: 502 }))

    await expect(replayReasoningPrompt(makeReplayRequest())).rejects.toMatchObject({ status: 502 })
  })
})
