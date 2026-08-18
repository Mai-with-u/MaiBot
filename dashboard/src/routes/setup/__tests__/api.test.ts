import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError, authApi, backendApi } from '@/lib/http'

import {
  completeSetup,
  loadApiProviderSetupConfig,
  loadBotBasicConfig,
  loadModelSetupConfig,
  loadPersonalityConfig,
  loadSetupStatus,
  saveApiProviderSetupConfig,
  saveBotBasicConfig,
  saveModelSetupConfig,
  savePersonalityConfig,
  updateAccessToken,
} from '../api'

vi.mock('@/lib/http', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/http')>()
  return {
    ...actual,
    authApi: {
      request: vi.fn(),
      get: vi.fn(),
      post: vi.fn(),
      put: vi.fn(),
      patch: vi.fn(),
      delete: vi.fn(),
    },
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

const authGetMock = vi.mocked(authApi.get)
const authPostMock = vi.mocked(authApi.post)
const getMock = vi.mocked(backendApi.get)
const postMock = vi.mocked(backendApi.post)

beforeEach(() => {
  authGetMock.mockReset()
  authPostMock.mockReset()
  getMock.mockReset()
  postMock.mockReset()
})

describe('loadSetupStatus', () => {
  it('通过认证实例读取设置状态（401 不跳转登录页）', async () => {
    const status = {
      is_first_setup: true,
      token_source: 'default',
      requires_custom_token: true,
    }
    authGetMock.mockResolvedValue(status)

    await expect(loadSetupStatus()).resolves.toBe(status)
    expect(authGetMock).toHaveBeenCalledWith('/api/webui/setup/status', {
      errorMessage: '读取设置状态失败',
    })
  })

  it('后端失败时向上抛出 ApiError', async () => {
    authGetMock.mockRejectedValue(new ApiError('读取设置状态失败', { status: 503 }))

    await expect(loadSetupStatus()).rejects.toMatchObject({ status: 503 })
  })
})

describe('loadBotBasicConfig', () => {
  it('读取 bot 段并去除 QQ 账号两端空白', async () => {
    getMock.mockResolvedValue({
      config: {
        bot: {
          platform: 'qq',
          qq_account: ' 12345 ',
          platforms: ['telegram:88'],
          nickname: '麦麦',
          alias_names: ['mai'],
        },
      },
    })

    await expect(loadBotBasicConfig()).resolves.toEqual({
      platform: 'qq',
      qq_account: '12345',
      platforms: ['telegram:88'],
      nickname: '麦麦',
      alias_names: ['mai'],
    })
    expect(getMock).toHaveBeenCalledWith('/api/webui/config/bot', {
      errorMessage: '读取 Bot 配置失败',
    })
  })

  it('缺少 platform 但填写了 QQ 账号时默认平台为 qq', async () => {
    getMock.mockResolvedValue({
      config: { bot: { qq_account: '10001', nickname: '麦麦' } },
    })

    await expect(loadBotBasicConfig()).resolves.toMatchObject({
      platform: 'qq',
      qq_account: '10001',
    })
  })

  it('配置缺少 bot 段时返回空默认值', async () => {
    getMock.mockResolvedValue({ config: {} })

    await expect(loadBotBasicConfig()).resolves.toEqual({
      platform: '',
      qq_account: '',
      platforms: [],
      nickname: '',
      alias_names: [],
    })
  })
})

describe('loadPersonalityConfig', () => {
  it('读取 personality 段并保留概率为 0 的显式配置', async () => {
    getMock.mockResolvedValue({
      config: {
        personality: {
          personality: '活泼开朗',
          reply_style: '简短口语',
          multiple_reply_style: ['风格A'],
          multiple_probability: 0,
        },
      },
    })

    await expect(loadPersonalityConfig()).resolves.toEqual({
      personality: '活泼开朗',
      reply_style: '简短口语',
      multiple_reply_style: ['风格A'],
      multiple_probability: 0,
    })
    expect(getMock).toHaveBeenCalledWith('/api/webui/config/bot', {
      errorMessage: '读取人格配置失败',
    })
  })

  it('配置缺少 personality 段时概率回退到 0.2', async () => {
    getMock.mockResolvedValue({ config: {} })

    await expect(loadPersonalityConfig()).resolves.toEqual({
      personality: '',
      reply_style: '',
      multiple_reply_style: [],
      multiple_probability: 0.2,
    })
  })
})

describe('loadApiProviderSetupConfig', () => {
  it('始终返回默认预设服务商配置', async () => {
    await expect(loadApiProviderSetupConfig()).resolves.toEqual({
      provider_name: 'DeepSeek',
      base_url: 'https://api.deepseek.com',
      api_key: '',
    })
    expect(getMock).not.toHaveBeenCalled()
  })
})

describe('loadModelSetupConfig', () => {
  it('读取 planner/replyer 模型的标识符、视觉与思考开关', async () => {
    getMock.mockResolvedValue({
      config: {
        models: [
          {
            model_identifier: 'deepseek-v4',
            name: 'planner-model',
            api_provider: 'DeepSeek',
            visual: true,
            extra_params: { thinking: { type: 'enabled' } },
          },
          {
            model_identifier: 'glm-5',
            name: 'replyer-model',
            api_provider: 'Zhipu',
            visual: false,
            extra_params: { enable_thinking: 'true' },
          },
        ],
        model_task_config: {
          planner: { model_list: ['planner-model'] },
          replyer: { model_list: ['replyer-model'] },
        },
      },
    })

    await expect(loadModelSetupConfig()).resolves.toEqual({
      planner_model_name: 'planner-model',
      planner_model_identifier: 'deepseek-v4',
      planner_visual: true,
      planner_thinking: true,
      replyer_model_name: 'replyer-model',
      replyer_model_identifier: 'glm-5',
      replyer_visual: false,
      replyer_thinking: true,
    })
  })

  it('无 extra_params 时按模型标识符推断思考开关，模型缺失时标识符回退到任务名', async () => {
    getMock.mockResolvedValue({
      config: {
        models: [
          {
            model_identifier: 'glm-5',
            name: 'planner-model',
            api_provider: 'Zhipu',
            extra_params: { enable_thinking: false },
          },
        ],
        model_task_config: {
          planner: { model_list: ['planner-model'] },
          // replyer 指向的模型不在 models 列表里
          replyer: { model_list: ['Deepseek-V4-Pro'] },
        },
      },
    })

    await expect(loadModelSetupConfig()).resolves.toMatchObject({
      planner_model_identifier: 'glm-5',
      planner_thinking: false,
      replyer_model_name: 'Deepseek-V4-Pro',
      // 模型不存在时标识符回退到任务里配置的名称
      replyer_model_identifier: 'Deepseek-V4-Pro',
      replyer_visual: false,
      // 标识符包含 deepseek-v4-pro（大小写不敏感）时推断为开启思考
      replyer_thinking: true,
    })
  })

  it('模型配置为空时返回空名称与关闭的开关', async () => {
    getMock.mockResolvedValue({ config: {} })

    await expect(loadModelSetupConfig()).resolves.toEqual({
      planner_model_name: '',
      planner_model_identifier: '',
      planner_visual: false,
      planner_thinking: false,
      replyer_model_name: '',
      replyer_model_identifier: '',
      replyer_visual: false,
      replyer_thinking: false,
    })
  })
})

describe('saveBotBasicConfig / savePersonalityConfig', () => {
  it('把 Bot 基础配置原样写入 bot 配置段', async () => {
    postMock.mockResolvedValue({ success: true })
    const config = {
      platform: 'qq',
      qq_account: '12345',
      platforms: [],
      nickname: '麦麦',
      alias_names: [],
    }

    await saveBotBasicConfig(config)

    expect(postMock).toHaveBeenCalledWith('/api/webui/config/bot/section/bot', {
      body: config,
      errorMessage: '保存 Bot 配置失败',
    })
  })

  it('把人格配置原样写入 personality 配置段', async () => {
    postMock.mockResolvedValue({ success: true })
    const config = {
      personality: '活泼开朗',
      reply_style: '简短口语',
      multiple_reply_style: [],
      multiple_probability: 0.2,
    }

    await savePersonalityConfig(config)

    expect(postMock).toHaveBeenCalledWith('/api/webui/config/bot/section/personality', {
      body: config,
      errorMessage: '保存人格配置失败',
    })
  })
})

describe('saveApiProviderSetupConfig', () => {
  it('新提供商去除空白后追加，并补齐默认连接参数', async () => {
    getMock.mockResolvedValue({
      config: {
        models: [{ model_identifier: 'glm-5', name: 'a', api_provider: 'Zhipu' }],
        api_providers: [],
      },
    })
    postMock.mockResolvedValue({ success: true })

    await saveApiProviderSetupConfig({
      provider_name: ' NewProvider ',
      base_url: ' https://new.example/v1 ',
      api_key: ' sk-new ',
    })

    expect(postMock).toHaveBeenCalledWith('/api/webui/config/model', {
      body: {
        models: [{ model_identifier: 'glm-5', name: 'a', api_provider: 'Zhipu' }],
        api_providers: [
          {
            name: 'NewProvider',
            base_url: 'https://new.example/v1',
            api_key: 'sk-new',
            client_type: 'openai',
            max_retry: 3,
            timeout: 120,
            retry_interval: 5,
          },
        ],
      },
      errorMessage: '保存 API 提供商配置失败',
    })
  })

  it('同名提供商原位更新且不影响其他提供商', async () => {
    getMock.mockResolvedValue({
      config: {
        api_providers: [
          { name: 'DeepSeek', base_url: 'https://old.example', api_key: 'sk-old' },
          { name: 'Zhipu', base_url: 'https://zhipu.example', api_key: 'sk-z' },
        ],
      },
    })
    postMock.mockResolvedValue({ success: true })

    await saveApiProviderSetupConfig({
      provider_name: 'DeepSeek',
      base_url: 'https://api.deepseek.com',
      api_key: 'sk-new',
    })

    const body = postMock.mock.calls[0][1]!.body as {
      api_providers: Array<Record<string, unknown>>
    }
    expect(body.api_providers).toEqual([
      {
        name: 'DeepSeek',
        base_url: 'https://api.deepseek.com',
        api_key: 'sk-new',
        client_type: 'openai',
        max_retry: 3,
        timeout: 120,
        retry_interval: 5,
      },
      { name: 'Zhipu', base_url: 'https://zhipu.example', api_key: 'sk-z' },
    ])
  })

  it('读取模型配置失败时直接抛出且不提交保存', async () => {
    getMock.mockRejectedValue(new ApiError('读取模型配置失败', { status: 500 }))

    await expect(
      saveApiProviderSetupConfig({ provider_name: 'X', base_url: 'https://x', api_key: 'k' })
    ).rejects.toBeInstanceOf(ApiError)
    expect(postMock).not.toHaveBeenCalled()
  })
})

describe('saveModelSetupConfig', () => {
  const setupConfig = {
    planner_model_name: '（表单里的名称会被标识符覆盖）',
    planner_model_identifier: ' deepseek-v4 ',
    planner_visual: true,
    planner_thinking: true,
    replyer_model_name: '',
    replyer_model_identifier: 'glm-5',
    replyer_visual: false,
    replyer_thinking: false,
  }

  it('空配置下新建 planner/replyer 模型并同步任务模型列表', async () => {
    getMock.mockResolvedValue({ config: {} })
    postMock.mockResolvedValue({ success: true })

    await saveModelSetupConfig(setupConfig, ' DeepSeek ')

    expect(postMock).toHaveBeenCalledWith('/api/webui/config/model', {
      body: {
        models: [
          {
            price_in: 0,
            cache: false,
            cache_price_in: 0,
            price_out: 0,
            force_stream_mode: false,
            // 开启思考：thinking enabled 且附带 reasoning_effort
            extra_params: { thinking: { type: 'enabled' }, reasoning_effort: 'high' },
            visual: true,
            model_identifier: 'deepseek-v4',
            name: 'deepseek-v4',
            api_provider: 'DeepSeek',
          },
          {
            price_in: 0,
            cache: false,
            cache_price_in: 0,
            price_out: 0,
            force_stream_mode: false,
            // 关闭思考：thinking disabled 且不携带 reasoning_effort
            extra_params: { thinking: { type: 'disabled' } },
            visual: false,
            model_identifier: 'glm-5',
            name: 'glm-5',
            api_provider: 'DeepSeek',
          },
        ],
        model_task_config: {
          planner: { model_list: ['deepseek-v4'] },
          replyer: { model_list: ['glm-5'] },
          utils: { model_list: ['deepseek-v4'] },
        },
      },
      errorMessage: '保存模型配置失败',
    })
  })

  it('已有同名模型时保留价格等既有字段并清理旧的 enable_thinking', async () => {
    getMock.mockResolvedValue({
      config: {
        models: [
          {
            model_identifier: 'deepseek-v4-old',
            name: 'deepseek-v4',
            api_provider: 'OldProvider',
            price_in: 2,
            price_out: 8,
            extra_params: { enable_thinking: true, temperature: 0.7 },
          },
        ],
        model_task_config: {
          planner: { model_list: ['deepseek-v4'], temperature: 0.3 },
          vlm: { model_list: ['vlm-model'] },
        },
      },
    })
    postMock.mockResolvedValue({ success: true })

    await saveModelSetupConfig(setupConfig, 'DeepSeek')

    const body = postMock.mock.calls[0][1]!.body as {
      models: Array<Record<string, unknown>>
      model_task_config: Record<string, unknown>
    }
    // 既有模型被原位更新：保留价格、清理 enable_thinking、保留其他 extra_params
    expect(body.models[0]).toMatchObject({
      model_identifier: 'deepseek-v4',
      name: 'deepseek-v4',
      api_provider: 'DeepSeek',
      price_in: 2,
      price_out: 8,
      extra_params: {
        temperature: 0.7,
        thinking: { type: 'enabled' },
        reasoning_effort: 'high',
      },
    })
    expect(body.models[0].extra_params).not.toHaveProperty('enable_thinking')
    // replyer 模型作为新模型追加
    expect(body.models[1]).toMatchObject({ name: 'glm-5', api_provider: 'DeepSeek' })
    // 任务配置：planner 保留既有字段并更新模型列表，vlm 等其他任务保持原样
    expect(body.model_task_config).toEqual({
      planner: { model_list: ['deepseek-v4'], temperature: 0.3 },
      replyer: { model_list: ['glm-5'] },
      utils: { model_list: ['deepseek-v4'] },
      vlm: { model_list: ['vlm-model'] },
    })
  })
})

describe('completeSetup', () => {
  it('调用标记设置完成接口', async () => {
    postMock.mockResolvedValue({ success: true })

    await completeSetup()

    expect(postMock).toHaveBeenCalledWith('/api/webui/setup/complete', {
      errorMessage: '标记设置完成失败',
    })
  })
})

describe('updateAccessToken', () => {
  it('通过认证实例提交新 Token', async () => {
    const result = { success: true, message: 'Token 已更新' }
    authPostMock.mockResolvedValue(result)

    await expect(updateAccessToken('new-token-123')).resolves.toBe(result)
    expect(authPostMock).toHaveBeenCalledWith('/api/webui/auth/update', {
      body: { new_token: 'new-token-123' },
      errorMessage: '更新 Token 失败',
    })
  })

  it('Token 不合法时向上抛出 ApiError', async () => {
    authPostMock.mockRejectedValue(new ApiError('更新 Token 失败', { status: 400 }))

    await expect(updateAccessToken('bad')).rejects.toMatchObject({ status: 400 })
  })
})
