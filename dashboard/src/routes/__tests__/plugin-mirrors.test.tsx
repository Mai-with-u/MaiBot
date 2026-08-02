import type { ReactNode } from 'react'

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { PluginMirrorsPage } from '../plugin-mirrors'
import { backendApi } from '@/lib/http'

const toastMock = vi.fn()
const navigateMock = vi.fn()

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

vi.mock('@/hooks/use-toast', () => ({ useToast: () => ({ toast: toastMock }) }))
vi.mock('@tanstack/react-router', () => ({ useNavigate: () => navigateMock }))
vi.mock('@/lib/http', () => ({
  backendApi: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}))

/** 与页面内部 MirrorConfig 对齐的测试数据结构（源文件未导出该类型，这里内联一份） */
interface MirrorConfig {
  id: string
  name: string
  raw_prefix: string
  clone_prefix: string
  enabled: boolean
  priority: number
}

function makeMirror(overrides: Partial<MirrorConfig> = {}): MirrorConfig {
  return {
    id: 'official',
    name: '官方镜像源',
    raw_prefix: 'https://raw.example.com',
    clone_prefix: 'https://clone.example.com',
    enabled: true,
    priority: 1,
    ...overrides,
  }
}

function makeWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
}

/**
 * 页面同时渲染桌面表格与移动端卡片（仅靠 CSS 隐藏），图标按钮无可访问名称，
 * 因此通过 lucide 图标 class 定位按钮；index 0 固定命中 DOM 中靠前的桌面版。
 */
function iconButton(iconClassFragment: string, index = 0): HTMLButtonElement {
  const svgs = Array.from(document.querySelectorAll(`svg[class*="${iconClassFragment}"]`))
  const button = svgs[index]?.closest('button')
  if (!button) throw new Error(`未找到包含图标 ${iconClassFragment} 的按钮（index=${index}）`)
  return button as HTMLButtonElement
}

beforeEach(() => {
  localStorage.clear()
  vi.mocked(backendApi.get).mockResolvedValue({ mirrors: [makeMirror()] } as never)
  vi.mocked(backendApi.post).mockResolvedValue({} as never)
  vi.mocked(backendApi.put).mockResolvedValue({} as never)
  vi.mocked(backendApi.delete).mockResolvedValue({} as never)
})

async function renderPage(props: { embedded?: boolean } = {}) {
  render(<PluginMirrorsPage {...props} />, { wrapper: makeWrapper() })
  // 等待列表查询完成（镜像名称出现即代表脱离 loading 态）
  await screen.findAllByText('官方镜像源')
}

describe('PluginMirrorsPage 特征化', () => {
  it('初始加载调用镜像源列表接口并渲染名称/ID/Raw 前缀', async () => {
    vi.mocked(backendApi.get).mockResolvedValue({
      mirrors: [makeMirror(), makeMirror({ id: 'ghproxy', name: '加速镜像', priority: 2 })],
    } as never)
    render(<PluginMirrorsPage />, { wrapper: makeWrapper() })

    expect(await screen.findAllByText('官方镜像源')).not.toHaveLength(0)
    expect(backendApi.get).toHaveBeenCalledWith('/api/webui/plugins/mirrors', {
      errorMessage: '获取镜像源列表失败',
    })
    // 桌面表格 + 移动端卡片各渲染一份
    expect(screen.getAllByText('加速镜像')).not.toHaveLength(0)
    expect(screen.getAllByText('official')).not.toHaveLength(0)
    expect(screen.getAllByText(/raw\.example\.com/)).not.toHaveLength(0)
  })

  it('列表加载失败时展示错误信息，点击重新加载会重新请求', async () => {
    vi.mocked(backendApi.get).mockRejectedValueOnce(new Error('后端不可用'))
    const user = userEvent.setup()
    render(<PluginMirrorsPage />, { wrapper: makeWrapper() })

    expect(await screen.findByText('加载失败')).toBeInTheDocument()
    expect(screen.getByText('后端不可用')).toBeInTheDocument()

    // 重新加载后（beforeEach 里的默认 resolved mock 生效）渲染出列表
    await user.click(screen.getByRole('button', { name: '重新加载' }))
    expect(await screen.findAllByText('官方镜像源')).not.toHaveLength(0)
    expect(backendApi.get).toHaveBeenCalledTimes(2)
  })

  it('「仅显示当前版本」默认开启，切换后把 false 写入 localStorage', async () => {
    const user = userEvent.setup()
    await renderPage()

    const compatSwitch = screen.getByRole('switch', { name: '仅显示当前版本' })
    expect(compatSwitch).toHaveAttribute('aria-checked', 'true')
    // 挂载时 useEffect 已回写当前值
    expect(localStorage.getItem('plugins-market-compatible-only')).toBe('true')

    await user.click(compatSwitch)
    expect(compatSwitch).toHaveAttribute('aria-checked', 'false')
    await waitFor(() =>
      expect(localStorage.getItem('plugins-market-compatible-only')).toBe('false')
    )
  })

  it('localStorage 为 false 时「仅显示当前版本」初始为关闭', async () => {
    localStorage.setItem('plugins-market-compatible-only', 'false')
    await renderPage()

    expect(screen.getByRole('switch', { name: '仅显示当前版本' })).toHaveAttribute(
      'aria-checked',
      'false'
    )
  })

  it('返回按钮默认导航到 /plugins', async () => {
    const user = userEvent.setup()
    await renderPage()

    await user.click(iconButton('arrow-left'))
    expect(navigateMock).toHaveBeenCalledWith({ to: '/plugins' })
  })

  it('embedded 模式下返回按钮导航到 /plugins/embed', async () => {
    const user = userEvent.setup()
    await renderPage({ embedded: true })

    await user.click(iconButton('arrow-left'))
    expect(navigateMock).toHaveBeenCalledWith({ to: '/plugins/embed' })
  })

  it('添加镜像源：填写表单提交后调用 POST，成功后弹 toast、关闭对话框并刷新列表', async () => {
    const user = userEvent.setup()
    await renderPage()

    await user.click(screen.getByRole('button', { name: '添加镜像源' }))
    expect(await screen.findByText('添加新的 Git 镜像源配置')).toBeInTheDocument()

    await user.type(screen.getByLabelText('镜像源 ID *'), 'my-mirror')
    await user.type(screen.getByLabelText('名称 *'), '我的镜像')
    await user.type(screen.getByLabelText('Raw 文件前缀 *'), 'https://raw.test')
    await user.type(screen.getByLabelText('克隆前缀 *'), 'https://clone.test')
    await user.click(screen.getByRole('button', { name: '添加' }))

    await waitFor(() =>
      expect(backendApi.post).toHaveBeenCalledWith('/api/webui/plugins/mirrors', {
        body: {
          id: 'my-mirror',
          name: '我的镜像',
          raw_prefix: 'https://raw.test',
          clone_prefix: 'https://clone.test',
          enabled: true,
          priority: 1,
        },
        errorMessage: '添加镜像源失败',
      })
    )
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith({ title: '添加成功', description: '镜像源已添加' })
    )
    // 对话框关闭 + 列表失效重新拉取
    await waitFor(() =>
      expect(screen.queryByText('添加新的 Git 镜像源配置')).not.toBeInTheDocument()
    )
    await waitFor(() => expect(backendApi.get).toHaveBeenCalledTimes(2))
  })

  it('切换镜像源启用状态调用 PUT 取反 enabled', async () => {
    const user = userEvent.setup()
    await renderPage()

    // switch 顺序：仅显示当前版本 → 桌面表格行 → 移动端卡片
    const switches = screen.getAllByRole('switch')
    await user.click(switches[1])

    await waitFor(() =>
      expect(backendApi.put).toHaveBeenCalledWith('/api/webui/plugins/mirrors/official', {
        body: { enabled: false },
        errorMessage: '更新状态失败',
      })
    )
  })

  it('删除镜像源：确认后调用 DELETE 并提示删除成功', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    const user = userEvent.setup()
    await renderPage()

    await user.click(iconButton('trash'))

    expect(confirmSpy).toHaveBeenCalledWith('确定要删除这个镜像源吗？')
    await waitFor(() =>
      expect(backendApi.delete).toHaveBeenCalledWith('/api/webui/plugins/mirrors/official', {
        errorMessage: '删除镜像源失败',
      })
    )
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith({ title: '删除成功', description: '镜像源已删除' })
    )
  })

  it('删除镜像源：取消确认则不发请求', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(false)
    const user = userEvent.setup()
    await renderPage()

    await user.click(iconButton('trash'))
    expect(backendApi.delete).not.toHaveBeenCalled()
  })

  it('优先级为 1 时上移按钮禁用，下移调用 PUT priority+1', async () => {
    const user = userEvent.setup()
    await renderPage()

    expect(iconButton('chevron-up')).toBeDisabled()

    await user.click(iconButton('chevron-down'))
    await waitFor(() =>
      expect(backendApi.put).toHaveBeenCalledWith('/api/webui/plugins/mirrors/official', {
        body: { priority: 2 },
        errorMessage: '更新优先级失败',
      })
    )
  })

  it('优先级大于 1 时上移调用 PUT priority-1', async () => {
    vi.mocked(backendApi.get).mockResolvedValue({
      mirrors: [makeMirror({ priority: 3 })],
    } as never)
    const user = userEvent.setup()
    await renderPage()

    const upButton = iconButton('chevron-up')
    expect(upButton).toBeEnabled()
    await user.click(upButton)

    await waitFor(() =>
      expect(backendApi.put).toHaveBeenCalledWith('/api/webui/plugins/mirrors/official', {
        body: { priority: 2 },
        errorMessage: '更新优先级失败',
      })
    )
  })

  it('编辑镜像源：对话框预填数据，保存后以完整字段调用 PUT', async () => {
    const user = userEvent.setup()
    await renderPage()

    await user.click(iconButton('pencil'))
    expect(await screen.findByText('修改镜像源配置')).toBeInTheDocument()

    // 表单预填当前镜像数据
    const nameInput = screen.getByLabelText('名称 *')
    expect(nameInput).toHaveValue('官方镜像源')
    expect(screen.getByLabelText('Raw 文件前缀 *')).toHaveValue('https://raw.example.com')

    await user.clear(nameInput)
    await user.type(nameInput, '改名镜像')
    await user.click(screen.getByRole('button', { name: '保存' }))

    await waitFor(() =>
      expect(backendApi.put).toHaveBeenCalledWith('/api/webui/plugins/mirrors/official', {
        body: {
          name: '改名镜像',
          raw_prefix: 'https://raw.example.com',
          clone_prefix: 'https://clone.example.com',
          enabled: true,
          priority: 1,
        },
        errorMessage: '更新镜像源失败',
      })
    )
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith({ title: '更新成功', description: '镜像源已更新' })
    )
    await waitFor(() => expect(screen.queryByText('修改镜像源配置')).not.toBeInTheDocument())
  })
})
