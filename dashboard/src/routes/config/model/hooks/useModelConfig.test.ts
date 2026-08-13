import { act, renderHook, waitFor } from '@testing-library/react'
import { isValidElement } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  createModelConfigVersion,
  deleteModelConfigVersion,
  getModelConfig,
  getModelConfigCached,
  getModelConfigSchema,
  getModelConfigVersions,
  switchModelConfigVersion,
  testModelCapability,
  testProviderConnection,
  updateModelConfig,
  updateModelConfigSection,
} from '@/lib/config-api'

import type { ModelInfo, ModelTaskConfig } from '../types'
import { useModelConfig } from './useModelConfig'

const toastMock = vi.fn()

vi.mock('@/hooks/use-toast', () => ({
  useToast: () => ({ toast: toastMock }),
}))

vi.mock('@/lib/config-api', () => ({
  createModelConfigVersion: vi.fn(),
  deleteModelConfigVersion: vi.fn(),
  getModelConfig: vi.fn(),
  getModelConfigCached: vi.fn(),
  getModelConfigSchema: vi.fn(),
  getModelConfigVersions: vi.fn(),
  switchModelConfigVersion: vi.fn(),
  testModelCapability: vi.fn(),
  testProviderConnection: vi.fn(),
  updateModelConfig: vi.fn(),
  updateModelConfigSection: vi.fn(),
}))

const getModelConfigMock = vi.mocked(getModelConfig)
const getModelConfigCachedMock = vi.mocked(getModelConfigCached)
const getModelConfigSchemaMock = vi.mocked(getModelConfigSchema)
const getModelConfigVersionsMock = vi.mocked(getModelConfigVersions)
const updateModelConfigMock = vi.mocked(updateModelConfig)
const updateModelConfigSectionMock = vi.mocked(updateModelConfigSection)
const createModelConfigVersionMock = vi.mocked(createModelConfigVersion)
const deleteModelConfigVersionMock = vi.mocked(deleteModelConfigVersion)
const switchModelConfigVersionMock = vi.mocked(switchModelConfigVersion)
const testModelCapabilityMock = vi.mocked(testModelCapability)
const testProviderConnectionMock = vi.mocked(testProviderConnection)

function createDeferred<T>() {
  let resolve: (value: T | PromiseLike<T>) => void = () => {}
  let reject: (reason?: unknown) => void = () => {}
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, reject, resolve }
}

function versionInfo(id: string, label: string, active = false) {
  return {
    id,
    label,
    created_at: 1,
    modified_at: 2,
    size: 16,
    active,
    inner_config_version: null,
    valid: true,
    error: null,
  }
}

function provider(name: string, extra: Record<string, unknown> = {}) {
  return {
    name,
    base_url:
      name === 'deepseek' ? 'https://api.deepseek.com' : `https://${name}.example.com/v1`,
    api_key: `key-${name}`,
    client_type: 'openai',
    max_retry: 2,
    timeout: 30,
    retry_interval: 10,
    ...extra,
  }
}

function model(name: string, apiProvider = 'main', extra: Partial<ModelInfo> = {}): ModelInfo {
  return {
    name,
    model_identifier: `${name}-id`,
    api_provider: apiProvider,
    price_in: 1,
    price_out: 2,
    ...extra,
  }
}

function taskConfigSchema() {
  return {
    schema: {
      nested: {
        model_task_config: {
          className: 'ModelTaskConfig',
          classDoc: '',
          fields: [
            { name: 'replyer', type: 'object', advanced: false },
            { name: 'utils', type: 'object', advanced: false },
            { name: 'embedding', type: 'object', advanced: false },
            { name: 'memory', type: 'object', advanced: false },
            { name: 'learner', type: 'object', advanced: false },
            { name: 'emoji', type: 'object', advanced: false },
            { name: 'voice', type: 'object', advanced: false },
            { name: 'hidden', type: 'object', advanced: true },
            { name: 'note', type: 'string', advanced: false },
          ],
        },
      },
    },
  }
}

function defaultTaskConfig(): ModelTaskConfig {
  return {
    replyer: { model_list: ['chat'] },
    embedding: { model_list: ['chat'] },
  }
}

function defaultConfig(overrides: Record<string, unknown> = {}) {
  return {
    models: [model('chat')],
    api_providers: [provider('main'), provider('spare')],
    model_task_config: defaultTaskConfig(),
    ...overrides,
  }
}

function stubConfig(overrides: Record<string, unknown> = {}) {
  const config = defaultConfig(overrides)
  getModelConfigCachedMock.mockImplementation(async () => structuredClone(config))
  getModelConfigMock.mockImplementation(async () => structuredClone(config))
  updateModelConfigMock.mockImplementation(async (next) => next)
  return config
}

function stubVersions() {
  const active = versionInfo('active-id', '当前', true)
  const archived = versionInfo('archive-id', '归档')
  getModelConfigVersionsMock.mockResolvedValue({
    success: true,
    active_version: active,
    versions: [active, archived],
  } as never)
  return { active, archived }
}

async function renderLoadedHook() {
  const view = renderHook(() => useModelConfig())
  await waitFor(() => expect(view.result.current.loading).toBe(false))
  return view
}

function toastTitles() {
  return toastMock.mock.calls.map((call) => call[0]?.title)
}

function lastToast() {
  return toastMock.mock.calls.at(-1)?.[0]
}

beforeEach(() => {
  vi.spyOn(console, 'error').mockImplementation(() => {})
  stubConfig()
  stubVersions()
  getModelConfigSchemaMock.mockResolvedValue(taskConfigSchema() as never)
  updateModelConfigSectionMock.mockResolvedValue({} as never)
  createModelConfigVersionMock.mockResolvedValue(versionInfo('new-id', '午后快照') as never)
  switchModelConfigVersionMock.mockResolvedValue(versionInfo('archive-id', '归档', true) as never)
  deleteModelConfigVersionMock.mockResolvedValue(undefined)
  testProviderConnectionMock.mockResolvedValue({
    network_ok: true,
    api_key_valid: true,
    latency_ms: 12,
    error: null,
    http_status: 200,
  })
  testModelCapabilityMock.mockResolvedValue({
    success: true,
    model_name: 'chat',
    visual_tested: false,
    tool_call_ok: true,
    response: 'ok',
    reasoning: '',
    tool_calls: [],
    latency_ms: 1500,
    error: null,
    prompt_tokens: 1,
    completion_tokens: 1,
    total_tokens: 2,
  })
})

afterEach(() => {
  vi.useRealTimers()
})

describe('useModelConfig 加载与 unwrap', () => {
  it('解开 { config } 信封并写入模型 / 提供商 / 任务草稿', async () => {
    const inner = defaultConfig({
      models: [model('alpha'), model('beta', 'spare')],
      model_task_config: {
        replyer: { model_list: ['alpha'] },
        utils: { model_list: ['beta'] },
      },
    })
    getModelConfigCachedMock.mockResolvedValue({ success: true, config: inner } as never)

    const { result, unmount } = await renderLoadedHook()

    expect(result.current.models.map((item) => item.name)).toEqual(['alpha', 'beta'])
    expect(result.current.providers).toEqual(['main', 'spare'])
    expect(result.current.taskConfig?.replyer.model_list).toEqual(['alpha'])
    expect(result.current.taskConfigSchema?.fields.map((field) => field.name)).toContain('replyer')
    expect(result.current.activeConfigVersion?.id).toBe('active-id')
    expect(result.current.configVersions).toHaveLength(2)
    unmount()
  })

  it('缺失 models / api_providers / model_task_config 时回落到空草稿', async () => {
    getModelConfigCachedMock.mockResolvedValue({} as never)

    const { result, unmount } = await renderLoadedHook()

    expect(result.current.models).toEqual([])
    expect(result.current.providers).toEqual([])
    expect(result.current.apiProviders).toEqual([])
    expect(result.current.taskConfig).toBeNull()
    expect(result.current.invalidModelRefs).toEqual([])
    expect(result.current.emptyTasks).toEqual([])
    unmount()
  })

  it('缓存配置失败时按 Error / 非 Error 分别提示', async () => {
    getModelConfigCachedMock.mockRejectedValueOnce(new Error('后端超时'))
    const first = await renderLoadedHook()
    expect(lastToast()).toEqual(
      expect.objectContaining({
        title: '加载失败',
        description: '后端超时',
        variant: 'destructive',
      })
    )
    first.unmount()

    getModelConfigCachedMock.mockRejectedValueOnce('not-an-error')
    const second = await renderLoadedHook()
    expect(lastToast()).toEqual(
      expect.objectContaining({
        title: '加载失败',
        description: '加载模型配置失败',
        variant: 'destructive',
      })
    )
    second.unmount()
  })

  it('schema 结果为空时跳过任务 schema 解析', async () => {
    getModelConfigSchemaMock.mockResolvedValueOnce(null as never)

    const { result, unmount } = await renderLoadedHook()

    expect(result.current.taskConfigSchema).toBeNull()
    expect(result.current.models).toHaveLength(1)
    expect(result.current.loading).toBe(false)
    unmount()
  })

  it('schema 信封缺少 schema 字段时走 loadConfig catch，不阻断 finally', async () => {
    getModelConfigSchemaMock.mockResolvedValue({} as never)

    const { result, unmount } = await renderLoadedHook()

    expect(result.current.loading).toBe(false)
    expect(result.current.models.map((item) => item.name)).toEqual(['chat'])
    expect(console.error).toHaveBeenCalled()
    unmount()
  })

  it('副本列表加载失败时仍完成配置加载', async () => {
    getModelConfigVersionsMock.mockRejectedValue(new Error('副本服务不可用'))

    const { result, unmount } = await renderLoadedHook()

    expect(result.current.models).toHaveLength(1)
    expect(result.current.activeConfigVersion).toBeNull()
    expect(result.current.configVersions).toEqual([])
    expect(console.error).toHaveBeenCalled()
    unmount()
  })

  it('按 schema 标记必填空任务、跳过空 task，并收集无效模型引用', async () => {
    stubConfig({
      models: [model('chat')],
      model_task_config: {
        replyer: { model_list: [] },
        utils: { model_list: ['ghost', 'chat'] },
        embedding: { model_list: ['chat'] },
        memory: { model_list: [] },
        learner: null,
        emoji: { model_list: ['missing'] },
        hidden: { model_list: [] },
        note: { model_list: [] },
      },
    })

    const { result, unmount } = await renderLoadedHook()

    // memory/learner/emoji/voice 视为高级任务，空列表不进入 emptyTasks
    expect(result.current.emptyTasks).toEqual(['replyer'])
    expect(result.current.invalidModelRefs).toEqual([
      { taskName: 'utils', invalidModels: ['ghost'] },
      { taskName: 'emoji', invalidModels: ['missing'] },
    ])
    unmount()
  })
})

describe('useModelConfig 配置副本', () => {
  it('loadConfigVersions 成功写入列表，失败则 toast', async () => {
    const { result, unmount } = await renderLoadedHook()

    const nextActive = versionInfo('v2', '晚间', true)
    getModelConfigVersionsMock.mockResolvedValueOnce({
      success: true,
      active_version: nextActive,
      versions: [nextActive],
    } as never)

    await act(async () => {
      await result.current.loadConfigVersions()
    })
    expect(result.current.activeConfigVersion?.label).toBe('晚间')
    expect(result.current.versionsLoading).toBe(false)

    getModelConfigVersionsMock.mockRejectedValueOnce(new Error('列表拉取失败'))
    await act(async () => {
      await result.current.loadConfigVersions()
    })
    expect(lastToast()).toEqual(
      expect.objectContaining({
        title: '副本列表加载失败',
        description: '列表拉取失败',
        variant: 'destructive',
      })
    )
    expect(result.current.versionsLoading).toBe(false)
    unmount()
  })

  it('创建副本：无未保存改动直接创建，有改动则先整份写入', async () => {
    const { result, unmount } = await renderLoadedHook()

    await act(async () => {
      await result.current.handleCreateConfigVersion('午后快照')
    })
    expect(updateModelConfigMock).not.toHaveBeenCalled()
    expect(createModelConfigVersionMock).toHaveBeenCalledWith('午后快照')
    expect(lastToast()).toEqual(
      expect.objectContaining({
        title: '副本已创建',
        description: '已保存为「午后快照」',
      })
    )

    act(() => {
      result.current.updateTaskConfig('replyer', 'temperature', 0.8)
    })
    await waitFor(() => expect(result.current.hasUnsavedChanges).toBe(true))

    createModelConfigVersionMock.mockResolvedValueOnce(versionInfo('v3', '删除后') as never)
    await act(async () => {
      await result.current.handleCreateConfigVersion('删除后')
    })
    expect(updateModelConfigMock).toHaveBeenCalled()
    expect(createModelConfigVersionMock).toHaveBeenLastCalledWith('删除后')
    unmount()
  })

  it('有未保存改动时创建/切换副本若整份写入失败则中止', async () => {
    const { result, unmount } = await renderLoadedHook()
    act(() => {
      result.current.updateTaskConfig('replyer', 'temperature', 0.1)
    })
    await waitFor(() => expect(result.current.hasUnsavedChanges).toBe(true))

    updateModelConfigMock.mockRejectedValueOnce(new Error('落盘失败'))
    await act(async () => {
      await result.current.handleCreateConfigVersion('不会建成')
    })
    expect(createModelConfigVersionMock).not.toHaveBeenCalled()
    expect(lastToast()).toEqual(
      expect.objectContaining({
        title: '创建副本失败',
        description: '落盘失败',
        variant: 'destructive',
      })
    )

    updateModelConfigMock.mockRejectedValueOnce(new Error('切换前落盘失败'))
    await act(async () => {
      await result.current.handleSwitchConfigVersion('archive-id')
    })
    expect(switchModelConfigVersionMock).not.toHaveBeenCalled()
    expect(lastToast()).toEqual(
      expect.objectContaining({
        title: '切换副本失败',
        description: '切换前落盘失败',
        variant: 'destructive',
      })
    )
    unmount()
  })

  it('创建副本失败时提示错误', async () => {
    const { result, unmount } = await renderLoadedHook()
    createModelConfigVersionMock.mockRejectedValueOnce(new Error('磁盘已满'))

    await act(async () => {
      await result.current.handleCreateConfigVersion('失败快照')
    })

    expect(lastToast()).toEqual(
      expect.objectContaining({
        title: '创建副本失败',
        description: '磁盘已满',
        variant: 'destructive',
      })
    )
    expect(result.current.creatingConfigVersion).toBe(false)
    unmount()
  })

  it('切换副本：忽略空 / active，无改动直接切换，有改动先持久化', async () => {
    const { result, unmount } = await renderLoadedHook()

    await act(async () => {
      await result.current.handleSwitchConfigVersion('')
      await result.current.handleSwitchConfigVersion('active')
    })
    expect(switchModelConfigVersionMock).not.toHaveBeenCalled()

    await act(async () => {
      await result.current.handleSwitchConfigVersion('archive-id')
    })
    expect(updateModelConfigMock).not.toHaveBeenCalled()
    expect(switchModelConfigVersionMock).toHaveBeenCalledWith('archive-id')
    expect(lastToast()).toEqual(expect.objectContaining({ title: '副本已切换' }))

    act(() => {
      result.current.updateTaskConfig('replyer', 'temperature', 0.6)
    })
    await waitFor(() => expect(result.current.hasUnsavedChanges).toBe(true))

    await act(async () => {
      await result.current.handleSwitchConfigVersion('archive-id')
    })
    expect(updateModelConfigMock).toHaveBeenCalled()
    unmount()
  })

  it('切换副本失败时提示错误', async () => {
    const { result, unmount } = await renderLoadedHook()
    switchModelConfigVersionMock.mockRejectedValueOnce(new Error('副本损坏'))

    await act(async () => {
      await result.current.handleSwitchConfigVersion('archive-id')
    })

    expect(lastToast()).toEqual(
      expect.objectContaining({
        title: '切换副本失败',
        description: '副本损坏',
        variant: 'destructive',
      })
    )
    expect(result.current.switchingConfigVersion).toBeNull()
    unmount()
  })

  it('删除副本成功与失败', async () => {
    const { result, unmount } = await renderLoadedHook()

    await act(async () => {
      await result.current.handleDeleteConfigVersion('archive-id')
    })
    expect(deleteModelConfigVersionMock).toHaveBeenCalledWith('archive-id')
    expect(lastToast()).toEqual(expect.objectContaining({ title: '副本已删除' }))

    deleteModelConfigVersionMock.mockRejectedValueOnce(new Error('正在使用'))
    await act(async () => {
      await result.current.handleDeleteConfigVersion('archive-id')
    })
    expect(lastToast()).toEqual(
      expect.objectContaining({
        title: '删除副本失败',
        description: '正在使用',
        variant: 'destructive',
      })
    )
    unmount()
  })
})

describe('useModelConfig 任务配置与 embedding', () => {
  it('一键清理无效引用，并保留没有 model_list 的任务', async () => {
    stubConfig({
      model_task_config: {
        replyer: { model_list: ['chat', 'ghost'], temperature: 0.4 },
        utils: { temperature: 0.1 },
        learner: null,
      },
    })
    const { result, unmount } = await renderLoadedHook()
    expect(result.current.invalidModelRefs).toEqual([
      { taskName: 'replyer', invalidModels: ['ghost'] },
    ])

    act(() => {
      result.current.handleRemoveInvalidRefs()
    })

    expect(result.current.taskConfig?.replyer).toEqual({
      model_list: ['chat'],
      temperature: 0.4,
    })
    expect(result.current.taskConfig?.utils).toEqual({ temperature: 0.1 })
    expect(result.current.taskConfig?.learner).toBeNull()
    expect(result.current.invalidModelRefs).toEqual([])
    expect(lastToast()).toEqual(expect.objectContaining({ title: '清理完成' }))
    unmount()
  })

  it('taskConfig 为空时清理无效引用直接返回', async () => {
    stubConfig({ model_task_config: null })
    const { result, unmount } = await renderLoadedHook()

    act(() => {
      result.current.handleRemoveInvalidRefs()
    })

    expect(result.current.taskConfig).toBeNull()
    expect(toastTitles()).not.toContain('清理完成')
    unmount()
  })

  it('updateTaskConfig 写入字段并重算空任务；空草稿时直接返回', async () => {
    const { result, unmount } = await renderLoadedHook()

    act(() => {
      result.current.updateTaskConfig('replyer', 'temperature', 0.2)
    })
    expect(result.current.taskConfig?.replyer.temperature).toBe(0.2)
    act(() => {
      result.current.updateTaskConfig('replyer', 'model_list', [])
    })
    expect(result.current.emptyTasks).toContain('replyer')
    expect(result.current.taskConfig?.replyer.temperature).toBe(0.2)

    stubConfig({ model_task_config: null })
    const empty = await renderLoadedHook()
    act(() => {
      empty.result.current.updateTaskConfig('replyer', 'temperature', 0.9)
    })
    expect(empty.result.current.taskConfig).toBeNull()
    empty.unmount()
    unmount()
  })

  it('embedding 模型列表变更会被拦截，确认后写回并重检任务', async () => {
    stubConfig({
      models: [model('chat'), model('embed', 'spare')],
      model_task_config: {
        replyer: { model_list: ['chat'] },
        embedding: { model_list: ['chat'] },
      },
    })
    const { result, unmount } = await renderLoadedHook()

    act(() => {
      result.current.updateTaskConfig('embedding', 'model_list', ['embed'])
    })
    expect(result.current.taskConfig?.embedding.model_list).toEqual(['chat'])
    expect(result.current.embeddingWarning.isOpen).toBe(true)

    await act(async () => {
      await result.current.embeddingWarning.confirm()
    })
    expect(result.current.taskConfig?.embedding.model_list).toEqual(['embed'])
    expect(lastToast()).toEqual(expect.objectContaining({ title: '嵌入模型已更新' }))

    act(() => {
      result.current.updateTaskConfig('embedding', 'model_list', ['embed'])
    })
    expect(result.current.embeddingWarning.isOpen).toBe(false)

    act(() => {
      result.current.updateTaskConfig('embedding', 'model_list', ['chat'])
    })
    expect(result.current.embeddingWarning.isOpen).toBe(true)
    act(() => {
      result.current.embeddingWarning.setOpen(false)
    })
    expect(result.current.taskConfig?.embedding.model_list).toEqual(['embed'])
    expect(result.current.embeddingWarning.isOpen).toBe(false)
    unmount()
  })

  it('isModelUsed 在无任务配置、未引用、已引用时分别返回', async () => {
    const { result, unmount } = await renderLoadedHook()
    expect(result.current.isModelUsed('chat')).toBe(true)
    expect(result.current.isModelUsed('ghost')).toBe(false)

    stubConfig({ model_task_config: null })
    const empty = await renderLoadedHook()
    expect(empty.result.current.isModelUsed('chat')).toBe(false)
    empty.unmount()
    unmount()
  })
})

describe('useModelConfig 模型编辑与校验', () => {
  it('打开编辑框：沿用已有模型，新增时按 DeepSeek 模板打开 cache', async () => {
    stubConfig({
      api_providers: [provider('deepseek'), provider('spare')],
    })
    const { result, unmount } = await renderLoadedHook()
    const onOpened = vi.fn()

    act(() => {
      result.current.openEditDialog(result.current.models[0], 0, onOpened)
    })
    expect(onOpened).toHaveBeenCalledOnce()
    expect(result.current.editingIndex).toBe(0)
    expect(result.current.editingModel?.name).toBe('chat')
    expect(result.current.isDeepSeekTemplateProvider('deepseek')).toBe(true)
    expect(result.current.isDeepSeekTemplateProvider('spare')).toBe(false)
    expect(result.current.isDeepSeekTemplateProvider('missing')).toBe(false)
    expect(result.current.getProviderConfig('deepseek')?.base_url).toBe('https://api.deepseek.com')
    expect(result.current.getProviderConfig('nope')).toBeUndefined()

    act(() => {
      result.current.openEditDialog(null, null)
    })
    expect(result.current.editingIndex).toBeNull()
    expect(result.current.editingModel).toEqual(
      expect.objectContaining({
        name: '',
        api_provider: 'deepseek',
        cache: true,
      })
    )
    unmount()
  })

  it('没有提供商时新增模型的默认 api_provider 为空字符串', async () => {
    stubConfig({ api_providers: [] })
    const { result, unmount } = await renderLoadedHook()

    act(() => {
      result.current.openEditDialog(null, null)
    })
    expect(result.current.editingModel?.api_provider).toBe('')
    unmount()
  })

  it('保存前校验空名称、重复名称、缺失提供商和标识符', async () => {
    const { result, unmount } = await renderLoadedHook()

    await act(async () => {
      await result.current.handleSaveEdit()
    })
    expect(updateModelConfigMock).not.toHaveBeenCalled()

    act(() => {
      result.current.openEditDialog(null, null)
      result.current.setEditingModel({
        name: '   ',
        model_identifier: '',
        api_provider: '',
        price_in: 0,
        price_out: 0,
      })
    })
    await act(async () => {
      await result.current.handleSaveEdit()
    })
    expect(result.current.formErrors).toEqual({
      name: '请输入模型名称',
      api_provider: '请选择 API 提供商',
      model_identifier: '请输入模型标识符',
    })

    act(() => {
      result.current.setEditingModel({
        name: 'CHAT',
        model_identifier: 'new-id',
        api_provider: 'main',
        price_in: 0,
        price_out: 0,
      })
    })
    await act(async () => {
      await result.current.handleSaveEdit()
    })
    expect(result.current.formErrors.name).toBe('模型名称已存在，请使用其他名称')

    act(() => {
      result.current.openEditDialog(result.current.models[0], 0)
    })
    expect(result.current.formErrors).toEqual({})
    await act(async () => {
      await result.current.handleSaveEdit()
    })
    expect(result.current.editDialogOpen).toBe(false)
    expect(lastToast()).toEqual(expect.objectContaining({ title: '模型已更新' }))
    unmount()
  })

  it('新增模型写入可选字段；更新时重命名会同步任务引用', async () => {
    const { result, unmount } = await renderLoadedHook()

    act(() => {
      result.current.openEditDialog(null, null)
      result.current.setEditingModel({
        name: 'vision',
        model_identifier: 'vision-id',
        api_provider: 'spare',
        price_in: null,
        price_out: null,
        temperature: 0.3,
        max_tokens: 256,
        extra_params: { top_p: 0.9 },
      })
    })
    await act(async () => {
      await result.current.handleSaveEdit()
    })
    expect(result.current.models.map((item) => item.name)).toEqual(['chat', 'vision'])
    expect(updateModelConfigMock).toHaveBeenCalledWith(
      expect.objectContaining({
        models: expect.arrayContaining([
          expect.objectContaining({
            name: 'vision',
            temperature: 0.3,
            max_tokens: 256,
            price_in: 0,
            extra_params: { top_p: 0.9 },
          }),
        ]),
      })
    )
    expect(lastToast()).toEqual(expect.objectContaining({ title: '模型已添加' }))

    act(() => {
      result.current.openEditDialog(result.current.models[0], 0)
      result.current.setEditingModel({
        ...result.current.models[0],
        name: 'chat-renamed',
        temperature: null,
        max_tokens: null,
      })
    })
    await act(async () => {
      await result.current.handleSaveEdit()
    })
    expect(result.current.models[0].name).toBe('chat-renamed')
    expect(result.current.taskConfig?.replyer.model_list).toEqual(['chat-renamed'])
    expect(result.current.taskConfig?.embedding.model_list).toEqual(['chat-renamed'])
    expect(lastToast()).toEqual(
      expect.objectContaining({
        title: '模型已更新',
        description: '模型名称及任务引用已同步保存',
      })
    )
    unmount()
  })

  it('没有任务配置时重命名模型仍只保存模型列表', async () => {
    stubConfig({ model_task_config: null })
    const { result, unmount } = await renderLoadedHook()

    act(() => {
      result.current.openEditDialog(result.current.models[0], 0)
      result.current.setEditingModel({
        ...result.current.models[0],
        name: 'solo',
      })
    })
    await act(async () => {
      await result.current.handleSaveEdit()
    })

    expect(result.current.models[0].name).toBe('solo')
    expect(result.current.taskConfig).toBeNull()
    expect(updateModelConfigMock).toHaveBeenCalledWith(
      expect.objectContaining({
        model_task_config: null,
        models: [expect.objectContaining({ name: 'solo' })],
      })
    )
    unmount()
  })

  it('保存模型时整份写入失败会 toast 且保持对话框打开', async () => {
    const { result, unmount } = await renderLoadedHook()
    updateModelConfigMock.mockRejectedValueOnce(new Error('校验失败'))

    act(() => {
      result.current.openEditDialog(result.current.models[0], 0)
    })
    await act(async () => {
      await result.current.handleSaveEdit()
    })

    expect(result.current.editDialogOpen).toBe(true)
    expect(lastToast()).toEqual(
      expect.objectContaining({
        title: '保存失败',
        description: '校验失败',
        variant: 'destructive',
      })
    )
    expect(result.current.saving).toBe(false)
    unmount()
  })

  it('关闭编辑框时把空价格填成 0；无草稿或重新打开不改价格策略', async () => {
    const { result, unmount } = await renderLoadedHook()

    act(() => {
      result.current.openEditDialog(
        { ...result.current.models[0], price_in: null, price_out: null },
        0
      )
    })
    act(() => {
      result.current.handleEditDialogClose(false)
    })
    expect(result.current.editDialogOpen).toBe(false)
    expect(result.current.editingModel?.price_in).toBe(0)
    expect(result.current.editingModel?.price_out).toBe(0)

    act(() => {
      result.current.handleEditDialogClose(true)
    })
    expect(result.current.editDialogOpen).toBe(true)

    act(() => {
      result.current.setEditingModel(null)
    })
    act(() => {
      result.current.handleEditDialogClose(false)
    })
    expect(result.current.editDialogOpen).toBe(false)
    expect(result.current.editingModel).toBeNull()
    unmount()
  })
})

describe('useModelConfig 删除、批量与分页搜索', () => {
  it('确认删除模型后重检任务；没有 deletingIndex 时只关对话框', async () => {
    const { result, unmount } = await renderLoadedHook()

    act(() => {
      result.current.handleConfirmDelete()
    })
    expect(result.current.models).toHaveLength(1)

    act(() => {
      result.current.openDeleteDialog(0)
    })
    expect(result.current.deletingIndex).toBe(0)
    act(() => {
      result.current.handleConfirmDelete()
    })
    expect(result.current.models).toEqual([])
    // 任务里仍挂着已删模型名，应记入无效引用而不是空任务
    expect(result.current.invalidModelRefs).toEqual([
      { taskName: 'replyer', invalidModels: ['chat'] },
      { taskName: 'embedding', invalidModels: ['chat'] },
    ])
    expect(lastToast()).toEqual(expect.objectContaining({ title: '删除成功' }))
    expect(result.current.deleteDialogOpen).toBe(false)
    unmount()
  })

  it('单选、按过滤结果全选/取消全选，以及空选择批量删除提示', async () => {
    stubConfig({
      models: [model('chat'), model('vision', 'spare'), model('tool', 'main')],
    })
    const { result, unmount } = await renderLoadedHook()

    act(() => {
      result.current.toggleModelSelection(1)
    })
    expect(result.current.selectedModels.has(1)).toBe(true)
    act(() => {
      result.current.toggleModelSelection(1)
    })
    expect(result.current.selectedModels.has(1)).toBe(false)

    act(() => {
      result.current.setSearchQuery('vision')
    })
    expect(result.current.searchQuery).toBe('vision')
    expect(result.current.filteredModels.map((item) => item.name)).toEqual(['vision'])
    act(() => {
      result.current.toggleSelectAll()
    })
    expect([...result.current.selectedModels]).toEqual([1])
    act(() => {
      result.current.toggleSelectAll()
    })
    expect(result.current.selectedModels.size).toBe(0)

    act(() => {
      result.current.openBatchDeleteDialog()
    })
    expect(lastToast()).toEqual(
      expect.objectContaining({
        title: '提示',
        description: '请先选择要删除的模型',
      })
    )
    unmount()
  })

  it('批量删除选中模型并重检任务配置', async () => {
    stubConfig({
      models: [model('chat'), model('vision', 'spare')],
      model_task_config: {
        replyer: { model_list: ['chat'] },
        utils: { model_list: ['vision'] },
      },
    })
    const { result, unmount } = await renderLoadedHook()

    act(() => {
      result.current.toggleModelSelection(0)
    })
    act(() => {
      result.current.openBatchDeleteDialog()
    })
    expect(result.current.batchDeleteDialogOpen).toBe(true)

    act(() => {
      result.current.handleConfirmBatchDelete()
    })
    expect(result.current.models.map((item) => item.name)).toEqual(['vision'])
    expect(result.current.selectedModels.size).toBe(0)
    expect(result.current.invalidModelRefs).toEqual([
      { taskName: 'replyer', invalidModels: ['chat'] },
    ])
    expect(lastToast()).toEqual(
      expect.objectContaining({
        title: '批量删除成功',
        description: '已删除 1 个模型，配置将在 2 秒后自动保存',
      })
    )
    unmount()
  })

  it('搜索与提供商过滤会重置页码和选择，页码跳转校验边界', async () => {
    stubConfig({
      models: [
        model('alpha', 'main'),
        model('beta', 'spare'),
        model('gamma', 'main'),
        model('delta', 'spare'),
      ],
    })
    const { result, unmount } = await renderLoadedHook()

    act(() => {
      result.current.setPageSize(1)
      result.current.setPage(3)
      result.current.toggleModelSelection(0)
      result.current.setSearchQuery('lph')
    })
    expect(result.current.page).toBe(1)
    expect(result.current.selectedModels.size).toBe(0)
    expect(result.current.filteredModels.map((item) => item.name)).toEqual(['alpha'])

    act(() => {
      result.current.setSearchQuery('')
      result.current.setModelProviderFilter('main')
    })
    expect(result.current.page).toBe(1)
    expect(result.current.filteredModels.map((item) => item.name)).toEqual(['alpha', 'gamma'])
    expect(result.current.paginatedModels.map((item) => item.name)).toEqual(['alpha'])

    act(() => {
      result.current.setPage(2)
    })
    expect(result.current.paginatedModels.map((item) => item.name)).toEqual(['gamma'])

    act(() => {
      result.current.setJumpToPage('1')
    })
    act(() => {
      result.current.handleJumpToPage()
    })
    expect(result.current.page).toBe(1)
    expect(result.current.jumpToPage).toBe('')

    act(() => {
      result.current.setJumpToPage('2')
    })
    act(() => {
      result.current.handleJumpToPage()
    })
    expect(result.current.page).toBe(2)
    expect(result.current.paginatedModels.map((item) => item.name)).toEqual(['gamma'])

    act(() => {
      result.current.setJumpToPage('0')
    })
    act(() => {
      result.current.handleJumpToPage()
    })
    act(() => {
      result.current.setJumpToPage('9')
    })
    act(() => {
      result.current.handleJumpToPage()
    })
    act(() => {
      result.current.setJumpToPage('abc')
    })
    act(() => {
      result.current.handleJumpToPage()
    })
    expect(result.current.page).toBe(2)
    expect(result.current.jumpToPage).toBe('abc')
    unmount()
  })
})

describe('useModelConfig 提供商编辑与级联删除', () => {
  it('打开提供商对话框：新增给默认值，编辑回填当前项', async () => {
    const { result, unmount } = await renderLoadedHook()

    act(() => {
      result.current.openProviderDialog(null, null)
    })
    expect(result.current.providerDialogOpen).toBe(true)
    expect(result.current.editingProviderIndex).toBeNull()
    expect(result.current.editingProvider).toEqual(
      expect.objectContaining({
        name: '',
        client_type: 'openai',
        max_retry: 2,
        timeout: 30,
        retry_interval: 10,
      })
    )

    act(() => {
      result.current.openProviderDialog(result.current.apiProviders[1], 1)
    })
    expect(result.current.editingProviderIndex).toBe(1)
    expect(result.current.editingProvider?.name).toBe('spare')
    unmount()
  })

  it('保存提供商：新增与更新都会整份写入', async () => {
    const { result, unmount } = await renderLoadedHook()

    await act(async () => {
      await result.current.handleSaveProviderEdit(
        {
          name: 'extra',
          base_url: 'https://extra.example.com/v1',
          api_key: 'key-extra',
          client_type: 'openai',
          max_retry: null,
          timeout: null,
          retry_interval: null,
        },
        null
      )
    })
    expect(result.current.apiProviders.map((item) => item.name)).toEqual([
      'main',
      'spare',
      'extra',
    ])
    expect(lastToast()).toEqual(expect.objectContaining({ title: '提供商已添加' }))

    await act(async () => {
      await result.current.handleSaveProviderEdit(
        {
          ...result.current.apiProviders[2],
          api_key: 'rotated',
        },
        2
      )
    })
    expect(result.current.apiProviders[2].api_key).toBe('rotated')
    expect(lastToast()).toEqual(expect.objectContaining({ title: '提供商已更新' }))
    unmount()
  })

  it('重命名仍被模型引用的提供商时中止保存并打开级联确认', async () => {
    const { result, unmount } = await renderLoadedHook()

    await act(async () => {
      await result.current.handleSaveProviderEdit(
        {
          ...result.current.apiProviders[0],
          name: 'renamed-main',
        },
        0
      )
    })

    expect(updateModelConfigMock).not.toHaveBeenCalled()
    expect(result.current.providerDialogOpen).toBe(false)
    expect(result.current.editingProvider).toBeNull()
    expect(result.current.deleteConfirmState.isOpen).toBe(true)
    expect(result.current.deleteConfirmState.context).toBe('manual')
    expect(result.current.deleteConfirmState.providersToDelete).toEqual(['main'])
    expect(result.current.deleteConfirmState.affectedModels.map((item) => item.name)).toEqual([
      'chat',
    ])
    unmount()
  })

  it('保存提供商失败时 toast', async () => {
    const { result, unmount } = await renderLoadedHook()
    updateModelConfigMock.mockRejectedValueOnce(new Error('提供商节写入失败'))

    await act(async () => {
      await result.current.handleSaveProviderEdit(
        {
          name: 'extra',
          base_url: 'https://extra.example.com/v1',
          api_key: 'key-extra',
          client_type: 'openai',
          max_retry: 2,
          timeout: 30,
          retry_interval: 10,
        },
        null
      )
    })

    expect(lastToast()).toEqual(
      expect.objectContaining({
        title: '保存失败',
        description: '提供商节写入失败',
        variant: 'destructive',
      })
    )
    expect(result.current.saving).toBe(false)
    unmount()
  })

  it('删除未被引用的提供商直接改列表；被引用时只打开级联确认', async () => {
    const { result, unmount } = await renderLoadedHook()

    act(() => {
      result.current.handleConfirmProviderDelete()
    })
    expect(result.current.apiProviders).toHaveLength(2)

    act(() => {
      result.current.openProviderDeleteDialog(1)
    })
    await act(async () => {
      await result.current.handleConfirmProviderDelete()
    })
    expect(result.current.apiProviders.map((item) => item.name)).toEqual(['main'])
    expect(lastToast()).toEqual(
      expect.objectContaining({
        title: '删除成功',
        description: '提供商已从列表中移除',
      })
    )

    act(() => {
      result.current.openProviderDeleteDialog(0)
    })
    await act(async () => {
      await result.current.handleConfirmProviderDelete()
    })
    expect(result.current.apiProviders.map((item) => item.name)).toEqual(['main'])
    expect(result.current.deleteConfirmState.isOpen).toBe(true)
    expect(result.current.deleteConfirmState.affectedModels.map((item) => item.name)).toEqual([
      'chat',
    ])
    unmount()
  })

  it('默认 auto 上下文确认级联删除会走 provider 保存计数', async () => {
    const persist = createDeferred<Record<string, unknown>>()
    updateModelConfigMock.mockReturnValueOnce(persist.promise)
    const { result, unmount } = await renderLoadedHook()

    let pending: Promise<void> = Promise.resolve()
    act(() => {
      pending = result.current.handleConfirmDeleteProviderImpact()
    })
    await waitFor(() => expect(result.current.autoSaving).toBe(true))
    expect(result.current.saving).toBe(false)

    await act(async () => {
      persist.resolve({})
      await pending
    })
    expect(result.current.autoSaving).toBe(false)
    expect(result.current.apiProviders).toEqual([])
    expect(lastToast()).toEqual(
      expect.objectContaining({
        title: '删除成功',
        description: '已删除 0 个提供商和 0 个关联模型',
      })
    )
    unmount()
  })

  it('auto 上下文级联删除失败会 toast', async () => {
    const { result, unmount } = await renderLoadedHook()
    updateModelConfigMock.mockRejectedValueOnce(new Error('级联写入失败'))

    await act(async () => {
      await result.current.handleConfirmDeleteProviderImpact()
    })

    expect(lastToast()).toEqual(
      expect.objectContaining({
        title: '删除失败',
        description: '级联写入失败',
        variant: 'destructive',
      })
    )
    expect(result.current.autoSaving).toBe(false)
    unmount()
  })

  it('手动确认级联删除会同时移除关联模型和任务引用', async () => {
    stubConfig({
      models: [model('chat'), model('helper', 'spare')],
      model_task_config: {
        replyer: { model_list: ['chat', 'helper'] },
        utils: { model_list: ['chat'] },
        learner: null,
      },
    })
    const { result, unmount } = await renderLoadedHook()

    act(() => {
      result.current.openProviderDeleteDialog(0)
    })
    await act(async () => {
      await result.current.handleConfirmProviderDelete()
    })

    const persist = createDeferred<Record<string, unknown>>()
    updateModelConfigMock.mockReturnValueOnce(persist.promise)
    let pending: Promise<void> = Promise.resolve()
    act(() => {
      pending = result.current.handleConfirmDeleteProviderImpact()
    })
    await waitFor(() => expect(result.current.saving).toBe(true))

    await act(async () => {
      persist.resolve({})
      await pending
    })

    expect(result.current.saving).toBe(false)
    expect(result.current.apiProviders.map((item) => item.name)).toEqual(['spare'])
    expect(result.current.models.map((item) => item.name)).toEqual(['helper'])
    expect(result.current.taskConfig?.replyer.model_list).toEqual(['helper'])
    expect(result.current.taskConfig?.utils.model_list).toEqual([])
    expect(result.current.deleteConfirmState.isOpen).toBe(false)
    expect(lastToast()).toEqual(
      expect.objectContaining({
        title: '删除成功',
        description: '已删除 1 个提供商和 1 个关联模型',
      })
    )
    unmount()
  })

  it('无任务配置时确认级联删除只删模型', async () => {
    stubConfig({
      models: [model('chat')],
      model_task_config: null,
    })
    const { result, unmount } = await renderLoadedHook()

    act(() => {
      result.current.openProviderDeleteDialog(0)
    })
    await act(async () => {
      await result.current.handleConfirmProviderDelete()
    })
    await act(async () => {
      await result.current.handleConfirmDeleteProviderImpact()
    })

    expect(result.current.models).toEqual([])
    expect(result.current.taskConfig).toBeNull()
    expect(result.current.apiProviders.map((item) => item.name)).toEqual(['spare'])
    unmount()
  })

  it('手动级联删除失败保持确认框并 toast', async () => {
    const { result, unmount } = await renderLoadedHook()
    act(() => {
      result.current.openProviderDeleteDialog(0)
    })
    await act(async () => {
      await result.current.handleConfirmProviderDelete()
    })
    updateModelConfigMock.mockRejectedValueOnce(new Error('级联保存失败'))

    await act(async () => {
      await result.current.handleConfirmDeleteProviderImpact()
    })

    expect(result.current.deleteConfirmState.isOpen).toBe(true)
    expect(result.current.saving).toBe(false)
    expect(lastToast()).toEqual(
      expect.objectContaining({
        title: '删除失败',
        description: '级联保存失败',
        variant: 'destructive',
      })
    )
    unmount()
  })

  it('取消级联删除时本地列表尚未变成 pending，只清确认态', async () => {
    const { result, unmount } = await renderLoadedHook()
    act(() => {
      result.current.openProviderDeleteDialog(0)
    })
    await act(async () => {
      await result.current.handleConfirmProviderDelete()
    })

    act(() => {
      result.current.handleCancelDeleteProviderImpact()
    })

    expect(result.current.apiProviders.map((item) => item.name)).toEqual(['main', 'spare'])
    expect(result.current.deleteConfirmState.isOpen).toBe(false)
    expect(result.current.deleteConfirmState.oldProviders).toEqual([])
    unmount()
  })

  it('提供商自动保存失败时 toast 并保持未保存', async () => {
    const { result, unmount } = await renderLoadedHook()
    updateModelConfigSectionMock.mockRejectedValueOnce(new Error('自动写回失败'))
    vi.useFakeTimers()

    act(() => {
      result.current.openProviderDeleteDialog(1)
    })
    await act(async () => {
      await result.current.handleConfirmProviderDelete()
    })

    await act(async () => {
      vi.advanceTimersByTime(2000)
    })
    vi.useRealTimers()

    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({
          title: '自动保存失败',
          description: '自动写回失败',
          variant: 'destructive',
        })
      )
    )
    expect(result.current.hasUnsavedChanges).toBe(true)
    unmount()
  })
})

describe('useModelConfig 连接测试与手动保存', () => {
  it('提供商连接测试覆盖成功、Key 无效、网络失败与异常', async () => {
    const { result, unmount } = await renderLoadedHook()

    await act(async () => {
      await result.current.handleTestProviderConnection('main')
    })
    expect(lastToast()).toEqual(
      expect.objectContaining({
        title: '连接正常',
        description: 'main 可以访问 (12ms)',
      })
    )

    testProviderConnectionMock.mockResolvedValueOnce({
      network_ok: true,
      api_key_valid: null,
      latency_ms: 30,
      error: null,
      http_status: 200,
    })
    await act(async () => {
      await result.current.handleTestProviderConnection('main')
    })
    expect(lastToast()?.title).toBe('网络连接正常')

    testProviderConnectionMock.mockResolvedValueOnce({
      network_ok: true,
      api_key_valid: false,
      latency_ms: 40,
      error: 'key 被吊销',
      http_status: 401,
    })
    await act(async () => {
      await result.current.handleTestProviderConnection('main')
    })
    expect(lastToast()).toEqual(
      expect.objectContaining({
        title: '连接正常但 Key 无效',
        description: 'key 被吊销',
        variant: 'destructive',
      })
    )

    testProviderConnectionMock.mockResolvedValueOnce({
      network_ok: false,
      api_key_valid: false,
      latency_ms: null,
      error: null,
      http_status: null,
    })
    await act(async () => {
      await result.current.handleTestProviderConnection('spare')
    })
    expect(lastToast()).toEqual(
      expect.objectContaining({
        title: '连接失败',
        description: 'spare API Key 无效或无法连接',
        variant: 'destructive',
      })
    )

    testProviderConnectionMock.mockRejectedValueOnce(new Error('探测超时'))
    await act(async () => {
      await result.current.handleTestProviderConnection('main')
    })
    expect(lastToast()).toEqual(
      expect.objectContaining({
        title: '测试失败',
        description: '探测超时',
        variant: 'destructive',
      })
    )
    expect(result.current.testingProviders.size).toBe(0)
    unmount()
  })

  it('批量测试会顺序覆盖所有提供商，单个失败不中断后续', async () => {
    testProviderConnectionMock
      .mockRejectedValueOnce(new Error('main 失败'))
      .mockResolvedValueOnce({
        network_ok: true,
        api_key_valid: true,
        latency_ms: 8,
        error: null,
        http_status: 200,
      })
    const { result, unmount } = await renderLoadedHook()

    await act(async () => {
      await result.current.handleTestAllProviderConnections()
    })

    expect(testProviderConnectionMock).toHaveBeenCalledTimes(2)
    expect(testProviderConnectionMock).toHaveBeenNthCalledWith(1, 'main')
    expect(testProviderConnectionMock).toHaveBeenNthCalledWith(2, 'spare')
    expect(result.current.testResults.get('spare')?.latency_ms).toBe(8)
    unmount()
  })

  it('模型能力测试成功时可通过 toast action 打开详情', async () => {
    testModelCapabilityMock.mockResolvedValueOnce({
      success: true,
      model_name: 'chat',
      visual_tested: true,
      tool_call_ok: true,
      response: 'ok',
      reasoning: 'think',
      tool_calls: [],
      latency_ms: 2100,
      error: null,
      prompt_tokens: 1,
      completion_tokens: 1,
      total_tokens: 2,
    })
    const { result, unmount } = await renderLoadedHook()

    await act(async () => {
      await result.current.handleTestModelCapability('chat')
    })
    expect(lastToast()?.title).toBe('模型测试通过')
    expect(String(lastToast()?.description)).toContain('文本、视觉与工具调用测试 (2.10s)')

    const action = lastToast()?.action
    expect(isValidElement(action)).toBe(true)
    act(() => {
      ;(action as { props: { onClick: () => void } }).props.onClick()
    })
    expect(result.current.selectedModelTestResult?.model_name).toBe('chat')
    expect(result.current.selectedModelTestResult?.visual_tested).toBe(true)

    testModelCapabilityMock.mockResolvedValueOnce({
      success: true,
      model_name: 'chat',
      visual_tested: false,
      tool_call_ok: true,
      response: 'ok',
      reasoning: '',
      tool_calls: [],
      latency_ms: null,
      error: null,
      prompt_tokens: 0,
      completion_tokens: 0,
      total_tokens: 0,
    })
    await act(async () => {
      await result.current.handleTestModelCapability('chat')
    })
    expect(String(lastToast()?.description)).toContain('文本与工具调用测试 (-)')
    unmount()
  })

  it('模型能力测试失败区分工具调用，异常则 toast', async () => {
    const { result, unmount } = await renderLoadedHook()

    testModelCapabilityMock.mockResolvedValueOnce({
      success: false,
      model_name: 'chat',
      visual_tested: false,
      tool_call_ok: true,
      response: '',
      reasoning: '',
      tool_calls: [],
      latency_ms: 10,
      error: '空响应',
      prompt_tokens: 0,
      completion_tokens: 0,
      total_tokens: 0,
    })
    await act(async () => {
      await result.current.handleTestModelCapability('chat')
    })
    expect(lastToast()?.title).toBe('模型响应异常')

    testModelCapabilityMock.mockResolvedValueOnce({
      success: false,
      model_name: 'chat',
      visual_tested: false,
      tool_call_ok: false,
      response: '',
      reasoning: '',
      tool_calls: [],
      latency_ms: 10,
      error: null,
      prompt_tokens: 0,
      completion_tokens: 0,
      total_tokens: 0,
    })
    await act(async () => {
      await result.current.handleTestModelCapability('chat')
    })
    expect(lastToast()).toEqual(
      expect.objectContaining({
        title: '工具调用未通过',
        description: 'chat 未通过模型能力测试',
        variant: 'destructive',
      })
    )

    testModelCapabilityMock.mockRejectedValueOnce(new Error('网关 502'))
    await act(async () => {
      await result.current.handleTestModelCapability('chat')
    })
    expect(lastToast()).toEqual(
      expect.objectContaining({
        title: '模型测试失败',
        description: '网关 502',
        variant: 'destructive',
      })
    )
    expect(result.current.testingModels.size).toBe(0)
    unmount()
  })

  it('手动保存成功且屏障可回填时重新加载；写入失败 toast', async () => {
    const inner = defaultConfig()
    getModelConfigMock.mockResolvedValueOnce({ config: inner } as never)
    const { result, unmount } = await renderLoadedHook()
    const cachedCalls = getModelConfigCachedMock.mock.calls.length

    await act(async () => {
      await result.current.saveConfig()
    })
    expect(inner.models).toEqual(expect.any(Array))
    expect(updateModelConfigMock).toHaveBeenCalledWith(inner)
    expect(lastToast()).toEqual(expect.objectContaining({ title: '保存成功' }))
    expect(getModelConfigCachedMock.mock.calls.length).toBeGreaterThan(cachedCalls)

    updateModelConfigMock.mockRejectedValueOnce(new Error('整份保存失败'))
    await act(async () => {
      await result.current.saveConfig()
    })
    expect(lastToast()).toEqual(
      expect.objectContaining({
        title: '保存失败',
        description: '整份保存失败',
        variant: 'destructive',
      })
    )
    expect(result.current.saving).toBe(false)
    unmount()
  })

  it('保存期间又编辑模型时不会用过期草稿覆盖界面', async () => {
    const persistGet = createDeferred<Record<string, unknown>>()
    getModelConfigMock.mockReturnValueOnce(persistGet.promise)
    const { result, unmount } = await renderLoadedHook()
    const cachedCalls = getModelConfigCachedMock.mock.calls.length

    let savePromise: Promise<void> = Promise.resolve()
    act(() => {
      savePromise = result.current.saveConfig()
    })
    act(() => {
      result.current.openDeleteDialog(0)
    })
    act(() => {
      result.current.handleConfirmDelete()
    })

    await act(async () => {
      persistGet.resolve(defaultConfig())
      await savePromise
    })

    expect(result.current.models).toEqual([])
    expect(lastToast()?.title).toBe('保存成功')
    expect(getModelConfigCachedMock.mock.calls.length).toBe(cachedCalls)
    unmount()
  })
})
