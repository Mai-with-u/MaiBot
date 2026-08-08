import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ModelConfigPage } from '../model'
import * as configApi from '@/lib/config-api'

const toastMock = vi.fn()

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

vi.mock('@/hooks/use-toast', () => ({ useToast: () => ({ toast: toastMock }) }))
vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => vi.fn(),
  useRouterState: ({
    select,
  }: {
    select: (state: { location: { searchStr: string } }) => string
  }) => select({ location: { searchStr: '' } }),
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
  fetchProviderModels: vi.fn(),
  fetchModelClientTypes: vi.fn(),
}))

// 子组件桩：暴露关键回调以驱动主文件编排逻辑（加载/embedding 警告/保存/级联）
vi.mock('../model/components', () => ({
  Pagination: () => <div data-testid="pagination" />,
  ModelCardList: () => <div data-testid="model-card-list" />,
  ModelTable: ({
    paginatedModels,
    onDelete,
    onEdit,
  }: {
    paginatedModels: { name: string }[]
    onDelete: (i: number) => void
    onEdit: (model: { name: string }, index: number) => void
  }) => (
    <div data-testid="model-table">
      {paginatedModels.map((m, i) => (
        <div key={m.name}>
          <span>{m.name}</span>
          <button type="button" onClick={() => onEdit(m, i)}>
            {`edit-model-${m.name}`}
          </button>
          <button type="button" onClick={() => onDelete(i)}>{`del-model-${m.name}`}</button>
        </div>
      ))}
    </div>
  ),
  // 唯一的 TaskConfigCard 对应 schema 里的 embedding 字段
  TaskConfigCard: ({
    taskConfig,
    onChange,
  }: {
    taskConfig: { model_list?: string[] }
    onChange: (f: string, v: string[]) => void
  }) => (
    <div data-testid="task-config-card">
      <span data-testid="task-models">{JSON.stringify(taskConfig.model_list ?? [])}</span>
      <button type="button" onClick={() => onChange('model_list', ['new-embed-model'])}>
        change-embedding
      </button>
    </div>
  ),
}))

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

beforeEach(() => {
  window.history.replaceState(null, '', '/config/model')
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
  vi.mocked(configApi.fetchProviderModels).mockResolvedValue([])
})

async function renderModelPage() {
  render(<ModelConfigPage />)
  // 等待初始加载完成（任意一个 tab 出现）
  await screen.findByRole('tab', { name: '模型设置' })
}

describe('ModelConfigPage 特征化', () => {
  it('初始加载调用 getModelConfigCached + getModelConfigSchema 并渲染', async () => {
    await renderModelPage()
    expect(configApi.getModelConfigCached).toHaveBeenCalled()
    expect(configApi.getModelConfigSchema).toHaveBeenCalled()
    expect(screen.getByRole('tab', { name: '模型设置' })).toBeInTheDocument()
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

    const dialog = await screen.findByRole('dialog', { name: '添加模型' })
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
    await user.click(within(extraParamsDialog).getByRole('button', { name: '保存' }))
    expect(thinkingSwitch).toBeChecked()
    expect(effortSelect).toBeEnabled()
    expect(effortSelect).toHaveTextContent('最高')

    await user.click(within(dialog).getByRole('button', { name: '高级设置' }))
    expect(within(dialog).getByRole('switch', { name: '支持缓存' })).toBeChecked()
  })

  it('切到任务页可见 embedding 配置卡片', async () => {
    const user = userEvent.setup()
    await renderModelPage()
    await user.click(screen.getByRole('tab', { name: '功能分配' }))
    expect(await screen.findByTestId('task-config-card')).toBeInTheDocument()
    expect(screen.getByTestId('task-models')).toHaveTextContent('old-embed-model')
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

    await user.click(screen.getByRole('tab', { name: '模型设置' }))
    await user.click(screen.getByText('edit-model-gpt-4'))
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

  it('提供商连接测试调用 testProviderConnection', async () => {
    const user = userEvent.setup()
    await renderModelPage()
    await user.click(screen.getByRole('tab', { name: '模型设置' }))
    await user.click(await screen.findByRole('button', { name: '测试厂商 openai 连接' }))
    await waitFor(() => expect(configApi.testProviderConnection).toHaveBeenCalledWith('openai'))
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
    const modelTable = screen.getByTestId('model-table')
    expect(within(modelTable).getByText('gpt-4')).toBeInTheDocument()
    expect(within(modelTable).getByText('local-model')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '筛选厂商 ollama' }))
    expect(within(modelTable).queryByText('gpt-4')).not.toBeInTheDocument()
    expect(within(modelTable).getByText('local-model')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '全部' }))
    expect(within(modelTable).getByText('gpt-4')).toBeInTheDocument()
  })

  it('删除被模型引用的提供商触发级联确认，确认后连带移除关联模型', async () => {
    const user = userEvent.setup()
    await renderModelPage()
    await user.click(screen.getByRole('tab', { name: '模型设置' }))

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
})
