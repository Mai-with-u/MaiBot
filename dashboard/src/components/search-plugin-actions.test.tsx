import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { AISearchInstallRequest } from '@/lib/ai-search-api'
import { installPlugin } from '@/lib/plugin-api/install-flow'

import { SearchPluginActions } from './search-plugin-actions'

vi.mock('@/lib/plugin-api/install-flow', () => ({ installPlugin: vi.fn() }))
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }))

const installMock = vi.mocked(installPlugin)
const request: AISearchInstallRequest = {
  id: 'proposal-1',
  plugin_id: 'test.voice',
  name: 'Voice',
  version: '1.0.0',
  repository_url: 'https://github.com/test/voice',
  branch: 'main',
  mode: 'releases',
}

describe('SearchPluginActions', () => {
  beforeEach(() => installMock.mockReset())
  afterEach(cleanup)

  it('展示与重新渲染都不安装，手动确认后仅安装展示的版本一次', async () => {
    installMock.mockResolvedValue({ success: true, message: '安装完成' })
    const onOpenConfig = vi.fn()
    const props = { installRequests: [request], configUpdates: [], onOpenConfig }
    const view = render(<SearchPluginActions {...props} />)
    view.rerender(<SearchPluginActions {...props} />)
    expect(installMock).not.toHaveBeenCalled()
    expect(screen.getByText(/v1.0.0/)).toBeInTheDocument()
    const button = screen.getByRole('button', { name: 'search.pluginConfirmInstall' })
    act(() => {
      button.click()
      button.click()
    })
    await screen.findByText('安装完成')
    expect(installMock).toHaveBeenCalledExactlyOnceWith(
      'test.voice',
      request.repository_url,
      'main',
      { version: '1.0.0' }
    )
    fireEvent.click(screen.getByRole('button', { name: 'search.pluginOpenConfig' }))
    expect(onOpenConfig).toHaveBeenCalledWith('test.voice')
  })

  it('取消不会触发安装，也不会因重新渲染自动恢复确认', () => {
    const props = { installRequests: [request], configUpdates: [], onOpenConfig: vi.fn() }
    const view = render(<SearchPluginActions {...props} />)
    fireEvent.click(screen.getByRole('button', { name: 'search.pluginCancelInstall' }))
    view.rerender(<SearchPluginActions {...props} />)
    expect(installMock).not.toHaveBeenCalled()
    expect(
      screen.queryByRole('button', { name: 'search.pluginConfirmInstall' })
    ).not.toBeInTheDocument()
    expect(screen.getByText('search.pluginInstallStatus.cancelled')).toBeInTheDocument()
  })

  it('失败后保留错误，只有再次手动确认才会重试', async () => {
    installMock
      .mockRejectedValueOnce(new Error('版本已撤回'))
      .mockResolvedValueOnce({ success: true, message: '安装完成' })
    render(
      <SearchPluginActions installRequests={[request]} configUpdates={[]} onOpenConfig={vi.fn()} />
    )
    fireEvent.click(screen.getByRole('button', { name: 'search.pluginConfirmInstall' }))
    await screen.findByText('版本已撤回')
    await waitFor(() => expect(installMock).toHaveBeenCalledTimes(1))
    fireEvent.click(screen.getByRole('button', { name: 'search.pluginConfirmInstall' }))
    await screen.findByText('安装完成')
    expect(installMock).toHaveBeenCalledTimes(2)
  })

  it('展示配置实际改动并支持进入对应插件配置', () => {
    const onOpenConfig = vi.fn()
    render(
      <SearchPluginActions
        installRequests={[]}
        configUpdates={[
          {
            id: 'change-1',
            plugin_id: 'test.voice',
            name: 'Voice',
            message: '配置已保存',
            changes: [{ path: ['general', 'frequency'], before: 5, after: 8 }],
          },
        ]}
        onOpenConfig={onOpenConfig}
      />
    )
    expect(screen.getByText('general.frequency')).toBeInTheDocument()
    expect(screen.getByText('5 → 8')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'search.pluginOpenConfig' }))
    expect(onOpenConfig).toHaveBeenCalledWith('test.voice')
    expect(installMock).not.toHaveBeenCalled()
  })
})
