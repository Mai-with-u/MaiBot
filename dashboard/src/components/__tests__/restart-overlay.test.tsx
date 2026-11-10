import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { RestartContextValue, RestartState } from '@/lib/restart-context'

import { RestartOverlay } from '../restart-overlay'

// t 在 vi.mock 工厂内建一次，保持稳定引用；带插值参数时拼接 current/max 便于断言
vi.mock('react-i18next', () => {
  const t = (key: string, opts?: Record<string, unknown>) =>
    opts && 'current' in opts ? `${key} ${String(opts.current)}/${String(opts.max)}` : key
  return {
    useTranslation: () => ({ t, i18n: { language: 'zh-CN' } }),
  }
})

// 用可控的 useRestart 桩驱动组件的 Provider 模式 / 独立模式两个分支
const { useRestartMock } = vi.hoisted(() => ({
  useRestartMock: vi.fn(),
}))

vi.mock('@/lib/restart-context', () => ({
  useRestart: useRestartMock,
}))

// 构造重启状态与 context 值的内联工具
const makeState = (overrides: Partial<RestartState> = {}): RestartState => ({
  status: 'restarting',
  progress: 42,
  elapsedTime: 65,
  checkAttempts: 0,
  maxAttempts: 60,
  ...overrides,
})

const makeContext = (
  state: RestartState,
  overrides: Partial<RestartContextValue> = {},
): RestartContextValue => ({
  state,
  isRestarting: state.status !== 'idle',
  triggerRestart: vi.fn(),
  resetState: vi.fn(),
  retryHealthCheck: vi.fn(),
  ...overrides,
})

// 让 useRestart 抛错，模拟组件在 Provider 之外使用（独立模式）
const mockWithoutProvider = () => {
  useRestartMock.mockImplementation(() => {
    throw new Error('useRestart must be used within a RestartProvider')
  })
}

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('RestartOverlay（Provider 模式）', () => {
  it('idle 状态不渲染任何内容', () => {
    useRestartMock.mockReturnValue(makeContext(makeState({ status: 'idle' })))

    const { container } = render(<RestartOverlay />)
    expect(container).toBeEmptyDOMElement()
  })

  it('restarting 状态显示标题、描述、进度与已用时间', () => {
    useRestartMock.mockReturnValue(
      makeContext(makeState({ status: 'restarting', progress: 42, elapsedTime: 65 })),
    )

    render(<RestartOverlay />)

    expect(screen.getByText('restart.restarting')).toBeInTheDocument()
    expect(screen.getByText('restart.restartingDesc')).toBeInTheDocument()
    expect(screen.getByText('restart.restartingTip')).toBeInTheDocument()
    expect(screen.getByText('42%')).toBeInTheDocument()
    // 65 秒格式化为 1:05
    expect(screen.getByText('restart.elapsed 1:05')).toBeInTheDocument()
  })

  it('自定义标题与描述覆盖默认文案', () => {
    useRestartMock.mockReturnValue(makeContext(makeState({ status: 'restarting' })))

    render(<RestartOverlay title="正在升级" description="请勿关闭页面" />)

    expect(screen.getByText('正在升级')).toBeInTheDocument()
    expect(screen.getByText('请勿关闭页面')).toBeInTheDocument()
    expect(screen.queryByText('restart.restarting')).not.toBeInTheDocument()
    expect(screen.queryByText('restart.restartingDesc')).not.toBeInTheDocument()
  })

  it('checking 状态描述包含当前检查次数', () => {
    useRestartMock.mockReturnValue(makeContext(makeState({ status: 'checking', checkAttempts: 3 })))

    render(<RestartOverlay />)

    expect(screen.getByText('restart.checking')).toBeInTheDocument()
    expect(screen.getByText('restart.checkingDesc 3/60')).toBeInTheDocument()
  })

  it('success 状态显示成功文案并触发 onComplete', () => {
    const onComplete = vi.fn()
    useRestartMock.mockReturnValue(makeContext(makeState({ status: 'success', progress: 100 })))

    render(<RestartOverlay onComplete={onComplete} />)

    expect(screen.getByText('restart.success')).toBeInTheDocument()
    expect(screen.getByText('restart.successTip')).toBeInTheDocument()
    expect(screen.getByText('100%')).toBeInTheDocument()
    expect(onComplete).toHaveBeenCalledTimes(1)
  })

  it('failed 状态隐藏进度条、触发 onFailed，重试按钮调用 retryHealthCheck', () => {
    const onFailed = vi.fn()
    const retryHealthCheck = vi.fn()
    useRestartMock.mockReturnValue(
      makeContext(makeState({ status: 'failed', progress: 55, error: '健康检查超时 (60/60)' }), {
        retryHealthCheck,
      }),
    )

    render(<RestartOverlay onFailed={onFailed} />)

    expect(screen.getByText('restart.failed')).toBeInTheDocument()
    expect(screen.getByText('restart.failedTip')).toBeInTheDocument()
    // 失败时不展示进度条
    expect(screen.queryByText('55%')).not.toBeInTheDocument()
    expect(onFailed).toHaveBeenCalledTimes(1)

    expect(screen.getByRole('button', { name: 'restart.refreshPage' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'restart.retryCheck' }))
    expect(retryHealthCheck).toHaveBeenCalledTimes(1)
  })

  it('showAnimation=false 时不渲染背景动画粒子', () => {
    useRestartMock.mockReturnValue(makeContext(makeState()))

    const { container, rerender } = render(<RestartOverlay />)
    expect(container.querySelector('.animate-bounce')).not.toBeNull()

    rerender(<RestartOverlay showAnimation={false} />)
    expect(container.querySelector('.animate-bounce')).toBeNull()
  })
})

describe('RestartOverlay（独立模式，无 Provider）', () => {
  it('visible=false 时不渲染', () => {
    mockWithoutProvider()

    const { container } = render(<RestartOverlay visible={false} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('visible=true 时以 restarting 状态渲染', () => {
    vi.useFakeTimers()
    mockWithoutProvider()

    render(<RestartOverlay visible />)

    expect(screen.getByText('restart.restarting')).toBeInTheDocument()
    expect(screen.getByText('restart.restartingTip')).toBeInTheDocument()
  })

  it('3 秒后开始健康检查，成功后进入 success 并回调 onComplete', async () => {
    vi.useFakeTimers()
    mockWithoutProvider()
    const onComplete = vi.fn()
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue({ ok: true } as Response)
    vi.stubGlobal('fetch', fetchMock)

    render(<RestartOverlay visible onComplete={onComplete} />)

    // 初始等待 3 秒后发起健康检查，服务已恢复
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000)
    })

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/webui/system/status',
      expect.objectContaining({ method: 'GET' }),
    )
    expect(screen.getByText('restart.success')).toBeInTheDocument()
    expect(onComplete).toHaveBeenCalledTimes(1)
  })
})
