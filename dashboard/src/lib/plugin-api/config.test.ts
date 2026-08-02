import { describe, expect, it, vi } from 'vitest'

import { ApiError, backendApi } from '@/lib/http'

import {
  getPluginConfig,
  getPluginConfigBundle,
  getPluginConfigRaw,
  getPluginConfigSchema,
  getPluginRuntimeComponents,
  resetPluginConfig,
  togglePlugin,
  updatePluginConfig,
  updatePluginConfigRaw,
} from './config'
import type { PluginConfigSchema, PluginRuntimeComponent } from './types'

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

const getMock = vi.mocked(backendApi.get)
const postMock = vi.mocked(backendApi.post)
const putMock = vi.mocked(backendApi.put)

/** 构造一个最小可用的插件配置 Schema */
function makeSchema(pluginId: string): PluginConfigSchema {
  return {
    plugin_id: pluginId,
    plugin_info: {
      name: '演示插件',
      version: '1.0.0',
      description: '用于测试的插件',
      author: '测试作者',
    },
    sections: {},
    layout: { type: 'auto', tabs: [] },
  }
}

/** 构造一个最小可用的运行时组件 */
function makeComponent(name: string): PluginRuntimeComponent {
  return {
    name,
    description: '组件描述',
    enabled: true,
    plugin_name: '演示插件',
    component_type: 'action',
  }
}

describe('getPluginConfigBundle', () => {
  it('成功时把响应字段映射为 PluginConfigBundle', async () => {
    const schema = makeSchema('demo')
    getMock.mockResolvedValue({
      success: true,
      schema,
      config: { enabled: true },
      raw_config: '[plugin]\nenabled = true\n',
      message: '获取成功',
    })

    await expect(getPluginConfigBundle('demo')).resolves.toEqual({
      schema,
      config: { enabled: true },
      rawConfig: '[plugin]\nenabled = true\n',
      message: '获取成功',
    })
    expect(getMock).toHaveBeenCalledWith('/api/webui/plugins/config/demo/bundle', {
      errorMessage: '获取插件配置初始化数据失败',
    })
  })

  it('业务级失败（success 为 false）时抛出带后端 message 的 ApiError', async () => {
    getMock.mockResolvedValue({ success: false, message: '插件不存在' })

    await expect(getPluginConfigBundle('demo')).rejects.toBeInstanceOf(ApiError)
    getMock.mockResolvedValue({ success: false, message: '插件不存在' })
    await expect(getPluginConfigBundle('demo')).rejects.toMatchObject({ message: '插件不存在' })
  })

  it('缺少 raw_config 时抛出 ApiError 并使用兜底文案', async () => {
    getMock.mockResolvedValue({
      success: true,
      schema: makeSchema('demo'),
      config: { enabled: true },
    })

    await expect(getPluginConfigBundle('demo')).rejects.toMatchObject({
      message: '获取插件配置初始化数据失败',
    })
  })

  it('缺少 config 时抛出 ApiError', async () => {
    getMock.mockResolvedValue({
      success: true,
      schema: makeSchema('demo'),
      raw_config: '',
      message: '配置读取异常',
    })

    await expect(getPluginConfigBundle('demo')).rejects.toMatchObject({ message: '配置读取异常' })
  })
})

describe('getPluginConfigSchema', () => {
  it('成功时返回响应中的 schema', async () => {
    const schema = makeSchema('demo')
    getMock.mockResolvedValue({ success: true, schema })

    await expect(getPluginConfigSchema('demo')).resolves.toBe(schema)
    expect(getMock).toHaveBeenCalledWith('/api/webui/plugins/config/demo/schema', {
      errorMessage: '获取配置 Schema 失败',
    })
  })

  it('success 为 true 但缺少 schema 时抛出 ApiError', async () => {
    getMock.mockResolvedValue({ success: true })

    await expect(getPluginConfigSchema('demo')).rejects.toMatchObject({
      message: '获取配置 Schema 失败',
    })
  })
})

describe('getPluginConfig', () => {
  it('成功时返回配置对象', async () => {
    getMock.mockResolvedValue({ success: true, config: { retries: 3 } })

    await expect(getPluginConfig('demo')).resolves.toEqual({ retries: 3 })
    expect(getMock).toHaveBeenCalledWith('/api/webui/plugins/config/demo', {
      errorMessage: '获取配置失败',
    })
  })

  it('缺少 config 时抛出带后端 message 的 ApiError', async () => {
    getMock.mockResolvedValue({ success: true, message: '配置文件损坏' })

    await expect(getPluginConfig('demo')).rejects.toMatchObject({ message: '配置文件损坏' })
  })
})

describe('getPluginConfigRaw', () => {
  it('成功时返回原始 TOML 字符串', async () => {
    getMock.mockResolvedValue({ success: true, config: '[plugin]\nname = "demo"\n' })

    await expect(getPluginConfigRaw('demo')).resolves.toBe('[plugin]\nname = "demo"\n')
    expect(getMock).toHaveBeenCalledWith('/api/webui/plugins/config/demo/raw', {
      errorMessage: '获取配置失败',
    })
  })

  it('缺少 config 时抛出 ApiError', async () => {
    getMock.mockResolvedValue({ success: true })

    await expect(getPluginConfigRaw('demo')).rejects.toMatchObject({ message: '获取配置失败' })
  })
})

describe('updatePluginConfig', () => {
  it('把配置对象包在 body.config 中 PUT 到插件配置接口', async () => {
    const response = { success: true, message: '更新成功', note: '需要重载' }
    putMock.mockResolvedValue(response)

    await expect(updatePluginConfig('demo', { enabled: false })).resolves.toBe(response)
    expect(putMock).toHaveBeenCalledWith('/api/webui/plugins/config/demo', {
      body: { config: { enabled: false } },
      errorMessage: '更新插件配置失败',
    })
  })

  it('HTTP 层失败时向上抛出 ApiError', async () => {
    putMock.mockRejectedValue(new ApiError('更新插件配置失败', { status: 500 }))

    await expect(updatePluginConfig('demo', {})).rejects.toMatchObject({ status: 500 })
  })
})

describe('updatePluginConfigRaw', () => {
  it('把 TOML 字符串包在 body.config 中 PUT 到 raw 接口', async () => {
    const response = { success: true, message: '更新成功' }
    putMock.mockResolvedValue(response)

    await expect(updatePluginConfigRaw('demo', '[plugin]\n')).resolves.toBe(response)
    expect(putMock).toHaveBeenCalledWith('/api/webui/plugins/config/demo/raw', {
      body: { config: '[plugin]\n' },
      errorMessage: '更新插件配置失败',
    })
  })
})

describe('resetPluginConfig', () => {
  it('POST 到 reset 接口并返回带备份路径的结果', async () => {
    const response = { success: true, message: '已重置', backup: '/backup/demo.toml' }
    postMock.mockResolvedValue(response)

    await expect(resetPluginConfig('demo')).resolves.toBe(response)
    expect(postMock).toHaveBeenCalledWith('/api/webui/plugins/config/demo/reset', {
      errorMessage: '重置插件配置失败',
    })
  })
})

describe('togglePlugin', () => {
  it('POST 到 toggle 接口并返回切换后的启用状态', async () => {
    const response = { success: true, enabled: false, message: '已停用' }
    postMock.mockResolvedValue(response)

    await expect(togglePlugin('demo')).resolves.toBe(response)
    expect(postMock).toHaveBeenCalledWith('/api/webui/plugins/config/demo/toggle', {
      errorMessage: '切换插件状态失败',
    })
  })

  it('HTTP 层失败时向上抛出 ApiError', async () => {
    postMock.mockRejectedValue(new ApiError('切换插件状态失败', { status: 503 }))

    await expect(togglePlugin('demo')).rejects.toBeInstanceOf(ApiError)
  })
})

describe('getPluginRuntimeComponents', () => {
  it('成功时返回组件列表', async () => {
    const components = [makeComponent('发送表情'), makeComponent('查询天气')]
    getMock.mockResolvedValue({ success: true, components })

    await expect(getPluginRuntimeComponents('demo')).resolves.toBe(components)
    expect(getMock).toHaveBeenCalledWith('/api/webui/plugins/runtime/plugins/demo/components', {
      errorMessage: '获取插件组件失败',
    })
  })

  it('success 为 true 但组件字段缺省时返回空数组', async () => {
    getMock.mockResolvedValue({ success: true })

    await expect(getPluginRuntimeComponents('demo')).resolves.toEqual([])
  })

  it('业务级失败时抛出 ApiError', async () => {
    getMock.mockResolvedValue({ success: false, message: '插件未加载' })

    await expect(getPluginRuntimeComponents('demo')).rejects.toMatchObject({
      message: '插件未加载',
    })
  })
})
