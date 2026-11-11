import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { getApiBaseUrl, getControlApiBaseUrl, getWsBaseUrl, resolveApiPath } from '@/lib/api-base'
import { authApi, backendApi } from '@/lib/http/instances'

import { bootstrapHostRuntime } from '../api'
import { getHostRuntime, scopeBotResourcePath, setHostRuntime } from '../state'

const fetchMock = vi.fn()

beforeEach(() => {
  sessionStorage.clear()
  setHostRuntime({
    host: false,
    authenticated: false,
    selected_bot_id: null,
    selected_bot_running: false,
    bots: [],
  })
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

function json(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('统一 WebUI 的 Bot 归属', () => {
  it('兼容没有 Host API 的单 Bot 后端', async () => {
    fetchMock.mockResolvedValue(json({ detail: 'Not Found' }, 404))
    await bootstrapHostRuntime()
    expect(getHostRuntime().host).toBe(false)
    expect(await getApiBaseUrl()).toBe('')
  })

  it('另一个标签页改变默认选择后，刷新仍读取本标签页固定的 Bot', async () => {
    const key = `maibot-selected-bot:${window.location.origin}`
    sessionStorage.setItem(key, 'bot-a')
    fetchMock.mockImplementation((url: string) =>
      Promise.resolve(
        url.includes('/mode')
          ? json({
              host: true,
              authenticated: true,
              selected_bot_id: 'bot-b',
              selected_bot_running: true,
            })
          : json({
              workspace: '/host',
              bots: [
                { bot_id: 'bot-a', status: 'stopped' },
                { bot_id: 'bot-b', status: 'running' },
              ],
            })
      )
    )
    await bootstrapHostRuntime()
    expect(fetchMock.mock.calls[0][0]).toBe('/api/host/mode?bot_id=bot-a')
    expect(getHostRuntime().selected_bot_id).toBe('bot-a')
    expect(getHostRuntime().selected_bot_running).toBe(false)
    expect(await getApiBaseUrl()).toBe('/api/host/bots/bot-a/proxy')
  })

  it('HTTP、WebSocket 与资源固定到同一 Bot，认证请求保持在宿主', async () => {
    setHostRuntime({
      host: true,
      authenticated: true,
      selected_bot_id: 'bot-a',
      selected_bot_running: true,
      bots: [],
    })
    const prefix = '/api/host/bots/bot-a/proxy'
    expect(await getControlApiBaseUrl()).toBe('')
    expect(await resolveApiPath('/api/webui/avatar')).toBe(`${prefix}/api/webui/avatar`)
    expect(await resolveApiPath(`${prefix}/api/webui/avatar`)).toBe(`${prefix}/api/webui/avatar`)
    expect(scopeBotResourcePath('https://example.com/photo.png')).toBe(
      'https://example.com/photo.png'
    )
    expect(await getWsBaseUrl()).toBe(`${window.location.origin.replace(/^http/, 'ws')}${prefix}`)
    fetchMock.mockImplementation(() => Promise.resolve(json({ authenticated: true })))
    await authApi.get('/api/webui/auth/check')
    expect(fetchMock.mock.calls.at(-1)?.[0]).toBe('/api/webui/auth/check')
    await backendApi.post('/api/webui/config/bot', { body: { nickname: 'A' } })
    expect(fetchMock.mock.calls.at(-1)?.[0]).toBe(`${prefix}/api/webui/config/bot`)
  })

  it('未选择 Bot 时不会把业务写入宿主或默认 Bot', async () => {
    setHostRuntime({
      host: true,
      authenticated: true,
      selected_bot_id: null,
      selected_bot_running: false,
      bots: [],
    })
    await expect(backendApi.post('/api/webui/config/bot', { body: {} })).rejects.toThrow('选择')
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('先兑换一次性票据，再读取已登录状态和默认 Bot', async () => {
    window.history.replaceState(null, '', '/bots#ticket=once')
    fetchMock.mockImplementation((url: string) => {
      expect(window.location.hash).toBe('')
      if (url === '/api/host/session') return Promise.resolve(json({ authenticated: true }))
      if (url.includes('/mode'))
        return Promise.resolve(
          json({
            host: true,
            authenticated: true,
            selected_bot_id: 'bot-a',
            selected_bot_running: true,
          })
        )
      return Promise.resolve(
        json({ workspace: '/host', bots: [{ bot_id: 'bot-a', status: 'running' }] })
      )
    })
    await bootstrapHostRuntime()
    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      '/api/host/session',
      '/api/host/mode',
      '/api/host/bots',
    ])
    expect(getHostRuntime().authenticated).toBe(true)
    expect(getHostRuntime().selected_bot_id).toBe('bot-a')
    window.history.replaceState(null, '', '/')
  })
})
