import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError, backendApi } from '@/lib/http'

import { applyPromptGeneratorBlocks, generatePromptPersona } from '../prompt-generator-api'
import type {
  PromptGeneratorConfigBlock,
  PromptGeneratorRequest,
  PromptGeneratorResponse,
} from '../prompt-generator-api'

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

const postMock = vi.mocked(backendApi.post)

/** 405 / method not allowed 归一化后的用户友好提示 */
const METHOD_NOT_ALLOWED_MESSAGE =
  '生成失败：可能是模型不支持或前后端版本不匹配，请换用文本聊天模型，或刷新并重启 WebUI 后再试。'

/** 构造一份完整的生成请求 */
function makeRequest(): PromptGeneratorRequest {
  return {
    model_name: 'replyer',
    source_text: '一只爱聊天的猫娘',
    target_scene: 'group',
    language: 'zh',
    extra_requirements: '',
    temperature: 0.7,
    max_tokens: 4096,
  }
}

/** 构造一份完整的生成响应 */
function makeResponse(): PromptGeneratorResponse {
  return {
    success: true,
    model_name: 'replyer',
    result: {
      personality: '活泼',
      behavior_style: '主动',
      reply_style: '简短',
      multiple_reply_style: ['简短', '幽默'],
      group_chat_prompt: '群聊提示',
      private_chat_prompts: '私聊提示',
      chat_prompts: [],
      notes: [],
    },
    config_blocks: [],
    toml_snippet: '[personality]',
    raw_response: '{}',
    reasoning: '',
    prompt_tokens: 100,
    completion_tokens: 200,
    total_tokens: 300,
  }
}

beforeEach(() => {
  postMock.mockReset()
})

describe('generatePromptPersona', () => {
  it('把请求参数作为请求体提交到生成接口并返回响应', async () => {
    const response = makeResponse()
    postMock.mockResolvedValue(response)
    const payload = makeRequest()

    await expect(generatePromptPersona(payload)).resolves.toBe(response)
    expect(postMock).toHaveBeenCalledWith('/api/webui/config/prompt-generator/generate', {
      body: payload,
      errorMessage: '生成人设 Prompt 失败',
    })
  })

  it('错误信息含 method not allowed 时归一化为友好提示', async () => {
    postMock.mockRejectedValue(new ApiError('Method Not Allowed', { status: 405 }))

    await expect(generatePromptPersona(makeRequest())).rejects.toThrowError(
      METHOD_NOT_ALLOWED_MESSAGE
    )
  })

  it('错误信息含 405 时同样归一化为友好提示', async () => {
    postMock.mockRejectedValue(new ApiError('请求失败: HTTP 405'))

    const error = await generatePromptPersona(makeRequest()).catch((e: unknown) => e)

    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).message).toBe(METHOD_NOT_ALLOWED_MESSAGE)
  })

  it('其他 ApiError 原样向上抛出，不做归一化', async () => {
    const original = new ApiError('生成人设 Prompt 失败', { status: 500 })
    postMock.mockRejectedValue(original)

    await expect(generatePromptPersona(makeRequest())).rejects.toBe(original)
  })

  it('非 ApiError 异常原样向上抛出', async () => {
    const original = new TypeError('Failed to fetch, method not allowed')
    postMock.mockRejectedValue(original)

    // 即使消息里出现 method not allowed，非 ApiError 也不应被归一化
    await expect(generatePromptPersona(makeRequest())).rejects.toBe(original)
  })
})

describe('applyPromptGeneratorBlocks', () => {
  it('把配置块列表包进 blocks 字段提交到应用接口', async () => {
    const blocks: PromptGeneratorConfigBlock[] = [
      {
        id: 'personality.personality',
        section: 'personality',
        field: 'personality',
        title: '人格',
        description: '核心人格描述',
        value: '活泼',
        toml: 'personality = "活泼"',
      },
    ]
    const response = {
      success: true,
      message: '已应用',
      applied_blocks: 1,
      sections: ['personality'],
    }
    postMock.mockResolvedValue(response)

    await expect(applyPromptGeneratorBlocks(blocks)).resolves.toBe(response)
    expect(postMock).toHaveBeenCalledWith('/api/webui/config/prompt-generator/apply', {
      body: { blocks },
      errorMessage: '应用生成结果失败',
    })
  })

  it('应用失败时向上抛出 ApiError', async () => {
    postMock.mockRejectedValue(new ApiError('应用生成结果失败', { status: 500 }))

    await expect(applyPromptGeneratorBlocks([])).rejects.toMatchObject({ status: 500 })
  })
})
