import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { PluginIcon } from '../PluginIcon'

vi.mock('@/lib/plugin-api', () => ({
  checkGitStatus: vi.fn(),
  checkPluginInstalled: vi.fn(),
  connectPluginProgressWebSocket: vi.fn(),
  fetchPluginList: vi.fn(),
  getCachedPluginList: vi.fn(),
  getInstalledPluginVersion: vi.fn(),
  getInstalledPlugins: vi.fn(),
  getMaimaiVersion: vi.fn(),
  installPlugin: vi.fn(),
  isPluginCompatible: vi.fn(),
  uninstallPlugin: vi.fn(),
  updatePlugin: vi.fn(),
}))

afterEach(() => {
  cleanup()
})

describe('PluginIcon 图标回退', () => {
  it('emoji 图标渲染字符并应用背景色', () => {
    const { container } = render(
      <PluginIcon
        pluginId="emoji-plugin"
        manifest={{
          display: { icon: { type: 'emoji', value: '🎯', background: '#ff00aa' } },
        }}
      />,
    )

    expect(screen.getByText('🎯')).toHaveAttribute('aria-hidden', 'true')
    expect(container.firstElementChild).toHaveStyle({ backgroundColor: 'rgb(255, 0, 170)' })
  })

  it('lucide 命中、别名、未知值回退到 fallback 或插件类型图标', () => {
    const { rerender, container } = render(
      <PluginIcon
        pluginId="lucide-bot"
        manifest={{ display: { icon: { type: 'lucide', value: '  BoT  ' } } }}
        iconClassName="icon-size"
      />,
    )
    expect(container.querySelector('svg.lucide-bot')).toHaveClass('h-5', 'w-5', 'icon-size')

    rerender(
      <PluginIcon
        pluginId="lucide-alias"
        manifest={{ display: { icon: { type: 'lucide', value: 'bar-chart-3' } } }}
      />,
    )
    // lucide-react 0.556 把 BarChart3 重导出为 ChartColumn
    expect(container.querySelector('svg.lucide-chart-column')).not.toBeNull()

    rerender(
      <PluginIcon
        pluginId="lucide-underscore-alias"
        manifest={{ display: { icon: { type: 'lucide', value: 'bar_chart_3' } } }}
      />,
    )
    expect(container.querySelector('svg.lucide-chart-column')).not.toBeNull()

    rerender(
      <PluginIcon
        pluginId="lucide-fallback"
        manifest={{
          plugin_type: 'game',
          display: { icon: { type: 'lucide', value: 'not-an-icon', fallback: 'shield' } },
        }}
      />,
    )
    expect(container.querySelector('svg.lucide-shield')).not.toBeNull()

    rerender(
      <PluginIcon
        pluginId="lucide-empty"
        manifest={{
          plugin_type: 'chat',
          display: { icon: { type: 'lucide', value: '   ', fallback: 'search' } },
        }}
      />,
    )
    expect(container.querySelector('svg.lucide-search')).not.toBeNull()

    rerender(
      <PluginIcon
        pluginId="lucide-type"
        manifest={{
          plugin_type: 'game',
          display: { icon: { type: 'lucide', value: 'missing-icon' } },
        }}
      />,
    )
    expect(container.querySelector('svg.lucide-gamepad-2')).not.toBeNull()
  })

  it('无图标时按插件类型回退，空白类型用扩展，未知类型用其他', () => {
    const { rerender, container } = render(
      <PluginIcon pluginId="adapter" manifest={{ plugin_type: 'adapter' }} />,
    )
    expect(container.querySelector('svg.lucide-plug')).not.toBeNull()

    rerender(<PluginIcon pluginId="blank-type" manifest={{ plugin_type: '   ' }} />)
    expect(container.querySelector('svg.lucide-puzzle')).not.toBeNull()

    rerender(<PluginIcon pluginId="unknown-type" manifest={{ plugin_type: 'not-real' }} />)
    expect(container.querySelector('svg.lucide-package')).not.toBeNull()

    rerender(<PluginIcon pluginId="no-manifest" />)
    expect(container.querySelector('svg.lucide-puzzle')).not.toBeNull()
  })

  it('已安装本地图标走插件 API，失败后回退到 fallback 或类型图标', () => {
    const { rerender, container } = render(
      <PluginIcon
        pluginId="hello/world"
        installed
        className="custom-wrap"
        manifest={{
          plugin_type: 'chat',
          display: { icon: { type: 'local', value: 'icon.png', fallback: 'search' } },
        }}
      />,
    )

    const image = container.querySelector('img')
    expect(image).toHaveAttribute('src', '/api/webui/plugins/icon/hello%2Fworld')
    expect(image).toHaveAttribute('alt', '')
    expect(container.firstElementChild).toHaveClass('custom-wrap')

    fireEvent.error(image!)
    expect(container.querySelector('img')).toBeNull()
    expect(container.querySelector('svg.lucide-search')).not.toBeNull()

    rerender(
      <PluginIcon
        pluginId="local-type-fallback"
        installed
        manifest={{
          plugin_type: 'chat',
          display: { icon: { type: 'local', value: 'icon.png' } },
        }}
      />,
    )
    const typeFallbackImage = container.querySelector('img')
    expect(typeFallbackImage).toHaveAttribute(
      'src',
      '/api/webui/plugins/icon/local-type-fallback',
    )
    fireEvent.error(typeFallbackImage!)
    expect(container.querySelector('svg.lucide-bot')).not.toBeNull()
  })

  it('未安装时使用市场图标地址，缺失地址则直接回退', () => {
    const { rerender, container } = render(
      <PluginIcon
        pluginId="market-icon"
        marketplaceIconUrl="  https://cdn.example/icon.png  "
        manifest={{
          plugin_type: 'search',
          display: { icon: { type: 'local', value: 'icon.png' } },
        }}
      />,
    )
    expect(container.querySelector('img')).toHaveAttribute('src', 'https://cdn.example/icon.png')

    rerender(
      <PluginIcon
        pluginId="missing-market-icon"
        marketplaceIconUrl="   "
        manifest={{
          plugin_type: 'search',
          display: { icon: { type: 'local', value: 'icon.png', fallback: 'cloud' } },
        }}
      />,
    )
    expect(container.querySelector('img')).toBeNull()
    expect(container.querySelector('svg.lucide-cloud')).not.toBeNull()

    rerender(
      <PluginIcon
        pluginId="no-market-url"
        manifest={{
          plugin_type: 'search',
          display: { icon: { type: 'local', value: 'icon.png' } },
        }}
      />,
    )
    expect(container.querySelector('img')).toBeNull()
    expect(container.querySelector('svg.lucide-search')).not.toBeNull()
  })
})
