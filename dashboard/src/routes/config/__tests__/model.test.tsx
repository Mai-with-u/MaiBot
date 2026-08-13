import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ModelConfigPage } from '../model'
import * as configApi from '@/lib/config-api'
import * as configSearchNavigation from '@/lib/config-search-navigation'

const toastMock = vi.fn()
const routeState = vi.hoisted(() => ({ searchStr: '' }))

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
  localStorage.removeItem('model-assignment-tour-entry-dismissed')
})

vi.mock('@/hooks/use-toast', () => ({ useToast: () => ({ toast: toastMock }) }))
vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => vi.fn(),
  useRouterState: ({
    select,
  }: {
    select: (state: { location: { searchStr: string } }) => string
  }) => select({ location: { searchStr: routeState.searchStr } }),
}))
vi.mock('@/lib/restart-context', () => ({
  RestartProvider: ({ children }: { children: React.ReactNode }) => children,
  useRestart: () => ({ isRestarting: false, triggerRestart: vi.fn() }),
}))
vi.mock('@/components/restart-overlay', () => ({ RestartOverlay: () => null }))

// 仅 stub useModelTour（页面只取 startTour/isRunning），保留 useModelAutoSave/useModelFetcher 真实
vi.mock('../model/hooks', async (importActual) => {
  const actual = await importActual<typeof import('../model/hooks')>()
  return { ...actual, useModelTour: () => ({ startTour: vi.fn(), isRunning: false, stepIndex: 0 }) }
})

vi.mock('@/lib/config-api', () => ({
  createModelConfigVersion: vi.fn(),
  deleteModelConfigVersion: vi.fn(),
  getModelConfigCached: vi.fn(),
  getModelConfig: vi.fn(),
  getModelConfigSchema: vi.fn(),
  getModelConfigVersions: vi.fn(),
  switchModelConfigVersion: vi.fn(),
  updateModelConfig: vi.fn(),
  updateModelConfigSection: vi.fn(),
  testProviderConnection: vi.fn(),
  testModelCapability: vi.fn(),
  fetchProviderModels: vi.fn(),
  fetchModelClientTypes: vi.fn(),
}))

// 真实表格/卡片用于覆盖响应式双视图；任务卡片仍桩以便稳定触发 embedding 警告
vi.mock('../model/components', async (importActual) => {
  const actual = await importActual<typeof import('../model/components')>()
  return {
    ...actual,
    TaskConfigCard: ({
      taskConfig,
      onChange,
      hideTemperature,
      hideMaxTokens,
    }: {
      taskConfig: { model_list?: string[] }
      onChange: (f: string, v: string[]) => void
      hideTemperature?: boolean
      hideMaxTokens?: boolean
    }) => (
      <div data-testid="task-config-card">
        <span data-testid="task-models">{JSON.stringify(taskConfig.model_list ?? [])}</span>
        {hideTemperature ? <span>温度已隐藏</span> : null}
        {hideMaxTokens ? <span>最大 Token 已隐藏</span> : null}
        <button type="button" onClick={() => onChange('model_list', ['new-embed-model'])}>
          change-embedding
        </button>
      </div>
    ),
  }
})

vi.mock('../modelProvider/ProviderForm', () => ({
  ProviderForm: () => <div data-testid="provider-form" />,
}))

function baseConfig() {
  return {
    models: [{ name: 'gpt-4', model_identifier: 'gpt-4', api_provider: 'openai' }],
    api_providers: [
      {
        name: 'openai',
        base_url: 'https://api.openai.com/v1',
        api_key: 'sk-x',
        client_type: 'openai',
      },
    ],
    model_task_config: {
      replyer: { model_list: ['gpt-4'] },
      embedding: { model_list: ['old-embed-model'] },
    },
  }
}

function baseSchema() {
  return {
    schema: {
      nested: {
        model_task_config: {
          fields: [{ name: 'embedding', type: 'object', advanced: false, description: '嵌入模型' }],
        },
      },
    },
  }
}

function schemaWithTasks() {
  return {
    schema: {
      nested: {
        model_task_config: {
          fields: [
            { name: 'replyer', type: 'object', advanced: false, description: '回复' },
            { name: 'embedding', type: 'object', advanced: false, description: '嵌入模型' },
            { name: 'vlm', type: 'object', advanced: false, description: '视觉' },
            { name: 'voice', type: 'object', advanced: true, description: '语音' },
          ],
        },
      },
    },
  }
}

function baseVersions() {
  return {
    success: true,
    active_version: {
      id: 'active',
      label: '默认配置',
      created_at: 1,
      modified_at: 1,
      size: 100,
      active: true,
      inner_config_version: '1.17.6',
      valid: true,
      error: null,
    },
    versions: [],
  }
}

function makeModels(count: number, provider = 'openai') {
  return Array.from({ length: count }, (_, index) => ({
    name: `model-${String(index).padStart(2, '0')}`,
    model_identifier: `id-${index}`,
    api_provider: provider,
    price_in: 1,
    price_out: 2,
  }))
}

function getModelTable() {
  return screen.getByRole('table', { name: '模型列表' })
}

function expectTableHasModel(name: string) {
  expect(within(getModelTable()).getByRole('button', { name: `编辑模型 ${name}` })).toBeInTheDocument()
}

function expectTableNotHasModel(name: string) {
  expect(
    within(getModelTable()).queryByRole('button', { name: `编辑模型 ${name}` })
  ).not.toBeInTheDocument()
}

function getModelListScroller() {
  return document.querySelector<HTMLElement>('[data-config-field-path="models"]')
}

function setScrollMetrics(
  element: HTMLElement,
  metrics: { scrollHeight: number; scrollTop: number; clientHeight: number }
) {
  Object.defineProperty(element, 'scrollHeight', { configurable: true, value: metrics.scrollHeight })
  Object.defineProperty(element, 'scrollTop', { configurable: true, value: metrics.scrollTop })
  Object.defineProperty(element, 'clientHeight', { configurable: true, value: metrics.clientHeight })
}

function installPointerCaptureStub() {
  if (!Element.prototype.hasPointerCapture) {
    Element.prototype.hasPointerCapture = () => false
  }
  if (!Element.prototype.setPointerCapture) {
    Element.prototype.setPointerCapture = () => undefined
  }
  if (!Element.prototype.releasePointerCapture) {
    Element.prototype.releasePointerCapture = () => undefined
  }
}

beforeEach(() => {
  routeState.searchStr = ''
  window.history.replaceState(null, '', '/config/model')
  localStorage.removeItem('model-assignment-tour-entry-dismissed')
  installPointerCaptureStub()
  vi.mocked(configApi.getModelConfigCached).mockResolvedValue(baseConfig() as never)
  vi.mocked(configApi.getModelConfig).mockResolvedValue(baseConfig() as never)
  vi.mocked(configApi.getModelConfigSchema).mockResolvedValue(baseSchema() as never)
  vi.mocked(configApi.getModelConfigVersions).mockResolvedValue(baseVersions() as never)
  vi.mocked(configApi.createModelConfigVersion).mockResolvedValue({
    ...baseVersions().active_version,
    id: 'v1',
    label: '测试副本',
    active: false,
  } as never)
  vi.mocked(configApi.switchModelConfigVersion).mockResolvedValue(
    baseVersions().active_version as never
  )
  vi.mocked(configApi.deleteModelConfigVersion).mockResolvedValue(undefined as never)
  vi.mocked(configApi.updateModelConfig).mockResolvedValue(baseConfig() as never)
  vi.mocked(configApi.updateModelConfigSection).mockResolvedValue(baseConfig() as never)
  vi.mocked(configApi.testProviderConnection).mockResolvedValue({
    network_ok: true,
    api_key_valid: true,
    latency_ms: 120,
    error: null,
    http_status: 200,
  } as never)
  vi.mocked(configApi.testModelCapability).mockResolvedValue({
    success: true,
    model_name: 'gpt-4',
    visual_tested: false,
    tool_call_ok: true,
    response: 'ok',
    reasoning: '',
    tool_calls: [],
    latency_ms: 100,
    error: null,
    prompt_tokens: 1,
    completion_tokens: 1,
    total_tokens: 2,
  } as never)
  vi.mocked(configApi.fetchProviderModels).mockResolvedValue([])
})

async function renderModelPage() {
  render(<ModelConfigPage />)
  // 等待初始加载完成（任意一个 tab 出现）
  await screen.findByRole('tab', { name: '模型设置' })
}

async function openConfigurationTab(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole('tab', { name: '模型设置' }))
  await screen.findByRole('table', { name: '模型列表' })
}

describe('ModelConfigPage 特征化', () => {
  it('初始加载调用 getModelConfigCached + getModelConfigSchema 并渲染', async () => {
    await renderModelPage()
    expect(configApi.getModelConfigCached).toHaveBeenCalled()
    expect(configApi.getModelConfigSchema).toHaveBeenCalled()
    expect(screen.getByRole('tab', { name: '模型设置' })).toBeInTheDocument()
  })

  it('加载未完成时展示加载状态', async () => {
    vi.mocked(configApi.getModelConfigCached).mockImplementation(
      () => new Promise(() => undefined)
    )
    render(<ModelConfigPage />)
    expect(await screen.findByRole('status', { name: '加载中' })).toBeInTheDocument()
  })

  it('DeepSeek Responses 模型默认缓存，并在高级设置中映射思考与联网参数', async () => {
    const user = userEvent.setup()
    const deepSeekConfig = {
      ...baseConfig(),
      models: [],
      api_providers: [
        {
          name: '自定义名称',
          base_url: 'https://api.deepseek.com',
          api_key: 'sk-deepseek',
          client_type: 'openai_responses',
        },
      ],
    }
    vi.mocked(configApi.getModelConfigCached).mockResolvedValue(deepSeekConfig as never)
    vi.mocked(configApi.getModelConfig).mockResolvedValue(deepSeekConfig as never)

    await renderModelPage()
    await user.click(screen.getByRole('tab', { name: '模型设置' }))
    const addModelButton = document.querySelector<HTMLButtonElement>(
      '[data-tour="add-model-button"]'
    )
    expect(addModelButton).not.toBeNull()
    await user.click(addModelButton!)

    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByRole('heading', { name: '添加模型' })).toBeInTheDocument()
    expect(within(dialog).queryByText('支持缓存')).not.toBeInTheDocument()
    const thinkingSwitch = within(dialog).getByRole('switch', { name: '启用思考' })
    const effortSelect = within(dialog).getByRole('combobox', { name: '思考力度' })
    const webSearchSwitch = within(dialog).getByRole('switch', { name: '启用联网搜索' })
    expect(thinkingSwitch).toBeChecked()
    expect(effortSelect).toBeEnabled()
    expect(webSearchSwitch).not.toBeChecked()

    await user.click(webSearchSwitch)
    await user.click(thinkingSwitch)
    expect(webSearchSwitch).toBeChecked()
    expect(thinkingSwitch).not.toBeChecked()
    expect(effortSelect).toBeDisabled()
    expect(within(dialog).getByText('已配置 2 个参数')).toBeInTheDocument()

    await user.click(within(dialog).getByRole('button', { name: '已配置 2 个参数' }))
    const extraParamsDialog = await screen.findByRole('dialog', { name: '编辑额外参数' })
    await user.click(within(extraParamsDialog).getByRole('tab', { name: 'JSON 编辑' }))
    const jsonEditor = within(extraParamsDialog).getByRole('textbox')
    expect((jsonEditor as HTMLTextAreaElement).value).toContain('"reasoning"')
    expect((jsonEditor as HTMLTextAreaElement).value).toContain('"effort": "none"')

    fireEvent.change(jsonEditor, {
      target: {
        value: JSON.stringify(
          {
            reasoning: { effort: 'max' },
            thinking: { type: 'disabled' },
            tools: [{ type: 'web_search' }],
          },
          null,
          2
        ),
      },
    })
    expect(within(extraParamsDialog).getByRole('button', { name: '保存' })).toBeDisabled()

    fireEvent.change(jsonEditor, {
      target: {
        value: JSON.stringify(
          {
            reasoning: { effort: 'max' },
            tools: [{ type: 'web_search' }],
          },
          null,
          2
        ),
      },
    })
    const saveExtraParamsButton = within(extraParamsDialog).getByRole('button', { name: '保存' })
    fireEvent.pointerDown(saveExtraParamsButton, { pointerType: 'touch' })
    // 内层弹窗打开时 Radix 会把外层弹窗标记为 aria-hidden，但外层不应被触摸事件卸载。
    expect(dialog).toBeInTheDocument()
    await user.click(saveExtraParamsButton)
    expect(within(dialog).getByRole('heading', { name: '添加模型' })).toBeInTheDocument()
    expect(thinkingSwitch).toBeChecked()
    expect(effortSelect).toBeEnabled()
    expect(effortSelect).toHaveTextContent('最高')

    await user.click(within(dialog).getByRole('button', { name: '高级设置' }))
    expect(within(dialog).getByRole('switch', { name: '支持缓存' })).toBeChecked()

    await user.click(within(dialog).getByRole('button', { name: '已配置 2 个参数' }))
    const reopenedExtraParamsDialog = await screen.findByRole('dialog', {
      name: '编辑额外参数',
    })
    await user.click(within(reopenedExtraParamsDialog).getByRole('tab', { name: 'JSON 编辑' }))
    fireEvent.change(within(reopenedExtraParamsDialog).getByRole('textbox'), {
      target: { value: '' },
    })
    await user.click(within(reopenedExtraParamsDialog).getByRole('button', { name: '保存' }))

    expect(within(dialog).getByRole('heading', { name: '添加模型' })).toBeInTheDocument()
    expect(within(dialog).getByText('未配置额外参数')).toBeInTheDocument()
  })

  describe('embedding 换模型警告', () => {
    it('更改 embedding 模型弹出警告对话框，确认后应用变更', async () => {
      const user = userEvent.setup()
      await renderModelPage()
      await user.click(screen.getByRole('tab', { name: '功能分配' }))
      await user.click(await screen.findByText('change-embedding'))

      // 弹出警告
      expect(await screen.findByText('更换嵌入模型警告')).toBeInTheDocument()
      // 此刻尚未应用
      expect(screen.getByTestId('task-models')).toHaveTextContent('old-embed-model')

      // 确认更换
      await user.click(screen.getByRole('button', { name: '确认更换' }))
      await waitFor(() =>
        expect(screen.getByTestId('task-models')).toHaveTextContent('new-embed-model')
      )
    })

    it('取消则不应用变更', async () => {
      const user = userEvent.setup()
      await renderModelPage()
      await user.click(screen.getByRole('tab', { name: '功能分配' }))
      await user.click(await screen.findByText('change-embedding'))
      expect(await screen.findByText('更换嵌入模型警告')).toBeInTheDocument()

      await user.click(screen.getByRole('button', { name: '取消' }))
      await waitFor(() => expect(screen.queryByText('更换嵌入模型警告')).not.toBeInTheDocument())
      expect(screen.getByTestId('task-models')).toHaveTextContent('old-embed-model')
    })
  })

  it('移动端由页面承载纵向滚动，子标签内容不被裁切', async () => {
    await renderModelPage()

    const page = document.querySelector('[data-model-config-page="true"]')
    expect(page?.parentElement).toHaveClass('overflow-y-auto', 'lg:overflow-hidden')
    expect(page).toHaveClass('min-h-full', 'lg:h-full', 'lg:overflow-hidden')

    const configurationPanel = screen.getByRole('tabpanel')
    expect(configurationPanel).toHaveClass('overflow-visible', 'lg:overflow-hidden')
  })

  it('保存配置：产生变更后点击保存调用 getModelConfig + updateModelConfig', async () => {
    const user = userEvent.setup()
    await renderModelPage()
    // 先经 embedding 确认产生一次变更（hasUnsavedChanges = true）
    await user.click(screen.getByRole('tab', { name: '功能分配' }))
    await user.click(await screen.findByText('change-embedding'))
    await user.click(screen.getByRole('button', { name: '确认更换' }))

    // 保存按钮位于「模型设置」tab
    await user.click(screen.getByRole('tab', { name: '模型设置' }))
    const saveButton = await screen.findByRole('button', { name: /保存配置/ })
    await user.click(saveButton)

    await waitFor(() => expect(configApi.getModelConfig).toHaveBeenCalled())
    expect(configApi.updateModelConfig).toHaveBeenCalled()
  })

  it('模型改名时原子保存模型列表与任务引用', async () => {
    const user = userEvent.setup()
    await renderModelPage()

    await openConfigurationTab(user)
    await user.click(within(getModelTable()).getByRole('button', { name: '编辑模型 gpt-4' }))
    const nameInput = await screen.findByRole('textbox', { name: '模型名称 *' })
    await user.clear(nameInput)
    await user.type(nameInput, 'renamed-gpt-4')
    await user.click(screen.getByRole('button', { name: '保存' }))

    await waitFor(() => expect(configApi.updateModelConfig).toHaveBeenCalledTimes(1))
    const savedConfig = vi.mocked(configApi.updateModelConfig).mock.calls[0][0] as {
      models: { name: string }[]
      model_task_config: Record<string, { model_list: string[] }>
    }
    expect(savedConfig.models[0].name).toBe('renamed-gpt-4')
    expect(savedConfig.model_task_config.replyer.model_list).toEqual(['renamed-gpt-4'])
    expect(savedConfig.model_task_config.embedding.model_list).toEqual(['old-embed-model'])
    expect(configApi.updateModelConfigSection).not.toHaveBeenCalled()
  })

  it('选择左侧厂商后只显示该厂商的模型，选择全部后恢复', async () => {
    const user = userEvent.setup()
    const filteredConfig = {
      ...baseConfig(),
      models: [
        { name: 'gpt-4', model_identifier: 'gpt-4', api_provider: 'openai' },
        { name: 'local-model', model_identifier: 'local-model', api_provider: 'ollama' },
      ],
      api_providers: [
        ...baseConfig().api_providers,
        {
          name: 'ollama',
          base_url: 'http://127.0.0.1:11434/v1',
          api_key: 'local',
          client_type: 'openai',
        },
      ],
    }
    vi.mocked(configApi.getModelConfigCached).mockResolvedValue(filteredConfig as never)
    vi.mocked(configApi.getModelConfig).mockResolvedValue(filteredConfig as never)

    await renderModelPage()
    await openConfigurationTab(user)
    expectTableHasModel('gpt-4')
    expectTableHasModel('local-model')

    await user.click(screen.getByRole('button', { name: '筛选厂商 ollama' }))
    expectTableNotHasModel('gpt-4')
    expectTableHasModel('local-model')
    expect(screen.getByRole('heading', { name: 'ollama' })).toBeInTheDocument()
    expect(screen.getByText('客户端类型：openai')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '全部' }))
    expectTableHasModel('gpt-4')
  })

  it('删除被模型引用的提供商触发级联确认，确认后连带移除关联模型', async () => {
    const user = userEvent.setup()
    await renderModelPage()
    await user.click(screen.getByRole('tab', { name: '模型设置' }))
    await user.click(screen.getByRole('button', { name: '筛选厂商 openai' }))

    // 删除 openai（被 gpt-4 引用）→ 单删确认框
    await user.click(await screen.findByRole('button', { name: '删除厂商 openai' }))
    expect(await screen.findByText('确认删除提供商')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '删除' }))

    // 触发级联确认框
    expect(await screen.findByText('删除提供商会同时移除关联模型')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '确认删除' }))

    // saveProviders 以 manual 上下文整保存：models 已移除 gpt-4
    await waitFor(() => expect(configApi.updateModelConfig).toHaveBeenCalled())
    const savedConfig = vi.mocked(configApi.updateModelConfig).mock.calls.at(-1)?.[0] as {
      models?: { name: string }[]
    }
    expect(savedConfig.models?.some((m) => m.name === 'gpt-4')).toBe(false)
  })

  it('同时渲染移动端卡片和桌面端表格，并用响应式 class 切换', async () => {
    const user = userEvent.setup()
    await renderModelPage()
    await openConfigurationTab(user)

    const tableSurface = document.querySelector('[data-model-config-table-surface="true"]')
    expect(tableSurface).toHaveClass('hidden', 'md:block')
    expectTableHasModel('gpt-4')

    // 卡片标题用测试状态作为 aria-label，不能按 heading name=模型名查询
    const cardList = document.querySelector('div.space-y-2\\.5.md\\:hidden')
    expect(cardList).toHaveClass('md:hidden')
    expect(cardList).toHaveTextContent('gpt-4')
    expect(within(cardList as HTMLElement).getByRole('button', { name: '编辑模型 gpt-4' })).toBeInTheDocument()
  })

  it('空厂商与空模型时展示侧栏全部入口和双视图空态', async () => {
    const user = userEvent.setup()
    const emptyConfig = {
      models: [],
      api_providers: [],
      model_task_config: {
        replyer: { model_list: [] },
        embedding: { model_list: [] },
      },
    }
    vi.mocked(configApi.getModelConfigCached).mockResolvedValue(emptyConfig as never)
    vi.mocked(configApi.getModelConfig).mockResolvedValue(emptyConfig as never)
    vi.mocked(configApi.getModelConfigSchema).mockResolvedValue(schemaWithTasks() as never)

    await renderModelPage()
    expect(screen.getByText('以下任务未配置模型')).toBeInTheDocument()
    expect(screen.getByText(/replyer、embedding 还未分配模型/)).toBeInTheDocument()

    await openConfigurationTab(user)
    expect(screen.getByRole('button', { name: '全部' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /筛选厂商/ })).not.toBeInTheDocument()
    expect(screen.getAllByText('暂无模型配置')).toHaveLength(2)
    expect(screen.queryByRole('heading', { name: 'openai' })).not.toBeInTheDocument()
  })

  it('空厂商添加模型时校验缺失的提供商和必填项', async () => {
    const user = userEvent.setup()
    const emptyConfig = {
      models: [],
      api_providers: [],
      model_task_config: { embedding: { model_list: [] } },
    }
    vi.mocked(configApi.getModelConfigCached).mockResolvedValue(emptyConfig as never)
    vi.mocked(configApi.getModelConfig).mockResolvedValue(emptyConfig as never)

    await renderModelPage()
    await user.click(screen.getByRole('tab', { name: '模型设置' }))
    await user.click(document.querySelector<HTMLButtonElement>('[data-tour="add-model-button"]')!)

    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByRole('heading', { name: '添加模型' })).toBeInTheDocument()
    await user.click(within(dialog).getByRole('button', { name: '保存' }))

    expect(within(dialog).getByText('请输入模型名称')).toBeInTheDocument()
    expect(within(dialog).getByText('请选择 API 提供商')).toBeInTheDocument()
    expect(within(dialog).getByText('请输入模型标识符')).toBeInTheDocument()
    expect(configApi.updateModelConfig).not.toHaveBeenCalled()
  })

  it('搜索无匹配时卡片和表格都显示未找到，并给出结果计数', async () => {
    const user = userEvent.setup()
    await renderModelPage()
    await openConfigurationTab(user)

    await user.type(screen.getByPlaceholderText('搜索模型名称、标识符或提供商...'), 'zzzz-missing')
    expect(screen.getByText('找到 0 个结果')).toBeInTheDocument()
    expect(screen.getAllByText('未找到匹配的模型')).toHaveLength(2)
  })

  it('模型列表首屏只展示 20 条，接近底部时再加载下一批', async () => {
    const user = userEvent.setup()
    const pagedConfig = {
      ...baseConfig(),
      models: makeModels(25),
    }
    vi.mocked(configApi.getModelConfigCached).mockResolvedValue(pagedConfig as never)
    vi.mocked(configApi.getModelConfig).mockResolvedValue(pagedConfig as never)

    await renderModelPage()
    await openConfigurationTab(user)

    expectTableHasModel('model-00')
    expectTableHasModel('model-19')
    expectTableNotHasModel('model-20')

    const scroller = getModelListScroller()
    expect(scroller).not.toBeNull()
    setScrollMetrics(scroller!, { scrollHeight: 1000, scrollTop: 0, clientHeight: 200 })
    fireEvent.scroll(scroller!)
    expectTableNotHasModel('model-20')

    setScrollMetrics(scroller!, { scrollHeight: 400, scrollTop: 220, clientHeight: 200 })
    fireEvent.scroll(scroller!)
    await waitFor(() => expectTableHasModel('model-20'))
    expectTableHasModel('model-24')
  })

  it('搜索会重置无限滚动窗口并只展示匹配项', async () => {
    const user = userEvent.setup()
    const pagedConfig = {
      ...baseConfig(),
      models: makeModels(25),
    }
    vi.mocked(configApi.getModelConfigCached).mockResolvedValue(pagedConfig as never)
    vi.mocked(configApi.getModelConfig).mockResolvedValue(pagedConfig as never)

    await renderModelPage()
    await openConfigurationTab(user)

    const scroller = getModelListScroller()!
    setScrollMetrics(scroller, { scrollHeight: 400, scrollTop: 220, clientHeight: 200 })
    fireEvent.scroll(scroller)
    await waitFor(() => expectTableHasModel('model-24'))

    fireEvent.change(screen.getByPlaceholderText('搜索模型名称、标识符或提供商...'), {
      target: { value: 'model-24' },
    })
    expect(screen.getByText('找到 1 个结果')).toBeInTheDocument()
    expectTableHasModel('model-24')
    expectTableNotHasModel('model-00')
    expectTableNotHasModel('model-19')
  })

  it('无效模型引用可一键清理', async () => {
    const user = userEvent.setup()
    const invalidConfig = {
      ...baseConfig(),
      model_task_config: {
        replyer: { model_list: ['ghost-model'] },
        embedding: { model_list: ['old-embed-model'] },
      },
    }
    vi.mocked(configApi.getModelConfigCached).mockResolvedValue(invalidConfig as never)
    vi.mocked(configApi.getModelConfig).mockResolvedValue(invalidConfig as never)

    await renderModelPage()
    expect(screen.getByText('检测到无效的模型引用')).toBeInTheDocument()
    expect(screen.getByText(/引用了不存在的模型: ghost-model/)).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '一键清理' }))
    await waitFor(() => expect(screen.queryByText('检测到无效的模型引用')).not.toBeInTheDocument())
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({ title: '清理完成', description: '已删除所有无效的模型引用' })
    )
  })

  it('关闭新手引导后写入本地标记且不再展示', async () => {
    const user = userEvent.setup()
    await renderModelPage()
    expect(screen.getByText(/新手引导/)).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '关闭' }))
    expect(screen.queryByText(/新手引导/)).not.toBeInTheDocument()
    expect(localStorage.getItem('model-assignment-tour-entry-dismissed')).toBe('true')

    cleanup()
    render(<ModelConfigPage />)
    await screen.findByRole('tab', { name: '模型设置' })
    expect(screen.queryByText(/新手引导/)).not.toBeInTheDocument()
  })

  it('切换标签会改写地址栏，URL tab 参数会决定初始标签', async () => {
    const user = userEvent.setup()
    await renderModelPage()

    await user.click(screen.getByRole('tab', { name: '模型设置' }))
    expect(window.location.pathname + window.location.search).toBe('/config/model?tab=configuration')

    await user.click(screen.getByRole('tab', { name: '功能分配' }))
    expect(window.location.pathname + window.location.search).toBe('/config/model')

    cleanup()
    window.history.replaceState(null, '', '/config/model?tab=configuration')
    routeState.searchStr = '?tab=configuration'
    render(<ModelConfigPage />)
    await screen.findByRole('tab', { name: '模型设置' })
    await waitFor(() =>
      expect(screen.getByRole('tab', { name: '模型设置' })).toHaveAttribute('data-state', 'active')
    )
  })

  it('保存并管理配置副本：空态、时间格式化、切换与删除', async () => {
    const user = userEvent.setup()
    const versions = {
      ...baseVersions(),
      versions: [
        {
          id: 'v1',
          label: '夜间副本',
          created_at: 1700000000,
          modified_at: 1700000000,
          size: 10,
          active: false,
          inner_config_version: '1.17.6',
          valid: true,
          error: null,
        },
        {
          id: 'v2',
          label: '无时间副本',
          created_at: 0,
          modified_at: 0,
          size: 10,
          active: false,
          inner_config_version: '1.17.6',
          valid: false,
          error: '解析失败',
        },
      ],
    }
    vi.mocked(configApi.getModelConfigVersions).mockResolvedValue(versions as never)

    await renderModelPage()
    await user.click(screen.getByRole('button', { name: '保存当前配置副本' }))
    const createDialog = await screen.findByRole('dialog', { name: '保存模型配置副本' })
    await user.type(within(createDialog).getByLabelText('副本名称'), '备份-A')
    await user.click(within(createDialog).getByRole('button', { name: '保存副本' }))
    await waitFor(() => expect(configApi.createModelConfigVersion).toHaveBeenCalledWith('备份-A'))
    expect(screen.queryByRole('dialog', { name: '保存模型配置副本' })).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '管理配置副本' }))
    const manageDialog = await screen.findByRole('dialog', { name: '模型配置副本' })
    expect(within(manageDialog).getByText('夜间副本')).toBeInTheDocument()
    expect(within(manageDialog).getByText('无时间副本')).toBeInTheDocument()
    expect(within(manageDialog).getByText('-')).toBeInTheDocument()
    expect(within(manageDialog).getByText('无效')).toBeInTheDocument()
    expect(within(manageDialog).getByText('解析失败')).toBeInTheDocument()
    expect(
      new Date(1700000000 * 1000).toLocaleString('zh-CN', {
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
      })
    ).toEqual(expect.any(String))
    expect(
      within(manageDialog).getByText(
        new Date(1700000000 * 1000).toLocaleString('zh-CN', {
          month: '2-digit',
          day: '2-digit',
          hour: '2-digit',
          minute: '2-digit',
        })
      )
    ).toBeInTheDocument()

    const switchButtons = within(manageDialog).getAllByRole('button', { name: '切换' })
    expect(switchButtons.some((button) => (button as HTMLButtonElement).disabled)).toBe(true)
    await user.click(switchButtons.find((button) => !(button as HTMLButtonElement).disabled)!)
    await waitFor(() => expect(configApi.switchModelConfigVersion).toHaveBeenCalledWith('v1'))

    const reopened = await screen.findByRole('dialog', { name: '模型配置副本' })
    await user.click(within(reopened).getByRole('button', { name: '删除副本 夜间副本' }))
    expect(await screen.findByText('删除模型配置副本')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '删除' }))
    await waitFor(() => expect(configApi.deleteModelConfigVersion).toHaveBeenCalledWith('v1'))
  })

  it('管理副本在没有任何未启用副本时展示空态', async () => {
    const user = userEvent.setup()
    await renderModelPage()
    await user.click(screen.getByRole('button', { name: '管理配置副本' }))
    expect(await screen.findByText('暂无未启用副本')).toBeInTheDocument()
  })

  it('搜索字段参数会切到对应标签并滚动定位', async () => {
    const scrollSpy = vi.spyOn(configSearchNavigation, 'scrollToConfigSearchField')
    routeState.searchStr = '?field=models&tab=configuration'
    window.history.replaceState(null, '', '/config/model?field=models&tab=configuration')

    await renderModelPage()
    await waitFor(() => expect(scrollSpy).toHaveBeenCalledWith('models'))
    expect(screen.getByRole('tab', { name: '模型设置' })).toHaveAttribute('data-state', 'active')
    scrollSpy.mockRestore()
  })

  it('任务搜索字段会展开高级设置并选中对应任务', async () => {
    const user = userEvent.setup()
    vi.mocked(configApi.getModelConfigSchema).mockResolvedValue(schemaWithTasks() as never)
    routeState.searchStr = '?field=model_task_config.vlm.model_list'
    window.history.replaceState(null, '', '/config/model?field=model_task_config.vlm.model_list')

    await renderModelPage()
    await waitFor(() =>
      expect(screen.getByRole('tab', { name: '功能分配' })).toHaveAttribute('data-state', 'active')
    )
    expect(await screen.findByText('温度已隐藏')).toBeInTheDocument()

    const voiceButton = await screen.findByRole('button', { name: /voice/ })
    await user.click(voiceButton)
    expect(await screen.findByText('最大 Token 已隐藏')).toBeInTheDocument()
  })

  it('全选后可批量删除当前页模型', async () => {
    const user = userEvent.setup()
    await renderModelPage()
    await openConfigurationTab(user)

    const selectAll = within(getModelTable()).getAllByRole('checkbox')[0]
    await user.click(selectAll)
    const batchButton = await screen.findByRole('button', { name: /批量删除 \(1\)/ })
    await user.click(batchButton)
    expect(await screen.findByText('确认批量删除')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '批量删除' }))
    await waitFor(() => expect(within(getModelTable()).queryByText('gpt-4')).not.toBeInTheDocument())
    expect(screen.getAllByText('暂无模型配置')).toHaveLength(2)
  })

  it('确认删除单个模型后从列表移除', async () => {
    const user = userEvent.setup()
    await renderModelPage()
    await openConfigurationTab(user)

    await user.click(within(getModelTable()).getByRole('button', { name: '删除模型 gpt-4' }))
    expect(await screen.findByText('确认删除')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '删除' }))
    await waitFor(() => expect(within(getModelTable()).queryByText('gpt-4')).not.toBeInTheDocument())
  })

  it('ResizeObserver 不可用时模型标识用 window resize 计算滚动距离', async () => {
    const originalObserver = window.ResizeObserver
    // 覆盖页面在无 ResizeObserver 时回退到 resize 监听的分支
    // @ts-expect-error 测试故意删除浏览器观察者
    delete window.ResizeObserver
    const restoreWidth = (() => {
      const proto = HTMLElement.prototype
      const scrollDesc = Object.getOwnPropertyDescriptor(proto, 'scrollWidth')
      const clientDesc = Object.getOwnPropertyDescriptor(proto, 'clientWidth')
      Object.defineProperty(proto, 'scrollWidth', { configurable: true, get: () => 400 })
      Object.defineProperty(proto, 'clientWidth', { configurable: true, get: () => 80 })
      return () => {
        if (scrollDesc) Object.defineProperty(proto, 'scrollWidth', scrollDesc)
        else delete (proto as { scrollWidth?: number }).scrollWidth
        if (clientDesc) Object.defineProperty(proto, 'clientWidth', clientDesc)
        else delete (proto as { clientWidth?: number }).clientWidth
      }
    })()

    try {
      vi.mocked(configApi.getModelConfigSchema).mockResolvedValue(schemaWithTasks() as never)
      await renderModelPage()
      const marqueeText = document.querySelector('.model-identifier-marquee-text') as HTMLElement
      expect(marqueeText).not.toBeNull()
      fireEvent(window, new Event('resize'))
      await waitFor(() =>
        expect(marqueeText.style.getPropertyValue('--model-identifier-marquee-distance')).toBe(
          '-320px'
        )
      )
    } finally {
      restoreWidth()
      window.ResizeObserver = originalObserver
    }
  })
})
