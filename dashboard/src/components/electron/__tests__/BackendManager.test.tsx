/**
 * BackendManager 组件测试。
 *
 * 通过 window.electronAPI 桩驱动真实 useBackendConnections，
 * 覆盖空列表、加载中、增删改切与表单校验。
 */
import type { ReactNode } from 'react'

import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { BackendConnection, ElectronAPI } from '@/types/electron'

import { BackendManager } from '../BackendManager'

const { isElectronMock } = vi.hoisted(() => ({
  isElectronMock: vi.fn(() => true),
}))

vi.mock('@/lib/runtime', () => ({
  isElectron: () => isElectronMock(),
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

const localBackend: BackendConnection = {
  id: 'local',
  name: '本地后端',
  url: 'http://127.0.0.1:8000',
  isDefault: true,
}
const remoteBackend: BackendConnection = {
  id: 'remote',
  name: '远程后端',
  url: 'https://example.com',
  isDefault: false,
}

function createElectronStub(overrides: Partial<ElectronAPI> = {}) {
  return {
    getBackends: vi.fn().mockResolvedValue([]),
    getActiveBackend: vi.fn().mockResolvedValue(null),
    addBackend: vi.fn().mockResolvedValue({
      id: 'new',
      name: '新后端',
      url: 'https://new.example',
      isDefault: false,
    }),
    updateBackend: vi.fn().mockResolvedValue(undefined),
    removeBackend: vi.fn().mockResolvedValue(undefined),
    setActiveBackend: vi.fn().mockResolvedValue(undefined),
    ...overrides,
  }
}

type ElectronStub = ReturnType<typeof createElectronStub>

function installElectronAPI(overrides: Partial<ElectronAPI> = {}): ElectronStub {
  const api = createElectronStub(overrides)
  Object.defineProperty(window, 'electronAPI', {
    configurable: true,
    value: api,
  })
  return api
}

async function renderLoaded(ui: ReactNode) {
  const result = render(ui)
  await waitFor(() => {
    expect(document.querySelector('.animate-spin')).not.toBeInTheDocument()
  })
  return result
}

describe('BackendManager', () => {
  const reload = vi.fn()

  beforeEach(() => {
    isElectronMock.mockReturnValue(true)
    vi.stubGlobal('location', { reload })
  })

  afterEach(() => {
    cleanup()
    reload.mockReset()
    delete window.electronAPI
    vi.clearAllMocks()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('浏览器环境不渲染', () => {
    isElectronMock.mockReturnValue(false)
    installElectronAPI()
    const { container } = render(<BackendManager open onOpenChange={vi.fn()} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('加载中显示等待状态，不展示连接列表', () => {
    installElectronAPI({
      getBackends: vi.fn().mockReturnValue(new Promise(() => {})),
      getActiveBackend: vi.fn().mockReturnValue(new Promise(() => {})),
    })
    render(<BackendManager open onOpenChange={vi.fn()} />)

    expect(screen.getByRole('heading', { name: '后端连接管理' })).toBeInTheDocument()
    expect(document.querySelector('.animate-spin')).toBeInTheDocument()
    expect(screen.queryByText('本地后端')).not.toBeInTheDocument()
  })

  it('列表空态只保留添加入口，不展示已有连接', async () => {
    const api = installElectronAPI({
      getBackends: vi.fn().mockResolvedValue([]),
      getActiveBackend: vi.fn().mockResolvedValue(null),
    })
    await renderLoaded(<BackendManager open onOpenChange={vi.fn()} />)

    expect(api.getBackends).toHaveBeenCalledOnce()
    expect(screen.getByRole('heading', { name: '后端连接管理' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '添加新连接' })).toBeInTheDocument()
    expect(screen.queryByText('本地后端')).not.toBeInTheDocument()
    expect(screen.queryByTitle('切换到此后端')).not.toBeInTheDocument()
  })

  it('空列表中保存新连接后刷新出该条目', async () => {
    const user = userEvent.setup()
    let backends: BackendConnection[] = []
    const api = installElectronAPI({
      getBackends: vi.fn().mockImplementation(async () => backends),
      addBackend: vi.fn().mockImplementation(async (conn: Omit<BackendConnection, 'id'>) => {
        const created: BackendConnection = { id: 'created', ...conn }
        backends = [created]
        return created
      }),
    })
    await renderLoaded(<BackendManager open onOpenChange={vi.fn()} />)

    await user.click(screen.getByRole('button', { name: '添加新连接' }))
    expect(screen.getByRole('heading', { name: '添加连接' })).toBeInTheDocument()
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

    expect(api.addBackend).toHaveBeenCalledWith({
      name: '测试服务器',
      url: 'https://valid.example',
      isDefault: false,
    })
    expect(await screen.findByText('测试服务器')).toBeInTheDocument()
    expect(screen.getByText('https://valid.example')).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: '添加连接' })).not.toBeInTheDocument()
  })

  it('取消添加或关闭管理弹窗不会写入后端', async () => {
    const user = userEvent.setup()
    const onOpenChange = vi.fn()
    const api = installElectronAPI()
    await renderLoaded(<BackendManager open onOpenChange={onOpenChange} />)

    await user.click(screen.getByRole('button', { name: '添加新连接' }))
    await user.type(screen.getByLabelText('名称'), '不会保存')
    await user.click(screen.getByRole('button', { name: '取消' }))
    expect(screen.queryByRole('heading', { name: '添加连接' })).not.toBeInTheDocument()
    expect(api.addBackend).not.toHaveBeenCalled()

    await user.click(screen.getByRole('button', { name: '模拟关闭' }))
    expect(onOpenChange).toHaveBeenCalledWith(false)
  })

  it('展示连接并切换非活跃后端，同时禁止删除活跃项', async () => {
    const user = userEvent.setup()
    const api = installElectronAPI({
      getBackends: vi.fn().mockResolvedValue([localBackend, remoteBackend]),
      getActiveBackend: vi.fn().mockResolvedValue(localBackend),
    })
    await renderLoaded(<BackendManager open onOpenChange={vi.fn()} />)

    expect(screen.getByText('本地后端')).toBeInTheDocument()
    expect(screen.getByText('远程后端')).toBeInTheDocument()
    expect(screen.getByTitle('未知状态')).toBeInTheDocument()
    expect(screen.getByTitle('无法删除活跃后端')).toBeDisabled()
    expect(screen.queryByTitle('切换到此后端')).toBeEnabled()

    await user.click(screen.getByTitle('切换到此后端'))
    expect(api.setActiveBackend).toHaveBeenCalledWith('remote')
    expect(reload).toHaveBeenCalledOnce()
  })

  it('编辑既有连接时走更新接口并刷新名称', async () => {
    const user = userEvent.setup()
    let backends: BackendConnection[] = [localBackend, remoteBackend]
    const api = installElectronAPI({
      getBackends: vi.fn().mockImplementation(async () => backends),
      getActiveBackend: vi.fn().mockResolvedValue(localBackend),
      updateBackend: vi.fn().mockImplementation(
        async (id: string, patch: Partial<BackendConnection>) => {
          backends = backends.map((item) => (item.id === id ? { ...item, ...patch } : item))
        }
      ),
    })
    await renderLoaded(<BackendManager open onOpenChange={vi.fn()} />)

    await user.click(screen.getAllByTitle('编辑')[1])
    expect(screen.getByRole('heading', { name: '编辑连接' })).toBeInTheDocument()
    const name = screen.getByLabelText('名称')
    expect(name).toHaveValue('远程后端')
    await user.clear(name)
    await user.type(name, '远程后端二号')
    await user.click(screen.getByRole('button', { name: '保存' }))

    expect(api.updateBackend).toHaveBeenCalledWith(
      'remote',
      expect.objectContaining({
        id: 'remote',
        name: '远程后端二号',
        url: 'https://example.com',
      })
    )
    expect(await screen.findByText('远程后端二号')).toBeInTheDocument()
  })

  it('删除非活跃连接并允许取消删除确认', async () => {
    const user = userEvent.setup()
    let backends: BackendConnection[] = [localBackend, remoteBackend]
    const api = installElectronAPI({
      getBackends: vi.fn().mockImplementation(async () => backends),
      getActiveBackend: vi.fn().mockResolvedValue(localBackend),
      removeBackend: vi.fn().mockImplementation(async (id: string) => {
        backends = backends.filter((item) => item.id !== id)
      }),
    })
    await renderLoaded(<BackendManager open onOpenChange={vi.fn()} />)

    await user.click(screen.getByTitle('删除'))
    const dialog = screen.getByTestId('delete-dialog')
    expect(within(dialog).getByText(/确定要删除 远程后端/)).toBeInTheDocument()

    await user.click(within(dialog).getByRole('button', { name: '模拟取消删除' }))
    expect(screen.queryByTestId('delete-dialog')).not.toBeInTheDocument()
    expect(api.removeBackend).not.toHaveBeenCalled()

    await user.click(screen.getByTitle('删除'))
    await user.click(
      within(screen.getByTestId('delete-dialog')).getByRole('button', { name: '删除' })
    )
    expect(api.removeBackend).toHaveBeenCalledWith('remote')
    await waitFor(() => {
      expect(screen.queryByText('远程后端')).not.toBeInTheDocument()
    })
  })
})
