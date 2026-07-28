import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError, backendApi } from '@/lib/http'

import { getMCPStatus, testMCPConnection } from '../mcp-api'

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

describe('testMCPConnection', () => {
  it('把服务器配置原样作为请求体提交到测试接口', async () => {
    const response = {
      success: true,
      error: '',
      protocol_version: '2024-11-05',
      tools: [],
    }
    postMock.mockResolvedValue(response)

    await expect(testMCPConnection({ name: 'demo', transport: 'stdio' })).resolves.toBe(response)
    expect(postMock).toHaveBeenCalledWith('/api/webui/mcp/test', {
      body: { name: 'demo', transport: 'stdio' },
      errorMessage: '测试 MCP 连接失败',
    })
  })

  it('后端返回错误时向上抛出 ApiError', async () => {
    postMock.mockRejectedValue(new ApiError('测试 MCP 连接失败', { status: 500 }))

    await expect(testMCPConnection({})).rejects.toBeInstanceOf(ApiError)
    await expect(testMCPConnection({})).rejects.toMatchObject({ status: 500 })
  })
})

describe('getMCPStatus', () => {
  it('以 no-store 缓存模式读取 MCP 状态', async () => {
    const response = {
      initialized: true,
      server_count: 1,
      tool_count: 3,
      servers: [],
    }
    getMock.mockResolvedValue(response)

    await expect(getMCPStatus()).resolves.toBe(response)
    expect(getMock).toHaveBeenCalledWith('/api/webui/mcp/status', {
      cache: 'no-store',
      errorMessage: '获取 MCP 状态失败',
    })
  })

  it('后端 503 时向上抛出 ApiError', async () => {
    getMock.mockRejectedValue(new ApiError('获取 MCP 状态失败', { status: 503 }))

    await expect(getMCPStatus()).rejects.toMatchObject({ status: 503 })
  })
})
