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

import userEvent from '@testing-library/user-event'

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

// Radix Select 在 jsdom 下需要 PointerCapture
if (!Element.prototype.hasPointerCapture) {
  Element.prototype.hasPointerCapture = () => false
}
if (!Element.prototype.setPointerCapture) {
  Element.prototype.setPointerCapture = () => {}
}
if (!Element.prototype.releasePointerCapture) {
  Element.prototype.releasePointerCapture = () => {}
}

function createDeferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, reject, resolve }
}

function makeSavedPersona(overrides: Record<string, unknown> = {}) {
  return {
    extra_requirements: '',
    id: 'p1',
    language: '简体中文',
    model_name: 'gpt-test',
    response: makeResponse(),
    saved_at: '2026-01-01T00:00:00.000Z',
    source_text: '旧的文本',
    target_scene: 'group',
    title: '已有人设',
    ...overrides,
  }
}

/** 直接调用 React onClick，绕过 disabled 按钮不派发 click 的限制 */
function invokeButtonClick(name: string) {
  const button = screen.getByRole('button', { name })
  const propsKey = Object.keys(button).find((key) => key.startsWith('__reactProps$'))
  if (!propsKey) {
    throw new Error(`无法读取按钮「${name}」的 React props`)
  }
  const props = (button as unknown as Record<string, { onClick: (event: MouseEvent) => void }>)[
    propsKey
  ]
  props.onClick(new MouseEvent('click', { bubbles: true, cancelable: true }))
}

describe('PromptGeneratorPage 模型刷新与缺省字段', () => {
  it('加载中展示占位并禁用生成/刷新，刷新会再次拉取模型', async () => {
    const modelsDeferred = createDeferred<Record<string, unknown>>()
    vi.mocked(configApi.getModelConfig).mockReturnValue(modelsDeferred.promise)
    renderPage()

    expect(await screen.findByText('加载模型中...')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '生成' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '刷新模型' })).toBeDisabled()

    modelsDeferred.resolve({
      config: {
        models: [{ name: 'gpt-test', model_identifier: 'gpt-4o', api_provider: 'openai' }],
      },
    })
    await waitModelsReady()

    fireEvent.click(screen.getByRole('button', { name: '刷新模型' }))
    await waitFor(() => {
      expect(configApi.getModelConfig).toHaveBeenCalledTimes(2)
    })
  })

  it('配置未包一层 config 且缺提供商/标识时回落到未指定提供商与模型名', async () => {
    vi.mocked(configApi.getModelConfig).mockResolvedValue({
      models: [{ name: 'bare-model' }],
    })
    renderPage()
    await waitModelsReady()

    expect(screen.getByText('未指定提供商')).toBeInTheDocument()
    expect(screen.getAllByText('bare-model').length).toBeGreaterThan(0)
    expect(screen.queryByText('视觉模型')).not.toBeInTheDocument()
  })

  it('models 不是数组时与空列表一样禁用生成', async () => {
    vi.mocked(configApi.getModelConfig).mockResolvedValue({ config: { models: { name: 'x' } } })
    renderPage()

    await waitFor(() => {
      expect(screen.getByText('选择模型')).toBeInTheDocument()
    })
    expect(screen.getByRole('button', { name: '生成' })).toBeDisabled()
  })
})

describe('PromptGeneratorPage 输入联动生成', () => {
  it('切换模型/场景/语言/额外要求后按整理后的参数生成', async () => {
    const user = userEvent.setup()
    renderPage()
    await waitModelsReady()

    const [modelSelect, sceneSelect] = screen.getAllByRole('combobox')
    await user.click(modelSelect)
    await user.click(await screen.findByRole('option', { name: 'second-model' }))
    expect(screen.getByText('zhipu')).toBeInTheDocument()
    expect(screen.getByText('glm-4')).toBeInTheDocument()
    expect(screen.queryByText('视觉模型')).not.toBeInTheDocument()

    await user.click(sceneSelect)
    await user.click(await screen.findByRole('option', { name: '群聊 + 私聊' }))

    fireEvent.change(screen.getByDisplayValue('简体中文'), { target: { value: 'English' } })
    fireEvent.change(screen.getByPlaceholderText(/例如：更短/), {
      target: { value: '  保持克制  ' },
    })
    fireEvent.change(screen.getByPlaceholderText(/可以粘贴角色卡/), {
      target: { value: '人设文本' },
    })
    fireEvent.change(document.querySelector('input[inputmode="decimal"]')!, {
      target: { value: '0.7' },
    })
    fireEvent.change(document.querySelector('input[inputmode="numeric"]')!, {
      target: { value: '2048' },
    })
    fireEvent.click(screen.getByRole('button', { name: '生成' }))

    await waitFor(() => {
      expect(promptApi.generatePromptPersona).toHaveBeenCalledWith({
        model_name: 'second-model',
        source_text: '人设文本',
        target_scene: 'both',
        language: 'English',
        extra_requirements: '保持克制',
        temperature: 0.7,
        max_tokens: 2048,
      })
    })
  })

  it('温度或 Token 非数字、温度为负、Token 过大时拦截', async () => {
    renderPage()
    await waitModelsReady()
    fireEvent.change(screen.getByPlaceholderText(/可以粘贴角色卡/), {
      target: { value: '人设文本' },
    })

    const temperatureInput = document.querySelector('input[inputmode="decimal"]')!
    const maxTokensInput = document.querySelector('input[inputmode="numeric"]')!

    fireEvent.change(temperatureInput, { target: { value: 'abc' } })
    fireEvent.click(screen.getByRole('button', { name: '生成' }))
    expect(toastMock).toHaveBeenCalledWith({
      title: '温度需要在 0-2 之间',
      variant: 'destructive',
    })

    fireEvent.change(temperatureInput, { target: { value: '-0.1' } })
    fireEvent.click(screen.getByRole('button', { name: '生成' }))
    expect(toastMock).toHaveBeenCalledWith({
      title: '温度需要在 0-2 之间',
      variant: 'destructive',
    })

    fireEvent.change(temperatureInput, { target: { value: '0.3' } })
    fireEvent.change(maxTokensInput, { target: { value: 'abc' } })
    fireEvent.click(screen.getByRole('button', { name: '生成' }))
    expect(toastMock).toHaveBeenCalledWith({
      title: '最大输出 Token 需要在 256-8192 之间',
      variant: 'destructive',
    })

    fireEvent.change(maxTokensInput, { target: { value: '9000' } })
    fireEvent.click(screen.getByRole('button', { name: '生成' }))
    expect(toastMock).toHaveBeenCalledWith({
      title: '最大输出 Token 需要在 256-8192 之间',
      variant: 'destructive',
    })
    expect(promptApi.generatePromptPersona).not.toHaveBeenCalled()
  })

  it('无模型时仍点击生成会提示请选择生成模型', async () => {
    vi.mocked(configApi.getModelConfig).mockResolvedValue({ config: { models: [] } })
    renderPage()
    await waitFor(() => {
      expect(screen.getByText('选择模型')).toBeInTheDocument()
    })

    invokeButtonClick('生成')
    expect(toastMock).toHaveBeenCalledWith({
      title: '请选择生成模型',
      variant: 'destructive',
    })
    expect(promptApi.generatePromptPersona).not.toHaveBeenCalled()
  })
})

describe('PromptGeneratorPage 生成与注入进行中', () => {
  it('生成过程中按钮变为生成中，完成后恢复', async () => {
    const generateDeferred = createDeferred<PromptGeneratorResponse>()
    vi.mocked(promptApi.generatePromptPersona).mockReturnValue(generateDeferred.promise)
    renderPage()
    await waitModelsReady()
    fireEvent.change(screen.getByPlaceholderText(/可以粘贴角色卡/), {
      target: { value: '一个测试人设' },
    })
    fireEvent.click(screen.getByRole('button', { name: '生成' }))

    await waitFor(() => {
      expect(screen.getByRole('button', { name: '生成中' })).toBeDisabled()
    })

    generateDeferred.resolve(makeResponse())
    await waitFor(() => {
      expect(screen.getByRole('tab', { name: '配置块' })).toBeInTheDocument()
    })
    expect(screen.getByRole('button', { name: '生成' })).toBeEnabled()
  })

  it('单块注入进行中禁用注入按钮并在完成后按空 sections 省略描述', async () => {
    const applyDeferred = createDeferred<PromptGeneratorApplyResponse>()
    vi.mocked(promptApi.applyPromptGeneratorBlocks).mockReturnValue(applyDeferred.promise)
    renderPage()
    await generatePersona()

    fireEvent.click(screen.getAllByRole('button', { name: '注入此块' })[0])
    await waitFor(() => {
      expect(screen.getAllByRole('button', { name: '注入此块' })[0]).toBeDisabled()
      expect(screen.getByRole('button', { name: '全部注入' })).toBeDisabled()
    })

    applyDeferred.resolve({ success: true, message: '', applied_blocks: 1, sections: [] })
    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith({
        title: '人格已注入配置',
        description: undefined,
      })
    })
  })

  it('全部注入进行中展示旋转图标，多 section 用顿号拼接', async () => {
    const applyDeferred = createDeferred<PromptGeneratorApplyResponse>()
    vi.mocked(promptApi.applyPromptGeneratorBlocks).mockReturnValue(applyDeferred.promise)
    renderPage()
    await generatePersona()

    fireEvent.click(screen.getByRole('button', { name: '全部注入' }))
    await waitFor(() => {
      expect(
        screen.getByRole('button', { name: '全部注入' }).querySelector('.animate-spin')
      ).not.toBeNull()
    })

    applyDeferred.resolve(makeApplyResponse(['personality', 'expression']))
    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith({
        title: '全部配置块已注入配置',
        description: '已更新 personality、expression',
      })
    })
  })

  it('生成结果没有配置块时全部注入提示没有可注入的配置块', async () => {
    vi.mocked(promptApi.generatePromptPersona).mockResolvedValue(makeResponse({ config_blocks: [] }))
    renderPage()
    await generatePersona()

    fireEvent.click(screen.getByRole('button', { name: '全部注入' }))
    expect(toastMock).toHaveBeenCalledWith({
      title: '没有可注入的配置块',
      variant: 'destructive',
    })
    expect(promptApi.applyPromptGeneratorBlocks).not.toHaveBeenCalled()
  })
})

describe('PromptGeneratorPage 结果 Tabs 与复制失败', () => {
  it('切换到配置片段和带 reasoning 的原始输出，token 为 0 时按 0 展示', async () => {
    vi.mocked(promptApi.generatePromptPersona).mockResolvedValue(
      makeResponse({
        completion_tokens: 0,
        prompt_tokens: 0,
        raw_response: '原始输出内容',
        reasoning: '逐步思考',
        result: {
          behavior_style: '克制',
          chat_prompts: [],
          group_chat_prompt: '',
          multiple_reply_style: [],
          notes: [],
          personality: '温柔的助教',
          private_chat_prompts: '',
          reply_style: '简短',
        },
        total_tokens: 0,
      })
    )
    renderPage()
    await generatePersona()

    expect(toastMock).toHaveBeenCalledWith({
      title: '人设解析完成',
      description: 'gpt-test · 0 tokens',
    })
    expect(screen.getByText('输入 0')).toBeInTheDocument()
    expect(screen.getByText('输出 0')).toBeInTheDocument()
    expect(screen.getByText('总计 0 tokens')).toBeInTheDocument()

    const textareaValues = () =>
      Array.from(document.querySelectorAll('textarea')).map((element) => element.value)

    // Radix Tabs 在 pointerdown/mousedown 时切换；内容可能因动画被当成 hidden
    const tomlTab = screen.getByRole('tab', { name: '配置片段' })
    fireEvent.pointerDown(tomlTab, { button: 0 })
    fireEvent.mouseDown(tomlTab, { button: 0 })
    expect(tomlTab).toHaveAttribute('data-state', 'active')
    await waitFor(() => {
      expect(textareaValues()).toContain('[personality]\npersonality = "x"')
    })

    const rawTab = screen.getByRole('tab', { name: '原始输出' })
    fireEvent.pointerDown(rawTab, { button: 0 })
    fireEvent.mouseDown(rawTab, { button: 0 })
    expect(rawTab).toHaveAttribute('data-state', 'active')
    await waitFor(() => {
      expect(textareaValues().some((value) => value.includes('# reasoning\n逐步思考'))).toBe(true)
    })
  })

  it('配置块复制成功按块标题提示', async () => {
    const writeTextSpy = vi.spyOn(navigator.clipboard, 'writeText').mockResolvedValue()
    renderPage()
    await generatePersona()

    fireEvent.click(screen.getAllByRole('button', { name: '复制' })[0])
    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith({ title: '人格已复制' })
    })
    expect(writeTextSpy).toHaveBeenCalledWith('personality = "x"')
  })

  it('剪贴板写入失败时提示复制失败', async () => {
    vi.spyOn(navigator.clipboard, 'writeText').mockRejectedValue(new Error('denied'))
    renderPage()
    await generatePersona()

    fireEvent.click(screen.getByRole('button', { name: '复制配置' }))
    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith({
        title: '复制失败',
        description: 'denied',
        variant: 'destructive',
      })
    })
  })
})

describe('PromptGeneratorPage 保存标题与 localStorage 异常', () => {
  it('无生成结果时点击保存提示没有可保存的人设', async () => {
    renderPage()
    await waitModelsReady()
    invokeButtonClick('保存人设')
    expect(toastMock).toHaveBeenCalledWith({
      title: '没有可保存的人设',
      variant: 'destructive',
    })
  })

  it('超长原文截断标题，清空原文后回落到人格或模型名', async () => {
    renderPage()
    await generatePersona('超长标题'.repeat(10))
    fireEvent.click(screen.getByRole('button', { name: '保存人设' }))
    expect(toastMock).toHaveBeenCalledWith({
      title: '人设已保存',
      description: `${'超长标题'.repeat(7)}...`,
    })

    fireEvent.change(screen.getByPlaceholderText(/可以粘贴角色卡/), { target: { value: '   ' } })
    fireEvent.click(screen.getByRole('button', { name: '保存人设' }))
    expect(toastMock).toHaveBeenCalledWith({
      title: '人设已保存',
      description: '温柔的助教',
    })

    vi.mocked(promptApi.generatePromptPersona).mockClear()
    vi.mocked(promptApi.generatePromptPersona).mockResolvedValue(
      makeResponse({
        result: {
          behavior_style: '',
          chat_prompts: [],
          group_chat_prompt: '',
          multiple_reply_style: [],
          notes: [],
          personality: '   ',
          private_chat_prompts: '',
          reply_style: '',
        },
      })
    )
    fireEvent.change(screen.getByPlaceholderText(/可以粘贴角色卡/), {
      target: { value: '再次生成' },
    })
    fireEvent.click(screen.getByRole('button', { name: '生成' }))
    await waitFor(() => {
      expect(promptApi.generatePromptPersona).toHaveBeenCalledTimes(1)
    })
    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith({
        title: '人设解析完成',
        description: 'gpt-test · 30 tokens',
      })
    })
    fireEvent.change(screen.getByPlaceholderText(/可以粘贴角色卡/), { target: { value: '' } })
    fireEvent.click(screen.getByRole('button', { name: '保存人设' }))
    expect(toastMock).toHaveBeenCalledWith({
      title: '人设已保存',
      description: 'gpt-test 生成结果',
    })
  })

  it('保存写入 localStorage 失败时提示保存人设失败', async () => {
    renderPage()
    await generatePersona()
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('quota exceeded')
    })

    fireEvent.click(screen.getByRole('button', { name: '保存人设' }))
    expect(toastMock).toHaveBeenCalledWith({
      title: '保存人设失败',
      description: 'quota exceeded',
      variant: 'destructive',
    })
  })

  it('删除写入 localStorage 失败时提示删除人设失败', async () => {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify([makeSavedPersona()]))
    renderPage()
    await waitModelsReady()
    expect(screen.getByText('已有人设')).toBeInTheDocument()

    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('disk full')
    })
    fireEvent.click(screen.getByTitle('删除保存的人设'))
    expect(toastMock).toHaveBeenCalledWith({
      title: '删除人设失败',
      description: 'disk full',
      variant: 'destructive',
    })
  })

  it('localStorage 非法 JSON 时警告并展示空列表', async () => {
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})
    window.localStorage.setItem(STORAGE_KEY, '{not-json')
    renderPage()
    await waitModelsReady()

    expect(screen.getByText('还没有保存的人设')).toBeInTheDocument()
    expect(warnSpy).toHaveBeenCalledWith('读取已保存人设失败:', expect.any(SyntaxError))
  })

  it('localStorage 非数组 JSON 视为空列表', async () => {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify({ foo: 1 }))
    renderPage()
    await waitModelsReady()
    expect(screen.getByText('还没有保存的人设')).toBeInTheDocument()
  })

  it('过滤非对象与非法 response，保留 both 场景记录', async () => {
    window.localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify([
        null,
        'str',
        1,
        makeSavedPersona({ id: 'bad-response', response: null, title: '非法响应' }),
        makeSavedPersona({
          id: 'bad-date',
          saved_at: 'not-a-date',
          title: '非法日期',
        }),
        makeSavedPersona({
          id: 'bad-scene',
          target_scene: 'other',
          title: '非法场景',
        }),
        makeSavedPersona({
          extra_requirements: 1,
          id: 'bad-extra',
          title: '非法额外要求',
        }),
        makeSavedPersona({
          id: 'both-ok',
          target_scene: 'both',
          title: '双场景人设',
        }),
      ])
    )
    renderPage()
    await waitModelsReady()

    expect(screen.getByText('双场景人设')).toBeInTheDocument()
    expect(screen.queryByText('非法响应')).not.toBeInTheDocument()
    expect(screen.queryByText('非法日期')).not.toBeInTheDocument()
    expect(screen.queryByText('非法场景')).not.toBeInTheDocument()
    expect(screen.queryByText('非法额外要求')).not.toBeInTheDocument()
  })
})
