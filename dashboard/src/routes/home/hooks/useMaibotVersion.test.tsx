import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  getVersionCompatibility,
  type VersionCompatibilityResult,
} from '@/lib/version-compatibility-api'

import { useMaibotVersion, type HitokotoSettings } from './useMaibotVersion'

vi.mock('react-i18next', () => {
  const t = (key: string) => key
  return { useTranslation: () => ({ t }) }
})

vi.mock('@/lib/version-compatibility-api', () => ({
  getVersionCompatibility: vi.fn(),
}))

const getVersionCompatibilityMock = vi.mocked(getVersionCompatibility)

const HITOKOTO_SETTINGS_STORAGE_KEY = 'maibot-home-hitokoto-settings-v1'
const HITOKOTO_INDEX_STORAGE_KEY = 'maibot-home-hitokoto-index-v1'

const compatible: VersionCompatibilityResult = {
  status: 'compatible',
  main_program_version: '1.0.0',
  webui_version: '1.0.0',
  required_webui_version: '1.0.0',
}

function jsonResponse(data: unknown, ok = true, status = 200) {
  return Promise.resolve({
    ok,
    status,
    json: async () => data,
  } as Response)
}

function mockBrowserFetch(
  handlers: {
    hitokoto?: () => ReturnType<typeof jsonResponse>
    github?: () => ReturnType<typeof jsonResponse>
  } = {}
) {
  vi.stubGlobal(
    'fetch',
    vi.fn((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes('github.com')) {
        return (
          handlers.github?.() ??
          jsonResponse([
            {
              tag_name: 'v2.0.0',
              draft: false,
              prerelease: false,
              html_url: 'https://github.com/Mai-with-u/MaiBot/releases/tag/v2.0.0',
            },
          ])
        )
      }
      if (url.includes('hitokoto')) {
        return handlers.hitokoto?.() ?? jsonResponse({ hitokoto: '测试一言', from: '来源' })
      }
      return jsonResponse({})
    }) as typeof fetch
  )
}

function createDeferred<T>() {
  let resolve: (value: T | PromiseLike<T>) => void = () => {}
  let reject: (reason?: unknown) => void = () => {}
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, resolve, reject }
}

function customSettings(
  items: HitokotoSettings['customItems'],
  defaultEnabled = false
): HitokotoSettings {
  return { defaultEnabled, customItems: items }
}

beforeEach(() => {
  localStorage.clear()
  getVersionCompatibilityMock.mockResolvedValue(compatible)
  mockBrowserFetch()
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.clearAllMocks()
  localStorage.clear()
})

describe('useMaibotVersion', () => {
  it('挂载时拉取版本兼容性与最新稳定版', async () => {
    mockBrowserFetch({
      github: () =>
        jsonResponse([
          {
            tag_name: 'v9.0.0',
            draft: true,
            prerelease: false,
            html_url: 'https://example.test/draft',
          },
          {
            tag_name: 'v8.0.0',
            draft: false,
            prerelease: true,
            html_url: 'https://example.test/pre',
          },
          {
            tag_name: 'V1.2.3',
            draft: false,
            prerelease: false,
            html_url: 'https://example.test/stable',
          },
        ]),
    })
    const { result } = renderHook(() => useMaibotVersion())

    await waitFor(() => {
      expect(result.current.versionCompatibility).toEqual(compatible)
      expect(result.current.maibotStableRelease).toEqual({
        version: '1.2.3',
        url: 'https://example.test/stable',
      })
    })
    expect(getVersionCompatibilityMock).toHaveBeenCalledWith(expect.any(AbortSignal))
    expect(fetch).toHaveBeenCalledWith(
      'https://api.github.com/repos/Mai-with-u/MaiBot/releases?per_page=20',
      { headers: { Accept: 'application/vnd.github+json' } }
    )
  })

  it('稳定版缺少 html_url 时回退到 releases 页', async () => {
    mockBrowserFetch({
      github: () => jsonResponse([{ tag_name: 'v3.1.0', draft: false, prerelease: false }]),
    })
    const { result } = renderHook(() => useMaibotVersion())

    await waitFor(() => {
      expect(result.current.maibotStableRelease).toEqual({
        version: '3.1.0',
        url: 'https://github.com/Mai-with-u/MaiBot/releases',
      })
    })
  })

  it('没有稳定版或 GitHub 请求失败时保持空态', async () => {
    const debugSpy = vi.spyOn(console, 'debug').mockImplementation(() => {})
    mockBrowserFetch({
      github: () => jsonResponse([{ tag_name: 'v0.1.0', draft: false, prerelease: true }]),
    })
    const first = renderHook(() => useMaibotVersion())
    await waitFor(() => expect(getVersionCompatibilityMock).toHaveBeenCalled())
    await act(async () => {
      await Promise.resolve()
    })
    expect(first.result.current.maibotStableRelease).toBeNull()
    first.unmount()

    mockBrowserFetch({
      github: () => jsonResponse([], false, 500),
    })
    const second = renderHook(() => useMaibotVersion())
    await waitFor(() => expect(debugSpy).toHaveBeenCalled())
    expect(second.result.current.maibotStableRelease).toBeNull()
    second.unmount()

    mockBrowserFetch({
      github: () => Promise.reject(new Error('网络错误')) as never,
    })
    const third = renderHook(() => useMaibotVersion())
    await waitFor(() => expect(debugSpy).toHaveBeenCalledTimes(2))
    expect(third.result.current.maibotStableRelease).toBeNull()
  })

  it('版本兼容性失败时记录调试信息并保持空态', async () => {
    const debugSpy = vi.spyOn(console, 'debug').mockImplementation(() => {})
    getVersionCompatibilityMock.mockRejectedValue(new Error('兼容性接口不可用'))
    const { result } = renderHook(() => useMaibotVersion())

    await waitFor(() => {
      expect(debugSpy).toHaveBeenCalledWith('检查版本匹配状态失败:', expect.any(Error))
    })
    expect(result.current.versionCompatibility).toBeNull()
  })

  it('卸载后丢弃过期的兼容性与 GitHub 结果', async () => {
    const compatibility = createDeferred<VersionCompatibilityResult>()
    const github = createDeferred<Response>()
    getVersionCompatibilityMock.mockReturnValue(compatibility.promise)
    mockBrowserFetch({
      github: () => github.promise,
    })
    const { result, unmount } = renderHook(() => useMaibotVersion())
    unmount()

    await act(async () => {
      compatibility.resolve({
        status: 'webui_outdated',
        main_program_version: '9.0.0',
        webui_version: '1.0.0',
        required_webui_version: '9.0.0',
      })
      github.resolve(
        (await jsonResponse([
          {
            tag_name: 'v9.9.9',
            draft: false,
            prerelease: false,
            html_url: 'https://example.test/late',
          },
        ])) as Response
      )
      await Promise.resolve()
    })

    expect(result.current.versionCompatibility).toBeNull()
    expect(result.current.maibotStableRelease).toBeNull()
  })

  it('读取默认一言成功', async () => {
    const { result } = renderHook(() => useMaibotVersion())
    expect(result.current.hitokotoLoading).toBe(true)
    expect(result.current.hitokoto).toBeNull()

    await act(async () => {
      await result.current.fetchHitokoto()
    })

    expect(result.current.hitokoto).toEqual({ hitokoto: '测试一言', from: '来源' })
    expect(result.current.hitokotoLoading).toBe(false)
    expect(fetch).toHaveBeenCalledWith('https://v1.hitokoto.cn/?c=a&c=b&c=c&c=d&c=h&c=i&c=k')
    expect(localStorage.getItem(HITOKOTO_INDEX_STORAGE_KEY)).toBe('1')
  })

  it('默认一言缺少 from 时回退 from_who 或未知来源', async () => {
    mockBrowserFetch({
      hitokoto: () => jsonResponse({ hitokoto: '只有作者', from: '', from_who: '作者' }),
    })
    const first = renderHook(() => useMaibotVersion())
    await act(async () => {
      await first.result.current.fetchHitokoto()
    })
    expect(first.result.current.hitokoto).toEqual({ hitokoto: '只有作者', from: '作者' })
    first.unmount()

    mockBrowserFetch({
      hitokoto: () => jsonResponse({ hitokoto: '无名', from: '', from_who: '' }),
    })
    const second = renderHook(() => useMaibotVersion())
    await act(async () => {
      await second.result.current.fetchHitokoto()
    })
    expect(second.result.current.hitokoto).toEqual({
      hitokoto: '无名',
      from: 'home.unknownSource',
    })
  })

  it('默认一言失败且无自定义内容时使用 fallback', async () => {
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    mockBrowserFetch({
      hitokoto: () => jsonResponse({}, false, 503),
    })
    const { result } = renderHook(() => useMaibotVersion())

    await act(async () => {
      await result.current.fetchHitokoto()
    })

    expect(result.current.hitokoto).toEqual({
      hitokoto: 'home.hitokotoFallback',
      from: 'home.hitokotoFallbackFrom',
    })
    expect(errorSpy).toHaveBeenCalledWith('获取默认一言失败:', expect.any(Error))
  })

  it('默认一言失败但有自定义内容时不使用 fallback', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    localStorage.setItem(
      HITOKOTO_SETTINGS_STORAGE_KEY,
      JSON.stringify(customSettings([{ id: 'c1', content: '自定义一句', source: '手册' }], true))
    )
    mockBrowserFetch({
      hitokoto: () => Promise.reject(new Error('一言挂了')) as never,
    })
    const { result } = renderHook(() => useMaibotVersion())

    await act(async () => {
      await result.current.fetchHitokoto()
    })

    expect(result.current.hitokoto).toEqual({ hitokoto: '自定义一句', from: '手册' })
  })

  it('停用默认来源且自定义列表为空时一言为空', async () => {
    localStorage.setItem(HITOKOTO_SETTINGS_STORAGE_KEY, JSON.stringify(customSettings([], false)))
    const { result } = renderHook(() => useMaibotVersion())

    await act(async () => {
      await result.current.fetchHitokoto()
    })

    expect(result.current.hitokoto).toBeNull()
    expect(result.current.hitokotoLoading).toBe(false)
    expect(
      vi.mocked(fetch).mock.calls.some(([input]) => String(input).includes('hitokoto.cn'))
    ).toBe(false)
  })

  it('按本地索引轮换自定义一言', async () => {
    localStorage.setItem(
      HITOKOTO_SETTINGS_STORAGE_KEY,
      JSON.stringify(
        customSettings(
          [
            { id: 'a', content: '第一句', source: 'A' },
            { id: 'b', content: '第二句', source: 'B' },
          ],
          false
        )
      )
    )
    localStorage.setItem(HITOKOTO_INDEX_STORAGE_KEY, '1')
    const { result } = renderHook(() => useMaibotVersion())

    await act(async () => {
      await result.current.fetchHitokoto()
    })
    expect(result.current.hitokoto).toEqual({ hitokoto: '第二句', from: 'B' })
    expect(localStorage.getItem(HITOKOTO_INDEX_STORAGE_KEY)).toBe('2')

    await act(async () => {
      await result.current.fetchHitokoto()
    })
    expect(result.current.hitokoto).toEqual({ hitokoto: '第一句', from: 'A' })
  })

  it('非法本地设置与索引回退默认，损坏 JSON 会记录错误', async () => {
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    localStorage.setItem(HITOKOTO_SETTINGS_STORAGE_KEY, '{not-json')
    localStorage.setItem(HITOKOTO_INDEX_STORAGE_KEY, '-3')
    const broken = renderHook(() => useMaibotVersion())
    expect(broken.result.current.hitokotoSettings).toEqual({
      defaultEnabled: true,
      customItems: [],
    })
    expect(errorSpy).toHaveBeenCalledWith('读取一言设置失败:', expect.any(Error))
    broken.unmount()

    localStorage.setItem(
      HITOKOTO_SETTINGS_STORAGE_KEY,
      JSON.stringify({
        defaultEnabled: 'yes',
        customItems: [
          { id: 1, content: '数字 id', source: 'x' },
          { id: 'ok', content: '  有效  ', source: '  出处  ' },
          { id: 'empty', content: '   ', source: '空' },
          null,
        ],
      })
    )
    localStorage.setItem(HITOKOTO_INDEX_STORAGE_KEY, 'nope')
    const normalized = renderHook(() => useMaibotVersion())
    expect(normalized.result.current.hitokotoSettings).toEqual({
      defaultEnabled: true,
      customItems: [{ id: 'ok', content: '有效', source: '出处' }],
    })
  })

  it('保存设置会规范化、重置索引并重新加载一言', async () => {
    const { result } = renderHook(() => useMaibotVersion())

    await act(async () => {
      await result.current.saveHitokotoSettings({
        defaultEnabled: false,
        customItems: [
          { id: 'keep', content: '  留下  ', source: '  书  ' },
          { id: 'drop', content: '   ', source: '无' },
        ],
      })
    })

    expect(result.current.hitokotoSettings).toEqual({
      defaultEnabled: false,
      customItems: [{ id: 'keep', content: '留下', source: '书' }],
    })
    expect(JSON.parse(localStorage.getItem(HITOKOTO_SETTINGS_STORAGE_KEY) ?? '{}')).toEqual({
      defaultEnabled: false,
      customItems: [{ id: 'keep', content: '留下', source: '书' }],
    })
    expect(localStorage.getItem(HITOKOTO_INDEX_STORAGE_KEY)).toBe('1')
    expect(result.current.hitokoto).toEqual({ hitokoto: '留下', from: '书' })
    expect(result.current.hitokotoLoading).toBe(false)
  })

  it('保存空自定义且关闭默认来源后一言为空', async () => {
    const { result } = renderHook(() => useMaibotVersion())

    await act(async () => {
      await result.current.saveHitokotoSettings({ defaultEnabled: false, customItems: [] })
    })

    expect(result.current.hitokoto).toBeNull()
    expect(result.current.hitokotoSettings.defaultEnabled).toBe(false)
  })

  it('卸载后完成的一言请求不会回写', async () => {
    const hitokoto = createDeferred<Response>()
    mockBrowserFetch({
      hitokoto: () => hitokoto.promise,
    })
    const { result, unmount } = renderHook(() => useMaibotVersion())

    let pending: Promise<void> = Promise.resolve()
    act(() => {
      pending = result.current.fetchHitokoto()
    })
    unmount()

    await act(async () => {
      hitokoto.resolve((await jsonResponse({ hitokoto: '迟到', from: '晚' })) as Response)
      await pending
    })
  })
})
