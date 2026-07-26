import type { ReactNode } from 'react'

import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ChatEmbedPage } from '../chat/embed'
import { NotFoundPage } from '../404'
import { ModelPresetsPage } from '../model-presets'
import { PlannerMonitorPage } from '../monitor'
import { PluginConfigEmbedPage } from '../plugin-config-embed'
import { PluginMirrorsEmbedPage } from '../plugin-mirrors-embed'
import { PluginMarketplaceEmbedPage } from '../plugins/embed'

const { navigateMock, shellState } = vi.hoisted(() => ({
  navigateMock: vi.fn(),
  shellState: { checking: false },
}))

vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => navigateMock,
}))

vi.mock('@/components/embed-page-shell', () => ({
  EmbedPageShell: ({
    children,
    shellId,
    title,
  }: {
    children: ReactNode
    shellId: string
    title: string
  }) => (
    <section data-testid="embed-shell" data-shell-id={shellId} data-title={title}>
      {children}
    </section>
  ),
}))

vi.mock('../plugin-config', () => ({
  PluginConfigPage: () => <div>插件配置内容</div>,
}))

vi.mock('../plugin-mirrors', () => ({
  PluginMirrorsPage: ({ embedded }: { embedded?: boolean }) => (
    <div data-testid="plugin-mirrors" data-embedded={String(embedded)} />
  ),
}))

vi.mock('../plugins/PluginMarketplacePage', () => ({
  PluginMarketplacePage: ({ embedded }: { embedded?: boolean }) => (
    <div data-testid="plugin-marketplace" data-embedded={String(embedded)} />
  ),
}))

vi.mock('../monitor/maisaka-monitor', () => ({
  MaisakaMonitor: () => <div>监控主体</div>,
}))

vi.mock('../chat/index', () => ({
  ChatPage: () => <div>聊天工作区</div>,
}))

vi.mock('@/hooks/use-auth', () => ({
  useAuthGuard: () => ({ checking: shellState.checking }),
}))

vi.mock('@/hooks/use-background', () => ({
  useBackground: () => ({ config: { type: 'none' } }),
}))

vi.mock('@/components/background-layer', () => ({
  BackgroundLayer: ({ layerId }: { layerId: string }) => (
    <div data-testid="background-layer" data-layer-id={layerId} />
  ),
}))

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

beforeEach(() => {
  shellState.checking = false
})

describe('基础路由页面', () => {
  it('404 页面可以返回首页或调用浏览器后退', async () => {
    const user = userEvent.setup()
    const backSpy = vi.spyOn(window.history, 'back').mockImplementation(() => {})
    render(<NotFoundPage />)

    expect(screen.getByRole('heading', { name: '页面未找到' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '返回首页' }))
    expect(navigateMock).toHaveBeenCalledWith({ to: '/' })

    await user.click(screen.getByRole('button', { name: '返回上一页' }))
    expect(backSpy).toHaveBeenCalledOnce()
  })

  it('模型预设页明确展示开发中状态与计划能力', () => {
    render(<ModelPresetsPage />)

    expect(screen.getByRole('heading', { name: '模型分配预设市场' })).toBeInTheDocument()
    expect(screen.getByText('功能开发中')).toBeInTheDocument()
    expect(screen.getByText('一键下载和应用预设配置')).toBeInTheDocument()
  })
})

describe('嵌入页转发壳', () => {
  it.each([
    [
      <PluginConfigEmbedPage key="config" />,
      'embed-plugin-config',
      '插件管理 - MaiBot Dashboard',
      '插件配置内容',
    ],
    [
      <PluginMirrorsEmbedPage key="mirrors" />,
      'embed-plugin-mirrors',
      '插件商店设置 - MaiBot Dashboard',
      null,
    ],
    [
      <PluginMarketplaceEmbedPage key="market" />,
      'embed-plugin-marketplace',
      '插件市场 - MaiBot Dashboard',
      null,
    ],
  ])('向 EmbedPageShell 传递固定标识和标题', (page, shellId, title, childText) => {
    render(page)

    const shell = screen.getByTestId('embed-shell')
    expect(shell).toHaveAttribute('data-shell-id', shellId)
    expect(shell).toHaveAttribute('data-title', title)
    if (childText) expect(screen.getByText(childText)).toBeInTheDocument()
  })

  it('插件设置和市场页向内部页面传递 embedded 标记', () => {
    const { rerender } = render(<PluginMirrorsEmbedPage />)
    expect(screen.getByTestId('plugin-mirrors')).toHaveAttribute('data-embedded', 'true')

    rerender(<PluginMarketplaceEmbedPage />)
    expect(screen.getByTestId('plugin-marketplace')).toHaveAttribute('data-embedded', 'true')
  })

  it('监控入口渲染监控主体', () => {
    render(<PlannerMonitorPage />)
    expect(screen.getByText('监控主体')).toBeInTheDocument()
  })
})

describe('聊天嵌入页', () => {
  it('认证检查期间显示加载文案', () => {
    shellState.checking = true
    render(<ChatEmbedPage />)

    expect(screen.getByText('麦麦正在啃食服务器...')).toBeInTheDocument()
    expect(screen.queryByText('聊天工作区')).not.toBeInTheDocument()
  })

  it('认证完成后渲染背景和聊天工作区并更新标题', () => {
    render(<ChatEmbedPage />)

    expect(document.title).toBe('聊聊 - MaiBot Dashboard')
    expect(screen.getByTestId('background-layer')).toHaveAttribute('data-layer-id', 'page')
    expect(screen.getByText('聊天工作区')).toBeInTheDocument()
    expect(screen.getByRole('main')).toHaveAttribute('id', 'main-content')
  })
})
