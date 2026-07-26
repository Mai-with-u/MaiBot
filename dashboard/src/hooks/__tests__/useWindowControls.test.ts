/**
 * useWindowControls Hook 测试。
 *
 * 窗口控制走 window.electronAPI 桩；最大化事件通过捕获注册的回调手动触发，
 * 运行环境分支通过 mock @/lib/runtime 的 isElectron 控制。
 */
import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { ElectronAPI } from '@/types/electron'

import { useWindowControls } from '../useWindowControls'

// vi.hoisted 保证 mock 工厂内引用的是稳定的 vi.fn
const { isElectronMock } = vi.hoisted(() => ({ isElectronMock: vi.fn() }))

vi.mock('@/lib/runtime', () => ({
  isElectron: isElectronMock,
}))

/** 安装 electronAPI 桩，捕获最大化/还原事件回调并返回退订间谍 */
function installElectronAPI(initialMaximized = false) {
  const listeners = {
    maximized: [] as Array<() => void>,
    unmaximized: [] as Array<() => void>,
  }
  const unsubMax = vi.fn()
  const unsubUnmax = vi.fn()
  const api = {
    isMaximized: vi.fn().mockResolvedValue(initialMaximized),
    onWindowMaximized: vi.fn((callback: () => void) => {
      listeners.maximized.push(callback)
      return unsubMax
    }),
    onWindowUnmaximized: vi.fn((callback: () => void) => {
      listeners.unmaximized.push(callback)
      return unsubUnmax
    }),
    minimizeWindow: vi.fn(),
    maximizeWindow: vi.fn(),
    closeWindow: vi.fn(),
  }
  window.electronAPI = api as unknown as ElectronAPI
  return { api, listeners, unsubMax, unsubUnmax }
}

afterEach(() => {
  delete window.electronAPI
})

describe('useWindowControls', () => {
  it('挂载时读取初始最大化状态', async () => {
    isElectronMock.mockReturnValue(true)
    const { api } = installElectronAPI(true)

    const { result } = renderHook(() => useWindowControls())
    // isMaximized 是异步查询，初始为 false，落定后翻转为 true
    expect(result.current.isMaximized).toBe(false)
    await waitFor(() => expect(result.current.isMaximized).toBe(true))
    expect(api.isMaximized).toHaveBeenCalledTimes(1)
  })

  it('窗口最大化 / 还原事件驱动 isMaximized 状态切换', async () => {
    isElectronMock.mockReturnValue(true)
    const { listeners } = installElectronAPI(false)

    const { result } = renderHook(() => useWindowControls())
    // 等初始异步查询落定，避免与事件触发的状态更新竞争
    await act(async () => {})
    expect(result.current.isMaximized).toBe(false)

    // 触发最大化事件
    act(() => listeners.maximized.forEach((callback) => callback()))
    expect(result.current.isMaximized).toBe(true)

    // 触发还原事件
    act(() => listeners.unmaximized.forEach((callback) => callback()))
    expect(result.current.isMaximized).toBe(false)
  })

  it('卸载时取消两个窗口事件订阅', async () => {
    isElectronMock.mockReturnValue(true)
    const { unsubMax, unsubUnmax } = installElectronAPI()

    const { unmount } = renderHook(() => useWindowControls())
    await act(async () => {})

    unmount()
    expect(unsubMax).toHaveBeenCalledTimes(1)
    expect(unsubUnmax).toHaveBeenCalledTimes(1)
  })

  it('minimize / toggleMaximize / close 分别转发到对应 IPC 方法', async () => {
    isElectronMock.mockReturnValue(true)
    const { api } = installElectronAPI()

    const { result } = renderHook(() => useWindowControls())
    await act(async () => {})

    result.current.minimize()
    result.current.toggleMaximize()
    result.current.close()

    expect(api.minimizeWindow).toHaveBeenCalledTimes(1)
    expect(api.maximizeWindow).toHaveBeenCalledTimes(1)
    expect(api.closeWindow).toHaveBeenCalledTimes(1)
  })

  it('非 Electron 环境下不访问 electronAPI，控制函数安全空转', async () => {
    isElectronMock.mockReturnValue(false)
    const { api } = installElectronAPI()

    const { result } = renderHook(() => useWindowControls())
    await act(async () => {})

    // isElectron 为 false 时 effect 直接返回，不查询初始状态、不注册事件
    expect(api.isMaximized).not.toHaveBeenCalled()
    expect(api.onWindowMaximized).not.toHaveBeenCalled()
    expect(result.current.isMaximized).toBe(false)
  })

  it('electronAPI 缺失时（如 preload 未注入）不崩溃', async () => {
    isElectronMock.mockReturnValue(true)
    delete window.electronAPI

    const { result } = renderHook(() => useWindowControls())
    await act(async () => {})
    expect(result.current.isMaximized).toBe(false)

    // 控制函数用可选链保护，无 electronAPI 时调用不抛错
    expect(() => {
      result.current.minimize()
      result.current.toggleMaximize()
      result.current.close()
    }).not.toThrow()
  })
})
