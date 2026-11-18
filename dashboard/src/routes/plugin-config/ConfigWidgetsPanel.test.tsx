import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ConfigWidgetsPanel } from './ConfigWidgetsPanel'
import { getConfigWidgets, invokeConfigWidget } from '@/lib/plugin-api/config'
import type { ConfigWidgetSnapshot } from '@/lib/plugin-api/types'

vi.mock('@/lib/plugin-api/config', () => ({
  getConfigWidgets: vi.fn(),
  invokeConfigWidget: vi.fn(),
}))
afterEach(() => {
  cleanup()
  vi.resetAllMocks()
})

function snapshot(expiresAt = Date.now() / 1000 + 120): ConfigWidgetSnapshot {
  return {
    widgets: [
      { id: 'qr', kind: 'qrcode', label: '扫码登录' },
      { id: 'refresh', kind: 'action', label: '刷新二维码' },
    ],
    states: {
      qr: {
        text: '等待扫码',
        tone: 'neutral',
        visible: true,
        disabled: false,
        image: 'data:image/png;base64,AAAA',
        expires_at: expiresAt,
      },
      refresh: { text: '', tone: 'neutral', visible: true, disabled: false },
    },
  }
}
function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const view = render(
    <QueryClientProvider client={client}>
      <ConfigWidgetsPanel pluginId="adapter.test" />
    </QueryClientProvider>
  )
  return { client, ...view }
}

describe('配置页运行时控件', () => {
  it('离开页面后，迟到的按钮结果不会重建二维码缓存', async () => {
    vi.mocked(getConfigWidgets).mockResolvedValue(snapshot())
    let complete!: (value: ConfigWidgetSnapshot) => void
    vi.mocked(invokeConfigWidget).mockReturnValue(new Promise((resolve) => { complete = resolve }))
    const { client, unmount } = mount()
    await screen.findByAltText('扫码登录')
    fireEvent.click(screen.getByRole('button', { name: '刷新二维码' }))
    await waitFor(() => expect(invokeConfigWidget).toHaveBeenCalledOnce())
    unmount()
    await act(async () => { complete(snapshot()) })
    await waitFor(() => expect(client.getQueryData(['plugin-config-widgets', 'adapter.test'])).toBeUndefined())
  })

  it('显示二维码，按钮只调用运行时接口并更新状态', async () => {
    vi.mocked(getConfigWidgets).mockResolvedValue(snapshot())
    const connected = snapshot()
    connected.states.qr = { text: '已连接', tone: 'success', visible: true, disabled: false }
    vi.mocked(invokeConfigWidget).mockResolvedValue(connected)
    mount()
    expect(await screen.findByAltText('扫码登录')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '刷新二维码' }))
    await waitFor(() => expect(invokeConfigWidget).toHaveBeenCalledWith('adapter.test', 'refresh'))
    expect(await screen.findByText('已连接')).toBeInTheDocument()
    expect(screen.queryByAltText('扫码登录')).not.toBeInTheDocument()
  })

  it('不显示过期二维码及不可见控件', async () => {
    const expired = snapshot(1)
    expired.states.refresh.visible = false
    vi.mocked(getConfigWidgets).mockResolvedValue(expired)
    mount()
    expect(await screen.findByText('二维码已过期，请刷新')).toBeInTheDocument()
    expect(screen.queryByAltText('扫码登录')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '刷新二维码' })).not.toBeInTheDocument()
  })

  it('读取失败显示错误，不伪装成正常状态', async () => {
    vi.mocked(getConfigWidgets).mockRejectedValue(new Error('插件未运行'))
    mount()
    expect(await screen.findByText('插件未运行')).toBeInTheDocument()
    expect(screen.queryByAltText('扫码登录')).not.toBeInTheDocument()
  })
})
