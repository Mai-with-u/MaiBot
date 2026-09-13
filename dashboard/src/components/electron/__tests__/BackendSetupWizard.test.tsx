/**
 * BackendSetupWizard 组件测试。
 *
 * 通过 window.electronAPI 桩覆盖向导步骤：表单校验、测试连接、保存配置。
 */
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { BackendSetupWizard } from '../BackendSetupWizard'

const { isElectronMock } = vi.hoisted(() => ({
  isElectronMock: vi.fn(() => true),
}))

vi.mock('@/lib/runtime', () => ({
  isElectron: () => isElectronMock(),
}))

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

describe('BackendSetupWizard', () => {
  const fetchMock = vi.fn()
  const addBackend = vi.fn()
  const setActiveBackend = vi.fn()
  const markFirstLaunchComplete = vi.fn()
  const reload = vi.fn()

  beforeEach(() => {
    isElectronMock.mockReturnValue(true)
    vi.stubGlobal('fetch', fetchMock)
    vi.stubGlobal('location', { reload })
    Object.defineProperty(window, 'electronAPI', {
      configurable: true,
      value: {
        addBackend,
        setActiveBackend,
        markFirstLaunchComplete,
      },
    })
    vi.spyOn(AbortSignal, 'timeout').mockReturnValue(new AbortController().signal)
  })

  afterEach(() => {
    cleanup()
    fetchMock.mockReset()
    addBackend.mockReset()
    setActiveBackend.mockReset()
    markFirstLaunchComplete.mockReset()
    reload.mockReset()
    delete window.electronAPI
    vi.clearAllMocks()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('仅在 Electron 且 open=true 时渲染欢迎步骤', () => {
    const { container, rerender } = render(<BackendSetupWizard open={false} />)
    expect(container).toBeEmptyDOMElement()

    rerender(<BackendSetupWizard open />)
    expect(screen.getByText('欢迎使用 MaiBot')).toBeInTheDocument()
    expect(screen.getByText('配置您的第一个后端连接以开始使用')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '测试连接' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '开始使用' })).toBeDisabled()

    isElectronMock.mockReturnValue(false)
    rerender(<BackendSetupWizard open />)
    expect(container).toBeEmptyDOMElement()
  })

  it('失焦校验名称与地址，输入后清除错误', async () => {
    const user = userEvent.setup()
    render(<BackendSetupWizard open />)

    fireEvent.blur(screen.getByLabelText(/后端名称/))
    fireEvent.blur(screen.getByLabelText(/后端地址/))
    expect(screen.getByText('后端名称不能为空')).toBeInTheDocument()
    expect(screen.getByText('后端地址不能为空')).toBeInTheDocument()

    fireEvent.change(screen.getByLabelText(/后端地址/), {
      target: { value: 'example.com' },
    })
    fireEvent.blur(screen.getByLabelText(/后端地址/))
    expect(screen.getByText('地址必须以 http:// 或 https:// 开头')).toBeInTheDocument()

    fireEvent.change(screen.getByLabelText(/后端地址/), {
      target: { value: 'https://example.com/' },
    })
    expect(screen.getByText('地址末尾不能包含 /')).toBeInTheDocument()

    fireEvent.change(screen.getByLabelText(/后端地址/), {
      target: { value: 'https://example.com' },
    })
    expect(screen.queryByText('地址末尾不能包含 /')).not.toBeInTheDocument()

    await user.type(screen.getByLabelText(/后端名称/), '本地后端')
    expect(screen.queryByText('后端名称不能为空')).not.toBeInTheDocument()
  })

  it('地址非法时测试连接不发请求', () => {
    render(<BackendSetupWizard open />)
    fireEvent.change(screen.getByLabelText(/后端地址/), {
      target: { value: 'example.com' },
    })
    fireEvent.click(screen.getByRole('button', { name: '测试连接' }))

    expect(screen.getByText('地址必须以 http:// 或 https:// 开头')).toBeInTheDocument()
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('测试连接成功后展示连接成功，修改地址会重置状态', async () => {
    const user = userEvent.setup()
    const pending = deferred<{ ok: boolean; status: number }>()
    fetchMock.mockReturnValueOnce(pending.promise)
    render(<BackendSetupWizard open />)

    await user.type(screen.getByLabelText(/后端地址/), 'https://example.com')
    await user.click(screen.getByRole('button', { name: '测试连接' }))
    expect(screen.getByRole('button', { name: /测试连接中/ })).toBeDisabled()
    expect(fetchMock).toHaveBeenCalledWith('https://example.com/api/webui/system/health', {
      method: 'GET',
      signal: expect.any(AbortSignal),
    })

    pending.resolve({ ok: true, status: 200 })
    expect(await screen.findByText('连接成功')).toBeInTheDocument()

    fireEvent.change(screen.getByLabelText(/后端地址/), {
      target: { value: 'https://example.com/v2' },
    })
    expect(screen.queryByText('连接成功')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '测试连接' })).toBeEnabled()
  })

  it('区分 HTTP、超时、网络和未知连接失败', async () => {
    render(<BackendSetupWizard open />)
    fireEvent.change(screen.getByLabelText(/后端地址/), {
      target: { value: 'https://example.com' },
    })

    fetchMock.mockResolvedValueOnce({ ok: false, status: 503 })
    fireEvent.click(screen.getByRole('button', { name: '测试连接' }))
    expect(await screen.findByText('服务器返回状态码 503')).toBeInTheDocument()

    fetchMock.mockRejectedValueOnce(new DOMException('timeout', 'TimeoutError'))
    fireEvent.click(screen.getByRole('button', { name: '测试连接' }))
    expect(await screen.findByText('连接超时，请检查地址是否正确')).toBeInTheDocument()

    fetchMock.mockRejectedValueOnce(new TypeError('offline'))
    fireEvent.click(screen.getByRole('button', { name: '测试连接' }))
    expect(await screen.findByText('无法连接到服务器，请检查地址和网络')).toBeInTheDocument()

    fetchMock.mockRejectedValueOnce(new Error('证书无效'))
    fireEvent.click(screen.getByRole('button', { name: '测试连接' }))
    expect(await screen.findByText('证书无效')).toBeInTheDocument()

    fetchMock.mockRejectedValueOnce('broken')
    fireEvent.click(screen.getByRole('button', { name: '测试连接' }))
    expect(await screen.findByText('未知错误')).toBeInTheDocument()
  })

  it('开始使用会保存、激活后端并完成首次启动', async () => {
    const user = userEvent.setup()
    const pending = deferred<{
      id: string
      name: string
      url: string
      isDefault: boolean
    }>()
    addBackend.mockReturnValueOnce(pending.promise)
    render(<BackendSetupWizard open />)

    await user.type(screen.getByLabelText(/后端名称/), '  新后端  ')
    await user.type(screen.getByLabelText(/后端地址/), 'https://example.com')
    await user.click(screen.getByRole('button', { name: '开始使用' }))
    expect(screen.getByRole('button', { name: /配置中/ })).toBeDisabled()

    pending.resolve({
      id: 'created',
      name: '新后端',
      url: 'https://example.com',
      isDefault: true,
    })

    await waitFor(() =>
      expect(addBackend).toHaveBeenCalledWith({
        name: '新后端',
        url: 'https://example.com',
        isDefault: true,
      })
    )
    expect(setActiveBackend).toHaveBeenCalledWith('created')
    expect(markFirstLaunchComplete).toHaveBeenCalledOnce()
    expect(reload).toHaveBeenCalledOnce()
  })

  it('保存配置失败时恢复按钮并展示错误信息', async () => {
    const user = userEvent.setup()
    addBackend.mockRejectedValueOnce(new Error('磁盘写入失败'))
    render(<BackendSetupWizard open />)

    await user.type(screen.getByLabelText(/后端名称/), '本地后端')
    await user.type(screen.getByLabelText(/后端地址/), 'http://127.0.0.1:8000')
    await user.click(screen.getByRole('button', { name: '开始使用' }))

    expect(await screen.findByText('磁盘写入失败')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '开始使用' })).toBeEnabled()
    expect(setActiveBackend).not.toHaveBeenCalled()
    expect(reload).not.toHaveBeenCalled()

    addBackend.mockRejectedValueOnce('io')
    await user.click(screen.getByRole('button', { name: '开始使用' }))
    expect(await screen.findByText('保存配置失败，请重试')).toBeInTheDocument()
  })
})
