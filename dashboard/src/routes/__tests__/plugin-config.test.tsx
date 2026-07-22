import type { ReactNode } from 'react'

import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { PluginConfigPage } from '../plugin-config'
import * as pluginApi from '@/lib/plugin-api'

const toastMock = vi.fn()

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
  // openPluginConfig 会用 replaceState 写入 ?plugin=xxx，需重置避免深链接污染后续测试
  window.history.replaceState(null, '', '/plugin-config')
})

vi.mock('@/hooks/use-toast', () => ({ useToast: () => ({ toast: toastMock }) }))
vi.mock('@/lib/restart-context', () => ({
  RestartProvider: ({ children }: { children: ReactNode }) => <>{children}</>,
  useRestart: () => ({ isRestarting: false, triggerRestart: vi.fn() }),
}))
vi.mock('@/components/restart-overlay', () => ({ RestartOverlay: () => null }))
vi.mock('@/components/use-theme', () => ({ useTheme: () => ({ themeConfig: { dashboardStyle: 'modern' } }) }))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k, i18n: { resolvedLanguage: 'zh', language: 'zh' } }),
}))
vi.mock('@tanstack/react-router', () => ({
  useBlocker: () => ({ status: 'unblocked', reset: vi.fn(), proceed: vi.fn() }),
}))
// 避免真实 WebSocket 连接（插件进度订阅）
vi.mock('@/lib/plugin-progress-client', () => ({
  pluginProgressClient: { subscribe: vi.fn(() => Promise.resolve(() => Promise.resolve())) },
}))
vi.mock('@/components/CodeEditor', () => ({
  CodeEditor: ({ value, onChange }: { value: string; onChange?: (v: string) => void }) => (
    <textarea data-testid="code-editor" value={value} onChange={(e) => onChange?.(e.target.value)} />
  ),
}))
vi.mock('@/components/ListFieldEditor', () => ({ ListFieldEditor: () => <div data-testid="list-field-editor" /> }))

vi.mock('@/lib/plugin-api', () => ({
  getInstalledPlugins: vi.fn(),
  fetchPluginList: vi.fn(),
  getMaimaiVersion: vi.fn(),
  isPluginCompatible: vi.fn((minVersion: string, maxVersion: string | undefined, currentVersion: { version: string }) => {
    const current = currentVersion.version.split('.').map(Number)
    const min = minVersion.split('.').map(Number)
    const max = maxVersion?.split('.').map(Number)
    const compare = (left: number[], right: number[]) => {
      for (let index = 0; index < 3; index++) {
        if ((left[index] || 0) !== (right[index] || 0)) {
          return (left[index] || 0) - (right[index] || 0)
        }
      }
      return 0
    }
    return compare(current, min) >= 0 && (!max || compare(current, max) <= 0)
  }),
  getPluginConfigBundle: vi.fn(),
  updatePluginConfig: vi.fn(),
  updatePluginConfigRaw: vi.fn(),
  resetPluginConfig: vi.fn(),
  togglePlugin: vi.fn(),
  uninstallPlugin: vi.fn(),
  updatePlugin: vi.fn(),
}))

function makePlugin(id: string, name: string) {
  return {
    id,
    path: `/plugins/${id}`,
    enabled: true,
    load_status: 'success',
    load_error: undefined as string | undefined,
    manifest: {
      manifest_version: 2,
      name,
      version: '1.0.0',
      description: 'desc',
      author: { name: 'tester' },
      license: 'MIT',
      host_application: {
        min_version: '1.0.0',
        max_version: undefined as string | undefined,
      },
    },
  }
}

beforeEach(() => {
  Object.defineProperty(Element.prototype, 'scrollIntoView', {
    configurable: true,
    value: vi.fn(),
  })
  vi.mocked(pluginApi.getInstalledPlugins).mockResolvedValue([makePlugin('test.emoji', 'Emoji Plugin')] as never)
  vi.mocked(pluginApi.fetchPluginList).mockResolvedValue([] as never)
  vi.mocked(pluginApi.getMaimaiVersion).mockResolvedValue({
    version: '1.1.0',
    version_major: 1,
    version_minor: 1,
    version_patch: 0,
  })
  vi.mocked(pluginApi.getPluginConfigBundle).mockResolvedValue({
    schema: {
      plugin_info: { name: 'Emoji Plugin', version: '1.0.0', description: 'desc' },
      sections: {},
      layout: { type: 'auto' },
    },
    config: {},
    rawConfig: 'key = "value"\n',
  } as never)
  vi.mocked(pluginApi.updatePluginConfigRaw).mockResolvedValue({ success: true, message: 'ok' } as never)
  vi.mocked(pluginApi.updatePluginConfig).mockResolvedValue({ success: true, message: 'ok' } as never)
  vi.mocked(pluginApi.togglePlugin).mockResolvedValue({ success: true, enabled: false, message: '已禁用插件' } as never)
})

describe('PluginConfigPage 特征化', () => {
  it('显示已装插件且不暴露 A_Memorix', async () => {
    render(<PluginConfigPage />)
    expect(await screen.findByText('Emoji Plugin')).toBeInTheDocument()
    expect(screen.queryByText(/A_Memorix/i)).not.toBeInTheDocument()
  })

  it('无插件时显示空态提示', async () => {
    vi.mocked(pluginApi.getInstalledPlugins).mockResolvedValue([] as never)
    render(<PluginConfigPage />)
    await waitFor(() => expect(screen.getByText('暂无已安装的插件')).toBeInTheDocument())
  })

  it('插件版本不兼容时优先展示用户可理解的结论并保留技术详情', async () => {
    const user = userEvent.setup()
    const incompatiblePlugin = makePlugin('test.incompatible', 'Incompatible Plugin')
    incompatiblePlugin.manifest.version = '1.3.2'
    incompatiblePlugin.manifest.host_application.max_version = '1.0.99'
    incompatiblePlugin.load_status = 'failed'
    incompatiblePlugin.load_error =
      'manifest 校验失败: Host 版本不兼容: 版本 1.1.0 高于最大支持 1.0.99 (当前 Host: 1.1.0)'
    vi.mocked(pluginApi.getInstalledPlugins).mockResolvedValue([incompatiblePlugin] as never)

    render(<PluginConfigPage />)

    expect(await screen.findByText('当前插件版本已不兼容')).toBeInTheDocument()
    expect(screen.getByText('已安装 v1.3.2 与当前麦麦版本不兼容，请前往插件市场查看兼容版本。')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '前往插件市场' })).toHaveAttribute('href', '/plugins')
    expect(screen.queryByText(incompatiblePlugin.load_error)).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '查看详情' }))
    expect(screen.getByText(incompatiblePlugin.load_error)).toBeInTheDocument()
  })

  it('插件版本不兼容且市场有兼容新版时直接引导更新', async () => {
    const user = userEvent.setup()
    const incompatiblePlugin = makePlugin('test.incompatible', 'Incompatible Plugin')
    incompatiblePlugin.manifest.version = '1.3.2'
    incompatiblePlugin.manifest.host_application.max_version = '1.0.99'
    incompatiblePlugin.load_status = 'failed'
    incompatiblePlugin.load_error =
      'manifest 校验失败: Host 版本不兼容: 版本 1.1.0 高于最大支持 1.0.99 (当前 Host: 1.1.0)'
    vi.mocked(pluginApi.getInstalledPlugins).mockResolvedValue([incompatiblePlugin] as never)
    vi.mocked(pluginApi.fetchPluginList).mockResolvedValue([
      {
        id: 'test.incompatible',
        manifest: {
          ...incompatiblePlugin.manifest,
          version: '1.4.0',
          repository_url: 'https://example.com/test.incompatible.git',
          host_application: { min_version: '1.1.0', max_version: '1.1.99' },
        },
      },
    ] as never)

    render(<PluginConfigPage />)

    expect(await screen.findByText('当前插件版本需要更新')).toBeInTheDocument()
    expect(screen.getByText('已安装 v1.3.2，插件市场已有 v1.4.0，请更新后重试。')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '立即更新' }))
    expect(screen.getByRole('heading', { name: '确认更新插件' })).toBeInTheDocument()
  })

  it('选中插件加载其 schema/config/raw 并进入编辑器', async () => {
    const user = userEvent.setup()
    render(<PluginConfigPage />)
    await user.click(await screen.findByRole('button', { name: /Emoji Plugin/ }))

    await waitFor(() => expect(pluginApi.getPluginConfigBundle).toHaveBeenCalledWith('test.emoji'))
    expect(await screen.findByRole('button', { name: /保存/ })).toBeInTheDocument()
  })

  it('编辑器内启停插件调用 togglePlugin', async () => {
    const user = userEvent.setup()
    render(<PluginConfigPage />)
    await user.click(await screen.findByRole('button', { name: /Emoji Plugin/ }))
    await user.click(await screen.findByRole('switch', { name: /禁用插件/ }))
    await waitFor(() => expect(pluginApi.togglePlugin).toHaveBeenCalledWith('test.emoji'))
  })

  it('源代码模式编辑后保存调用 updatePluginConfigRaw', async () => {
    const user = userEvent.setup()
    render(<PluginConfigPage />)
    await user.click(await screen.findByRole('button', { name: /Emoji Plugin/ }))
    // 切到源代码模式
    await user.click(await screen.findByRole('button', { name: /源代码/ }))
    const editor = await screen.findByTestId('code-editor')
    await user.clear(editor)
    await user.type(editor, 'key = "changed"')
    await user.click(screen.getByRole('button', { name: /保存/ }))
    await waitFor(() => expect(pluginApi.updatePluginConfigRaw).toHaveBeenCalled())
  })

  it('可视化模式下将 multiple=true 的 select 字段保存为字符串数组', async () => {
    const user = userEvent.setup()
    vi.mocked(pluginApi.getPluginConfigBundle).mockResolvedValue({
      schema: {
        plugin_info: { name: 'Emoji Plugin', version: '1.0.0', description: 'desc' },
        sections: {
          batch: {
            name: 'batch',
            title: '批量配置',
            collapsed: false,
            order: 0,
            fields: {
              push_format: {
                name: 'push_format',
                type: 'select',
                default: [],
                description: '推送格式',
                required: false,
                choices: ['image', 'text'],
                multiple: true,
                label: '推送格式',
                hidden: false,
                disabled: false,
                order: 0,
                ui_type: 'select',
              },
            },
          },
        },
        layout: { type: 'auto', tabs: [] },
      },
      config: { batch: { push_format: [] } },
      rawConfig: 'key = "value"\n',
    } as never)

    render(<PluginConfigPage />)
    await user.click(await screen.findByRole('button', { name: /Emoji Plugin/ }))

    await screen.findByText('推送格式')
    await user.click((await screen.findAllByRole('combobox'))[0])
    await user.click(await screen.findByText('image'))
    await user.click(await screen.findByText('text'))
    await user.click(screen.getByRole('button', { name: /保存/ }))

    await waitFor(() =>
      expect(pluginApi.updatePluginConfig).toHaveBeenCalledWith('test.emoji', {
        batch: { push_format: ['image', 'text'] },
      })
    )
  })

  it('可视化模式下将 disabled 的多选字段渲染为禁用态', async () => {
    vi.mocked(pluginApi.getPluginConfigBundle).mockResolvedValue({
      schema: {
        plugin_info: { name: 'Emoji Plugin', version: '1.0.0', description: 'desc' },
        sections: {
          batch: {
            name: 'batch',
            title: '批量配置',
            collapsed: false,
            order: 0,
            fields: {
              push_format: {
                name: 'push_format',
                type: 'select',
                default: ['image'],
                description: '推送格式',
                required: false,
                choices: ['image', 'text'],
                multiple: true,
                label: '推送格式',
                hidden: false,
                disabled: true,
                order: 0,
                ui_type: 'select',
              },
            },
          },
        },
        layout: { type: 'auto', tabs: [] },
      },
      config: { batch: { push_format: ['image'] } },
      rawConfig: 'key = "value"\n',
    } as never)

    render(<PluginConfigPage />)
    await userEvent.click(await screen.findByRole('button', { name: /Emoji Plugin/ }))

    await screen.findByText('推送格式')
    expect((await screen.findAllByRole('combobox'))[0]).toBeDisabled()
  })
})
