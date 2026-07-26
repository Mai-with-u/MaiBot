import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ElectronShell } from '../electron-shell'
import { TitleBar } from '../TitleBar'

const { runtimeState, controls, wizardState } = vi.hoisted(() => ({
  runtimeState: { electron: false, platform: 'browser' },
  controls: {
    close: vi.fn(),
    isMaximized: false,
    minimize: vi.fn(),
    toggleMaximize: vi.fn(),
  },
  wizardState: { open: false },
}))

vi.mock('@/lib/runtime', () => ({
  isElectron: () => runtimeState.electron,
  getPlatform: () => runtimeState.platform,
}))

vi.mock('@/hooks/useWindowControls', () => ({
  useWindowControls: () => controls,
}))

vi.mock('../BackendSetupWizard', () => ({
  BackendSetupWizard: ({ open }: { open: boolean }) => {
    wizardState.open = open
    return <div data-testid="wizard" data-open={String(open)} />
  },
}))

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

beforeEach(() => {
  runtimeState.electron = false
  runtimeState.platform = 'browser'
  controls.isMaximized = false
})

describe('TitleBar', () => {
  it('浏览器环境不渲染标题栏', () => {
    const { container } = render(<TitleBar />)
    expect(container).toBeEmptyDOMElement()
  })

  it('Windows/Linux 环境显示三个窗口操作并调用控制接口', async () => {
    const user = userEvent.setup()
    runtimeState.electron = true
    runtimeState.platform = 'win32'
    render(<TitleBar />)

    await user.click(screen.getByRole('button', { name: '最小化' }))
    await user.click(screen.getByRole('button', { name: '最大化' }))
    await user.click(screen.getByRole('button', { name: '关闭窗口' }))
    expect(controls.minimize).toHaveBeenCalledOnce()
    expect(controls.toggleMaximize).toHaveBeenCalledOnce()
    expect(controls.close).toHaveBeenCalledOnce()
  })

  it('最大化后按钮标签切换为还原窗口', () => {
    runtimeState.electron = true
    runtimeState.platform = 'linux'
    controls.isMaximized = true
    render(<TitleBar />)
    expect(screen.getByRole('button', { name: '还原窗口' })).toBeInTheDocument()
  })

  it('macOS 仅保留标题与交通灯占位，不渲染自定义按钮', () => {
    runtimeState.electron = true
    runtimeState.platform = 'darwin'
    render(<TitleBar />)
    expect(screen.getByText('MaiBot')).toBeInTheDocument()
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })
})

describe('ElectronShell', () => {
  it.each([true, false])('把首次启动状态 %s 传给后端设置向导', async (isFirstLaunch) => {
    vi.stubGlobal('electronAPI', {
      isFirstLaunch: vi.fn().mockResolvedValue(isFirstLaunch),
    })
    render(<ElectronShell />)

    await waitFor(() =>
      expect(screen.getByTestId('wizard')).toHaveAttribute('data-open', String(isFirstLaunch))
    )
  })
})
