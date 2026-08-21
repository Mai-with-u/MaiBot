import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { BackendConnection, ElectronAPI } from '@/types/electron'

const { isElectronMock } = vi.hoisted(() => ({
  isElectronMock: vi.fn(),
}))

vi.mock('./runtime', () => ({
  isElectron: isElectronMock,
}))

const originalLocation = window.location

function stubLocation(overrides: { host?: string; protocol?: string }): void {
  Object.defineProperty(window, 'location', {
    configurable: true,
    writable: true,
    value: {
      protocol: overrides.protocol ?? 'http:',
      host: overrides.host ?? 'localhost:5173',
    },
  })
}

function restoreLocation(): void {
  Object.defineProperty(window, 'location', {
    configurable: true,
    writable: true,
    value: originalLocation,
  })
}

function installElectronAPI(overrides: Partial<ElectronAPI> = {}): ElectronAPI {
  const api = {
    getActiveBackendUrl: vi.fn().mockResolvedValue(null),
    ...overrides,
  } as unknown as ElectronAPI
  window.electronAPI = api
  return api
}

describe('api-base', () => {
  beforeEach(() => {
    isElectronMock.mockReturnValue(false)
    delete window.electronAPI
  })

  afterEach(() => {
    restoreLocation()
    delete window.electronAPI
    vi.unstubAllEnvs()
  })

  it('浏览器环境下 HTTP 基址为空，路径原样返回', async () => {
    const { getApiBaseUrl, resolveApiPath } = await import('./api-base')

    await expect(getApiBaseUrl()).resolves.toBe('')
    await expect(resolveApiPath('/api/webui/status')).resolves.toBe('/api/webui/status')
  })

  it('Electron 使用活动后端 URL，缺失时回落为空字符串', async () => {
    isElectronMock.mockReturnValue(true)
    const api = installElectronAPI({
      getActiveBackendUrl: vi.fn().mockResolvedValue('http://127.0.0.1:8000'),
    })
    const { getApiBaseUrl } = await import('./api-base')

    await expect(getApiBaseUrl()).resolves.toBe('http://127.0.0.1:8000')
    expect(api.getActiveBackendUrl).toHaveBeenCalledTimes(1)

    installElectronAPI({
      getActiveBackendUrl: vi.fn().mockResolvedValue(null),
    })
    await expect(getApiBaseUrl()).resolves.toBe('')
  })

  it('Electron 未注入 electronAPI 时基址为空字符串', async () => {
    isElectronMock.mockReturnValue(true)
    delete window.electronAPI
    const { getApiBaseUrl } = await import('./api-base')

    await expect(getApiBaseUrl()).resolves.toBe('')
  })

  it('Electron 把 http/https 后端地址改写成 ws/wss，空地址直接返回空', async () => {
    isElectronMock.mockReturnValue(true)
    const { getWsBaseUrl } = await import('./api-base')

    installElectronAPI({
      getActiveBackendUrl: vi.fn().mockResolvedValue('http://127.0.0.1:8000'),
    })
    await expect(getWsBaseUrl()).resolves.toBe('ws://127.0.0.1:8000')

    installElectronAPI({
      getActiveBackendUrl: vi.fn().mockResolvedValue('https://backend.example.com:8443'),
    })
    await expect(getWsBaseUrl()).resolves.toBe('wss://backend.example.com:8443')

    installElectronAPI({
      getActiveBackendUrl: vi.fn().mockResolvedValue(null),
    })
    await expect(getWsBaseUrl()).resolves.toBe('')
  })

  it('浏览器开发环境按当前页面协议构造同源 WS 地址', async () => {
    const { getWsBaseUrl } = await import('./api-base')

    stubLocation({ protocol: 'http:', host: 'localhost:5173' })
    await expect(getWsBaseUrl()).resolves.toBe('ws://localhost:5173')

    stubLocation({ protocol: 'https:', host: 'dev.example.com' })
    await expect(getWsBaseUrl()).resolves.toBe('wss://dev.example.com')
  })

  it('浏览器生产环境同样按页面协议构造 WS 地址', async () => {
    vi.stubEnv('DEV', false)
    vi.stubEnv('MODE', 'production')
    vi.resetModules()
    stubLocation({ protocol: 'https:', host: 'prod.example.com' })

    const { getWsBaseUrl } = await import('./api-base')
    await expect(getWsBaseUrl()).resolves.toBe('wss://prod.example.com')

    stubLocation({ protocol: 'http:', host: 'prod.example.com' })
    await expect(getWsBaseUrl()).resolves.toBe('ws://prod.example.com')
  })

  it('Electron 在基址非空时拼接完整 API 路径，否则返回原路径', async () => {
    isElectronMock.mockReturnValue(true)
    const { resolveApiPath } = await import('./api-base')

    installElectronAPI({
      getActiveBackendUrl: vi.fn().mockResolvedValue('http://127.0.0.1:8000'),
    })
    await expect(resolveApiPath('/api/webui/status')).resolves.toBe(
      'http://127.0.0.1:8000/api/webui/status'
    )

    installElectronAPI({
      getActiveBackendUrl: vi.fn().mockResolvedValue(''),
    })
    await expect(resolveApiPath('/api/webui/status')).resolves.toBe('/api/webui/status')
  })

  it('浏览器下订阅后端地址变化是空操作', async () => {
    const { onBackendUrlChanged } = await import('./api-base')
    const callback = vi.fn()

    const unsubscribe = onBackendUrlChanged(callback)
    expect(typeof unsubscribe).toBe('function')
    expect(callback).not.toHaveBeenCalled()
    expect(() => unsubscribe()).not.toThrow()
  })

  it('Electron 缺少 onBackendChanged 时订阅也是空操作', async () => {
    isElectronMock.mockReturnValue(true)
    installElectronAPI()
    const { onBackendUrlChanged } = await import('./api-base')

    const unsubscribe = onBackendUrlChanged(vi.fn())
    expect(() => unsubscribe()).not.toThrow()
  })

  it('Electron 把 BackendConnection.url 映射给回调，缺失时传 null', async () => {
    isElectronMock.mockReturnValue(true)
    let ipcListener: ((backend: BackendConnection | null) => void) | undefined
    const ipcUnsubscribe = vi.fn()
    installElectronAPI({
      onBackendChanged: vi.fn((listener) => {
        ipcListener = listener
        return ipcUnsubscribe
      }),
    })
    const { onBackendUrlChanged } = await import('./api-base')
    const callback = vi.fn()

    const unsubscribe = onBackendUrlChanged(callback)
    expect(ipcListener).toEqual(expect.any(Function))

    ipcListener?.({
      id: 'b1',
      name: '本地',
      url: 'http://127.0.0.1:8000',
      isDefault: true,
    })
    expect(callback).toHaveBeenCalledWith('http://127.0.0.1:8000')

    ipcListener?.(null)
    expect(callback).toHaveBeenCalledWith(null)

    unsubscribe()
    expect(ipcUnsubscribe).toHaveBeenCalledTimes(1)
  })

  it('Electron 后端对象缺少 url 时回调收到 null', async () => {
    isElectronMock.mockReturnValue(true)
    let ipcListener: ((backend: BackendConnection | null) => void) | undefined
    installElectronAPI({
      onBackendChanged: vi.fn((listener) => {
        ipcListener = listener
        return vi.fn()
      }),
    })
    const { onBackendUrlChanged } = await import('./api-base')
    const callback = vi.fn()

    onBackendUrlChanged(callback)
    ipcListener?.({ id: 'missing-url', name: '未配置', isDefault: false } as BackendConnection)

    expect(callback).toHaveBeenCalledWith(null)
  })
})
