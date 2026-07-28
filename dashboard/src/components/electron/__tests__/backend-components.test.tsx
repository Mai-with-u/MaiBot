import type { ReactNode } from 'react'

import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { BackendConnection } from '@/types/electron'

import { BackendManager } from '../BackendManager'
import { BackendSetupWizard } from '../BackendSetupWizard'

const mocks = vi.hoisted(() => ({
  electron: true,
  activeId: 'local',
  loading: false,
  backends: [
    {
      id: 'local',
      name: '本地后端',
      url: 'http://127.0.0.1:8000',
      isDefault: true,
    },
    {
      id: 'remote',
      name: '远程后端',
      url: 'https://example.com',
      isDefault: false,
    },
  ] as BackendConnection[],
  addBackend: vi.fn(),
  updateBackend: vi.fn(),
  removeBackend: vi.fn(),
  switchBackend: vi.fn(),
}))

vi.mock('@/lib/runtime', () => ({
  isElectron: () => mocks.electron,
}))

vi.mock('@/hooks/useBackendConnections', () => ({
  useBackendConnections: () => ({
    activeId: mocks.activeId,
    addBackend: mocks.addBackend,
    backends: mocks.backends,
    loading: mocks.loading,
    removeBackend: mocks.removeBackend,
    switchBackend: mocks.switchBackend,
    updateBackend: mocks.updateBackend,
  }),
}))

vi.mock('@/components/ui/dialog', () => ({
  Dialog: ({
    children,
    open,
    onOpenChange,
  }: {
    children: ReactNode
    open: boolean
    onOpenChange: (open: boolean) => void
  }) =>
    open ? (
      <section data-testid="dialog">
        <button type="button" onClick={() => onOpenChange(false)}>
          模拟关闭
        </button>
        {children}
      </section>
    ) : null,
  DialogBody: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  DialogContent: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  DialogHeader: ({ children }: { children: ReactNode }) => <header>{children}</header>,
  DialogTitle: ({ children }: { children: ReactNode }) => <h2>{children}</h2>,
}))

vi.mock('@/components/ui/alert-dialog', () => ({
  AlertDialog: ({
    children,
    open,
    onOpenChange,
  }: {
    children: ReactNode
    open: boolean
    onOpenChange: (open: boolean) => void
  }) =>
    open ? (
      <section data-testid="delete-dialog">
        <button type="button" onClick={() => onOpenChange(false)}>
          模拟取消删除
        </button>
        {children}
      </section>
    ) : null,
  AlertDialogContent: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  AlertDialogHeader: ({ children }: { children: ReactNode }) => <header>{children}</header>,
  AlertDialogTitle: ({ children }: { children: ReactNode }) => <h3>{children}</h3>,
  AlertDialogDescription: ({ children }: { children: ReactNode }) => <p>{children}</p>,
  AlertDialogFooter: ({ children }: { children: ReactNode }) => <footer>{children}</footer>,
  AlertDialogCancel: ({ children }: { children: ReactNode }) => (
    <button type="button">{children}</button>
  ),
  AlertDialogAction: ({ children, onClick }: { children: ReactNode; onClick?: () => void }) => (
    <button type="button" onClick={onClick}>
      {children}
    </button>
  ),
}))

describe('BackendManager', () => {
  beforeEach(() => {
    mocks.electron = true
    mocks.activeId = 'local'
    mocks.loading = false
  })

  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('浏览器环境不渲染，加载中显示等待状态', () => {
    mocks.electron = false
    const { container, rerender } = render(<BackendManager open onOpenChange={vi.fn()} />)
    expect(container).toBeEmptyDOMElement()

    mocks.electron = true
    mocks.loading = true
    rerender(<BackendManager open onOpenChange={vi.fn()} />)
    expect(screen.getByRole('heading', { name: '后端连接管理' })).toBeInTheDocument()
    expect(screen.queryByText('本地后端')).not.toBeInTheDocument()
  })

  it('展示连接状态，切换非活跃后端且禁止删除活跃后端', async () => {
    const user = userEvent.setup()
    render(<BackendManager open onOpenChange={vi.fn()} />)

    expect(screen.getByText('本地后端')).toBeInTheDocument()
    expect(screen.getByText('远程后端')).toBeInTheDocument()
    expect(screen.getByTitle('无法删除活跃后端')).toBeDisabled()

    await user.click(screen.getByTitle('切换到此后端'))
    expect(mocks.switchBackend).toHaveBeenCalledWith('remote')

    await user.click(screen.getByTitle('删除'))
    const dialog = screen.getByTestId('delete-dialog')
    expect(within(dialog).getByText(/确定要删除 远程后端/)).toBeInTheDocument()
    await user.click(within(dialog).getByRole('button', { name: '删除' }))
    expect(mocks.removeBackend).toHaveBeenCalledWith('remote')
  })

  it('校验新连接并保存标准化表单内容', async () => {
    const user = userEvent.setup()
    render(<BackendManager open onOpenChange={vi.fn()} />)

    await user.click(screen.getByRole('button', { name: '添加新连接' }))
    const save = screen.getByRole('button', { name: '保存' })
    expect(save).toBeDisabled()

    await user.type(screen.getByLabelText('名称'), '测试服务器')
    fireEvent.change(screen.getByLabelText('URL'), {
      target: { value: 'ftp://invalid.example' },
    })
    expect(save).toBeDisabled()

    fireEvent.change(screen.getByLabelText('URL'), {
      target: { value: 'https://valid.example' },
    })
    await user.click(save)

    expect(mocks.addBackend).toHaveBeenCalledWith({
      name: '测试服务器',
      url: 'https://valid.example',
      isDefault: false,
    })
  })

  it('编辑既有连接时调用更新接口并保留连接 ID', async () => {
    const user = userEvent.setup()
    render(<BackendManager open onOpenChange={vi.fn()} />)

    await user.click(screen.getAllByTitle('编辑')[1])
    const name = screen.getByLabelText('名称')
    expect(name).toHaveValue('远程后端')
    await user.clear(name)
    await user.type(name, '远程后端二号')
    await user.click(screen.getByRole('button', { name: '保存' }))

    expect(mocks.updateBackend).toHaveBeenCalledWith(
      'remote',
      expect.objectContaining({
        id: 'remote',
        name: '远程后端二号',
        url: 'https://example.com',
      })
    )
  })
})

describe('BackendSetupWizard', () => {
  const fetchMock = vi.fn()
  const addBackend = vi.fn()
  const setActiveBackend = vi.fn()
  const markFirstLaunchComplete = vi.fn()
  const reload = vi.fn()

  beforeEach(() => {
    mocks.electron = true
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
    vi.clearAllMocks()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('仅在 Electron 且 open=true 时渲染', () => {
    const { container, rerender } = render(<BackendSetupWizard open={false} />)
    expect(container).toBeEmptyDOMElement()

    rerender(<BackendSetupWizard open />)
    expect(screen.getByText('欢迎使用 MaiBot')).toBeInTheDocument()

    mocks.electron = false
    rerender(<BackendSetupWizard open />)
    expect(container).toBeEmptyDOMElement()
  })

  it('在失焦时给出名称、协议和尾斜杠校验错误', () => {
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
  })

  it('测试连接成功后保存后端、激活并完成首次启动', async () => {
    const user = userEvent.setup()
    fetchMock.mockResolvedValue({ ok: true, status: 200 })
    addBackend.mockResolvedValue({
      id: 'created',
      name: '新后端',
      url: 'https://example.com',
      isDefault: true,
    })
    render(<BackendSetupWizard open />)

    await user.type(screen.getByLabelText(/后端名称/), '  新后端  ')
    await user.type(screen.getByLabelText(/后端地址/), 'https://example.com')
    await user.click(screen.getByRole('button', { name: '测试连接' }))
    expect(await screen.findByText('连接成功')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '开始使用' }))
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

  it('区分 HTTP、超时和网络连接失败', async () => {
    render(<BackendSetupWizard open />)
    const url = screen.getByLabelText(/后端地址/)
    fireEvent.change(url, { target: { value: 'https://example.com' } })

    fetchMock.mockResolvedValueOnce({ ok: false, status: 503 })
    fireEvent.click(screen.getByRole('button', { name: '测试连接' }))
    expect(await screen.findByText('服务器返回状态码 503')).toBeInTheDocument()

    fetchMock.mockRejectedValueOnce(new DOMException('timeout', 'TimeoutError'))
    fireEvent.click(screen.getByRole('button', { name: '测试连接' }))
    expect(await screen.findByText('连接超时，请检查地址是否正确')).toBeInTheDocument()

    fetchMock.mockRejectedValueOnce(new TypeError('offline'))
    fireEvent.click(screen.getByRole('button', { name: '测试连接' }))
    expect(await screen.findByText('无法连接到服务器，请检查地址和网络')).toBeInTheDocument()
  })

  it('保存配置失败时恢复按钮并展示原始错误信息', async () => {
    const user = userEvent.setup()
    addBackend.mockRejectedValue(new Error('磁盘写入失败'))
    render(<BackendSetupWizard open />)

    await user.type(screen.getByLabelText(/后端名称/), '本地后端')
    await user.type(screen.getByLabelText(/后端地址/), 'http://127.0.0.1:8000')
    await user.click(screen.getByRole('button', { name: '开始使用' }))

    expect(await screen.findByText('磁盘写入失败')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '开始使用' })).toBeEnabled()
    expect(setActiveBackend).not.toHaveBeenCalled()
  })
})
