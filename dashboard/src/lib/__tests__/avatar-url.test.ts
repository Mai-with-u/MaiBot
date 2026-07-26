import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { resolveApiPath } from '@/lib/api-base'
import { ApiError, backendApi } from '@/lib/http'

import {
  buildWebuiAvatarPath,
  isAvatarFetchEnabled,
  uploadWebuiUserAvatar,
  useAvatarFetchEnabled,
  useResolvedAvatarUrl,
} from '../avatar-url'

vi.mock('@/lib/api-base', () => ({
  getApiBaseUrl: vi.fn(),
  resolveApiPath: vi.fn(),
}))

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

const resolveApiPathMock = vi.mocked(resolveApiPath)
const putMock = vi.mocked(backendApi.put)

/** 头像获取开关对应的 localStorage key（与 settings-manager 中的定义一致） */
const AVATAR_FETCH_STORAGE_KEY = 'maibot-enable-avatar-fetch'

beforeEach(() => {
  localStorage.clear()
  // 每个用例前重挂实现：解析结果加上 resolved: 前缀，便于断言路径确实经过了解析
  resolveApiPathMock.mockImplementation(async (path: string) => `resolved:${path}`)
})

describe('buildWebuiAvatarPath', () => {
  it('用户头像：platform 去空格并转小写，targetId 去空格后拼入 user_id', () => {
    expect(buildWebuiAvatarPath(' QQ ', ' 12345 ')).toBe(
      '/api/webui/avatar?platform=qq&user_id=12345'
    )
  })

  it('群头像：targetType 为 group 时使用 group_id 参数', () => {
    expect(buildWebuiAvatarPath('qq', '67890', 'group')).toBe(
      '/api/webui/avatar?platform=qq&group_id=67890'
    )
  })

  it('提供 version 时追加 v 参数，数字 0 也是有效版本', () => {
    expect(buildWebuiAvatarPath('qq', '1', 'user', 'v1.2')).toBe(
      '/api/webui/avatar?platform=qq&user_id=1&v=v1.2'
    )
    expect(buildWebuiAvatarPath('qq', '1', 'user', 0)).toBe(
      '/api/webui/avatar?platform=qq&user_id=1&v=0'
    )
  })

  it('version 为 undefined 或 null 时不追加 v 参数', () => {
    expect(buildWebuiAvatarPath('qq', '1', 'user', undefined)).toBe(
      '/api/webui/avatar?platform=qq&user_id=1'
    )
    expect(buildWebuiAvatarPath('qq', '1', 'user', null)).toBe(
      '/api/webui/avatar?platform=qq&user_id=1'
    )
  })

  it('特殊字符经 encodeURIComponent 编码', () => {
    expect(buildWebuiAvatarPath('qq', 'a&b=c')).toBe(
      '/api/webui/avatar?platform=qq&user_id=a%26b%3Dc'
    )
  })

  it('platform 或 targetId 缺失（含纯空白）时返回 null', () => {
    expect(buildWebuiAvatarPath(undefined, '123')).toBeNull()
    expect(buildWebuiAvatarPath('qq', null)).toBeNull()
    expect(buildWebuiAvatarPath('   ', '123')).toBeNull()
    expect(buildWebuiAvatarPath('qq', '   ')).toBeNull()
  })
})

describe('isAvatarFetchEnabled', () => {
  it('未设置时默认开启', () => {
    expect(isAvatarFetchEnabled()).toBe(true)
  })

  it('localStorage 中显式关闭后返回 false', () => {
    localStorage.setItem(AVATAR_FETCH_STORAGE_KEY, 'false')
    expect(isAvatarFetchEnabled()).toBe(false)
  })
})

describe('useAvatarFetchEnabled', () => {
  it('初始值从设置中读取', () => {
    localStorage.setItem(AVATAR_FETCH_STORAGE_KEY, 'false')
    const { result } = renderHook(() => useAvatarFetchEnabled())
    expect(result.current).toBe(false)
  })

  it('settings-change 事件命中 enableAvatarFetch 时同步最新值，无关 key 不触发同步', () => {
    const { result } = renderHook(() => useAvatarFetchEnabled())
    expect(result.current).toBe(true)

    localStorage.setItem(AVATAR_FETCH_STORAGE_KEY, 'false')

    // 无关 key 的设置变更不应触发同步
    act(() => {
      window.dispatchEvent(
        new CustomEvent('maibot-settings-change', { detail: { key: 'theme', value: 'dark' } })
      )
    })
    expect(result.current).toBe(true)

    // 命中 enableAvatarFetch 时同步为最新值
    act(() => {
      window.dispatchEvent(
        new CustomEvent('maibot-settings-change', {
          detail: { key: 'enableAvatarFetch', value: false },
        })
      )
    })
    expect(result.current).toBe(false)
  })

  it('settings-reset 事件触发全量同步', () => {
    localStorage.setItem(AVATAR_FETCH_STORAGE_KEY, 'false')
    const { result } = renderHook(() => useAvatarFetchEnabled())
    expect(result.current).toBe(false)

    localStorage.removeItem(AVATAR_FETCH_STORAGE_KEY)
    act(() => {
      window.dispatchEvent(new CustomEvent('maibot-settings-reset'))
    })
    expect(result.current).toBe(true)
  })
})

describe('useResolvedAvatarUrl', () => {
  it('参数有效且开关开启时，解析出经 resolveApiPath 处理的头像地址', async () => {
    const { result } = renderHook(() => useResolvedAvatarUrl('QQ', '12345'))

    await waitFor(() => {
      expect(result.current).toBe('resolved:/api/webui/avatar?platform=qq&user_id=12345')
    })
    expect(resolveApiPathMock).toHaveBeenCalledWith('/api/webui/avatar?platform=qq&user_id=12345')
  })

  it('参数不足以构造路径时返回 undefined，且不发起解析', () => {
    const { result } = renderHook(() => useResolvedAvatarUrl(undefined, '12345'))

    expect(result.current).toBeUndefined()
    expect(resolveApiPathMock).not.toHaveBeenCalled()
  })

  it('头像获取开关关闭时返回 undefined，且不发起解析', () => {
    localStorage.setItem(AVATAR_FETCH_STORAGE_KEY, 'false')
    const { result } = renderHook(() => useResolvedAvatarUrl('qq', '12345'))

    expect(result.current).toBeUndefined()
    expect(resolveApiPathMock).not.toHaveBeenCalled()
  })
})

describe('uploadWebuiUserAvatar', () => {
  it('以 FormData 形式 PUT 用户 ID 与文件到头像上传接口', async () => {
    const response = { success: true, avatar_url: '/api/webui/avatar/webui-user/u1.png' }
    putMock.mockResolvedValue(response)
    const file = new File(['img'], 'avatar.png', { type: 'image/png' })

    await expect(uploadWebuiUserAvatar('u1', file)).resolves.toBe(response)

    expect(putMock).toHaveBeenCalledWith('/api/webui/avatar/webui-user', {
      body: expect.any(FormData),
      errorMessage: '保存用户头像失败',
    })
    const body = putMock.mock.calls[0][1]?.body as FormData
    expect(body.get('user_id')).toBe('u1')
    expect(body.get('file')).toBe(file)
  })

  it('后端返回错误时向上抛出 ApiError', async () => {
    putMock.mockRejectedValue(new ApiError('保存用户头像失败', { status: 500 }))
    const file = new File(['img'], 'avatar.png', { type: 'image/png' })

    await expect(uploadWebuiUserAvatar('u1', file)).rejects.toMatchObject({
      message: '保存用户头像失败',
      status: 500,
    })
  })
})
