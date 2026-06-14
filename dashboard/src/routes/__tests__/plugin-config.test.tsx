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
  getPluginConfigSchema: vi.fn(),
  getPluginConfig: vi.fn(),
  getPluginConfigRaw: vi.fn(),
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
    manifest: {
      manifest_version: 2,
      name,
      version: '1.0.0',
      description: 'desc',
      author: { name: 'tester' },
      license: 'MIT',
      host_application: { min_version: '1.0.0' },
    },
  }
}

beforeEach(() => {
  vi.mocked(pluginApi.getInstalledPlugins).mockResolvedValue([makePlugin('test.emoji', 'Emoji Plugin')] as never)
  vi.mocked(pluginApi.fetchPluginList).mockResolvedValue([] as never)
  vi.mocked(pluginApi.getPluginConfigSchema).mockResolvedValue({
    plugin_info: { name: 'Emoji Plugin', version: '1.0.0', description: 'desc' }, sections: {}, layout: { type: 'auto' },
  } as never)
  vi.mocked(pluginApi.getPluginConfig).mockResolvedValue({} as never)
  vi.mocked(pluginApi.getPluginConfigRaw).mockResolvedValue('key = "value"\n' as never)
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

  it('选中插件加载其 schema/config/raw 并进入编辑器', async () => {
    const user = userEvent.setup()
    render(<PluginConfigPage />)
    await user.click(await screen.findByRole('button', { name: /Emoji Plugin/ }))

    await waitFor(() => expect(pluginApi.getPluginConfigSchema).toHaveBeenCalledWith('test.emoji'))
    expect(pluginApi.getPluginConfig).toHaveBeenCalledWith('test.emoji')
    expect(pluginApi.getPluginConfigRaw).toHaveBeenCalledWith('test.emoji')
    expect(await screen.findByRole('button', { name: /保存/ })).toBeInTheDocument()
  })

  it('编辑器内启停插件调用 togglePlugin', async () => {
    const user = userEvent.setup()
    render(<PluginConfigPage />)
    await user.click(await screen.findByRole('button', { name: /Emoji Plugin/ }))
    await user.click(await screen.findByRole('button', { name: /禁用/ }))
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
})
