import { describe, expect, it, vi } from 'vitest'

import { ApiError, backendApi } from '@/lib/http'

import { installPlugin, uninstallPlugin, updatePlugin } from './install-flow'

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

const postMock = vi.mocked(backendApi.post)

describe('installPlugin', () => {
  it('未指定分支时默认使用 main 分支提交安装请求', async () => {
    const response = { success: true, message: '安装成功' }
    postMock.mockResolvedValue(response)

    await expect(installPlugin('demo', 'https://github.com/user/demo.git')).resolves.toBe(response)
    expect(postMock).toHaveBeenCalledWith('/api/webui/plugins/install', {
      body: {
        plugin_id: 'demo',
        repository_url: 'https://github.com/user/demo.git',
        branch: 'main',
      },
      errorMessage: '安装插件失败',
    })
  })

  it('指定分支时按传入分支提交', async () => {
    postMock.mockResolvedValue({ success: true, message: '安装成功' })

    await installPlugin('demo', 'https://github.com/user/demo.git', 'dev')

    expect(postMock).toHaveBeenCalledWith('/api/webui/plugins/install', {
      body: {
        plugin_id: 'demo',
        repository_url: 'https://github.com/user/demo.git',
        branch: 'dev',
      },
      errorMessage: '安装插件失败',
    })
  })

  it('后端返回错误时向上抛出 ApiError', async () => {
    postMock.mockRejectedValue(new ApiError('安装插件失败', { status: 500 }))

    await expect(installPlugin('demo', 'https://github.com/user/demo.git')).rejects.toBeInstanceOf(
      ApiError
    )
  })
})

describe('uninstallPlugin', () => {
  it('把插件 ID 作为请求体提交到卸载接口', async () => {
    const response = { success: true, message: '卸载成功' }
    postMock.mockResolvedValue(response)

    await expect(uninstallPlugin('demo')).resolves.toBe(response)
    expect(postMock).toHaveBeenCalledWith('/api/webui/plugins/uninstall', {
      body: { plugin_id: 'demo' },
      errorMessage: '卸载插件失败',
    })
  })

  it('后端返回错误时向上抛出 ApiError', async () => {
    postMock.mockRejectedValue(new ApiError('卸载插件失败', { status: 500 }))

    await expect(uninstallPlugin('demo')).rejects.toMatchObject({ status: 500 })
  })
})

describe('updatePlugin', () => {
  it('提交更新请求并返回新旧版本信息', async () => {
    const response = {
      success: true,
      message: '更新成功',
      old_version: '1.0.0',
      new_version: '1.1.0',
      update_mode: 'git_pull' as const,
    }
    postMock.mockResolvedValue(response)

    await expect(updatePlugin('demo', 'https://github.com/user/demo.git')).resolves.toBe(response)
    expect(postMock).toHaveBeenCalledWith('/api/webui/plugins/update', {
      body: {
        plugin_id: 'demo',
        repository_url: 'https://github.com/user/demo.git',
        branch: 'main',
      },
      errorMessage: '更新插件失败',
    })
  })

  it('指定分支时按传入分支提交更新请求', async () => {
    postMock.mockResolvedValue({
      success: true,
      message: '更新成功',
      old_version: '1.0.0',
      new_version: '1.1.0',
    })

    await updatePlugin('demo', 'https://github.com/user/demo.git', 'release')

    expect(postMock).toHaveBeenCalledWith('/api/webui/plugins/update', {
      body: {
        plugin_id: 'demo',
        repository_url: 'https://github.com/user/demo.git',
        branch: 'release',
      },
      errorMessage: '更新插件失败',
    })
  })

  it('后端返回错误时向上抛出 ApiError', async () => {
    postMock.mockRejectedValue(new ApiError('更新插件失败', { status: 502 }))

    await expect(updatePlugin('demo', 'https://github.com/user/demo.git')).rejects.toMatchObject({
      status: 502,
    })
  })
})
