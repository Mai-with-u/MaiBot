import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ModelConfigPage } from '../model'
import { modelListCache } from '../model/constants'
import * as configApi from '@/lib/config-api'
import * as configSearchNavigation from '@/lib/config-search-navigation'

const toastMock = vi.fn()
const routeState = vi.hoisted(() => ({ searchStr: '' }))
const tourState = vi.hoisted(() => ({ startTour: vi.fn() }))

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
  localStorage.removeItem('model-assignment-tour-entry-dismissed')
  modelListCache.clear()
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
  return {
    ...actual,
    useModelTour: () => ({ startTour: tourState.startTour, isRunning: false, stepIndex: 0 }),
  }
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
        <button type="button" onClick={() => onChange('model_list', ['another-embed-model'])}>
          change-embedding-alt
        </button>
      </div>
    ),
  }
})

vi.mock('../modelProvider/ProviderForm', () => ({
  ProviderForm: ({ open }: { open: boolean }) =>
    open ? <div data-testid="provider-form">厂商表单</div> : null,
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

function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

function clickLatestToastAction() {
  const payload = toastMock.mock.calls.at(-1)?.[0] as
    | { action?: { props?: { onClick?: () => void } } }
    | undefined
  payload?.action?.props?.onClick?.()
}

function stubMatchMedia(matchesMinWidthLg: boolean) {
  const original = window.matchMedia
  window.matchMedia = ((query: string) => ({
    matches: matchesMinWidthLg && query.includes('min-width: 1024px'),
    media: query,
    onchange: null,
    addListener: () => undefined,
    removeListener: () => undefined,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    dispatchEvent: () => false,
  })) as typeof window.matchMedia
  return () => {
    window.matchMedia = original
  }
}

function getModelIdentifierCombobox(container: HTMLElement) {
  return within(container)
    .getAllByRole('combobox')
    .find((element) => element.getAttribute('aria-haspopup') === 'dialog') as HTMLElement
}

function clickDialogAction(container: HTMLElement, name: string) {
  const buttons = within(container).getAllByRole('button', { name })
  const labeled = buttons.find((button) => button.getAttribute('data-dashboard-button') === 'true')
  fireEvent.click(labeled ?? buttons[0])
}

function extraVersions() {
  return {
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
        id: 'v-invalid',
        label: '损坏副本',
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
    const sendTemperatureSwitch = within(dialog).getByRole('switch', {
      name: '发送 temperature 参数',
    })
    const customTemperatureSwitch = within(dialog).getByRole('switch', { name: '自定义模型温度' })
    expect(sendTemperatureSwitch).toBeChecked()
    await user.click(sendTemperatureSwitch)
    expect(customTemperatureSwitch).toBeDisabled()
    await user.click(sendTemperatureSwitch)
    expect(customTemperatureSwitch).toBeEnabled()

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

  it('在指定厂商下添加模型时默认选择该厂商', async () => {
    const user = userEvent.setup()
    const configWithTwoProviders = {
      ...baseConfig(),
      api_providers: [
        ...baseConfig().api_providers,
        {
          name: 'custom-provider',
          base_url: 'https://example.com/v1',
          api_key: 'sk-custom',
          client_type: 'openai',
        },
      ],
    }
    vi.mocked(configApi.getModelConfigCached).mockResolvedValue(configWithTwoProviders as never)
    vi.mocked(configApi.getModelConfig).mockResolvedValue(configWithTwoProviders as never)

    await renderModelPage()
    await openConfigurationTab(user)
    await user.click(screen.getByRole('button', { name: '筛选厂商 custom-provider' }))
    await user.click(document.querySelector<HTMLButtonElement>('[data-tour="add-model-button"]')!)

    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByRole('combobox', { name: 'API 提供商 *' })).toHaveTextContent(
      'custom-provider'
    )
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

  it('schema 加载失败时功能分配不渲染任务卡片', async () => {
    const user = userEvent.setup()
    vi.mocked(configApi.getModelConfigSchema).mockRejectedValue(new Error('schema 不可用'))
    vi.mocked(configApi.getModelConfigVersions).mockRejectedValue(new Error('副本服务挂了'))

    await renderModelPage()
    await user.click(screen.getByRole('tab', { name: '功能分配' }))

    expect(screen.getByText('为不同的任务配置使用的模型和参数')).toBeInTheDocument()
    expect(screen.queryByText('模型类别')).not.toBeInTheDocument()
    expect(screen.queryByTestId('task-config-card')).not.toBeInTheDocument()

    expect(screen.getByRole('combobox', { name: '模型配置副本' })).toHaveTextContent('默认配置')
  })

  it('保存配置失败时弹出失败提示并恢复保存按钮', async () => {
    const user = userEvent.setup()
    const saveJob = deferred<never>()
    vi.mocked(configApi.updateModelConfig).mockImplementation(() => saveJob.promise)

    await renderModelPage()
    await user.click(screen.getByRole('tab', { name: '功能分配' }))
    await user.click(await screen.findByText('change-embedding'))
    await user.click(screen.getByRole('button', { name: '确认更换' }))

    await user.click(screen.getByRole('tab', { name: '模型设置' }))
    await user.click(await screen.findByRole('button', { name: /保存配置/ }))
    expect(await screen.findByRole('button', { name: '保存中' })).toBeInTheDocument()

    saveJob.reject(new Error('磁盘已满'))
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({ title: '保存失败', description: '磁盘已满', variant: 'destructive' })
      )
    )
    expect(await screen.findByRole('button', { name: /保存配置/ })).toBeEnabled()
  })

  it('空 embedding 首次分配不弹警告，Esc 关闭警告会放弃变更', async () => {
    const user = userEvent.setup()
    const emptyEmbeddingConfig = {
      ...baseConfig(),
      model_task_config: {
        replyer: { model_list: ['gpt-4'] },
        embedding: { model_list: [] },
      },
    }
    vi.mocked(configApi.getModelConfigCached).mockResolvedValue(emptyEmbeddingConfig as never)
    vi.mocked(configApi.getModelConfig).mockResolvedValue(emptyEmbeddingConfig as never)
    vi.mocked(configApi.getModelConfigSchema).mockResolvedValue(schemaWithTasks() as never)

    await renderModelPage()
    await user.click(screen.getByRole('tab', { name: '功能分配' }))
    await user.click(await screen.findByRole('button', { name: /^embedding/ }))
    await waitFor(() => expect(screen.getByTestId('task-models')).toHaveTextContent('[]'))
    await user.click(await screen.findByText('change-embedding'))

    expect(screen.queryByText('更换嵌入模型警告')).not.toBeInTheDocument()
    expect(screen.getByTestId('task-models')).toHaveTextContent('new-embed-model')

    await user.click(screen.getByText('change-embedding-alt'))
    expect(await screen.findByText('更换嵌入模型警告')).toBeInTheDocument()
    await user.keyboard('{Escape}')
    await waitFor(() => expect(screen.queryByText('更换嵌入模型警告')).not.toBeInTheDocument())
    expect(screen.getByTestId('task-models')).toHaveTextContent('new-embed-model')
  })

  it('通过顶部选择器切换草稿，失败时保留当前列表', async () => {
    const user = userEvent.setup()
    vi.mocked(configApi.getModelConfigVersions).mockResolvedValue(extraVersions() as never)
    vi.mocked(configApi.switchModelConfigVersion).mockRejectedValue(new Error('副本损坏'))

    await renderModelPage()
    await user.click(screen.getByRole('combobox', { name: '模型配置副本' }))
    expect(screen.getByRole('option', { name: /损坏副本/ })).toHaveAttribute('data-disabled')
    await user.click(screen.getByRole('option', { name: /夜间副本/ }))

    await waitFor(() => expect(configApi.switchModelConfigVersion).toHaveBeenCalledWith('v1'))
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({ title: '切换副本失败', description: '副本损坏', variant: 'destructive' })
    )
    await openConfigurationTab(user)
    expectTableHasModel('gpt-4')
  })

  it('有未保存变更时切换草稿会先落盘再加载新配置', async () => {
    const user = userEvent.setup()
    const switchedConfig = {
      ...baseConfig(),
      models: [{ name: 'switched-model', model_identifier: 'switched', api_provider: 'openai' }],
    }
    vi.mocked(configApi.getModelConfigVersions).mockResolvedValue(extraVersions() as never)

    await renderModelPage()
    await user.click(screen.getByRole('tab', { name: '功能分配' }))
    await user.click(await screen.findByText('change-embedding'))
    await user.click(screen.getByRole('button', { name: '确认更换' }))

    vi.mocked(configApi.getModelConfigCached).mockResolvedValue(switchedConfig as never)
    vi.mocked(configApi.getModelConfig).mockResolvedValue(switchedConfig as never)

    await user.click(screen.getByRole('combobox', { name: '模型配置副本' }))
    await user.click(screen.getByRole('option', { name: /夜间副本/ }))

    await waitFor(() => expect(configApi.updateModelConfig).toHaveBeenCalled())
    await waitFor(() => expect(configApi.switchModelConfigVersion).toHaveBeenCalledWith('v1'))
    expect(toastMock).toHaveBeenCalledWith(expect.objectContaining({ title: '副本已切换' }))
  })

  it('tab=models / tab=providers / 厂商搜索字段都会落到模型设置', async () => {
    window.history.replaceState(null, '', '/config/model?tab=models')
    routeState.searchStr = '?tab=models'
    const { unmount: unmountModels } = render(<ModelConfigPage />)
    await screen.findByRole('tab', { name: '模型设置' })
    await waitFor(() =>
      expect(screen.getByRole('tab', { name: '模型设置' })).toHaveAttribute('data-state', 'active')
    )
    unmountModels()

    window.history.replaceState(null, '', '/config/model?tab=providers')
    routeState.searchStr = '?tab=providers'
    const { unmount: unmountProviders } = render(<ModelConfigPage />)
    await screen.findByRole('tab', { name: '模型设置' })
    await waitFor(() =>
      expect(screen.getByRole('tab', { name: '模型设置' })).toHaveAttribute('data-state', 'active')
    )
    unmountProviders()

    const scrollSpy = vi.spyOn(configSearchNavigation, 'scrollToConfigSearchField')
    window.history.replaceState(null, '', '/config/model?field=api_providers')
    routeState.searchStr = '?field=api_providers'
    render(<ModelConfigPage />)
    await waitFor(() => expect(scrollSpy).toHaveBeenCalledWith('api_providers'))
    expect(screen.getByRole('tab', { name: '模型设置' })).toHaveAttribute('data-state', 'active')
    scrollSpy.mockRestore()
  })

  it('测试模型后可通过 toast 打开详情并关闭，失败详情展示错误与空返回', async () => {
    const user = userEvent.setup()
    vi.mocked(configApi.testModelCapability).mockResolvedValueOnce({
      success: true,
      model_name: 'gpt-4',
      visual_tested: true,
      tool_call_ok: true,
      response: 'hello',
      reasoning: 'chain',
      tool_calls: [{ name: 'ping' }],
      latency_ms: 2100,
      error: null,
      prompt_tokens: 3,
      completion_tokens: 5,
      total_tokens: 8,
    } as never)

    await renderModelPage()
    await openConfigurationTab(user)
    await user.click(within(getModelTable()).getByRole('button', { name: '测试模型 gpt-4' }))
    await waitFor(() => expect(toastMock).toHaveBeenCalled())
    act(() => clickLatestToastAction())

    const successDialog = await screen.findByRole('dialog', { name: '模型测试详情' })
    expect(within(successDialog).getByText('通过')).toBeInTheDocument()
    expect(within(successDialog).getByText('2.10s')).toBeInTheDocument()
    expect(within(successDialog).getByText('已返回测试工具调用')).toBeInTheDocument()
    expect(within(successDialog).getByText('已附加测试图片')).toBeInTheDocument()
    expect(within(successDialog).getByText('推理内容')).toBeInTheDocument()
    expect(within(successDialog).getByText('chain')).toBeInTheDocument()
    clickDialogAction(successDialog, '关闭')
    await waitFor(() => expect(screen.queryByRole('dialog', { name: '模型测试详情' })).not.toBeInTheDocument())

    vi.mocked(configApi.testModelCapability).mockResolvedValueOnce({
      success: false,
      model_name: 'gpt-4',
      visual_tested: false,
      tool_call_ok: false,
      response: '',
      reasoning: '',
      tool_calls: [],
      latency_ms: null,
      error: '上游 500',
      prompt_tokens: 0,
      completion_tokens: 0,
      total_tokens: 0,
    } as never)
    await user.click(within(getModelTable()).getByRole('button', { name: '测试模型 gpt-4' }))
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith(expect.objectContaining({ title: '工具调用未通过' }))
    )
    act(() => clickLatestToastAction())
    const failedDialog = await screen.findByRole('dialog', { name: '模型测试详情' })
    expect(within(failedDialog).getByText('未通过')).toBeInTheDocument()
    expect(within(failedDialog).getByText('-')).toBeInTheDocument()
    expect(within(failedDialog).getByText('未返回测试工具调用')).toBeInTheDocument()
    expect(within(failedDialog).getByText('未附加图片')).toBeInTheDocument()
    expect(within(failedDialog).getByText('完整错误信息')).toBeInTheDocument()
    expect(within(failedDialog).getByText('上游 500')).toBeInTheDocument()
    expect(within(failedDialog).getByText('（无文本返回）')).toBeInTheDocument()
  })

  it('厂商零模型、测试连接、添加与编辑都会打开对应对话框', async () => {
    const user = userEvent.setup()
    const configWithEmptyProvider = {
      ...baseConfig(),
      api_providers: [
        ...baseConfig().api_providers,
        {
          name: 'empty-vendor',
          base_url: 'https://empty.example/v1',
          api_key: 'sk-empty',
          client_type: 'openai',
        },
      ],
    }
    vi.mocked(configApi.getModelConfigCached).mockResolvedValue(configWithEmptyProvider as never)
    vi.mocked(configApi.getModelConfig).mockResolvedValue(configWithEmptyProvider as never)

    await renderModelPage()
    await openConfigurationTab(user)
    expect(screen.queryByTestId('provider-form')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '添加厂商' }))
    expect(await screen.findByTestId('provider-form')).toHaveTextContent('厂商表单')

    await user.click(screen.getByRole('button', { name: '筛选厂商 empty-vendor' }))
    expect(screen.getByText('0 个模型')).toBeInTheDocument()
    expect(screen.getByText('Base URL：https://empty.example/v1')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '测试厂商 empty-vendor 连接' }))
    await waitFor(() => expect(configApi.testProviderConnection).toHaveBeenCalledWith('empty-vendor'))
    expect(toastMock).toHaveBeenCalledWith(expect.objectContaining({ title: '连接正常' }))

    await user.click(screen.getByRole('button', { name: '编辑厂商 empty-vendor' }))
    expect(screen.getByTestId('provider-form')).toBeInTheDocument()

    await user.click(document.querySelector<HTMLButtonElement>('[data-tour="add-model-button"]')!)
    const addDialog = await screen.findByRole('dialog', { name: /添加模型/ })
    const identifierTrigger = getModelIdentifierCombobox(addDialog)
    await waitFor(() => expect(identifierTrigger).not.toBeDisabled())
    await user.click(identifierTrigger)
    expect(await screen.findByText('未找到匹配的模型')).toBeInTheDocument()
  })

  it('无关联模型的厂商删除不走级联，取消删除则保持原样', async () => {
    const user = userEvent.setup()
    const configWithSpare = {
      ...baseConfig(),
      api_providers: [
        ...baseConfig().api_providers,
        {
          name: 'spare',
          base_url: 'https://spare.example/v1',
          api_key: 'sk-spare',
          client_type: 'openai',
        },
      ],
    }
    vi.mocked(configApi.getModelConfigCached).mockResolvedValue(configWithSpare as never)
    vi.mocked(configApi.getModelConfig).mockResolvedValue(configWithSpare as never)

    await renderModelPage()
    await openConfigurationTab(user)
    await user.click(screen.getByRole('button', { name: '筛选厂商 spare' }))
    await user.click(screen.getByRole('button', { name: '删除厂商 spare' }))
    expect(await screen.findByText('确认删除提供商')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '取消' }))
    expect(screen.queryByText('确认删除提供商')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '筛选厂商 spare' })).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '删除厂商 spare' }))
    await user.click(screen.getByRole('button', { name: '删除' }))
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith(expect.objectContaining({ title: '删除成功' }))
    )
    await waitFor(() => expect(screen.queryByRole('button', { name: '筛选厂商 spare' })).not.toBeInTheDocument())
    expect(screen.queryByText('删除提供商会同时移除关联模型')).not.toBeInTheDocument()
  })

  it('级联删除超过 8 个关联模型时展示剩余计数，取消后模型仍在', async () => {
    const user = userEvent.setup()
    const manyModelsConfig = {
      ...baseConfig(),
      models: makeModels(9),
    }
    vi.mocked(configApi.getModelConfigCached).mockResolvedValue(manyModelsConfig as never)
    vi.mocked(configApi.getModelConfig).mockResolvedValue(manyModelsConfig as never)

    await renderModelPage()
    await openConfigurationTab(user)
    await user.click(screen.getByRole('button', { name: '筛选厂商 openai' }))
    await user.click(screen.getByRole('button', { name: '删除厂商 openai' }))
    await user.click(await screen.findByRole('button', { name: '删除' }))

    expect(await screen.findByText('删除提供商会同时移除关联模型')).toBeInTheDocument()
    expect(screen.getByText('还有 1 个模型...')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '取消' }))
    await waitFor(() =>
      expect(screen.queryByText('删除提供商会同时移除关联模型')).not.toBeInTheDocument()
    )
    expectTableHasModel('model-00')
  })

  it('创建与管理副本对话框可取消，创建失败仍关闭弹窗', async () => {
    const user = userEvent.setup()
    vi.mocked(configApi.getModelConfigVersions).mockResolvedValue(extraVersions() as never)
    vi.mocked(configApi.createModelConfigVersion).mockRejectedValue(new Error('磁盘满'))

    await renderModelPage()
    await user.click(screen.getByRole('button', { name: '保存当前配置副本' }))
    const createDialog = await screen.findByRole('dialog', { name: '保存模型配置副本' })
    await user.click(within(createDialog).getByRole('button', { name: '取消' }))
    expect(screen.queryByRole('dialog', { name: '保存模型配置副本' })).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '保存当前配置副本' }))
    const retryCreate = await screen.findByRole('dialog', { name: '保存模型配置副本' })
    await user.type(within(retryCreate).getByLabelText('副本名称'), '失败副本')
    await user.click(within(retryCreate).getByRole('button', { name: '保存副本' }))
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({ title: '创建副本失败', description: '磁盘满' })
      )
    )
    await waitFor(() =>
      expect(screen.queryByRole('dialog', { name: '保存模型配置副本' })).not.toBeInTheDocument()
    )

    await user.click(screen.getByRole('button', { name: '管理配置副本' }))
    const manageDialog = await screen.findByRole('dialog', { name: '模型配置副本' })
    await user.click(within(manageDialog).getByRole('button', { name: '删除副本 夜间副本' }))
    expect(await screen.findByText('删除模型配置副本')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '取消' }))
    await waitFor(() => expect(screen.queryByText('删除模型配置副本')).not.toBeInTheDocument())
    clickDialogAction(manageDialog, '关闭')
    await waitFor(() => expect(screen.queryByRole('dialog', { name: '模型配置副本' })).not.toBeInTheDocument())
  })

  it('切换副本过程中管理弹窗展示切换中', async () => {
    const user = userEvent.setup()
    const switchJob = deferred<never>()
    vi.mocked(configApi.getModelConfigVersions).mockResolvedValue(extraVersions() as never)
    vi.mocked(configApi.switchModelConfigVersion).mockImplementation(() => switchJob.promise as never)

    await renderModelPage()
    await user.click(screen.getByRole('button', { name: '管理配置副本' }))
    const manageDialog = await screen.findByRole('dialog', { name: '模型配置副本' })
    const switchButtons = within(manageDialog).getAllByRole('button', { name: '切换' })
    await user.click(switchButtons.find((button) => !(button as HTMLButtonElement).disabled)!)
    expect(await screen.findByText('切换中...')).toBeInTheDocument()
    switchJob.resolve(extraVersions().active_version as never)
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith(expect.objectContaining({ title: '副本已切换' }))
    )
  })

  it('DeepSeek Chat 模板写入 thinking 参数，且联网搜索不可用', async () => {
    const user = userEvent.setup()
    const deepSeekChatConfig = {
      ...baseConfig(),
      models: [],
      api_providers: [
        {
          name: 'deepseek-chat',
          base_url: 'https://api.deepseek.com',
          api_key: 'sk-deepseek',
          client_type: 'openai',
        },
      ],
    }
    vi.mocked(configApi.getModelConfigCached).mockResolvedValue(deepSeekChatConfig as never)
    vi.mocked(configApi.getModelConfig).mockResolvedValue(deepSeekChatConfig as never)

    await renderModelPage()
    await user.click(screen.getByRole('tab', { name: '模型设置' }))
    await user.click(document.querySelector<HTMLButtonElement>('[data-tour="add-model-button"]')!)

    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText('写入 thinking.type')).toBeInTheDocument()
    expect(within(dialog).getByText('仅 DeepSeek Responses API 支持原生联网搜索')).toBeInTheDocument()
    const webSearchSwitch = within(dialog).getByRole('switch', { name: '启用联网搜索' })
    expect(webSearchSwitch).toBeDisabled()

    await user.click(within(dialog).getByRole('combobox', { name: '思考力度' }))
    await user.click(screen.getByRole('option', { name: '低' }))
    expect(within(dialog).getByRole('combobox', { name: '思考力度' })).toHaveTextContent('低')
    await user.click(within(dialog).getByRole('switch', { name: '启用思考' }))
    expect(within(dialog).getByRole('combobox', { name: '思考力度' })).toBeDisabled()
  })

  it('编辑模型可改价格、视觉、缓存、温度、Token、额外参数，并拦截嵌套弹窗关闭', async () => {
    const user = userEvent.setup()
    const richConfig = {
      ...baseConfig(),
      models: [
        {
          name: 'gpt-4',
          model_identifier: 'gpt-4',
          api_provider: 'openai',
          visual: true,
          cache: true,
          cache_price_in: 0.4,
          force_stream_mode: false,
          temperature: 1.2,
          send_temperature: true,
          max_tokens: 2048,
          extra_params: { a: 1, b: 2, c: 3, d: 4 },
          price_in: 1,
          price_out: 2,
        },
      ],
    }
    vi.mocked(configApi.getModelConfigCached).mockResolvedValue(richConfig as never)
    vi.mocked(configApi.getModelConfig).mockResolvedValue(richConfig as never)

    await renderModelPage()
    await openConfigurationTab(user)
    await user.click(within(getModelTable()).getByRole('button', { name: '编辑模型 gpt-4' }))

    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByRole('heading', { name: '编辑模型' })).toBeInTheDocument()
    expect(within(dialog).getByText('...')).toBeInTheDocument()
    expect(within(dialog).getByText(/温度 > 1 会产生更随机/)).toBeInTheDocument()
    expect(within(dialog).getAllByRole('button', { name: '帮助信息' })).toHaveLength(2)

    await user.click(within(dialog).getByRole('switch', { name: '启用视觉' }))
    fireEvent.change(within(dialog).getByLabelText('输入价格 (¥/M token)'), { target: { value: '' } })
    fireEvent.change(within(dialog).getByLabelText('输出价格 (¥/M token)'), { target: { value: '3.5' } })
    fireEvent.change(within(dialog).getByLabelText('缓存价格 (¥/M token)'), { target: { value: '' } })

    const temperatureInput = within(dialog).getByDisplayValue('1.2')
    fireEvent.change(temperatureInput, { target: { value: '0.55' } })
    expect((temperatureInput as HTMLInputElement).value).toBe('0.55')
    fireEvent.blur(temperatureInput, { target: { value: '-1' } })
    fireEvent.blur(temperatureInput, { target: { value: '9' } })
    fireEvent.blur(temperatureInput, { target: { value: '1.1' } })

    const slider = within(dialog).getByRole('slider')
    fireEvent.keyDown(slider, { key: 'ArrowRight' })

    fireEvent.change(within(dialog).getByDisplayValue('2048'), { target: { value: '4096' } })
    await user.click(within(dialog).getByRole('switch', { name: '自定义最大 Token' }))
    await user.click(within(dialog).getByRole('switch', { name: '自定义最大 Token' }))
    expect(within(dialog).getByDisplayValue('2048')).toBeInTheDocument()

    await user.click(within(dialog).getByRole('button', { name: '高级设置' }))
    await user.click(within(dialog).getByRole('switch', { name: '强制流式输出模式' }))
    await user.click(within(dialog).getByRole('switch', { name: '支持缓存' }))
    expect(within(dialog).queryByLabelText('缓存价格 (¥/M token)')).not.toBeInTheDocument()

    await user.click(within(dialog).getByRole('button', { name: '已配置 4 个参数' }))
    const extraDialog = await screen.findByRole('dialog', { name: '编辑额外参数' })
    fireEvent.click(within(dialog).getByRole('button', { name: '关闭', hidden: true }))
    expect(within(dialog).getByRole('heading', { name: '编辑模型', hidden: true })).toBeInTheDocument()
    await user.click(within(extraDialog).getByRole('button', { name: '取消' }))
    expect(within(dialog).getByRole('heading', { name: '编辑模型' })).toBeInTheDocument()

    await user.click(within(dialog).getByRole('button', { name: '取消' }))
    await waitFor(() => expect(screen.queryByRole('heading', { name: '编辑模型' })).not.toBeInTheDocument())
  })

  it('自动获取模型列表可选中项，刷新失败后提示手动填写', async () => {
    const user = userEvent.setup()
    vi.mocked(configApi.fetchProviderModels).mockResolvedValue([
      { id: 'gpt-4', name: 'gpt-4' },
      { id: 'gpt-4o', name: 'GPT-4 Omni' },
    ] as never)

    await renderModelPage()
    await openConfigurationTab(user)
    await user.click(within(getModelTable()).getByRole('button', { name: '编辑模型 gpt-4' }))
    const dialog = await screen.findByRole('dialog')
    expect(await within(dialog).findByText('OpenAI')).toBeInTheDocument()

    const identifierCombobox = getModelIdentifierCombobox(dialog)
    await waitFor(() => expect(identifierCombobox).not.toBeDisabled())
    await user.click(identifierCombobox)
    expect(await screen.findByRole('option', { name: /GPT-4 Omni/ })).toBeInTheDocument()
    await user.click(screen.getByRole('option', { name: 'gpt-4' }))
    expect(within(dialog).getByLabelText(/模型标识符/)).toHaveValue('gpt-4')
    await user.click(getModelIdentifierCombobox(dialog))
    await user.click(screen.getByRole('option', { name: /GPT-4 Omni/ }))
    expect(within(dialog).getByLabelText(/模型标识符/)).toHaveValue('gpt-4o')

    vi.mocked(configApi.fetchProviderModels).mockRejectedValueOnce(new Error('timeout'))
    const refreshButton = within(dialog).getByText('OpenAI').parentElement?.querySelector('button')
    expect(refreshButton).not.toBeNull()
    await user.click(refreshButton!)
    expect(await within(dialog).findByText('请求超时，请检查网络连接后重试')).toBeInTheDocument()
    expect(within(dialog).getByText('手动填写')).toBeInTheDocument()
    expect(
      within(dialog).getByText('请手动输入模型标识符，或前往"模型厂商设置"检查 API Key')
    ).toBeInTheDocument()
  })

  it('提供商配置不完整时提示手动填写模型标识符', async () => {
    const user = userEvent.setup()
    const incompleteProviderConfig = {
      ...baseConfig(),
      api_providers: [
        {
          name: 'openai',
          base_url: '',
          api_key: 'sk-x',
          client_type: 'openai',
        },
      ],
    }
    vi.mocked(configApi.getModelConfigCached).mockImplementation(
      async () => incompleteProviderConfig as never
    )
    vi.mocked(configApi.getModelConfig).mockImplementation(
      async () => incompleteProviderConfig as never
    )

    await renderModelPage()
    await openConfigurationTab(user)
    await user.click(within(getModelTable()).getByRole('button', { name: '编辑模型 gpt-4' }))
    const keyDialog = await screen.findByRole('dialog')
    expect(
      await within(keyDialog).findByText('请手动输入模型标识符，或前往"模型厂商设置"检查 API Key')
    ).toBeInTheDocument()
  })

  it('添加模型时切换厂商会清空列表，重复名称会校验失败', async () => {
    const user = userEvent.setup()
    const twoProviderConfig = {
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
    vi.mocked(configApi.getModelConfigCached).mockResolvedValue(twoProviderConfig as never)
    vi.mocked(configApi.getModelConfig).mockResolvedValue(twoProviderConfig as never)

    await renderModelPage()
    await openConfigurationTab(user)
    await user.click(document.querySelector<HTMLButtonElement>('[data-tour="add-model-button"]')!)
    const dialog = await screen.findByRole('dialog')
    await user.type(within(dialog).getByRole('textbox', { name: '模型名称 *' }), 'gpt-4')
    await user.type(within(dialog).getByLabelText(/模型标识符/), 'dup')
    await user.click(within(dialog).getByRole('button', { name: '保存' }))
    expect(within(dialog).getByText('模型名称已存在，请使用其他名称')).toBeInTheDocument()

    await user.clear(within(dialog).getByRole('textbox', { name: '模型名称 *' }))
    await user.type(within(dialog).getByRole('textbox', { name: '模型名称 *' }), 'fresh-model')
    expect(within(dialog).queryByText('模型名称已存在，请使用其他名称')).not.toBeInTheDocument()

    await user.click(within(dialog).getByRole('combobox', { name: 'API 提供商 *' }))
    await user.click(screen.getByRole('option', { name: 'ollama' }))
    expect(within(dialog).getByRole('combobox', { name: 'API 提供商 *' })).toHaveTextContent('ollama')
  })

  it('功能分配可展开高级任务，空模型列表仍显示未配置摘要', async () => {
    const user = userEvent.setup()
    const emptyModelsConfig = {
      models: [],
      api_providers: baseConfig().api_providers,
      model_task_config: {
        replyer: { model_list: [] },
        embedding: { model_list: [] },
        vlm: { model_list: [] },
        voice: { model_list: [] },
      },
    }
    vi.mocked(configApi.getModelConfigCached).mockResolvedValue(emptyModelsConfig as never)
    vi.mocked(configApi.getModelConfig).mockResolvedValue(emptyModelsConfig as never)
    vi.mocked(configApi.getModelConfigSchema).mockResolvedValue(schemaWithTasks() as never)

    await renderModelPage()
    expect(screen.getByText('以下任务未配置模型')).toBeInTheDocument()
    await user.click(screen.getByRole('tab', { name: '功能分配' }))
    expect(screen.getAllByText('未配置模型').length).toBeGreaterThan(0)
    expect(screen.getAllByText('未配置').length).toBeGreaterThan(0)

    await user.click(screen.getByRole('button', { name: '高级设置' }))
    const voiceButton = await screen.findByRole('button', { name: /voice/ })
    expect(within(voiceButton).getByText('高级')).toBeInTheDocument()
    await user.click(voiceButton)
    expect(await screen.findByText('最大 Token 已隐藏')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '高级设置' }))
    await waitFor(() => expect(screen.queryByRole('button', { name: /voice/ })).not.toBeInTheDocument())
  })

  it('取消删除单个模型与批量删除都不会改动列表', async () => {
    const user = userEvent.setup()
    await renderModelPage()
    await openConfigurationTab(user)

    await user.click(within(getModelTable()).getByRole('button', { name: '删除模型 gpt-4' }))
    expect(await screen.findByText('确认删除')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '取消' }))
    expectTableHasModel('gpt-4')

    await user.click(within(getModelTable()).getAllByRole('checkbox')[0])
    await user.click(await screen.findByRole('button', { name: /批量删除 \(1\)/ }))
    expect(await screen.findByText('确认批量删除')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '取消' }))
    expectTableHasModel('gpt-4')
  })

  it('大屏下按页面剩余高度约束模型布局，并响应 resize / visualViewport', async () => {
    const restoreMatchMedia = stubMatchMedia(true)
    const visualViewportListeners: Array<() => void> = []
    const originalVisualViewport = window.visualViewport
    Object.defineProperty(window, 'visualViewport', {
      configurable: true,
      value: {
        addEventListener: (_type: string, listener: () => void) => {
          visualViewportListeners.push(listener)
        },
        removeEventListener: (_type: string, listener: () => void) => {
          const index = visualViewportListeners.indexOf(listener)
          if (index >= 0) visualViewportListeners.splice(index, 1)
        },
      },
    })
    const originalGetRect = HTMLElement.prototype.getBoundingClientRect
    HTMLElement.prototype.getBoundingClientRect = function getBoundingClientRect() {
      if (this.getAttribute('data-model-config-page') === 'true') {
        return new DOMRect(0, 0, 1200, 900)
      }
      if (this.getAttribute('data-model-config-layout') === 'true') {
        return new DOMRect(0, 180, 1200, 0)
      }
      return originalGetRect.call(this)
    }

    try {
      const user = userEvent.setup()
      await renderModelPage()
      await openConfigurationTab(user)
      const layout = document.querySelector<HTMLElement>('[data-model-config-layout="true"]')
      expect(layout).not.toBeNull()
      await waitFor(() => expect(layout).toHaveStyle({ height: '720px' }))
      fireEvent(window, new Event('resize'))
      visualViewportListeners.forEach((listener) => listener())
      expect(layout).toHaveStyle({ height: '720px' })
    } finally {
      HTMLElement.prototype.getBoundingClientRect = originalGetRect
      restoreMatchMedia()
      Object.defineProperty(window, 'visualViewport', {
        configurable: true,
        value: originalVisualViewport,
      })
    }
  })

  it('点击新手引导入口会启动引导', async () => {
    const user = userEvent.setup()
    await renderModelPage()
    await user.click(screen.getByRole('button', { name: '开始引导' }))
    expect(tourState.startTour).toHaveBeenCalled()
  })
})
