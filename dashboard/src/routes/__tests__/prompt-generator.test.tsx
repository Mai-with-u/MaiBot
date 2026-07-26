/**
 * PromptGeneratorPage 特征化测试
 *
 * 人设生成器页：模型列表加载与默认选中 / 生成参数校验 / 生成成功渲染配置块 /
 * 注入单块与全部注入 / 保存-载入-删除人设（localStorage） / 复制与下载。
 * config-api 与 prompt-generator-api 全量打桩；react-query 由测试内的 QueryClient 真实驱动。
 */
import type { ReactNode } from 'react'
import type {
  PromptGeneratorApplyResponse,
  PromptGeneratorResponse,
} from '@/lib/prompt-generator-api'

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { PromptGeneratorPage } from '../prompt-generator'
import * as configApi from '@/lib/config-api'
import * as promptApi from '@/lib/prompt-generator-api'

const { toastMock } = vi.hoisted(() => ({ toastMock: vi.fn() }))

vi.mock('@/hooks/use-toast', () => ({ useToast: () => ({ toast: toastMock }) }))
vi.mock('@/lib/config-api', () => ({ getModelConfig: vi.fn() }))
vi.mock('@/lib/prompt-generator-api', () => ({
  applyPromptGeneratorBlocks: vi.fn(),
  generatePromptPersona: vi.fn(),
}))

const STORAGE_KEY = 'maibot_prompt_generator_saved_personas'

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
  window.localStorage.clear()
})

/** 构造一份合法的生成结果 */
function makeResponse(overrides: Partial<PromptGeneratorResponse> = {}): PromptGeneratorResponse {
  return {
    success: true,
    model_name: 'gpt-test',
    result: {
      personality: '温柔的助教',
      behavior_style: '克制',
      reply_style: '简短',
      multiple_reply_style: [],
      group_chat_prompt: '',
      private_chat_prompts: '',
      chat_prompts: [],
      notes: ['注意事项一'],
    },
    config_blocks: [
      {
        id: 'blk-1',
        section: 'personality',
        field: 'personality',
        title: '人格',
        description: '人格描述',
        value: 'x',
        toml: 'personality = "x"',
      },
      {
        id: 'blk-2',
        section: 'personality',
        field: 'reply_style',
        title: '表达方式',
        description: '',
        value: 'y',
        toml: 'reply_style = "y"',
      },
    ],
    toml_snippet: '[personality]\npersonality = "x"',
    raw_response: '原始输出内容',
    reasoning: '',
    prompt_tokens: 10,
    completion_tokens: 20,
    total_tokens: 30,
    ...overrides,
  }
}

function makeApplyResponse(sections: string[]): PromptGeneratorApplyResponse {
  return { success: true, message: '', applied_blocks: sections.length, sections }
}

function makeWrapper() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
}

function renderPage() {
  render(<PromptGeneratorPage />, { wrapper: makeWrapper() })
}

beforeEach(() => {
  vi.mocked(configApi.getModelConfig).mockResolvedValue({
    config: {
      models: [
        { name: 'gpt-test', model_identifier: 'gpt-4o', api_provider: 'openai', visual: true },
        { name: 'second-model', model_identifier: 'glm-4', api_provider: 'zhipu' },
        { model_identifier: '没有名字会被过滤' },
        'not-an-object',
      ],
    },
  })
  vi.mocked(promptApi.generatePromptPersona).mockResolvedValue(makeResponse())
  vi.mocked(promptApi.applyPromptGeneratorBlocks).mockResolvedValue(
    makeApplyResponse(['personality'])
  )
})

/** 等待模型加载完成（生成按钮从禁用变为可用） */
async function waitModelsReady() {
  await waitFor(() => {
    expect(screen.getByRole('button', { name: '生成' })).toBeEnabled()
  })
}

/** 填入人设文本并点击生成，等待结果 Tabs 出现 */
async function generatePersona(sourceText = '一个测试人设') {
  await waitModelsReady()
  fireEvent.change(screen.getByPlaceholderText(/可以粘贴角色卡/), {
    target: { value: sourceText },
  })
  fireEvent.click(screen.getByRole('button', { name: '生成' }))
  await waitFor(() => {
    expect(screen.getByRole('tab', { name: '配置块' })).toBeInTheDocument()
  })
}

describe('PromptGeneratorPage 模型加载', () => {
  it('加载成功后默认选中首个合法模型并展示提供商/标识/视觉徽标', async () => {
    renderPage()
    await waitModelsReady()

    // 首个模型 gpt-test 的派生徽标（未手动选择时回落到首个模型）
    expect(screen.getByText('openai')).toBeInTheDocument()
    expect(screen.getByText('gpt-4o')).toBeInTheDocument()
    expect(screen.getByText('视觉模型')).toBeInTheDocument()
  })

  it('模型列表为空时生成按钮禁用，显示选择模型占位符', async () => {
    vi.mocked(configApi.getModelConfig).mockResolvedValue({ config: { models: [] } })
    renderPage()

    await waitFor(() => {
      expect(screen.getByText('选择模型')).toBeInTheDocument()
    })
    expect(screen.getByRole('button', { name: '生成' })).toBeDisabled()
    // 无生成结果时展示空态
    expect(screen.getByText('等待生成')).toBeInTheDocument()
  })
})

describe('PromptGeneratorPage 生成参数校验', () => {
  it('未输入人设文本时点击生成给出错误 toast 且不发请求', async () => {
    renderPage()
    await waitModelsReady()

    fireEvent.click(screen.getByRole('button', { name: '生成' }))

    expect(toastMock).toHaveBeenCalledWith({
      title: '请输入要解析的人设或文段',
      variant: 'destructive',
    })
    expect(promptApi.generatePromptPersona).not.toHaveBeenCalled()
  })

  it('温度超出 0-2 或最大 Token 超出 256-8192 时拦截并提示', async () => {
    renderPage()
    await waitModelsReady()
    fireEvent.change(screen.getByPlaceholderText(/可以粘贴角色卡/), {
      target: { value: '人设文本' },
    })

    // 温度非法
    const temperatureInput = document.querySelector('input[inputmode="decimal"]')!
    fireEvent.change(temperatureInput, { target: { value: '5' } })
    fireEvent.click(screen.getByRole('button', { name: '生成' }))
    expect(toastMock).toHaveBeenCalledWith({
      title: '温度需要在 0-2 之间',
      variant: 'destructive',
    })

    // 温度改回合法后 maxTokens 非法
    fireEvent.change(temperatureInput, { target: { value: '0.5' } })
    const maxTokensInput = document.querySelector('input[inputmode="numeric"]')!
    fireEvent.change(maxTokensInput, { target: { value: '100' } })
    fireEvent.click(screen.getByRole('button', { name: '生成' }))
    expect(toastMock).toHaveBeenCalledWith({
      title: '最大输出 Token 需要在 256-8192 之间',
      variant: 'destructive',
    })

    expect(promptApi.generatePromptPersona).not.toHaveBeenCalled()
  })
})

describe('PromptGeneratorPage 生成成功', () => {
  it('携带整理后的参数发起生成，成功后渲染配置块与统计信息', async () => {
    renderPage()
    await generatePersona('  一个测试人设  ')

    // source_text 会被 trim
    expect(promptApi.generatePromptPersona).toHaveBeenCalledWith({
      model_name: 'gpt-test',
      source_text: '一个测试人设',
      target_scene: 'group',
      language: '简体中文',
      extra_requirements: '',
      temperature: 0.3,
      max_tokens: 1800,
    })
    expect(toastMock).toHaveBeenCalledWith({
      title: '人设解析完成',
      description: 'gpt-test · 30 tokens',
    })

    // 配置块视图：标题 + section.field 徽标
    expect(screen.getByText('人格')).toBeInTheDocument()
    expect(screen.getByText('表达方式')).toBeInTheDocument()
    expect(screen.getByText('personality.personality')).toBeInTheDocument()
    // 底部统计卡：tokens 与 notes
    expect(screen.getByText('总计 30 tokens')).toBeInTheDocument()
    expect(screen.getByText('注意事项一')).toBeInTheDocument()
  })
})

describe('PromptGeneratorPage 注入配置', () => {
  it('注入单块只提交该块，成功后按块标题提示', async () => {
    renderPage()
    await generatePersona()

    const injectButtons = screen.getAllByRole('button', { name: '注入此块' })
    fireEvent.click(injectButtons[0])

    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith({
        title: '人格已注入配置',
        description: '已更新 personality',
      })
    })
    expect(promptApi.applyPromptGeneratorBlocks).toHaveBeenCalledTimes(1)
    const blocks = vi.mocked(promptApi.applyPromptGeneratorBlocks).mock.calls[0][0]
    expect(blocks).toHaveLength(1)
    expect(blocks[0].id).toBe('blk-1')
  })

  it('全部注入提交所有配置块', async () => {
    renderPage()
    await generatePersona()

    fireEvent.click(screen.getByRole('button', { name: '全部注入' }))

    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith({
        title: '全部配置块已注入配置',
        description: '已更新 personality',
      })
    })
    const blocks = vi.mocked(promptApi.applyPromptGeneratorBlocks).mock.calls[0][0]
    expect(blocks.map((block) => block.id)).toEqual(['blk-1', 'blk-2'])
  })

  it('无生成结果时全部注入/保存/复制/下载按钮均禁用', async () => {
    renderPage()
    await waitModelsReady()

    expect(screen.getByRole('button', { name: '全部注入' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '保存人设' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '复制配置' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '下载' })).toBeDisabled()
  })
})

describe('PromptGeneratorPage 保存人设', () => {
  it('保存后写入 localStorage 并显示在已保存列表中', async () => {
    renderPage()
    await generatePersona()

    fireEvent.click(screen.getByRole('button', { name: '保存人设' }))

    expect(toastMock).toHaveBeenCalledWith({ title: '人设已保存', description: '一个测试人设' })
    const stored = JSON.parse(window.localStorage.getItem(STORAGE_KEY) ?? '[]') as Array<{
      title: string
      model_name: string
      target_scene: string
    }>
    expect(stored).toHaveLength(1)
    expect(stored[0].title).toBe('一个测试人设')
    expect(stored[0].model_name).toBe('gpt-test')
    expect(stored[0].target_scene).toBe('group')
    // 列表条目渲染标题（textarea 值也会形成同名文本节点，故取列表内的标题节点断言）
    const titleNodes = screen.getAllByText('一个测试人设')
    expect(titleNodes.some((node) => node.classList.contains('truncate'))).toBe(true)
  })

  it('载入已保存人设会回填输入与生成结果，删除后回到空态', async () => {
    // 预置一条合法记录和一条非法记录：非法记录应被过滤
    const validPersona = {
      id: 'p1',
      title: '已有人设',
      saved_at: '2026-01-01T00:00:00.000Z',
      model_name: 'second-model',
      source_text: '旧的文本',
      target_scene: 'private',
      language: '日本語',
      extra_requirements: '更简短',
      response: makeResponse({ model_name: 'second-model' }),
    }
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify([validPersona, { id: 'bad' }]))

    renderPage()
    await waitModelsReady()

    // 非法记录被过滤，仅剩合法记录
    expect(screen.getByText('已有人设')).toBeInTheDocument()
    expect(screen.queryByText('还没有保存的人设')).not.toBeInTheDocument()

    // 点击条目载入：输入回填 + 生成结果展示
    fireEvent.click(screen.getByText('已有人设'))
    expect(toastMock).toHaveBeenCalledWith({ title: '已载入保存的人设', description: '已有人设' })
    expect(screen.getByPlaceholderText(/可以粘贴角色卡/)).toHaveValue('旧的文本')
    expect(screen.getByDisplayValue('日本語')).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: '配置块' })).toBeInTheDocument()

    // 删除后列表回到空态且 localStorage 清空
    fireEvent.click(screen.getByTitle('删除保存的人设'))
    expect(toastMock).toHaveBeenCalledWith({ title: '已删除保存的人设', description: '已有人设' })
    expect(screen.getByText('还没有保存的人设')).toBeInTheDocument()
    expect(JSON.parse(window.localStorage.getItem(STORAGE_KEY) ?? '[]')).toEqual([])
  })
})

describe('PromptGeneratorPage 复制与下载', () => {
  it('复制配置把 toml 片段写入剪贴板并提示', async () => {
    const writeTextSpy = vi.spyOn(navigator.clipboard, 'writeText').mockResolvedValue()
    renderPage()
    await generatePersona()

    fireEvent.click(screen.getByRole('button', { name: '复制配置' }))

    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith({ title: '配置片段已复制' })
    })
    expect(writeTextSpy).toHaveBeenCalledWith('[personality]\npersonality = "x"')
  })

  it('下载按钮触发文件下载并提示文件名', async () => {
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    renderPage()
    await generatePersona()

    fireEvent.click(screen.getByRole('button', { name: '下载' }))

    expect(clickSpy).toHaveBeenCalledTimes(1)
    expect(toastMock).toHaveBeenCalledWith({
      title: '已生成下载文件',
      description: 'maibot-personality-prompt.toml',
    })
  })
})
