import { afterEach, describe, expect, it, vi } from 'vitest'

import { createApiClient } from '../client'
import { ApiError } from '../errors'

/** 构造一个文本/JSON 响应 */
function makeResponse(body: string, init: ResponseInit = {}): Response {
  return new Response(body, { status: 200, ...init })
}

function jsonResponse(data: unknown, init: ResponseInit = {}): Response {
  return makeResponse(JSON.stringify(data), init)
}

function mockFetch(response: Response | Error): ReturnType<typeof vi.fn> {
  // Response 的 body 只能消费一次，每次调用返回克隆以支持同一桩响应多次请求
  const fn = vi.fn(() =>
    response instanceof Error ? Promise.reject(response) : Promise.resolve(response.clone())
  )
  vi.stubGlobal('fetch', fn)
  return fn
}

/** 断言请求抛出 ApiError 并返回它，便于进一步检查字段 */
async function expectApiError(promise: Promise<unknown>): Promise<ApiError> {
  try {
    await promise
  } catch (error) {
    expect(error).toBeInstanceOf(ApiError)
    return error as ApiError
  }
  throw new Error('预期抛出 ApiError，但请求成功了')
}

function makeClient(overrides: Partial<Parameters<typeof createApiClient>[0]> = {}) {
  return createApiClient({
    resolveBaseUrl: () => '',
    ...overrides,
  })
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('createApiClient', () => {
  describe('URL 构建', () => {
    it('拼接 base URL 与路径', async () => {
      const fetchMock = mockFetch(jsonResponse({ ok: true }))
      const client = makeClient({ resolveBaseUrl: async () => 'http://backend:8000' })

      await client.get('/api/webui/chats')

      expect(fetchMock.mock.calls[0][0]).toBe('http://backend:8000/api/webui/chats')
    })

    it('序列化 query 参数：跳过 undefined/null，布尔转字符串，数组展开', async () => {
      const fetchMock = mockFetch(jsonResponse({}))
      const client = makeClient()

      await client.get('/api/list', {
        query: {
          page: 2,
          search: undefined,
          chat_id: null,
          include_legacy: true,
          ids: [1, 2],
        },
      })

      expect(fetchMock.mock.calls[0][0]).toBe('/api/list?page=2&include_legacy=true&ids=1&ids=2')
    })

    it('路径已含 query 时用 & 续接', async () => {
      const fetchMock = mockFetch(jsonResponse({}))
      const client = makeClient()

      await client.get('/api/list?limit=10', { query: { page: 1 } })

      expect(fetchMock.mock.calls[0][0]).toBe('/api/list?limit=10&page=1')
    })
  })

  describe('请求体与请求头', () => {
    it('对象 body 自动 JSON 序列化并携带 Content-Type', async () => {
      const fetchMock = mockFetch(jsonResponse({}))
      const client = makeClient()

      await client.post('/api/create', { body: { name: '麦麦' } })

      const init = fetchMock.mock.calls[0][1] as RequestInit
      expect(init.body).toBe(JSON.stringify({ name: '麦麦' }))
      expect(init.headers).toMatchObject({ 'Content-Type': 'application/json' })
    })

    it('FormData 原样发送且不设置 Content-Type', async () => {
      const fetchMock = mockFetch(jsonResponse({}))
      const client = makeClient()
      const formData = new FormData()
      formData.append('file', new Blob(['x']), 'a.txt')

      await client.post('/api/upload', { body: formData })

      const init = fetchMock.mock.calls[0][1] as RequestInit
      expect(init.body).toBe(formData)
      expect(init.headers).not.toHaveProperty('Content-Type')
    })

    it('cache 选项透传给 fetch', async () => {
      const fetchMock = mockFetch(jsonResponse({}))
      const client = makeClient()

      await client.get('/api/config', { cache: 'no-store' })

      expect((fetchMock.mock.calls[0][1] as RequestInit).cache).toBe('no-store')
    })

    it('cookie 认证实例携带 credentials: include，none 实例不携带', async () => {
      const fetchMock = mockFetch(jsonResponse({}))
      await makeClient({ auth: 'cookie' }).get('/a')
      await makeClient({ auth: 'none' }).get('/b')

      expect((fetchMock.mock.calls[0][1] as RequestInit).credentials).toBe('include')
      expect((fetchMock.mock.calls[1][1] as RequestInit).credentials).toBeUndefined()
    })
  })

  describe('错误处理', () => {
    it('HTTP 失败时抛出 ApiError：message 经 formatApiError 格式化，携带 status 与 detail', async () => {
      mockFetch(jsonResponse({ detail: '资源不存在' }, { status: 404 }))
      const client = makeClient()

      const error = await expectApiError(client.get('/api/x', { errorMessage: '获取失败' }))

      expect(error).toBeInstanceOf(ApiError)
      expect(error.message).toBe('资源不存在')
      expect(error.status).toBe(404)
      expect(error.detail).toEqual({ detail: '资源不存在' })
    })

    it('HTTP 失败且错误体无可用信息时使用 errorMessage 文案', async () => {
      mockFetch(jsonResponse({}, { status: 500 }))
      const client = makeClient()

      const error = await expectApiError(client.get('/api/x', { errorMessage: '获取聊天列表失败' }))

      expect(error.message).toBe('获取聊天列表失败')
    })

    it('FastAPI 校验错误数组被格式化为可读文本', async () => {
      mockFetch(
        jsonResponse(
          { detail: [{ loc: ['query', 'page'], msg: 'Input should be a valid integer' }] },
          { status: 422 }
        )
      )
      const client = makeClient()

      const error = await expectApiError(client.get('/api/x'))

      expect(error.message).toBe('query.page: Input should be a valid integer')
    })

    it('401 时调用 onUnauthorized 并抛出认证错误', async () => {
      mockFetch(makeResponse('', { status: 401 }))
      const onUnauthorized = vi.fn()
      const client = makeClient({ auth: 'cookie', onUnauthorized })

      const error = await expectApiError(client.get('/api/x'))

      expect(onUnauthorized).toHaveBeenCalledOnce()
      expect(error).toBeInstanceOf(ApiError)
      expect(error.status).toBe(401)
    })

    it('cookie 实例未配置 onUnauthorized 时，401 走普通错误路径并透传后端信息', async () => {
      mockFetch(jsonResponse({ detail: 'Token 无效' }, { status: 401 }))
      const client = makeClient({ auth: 'cookie' })

      const error = await expectApiError(client.post('/api/webui/auth/verify'))

      expect(error.message).toBe('Token 无效')
      expect(error.status).toBe(401)
    })

    it('auth: none 实例遇到 401 不触发 onUnauthorized，走普通错误路径', async () => {
      mockFetch(jsonResponse({ detail: '需要登录' }, { status: 401 }))
      const onUnauthorized = vi.fn()
      const client = makeClient({ auth: 'none', onUnauthorized })

      const error = await expectApiError(client.get('/api/x'))

      expect(onUnauthorized).not.toHaveBeenCalled()
      expect(error.message).toBe('需要登录')
    })

    it('网络层失败包装为 ApiError 且不带 status', async () => {
      mockFetch(new TypeError('Failed to fetch'))
      const client = makeClient()

      const error = await expectApiError(client.get('/api/x'))

      expect(error).toBeInstanceOf(ApiError)
      expect(error.message).toContain('网络请求失败')
      expect(error.message).toContain('Failed to fetch')
      expect(error.status).toBeUndefined()
    })
  })

  describe('路由未命中诊断', () => {
    it('成功状态但响应体是 HTML 页面时报出诊断信息与请求地址', async () => {
      mockFetch(makeResponse('<!DOCTYPE html><html><body>app</body></html>'))
      const client = makeClient()

      const error = await expectApiError(client.get('/api/webui/memory/graph'))

      expect(error).toBeInstanceOf(ApiError)
      expect(error.message).toContain('未命中后端 API 路由')
      expect(error.message).toContain('/api/webui/memory/graph')
    })

    it('404 且响应体是 HTML 页面时同样报出诊断信息', async () => {
      mockFetch(makeResponse('<html><body>Not Found</body></html>', { status: 404 }))
      const client = makeClient()

      const error = await expectApiError(client.get('/api/x'))

      expect(error.message).toContain('未命中后端 API 路由')
      expect(error.status).toBe(404)
    })
  })

  describe('响应解析', () => {
    it('默认解析 JSON', async () => {
      mockFetch(jsonResponse({ value: 42 }))
      const client = makeClient()

      await expect(client.get<{ value: number }>('/api/x')).resolves.toEqual({ value: 42 })
    })

    it('parse: text 返回原始文本', async () => {
      mockFetch(makeResponse('plain content'))
      const client = makeClient()

      await expect(client.get('/api/x', { parse: 'text' })).resolves.toBe('plain content')
    })

    it('parse: response 返回原始 Response，body 未被消费', async () => {
      mockFetch(jsonResponse({ value: 1 }))
      const client = makeClient()

      const result = await client.get<Response>('/api/x', { parse: 'response' })

      expect(result).toBeInstanceOf(Response)
      await expect(result.json()).resolves.toEqual({ value: 1 })
    })

    it('空响应体在 JSON 模式下显式报错', async () => {
      mockFetch(makeResponse(''))
      const client = makeClient()

      await expect(client.get('/api/x')).rejects.toThrow('接口返回了空响应')
    })

    it('非法 JSON 在 JSON 模式下显式报错', async () => {
      mockFetch(makeResponse('not-json'))
      const client = makeClient()

      await expect(client.get('/api/x')).rejects.toThrow('接口响应不是合法 JSON')
    })
  })
})
