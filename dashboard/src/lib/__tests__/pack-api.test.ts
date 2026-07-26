import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError, backendApi, statsApi } from '@/lib/http'

import {
  applyPack,
  checkPackLike,
  detectPackConflicts,
  getPack,
  getPackUserId,
  listPacks,
  recordPackDownload,
  togglePackLike,
} from '../pack-api'
import type {
  ApplyPackOptions,
  ModelPack,
  PackModel,
  PackProvider,
  PackTaskConfig,
} from '../pack-api'

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
    statsApi: {
      request: vi.fn(),
      get: vi.fn(),
      post: vi.fn(),
      put: vi.fn(),
      patch: vi.fn(),
      delete: vi.fn(),
    },
  }
})

const statsGetMock = vi.mocked(statsApi.get)
const statsPostMock = vi.mocked(statsApi.post)
const backendGetMock = vi.mocked(backendApi.get)
const backendPostMock = vi.mocked(backendApi.post)

beforeEach(() => {
  statsGetMock.mockReset()
  statsPostMock.mockReset()
  backendGetMock.mockReset()
  backendPostMock.mockReset()
})

// ============ 测试数据工厂 ============

/** 本地配置中的提供商条目（本地配置含 api_key，pack-api 未导出该类型，测试内自建） */
interface LocalProvider extends PackProvider {
  api_key?: string
}

/** /api/webui/config/model 返回的本地模型配置形状 */
interface LocalConfig {
  api_providers: LocalProvider[]
  models: PackModel[]
  model_task_config: Record<string, PackTaskConfig>
}

function makeProvider(overrides: Partial<PackProvider> = {}): PackProvider {
  return {
    name: '云端提供商',
    base_url: 'https://api.example.com/v1',
    client_type: 'openai',
    ...overrides,
  }
}

function makeModel(overrides: Partial<PackModel> = {}): PackModel {
  return {
    model_identifier: 'gpt-4o',
    name: 'gpt-4o',
    api_provider: '云端提供商',
    price_in: 1,
    price_out: 2,
    ...overrides,
  }
}

function makePack(overrides: Partial<ModelPack> = {}): ModelPack {
  return {
    id: 'pack-1',
    name: '测试配置包',
    description: '用于测试的配置包',
    author: '测试作者',
    version: '1.0.0',
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-02T00:00:00Z',
    status: 'approved',
    downloads: 10,
    likes: 5,
    providers: [],
    models: [],
    task_config: {},
    ...overrides,
  }
}

function makeLocalConfig(overrides: Partial<LocalConfig> = {}): LocalConfig {
  return {
    api_providers: [],
    models: [],
    model_task_config: {},
    ...overrides,
  }
}

/** 应用 Pack 的默认选项：三类内容全部关闭，按测试需要逐项打开 */
function makeApplyOptions(overrides: Partial<ApplyPackOptions> = {}): ApplyPackOptions {
  return {
    apply_providers: false,
    apply_models: false,
    apply_task_config: false,
    task_mode: 'replace',
    ...overrides,
  }
}

// ============ Pack 服务（statsApi）接口 ============

describe('listPacks', () => {
  it('把筛选参数透传为 query 并返回响应体', async () => {
    const response = { packs: [], total: 0, page: 2, page_size: 20, total_pages: 0 }
    statsGetMock.mockResolvedValue(response)

    await expect(
      listPacks({
        status: 'approved',
        page: 2,
        page_size: 20,
        search: '麦麦',
        sort_by: 'downloads',
        sort_order: 'desc',
      })
    ).resolves.toBe(response)

    expect(statsGetMock).toHaveBeenCalledWith('/pack', {
      query: {
        status: 'approved',
        page: 2,
        page_size: 20,
        search: '麦麦',
        sort_by: 'downloads',
        sort_order: 'desc',
      },
      errorMessage: '获取 Pack 列表失败',
    })
  })

  it('零值参数（page: 0、空 search）被归一化为 undefined，不出现在 query 里', async () => {
    statsGetMock.mockResolvedValue({ packs: [], total: 0, page: 1, page_size: 10, total_pages: 0 })

    await listPacks({ page: 0, page_size: 0, search: '' })

    const options = statsGetMock.mock.calls[0][1] as { query: Record<string, unknown> }
    expect(options.query.page).toBeUndefined()
    expect(options.query.page_size).toBeUndefined()
    expect(options.query.search).toBeUndefined()
  })

  it('不传参数时所有 query 值均为 undefined', async () => {
    statsGetMock.mockResolvedValue({ packs: [], total: 0, page: 1, page_size: 10, total_pages: 0 })

    await listPacks()

    // toHaveBeenCalledWith 的结构比较会忽略值为 undefined 的键
    expect(statsGetMock).toHaveBeenCalledWith('/pack', {
      query: {},
      errorMessage: '获取 Pack 列表失败',
    })
  })
})

describe('getPack', () => {
  it('成功时解包并返回 pack 字段', async () => {
    const pack = makePack({ id: 'pack-9' })
    statsGetMock.mockResolvedValue({ success: true, pack })

    await expect(getPack('pack-9')).resolves.toBe(pack)
    expect(statsGetMock).toHaveBeenCalledWith('/pack/pack-9', {
      errorMessage: '获取 Pack 失败',
    })
  })

  it('包络 success 为 false 时抛出携带业务错误文案与 detail 的 ApiError', async () => {
    const envelope = { success: false, error: 'Pack 不存在' }
    statsGetMock.mockResolvedValue(envelope)

    const error = await getPack('missing').catch((e: unknown) => e)
    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).message).toBe('Pack 不存在')
    expect((error as ApiError).detail).toBe(envelope)
  })

  it('包络失败且无 error 字段时使用默认文案', async () => {
    statsGetMock.mockResolvedValue({ success: false })

    await expect(getPack('missing')).rejects.toThrow('获取 Pack 失败')
  })
})

describe('recordPackDownload', () => {
  it('以 parse: response 提交下载计数请求体', async () => {
    statsPostMock.mockResolvedValue(new Response('ok'))

    await expect(recordPackDownload('pack-1', 'user-1')).resolves.toBeUndefined()
    expect(statsPostMock).toHaveBeenCalledWith('/pack/download', {
      body: { pack_id: 'pack-1', user_id: 'user-1' },
      parse: 'response',
      errorMessage: '记录 Pack 下载失败',
    })
  })

  it('HTTP 层失败（带 status 的 ApiError）被吞掉，不影响调用方', async () => {
    statsPostMock.mockRejectedValue(new ApiError('记录 Pack 下载失败', { status: 500 }))

    await expect(recordPackDownload('pack-1')).resolves.toBeUndefined()
  })

  it('网络层失败（status 为 undefined 的 ApiError）继续向上抛出', async () => {
    const networkError = new ApiError('网络请求失败')
    statsPostMock.mockRejectedValue(networkError)

    await expect(recordPackDownload('pack-1')).rejects.toBe(networkError)
  })

  it('非 ApiError 异常继续向上抛出', async () => {
    const unexpected = new TypeError('意外错误')
    statsPostMock.mockRejectedValue(unexpected)

    await expect(recordPackDownload('pack-1')).rejects.toBe(unexpected)
  })
})

describe('togglePackLike', () => {
  it('成功时返回最新点赞数与点赞状态', async () => {
    statsPostMock.mockResolvedValue({ success: true, likes: 6, liked: true })

    await expect(togglePackLike('pack-1', 'user-1')).resolves.toEqual({ likes: 6, liked: true })
    expect(statsPostMock).toHaveBeenCalledWith('/pack/like', {
      body: { pack_id: 'pack-1', user_id: 'user-1' },
      errorMessage: '点赞失败',
    })
  })

  it('包络 success 为 false 时抛出 ApiError', async () => {
    statsPostMock.mockResolvedValue({ success: false, error: '点赞过于频繁' })

    const error = await togglePackLike('pack-1', 'user-1').catch((e: unknown) => e)
    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).message).toBe('点赞过于频繁')
  })
})

describe('checkPackLike', () => {
  it('以 query 传递 pack_id 与 user_id 并返回点赞状态', async () => {
    statsGetMock.mockResolvedValue({ liked: true })

    await expect(checkPackLike('pack-1', 'user-1')).resolves.toBe(true)
    expect(statsGetMock).toHaveBeenCalledWith('/pack/like/check', {
      query: { pack_id: 'pack-1', user_id: 'user-1' },
      errorMessage: '查询 Pack 点赞状态失败',
    })
  })

  it('响应缺少 liked 字段时返回 false', async () => {
    statsGetMock.mockResolvedValue({})

    await expect(checkPackLike('pack-1', 'user-1')).resolves.toBe(false)
  })
})

// ============ 本地应用 Pack（backendApi）接口 ============

describe('detectPackConflicts', () => {
  it('按归一化 URL（忽略大小写与末尾斜杠）匹配提供商，收集全部匹配的本地提供商', async () => {
    const localConfig = makeLocalConfig({
      api_providers: [
        { ...makeProvider({ name: '本地甲', base_url: 'https://API.Example.com/v1/' }), api_key: 'k1' },
        { ...makeProvider({ name: '本地乙', base_url: 'https://api.example.com/v1' }), api_key: 'k2' },
        { ...makeProvider({ name: '无关商', base_url: 'https://other.example.com' }), api_key: 'k3' },
      ],
    })
    // 后端返回 { success, config } 包络时应解包 config
    backendGetMock.mockResolvedValue({ success: true, config: localConfig })

    const pack = makePack({
      providers: [
        makeProvider({ name: '云端甲', base_url: 'https://api.example.com/v1' }),
        makeProvider({ name: '云端新', base_url: 'https://new.example.com' }),
      ],
    })

    const conflicts = await detectPackConflicts(pack)

    expect(backendGetMock).toHaveBeenCalledWith('/api/webui/config/model', {
      errorMessage: '获取当前模型配置失败',
    })
    expect(conflicts.existing_providers).toEqual([
      {
        pack_provider: pack.providers[0],
        local_providers: [
          { name: '本地甲', base_url: 'https://API.Example.com/v1/' },
          { name: '本地乙', base_url: 'https://api.example.com/v1' },
        ],
      },
    ])
    expect(conflicts.new_providers).toEqual([pack.providers[1]])
  })

  it('检测同名模型冲突，未冲突模型不进入结果', async () => {
    const localConfig = makeLocalConfig({
      models: [makeModel({ name: 'gpt-4o' })],
    })
    // 后端直接返回配置体（无 config 包络）时按原样使用
    backendGetMock.mockResolvedValue(localConfig)

    const pack = makePack({
      models: [makeModel({ name: 'gpt-4o' }), makeModel({ name: 'claude-opus', model_identifier: 'claude-opus' })],
    })

    const conflicts = await detectPackConflicts(pack)

    expect(conflicts.conflicting_models).toEqual([{ pack_model: 'gpt-4o', local_model: 'gpt-4o' }])
  })

  it('非法 URL 走小写去末尾斜杠的回退比较', async () => {
    const localConfig = makeLocalConfig({
      api_providers: [{ ...makeProvider({ name: '本地商', base_url: 'not-a-url' }), api_key: 'k' }],
    })
    backendGetMock.mockResolvedValue(localConfig)

    const pack = makePack({
      providers: [makeProvider({ name: '云端商', base_url: 'Not-A-URL/' })],
    })

    const conflicts = await detectPackConflicts(pack)

    expect(conflicts.existing_providers).toHaveLength(1)
    expect(conflicts.existing_providers[0].local_providers).toEqual([
      { name: '本地商', base_url: 'not-a-url' },
    ])
    expect(conflicts.new_providers).toEqual([])
  })
})

describe('applyPack', () => {
  it('应用提供商：映射的跳过、新提供商带 API Key 追加、同名提供商覆盖，最后保存配置', async () => {
    const localConfig = makeLocalConfig({
      api_providers: [
        { ...makeProvider({ name: '本地已有', base_url: 'https://old.example.com' }), api_key: 'sk-old' },
        { ...makeProvider({ name: '同名商', base_url: 'https://legacy.example.com' }), api_key: 'sk-legacy' },
      ],
    })
    backendGetMock.mockResolvedValue(localConfig)
    backendPostMock.mockResolvedValue(new Response('{}'))

    const pack = makePack({
      providers: [
        makeProvider({ name: '映射商' }),
        makeProvider({ name: '新增商', base_url: 'https://new.example.com' }),
        makeProvider({ name: '同名商', base_url: 'https://dup.example.com' }),
      ],
    })

    await applyPack(
      pack,
      makeApplyOptions({ apply_providers: true }),
      { 映射商: '本地已有' },
      { 新增商: 'sk-new', 同名商: 'sk-dup' }
    )

    expect(backendPostMock).toHaveBeenCalledWith('/api/webui/config/model', {
      body: localConfig,
      parse: 'response',
      errorMessage: '保存配置失败',
    })
    const savedConfig = (backendPostMock.mock.calls[0][1] as { body: LocalConfig }).body
    expect(savedConfig.api_providers).toEqual([
      // 映射到已有提供商：原条目保持不变
      { ...makeProvider({ name: '本地已有', base_url: 'https://old.example.com' }), api_key: 'sk-old' },
      // 同名提供商被 Pack 中的配置覆盖，并写入新 API Key
      { ...makeProvider({ name: '同名商', base_url: 'https://dup.example.com' }), api_key: 'sk-dup' },
      // 新提供商追加到末尾
      { ...makeProvider({ name: '新增商', base_url: 'https://new.example.com' }), api_key: 'sk-new' },
    ])
  })

  it('新提供商缺少 API Key 时抛错且不保存配置', async () => {
    backendGetMock.mockResolvedValue(makeLocalConfig())

    const pack = makePack({ providers: [makeProvider({ name: '无钥商' })] })

    await expect(
      applyPack(pack, makeApplyOptions({ apply_providers: true }), {}, {})
    ).rejects.toThrow('提供商 "无钥商" 缺少 API Key')
    expect(backendPostMock).not.toHaveBeenCalled()
  })

  it('应用模型：按 selected_models 过滤、映射提供商名称、同名覆盖其余追加', async () => {
    const localConfig = makeLocalConfig({
      models: [makeModel({ name: '模型A', api_provider: '旧商' })],
    })
    backendGetMock.mockResolvedValue(localConfig)
    backendPostMock.mockResolvedValue(new Response('{}'))

    const pack = makePack({
      models: [
        makeModel({ name: '模型A', api_provider: '云端商' }),
        makeModel({ name: '模型B', api_provider: '云端商' }),
        makeModel({ name: '模型C', api_provider: '独立商' }),
      ],
    })

    await applyPack(
      pack,
      makeApplyOptions({ apply_models: true, selected_models: ['模型A', '模型B'] }),
      { 云端商: '本地商' },
      {}
    )

    const savedConfig = (backendPostMock.mock.calls[0][1] as { body: LocalConfig }).body
    expect(savedConfig.models).toEqual([
      // 同名模型被覆盖，api_provider 按映射改写为本地提供商
      makeModel({ name: '模型A', api_provider: '本地商' }),
      // 新模型追加，同样应用提供商映射
      makeModel({ name: '模型B', api_provider: '本地商' }),
      // 模型C 未被选中，不进入配置
    ])
  })

  it('任务配置 replace 模式：model_list 按已应用模型过滤，过滤后为空的任务跳过', async () => {
    const localConfig = makeLocalConfig({
      model_task_config: {
        replyer: { model_list: ['旧模型'] },
        utils: { model_list: ['旧工具模型'] },
      },
    })
    backendGetMock.mockResolvedValue(localConfig)
    backendPostMock.mockResolvedValue(new Response('{}'))

    const pack = makePack({
      models: [makeModel({ name: '模型A' }), makeModel({ name: '模型B' })],
      task_config: {
        replyer: { model_list: ['模型A', '模型B'], temperature: 0.7 },
        utils: { model_list: ['模型B'] },
      },
    })

    await applyPack(
      pack,
      makeApplyOptions({ apply_task_config: true, selected_models: ['模型A'] }),
      {},
      {}
    )

    const savedConfig = (backendPostMock.mock.calls[0][1] as { body: LocalConfig }).body
    // replyer 被替换，且 model_list 只保留已应用的模型A
    expect(savedConfig.model_task_config.replyer).toEqual({ model_list: ['模型A'], temperature: 0.7 })
    // utils 的 model_list 过滤后为空，保持本地原配置不变
    expect(savedConfig.model_task_config.utils).toEqual({ model_list: ['旧工具模型'] })
  })

  it('任务配置 append 模式：与现有 model_list 合并去重，无现有配置的任务直接写入', async () => {
    const localConfig = makeLocalConfig({
      model_task_config: {
        planner: { model_list: ['模型A', '本地模型'], slow_threshold: 30 },
      },
    })
    backendGetMock.mockResolvedValue(localConfig)
    backendPostMock.mockResolvedValue(new Response('{}'))

    const pack = makePack({
      models: [makeModel({ name: '模型A' }), makeModel({ name: '模型B' })],
      task_config: {
        planner: { model_list: ['模型A', '模型B'] },
        vlm: { model_list: ['模型A'] },
      },
    })

    await applyPack(
      pack,
      makeApplyOptions({ apply_task_config: true, task_mode: 'append' }),
      {},
      {}
    )

    const savedConfig = (backendPostMock.mock.calls[0][1] as { body: LocalConfig }).body
    // planner 合并去重：保留本地模型与原有字段，追加 Pack 中的新模型
    expect(savedConfig.model_task_config.planner).toEqual({
      model_list: ['模型A', '本地模型', '模型B'],
      slow_threshold: 30,
    })
    // vlm 本地不存在，直接写入 Pack 配置
    expect(savedConfig.model_task_config.vlm).toEqual({ model_list: ['模型A'] })
  })

  it('selected_tasks 只应用选中的任务，未选中任务保持本地配置', async () => {
    const localConfig = makeLocalConfig({
      model_task_config: {
        replyer: { model_list: ['旧回复模型'] },
        utils: { model_list: ['旧工具模型'] },
      },
    })
    backendGetMock.mockResolvedValue(localConfig)
    backendPostMock.mockResolvedValue(new Response('{}'))

    const pack = makePack({
      models: [makeModel({ name: '模型A' })],
      task_config: {
        replyer: { model_list: ['模型A'] },
        utils: { model_list: ['模型A'] },
      },
    })

    await applyPack(
      pack,
      makeApplyOptions({ apply_task_config: true, selected_tasks: ['replyer'] }),
      {},
      {}
    )

    const savedConfig = (backendPostMock.mock.calls[0][1] as { body: LocalConfig }).body
    expect(savedConfig.model_task_config.replyer).toEqual({ model_list: ['模型A'] })
    expect(savedConfig.model_task_config.utils).toEqual({ model_list: ['旧工具模型'] })
  })
})

// ============ 辅助函数 ============

describe('getPackUserId', () => {
  const storageKey = 'maibot_pack_user_id'

  beforeEach(() => {
    localStorage.removeItem(storageKey)
  })

  it('无缓存时生成 pack_user_ 前缀的用户 ID 并写入 localStorage', () => {
    const userId = getPackUserId()

    expect(userId.startsWith('pack_user_')).toBe(true)
    expect(localStorage.getItem(storageKey)).toBe(userId)
  })

  it('已有缓存时直接返回缓存值，不重新生成', () => {
    localStorage.setItem(storageKey, 'pack_user_fixed')

    expect(getPackUserId()).toBe('pack_user_fixed')
    expect(localStorage.getItem(storageKey)).toBe('pack_user_fixed')
  })
})
