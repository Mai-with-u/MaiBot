import type { ReactNode } from 'react'

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { PluginMirrorsPage } from '../plugin-mirrors'
import { PluginSettingsDialog } from '../plugins/PluginSettingsDialog'
import { backendApi } from '@/lib/http'
import { invalidatePluginMarketCache } from '@/lib/plugin-api'

const { toastMock, navigateMock } = vi.hoisted(() => ({ toastMock: vi.fn(), navigateMock: vi.fn() }))
vi.mock('@/hooks/use-toast', () => ({ useToast: () => ({ toast: toastMock }) }))
vi.mock('@tanstack/react-router', () => ({ useNavigate: () => navigateMock }))
vi.mock('@/lib/http', () => ({ backendApi: { get: vi.fn(), put: vi.fn() } }))
vi.mock('@/lib/plugin-api', () => ({ invalidatePluginMarketCache: vi.fn() }))
vi.mock('@/host/PluginDependenciesSection', () => ({ PluginDependenciesSection: () => <div>插件依赖</div> }))

const settings = { information_source: 'cnb', download_source: 'cnb', custom_download_prefix: null }

function renderSettings() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  const onClose = vi.fn()
  const onSaved = vi.fn()
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>
  const view = render(<PluginSettingsDialog onClose={onClose} onSaved={onSaved} onDependenciesChanged={vi.fn()} />, { wrapper })
  return { ...view, client, onClose, onSaved }
}

async function choose(label: string, option: string) {
  const user = userEvent.setup()
  fireEvent.keyDown(screen.getByRole('combobox', { name: label }), { key: 'ArrowDown' })
  await user.click(await screen.findByRole('option', { name: option }))
}

beforeEach(() => {
  vi.mocked(backendApi.get).mockResolvedValue({ ...settings })
  vi.mocked(backendApi.put).mockImplementation(async (_path, options) => options?.body as never)
})

describe('旧镜像页迁移', () => {
  it.each([[false, '/plugins'], [true, '/plugins/embed']] as const)('embedded=%s 时替换历史记录返回市场', (embedded, to) => {
    const { container } = render(<PluginMirrorsPage embedded={embedded} />)
    expect(navigateMock).toHaveBeenCalledWith({ to, replace: true })
    expect(container).toBeEmptyDOMElement()
    expect(backendApi.get).not.toHaveBeenCalled()
  })
})

// 镜像 CRUD/优先级已移除，当前产品把信息源、下载源及一个自定义前缀统一在设置弹窗中保存。
describe('插件商店来源设置', () => {
  it('加载当前来源，初始没有修改时不允许保存', async () => {
    renderSettings()
    expect(await screen.findByRole('combobox', { name: '插件信息源' })).toHaveTextContent('CNB镜像')
    expect(screen.getByRole('combobox', { name: '插件下载源' })).toHaveTextContent('CNB镜像')
    expect(screen.getByRole('button', { name: '保存设置' })).toBeDisabled()
    expect(backendApi.get).toHaveBeenCalledWith('/api/webui/plugins/marketplace/source', { errorMessage: '获取插件商店设置失败' })
  })

  it('加载失败展示原因，重试后显示来源选择', async () => {
    vi.mocked(backendApi.get).mockRejectedValueOnce(new Error('后端不可用'))
    renderSettings()
    expect(await screen.findByText('后端不可用')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '重试' }))
    expect(await screen.findByRole('combobox', { name: '插件信息源' })).toBeInTheDocument()
    expect(backendApi.get).toHaveBeenCalledTimes(2)
  })

  it('信息源和下载源一起提交，保存后刷新市场缓存与详情', async () => {
    const { client, onSaved } = renderSettings()
    const invalidate = vi.spyOn(client, 'invalidateQueries')
    await screen.findByRole('combobox', { name: '插件信息源' })
    await choose('插件信息源', 'Github源')
    expect(backendApi.put).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button', { name: '保存设置' }))
    await waitFor(() => expect(onSaved).toHaveBeenCalledOnce())
    expect(backendApi.put).toHaveBeenCalledWith('/api/webui/plugins/marketplace/source', {
      body: { ...settings, information_source: 'github' }, errorMessage: '保存插件商店设置失败',
    })
    expect(invalidatePluginMarketCache).toHaveBeenCalledOnce()
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['plugin-detail'] })
    expect(client.getQueryData(['plugin-market-source'])).toEqual({ ...settings, information_source: 'github' })
    expect(toastMock).toHaveBeenCalledWith({ title: '设置已保存' })
    expect(screen.getByRole('button', { name: '保存设置' })).toBeDisabled()
  })

  it('自定义下载源需要非空前缀，完整设置一次保存', async () => {
    renderSettings()
    await screen.findByRole('combobox', { name: '插件下载源' })
    await choose('插件下载源', '自定义源')
    const prefix = screen.getByLabelText('Git 克隆前缀')
    expect(screen.getByRole('button', { name: '保存设置' })).toBeDisabled()
    await userEvent.type(prefix, '   ')
    expect(screen.getByRole('button', { name: '保存设置' })).toBeDisabled()
    await userEvent.clear(prefix)
    await userEvent.type(prefix, 'https://mirror.example/https://github.com')
    await userEvent.click(screen.getByRole('button', { name: '保存设置' }))
    await waitFor(() => expect(backendApi.put).toHaveBeenCalledWith('/api/webui/plugins/marketplace/source', {
      body: { ...settings, download_source: 'custom', custom_download_prefix: 'https://mirror.example/https://github.com' },
      errorMessage: '保存插件商店设置失败',
    }))
  })

  it('加载已有自定义前缀并允许修改', async () => {
    vi.mocked(backendApi.get).mockResolvedValue({ ...settings, download_source: 'custom', custom_download_prefix: 'https://old.example' })
    renderSettings()
    const prefix = await screen.findByLabelText('Git 克隆前缀')
    expect(prefix).toHaveValue('https://old.example')
    await userEvent.clear(prefix)
    await userEvent.type(prefix, 'https://new.example')
    await userEvent.click(screen.getByRole('button', { name: '保存设置' }))
    await waitFor(() => expect(backendApi.put).toHaveBeenCalledWith(expect.any(String), expect.objectContaining({
      body: { ...settings, download_source: 'custom', custom_download_prefix: 'https://new.example' },
    })))
  })

  it('取消未保存修改，不写入服务端', async () => {
    const { onClose } = renderSettings()
    await screen.findByRole('combobox', { name: '插件信息源' })
    await choose('插件信息源', 'CNB镜像')
    await userEvent.click(screen.getByRole('button', { name: '取消' }))
    expect(onClose).toHaveBeenCalledOnce()
    expect(backendApi.put).not.toHaveBeenCalled()
  })

  it('保存失败保留草稿，可再次保存', async () => {
    let rejectSave!: (error: Error) => void
    const pendingSave = new Promise<never>((_resolve, reject) => { rejectSave = reject })
    vi.mocked(backendApi.put).mockReturnValueOnce(pendingSave)
    const { client, onSaved } = renderSettings()
    await screen.findByRole('combobox', { name: '插件下载源' })
    await choose('插件下载源', 'Github源')
    const saveButton = screen.getByRole('button', { name: '保存设置' })
    await userEvent.click(saveButton)
    await waitFor(() => {
      expect(backendApi.put).toHaveBeenCalledOnce()
      expect(saveButton).toBeDisabled()
    })
    expect(onSaved).not.toHaveBeenCalled()

    await act(async () => { rejectSave(new Error('保存失败')) })
    await waitFor(() => expect(saveButton).toBeEnabled())
    expect(onSaved).not.toHaveBeenCalled()
    expect(screen.getByRole('combobox', { name: '插件下载源' })).toHaveTextContent('Github源')
    expect(client.getQueryData(['plugin-market-source'])).toEqual(settings)

    await userEvent.click(saveButton)
    await waitFor(() => expect(onSaved).toHaveBeenCalledOnce())
    expect(backendApi.put).toHaveBeenCalledTimes(2)
    expect(backendApi.put).toHaveBeenLastCalledWith('/api/webui/plugins/marketplace/source', {
      body: { ...settings, download_source: 'github' }, errorMessage: '保存插件商店设置失败',
    })
    expect(client.getQueryData(['plugin-market-source'])).toEqual({ ...settings, download_source: 'github' })
    expect(saveButton).toBeDisabled()
  })
})
