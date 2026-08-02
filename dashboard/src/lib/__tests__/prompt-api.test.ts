import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError, backendApi } from '@/lib/http'

import {
  activatePromptVersion,
  getDefaultPromptFile,
  getPromptCatalog,
  getPromptFile,
  getPromptVersionFile,
  resetPromptFile,
  updatePromptFile,
} from '../prompt-api'
import type { PromptCatalog, PromptFileContent } from '../prompt-api'

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

/** 构造一个最小可用的 PromptFileContent 响应 */
function makeFileContent(overrides: Partial<PromptFileContent> = {}): PromptFileContent {
  return {
    success: true,
    language: 'zh',
    filename: 'planner.txt',
    content: '模板内容',
    customized: false,
    active_version_id: null,
    versions: [],
    validation: {
      valid: true,
      missing_placeholders: [],
      extra_placeholders: [],
      message: '',
    },
    ...overrides,
  }
}

beforeEach(() => {
  getMock.mockReset()
  postMock.mockReset()
  putMock.mockReset()
  deleteMock.mockReset()
})

describe('getPromptCatalog', () => {
  it('请求 Prompt 目录并原样返回响应', async () => {
    const catalog: PromptCatalog = {
      success: true,
      languages: ['zh', 'en'],
      files: { zh: [] },
    }
    getMock.mockResolvedValue(catalog)

    await expect(getPromptCatalog()).resolves.toBe(catalog)
    expect(getMock).toHaveBeenCalledWith('/api/webui/config/prompts', {
      errorMessage: '获取 Prompt 文件列表失败',
    })
  })

  it('后端失败时向上抛出 ApiError', async () => {
    getMock.mockRejectedValue(new ApiError('获取 Prompt 文件列表失败', { status: 500 }))

    await expect(getPromptCatalog()).rejects.toMatchObject({ status: 500 })
  })
})

describe('getPromptFile', () => {
  it('对语言与文件名做 URL 编码后请求文件内容', async () => {
    const content = makeFileContent()
    getMock.mockResolvedValue(content)

    await expect(getPromptFile('zh', 'group chat.txt')).resolves.toBe(content)
    expect(getMock).toHaveBeenCalledWith('/api/webui/config/prompts/zh/group%20chat.txt', {
      errorMessage: '获取 Prompt 文件失败',
    })
  })
})

describe('getDefaultPromptFile', () => {
  it('请求 default 端点获取默认文件内容', async () => {
    const content = makeFileContent()
    getMock.mockResolvedValue(content)

    await expect(getDefaultPromptFile('zh', 'planner.txt')).resolves.toBe(content)
    expect(getMock).toHaveBeenCalledWith('/api/webui/config/prompts/zh/planner.txt/default', {
      errorMessage: '获取默认 Prompt 文件失败',
    })
  })
})

describe('updatePromptFile', () => {
  it('未传 options 时使用默认值：label 为空串、不创建版本', async () => {
    putMock.mockResolvedValue(makeFileContent())

    await updatePromptFile('zh', 'planner.txt', '新内容')

    expect(putMock).toHaveBeenCalledTimes(1)
    const [path, options] = putMock.mock.calls[0]
    expect(path).toBe('/api/webui/config/prompts/zh/planner.txt')
    expect(options?.errorMessage).toBe('保存 Prompt 文件失败')
    const body = options?.body as Record<string, unknown>
    expect(body.content).toBe('新内容')
    expect(body.version_id).toBeUndefined()
    expect(body.label).toBe('')
    expect(body.create_version).toBe(false)
  })

  it('携带完整 options 时透传版本参数', async () => {
    const content = makeFileContent({ active_version_id: 'v1' })
    putMock.mockResolvedValue(content)

    await expect(
      updatePromptFile('en', 'reply.txt', 'content', {
        versionId: 'v1',
        label: '备份',
        createVersion: true,
      })
    ).resolves.toBe(content)
    expect(putMock).toHaveBeenCalledWith('/api/webui/config/prompts/en/reply.txt', {
      body: {
        content: 'content',
        version_id: 'v1',
        label: '备份',
        create_version: true,
      },
      errorMessage: '保存 Prompt 文件失败',
    })
  })

  it('保存失败时向上抛出 ApiError', async () => {
    putMock.mockRejectedValue(new ApiError('保存 Prompt 文件失败', { status: 422 }))

    await expect(updatePromptFile('zh', 'planner.txt', 'x')).rejects.toBeInstanceOf(ApiError)
  })
})

describe('resetPromptFile', () => {
  it('以 DELETE 重置文件为默认内容', async () => {
    const content = makeFileContent({ customized: false })
    deleteMock.mockResolvedValue(content)

    await expect(resetPromptFile('zh', 'planner.txt')).resolves.toBe(content)
    expect(deleteMock).toHaveBeenCalledWith('/api/webui/config/prompts/zh/planner.txt', {
      errorMessage: '重置 Prompt 文件失败',
    })
  })
})

describe('getPromptVersionFile', () => {
  it('对版本 ID 做 URL 编码后请求版本内容', async () => {
    const content = makeFileContent()
    getMock.mockResolvedValue(content)

    await expect(getPromptVersionFile('zh', 'planner.txt', 'v/1')).resolves.toBe(content)
    expect(getMock).toHaveBeenCalledWith(
      '/api/webui/config/prompts/zh/planner.txt/versions/v%2F1',
      {
        errorMessage: '获取 Prompt 版本失败',
      }
    )
  })
})

describe('activatePromptVersion', () => {
  it('以 POST 请求 activate 端点启用版本', async () => {
    const content = makeFileContent({ active_version_id: 'v2' })
    postMock.mockResolvedValue(content)

    await expect(activatePromptVersion('zh', 'planner.txt', 'v2')).resolves.toBe(content)
    expect(postMock).toHaveBeenCalledWith(
      '/api/webui/config/prompts/zh/planner.txt/versions/v2/activate',
      {
        errorMessage: '启用 Prompt 版本失败',
      }
    )
  })

  it('启用失败时向上抛出 ApiError', async () => {
    postMock.mockRejectedValue(new ApiError('启用 Prompt 版本失败', { status: 404 }))

    await expect(activatePromptVersion('zh', 'planner.txt', 'missing')).rejects.toMatchObject({
      status: 404,
    })
  })
})
