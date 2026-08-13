import type { ReactNode } from 'react'

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { PluginConfigPage } from '../plugin-config'
import * as chatApi from '@/lib/chat-management-api'
import * as pluginApi from '@/lib/plugin-api'
import type { ConfigFieldSchema, InstalledPlugin, PluginRuntimeComponent } from '@/lib/plugin-api'

// jsdom 未完整实现 Pointer Capture，文档浮窗拖拽会调用这些方法
Element.prototype.setPointerCapture = function setPointerCapture() {}
Element.prototype.releasePointerCapture = function releasePointerCapture() {}
Element.prototype.hasPointerCapture = function hasPointerCapture() {
  return true
}

const {
  toastMock,
  tFn,
  i18nState,
  themeState,
  blockerState,
  restartState,
  progressClient,
  isPluginCompatibleImpl,
} = vi.hoisted(() => {
  const tFn = (key: string) => key
  const i18nState = { resolvedLanguage: 'zh', language: 'zh' }
  const themeState = { dashboardStyle: 'modern' }
  const blockerState = {
    status: 'unblocked' as 'unblocked' | 'blocked',
    reset: vi.fn(),
    proceed: vi.fn(),
  }
  const restartState = {
    isRestarting: false,
    triggerRestart: vi.fn(),
  }
  const progressClient = {
    listener: null as null | ((progress: unknown) => void),
    cleanup: vi.fn(async () => undefined),
    subscribe: vi.fn(),
    emit(progress: unknown) {
      progressClient.listener?.(progress)
    },
  }
  progressClient.subscribe.mockImplementation(async (callback: (progress: unknown) => void) => {
    progressClient.listener = callback
    return progressClient.cleanup
  })
  const isPluginCompatibleImpl = (
    minVersion: string,
    maxVersion: string | undefined,
    currentVersion: { version: string }
  ) => {
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
  }
  return {
    toastMock: vi.fn(),
    tFn,
    i18nState,
    themeState,
    blockerState,
    restartState,
    progressClient,
    isPluginCompatibleImpl,
  }
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
  i18nState.resolvedLanguage = 'zh'
  i18nState.language = 'zh'
  themeState.dashboardStyle = 'modern'
  blockerState.status = 'unblocked'
  restartState.isRestarting = false
  progressClient.listener = null
  // openPluginConfig 会用 replaceState 写入 ?plugin=xxx，需重置避免深链接污染后续测试
  window.history.replaceState(null, '', '/plugin-config')
})

vi.mock('@/hooks/use-toast', () => ({ useToast: () => ({ toast: toastMock }) }))
vi.mock('@/lib/restart-context', () => ({
  RestartProvider: ({ children }: { children: ReactNode }) => <>{children}</>,
  useRestart: () => restartState,
}))
vi.mock('@/components/restart-overlay', () => ({ RestartOverlay: () => null }))
vi.mock('@/components/use-theme', () => ({
  useTheme: () => ({ themeConfig: themeState }),
}))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: tFn, i18n: i18nState }),
}))
vi.mock('@tanstack/react-router', () => ({
  useBlocker: () => blockerState,
}))
// 避免真实 WebSocket 连接（插件进度订阅）
vi.mock('@/lib/plugin-progress-client', () => ({
  pluginProgressClient: { subscribe: progressClient.subscribe },
}))
vi.mock('@/components/CodeEditor', () => ({
  CodeEditor: ({ value, onChange }: { value: string; onChange?: (v: string) => void }) => (
    <textarea data-testid="code-editor" value={value} onChange={(e) => onChange?.(e.target.value)} />
  ),
}))
vi.mock('@/components/ListFieldEditor', () => ({
  ListFieldEditor: ({
    itemFields,
    onChange,
    placeholder,
    value,
  }: {
    itemFields?: Record<string, { label?: string; placeholder?: string }>
    onChange?: (next: unknown[]) => void
    placeholder?: string
    value?: unknown[]
  }) => (
    <div data-placeholder={placeholder ?? ''} data-testid="list-field-editor">
      {itemFields ? <pre data-testid="list-item-fields">{JSON.stringify(itemFields)}</pre> : null}
      <button
        type="button"
        onClick={() => onChange?.([...(Array.isArray(value) ? value : []), 'new-item'])}
      >
        添加列表项
      </button>
    </div>
  ),
}))
vi.mock('@/components/markdown-renderer', () => ({
  MarkdownRenderer: ({ content }: { content: string }) => <div data-testid="markdown">{content}</div>,
}))
vi.mock('@/components/plugin-stats', () => ({
  PluginStats: ({ pluginId }: { pluginId: string }) => <div data-testid="plugin-stats">{pluginId}</div>,
}))
vi.mock('@/lib/chat-management-api', () => ({
  getAdapterHostPolicy: vi.fn(),
  updateAdapterHostPolicy: vi.fn(),
}))

vi.mock('@/lib/plugin-api', () => ({
  getInstalledPlugins: vi.fn(),
  fetchPluginList: vi.fn(),
  getMaimaiVersion: vi.fn(),
  isPluginCompatible: vi.fn(isPluginCompatibleImpl),
  getPluginConfigBundle: vi.fn(),
  updatePluginConfig: vi.fn(),
  updatePluginConfigRaw: vi.fn(),
  resetPluginConfig: vi.fn(),
  togglePlugin: vi.fn(),
  uninstallPlugin: vi.fn(),
  updatePlugin: vi.fn(),
  getPluginRuntimeComponents: vi.fn(),
  getLocalPluginReadme: vi.fn(),
  getLocalPluginChangelog: vi.fn(),
}))

function makePlugin(id: string, name: string): InstalledPlugin {
  return {
    id,
    path: `/plugins/${id}`,
    enabled: true,
    load_status: 'success',
    load_error: undefined,
    changelog: null,
    manifest: {
      manifest_version: 2,
      id,
      name,
      version: '1.0.0',
      description: 'desc',
      author: { name: 'tester' },
      license: 'MIT',
      host_application: {
        min_version: '1.0.0',
        max_version: undefined,
      },
    },
  }
}

function makeField(overrides: Partial<ConfigFieldSchema> & { name: string; ui_type: string }): ConfigFieldSchema {
  return {
    type: 'string',
    default: '',
    description: '',
    required: false,
    label: overrides.name,
    hidden: false,
    disabled: false,
    order: 0,
    ...overrides,
  }
}

function makeHostPolicyResponse(pluginId: string) {
  return {
    success: true,
    plugin_id: pluginId,
    global_defaults: { group: 'allow' as const, private: 'block' as const },
    policy: {
      group: { default_action: 'inherit' as const, allow_ids: [] as string[], deny_ids: [] as string[] },
      private: { default_action: 'inherit' as const, allow_ids: [] as string[], deny_ids: [] as string[] },
    },
  }
}

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <PluginConfigPage />
    </QueryClientProvider>
  )
}

async function openNamedPlugin(name: RegExp | string = /Emoji Plugin/) {
  const user = userEvent.setup()
  await user.click(await screen.findByRole('button', { name }))
  return user
}

beforeEach(() => {
  Object.defineProperty(Element.prototype, 'scrollIntoView', {
    configurable: true,
    value: vi.fn(),
  })
  progressClient.subscribe.mockImplementation(async (callback: (progress: unknown) => void) => {
    progressClient.listener = callback
    return progressClient.cleanup
  })
  blockerState.reset.mockImplementation(() => {
    blockerState.status = 'unblocked'
  })
  blockerState.proceed.mockImplementation(() => {
    blockerState.status = 'unblocked'
  })
  vi.mocked(pluginApi.isPluginCompatible).mockImplementation(isPluginCompatibleImpl)
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
      layout: { type: 'auto', tabs: [] },
    },
    config: {},
    rawConfig: 'key = "value"\n',
  } as never)
  vi.mocked(pluginApi.updatePluginConfigRaw).mockResolvedValue({ success: true, message: 'ok' } as never)
  vi.mocked(pluginApi.updatePluginConfig).mockResolvedValue({ success: true, message: 'ok' } as never)
  vi.mocked(pluginApi.togglePlugin).mockResolvedValue({ success: true, enabled: false, message: '已禁用插件' } as never)
  vi.mocked(pluginApi.resetPluginConfig).mockResolvedValue({ success: true } as never)
  vi.mocked(pluginApi.uninstallPlugin).mockResolvedValue({ success: true } as never)
  vi.mocked(pluginApi.updatePlugin).mockResolvedValue({ success: true } as never)
  vi.mocked(pluginApi.getPluginRuntimeComponents).mockResolvedValue([] as never)
  vi.mocked(pluginApi.getLocalPluginReadme).mockResolvedValue('')
  vi.mocked(pluginApi.getLocalPluginChangelog).mockResolvedValue('')
  vi.mocked(chatApi.getAdapterHostPolicy).mockResolvedValue(makeHostPolicyResponse('adapter.qq') as never)
  vi.mocked(chatApi.updateAdapterHostPolicy).mockResolvedValue(makeHostPolicyResponse('adapter.qq') as never)
})

describe('PluginConfigPage 特征化', () => {
  it('显示已装插件且不暴露 A_Memorix', async () => {
    renderPage()
    expect(await screen.findByText('Emoji Plugin')).toBeInTheDocument()
    expect(screen.queryByText(/A_Memorix/i)).not.toBeInTheDocument()
  })

  it('适配器管理页不显示插件搜索、更新、刷新和重启操作', async () => {
    window.history.replaceState(null, '', '/adapter-management')
    const adapterPlugin = {
      ...makePlugin('test.adapter', 'Adapter Plugin'),
      manifest: {
        ...makePlugin('test.adapter', 'Adapter Plugin').manifest,
        plugin_type: 'adapter',
      },
    }
    vi.mocked(pluginApi.getInstalledPlugins).mockResolvedValue([adapterPlugin] as never)

    render(<PluginConfigPage />)

    expect(await screen.findByText('Adapter Plugin')).toBeInTheDocument()
    expect(screen.queryByPlaceholderText('搜索插件...')).not.toBeInTheDocument()
    expect(screen.queryByText('有更新')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '刷新' })).not.toBeInTheDocument()
    expect(screen.queryByText('重启麦麦')).not.toBeInTheDocument()
    expect(pluginApi.fetchPluginList).not.toHaveBeenCalled()
    expect(pluginApi.getMaimaiVersion).not.toHaveBeenCalled()
  })

  it('删除按钮使用透明底色与主题色边框和图标', async () => {
    renderPage()

    const deleteButton = await screen.findByRole('button', { name: '删除' })
    expect(deleteButton).toHaveClass(
      'border-current',
      'bg-transparent',
      'text-primary',
      'shadow-none'
    )
    expect(deleteButton.querySelector('svg')).not.toHaveClass('text-primary')
  })

  it('插件卡片不显示重复的配置按钮，更新按钮保留原色并标记统一边框', async () => {
    const { container } = render(<PluginConfigPage />)

    await screen.findByText('Emoji Plugin')
    expect(screen.queryByRole('button', { name: '配置' })).not.toBeInTheDocument()

    const updateButton = container.querySelector('.lucide-arrow-up')?.closest('button')
    const deleteButton = screen.getByRole('button', { name: '删除' })
    expect(updateButton).toHaveAttribute('data-plugin-update-button', 'true')
    expect(updateButton).not.toHaveClass('text-primary', 'border-current')
    expect(deleteButton).toHaveClass(
      'border-current',
      'bg-transparent',
      'shadow-none'
    )
  })

  it('无插件时显示空态提示', async () => {
    vi.mocked(pluginApi.getInstalledPlugins).mockResolvedValue([] as never)
    renderPage()
    await waitFor(() => expect(screen.getByText('暂无已安装的插件')).toBeInTheDocument())
  })

  it('按照加载成功、加载中、加载失败的顺序分层展示插件', async () => {
    const failedPlugin = makePlugin('test.failed', 'Failed Plugin')
    failedPlugin.load_status = 'failed'
    const disabledPlugin = makePlugin('test.disabled', 'Disabled Plugin')
    disabledPlugin.enabled = false
    const loadingPlugin = makePlugin('test.loading', 'Loading Plugin')
    loadingPlugin.load_status = 'loading'
    const successPlugin = makePlugin('test.success', 'Success Plugin')
    vi.mocked(pluginApi.getInstalledPlugins).mockResolvedValue([
      failedPlugin,
      disabledPlugin,
      loadingPlugin,
      successPlugin,
    ] as never)

    const { container } = renderPage()

    await screen.findByText('Success Plugin')
    const pluginNames = Array.from(
      container.querySelectorAll<HTMLElement>('[data-plugin-list-item="true"] h3')
    ).map((element) => element.textContent)
    expect(pluginNames).toEqual([
      'Success Plugin',
      'Loading Plugin',
      'Failed Plugin',
      'Disabled Plugin',
    ])
    expect(screen.getByRole('heading', { level: 2, name: '加载成功' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 2, name: '加载中' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 2, name: '加载失败' })).toBeInTheDocument()
    const disabledListItem = screen.getByText('Disabled Plugin').closest('[data-plugin-list-item="true"]')
    expect(disabledListItem).not.toHaveTextContent('已禁用')
  })

  it('重复插件 ID 被隔离时展示冲突目录', async () => {
    const user = userEvent.setup()
    const duplicatePlugin = makePlugin('test.duplicate', 'Duplicate Plugin')
    duplicatePlugin.load_status = 'failed'
    duplicatePlugin.load_error =
      '插件 ID 重复，已阻止加载；冲突目录: C:\\plugins\\duplicate_a, C:\\plugins\\duplicate_b'
    vi.mocked(pluginApi.getInstalledPlugins).mockResolvedValue([duplicatePlugin] as never)

    renderPage()

    expect(await screen.findByText('插件加载失败')).toBeInTheDocument()
    expect(screen.getByText(`失败原因：${duplicatePlugin.load_error}`)).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '查看详情' }))
    expect(screen.getByText(duplicatePlugin.load_error)).toBeInTheDocument()
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

    renderPage()

    expect(await screen.findByText('当前插件版本已不兼容')).toBeInTheDocument()
    expect(screen.getByText('已安装 v1.3.2 与当前麦麦版本不兼容，请前往插件市场查看兼容版本。')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '前往插件市场' })).toHaveAttribute('href', '/plugins')
    expect(screen.queryByText(incompatiblePlugin.load_error)).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '查看详情' }))
    expect(screen.getByText(incompatiblePlugin.load_error)).toBeInTheDocument()
  })

  it('嵌入模式保持嵌入路由并跳转到嵌入式插件市场', async () => {
    const user = userEvent.setup()
    const incompatiblePlugin = makePlugin('test.embedded', 'Embedded Plugin')
    incompatiblePlugin.manifest.version = '1.3.2'
    incompatiblePlugin.manifest.host_application.max_version = '1.0.99'
    incompatiblePlugin.load_status = 'failed'
    incompatiblePlugin.load_error =
      'manifest 校验失败: Host 版本不兼容: 版本 1.1.0 高于最大支持 1.0.99 (当前 Host: 1.1.0)'
    vi.mocked(pluginApi.getInstalledPlugins).mockResolvedValue([incompatiblePlugin] as never)
    window.history.replaceState(null, '', '/plugin-config/embed')

    renderPage()

    expect(await screen.findByText('当前插件版本已不兼容')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '前往插件市场' })).toHaveAttribute(
      'href',
      '/plugins/embed'
    )

    await user.click(screen.getByRole('button', { name: /Embedded Plugin/ }))
    await waitFor(() => {
      expect(window.location.pathname).toBe('/plugin-config/embed')
      expect(window.location.search).toBe('?plugin=test.embedded')
    })
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

    renderPage()

    expect(await screen.findByText('当前插件版本需要更新')).toBeInTheDocument()
    expect(screen.getByText('已安装 v1.3.2，插件市场已有 v1.4.0，请更新后重试。')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '立即更新' }))
    expect(screen.getByRole('heading', { name: '确认更新插件' })).toBeInTheDocument()
  })

  it('选中插件加载其 schema/config/raw 并进入编辑器', async () => {
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: /Emoji Plugin/ }))

    await waitFor(() => expect(pluginApi.getPluginConfigBundle).toHaveBeenCalledWith('test.emoji'))
    expect(await screen.findByRole('button', { name: /保存/ })).toBeInTheDocument()
  })

  it('编辑器内启停插件调用 togglePlugin', async () => {
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: /Emoji Plugin/ }))
    await user.click(await screen.findByRole('switch', { name: /禁用插件/ }))
    await waitFor(() => expect(pluginApi.togglePlugin).toHaveBeenCalledWith('test.emoji'))
  })

  it('源代码模式编辑后保存调用 updatePluginConfigRaw', async () => {
    const user = userEvent.setup()
    renderPage()
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

    renderPage()
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

    renderPage()
    await userEvent.click(await screen.findByRole('button', { name: /Emoji Plugin/ }))

    await screen.findByText('推送格式')
    expect((await screen.findAllByRole('combobox'))[0]).toBeDisabled()
  })
})

describe('PluginConfigPage 空列表', () => {
  it('搜索无匹配时提示更换关键词', async () => {
    const user = userEvent.setup()
    renderPage()
    await screen.findByText('Emoji Plugin')
    await user.type(screen.getByPlaceholderText('搜索插件...'), 'zzz-not-found')
    expect(await screen.findByText('没有找到匹配的插件')).toBeInTheDocument()
    expect(screen.getByText('尝试其他搜索关键词')).toBeInTheDocument()
  })

  it('仅看有更新且没有新版本时显示空态', async () => {
    const user = userEvent.setup()
    renderPage()
    await screen.findByText('Emoji Plugin')
    await waitFor(() =>
      expect(screen.getByRole('button', { name: '插件市场中没有找到该插件，无法判断新版本' })).toBeDisabled()
    )
    await user.click(screen.getByLabelText('有更新'))
    expect(await screen.findByText('暂无可更新插件')).toBeInTheDocument()
    expect(screen.getByText('当前已安装插件没有发现新版本')).toBeInTheDocument()
  })

  it('适配器管理路径在没有适配器时显示空列表', async () => {
    window.history.replaceState(null, '', '/adapter-management')
    renderPage()
    expect(await screen.findByText('暂无已安装的插件')).toBeInTheDocument()
    expect(screen.getByText('前往插件市场安装插件')).toBeInTheDocument()
    expect(screen.queryByText('Emoji Plugin')).not.toBeInTheDocument()
  })
})

describe('PluginConfigPage 主程序放行规则', () => {
  function makeAdapter() {
    const plugin = makePlugin('adapter.qq', 'QQ Adapter')
    plugin.manifest.plugin_type = 'adapter'
    return plugin
  }

  beforeEach(() => {
    window.history.replaceState(null, '', '/adapter-management')
    vi.mocked(pluginApi.getInstalledPlugins).mockResolvedValue([
      makeAdapter(),
      makePlugin('test.emoji', 'Emoji Plugin'),
    ] as never)
  })

  it('适配器管理页只列出适配器并展示主程序放行规则页签', async () => {
    const user = userEvent.setup()
    renderPage()

    expect(await screen.findByText('QQ Adapter')).toBeInTheDocument()
    expect(screen.queryByText('Emoji Plugin')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /QQ Adapter/ }))
    expect(await screen.findByRole('tab', { name: '主程序放行规则' })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: '设置' })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: '详情' })).toBeInTheDocument()
  })

  it('普通插件配置页不展示主程序放行规则页签', async () => {
    window.history.replaceState(null, '', '/plugin-config')
    vi.mocked(pluginApi.getInstalledPlugins).mockResolvedValue([makeAdapter()] as never)
    renderPage()
    await openNamedPlugin(/QQ Adapter/)
    await screen.findByRole('button', { name: /保存/ })
    expect(screen.queryByRole('tab', { name: '主程序放行规则' })).not.toBeInTheDocument()
  })

  it('深链接非适配器在适配器管理页不会打开编辑器', async () => {
    window.history.replaceState(null, '', '/adapter-management?plugin=test.emoji')
    renderPage()
    expect(await screen.findByText('QQ Adapter')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /保存/ })).not.toBeInTheDocument()
    expect(window.location.pathname).toBe('/adapter-management')
  })

  it('加载主程序规则失败时展示错误', async () => {
    const user = userEvent.setup()
    vi.mocked(chatApi.getAdapterHostPolicy).mockRejectedValue(new Error('策略服务不可用'))
    renderPage()
    await user.click(await screen.findByRole('button', { name: /QQ Adapter/ }))
    await user.click(await screen.findByRole('tab', { name: '主程序放行规则' }))
    expect(await screen.findByText('策略服务不可用')).toBeInTheDocument()
  })

  it('加载主程序规则非 Error 时使用回退文案', async () => {
    const user = userEvent.setup()
    vi.mocked(chatApi.getAdapterHostPolicy).mockRejectedValue('offline')
    renderPage()
    await user.click(await screen.findByRole('button', { name: /QQ Adapter/ }))
    await user.click(await screen.findByRole('tab', { name: '主程序放行规则' }))
    expect(await screen.findByText('主程序规则加载失败')).toBeInTheDocument()
  })

  it('主程序规则加载中展示转圈提示', async () => {
    const user = userEvent.setup()
    vi.mocked(chatApi.getAdapterHostPolicy).mockReturnValue(new Promise(() => {}))
    renderPage()
    await user.click(await screen.findByRole('button', { name: /QQ Adapter/ }))
    await user.click(await screen.findByRole('tab', { name: '主程序放行规则' }))
    expect(await screen.findByText('正在加载主程序规则')).toBeInTheDocument()
  })

  it('修改并保存主程序放行规则', async () => {
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: /QQ Adapter/ }))
    await user.click(await screen.findByRole('tab', { name: '主程序放行规则' }))

    expect(await screen.findByText('这是 MaiBot 主程序侧规则，与适配器自身名单相互独立。')).toBeInTheDocument()
    expect(screen.getByText('全局默认：放行')).toBeInTheDocument()
    expect(screen.getByText('全局默认：拒绝')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /保存主程序规则/ })).toBeDisabled()
    expect(screen.queryByRole('button', { name: /^保存$/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /源代码/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /重置/ })).not.toBeInTheDocument()

    await user.click(screen.getAllByRole('combobox')[0])
    await user.click(await screen.findByText('此适配器默认放行'))
    await user.click(screen.getAllByRole('button', { name: '添加列表项' })[0])
    await user.click(screen.getByRole('button', { name: /保存主程序规则/ }))

    await waitFor(() =>
      expect(chatApi.updateAdapterHostPolicy).toHaveBeenCalledWith('adapter.qq', {
        group: { default_action: 'allow', allow_ids: ['new-item'], deny_ids: [] },
        private: { default_action: 'inherit', allow_ids: [], deny_ids: [] },
      })
    )
    expect(toastMock).toHaveBeenCalledWith(expect.objectContaining({ title: '主程序放行规则已保存' }))
  })

  it('保存主程序放行规则失败时弹出错误 toast', async () => {
    const user = userEvent.setup()
    vi.mocked(chatApi.updateAdapterHostPolicy).mockRejectedValue(new Error('写入失败'))
    renderPage()
    await user.click(await screen.findByRole('button', { name: /QQ Adapter/ }))
    await user.click(await screen.findByRole('tab', { name: '主程序放行规则' }))
    await user.click(await screen.findByText('群聊规则'))
    await user.click(screen.getAllByRole('combobox')[0])
    await user.click(await screen.findByText('此适配器默认拒绝'))
    await user.click(screen.getByRole('button', { name: /保存主程序规则/ }))

    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({
          title: '主程序放行规则保存失败',
          description: '写入失败',
        })
      )
    )
  })

  it('编辑器返回时保持适配器管理路径', async () => {
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: /QQ Adapter/ }))
    await screen.findByRole('button', { name: /保存/ })
    await user.click(screen.getAllByRole('button')[0])
    expect(await screen.findByText('QQ Adapter')).toBeInTheDocument()
    expect(window.location.pathname).toBe('/adapter-management')
    expect(window.location.search).toBe('')
  })
})

describe('PluginConfigPage 嵌入与完整路径', () => {
  it('深链接在嵌入路径打开编辑器，返回后仍停留在 embed', async () => {
    window.history.replaceState(null, '', '/plugin-config/embed?plugin=test.emoji')
    renderPage()
    expect(await screen.findByRole('button', { name: /保存/ })).toBeInTheDocument()
    expect(pluginApi.getPluginConfigBundle).toHaveBeenCalledWith('test.emoji')
    expect(window.location.pathname).toBe('/plugin-config/embed')

    await userEvent.click(screen.getAllByRole('button')[0])
    expect(await screen.findByText('Emoji Plugin')).toBeInTheDocument()
    expect(window.location.pathname).toBe('/plugin-config/embed')
    expect(window.location.search).toBe('')
  })

  it('完整页返回列表后路径为 /plugin-config', async () => {
    renderPage()
    const user = await openNamedPlugin()
    await screen.findByRole('button', { name: /保存/ })
    await user.click(screen.getAllByRole('button')[0])
    expect(await screen.findByText('Emoji Plugin')).toBeInTheDocument()
    expect(window.location.pathname).toBe('/plugin-config')
  })

  it('完整页不兼容插件的市场入口指向 /plugins', async () => {
    const incompatiblePlugin = makePlugin('test.incompatible', 'Incompatible Plugin')
    incompatiblePlugin.manifest.version = '1.3.2'
    incompatiblePlugin.manifest.host_application.max_version = '1.0.99'
    incompatiblePlugin.load_status = 'failed'
    incompatiblePlugin.load_error =
      'manifest 校验失败: Host 版本不兼容: 版本 1.1.0 高于最大支持 1.0.99 (当前 Host: 1.1.0)'
    vi.mocked(pluginApi.getInstalledPlugins).mockResolvedValue([incompatiblePlugin] as never)

    renderPage()
    expect(await screen.findByRole('link', { name: '前往插件市场' })).toHaveAttribute('href', '/plugins')
  })

  it('键盘 Enter / Space 打开插件配置', async () => {
    renderPage()
    const row = await screen.findByRole('button', { name: /Emoji Plugin/ })
    fireEvent.keyDown(row, { key: 'Enter' })
    expect(await screen.findByRole('button', { name: /保存/ })).toBeInTheDocument()
  })

  it('空格键打开插件配置', async () => {
    renderPage()
    const row = await screen.findByRole('button', { name: /Emoji Plugin/ })
    fireEvent.keyDown(row, { key: ' ' })
    expect(await screen.findByRole('button', { name: /保存/ })).toBeInTheDocument()
  })
})

describe('PluginConfigPage 保存与错误', () => {
  it('加载配置失败时展示无法加载配置并可以返回', async () => {
    vi.mocked(pluginApi.getPluginConfigBundle).mockRejectedValue(new Error('bundle 丢失'))
    renderPage()
    await openNamedPlugin()
    expect(await screen.findByText('无法加载配置')).toBeInTheDocument()
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({ title: '加载配置失败', description: 'bundle 丢失' })
    )
    await userEvent.click(screen.getByRole('button', { name: /返回/ }))
    expect(await screen.findByText('Emoji Plugin')).toBeInTheDocument()
  })

  it('加载配置失败非 Error 时使用未知错误', async () => {
    vi.mocked(pluginApi.getPluginConfigBundle).mockRejectedValue('broken')
    renderPage()
    await openNamedPlugin()
    expect(await screen.findByText('无法加载配置')).toBeInTheDocument()
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({ title: '加载配置失败', description: '未知错误' })
    )
  })

  it('可视化保存失败时弹出保存失败 toast', async () => {
    vi.mocked(pluginApi.getPluginConfigBundle).mockResolvedValue({
      schema: {
        plugin_info: { name: 'Emoji Plugin', version: '1.0.0', description: 'desc' },
        sections: {
          general: {
            name: 'general',
            title: '通用',
            collapsed: false,
            order: 0,
            fields: {
              title: makeField({ name: 'title', ui_type: 'text', label: '标题', default: 'old' }),
            },
          },
        },
        layout: { type: 'auto', tabs: [] },
      },
      config: { general: { title: 'old' } },
      rawConfig: 'title = "old"\n',
    } as never)
    vi.mocked(pluginApi.updatePluginConfig).mockRejectedValue(new Error('磁盘满'))

    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: /Emoji Plugin/ }))
    const input = await screen.findByDisplayValue('old')
    fireEvent.change(input, { target: { value: 'new-title' } })
    expect(await screen.findByText('有未保存的更改')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /保存/ }))
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({ title: '保存失败', description: '磁盘满' })
      )
    )
  })

  it('可视化保存失败非 Error 时使用未知错误', async () => {
    vi.mocked(pluginApi.getPluginConfigBundle).mockResolvedValue({
      schema: {
        plugin_info: { name: 'Emoji Plugin', version: '1.0.0', description: 'desc' },
        sections: {
          general: {
            name: 'general',
            title: '通用',
            collapsed: false,
            order: 0,
            fields: {
              title: makeField({ name: 'title', ui_type: 'text', label: '标题', default: 'old' }),
            },
          },
        },
        layout: { type: 'auto', tabs: [] },
      },
      config: { general: { title: 'old' } },
      rawConfig: 'title = "old"\n',
    } as never)
    vi.mocked(pluginApi.updatePluginConfig).mockRejectedValue('nope')

    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: /Emoji Plugin/ }))
    fireEvent.change(await screen.findByDisplayValue('old'), { target: { value: 'x' } })
    await user.click(screen.getByRole('button', { name: /保存/ }))
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({ title: '保存失败', description: '未知错误' })
      )
    )
  })

  it('源代码 TOML 解析失败标记错误，再次编辑后清除', async () => {
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: /Emoji Plugin/ }))
    await user.click(await screen.findByRole('button', { name: /源代码/ }))
    const editor = await screen.findByTestId('code-editor')
    fireEvent.change(editor, { target: { value: '[[[invalid' } })
    await user.click(screen.getByRole('button', { name: /保存/ }))
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith(expect.objectContaining({ title: 'TOML 格式错误' }))
    )
    expect(screen.getByText(/上次保存失败，请检查 TOML 格式/)).toBeInTheDocument()
    expect(pluginApi.updatePluginConfigRaw).not.toHaveBeenCalled()

    fireEvent.change(editor, { target: { value: 'key = "ok"' } })
    expect(screen.queryByText(/上次保存失败，请检查 TOML 格式/)).not.toBeInTheDocument()
  })

  it('源代码保存接口失败时弹出保存失败', async () => {
    vi.mocked(pluginApi.updatePluginConfigRaw).mockRejectedValue(new Error('raw 写入失败'))
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: /Emoji Plugin/ }))
    await user.click(await screen.findByRole('button', { name: /源代码/ }))
    fireEvent.change(await screen.findByTestId('code-editor'), { target: { value: 'key = "ok"' } })
    await user.click(screen.getByRole('button', { name: /保存/ }))
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({ title: '保存失败', description: 'raw 写入失败' })
      )
    )
  })

  it('重置配置成功后重新加载并关闭对话框', async () => {
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: /Emoji Plugin/ }))
    await user.click(await screen.findByRole('button', { name: /重置/ }))
    expect(screen.getByRole('heading', { name: '确认重置配置' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '取消' }))
    expect(screen.queryByRole('heading', { name: '确认重置配置' })).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /重置/ }))
    await user.click(screen.getByRole('button', { name: '确认重置' }))
    await waitFor(() => expect(pluginApi.resetPluginConfig).toHaveBeenCalledWith('test.emoji'))
    expect(toastMock).toHaveBeenCalledWith(expect.objectContaining({ title: '配置已重置' }))
    expect(pluginApi.getPluginConfigBundle).toHaveBeenCalledTimes(2)
  })

  it('重置配置失败时弹出重置失败', async () => {
    vi.mocked(pluginApi.resetPluginConfig).mockRejectedValue(new Error('reset boom'))
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: /Emoji Plugin/ }))
    await user.click(await screen.findByRole('button', { name: /重置/ }))
    await user.click(screen.getByRole('button', { name: '确认重置' }))
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({ title: '重置失败', description: 'reset boom' })
      )
    )
  })

  it('重置失败非 Error 时使用未知错误', async () => {
    vi.mocked(pluginApi.resetPluginConfig).mockRejectedValue(0)
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: /Emoji Plugin/ }))
    await user.click(await screen.findByRole('button', { name: /重置/ }))
    await user.click(screen.getByRole('button', { name: '确认重置' }))
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({ title: '重置失败', description: '未知错误' })
      )
    )
  })

  it('编辑器内切换状态失败时弹出 toast', async () => {
    vi.mocked(pluginApi.togglePlugin).mockRejectedValue(new Error('toggle down'))
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: /Emoji Plugin/ }))
    await user.click(await screen.findByRole('switch', { name: /禁用插件/ }))
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({ title: '切换状态失败', description: 'toggle down' })
      )
    )
  })

  it('有未保存更改时返回可取消、不保存或保存并离开', async () => {
    vi.mocked(pluginApi.getPluginConfigBundle).mockResolvedValue({
      schema: {
        plugin_info: { name: 'Emoji Plugin', version: '1.0.0', description: 'desc' },
        sections: {
          general: {
            name: 'general',
            title: '通用',
            collapsed: false,
            order: 0,
            fields: {
              title: makeField({ name: 'title', ui_type: 'text', label: '标题', default: 'old' }),
            },
          },
        },
        layout: { type: 'auto', tabs: [] },
      },
      config: { general: { title: 'old' } },
      rawConfig: 'title = "old"\n',
    } as never)

    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: /Emoji Plugin/ }))
    fireEvent.change(await screen.findByDisplayValue('old'), { target: { value: 'draft' } })

    await user.click(screen.getAllByRole('button')[0])
    expect(await screen.findByRole('heading', { name: '有未保存的更改' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '取消' }))
    expect(screen.queryByRole('heading', { name: '有未保存的更改' })).not.toBeInTheDocument()
    expect(screen.getByDisplayValue('draft')).toBeInTheDocument()

    await user.click(screen.getAllByRole('button')[0])
    await user.click(await screen.findByRole('button', { name: '不保存' }))
    expect(await screen.findByText('Emoji Plugin')).toBeInTheDocument()
  })

  it('保存并离开会先保存再返回列表', async () => {
    vi.mocked(pluginApi.getPluginConfigBundle).mockResolvedValue({
      schema: {
        plugin_info: { name: 'Emoji Plugin', version: '1.0.0', description: 'desc' },
        sections: {
          general: {
            name: 'general',
            title: '通用',
            collapsed: false,
            order: 0,
            fields: {
              title: makeField({ name: 'title', ui_type: 'text', label: '标题', default: 'old' }),
            },
          },
        },
        layout: { type: 'auto', tabs: [] },
      },
      config: { general: { title: 'old' } },
      rawConfig: 'title = "old"\n',
    } as never)

    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: /Emoji Plugin/ }))
    fireEvent.change(await screen.findByDisplayValue('old'), { target: { value: 'draft' } })
    await user.click(screen.getAllByRole('button')[0])
    await user.click(await screen.findByRole('button', { name: /保存并离开/ }))
    await waitFor(() =>
      expect(pluginApi.updatePluginConfig).toHaveBeenCalledWith('test.emoji', { general: { title: 'draft' } })
    )
    expect(await screen.findByText('Emoji Plugin')).toBeInTheDocument()
  })

  it('保存并离开时保存失败则留在编辑器', async () => {
    vi.mocked(pluginApi.getPluginConfigBundle).mockResolvedValue({
      schema: {
        plugin_info: { name: 'Emoji Plugin', version: '1.0.0', description: 'desc' },
        sections: {
          general: {
            name: 'general',
            title: '通用',
            collapsed: false,
            order: 0,
            fields: {
              title: makeField({ name: 'title', ui_type: 'text', label: '标题', default: 'old' }),
            },
          },
        },
        layout: { type: 'auto', tabs: [] },
      },
      config: { general: { title: 'old' } },
      rawConfig: 'title = "old"\n',
    } as never)
    vi.mocked(pluginApi.updatePluginConfig).mockRejectedValue(new Error('仍失败'))

    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: /Emoji Plugin/ }))
    fireEvent.change(await screen.findByDisplayValue('old'), { target: { value: 'draft' } })
    await user.click(screen.getAllByRole('button')[0])
    await user.click(await screen.findByRole('button', { name: /保存并离开/ }))
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith(expect.objectContaining({ title: '保存失败' }))
    )
    expect(screen.getByRole('heading', { name: '有未保存的更改' })).toBeInTheDocument()
    expect(screen.getByDisplayValue('draft')).toBeInTheDocument()
  })

  it('路由拦截时取消调用 reset、不保存调用 proceed', async () => {
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: /Emoji Plugin/ }))
    await screen.findByRole('button', { name: /保存/ })

    blockerState.status = 'blocked'
    await user.click(screen.getByRole('button', { name: /源代码/ }))
    expect(await screen.findByRole('heading', { name: '有未保存的更改' })).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '取消' }))
    expect(blockerState.reset).toHaveBeenCalled()

    await user.click(screen.getByRole('button', { name: '不保存' }))
    expect(blockerState.proceed).toHaveBeenCalled()
  })

  it('路由拦截下保存并离开成功后 proceed', async () => {
    vi.mocked(pluginApi.getPluginConfigBundle).mockResolvedValue({
      schema: {
        plugin_info: { name: 'Emoji Plugin', version: '1.0.0', description: 'desc' },
        sections: {
          general: {
            name: 'general',
            title: '通用',
            collapsed: false,
            order: 0,
            fields: {
              title: makeField({ name: 'title', ui_type: 'text', label: '标题', default: 'old' }),
            },
          },
        },
        layout: { type: 'auto', tabs: [] },
      },
      config: { general: { title: 'old' } },
      rawConfig: 'title = "old"\n',
    } as never)

    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: /Emoji Plugin/ }))
    fireEvent.change(await screen.findByDisplayValue('old'), { target: { value: 'draft' } })
    blockerState.status = 'blocked'
    await user.click(screen.getByRole('button', { name: /源代码/ }))
    await user.click(await screen.findByRole('button', { name: /保存并离开/ }))
    await waitFor(() => expect(blockerState.proceed).toHaveBeenCalled())
  })
})

describe('PluginConfigPage 编辑器字段与页签', () => {
  it('可视化渲染各 ui_type 并保存变更', async () => {
    vi.mocked(pluginApi.getPluginConfigBundle).mockResolvedValue({
      schema: {
        plugin_info: {
          name: 'Emoji Plugin',
          version: '1.0.0',
          description: 'desc',
          i18n: { zh: { name: '表情插件' } },
        },
        sections: {
          general: {
            name: 'general',
            title: 'Fallback',
            description: 'desc-fallback',
            i18n: { zh: { title: '通用中文', description: '节描述' } },
            collapsed: false,
            order: 0,
            fields: {
              enabled: makeField({
                name: 'enabled',
                ui_type: 'switch',
                type: 'boolean',
                label: '启用开关',
                default: false,
                hint: '开关提示',
                order: 0,
              }),
              count: makeField({
                name: 'count',
                ui_type: 'number',
                type: 'number',
                label: '数量',
                default: 1,
                min: 0,
                max: 10,
                hint: '数字提示',
                order: 1,
              }),
              volume: makeField({
                name: 'volume',
                ui_type: 'slider',
                type: 'number',
                label: '音量',
                default: 50,
                min: 0,
                max: 100,
                hint: '滑动提示',
                order: 2,
              }),
              mode: makeField({
                name: 'mode',
                ui_type: 'select',
                label: '模式',
                choices: ['alpha', 'beta'],
                default: 'alpha',
                hint: '选择提示',
                order: 3,
              }),
              note: makeField({
                name: 'note',
                ui_type: 'textarea',
                label: '备注',
                default: 'hello',
                rows: 4,
                hint: '多行提示',
                order: 4,
              }),
              secret: makeField({
                name: 'secret',
                ui_type: 'password',
                label: '密钥',
                placeholder: '输入密钥',
                hint: '密码提示',
                order: 5,
              }),
              tags: makeField({
                name: 'tags',
                ui_type: 'list',
                label: '标签',
                item_type: 'string',
                hint: '列表提示',
                item_fields: {
                  name: {
                    type: 'string',
                    label: 'Name',
                    placeholder: '',
                    i18n: { zh: { label: '项名', placeholder: '项占位' } },
                  },
                },
                order: 6,
              }),
              title: makeField({
                name: 'title',
                ui_type: 'text',
                label: '标题',
                default: 'init',
                hint: '文本提示',
                max_length: 20,
                order: 7,
              }),
              alias: makeField({
                name: 'alias',
                ui_type: 'text',
                label: { zh_CN: '别名', en: 'Alias' } as unknown as string,
                order: 8,
              }),
              localized: makeField({
                name: 'localized',
                ui_type: 'text',
                label: 'fallback-label',
                hint: 'hint-fallback',
                i18n: { zh_CN: { label: '国际化标签', hint: '国际化提示' } },
                order: 9,
              }),
              empty_label: makeField({
                name: 'empty_label',
                ui_type: 'text',
                label: '',
                order: 10,
              }),
              hidden_field: makeField({
                name: 'hidden_field',
                ui_type: 'text',
                label: '隐藏字段',
                hidden: true,
                order: 11,
              }),
              disabled_text: makeField({
                name: 'disabled_text',
                ui_type: 'text',
                label: '禁用文本',
                disabled: true,
                default: 'locked',
                order: 12,
              }),
            },
          },
          more: {
            name: 'more',
            title: '更多',
            collapsed: true,
            order: 1,
            fields: {
              extra: makeField({ name: 'extra', ui_type: 'text', label: '额外字段' }),
            },
          },
        },
        layout: { type: 'auto', tabs: [] },
      },
      config: {
        general: {
          enabled: false,
          count: 2,
          volume: 30,
          mode: 'alpha',
          note: 'hello',
          secret: 's3',
          tags: ['old'],
          title: 'init',
          alias: '',
          localized: '',
          empty_label: '',
          disabled_text: 'locked',
        },
        more: { extra: '' },
      },
      rawConfig: 'key = "value"\n',
    } as never)

    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: /Emoji Plugin/ }))

    expect(await screen.findByRole('heading', { name: '表情插件' })).toBeInTheDocument()
    expect(screen.getByText('通用中文')).toBeInTheDocument()
    expect(screen.getByText('节描述')).toBeInTheDocument()
    expect(screen.getByText('开关提示')).toBeInTheDocument()
    expect(screen.getByText('国际化标签')).toBeInTheDocument()
    expect(screen.getByText('国际化提示')).toBeInTheDocument()
    expect(screen.getByText('别名')).toBeInTheDocument()
    expect(screen.getByText('empty_label')).toBeInTheDocument()
    expect(screen.queryByText('隐藏字段')).not.toBeInTheDocument()
    expect(screen.getByDisplayValue('locked')).toBeDisabled()
    expect(JSON.parse(screen.getByTestId('list-item-fields').textContent || '{}').name.label).toBe('项名')

    const fieldSwitch = screen.getAllByRole('switch').find((node) => !node.getAttribute('aria-label'))
    await user.click(fieldSwitch!)
    fireEvent.change(screen.getByDisplayValue('2'), { target: { value: '8' } })
    fireEvent.keyDown(screen.getByRole('slider'), { key: 'ArrowRight' })
    await user.click(screen.getAllByRole('combobox')[0])
    await user.click(await screen.findByText('beta'))
    fireEvent.change(screen.getByDisplayValue('hello'), { target: { value: 'world' } })
    const password = screen.getByPlaceholderText('输入密钥')
    expect(password).toHaveAttribute('type', 'password')
    await user.click(password.parentElement!.querySelector('button')!)
    expect(password).toHaveAttribute('type', 'text')
    fireEvent.change(password, { target: { value: 'secret2' } })
    await user.click(screen.getByRole('button', { name: '添加列表项' }))
    fireEvent.change(screen.getByDisplayValue('init'), { target: { value: 'renamed' } })
    await user.click(screen.getByText('更多'))
    expect(await screen.findByText('额外字段')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /保存/ }))
    await waitFor(() => expect(pluginApi.updatePluginConfig).toHaveBeenCalled())
    const saved = vi.mocked(pluginApi.updatePluginConfig).mock.calls[0][1] as {
      general: Record<string, unknown>
    }
    expect(saved.general.enabled).toBe(true)
    expect(saved.general.count).toBe(8)
    expect(saved.general.mode).toBe('beta')
    expect(saved.general.note).toBe('world')
    expect(saved.general.secret).toBe('secret2')
    expect(saved.general.tags).toEqual(['old', 'new-item'])
    expect(saved.general.title).toBe('renamed')
    expect(toastMock).toHaveBeenCalledWith(expect.objectContaining({ title: '配置已保存' }))
  })

  it('en/ja/ko locale 回退到对应 i18n 键', async () => {
    vi.mocked(pluginApi.getPluginConfigBundle).mockResolvedValue({
      schema: {
        plugin_info: { name: 'Emoji Plugin', version: '1.0.0', description: 'desc' },
        sections: {
          general: {
            name: 'general',
            title: '通用',
            collapsed: false,
            order: 0,
            fields: {
              en_field: makeField({
                name: 'en_field',
                ui_type: 'text',
                label: 'EN fallback',
                i18n: { en: { label: 'English Label' }, ja_JP: { label: '日本語ラベル' }, ko_KR: { label: '한국어라벨' } },
              }),
            },
          },
        },
        layout: { type: 'auto', tabs: [] },
      },
      config: { general: { en_field: '' } },
      rawConfig: '',
    } as never)

    i18nState.resolvedLanguage = 'en-US'
    i18nState.language = 'en-US'
    window.history.replaceState(null, '', '/plugin-config?plugin=test.emoji')
    renderPage()
    expect(await screen.findByText('English Label')).toBeInTheDocument()
    cleanup()

    i18nState.resolvedLanguage = 'ja'
    i18nState.language = 'ja'
    window.history.replaceState(null, '', '/plugin-config?plugin=test.emoji')
    renderPage()
    expect(await screen.findByText('日本語ラベル')).toBeInTheDocument()
    cleanup()

    i18nState.resolvedLanguage = 'ko'
    i18nState.language = 'ko'
    window.history.replaceState(null, '', '/plugin-config?plugin=test.emoji')
    renderPage()
    expect(await screen.findByText('한국어라벨')).toBeInTheDocument()
  })

  it('tabs 布局展示 badge，切换时写入 URL，缺失 section 被跳过', async () => {
    vi.mocked(pluginApi.getPluginConfigBundle).mockResolvedValue({
      schema: {
        plugin_info: { name: 'Emoji Plugin', version: '1.0.0', description: 'desc' },
        sections: {
          general: {
            name: 'general',
            title: '通用',
            collapsed: false,
            order: 0,
            fields: {
              title: makeField({ name: 'title', ui_type: 'text', label: '标题', default: 'a' }),
            },
          },
          more: {
            name: 'more',
            title: '更多节',
            collapsed: false,
            order: 1,
            fields: {
              extra: makeField({ name: 'extra', ui_type: 'text', label: '额外字段', default: 'b' }),
            },
          },
        },
        layout: {
          type: 'tabs',
          tabs: [
            { id: 'basic', title: '基础', sections: ['general', 'missing'], order: 0, badge: 'NEW', i18n: { zh: { title: '基础页' } } },
            { id: 'advanced', title: '高级', sections: ['more'], order: 1 },
          ],
        },
      },
      config: { general: { title: 'a' }, more: { extra: 'b' } },
      rawConfig: '',
    } as never)

    window.history.replaceState(null, '', '/plugin-config?plugin=test.emoji&tab=unknown')
    const user = userEvent.setup()
    renderPage()
    expect(await screen.findByRole('tab', { name: /基础页/ })).toBeInTheDocument()
    expect(screen.getByText('NEW')).toBeInTheDocument()
    expect(screen.getByText('标题')).toBeInTheDocument()

    await user.click(screen.getByRole('tab', { name: '高级' }))
    expect(await screen.findByText('额外字段')).toBeInTheDocument()
    expect(window.location.search).toBe('?plugin=test.emoji&tab=advanced')
  })

  it('深链接 tab 选中对应配置页', async () => {
    vi.mocked(pluginApi.getPluginConfigBundle).mockResolvedValue({
      schema: {
        plugin_info: { name: 'Emoji Plugin', version: '1.0.0', description: 'desc' },
        sections: {
          general: {
            name: 'general',
            title: '通用',
            collapsed: false,
            order: 0,
            fields: { title: makeField({ name: 'title', ui_type: 'text', label: '基础标题' }) },
          },
          more: {
            name: 'more',
            title: '更多节',
            collapsed: false,
            order: 1,
            fields: { extra: makeField({ name: 'extra', ui_type: 'text', label: '高级字段' }) },
          },
        },
        layout: {
          type: 'tabs',
          tabs: [
            { id: 'basic', title: '基础', sections: ['general'], order: 0 },
            { id: 'advanced', title: '高级', sections: ['more'], order: 1 },
          ],
        },
      },
      config: { general: { title: '' }, more: { extra: '' } },
      rawConfig: '',
    } as never)
    window.history.replaceState(null, '', '/plugin-config?plugin=test.emoji&tab=advanced')
    renderPage()
    expect(await screen.findByText('高级字段')).toBeInTheDocument()
  })

  it('禁用插件在编辑器显示已禁用', async () => {
    vi.mocked(pluginApi.getPluginConfigBundle).mockResolvedValue({
      schema: {
        plugin_info: { name: 'Emoji Plugin', version: '1.0.0', description: 'desc' },
        sections: {},
        layout: { type: 'auto', tabs: [] },
      },
      config: { plugin: { enabled: false } },
      rawConfig: '',
    } as never)
    renderPage()
    await openNamedPlugin()
    expect(await screen.findByText('已禁用')).toBeInTheDocument()
    expect(screen.getByRole('switch', { name: '启用插件' })).toBeInTheDocument()
  })

  it('multiple select 在值为非数组时回退 default', async () => {
    vi.mocked(pluginApi.getPluginConfigBundle).mockResolvedValue({
      schema: {
        plugin_info: { name: 'Emoji Plugin', version: '1.0.0', description: 'desc' },
        sections: {
          batch: {
            name: 'batch',
            title: '批量',
            collapsed: false,
            order: 0,
            fields: {
              formats: makeField({
                name: 'formats',
                ui_type: 'select',
                multiple: true,
                choices: ['image', 'text'],
                default: ['text'],
                label: '格式',
              }),
              empty_multi: makeField({
                name: 'empty_multi',
                ui_type: 'select',
                multiple: true,
                choices: ['a'],
                label: '空多选',
              }),
            },
          },
        },
        layout: { type: 'auto', tabs: [] },
      },
      config: { batch: { formats: 'image', empty_multi: null } },
      rawConfig: '',
    } as never)
    renderPage()
    await openNamedPlugin()
    expect(await screen.findByText('格式')).toBeInTheDocument()
    expect(screen.getByText('text')).toBeInTheDocument()
  })
})

describe('PluginConfigPage 详情与文档', () => {
  function makeRichPlugin() {
    const plugin = makePlugin('test.emoji', 'Emoji Plugin')
    plugin.manifest.homepage_url = 'https://home.example'
    plugin.manifest.repository_url = 'https://repo.example'
    plugin.manifest.urls = {
      documentation: 'https://docs.example',
      issues: 'https://issues.example',
    }
    plugin.changelog = '# 插件更新日志'
    plugin.manifest.description = '丰富描述'
    plugin.manifest.id = 'emoji.manifest'
    return plugin
  }

  function makeComponents(): PluginRuntimeComponent[] {
    return [
      {
        name: 'search_tool',
        description: '搜索工具',
        enabled: true,
        plugin_name: 'test.emoji',
        component_type: 'tool',
        parameters_schema: { properties: { q: {}, limit: {} } },
      },
      {
        name: 'broken_schema',
        description: '',
        enabled: true,
        plugin_name: 'test.emoji',
        component_type: 'tool',
        parameters_schema: { properties: null as unknown as Record<string, unknown> },
      },
      {
        name: 'old_action',
        description: '旧动作',
        enabled: false,
        plugin_name: 'test.emoji',
        component_type: 'action',
        activation_type: 'keyword',
        activation_keywords: ['hi', 'hello'],
      },
      {
        name: 'cmd',
        description: '命令组件',
        enabled: true,
        plugin_name: 'test.emoji',
        component_type: 'command',
      },
    ]
  }

  it('详情页展示链接、README、更新日志与分组组件', async () => {
    vi.mocked(pluginApi.getInstalledPlugins).mockResolvedValue([makeRichPlugin()] as never)
    vi.mocked(pluginApi.getPluginRuntimeComponents).mockResolvedValue(makeComponents() as never)
    vi.mocked(pluginApi.getLocalPluginReadme).mockResolvedValue('# Hello README')
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: /Emoji Plugin/ }))
    await user.click(await screen.findByRole('tab', { name: '详情' }))

    expect(await screen.findByText('插件详情')).toBeInTheDocument()
    expect(screen.getByText('丰富描述')).toBeInTheDocument()
    expect(screen.getByTestId('plugin-stats')).toHaveTextContent('emoji.manifest')
    expect(screen.getByRole('link', { name: '主页' })).toHaveAttribute('href', 'https://home.example')
    expect(screen.getByRole('link', { name: '仓库' })).toHaveAttribute('href', 'https://repo.example')
    expect(screen.getByRole('link', { name: '文档' })).toHaveAttribute('href', 'https://docs.example')
    expect(screen.getByRole('link', { name: '问题反馈' })).toHaveAttribute('href', 'https://issues.example')
    expect(await screen.findByText('# Hello README')).toBeInTheDocument()
    expect(screen.getByText('# 插件更新日志')).toBeInTheDocument()
    expect(await screen.findByText('search_tool')).toBeInTheDocument()
    expect(screen.getByText('q')).toBeInTheDocument()
    expect(screen.getByText('limit')).toBeInTheDocument()
    expect(screen.getByText('旧版动作')).toBeInTheDocument()
    expect(screen.getByText('触发方式：keyword')).toBeInTheDocument()
    expect(screen.getByText('hi')).toBeInTheDocument()
    expect(screen.getByText('命令组件')).toBeInTheDocument()
    expect(screen.getByText('禁用')).toBeInTheDocument()
    expect(screen.getByText('4 个组件')).toBeInTheDocument()
  })

  it('详情页在组件/README 失败与空内容时展示对应空态', async () => {
    const plugin = makePlugin('test.emoji', 'Emoji Plugin')
    plugin.manifest.description = ''
    plugin.changelog = null
    plugin.manifest.author = { name: '' }
    plugin.manifest.license = '   '
    vi.mocked(pluginApi.getInstalledPlugins).mockResolvedValue([plugin] as never)
    vi.mocked(pluginApi.getPluginRuntimeComponents).mockRejectedValue(new Error('组件挂了'))
    vi.mocked(pluginApi.getLocalPluginReadme).mockRejectedValue(new Error('readme 挂了'))

    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: /Emoji Plugin/ }))
    await user.click(await screen.findByRole('tab', { name: '详情' }))

    expect(await screen.findByText('暂无描述')).toBeInTheDocument()
    expect(await screen.findByText('组件挂了')).toBeInTheDocument()
    expect(screen.getByText('readme 挂了')).toBeInTheDocument()
    expect(screen.getByText('暂无更新日志')).toBeInTheDocument()
    expect(screen.queryByText('作者')).not.toBeInTheDocument()
  })

  it('详情页非 Error 失败使用回退文案，空组件展示占位', async () => {
    vi.mocked(pluginApi.getPluginRuntimeComponents).mockRejectedValue('x')
    vi.mocked(pluginApi.getLocalPluginReadme).mockRejectedValue('y')
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: /Emoji Plugin/ }))
    await user.click(await screen.findByRole('tab', { name: '详情' }))
    expect(await screen.findByText('组件加载失败')).toBeInTheDocument()
    expect(screen.getByText('README 加载失败')).toBeInTheDocument()
  })

  it('详情页空 README 与空组件列表', async () => {
    vi.mocked(pluginApi.getPluginRuntimeComponents).mockResolvedValue([] as never)
    vi.mocked(pluginApi.getLocalPluginReadme).mockResolvedValue('')
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: /Emoji Plugin/ }))
    await user.click(await screen.findByRole('tab', { name: '详情' }))
    expect(await screen.findByText('暂无 README')).toBeInTheDocument()
    expect(screen.getByText('当前插件未注册运行时组件')).toBeInTheDocument()
  })

  it('详情页仅有命令时工具分组展示空占位', async () => {
    vi.mocked(pluginApi.getPluginRuntimeComponents).mockResolvedValue([
      {
        name: 'only-cmd',
        description: '',
        enabled: true,
        plugin_name: 'test.emoji',
        component_type: 'command',
      },
    ] as never)
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: /Emoji Plugin/ }))
    await user.click(await screen.findByRole('tab', { name: '详情' }))
    expect(await screen.findByText('only-cmd')).toBeInTheDocument()
    expect(screen.getByText('暂无工具组件')).toBeInTheDocument()
  })

  it('打开文档面板加载 README，切换更新日志并关闭', async () => {
    vi.mocked(pluginApi.getLocalPluginReadme).mockResolvedValue('# 文档 README')
    vi.mocked(pluginApi.getLocalPluginChangelog).mockResolvedValue('# 远程 changelog')
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: /Emoji Plugin/ }))
    await user.click(await screen.findByRole('button', { name: /打开文档/ }))
    expect(await screen.findByText('插件文档')).toBeInTheDocument()
    expect(await screen.findByText('# 文档 README')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /更新日志/ }))
    expect(await screen.findByText('# 远程 changelog')).toBeInTheDocument()
    expect(pluginApi.getLocalPluginChangelog).toHaveBeenCalledWith('test.emoji')

    await user.click(screen.getByRole('button', { name: /README/ }))
    expect(await screen.findByText('# 文档 README')).toBeInTheDocument()

    const panel = document.querySelector('[data-dashboard-floating-content="true"]') as HTMLElement
    const closeBtn = Array.from(panel.querySelectorAll('button')).find(
      (button) => button.querySelector('svg.lucide-x') && button.textContent?.trim() === ''
    )
    expect(closeBtn).toBeTruthy()
    await user.click(closeBtn!)
    await waitFor(() =>
      expect(document.querySelector('[data-dashboard-floating-content="true"]')).not.toBeInTheDocument()
    )
  })

  it('文档面板使用插件自带 changelog 且支持拖拽与空内容', async () => {
    const plugin = makePlugin('test.emoji', 'Emoji Plugin')
    plugin.changelog = '  内置日志  '
    vi.mocked(pluginApi.getInstalledPlugins).mockResolvedValue([plugin] as never)
    vi.mocked(pluginApi.getLocalPluginReadme).mockResolvedValue('')
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: /Emoji Plugin/ }))
    await user.click(await screen.findByRole('button', { name: /打开文档/ }))
    expect(await screen.findByText('暂无 README')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /更新日志/ }))
    expect(await screen.findByText('内置日志')).toBeInTheDocument()
    expect(pluginApi.getLocalPluginChangelog).not.toHaveBeenCalled()

    const handle = screen.getByRole('button', { name: '移动插件文档窗口' })
    const panel = handle.parentElement as HTMLElement
    vi.spyOn(panel, 'getBoundingClientRect').mockReturnValue({
      x: 100,
      y: 100,
      left: 100,
      top: 100,
      right: 660,
      bottom: 720,
      width: 560,
      height: 620,
      toJSON: () => ({}),
    })
    fireEvent.pointerDown(handle, { button: 1, clientX: 110, clientY: 110, pointerId: 9 })
    fireEvent.pointerDown(handle, { button: 0, clientX: 110, clientY: 110, pointerId: 1 })
    fireEvent.pointerMove(handle, { clientX: 200, clientY: 180, pointerId: 99 })
    fireEvent.pointerMove(handle, { clientX: 240, clientY: 190, pointerId: 1 })
    fireEvent.pointerUp(handle, { pointerId: 1 })
    fireEvent.keyDown(handle, { key: 'a' })
    fireEvent.keyDown(handle, { key: 'ArrowRight' })
    fireEvent.keyDown(handle, { key: 'ArrowLeft', shiftKey: true })
    fireEvent.keyDown(handle, { key: 'ArrowDown' })
    fireEvent.keyDown(handle, { key: 'ArrowUp' })
    fireEvent.mouseDown(handle, { button: 0, clientX: 120, clientY: 120 })
    fireEvent.mouseMove(window, { clientX: 160, clientY: 140 })
    fireEvent.mouseUp(window)
    expect(handle).toHaveAttribute('aria-label', '移动插件文档窗口')
  })

  it('文档面板加载失败展示错误，非 Error 使用回退文案', async () => {
    vi.mocked(pluginApi.getLocalPluginReadme).mockRejectedValue(new Error('文档挂了'))
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: /Emoji Plugin/ }))
    await user.click(await screen.findByRole('button', { name: /打开文档/ }))
    expect(await screen.findByText('文档挂了')).toBeInTheDocument()
  })

  it('文档面板 changelog 远程失败非 Error 使用回退文案', async () => {
    vi.mocked(pluginApi.getLocalPluginReadme).mockResolvedValue('# r')
    vi.mocked(pluginApi.getLocalPluginChangelog).mockRejectedValue('bad')
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: /Emoji Plugin/ }))
    await user.click(await screen.findByRole('button', { name: /打开文档/ }))
    await screen.findByText('# r')
    await user.click(screen.getByRole('button', { name: /更新日志/ }))
    expect(await screen.findByText('文档加载失败')).toBeInTheDocument()
  })
})

describe('PluginConfigPage 列表操作与状态', () => {
  it('列表启停成功与失败', async () => {
    const user = userEvent.setup()
    renderPage()
    await screen.findByText('Emoji Plugin')
    await user.click(screen.getByRole('switch', { name: '关闭插件' }))
    await waitFor(() => expect(pluginApi.togglePlugin).toHaveBeenCalledWith('test.emoji'))
    expect(toastMock).toHaveBeenCalledWith(expect.objectContaining({ title: '插件已关闭' }))

    vi.mocked(pluginApi.togglePlugin).mockRejectedValue(new Error('列表切换失败'))
    await user.click(await screen.findByRole('switch', { name: '关闭插件' }))
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({ title: '切换插件状态失败', description: '列表切换失败' })
      )
    )
  })

  it('删除插件成功、失败与 loading 时不可关闭', async () => {
    let resolveUninstall: (value?: unknown) => void = () => undefined
    vi.mocked(pluginApi.uninstallPlugin).mockImplementation(
      () => new Promise((resolve) => {
        resolveUninstall = (value) => resolve(value as never)
      })
    )
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: '删除' }))
    expect(screen.getByRole('heading', { name: '确认删除插件' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '取消' }))
    expect(screen.queryByRole('heading', { name: '确认删除插件' })).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '删除' }))
    await user.click(screen.getByRole('button', { name: '确认删除' }))
    expect(await screen.findByText('正在删除')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '取消' }))
    expect(screen.getByText('正在删除')).toBeInTheDocument()
    resolveUninstall({ success: true })
    expect(await screen.findByText('删除完成')).toBeInTheDocument()
    expect(toastMock).toHaveBeenCalledWith(expect.objectContaining({ title: '删除插件成功' }))
  })

  it('删除插件失败展示错误进度', async () => {
    vi.mocked(pluginApi.uninstallPlugin).mockRejectedValue(new Error('卸载失败'))
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: '删除' }))
    await user.click(screen.getByRole('button', { name: '确认删除' }))
    expect(await screen.findByText('删除失败')).toBeInTheDocument()
    expect(screen.getByText('卸载失败')).toBeInTheDocument()
    expect(toastMock).toHaveBeenCalledWith(expect.objectContaining({ title: '删除插件失败' }))
    const closeButtons = screen.getAllByRole('button', { name: '关闭' })
    await user.click(closeButtons[closeButtons.length - 1])
    expect(screen.queryByRole('heading', { name: '确认删除插件' })).not.toBeInTheDocument()
  })

  it('更新插件成功、失败，并接收进度订阅', async () => {
    const plugin = makePlugin('test.emoji', 'Emoji Plugin')
    plugin.manifest.repository_url = 'https://example.com/emoji.git'
    vi.mocked(pluginApi.getInstalledPlugins).mockResolvedValue([plugin] as never)
    vi.mocked(pluginApi.fetchPluginList).mockResolvedValue([
      {
        id: 'test.emoji',
        manifest: {
          ...plugin.manifest,
          version: '1.4.0',
          repository_url: 'https://example.com/emoji.git',
        },
      },
    ] as never)

    let resolveUpdate: (value?: unknown) => void = () => undefined
    vi.mocked(pluginApi.updatePlugin).mockImplementation(
      () => new Promise((resolve) => {
        resolveUpdate = (value) => resolve(value as never)
      })
    )

    const user = userEvent.setup()
    renderPage()
    const updateButton = await screen.findByRole('button', { name: '发现新版本 v1.4.0' })
    await user.click(updateButton)
    expect(screen.getByRole('heading', { name: '确认更新插件' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '确认更新' }))
    expect(await screen.findByText('正在更新')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '取消' }))
    expect(screen.getByText('正在更新')).toBeInTheDocument()

    act(() => {
      progressClient.emit({
        operation: 'update',
        stage: 'loading',
        progress: 40,
        message: '下载中',
        plugin_id: 'test.emoji',
        total_plugins: 1,
        loaded_plugins: 0,
      })
    })
    expect(await screen.findByText('下载中')).toBeInTheDocument()

    act(() => {
      progressClient.emit({
        operation: 'uninstall',
        stage: 'loading',
        progress: 10,
        message: '不该出现',
        plugin_id: 'test.emoji',
        total_plugins: 1,
        loaded_plugins: 0,
      })
    })
    expect(screen.queryByText('不该出现')).not.toBeInTheDocument()

    resolveUpdate({ success: true })
    expect(await screen.findByText('更新完成')).toBeInTheDocument()
    expect(toastMock).toHaveBeenCalledWith(expect.objectContaining({ title: '更新插件成功' }))
    expect(pluginApi.updatePlugin).toHaveBeenCalledWith(
      'test.emoji',
      'https://example.com/emoji.git',
      'main'
    )
  })

  it('更新插件失败与缺少仓库地址', async () => {
    const plugin = makePlugin('test.emoji', 'Emoji Plugin')
    plugin.manifest.version = '1.3.2'
    plugin.manifest.host_application.max_version = '1.0.99'
    plugin.load_status = 'failed'
    plugin.load_error = 'Host 版本不兼容'
    vi.mocked(pluginApi.getInstalledPlugins).mockResolvedValue([plugin] as never)
    vi.mocked(pluginApi.fetchPluginList).mockResolvedValue([
      {
        id: 'test.emoji',
        manifest: {
          ...plugin.manifest,
          version: '1.4.0',
          host_application: { min_version: '1.1.0', max_version: '1.1.99' },
        },
      },
    ] as never)

    renderPage()
    expect(await screen.findByText('当前插件版本已不兼容')).toBeInTheDocument()
    // 没有仓库地址时 hasUpdate 为 false，不展示「立即更新」
    expect(screen.queryByRole('button', { name: '立即更新' })).not.toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: '插件清单中没有仓库地址，无法更新/升级' })
    ).toBeDisabled()
  })

  it('更新接口失败展示错误进度', async () => {
    const plugin = makePlugin('test.emoji', 'Emoji Plugin')
    plugin.manifest.repository_url = 'https://example.com/emoji.git'
    vi.mocked(pluginApi.getInstalledPlugins).mockResolvedValue([plugin] as never)
    vi.mocked(pluginApi.fetchPluginList).mockResolvedValue([
      {
        id: 'test.emoji',
        manifest: { ...plugin.manifest, version: '2.0.0', repository_url: 'https://example.com/emoji.git' },
      },
    ] as never)
    vi.mocked(pluginApi.updatePlugin).mockRejectedValue(new Error('网络中断'))

    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: '发现新版本 v2.0.0' }))
    await user.click(screen.getByRole('button', { name: '确认更新' }))
    expect(await screen.findByText('更新失败')).toBeInTheDocument()
    expect(screen.getByText('网络中断')).toBeInTheDocument()
    expect(toastMock).toHaveBeenCalledWith(expect.objectContaining({ title: '更新插件失败' }))
  })

  it('加载插件列表失败弹出 toast', async () => {
    vi.mocked(pluginApi.getInstalledPlugins).mockRejectedValue(new Error('列表挂了'))
    renderPage()
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({ title: '加载插件列表失败', description: '列表挂了' })
      )
    )
  })

  it('市场版本检查失败只打 warn 并允许重新检查', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined)
    vi.mocked(pluginApi.fetchPluginList).mockRejectedValue(new Error('市场挂了'))
    renderPage()
    await screen.findByText('Emoji Plugin')
    await waitFor(() => expect(warn).toHaveBeenCalled())
    warn.mockRestore()
  })

  it('麦麦版本获取失败时仍展示插件列表', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined)
    vi.mocked(pluginApi.getMaimaiVersion).mockRejectedValue(new Error('version down'))
    renderPage()
    expect(await screen.findByText('Emoji Plugin')).toBeInTheDocument()
    await waitFor(() => expect(warn).toHaveBeenCalled())
    warn.mockRestore()
  })

  it('熔断、半开、加载中与失败状态展示', async () => {
    const openPlugin = makePlugin('p.open', 'Open Circuit')
    openPlugin.circuit_status = { state: 'open', remaining_sec: 8.2, cooldown_level: 1, half_open_inflight: false }
    const expiredOpen = makePlugin('p.open0', 'Expired Circuit')
    expiredOpen.circuit_status = { state: 'open', remaining_sec: 0, cooldown_level: 1, half_open_inflight: false }
    const half = makePlugin('p.half', 'Half Open')
    half.circuit_status = { state: 'half_open', remaining_sec: 1, cooldown_level: 1, half_open_inflight: true }
    const loading = makePlugin('p.loading', 'Loading One')
    loading.load_status = 'loading'
    const failed = makePlugin('p.failed', 'Failed One')
    failed.load_status = 'failed'
    failed.load_error = undefined
    vi.mocked(pluginApi.getInstalledPlugins).mockResolvedValue([
      openPlugin,
      expiredOpen,
      half,
      loading,
      failed,
    ] as never)

    const user = userEvent.setup()
    renderPage()
    expect(await screen.findByText('熔断中 9s')).toBeInTheDocument()
    expect(screen.getByText('熔断中')).toBeInTheDocument()
    expect(screen.getByText('半开测试')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: '加载中' })).toBeInTheDocument()
    expect(screen.getByText(/熔断中 2 个/)).toBeInTheDocument()
    expect(screen.getByText('失败原因：运行时未返回具体失败原因')).toBeInTheDocument()

    await user.click(screen.getAllByRole('button', { name: '查看详情' })[0])
    expect(await screen.findByRole('heading', { name: '插件加载失败详情' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '关闭' }))
  })

  it('future-retro 主题按状态渲染状态条', async () => {
    themeState.dashboardStyle = 'future-retro'
    const disabled = makePlugin('p.off', 'Off Plugin')
    disabled.enabled = false
    const failed = makePlugin('p.bad', 'Bad Plugin')
    failed.load_status = 'failed'
    failed.load_error = 'boom'
    vi.mocked(pluginApi.getInstalledPlugins).mockResolvedValue([
      makePlugin('p.ok', 'Ok Plugin'),
      disabled,
      failed,
    ] as never)
    renderPage()
    expect(await screen.findByText('Ok Plugin')).toBeInTheDocument()
    expect(screen.getByTitle('Ok Plugin：已启用')).toHaveClass('bg-emerald-500')
    expect(screen.getByTitle('Off Plugin：已禁用')).toHaveClass('bg-muted-foreground/45')
    expect(screen.getByTitle('Bad Plugin：启动失败')).toHaveClass('bg-red-500')
    expect(screen.getByText('已安装 3 个插件，已启用 2 个，已禁用 1 个，加载中 0 个，启动失败 1 个')).toBeInTheDocument()
  })

  it('刷新列表与重启按钮', async () => {
    const user = userEvent.setup()
    renderPage()
    await screen.findByText('Emoji Plugin')
    await user.click(screen.getByRole('button', { name: '刷新' }))
    await waitFor(() => expect(vi.mocked(pluginApi.getInstalledPlugins).mock.calls.length).toBeGreaterThan(1))
    await user.click(screen.getByRole('button', { name: /重启麦麦/ }))
    expect(restartState.triggerRestart).toHaveBeenCalled()
  })

  it('重启中时禁用重启按钮', async () => {
    restartState.isRestarting = true
    renderPage()
    await screen.findByText('Emoji Plugin')
    expect(screen.getByRole('button', { name: /重启麦麦/ })).toBeDisabled()
  })

  it('重复插件 ID 只保留第一项', async () => {
    vi.mocked(pluginApi.getInstalledPlugins).mockResolvedValue([
      makePlugin('dup.id', 'First Dup'),
      makePlugin('dup.id', 'Second Dup'),
    ] as never)
    renderPage()
    expect(await screen.findByText('First Dup')).toBeInTheDocument()
    expect(screen.queryByText('Second Dup')).not.toBeInTheDocument()
  })

  it('市场无仓库、版本相同、最新版不兼容时更新按钮禁用', async () => {
    const noRepo = makePlugin('p.norepo', 'No Repo')
    const same = makePlugin('p.same', 'Same Ver')
    same.manifest.repository_url = 'https://example.com/same.git'
    const incompat = makePlugin('p.badm', 'Bad Market')
    incompat.manifest.repository_url = 'https://example.com/bad.git'
    vi.mocked(pluginApi.getInstalledPlugins).mockResolvedValue([noRepo, same, incompat] as never)
    vi.mocked(pluginApi.fetchPluginList).mockResolvedValue([
      { id: 'p.norepo', manifest: { ...noRepo.manifest, version: '2.0.0' } },
      {
        id: 'p.same',
        manifest: { ...same.manifest, version: '1.0.0', repository_url: 'https://example.com/same.git' },
      },
      {
        id: 'p.badm',
        manifest: {
          ...incompat.manifest,
          version: '9.0.0',
          repository_url: 'https://example.com/bad.git',
          host_application: { min_version: '9.0.0' },
        },
      },
    ] as never)

    renderPage()
    expect(await screen.findByRole('button', { name: '插件清单中没有仓库地址，无法更新/升级' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '当前已是最新版本' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '插件市场最新版本 v9.0.0 与当前麦麦不兼容' })).toBeDisabled()
  })

  it('无 Host 标记时按 manifest 判定版本不兼容', async () => {
    const plugin = makePlugin('p.manifest', 'Manifest Incompat')
    plugin.load_status = 'failed'
    plugin.load_error = '启动失败：未知原因'
    plugin.manifest.host_application.max_version = '0.9.0'
    vi.mocked(pluginApi.getInstalledPlugins).mockResolvedValue([plugin] as never)
    renderPage()
    expect(await screen.findByText('当前插件版本已不兼容')).toBeInTheDocument()
  })

  it('SDK / Manifest 版本不兼容标记同样识别', async () => {
    const sdk = makePlugin('p.sdk', 'SDK Bad')
    sdk.load_status = 'failed'
    sdk.load_error = 'SDK 版本不兼容'
    const manifest = makePlugin('p.mf', 'MF Bad')
    manifest.load_status = 'failed'
    manifest.load_error = 'Manifest 版本不兼容'
    vi.mocked(pluginApi.getInstalledPlugins).mockResolvedValue([sdk, manifest] as never)
    renderPage()
    const banners = await screen.findAllByText('当前插件版本已不兼容')
    expect(banners).toHaveLength(2)
  })

  it('仅看有更新会过滤出可升级插件', async () => {
    const stale = makePlugin('p.stale', 'Stale Plugin')
    stale.manifest.repository_url = 'https://example.com/stale.git'
    const fresh = makePlugin('p.fresh', 'Fresh Plugin')
    fresh.manifest.repository_url = 'https://example.com/fresh.git'
    vi.mocked(pluginApi.getInstalledPlugins).mockResolvedValue([stale, fresh] as never)
    vi.mocked(pluginApi.fetchPluginList).mockResolvedValue([
      {
        id: 'p.stale',
        manifest: { ...stale.manifest, version: '2.0.0', repository_url: 'https://example.com/stale.git' },
      },
      {
        id: 'p.fresh',
        manifest: { ...fresh.manifest, version: '1.0.0', repository_url: 'https://example.com/fresh.git' },
      },
    ] as never)

    const user = userEvent.setup()
    renderPage()
    await screen.findByText('Stale Plugin')
    await user.click(screen.getByLabelText('有更新'))
    expect(await screen.findByText('Stale Plugin')).toBeInTheDocument()
    expect(screen.queryByText('Fresh Plugin')).not.toBeInTheDocument()
  })

  it('卸载进度订阅写入删除对话框', async () => {
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: '删除' }))
    act(() => {
      progressClient.emit({
        operation: 'uninstall',
        stage: 'loading',
        progress: 55,
        message: '正在移除文件',
        plugin_id: 'test.emoji',
        total_plugins: 1,
        loaded_plugins: 0,
      })
    })
    expect(await screen.findByText('正在移除文件')).toBeInTheDocument()
    expect(screen.getByText('55%')).toBeInTheDocument()
  })
})
