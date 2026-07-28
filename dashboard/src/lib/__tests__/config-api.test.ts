import { beforeEach, describe, expect, it, vi } from 'vitest'

// 稳定的 mock：backendApi 方法与 ApiError 类在 vi.resetModules 后保持同一引用
const httpMocks = vi.hoisted(() => {
  class MockApiError extends Error {
    readonly status?: number
    readonly detail?: unknown

    constructor(message: string, options: { status?: number; detail?: unknown } = {}) {
      super(message)
      this.name = 'ApiError'
      this.status = options.status
      this.detail = options.detail
    }
  }

  return {
    ApiError: MockApiError,
    backendApi: {
      delete: vi.fn(),
      get: vi.fn(),
      patch: vi.fn(),
      post: vi.fn(),
    },
  }
})

vi.mock('@/lib/http', () => ({
  ApiError: httpMocks.ApiError,
  backendApi: httpMocks.backendApi,
}))

async function loadConfigApi() {
  return await import('../config-api')
}

describe('config-api', () => {
  beforeEach(() => {
    // 模块内有 schema / 配置数据缓存，逐用例重置模块获得干净状态
    vi.resetModules()
  })

  describe('响应解包', () => {
    it('getBotConfig 解包 config 字段', async () => {
      httpMocks.backendApi.get.mockResolvedValue({ config: { bot: { nickname: '麦麦' } } })
      const api = await loadConfigApi()

      await expect(api.getBotConfig()).resolves.toEqual({ bot: { nickname: '麦麦' } })
      expect(httpMocks.backendApi.get).toHaveBeenCalledWith('/api/webui/config/bot', {
        cache: 'no-store',
        errorMessage: '获取配置失败',
      })
    })

    it('getModelConfig 对无 config 包装的对象原样返回', async () => {
      httpMocks.backendApi.get.mockResolvedValue({ providers: [] })
      const api = await loadConfigApi()

      await expect(api.getModelConfig()).resolves.toEqual({ providers: [] })
    })

    it('响应不是对象时返回空对象', async () => {
      httpMocks.backendApi.get.mockResolvedValue('oops')
      const api = await loadConfigApi()

      await expect(api.getBotConfig()).resolves.toEqual({})
    })
  })

  describe('schema 缓存', () => {
    it('同一 schema 的并发/后续请求复用缓存 Promise', async () => {
      httpMocks.backendApi.get.mockResolvedValue({ sections: [] })
      const api = await loadConfigApi()

      await api.getBotConfigSchema()
      await api.getBotConfigSchema()
      await api.getConfigSectionSchema('bot')

      // bot schema 与 section schema 是不同缓存键，各请求一次
      expect(httpMocks.backendApi.get).toHaveBeenCalledTimes(2)
      expect(httpMocks.backendApi.get).toHaveBeenCalledWith('/api/webui/config/schema/bot', {
        cache: 'no-store',
        errorMessage: '获取配置架构失败',
      })
      expect(httpMocks.backendApi.get).toHaveBeenCalledWith(
        '/api/webui/config/schema/section/bot',
        { cache: 'no-store', errorMessage: '获取配置架构失败' }
      )
    })

    it('HTTP 层失败（带 status）时保留被拒绝的缓存，不重复请求', async () => {
      httpMocks.backendApi.get.mockRejectedValue(
        new httpMocks.ApiError('获取配置架构失败', { status: 500 })
      )
      const api = await loadConfigApi()

      await expect(api.getModelConfigSchema()).rejects.toThrow('获取配置架构失败')
      await expect(api.getModelConfigSchema()).rejects.toThrow('获取配置架构失败')
      expect(httpMocks.backendApi.get).toHaveBeenCalledTimes(1)
    })

    it('网络层瞬时失败（无 status）后剔除缓存并允许重试', async () => {
      httpMocks.backendApi.get
        .mockRejectedValueOnce(new httpMocks.ApiError('网络请求失败'))
        .mockResolvedValueOnce({ sections: [] })
      const api = await loadConfigApi()

      await expect(api.getBotConfigSchema()).rejects.toThrow('网络请求失败')
      await expect(api.getBotConfigSchema()).resolves.toEqual({ sections: [] })
      expect(httpMocks.backendApi.get).toHaveBeenCalledTimes(2)
    })
  })

  describe('配置数据缓存与失效', () => {
    it('getBotConfigCached 复用缓存，updateBotConfig 后失效并派发更新事件', async () => {
      httpMocks.backendApi.get.mockResolvedValue({ config: { bot: {} } })
      httpMocks.backendApi.post.mockResolvedValue({ success: true })
      const api = await loadConfigApi()

      await api.getBotConfigCached()
      await api.getBotConfigCached()
      expect(httpMocks.backendApi.get).toHaveBeenCalledTimes(1)

      const events: Event[] = []
      const handler = (event: Event) => {
        events.push(event)
      }
      window.addEventListener(api.BOT_CONFIG_UPDATED_EVENT, handler)

      try {
        await api.updateBotConfig({ bot: { nickname: '麦麦' } })
      } finally {
        window.removeEventListener(api.BOT_CONFIG_UPDATED_EVENT, handler)
      }

      expect(events).toHaveLength(1)
      expect(httpMocks.backendApi.post).toHaveBeenCalledWith('/api/webui/config/bot', {
        body: { bot: { nickname: '麦麦' } },
        errorMessage: '更新配置失败',
      })

      // 缓存已失效，再次读取会重新请求
      await api.getBotConfigCached()
      expect(httpMocks.backendApi.get).toHaveBeenCalledTimes(2)
    })

    it('updateModelConfig 只失效 model 缓存，不影响 bot 缓存', async () => {
      httpMocks.backendApi.get.mockResolvedValue({ config: {} })
      httpMocks.backendApi.post.mockResolvedValue({ success: true })
      const api = await loadConfigApi()

      await api.getBotConfigCached()
      await api.getModelConfigCached()
      expect(httpMocks.backendApi.get).toHaveBeenCalledTimes(2)

      await api.updateModelConfig({ providers: [] })

      await api.getBotConfigCached()
      await api.getModelConfigCached()
      // bot 命中缓存，model 重新请求
      expect(httpMocks.backendApi.get).toHaveBeenCalledTimes(3)
      expect(httpMocks.backendApi.get).toHaveBeenLastCalledWith('/api/webui/config/model', {
        cache: 'no-store',
        errorMessage: '获取配置失败',
      })
    })

    it('updateBotConfigSection 提交节数据并派发更新事件', async () => {
      httpMocks.backendApi.post.mockResolvedValue({ success: true })
      const api = await loadConfigApi()

      const events: Event[] = []
      const handler = (event: Event) => {
        events.push(event)
      }
      window.addEventListener(api.BOT_CONFIG_UPDATED_EVENT, handler)

      try {
        await api.updateBotConfigSection('personality', { style: '活泼' })
      } finally {
        window.removeEventListener(api.BOT_CONFIG_UPDATED_EVENT, handler)
      }

      expect(httpMocks.backendApi.post).toHaveBeenCalledWith(
        '/api/webui/config/bot/section/personality',
        { body: { style: '活泼' }, errorMessage: '更新配置失败' }
      )
      expect(events).toHaveLength(1)
    })
  })

  describe('模型配置副本', () => {
    const versionInfo = {
      id: 'v1',
      label: '备份一',
      created_at: 1,
      modified_at: 2,
      size: 3,
      active: false,
      inner_config_version: null,
      valid: true,
      error: null,
    }

    it('createModelConfigVersion 返回 version 字段', async () => {
      httpMocks.backendApi.post.mockResolvedValue({ version: versionInfo })
      const api = await loadConfigApi()

      await expect(api.createModelConfigVersion('备份一')).resolves.toEqual(versionInfo)
      expect(httpMocks.backendApi.post).toHaveBeenCalledWith('/api/webui/config/model/versions', {
        body: { label: '备份一' },
        errorMessage: '创建模型配置副本失败',
      })
    })

    it('switchModelConfigVersion 对副本 ID 做 URL 编码并归档当前配置', async () => {
      httpMocks.backendApi.post.mockResolvedValue({ version: versionInfo })
      const api = await loadConfigApi()

      await expect(api.switchModelConfigVersion('v 1')).resolves.toEqual(versionInfo)
      expect(httpMocks.backendApi.post).toHaveBeenCalledWith(
        '/api/webui/config/model/versions/v%201/activate',
        { body: { archive_current: true }, errorMessage: '切换模型配置副本失败' }
      )
    })

    it('updateModelConfigVersionLabel 通过 PATCH 更新名称', async () => {
      httpMocks.backendApi.patch.mockResolvedValue({ version: versionInfo })
      const api = await loadConfigApi()

      await expect(api.updateModelConfigVersionLabel('v1', '新名字')).resolves.toEqual(versionInfo)
      expect(httpMocks.backendApi.patch).toHaveBeenCalledWith(
        '/api/webui/config/model/versions/v1',
        { body: { label: '新名字' }, errorMessage: '更新模型配置副本失败' }
      )
    })

    it('deleteModelConfigVersion 调用 DELETE', async () => {
      httpMocks.backendApi.delete.mockResolvedValue(undefined)
      const api = await loadConfigApi()

      await api.deleteModelConfigVersion('v1')
      expect(httpMocks.backendApi.delete).toHaveBeenCalledWith(
        '/api/webui/config/model/versions/v1',
        { errorMessage: '删除模型配置副本失败' }
      )
    })
  })

  describe('模型列表与客户端类型', () => {
    it('fetchProviderModels 使用默认解析器与端点，并解包 models 字段', async () => {
      httpMocks.backendApi.get.mockResolvedValue({
        models: [{ id: 'm1', name: 'model-1' }],
      })
      const api = await loadConfigApi()

      await expect(api.fetchProviderModels('deepseek')).resolves.toEqual([
        { id: 'm1', name: 'model-1' },
      ])
      expect(httpMocks.backendApi.get).toHaveBeenCalledWith('/api/webui/models/list', {
        query: { provider_name: 'deepseek', parser: 'openai', endpoint: '/models' },
        errorMessage: '获取模型列表失败',
      })
    })

    it('fetchProviderModels 支持数组响应，非法响应返回空数组', async () => {
      httpMocks.backendApi.get.mockResolvedValueOnce([{ id: 'm2', name: 'model-2' }])
      const api = await loadConfigApi()
      await expect(api.fetchProviderModels('x', 'gemini', '/v1/models')).resolves.toEqual([
        { id: 'm2', name: 'model-2' },
      ])

      httpMocks.backendApi.get.mockResolvedValueOnce({ unexpected: true })
      await expect(api.fetchProviderModels('x')).resolves.toEqual([])
    })

    it('fetchModelClientTypes 解包 client_types 字段', async () => {
      const clientType = {
        client_type: 'openai',
        owner_plugin_id: null,
        version: '1.0.0',
        description: '内置',
        builtin: true,
      }
      httpMocks.backendApi.get.mockResolvedValue({ client_types: [clientType] })
      const api = await loadConfigApi()

      await expect(api.fetchModelClientTypes()).resolves.toEqual([clientType])
    })

    it('testProviderConnection / testModelCapability 透传后端结果', async () => {
      const connectionResult = {
        network_ok: true,
        api_key_valid: true,
        latency_ms: 12,
        error: null,
        http_status: 200,
      }
      httpMocks.backendApi.post.mockResolvedValue(connectionResult)
      const api = await loadConfigApi()

      await expect(api.testProviderConnection('deepseek')).resolves.toEqual(connectionResult)
      expect(httpMocks.backendApi.post).toHaveBeenCalledWith(
        '/api/webui/models/test-connection-by-name',
        { query: { provider_name: 'deepseek' }, errorMessage: '测试提供商连接失败' }
      )

      await api.testModelCapability('model-1')
      expect(httpMocks.backendApi.post).toHaveBeenLastCalledWith('/api/webui/models/test-model', {
        body: { model_name: 'model-1' },
        errorMessage: '测试模型能力失败',
      })
    })
  })
})
