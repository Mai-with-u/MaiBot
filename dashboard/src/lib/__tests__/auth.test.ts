import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError, authApi } from '@/lib/http'

import { checkAuthStatus, getAuthStatus, getSetupStatus, logout } from '../auth'

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
  }
})

const getMock = vi.mocked(authApi.get)
const postMock = vi.mocked(authApi.post)

beforeEach(() => {
  getMock.mockReset()
  postMock.mockReset()
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('logout', () => {
  /** 用可写的普通对象替换 location，拦截 logout 的整页跳转 */
  function stubLocation(): { href: string } {
    const locationStub = { href: '' }
    vi.stubGlobal('location', locationStub)
    return locationStub
  }

  it('调用登出接口（parse: response）并跳转到登录页', async () => {
    const locationStub = stubLocation()
    postMock.mockResolvedValue(new Response(null))

    await logout()

    expect(postMock).toHaveBeenCalledWith('/api/webui/auth/logout', { parse: 'response' })
    expect(locationStub.href).toBe('/auth')
  })

  it('登出请求失败时记录错误但依然跳转到登录页', async () => {
    const locationStub = stubLocation()
    const consoleErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    const error = new ApiError('登出失败', { status: 500 })
    postMock.mockRejectedValue(error)

    await logout()

    expect(consoleErrorSpy).toHaveBeenCalledWith('登出请求失败:', error)
    expect(locationStub.href).toBe('/auth')
  })
})

describe('getAuthStatus', () => {
  it('请求认证检查接口并原样映射字段', async () => {
    getMock.mockResolvedValue({
      authenticated: true,
      token_source: 'custom',
      requires_custom_token: true,
    })

    await expect(getAuthStatus()).resolves.toEqual({
      authenticated: true,
      token_source: 'custom',
      requires_custom_token: true,
    })
    expect(getMock).toHaveBeenCalledWith('/api/webui/auth/check')
  })

  it('可选字段缺失时 authenticated / requires_custom_token 强制为布尔值', async () => {
    getMock.mockResolvedValue({ authenticated: false })

    await expect(getAuthStatus()).resolves.toEqual({
      authenticated: false,
      token_source: undefined,
      requires_custom_token: false,
    })
  })

  it('请求失败时返回未认证状态而不抛错', async () => {
    getMock.mockRejectedValue(new ApiError('未认证', { status: 401 }))

    await expect(getAuthStatus()).resolves.toEqual({ authenticated: false })
  })
})

describe('checkAuthStatus', () => {
  it('认证有效时返回 true', async () => {
    getMock.mockResolvedValue({ authenticated: true, token_source: 'env' })

    await expect(checkAuthStatus()).resolves.toBe(true)
    expect(getMock).toHaveBeenCalledWith('/api/webui/auth/check')
  })

  it('请求失败时返回 false', async () => {
    getMock.mockRejectedValue(new ApiError('未认证', { status: 401 }))

    await expect(checkAuthStatus()).resolves.toBe(false)
  })
})

describe('getSetupStatus', () => {
  it('请求首次配置状态接口并透传响应', async () => {
    const response = {
      is_first_setup: true,
      token_source: 'default',
      requires_custom_token: true,
      message: '请设置自定义 Token',
    }
    getMock.mockResolvedValue(response)

    await expect(getSetupStatus()).resolves.toBe(response)
    expect(getMock).toHaveBeenCalledWith('/api/webui/setup/status')
  })

  it('请求失败时返回 null 而不抛错', async () => {
    getMock.mockRejectedValue(new ApiError('接口不可用', { status: 503 }))

    await expect(getSetupStatus()).resolves.toBeNull()
  })
})
