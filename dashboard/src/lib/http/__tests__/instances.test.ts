import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '../errors'
import { authApi, backendApi, statsApi, STATS_SERVICE_BASE_URL } from '../instances'

function jsonResponse(data: unknown, init: ResponseInit = {}): Response {
  return new Response(JSON.stringify(data), { status: 200, ...init })
}

function mockFetch(response: Response): ReturnType<typeof vi.fn> {
  const fn = vi.fn(() => Promise.resolve(response.clone()))
  vi.stubGlobal('fetch', fn)
  return fn
}

/** 用可写普通对象替换 location，拦截 401 跳转，避免 jsdom 真的改地址 */
function stubLocation(href = 'http://localhost/dashboard'): { href: string } {
  const locationStub = { href }
  vi.stubGlobal('location', locationStub)
  return locationStub
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('http instances', () => {
  it('导出 backendApi / statsApi / authApi 三个客户端实例', () => {
    for (const client of [backendApi, statsApi, authApi]) {
      expect(client).toEqual(
        expect.objectContaining({
          request: expect.any(Function),
          get: expect.any(Function),
          post: expect.any(Function),
          put: expect.any(Function),
          patch: expect.any(Function),
          delete: expect.any(Function),
        })
      )
    }
  })

  it('statsApi 请求打到统计服务基址且不携带凭据', async () => {
    const fetchMock = mockFetch(jsonResponse({ ok: true }))

    await expect(statsApi.get('/surveys')).resolves.toEqual({ ok: true })

    expect(fetchMock.mock.calls[0][0]).toBe(`${STATS_SERVICE_BASE_URL}/surveys`)
    expect((fetchMock.mock.calls[0][1] as RequestInit).credentials).toBeUndefined()
  })

  it('backendApi 遇到 401 时把 location 设到 /auth 并抛出认证错误', async () => {
    mockFetch(new Response('', { status: 401 }))
    const locationStub = stubLocation()

    const error = await backendApi.get('/api/webui/status').then(
      () => {
        throw new Error('预期抛出 ApiError，但请求成功了')
      },
      (reason: unknown) => reason
    )

    expect(error).toBeInstanceOf(ApiError)
    expect(error).toMatchObject({
      message: '认证失败，请重新登录',
      status: 401,
    })
    expect(locationStub.href).toBe('/auth')
  })

  it('authApi 遇到 401 不跳转登录页，透传后端错误信息', async () => {
    mockFetch(jsonResponse({ detail: 'Token 无效' }, { status: 401 }))
    const locationStub = stubLocation('http://localhost/setup')

    await expect(authApi.get('/api/webui/auth/check')).rejects.toMatchObject({
      message: 'Token 无效',
      status: 401,
    })
    expect(locationStub.href).toBe('http://localhost/setup')
  })
})
