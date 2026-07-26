import { act, render, renderHook, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { Component, type ReactNode } from 'react'

import { RestartProvider, useRestart, useRestartAction } from '../restart-context'
import { restartMaiBot } from '../system-api'

// 重启 API 由 system-api 单测覆盖，这里只关心它是否被调用/其结果如何驱动状态机
vi.mock('../system-api', () => ({
  restartMaiBot: vi.fn(),
}))

const restartMock = vi.mocked(restartMaiBot)

// 健康检查走原生 fetch（不经过 @/lib/http），用全局桩接管
const fetchMock = vi.fn<typeof fetch>()

// 渲染包裹在 RestartProvider 内的 useRestart，便于逐项断言 context 值
interface RenderRestartOptions {
  maxAttempts?: number
  healthCheckUrl?: string
  onRestartComplete?: () => void
  onRestartFailed?: (error: string) => void
}

const renderRestart = (options: RenderRestartOptions = {}) =>
  renderHook(() => useRestart(), {
    wrapper: ({ children }: { children: ReactNode }) => (
      <RestartProvider {...options}>{children}</RestartProvider>
    ),
  })

// 内联错误边界：React 19 下 render 抛错不再同步向外抛，改用边界捕获断言
class Boundary extends Component<{ children: ReactNode }, { message: string | null }> {
  state: { message: string | null } = { message: null }

  static getDerivedStateFromError(error: Error) {
    return { message: error.message }
  }

  render() {
    if (this.state.message) {
      return <div data-testid="boundary-message">{this.state.message}</div>
    }
    return this.props.children
  }
}

beforeEach(() => {
  vi.stubGlobal('fetch', fetchMock)
  restartMock.mockResolvedValue({ success: true, message: 'ok' })
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('useRestart', () => {
  it('在 Provider 外使用时抛出错误', () => {
    // 屏蔽 React 对边界捕获错误的 console.error 噪音
    vi.spyOn(console, 'error').mockImplementation(() => {})

    const ContextReader = () => {
      useRestart()
      return null
    }
    render(
      <Boundary>
        <ContextReader />
      </Boundary>,
    )

    expect(screen.getByTestId('boundary-message')).toHaveTextContent(
      'useRestart must be used within a RestartProvider',
    )
  })

  it('初始状态为 idle 且透传自定义 maxAttempts', () => {
    const { result } = renderRestart({ maxAttempts: 5 })

    expect(result.current.state).toEqual({
      status: 'idle',
      progress: 0,
      elapsedTime: 0,
      checkAttempts: 0,
      maxAttempts: 5,
    })
    expect(result.current.isRestarting).toBe(false)
  })
})

describe('triggerRestart', () => {
  it('调用重启 API 并进入 restarting 状态', async () => {
    vi.useFakeTimers()
    const { result } = renderRestart()

    await act(async () => {
      await result.current.triggerRestart()
    })

    expect(restartMock).toHaveBeenCalledTimes(1)
    expect(result.current.state.status).toBe('restarting')
    expect(result.current.isRestarting).toBe(true)
  })

  it('skipApiCall 时不调用重启 API 直接进入 restarting', async () => {
    vi.useFakeTimers()
    const { result } = renderRestart()

    await act(async () => {
      await result.current.triggerRestart({ skipApiCall: true })
    })

    expect(restartMock).not.toHaveBeenCalled()
    expect(result.current.state.status).toBe('restarting')
  })

  it('指定 delay 时先停留在 requesting，延迟结束后才进入 restarting', async () => {
    vi.useFakeTimers()
    const { result } = renderRestart()

    let pending: Promise<void> = Promise.resolve()
    act(() => {
      pending = result.current.triggerRestart({ delay: 500, skipApiCall: true })
    })
    expect(result.current.state.status).toBe('requesting')

    await act(async () => {
      await vi.advanceTimersByTimeAsync(500)
      await pending
    })
    expect(result.current.state.status).toBe('restarting')
  })

  it('重启进行中再次触发会被忽略', async () => {
    vi.useFakeTimers()
    const { result } = renderRestart()

    await act(async () => {
      await result.current.triggerRestart()
    })
    await act(async () => {
      await result.current.triggerRestart()
    })

    expect(restartMock).toHaveBeenCalledTimes(1)
  })

  it('进度随时间推进：1 秒 5 格、封顶 90，计时器按秒累加', async () => {
    vi.useFakeTimers()
    // 服务未恢复：健康检查一直失败，进度条持续推进
    fetchMock.mockRejectedValue(new TypeError('服务尚未恢复'))
    const { result } = renderRestart()

    await act(async () => {
      await result.current.triggerRestart({ skipApiCall: true })
    })

    // 200ms 一格：1000ms 后应为 5%，已用时 1 秒
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000)
    })
    expect(result.current.state.progress).toBe(5)
    expect(result.current.state.elapsedTime).toBe(1)

    // 累计 20 秒：进度封顶 90，此时仍在 checking（默认上限 60 次远未耗尽）
    await act(async () => {
      await vi.advanceTimersByTimeAsync(19000)
    })
    expect(result.current.state.progress).toBe(90)
    expect(result.current.state.elapsedTime).toBe(20)
    expect(result.current.state.status).toBe('checking')
  })
})

describe('健康检查', () => {
  it('按自定义 URL 以 GET + include 凭据发起检查', async () => {
    vi.useFakeTimers()
    fetchMock.mockResolvedValue({ ok: true } as Response)
    const { result } = renderRestart({ healthCheckUrl: '/custom/health' })

    await act(async () => {
      await result.current.triggerRestart({ skipApiCall: true })
    })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000)
    })

    expect(fetchMock).toHaveBeenCalledWith(
      '/custom/health',
      expect.objectContaining({
        method: 'GET',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
      }),
    )
  })

  it('检查成功后置为 success，延迟后回调 onRestartComplete', async () => {
    vi.useFakeTimers()
    fetchMock.mockResolvedValue({ ok: true } as Response)
    const onRestartComplete = vi.fn()
    const { result } = renderRestart({ onRestartComplete })

    await act(async () => {
      await result.current.triggerRestart({ skipApiCall: true })
    })
    // 3 秒初始等待后第一次检查即成功
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000)
    })

    expect(result.current.state.status).toBe('success')
    expect(result.current.state.progress).toBe(100)
    expect(onRestartComplete).not.toHaveBeenCalled()

    // 成功 1.5 秒后才执行跳转回调
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1500)
    })
    expect(onRestartComplete).toHaveBeenCalledTimes(1)
  })

  it('检查持续失败达到上限后置为 failed 并回调 onRestartFailed', async () => {
    vi.useFakeTimers()
    fetchMock.mockRejectedValue(new TypeError('网络不可达'))
    const onRestartFailed = vi.fn()
    const { result } = renderRestart({ maxAttempts: 2, onRestartFailed })

    await act(async () => {
      await result.current.triggerRestart({ skipApiCall: true })
    })

    // 第 1 次检查失败，仍在 checking
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000)
    })
    expect(result.current.state.status).toBe('checking')
    expect(result.current.state.checkAttempts).toBe(1)

    // 第 2 次检查失败，达到上限转为 failed
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000)
    })
    expect(result.current.state.status).toBe('failed')
    expect(result.current.state.error).toBe('健康检查超时 (2/2)')
    expect(onRestartFailed).toHaveBeenCalledWith('健康检查超时 (2/2)')
  })

  it('retryHealthCheck 在失败后重新检查并可成功', async () => {
    vi.useFakeTimers()
    fetchMock.mockRejectedValue(new TypeError('网络不可达'))
    const { result } = renderRestart({ maxAttempts: 1 })

    await act(async () => {
      await result.current.triggerRestart({ skipApiCall: true })
    })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000)
    })
    expect(result.current.state.status).toBe('failed')

    // 服务恢复后重试：立刻发起检查并成功
    fetchMock.mockResolvedValue({ ok: true } as Response)
    await act(async () => {
      result.current.retryHealthCheck()
      await vi.advanceTimersByTimeAsync(0)
    })
    expect(result.current.state.status).toBe('success')
    expect(result.current.state.error).toBeUndefined()
  })
})

describe('resetState', () => {
  it('清空状态回到 idle', async () => {
    vi.useFakeTimers()
    const { result } = renderRestart()

    await act(async () => {
      await result.current.triggerRestart({ skipApiCall: true })
    })
    expect(result.current.isRestarting).toBe(true)

    act(() => {
      result.current.resetState()
    })
    expect(result.current.state).toEqual({
      status: 'idle',
      progress: 0,
      elapsedTime: 0,
      checkAttempts: 0,
      maxAttempts: 60,
    })
    expect(result.current.isRestarting).toBe(false)
  })
})

describe('useRestartAction', () => {
  it('触发重启后调用 API 并进入 isRestarting', async () => {
    const { result } = renderHook(() => useRestartAction())
    expect(result.current.isRestarting).toBe(false)

    await act(async () => {
      await result.current.triggerRestart()
    })

    expect(restartMock).toHaveBeenCalledTimes(1)
    expect(result.current.isRestarting).toBe(true)
  })

  it('API 报错被吞掉（服务可能已关闭），且重复触发被忽略', async () => {
    restartMock.mockRejectedValue(new Error('服务已关闭'))
    const { result } = renderHook(() => useRestartAction())

    await act(async () => {
      await result.current.triggerRestart()
    })
    expect(result.current.isRestarting).toBe(true)

    await act(async () => {
      await result.current.triggerRestart()
    })
    expect(restartMock).toHaveBeenCalledTimes(1)
  })
})
