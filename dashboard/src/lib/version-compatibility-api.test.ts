import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { getVersionCompatibility } from './version-compatibility-api'
import { APP_VERSION } from './version'

const COMPATIBILITY_PATH = '/api/webui/version-compatibility'

function compatibilityUrl(baseUrl = '') {
  const query = new URLSearchParams({ webui_version: APP_VERSION })
  return `${baseUrl}${COMPATIBILITY_PATH}?${query.toString()}`
}

function mockFetch(response: Response): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn().mockResolvedValue(response)
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function jsonResponse(data: unknown, init: ResponseInit = {}): Response {
  const headers = new Headers(init.headers)
  if (!headers.has('content-type')) {
    headers.set('content-type', 'application/json')
  }
  return new Response(JSON.stringify(data), { status: 200, ...init, headers })
}

afterEach(() => {
  vi.unstubAllGlobals()
})

beforeEach(() => {
  vi.unstubAllGlobals()
})

describe('getVersionCompatibility', () => {
  it('请求免登录接口并在载荷合法时原样返回', async () => {
    const payload = {
      status: 'compatible' as const,
      main_program_version: '1.2.3',
      webui_version: APP_VERSION,
      required_webui_version: '1.7.0',
    }
    const fetchMock = mockFetch(jsonResponse(payload))
    const signal = new AbortController().signal

    await expect(getVersionCompatibility(signal)).resolves.toEqual(payload)
    expect(fetchMock).toHaveBeenCalledWith(compatibilityUrl(), {
      credentials: 'include',
      signal,
      cache: 'no-store',
    })
  })

  it.each(['webui_outdated', 'main_program_outdated'] as const)(
    '接受状态 %s 的合法结果',
    async (status) => {
      const payload = {
        status,
        main_program_version: '1.0.0',
        webui_version: '2.0.0',
        required_webui_version: '2.0.1',
      }
      mockFetch(jsonResponse(payload, { headers: { 'content-type': 'application/json; charset=utf-8' } }))

      await expect(getVersionCompatibility()).resolves.toEqual(payload)
    }
  )

  it.each([404, 405])('HTTP %s 视为主程序未提供兼容性接口', async (status) => {
    mockFetch(new Response('not found', { status }))

    await expect(getVersionCompatibility()).rejects.toThrow('当前主程序未提供版本兼容性检查接口')
  })

  it('200 但 Content-Type 是 HTML 时同样视为接口不存在', async () => {
    mockFetch(
      new Response('<!doctype html><html><body>login</body></html>', {
        status: 200,
        headers: { 'content-type': 'TEXT/HTML; charset=utf-8' },
      })
    )

    await expect(getVersionCompatibility()).rejects.toThrow('当前主程序未提供版本兼容性检查接口')
  })

  it('其他失败状态码抛出带 HTTP 码的检查失败错误', async () => {
    mockFetch(new Response('boom', { status: 500 }))

    await expect(getVersionCompatibility()).rejects.toThrow('版本兼容性检查失败（HTTP 500）')
  })

  it('响应不是合法 JSON 时把解析错误原样抛出', async () => {
    mockFetch(
      new Response('<html>not json</html>', {
        status: 200,
        headers: { 'content-type': 'application/json' },
      })
    )

    await expect(getVersionCompatibility()).rejects.toThrow(SyntaxError)
  })

  it.each([
    ['非对象', 'compatible'],
    ['缺少版本字段', { status: 'compatible', main_program_version: '1.0.0', webui_version: '1.0.0' }],
    [
      '版本字段不是字符串',
      {
        status: 'compatible',
        main_program_version: 1,
        webui_version: '1.0.0',
        required_webui_version: '1.0.0',
      },
    ],
    [
      '未知状态',
      {
        status: 'unknown',
        main_program_version: '1.0.0',
        webui_version: '1.0.0',
        required_webui_version: '1.0.0',
      },
    ],
    ['空对象', {}],
    ['null', null],
  ] as const)('载荷 %s 视为无效数据', async (_label, payload) => {
    mockFetch(jsonResponse(payload))

    await expect(getVersionCompatibility()).rejects.toThrow('版本兼容性接口返回了无效数据')
  })
})
