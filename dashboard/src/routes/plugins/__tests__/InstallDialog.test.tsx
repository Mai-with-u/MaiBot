import type { ReactNode } from 'react'
import type { PluginManifest } from '@/types/plugin'
import type { PluginInfo, PluginLoadProgress } from '../types'

import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { InstallDialog } from '../InstallDialog'

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
      <div data-testid="dialog-root">
        <button type="button" onClick={() => onOpenChange(false)}>
          模拟外部关闭
        </button>
        {children}
      </div>
    ) : null,
  DialogContent: ({ children }: { children: ReactNode }) => <section>{children}</section>,
  DialogHeader: ({ children }: { children: ReactNode }) => <header>{children}</header>,
  DialogTitle: ({ children }: { children: ReactNode }) => <h2>{children}</h2>,
  DialogDescription: ({ children }: { children: ReactNode }) => <p>{children}</p>,
  DialogFooter: ({ children }: { children: ReactNode }) => <footer>{children}</footer>,
}))

vi.mock('@/components/ui/checkbox', () => ({
  Checkbox: ({
    id,
    checked,
    onCheckedChange,
  }: {
    id?: string
    checked?: boolean
    onCheckedChange: (checked: boolean) => void
  }) => (
    <input
      id={id}
      type="checkbox"
      checked={Boolean(checked)}
      onChange={(event) => onCheckedChange(event.target.checked)}
    />
  ),
}))

vi.mock('@/components/ui/tabs', () => ({
  Tabs: ({
    children,
    onValueChange,
  }: {
    children: ReactNode
    onValueChange: (value: string) => void
  }) => (
    <div>
      <button type="button" onClick={() => onValueChange('custom')}>
        切换自定义分支
      </button>
      {children}
    </div>
  ),
  TabsList: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  TabsTrigger: ({ children }: { children: ReactNode }) => <span>{children}</span>,
}))

vi.mock('@/components/ui/select', () => ({
  Select: ({
    children,
    onValueChange,
  }: {
    children: ReactNode
    onValueChange: (value: string) => void
  }) => (
    <div>
      <button type="button" onClick={() => onValueChange('dev')}>
        选择 dev
      </button>
      {children}
    </div>
  ),
  SelectTrigger: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  SelectValue: () => null,
  SelectContent: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  SelectItem: ({ children }: { children: ReactNode }) => <span>{children}</span>,
}))

function makePlugin(
  id: string,
  overrides: Omit<Partial<PluginInfo>, 'manifest'> & { manifest?: Partial<PluginManifest> } = {},
): PluginInfo {
  const { manifest: manifestOverrides, ...pluginOverrides } = overrides
  return {
    id,
    manifest: {
      manifest_version: 2,
      id,
      name: `插件-${id}`,
      version: '1.2.0',
      description: `${id} 描述`,
      author: { name: '测试作者' },
      license: 'MIT',
      host_application: { min_version: '1.0.0' },
      repository_url: `https://example.com/${id}.git`,
      keywords: ['标签一'],
      plugin_type: 'extension',
      default_locale: 'zh-CN',
      ...manifestOverrides,
    },
    downloads: 0,
    rating: 0,
    review_count: 0,
    installed: false,
    published_at: '2026-01-01T00:00:00.000Z',
    updated_at: '2026-01-01T00:00:00.000Z',
    source: 'market',
    ...pluginOverrides,
  }
}

function makeProgress(
  pluginId: string,
  overrides: Partial<PluginLoadProgress> = {},
): PluginLoadProgress {
  return {
    operation: 'install',
    stage: 'loading',
    progress: 40,
    message: '正在克隆仓库',
    plugin_id: pluginId,
    total_plugins: 1,
    loaded_plugins: 0,
    ...overrides,
  }
}

afterEach(() => {
  cleanup()
})

describe('InstallDialog 确认安装', () => {
  it('默认从 main 分支确认安装，并可在未开始时取消', async () => {
    const user = userEvent.setup()
    const onInstall = vi.fn()
    const onOpenChange = vi.fn()
    render(
      <InstallDialog
        open
        plugin={makePlugin('alpha')}
        loadProgress={null}
        onOpenChange={onOpenChange}
        onInstall={onInstall}
      />,
    )

    expect(screen.getByText('安装插件')).toBeInTheDocument()
    expect(screen.getByText('安装 插件-alpha')).toBeInTheDocument()
    expect(screen.getByText('版本: 1.2.0')).toBeInTheDocument()
    expect(screen.getByText('作者: 测试作者')).toBeInTheDocument()
    expect(screen.getByText('将从默认分支 (main) 安装插件')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '安装' }))
    expect(onInstall).toHaveBeenCalledTimes(1)
    expect(onInstall).toHaveBeenCalledWith('main')

    await user.click(screen.getByRole('button', { name: '取消' }))
    expect(onOpenChange).toHaveBeenCalledWith(false)
  })

  it('作者为字符串时直接展示，高级模式可改用预设或自定义分支', async () => {
    const user = userEvent.setup()
    const onInstall = vi.fn()
    render(
      <InstallDialog
        open
        plugin={makePlugin('alpha', {
          manifest: { author: '字符串作者' as unknown as PluginManifest['author'] },
        })}
        loadProgress={null}
        onOpenChange={vi.fn()}
        onInstall={onInstall}
      />,
    )

    expect(screen.getByText('作者: 字符串作者')).toBeInTheDocument()

    await user.click(screen.getByLabelText('高级选项'))
    await user.click(screen.getByRole('button', { name: '选择 dev' }))
    await user.click(screen.getByRole('button', { name: '安装' }))
    expect(onInstall).toHaveBeenCalledWith('dev')

    await user.click(screen.getByRole('button', { name: '切换自定义分支' }))
    const input = screen.getByPlaceholderText('输入分支名称，例如: feature/new-feature')
    await user.type(input, '   ')
    await user.click(screen.getByRole('button', { name: '安装' }))
    expect(onInstall).toHaveBeenCalledTimes(1)

    await user.clear(input)
    await user.type(input, 'feature/new-ui')
    await user.click(screen.getByRole('button', { name: '安装' }))
    expect(onInstall).toHaveBeenCalledWith('feature/new-ui')
  })
})

describe('InstallDialog 安装失败与进行中', () => {
  it('安装失败展示错误信息，只保留关闭并允许退出', async () => {
    const user = userEvent.setup()
    const onOpenChange = vi.fn()
    const plugin = makePlugin('alpha')
    const { rerender } = render(
      <InstallDialog
        open
        plugin={plugin}
        loadProgress={makeProgress('alpha', { stage: 'error', error: '仓库不存在' })}
        onOpenChange={onOpenChange}
        onInstall={vi.fn()}
      />,
    )

    expect(screen.getByText('安装失败')).toBeInTheDocument()
    expect(screen.getByText('仓库不存在')).toBeInTheDocument()
    expect(screen.queryByText('40%')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '安装' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '关闭' })).toBeEnabled()

    await user.click(screen.getByRole('button', { name: '关闭' }))
    expect(onOpenChange).toHaveBeenCalledWith(false)

    rerender(
      <InstallDialog
        open
        plugin={plugin}
        loadProgress={makeProgress('alpha', { stage: 'error', error: undefined, message: '' })}
        onOpenChange={onOpenChange}
        onInstall={vi.fn()}
      />,
    )
    expect(screen.getByText('操作失败')).toBeInTheDocument()
  })

  it('安装中禁止关闭，成功后只提供关闭按钮', async () => {
    const user = userEvent.setup()
    const onOpenChange = vi.fn()
    const plugin = makePlugin('alpha')
    const { rerender } = render(
      <InstallDialog
        open
        plugin={plugin}
        loadProgress={makeProgress('alpha', {
          stage: 'loading',
          mirror_name: 'ghproxy',
          mirror_index: 1,
          total_mirrors: 2,
        })}
        onOpenChange={onOpenChange}
        onInstall={vi.fn()}
      />,
    )

    expect(screen.getByText('正在安装')).toBeInTheDocument()
    expect(screen.getByText('40%')).toBeInTheDocument()
    expect(screen.getByText('镜像源 1/2：ghproxy')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '安装中' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '取消' })).toBeDisabled()

    await user.click(screen.getByRole('button', { name: '模拟外部关闭' }))
    expect(onOpenChange).not.toHaveBeenCalled()

    rerender(
      <InstallDialog
        open
        plugin={plugin}
        loadProgress={makeProgress('alpha', { stage: 'success', progress: 100, message: '安装成功' })}
        onOpenChange={onOpenChange}
        onInstall={vi.fn()}
      />,
    )
    expect(screen.getByText('安装完成')).toBeInTheDocument()
    expect(screen.getByText('100%')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '安装' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '关闭' })).toBeEnabled()
  })

  it('忽略非安装进度以及其他插件的进度', () => {
    const plugin = makePlugin('alpha')
    const { rerender } = render(
      <InstallDialog
        open
        plugin={plugin}
        loadProgress={makeProgress('beta', { stage: 'error', error: '别人失败了' })}
        onOpenChange={vi.fn()}
        onInstall={vi.fn()}
      />,
    )
    expect(screen.queryByText('安装失败')).not.toBeInTheDocument()
    expect(screen.queryByText('别人失败了')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '安装' })).toBeEnabled()

    rerender(
      <InstallDialog
        open
        plugin={plugin}
        loadProgress={makeProgress('alpha', {
          operation: 'update',
          stage: 'error',
          error: '更新失败不应出现',
        })}
        onOpenChange={vi.fn()}
        onInstall={vi.fn()}
      />,
    )
    expect(screen.queryByText('更新失败不应出现')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '安装' })).toBeEnabled()
  })
})
