/**
 * useBackendConnections Hook 测试。
 *
 * Electron IPC 能力通过 window.electronAPI 桩实现，
 * 运行环境分支（Electron / 浏览器）通过 mock @/lib/runtime 的 isElectron 控制。
 */
import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { BackendConnection, ElectronAPI } from '@/types/electron'

import { useBackendConnections } from '../useBackendConnections'

// vi.hoisted 保证 mock 工厂内引用的是稳定的 vi.fn
const { isElectronMock } = vi.hoisted(() => ({ isElectronMock: vi.fn() }))

vi.mock('@/lib/runtime', () => ({
  isElectron: isElectronMock,
}))

const backendA: BackendConnection = {
  id: 'b1',
  name: '本地后端',
  url: 'http://127.0.0.1:8000',
  isDefault: true,
}
const backendB: BackendConnection = {
  id: 'b2',
  name: '远程后端',
  url: 'http://10.0.0.2:8000',
  isDefault: false,
}

/** 构造仅包含 Hook 用到的方法的 electronAPI 桩 */
function createElectronStub() {
  return {
    getBackends: vi.fn().mockResolvedValue([backendA, backendB]),
    getActiveBackend: vi.fn().mockResolvedValue(backendA),
    addBackend: vi.fn().mockResolvedValue(backendB),
    updateBackend: vi.fn().mockResolvedValue(undefined),
    removeBackend: vi.fn().mockResolvedValue(undefined),
    setActiveBackend: vi.fn().mockResolvedValue(undefined),
  }
}

type ElectronStub = ReturnType<typeof createElectronStub>

/** 安装 electronAPI 桩到 window 上，返回桩对象以便断言调用 */
function installElectronAPI(overrides: Partial<ElectronStub> = {}): ElectronStub {
  const api = { ...createElectronStub(), ...overrides }
  window.electronAPI = api as unknown as ElectronAPI
  return api
}

afterEach(() => {
  delete window.electronAPI
})

describe('useBackendConnections', () => {
  it('挂载后加载后端列表与当前活动后端', async () => {
    isElectronMock.mockReturnValue(true)
    installElectronAPI()

    const { result } = renderHook(() => useBackendConnections())
    expect(result.current.loading).toBe(true)

    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.backends).toEqual([backendA, backendB])
    expect(result.current.activeId).toBe('b1')
  })

  it('没有活动后端时 activeId 为 null', async () => {
    isElectronMock.mockReturnValue(true)
    installElectronAPI({ getActiveBackend: vi.fn().mockResolvedValue(null) })

    const { result } = renderHook(() => useBackendConnections())
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.activeId).toBeNull()
  })

  it('非 Electron 环境下不发起任何 IPC 调用', async () => {
    isElectronMock.mockReturnValue(false)
    const api = installElectronAPI()

    const { result } = renderHook(() => useBackendConnections())
    // 等一轮微任务，确认 refresh 已短路返回
    await act(async () => {})

    expect(api.getBackends).not.toHaveBeenCalled()
    expect(api.getActiveBackend).not.toHaveBeenCalled()
    expect(result.current.backends).toEqual([])
    expect(result.current.loading).toBe(true)
  })

  it('addBackend 把新连接转发给 IPC 并刷新列表', async () => {
    isElectronMock.mockReturnValue(true)
    const api = installElectronAPI()

    const { result } = renderHook(() => useBackendConnections())
    await waitFor(() => expect(result.current.loading).toBe(false))

    const draft: Omit<BackendConnection, 'id'> = {
      name: '新后端',
      url: 'http://new:8000',
      isDefault: false,
    }
    await act(async () => {
      await result.current.addBackend(draft)
    })

    expect(api.addBackend).toHaveBeenCalledWith(draft)
    // 挂载时一次 + 新增后刷新一次
    expect(api.getBackends).toHaveBeenCalledTimes(2)
  })

  it('updateBackend 转发 id 与补丁并刷新列表', async () => {
    isElectronMock.mockReturnValue(true)
    const api = installElectronAPI()

    const { result } = renderHook(() => useBackendConnections())
    await waitFor(() => expect(result.current.loading).toBe(false))

    await act(async () => {
      await result.current.updateBackend('b2', { name: '改名后端' })
    })

    expect(api.updateBackend).toHaveBeenCalledWith('b2', { name: '改名后端' })
    expect(api.getBackends).toHaveBeenCalledTimes(2)
  })

  it('removeBackend 转发 id 并刷新列表', async () => {
    isElectronMock.mockReturnValue(true)
    const api = installElectronAPI()

    const { result } = renderHook(() => useBackendConnections())
    await waitFor(() => expect(result.current.loading).toBe(false))

    await act(async () => {
      await result.current.removeBackend('b1')
    })

    expect(api.removeBackend).toHaveBeenCalledWith('b1')
    expect(api.getBackends).toHaveBeenCalledTimes(2)
  })

  it('switchBackend 设置活动后端并更新 activeId', async () => {
    isElectronMock.mockReturnValue(true)
    const api = installElectronAPI()

    const { result } = renderHook(() => useBackendConnections())
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.activeId).toBe('b1')

    // jsdom 未实现整页导航：location.reload 只会向 stderr 打一条
    // "Not implemented: navigation" 日志而不会抛错，可以安全调用
    await act(async () => {
      await result.current.switchBackend('b2')
    })

    expect(api.setActiveBackend).toHaveBeenCalledWith('b2')
    expect(result.current.activeId).toBe('b2')
    // 切换后端不触发 refresh（依赖整页刷新重建状态）
    expect(api.getBackends).toHaveBeenCalledTimes(1)
  })

  it('非 Electron 环境下增删改切操作全部短路', async () => {
    isElectronMock.mockReturnValue(false)
    const api = installElectronAPI()

    const { result } = renderHook(() => useBackendConnections())
    await act(async () => {
      await result.current.addBackend({ name: 'x', url: 'http://x', isDefault: false })
      await result.current.updateBackend('b1', { name: 'y' })
      await result.current.removeBackend('b1')
      await result.current.switchBackend('b2')
    })

    expect(api.addBackend).not.toHaveBeenCalled()
    expect(api.updateBackend).not.toHaveBeenCalled()
    expect(api.removeBackend).not.toHaveBeenCalled()
    expect(api.setActiveBackend).not.toHaveBeenCalled()
  })
})
