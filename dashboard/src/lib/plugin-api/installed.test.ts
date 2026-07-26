import { describe, expect, it, vi } from 'vitest'

import { checkPluginInstalled, getInstalledPluginVersion } from './installed'
import type { InstalledPlugin, LegacyInstalledPlugin } from './types'

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

/**
 * installed.ts 有模块级缓存（TTL 缓存 + 在途请求去重），
 * 每个用例通过 vi.resetModules + 动态 import 拿到全新模块，避免跨用例污染。
 * ApiError 也必须取同一代模块注册表里的类，保证 instanceof 判断一致。
 */
async function loadInstalledModule() {
  vi.resetModules()
  const http = await import('@/lib/http')
  const mod = await import('./installed')
  return {
    getMock: vi.mocked(http.backendApi.get),
    ApiError: http.ApiError,
    getInstalledPlugins: mod.getInstalledPlugins,
    getLocalPluginReadme: mod.getLocalPluginReadme,
    getLocalPluginChangelog: mod.getLocalPluginChangelog,
  }
}

/** 构造一个最小可用的已安装插件（新格式，带 manifest） */
function makeInstalledPlugin(id: string, version: string): InstalledPlugin {
  return {
    id,
    manifest: {
      manifest_version: 1,
      name: `插件 ${id}`,
      version,
      description: '用于测试的插件',
      author: { name: '测试作者' },
      license: 'MIT',
      host_application: { min_version: '1.0.0' },
    },
    path: `/plugins/${id}`,
  }
}

describe('getInstalledPlugins', () => {
  it('成功时请求已安装插件接口并返回插件列表', async () => {
    const { getMock, getInstalledPlugins } = await loadInstalledModule()
    const plugins = [makeInstalledPlugin('demo', '1.2.0')]
    getMock.mockResolvedValue({ success: true, plugins })

    await expect(getInstalledPlugins()).resolves.toEqual(plugins)
    expect(getMock).toHaveBeenCalledWith('/api/webui/plugins/installed', {
      errorMessage: '获取已安装插件列表失败',
    })
  })

  it('TTL 内的后续调用命中缓存，不再发起请求', async () => {
    const { getMock, getInstalledPlugins } = await loadInstalledModule()
    const plugins = [makeInstalledPlugin('demo', '1.2.0')]
    getMock.mockResolvedValue({ success: true, plugins })
    const nowSpy = vi.spyOn(Date, 'now').mockReturnValue(1_000_000)

    const first = await getInstalledPlugins()
    // TTL（1500ms）内：直接返回缓存结果
    nowSpy.mockReturnValue(1_000_000 + 1_400)
    const second = await getInstalledPlugins()

    expect(second).toBe(first)
    expect(getMock).toHaveBeenCalledTimes(1)
  })

  it('TTL 过期后重新发起请求', async () => {
    const { getMock, getInstalledPlugins } = await loadInstalledModule()
    getMock.mockResolvedValue({ success: true, plugins: [] })
    const nowSpy = vi.spyOn(Date, 'now').mockReturnValue(1_000_000)

    await getInstalledPlugins()
    // 超过 1500ms TTL：缓存失效，需要重新请求
    nowSpy.mockReturnValue(1_000_000 + 1_600)
    await getInstalledPlugins()

    expect(getMock).toHaveBeenCalledTimes(2)
  })

  it('forceRefresh 绕过 TTL 缓存强制重新请求', async () => {
    const { getMock, getInstalledPlugins } = await loadInstalledModule()
    getMock.mockResolvedValue({ success: true, plugins: [] })

    await getInstalledPlugins()
    await getInstalledPlugins({ forceRefresh: true })

    expect(getMock).toHaveBeenCalledTimes(2)
  })

  it('并发调用共享同一在途请求，只发起一次请求', async () => {
    const { getMock, getInstalledPlugins } = await loadInstalledModule()
    const plugins = [makeInstalledPlugin('demo', '1.2.0')]
    getMock.mockResolvedValue({ success: true, plugins })

    // 两次调用之间不 await：第二次应复用第一次的在途 Promise
    const p1 = getInstalledPlugins()
    const p2 = getInstalledPlugins()

    await expect(p1).resolves.toEqual(plugins)
    await expect(p2).resolves.toEqual(plugins)
    expect(getMock).toHaveBeenCalledTimes(1)
  })

  it('HTTP 层错误（非 401）被吞掉并返回空列表', async () => {
    const { getMock, getInstalledPlugins, ApiError } = await loadInstalledModule()
    getMock.mockRejectedValue(new ApiError('获取已安装插件列表失败', { status: 500 }))

    await expect(getInstalledPlugins()).resolves.toEqual([])
  })

  it('401 认证失效向上抛出 ApiError', async () => {
    const { getMock, getInstalledPlugins, ApiError } = await loadInstalledModule()
    getMock.mockRejectedValue(new ApiError('认证失效', { status: 401 }))

    await expect(getInstalledPlugins()).rejects.toMatchObject({ status: 401 })
  })

  it('网络层失败（status 为 undefined）向上抛出 ApiError', async () => {
    const { getMock, getInstalledPlugins, ApiError } = await loadInstalledModule()
    getMock.mockRejectedValue(new ApiError('网络异常', {}))

    await expect(getInstalledPlugins()).rejects.toMatchObject({ message: '网络异常' })
  })

  it('在途请求失败时并发调用共享同一个被拒绝的 Promise，await 重抛但不再请求', async () => {
    const { getMock, getInstalledPlugins, ApiError } = await loadInstalledModule()
    getMock.mockRejectedValue(new ApiError('认证失效', { status: 401 }))

    // 两次调用之间不 await：第二次拿到的是同一个（最终会被拒绝的）在途 Promise
    const p1 = getInstalledPlugins()
    const p2 = getInstalledPlugins()

    await expect(p1).rejects.toMatchObject({ status: 401 })
    await expect(p2).rejects.toMatchObject({ status: 401 })
    expect(getMock).toHaveBeenCalledTimes(1)
  })

  it('失败结算后不写入 TTL 缓存，下一次调用重新请求', async () => {
    const { getMock, getInstalledPlugins, ApiError } = await loadInstalledModule()
    getMock.mockRejectedValueOnce(new ApiError('认证失效', { status: 401 }))
    await expect(getInstalledPlugins()).rejects.toMatchObject({ status: 401 })

    // 失败不会缓存结果：下一次调用应重新发起请求并成功
    const plugins = [makeInstalledPlugin('demo', '1.2.0')]
    getMock.mockResolvedValueOnce({ success: true, plugins })
    await expect(getInstalledPlugins()).resolves.toEqual(plugins)
    expect(getMock).toHaveBeenCalledTimes(2)
  })

  it('业务级失败（success 为 false）返回空列表', async () => {
    const { getMock, getInstalledPlugins } = await loadInstalledModule()
    getMock.mockResolvedValue({ success: false, message: '插件系统未初始化' })

    await expect(getInstalledPlugins()).resolves.toEqual([])
  })

  it('success 为 true 但 plugins 字段缺省时返回空列表', async () => {
    const { getMock, getInstalledPlugins } = await loadInstalledModule()
    getMock.mockResolvedValue({ success: true })

    await expect(getInstalledPlugins()).resolves.toEqual([])
  })
})

describe('getLocalPluginReadme', () => {
  it('成功时返回 README 文本，且插件 ID 经过 URL 编码', async () => {
    const { getMock, getLocalPluginReadme } = await loadInstalledModule()
    getMock.mockResolvedValue({ success: true, data: '# 演示插件' })

    await expect(getLocalPluginReadme('demo/plugin')).resolves.toBe('# 演示插件')
    expect(getMock).toHaveBeenCalledWith('/api/webui/plugins/local-readme/demo%2Fplugin', {
      errorMessage: '获取 README 失败',
    })
  })

  it('业务级失败时返回空字符串', async () => {
    const { getMock, getLocalPluginReadme } = await loadInstalledModule()
    getMock.mockResolvedValue({ success: false, error: '文件不存在' })

    await expect(getLocalPluginReadme('demo')).resolves.toBe('')
  })

  it('success 为 true 但 data 缺省时返回空字符串', async () => {
    const { getMock, getLocalPluginReadme } = await loadInstalledModule()
    getMock.mockResolvedValue({ success: true })

    await expect(getLocalPluginReadme('demo')).resolves.toBe('')
  })
})

describe('getLocalPluginChangelog', () => {
  it('成功时返回更新日志文本', async () => {
    const { getMock, getLocalPluginChangelog } = await loadInstalledModule()
    getMock.mockResolvedValue({ success: true, data: '## 1.2.0\n- 修复问题' })

    await expect(getLocalPluginChangelog('demo')).resolves.toBe('## 1.2.0\n- 修复问题')
    expect(getMock).toHaveBeenCalledWith('/api/webui/plugins/local-changelog/demo', {
      errorMessage: '获取更新日志失败',
    })
  })

  it('业务级失败时返回空字符串', async () => {
    const { getMock, getLocalPluginChangelog } = await loadInstalledModule()
    getMock.mockResolvedValue({ success: false, error: '文件不存在' })

    await expect(getLocalPluginChangelog('demo')).resolves.toBe('')
  })
})

describe('checkPluginInstalled', () => {
  it('插件在列表中时返回 true', () => {
    const installed = [makeInstalledPlugin('demo', '1.0.0')]

    expect(checkPluginInstalled('demo', installed)).toBe(true)
  })

  it('插件不在列表中时返回 false', () => {
    const installed = [makeInstalledPlugin('demo', '1.0.0')]

    expect(checkPluginInstalled('other', installed)).toBe(false)
  })
})

describe('getInstalledPluginVersion', () => {
  it('新格式插件从 manifest 中取版本号', () => {
    const installed = [makeInstalledPlugin('demo', '2.3.4')]

    expect(getInstalledPluginVersion('demo', installed)).toBe('2.3.4')
  })

  it('旧格式插件直接取顶层 version 字段', () => {
    const legacy: LegacyInstalledPlugin = { id: 'legacy-demo', version: '0.9.0', path: '/plugins/legacy-demo' }

    expect(getInstalledPluginVersion('legacy-demo', [legacy])).toBe('0.9.0')
  })

  it('插件不存在时返回 undefined', () => {
    expect(getInstalledPluginVersion('missing', [makeInstalledPlugin('demo', '1.0.0')])).toBeUndefined()
  })
})
