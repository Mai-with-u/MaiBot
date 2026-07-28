import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { IncompatiblePluginNotice, UpdateNoticeResponse } from '@/lib/system-api'

import { UpdateNoticeDialog } from '../update-notice-dialog'
import { ackUpdateNotice, getUpdateNotice } from '@/lib/system-api'

// 路由导航桩
const navigateMock = vi.hoisted(() => vi.fn(() => Promise.resolve()))
vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => navigateMock,
}))

// 设置项桩：通过可变引用控制 alwaysShowUpdateNotice
const settingState = vi.hoisted(() => ({ alwaysShow: false }))
vi.mock('@/lib/settings-manager', () => ({
  getSetting: () => settingState.alwaysShow,
}))

vi.mock('@/lib/system-api', () => ({
  getUpdateNotice: vi.fn(),
  ackUpdateNotice: vi.fn(),
}))

// Markdown 渲染器替换为轻量桩，仅回显内容
vi.mock('@/components/markdown-renderer', () => ({
  MarkdownRenderer: ({ content }: { content: string }) => (
    <div data-testid="markdown-content">{content}</div>
  ),
}))

/** 构造更新公告响应 */
function makeNotice(overrides: Partial<UpdateNoticeResponse> = {}): UpdateNoticeResponse {
  return {
    pending: true,
    current_version: '0.12.0',
    from_version: '0.11.0',
    versions: ['0.12.0'],
    content: '更新亮点内容',
    incompatible_plugins: [],
    ...overrides,
  }
}

/** 构造不兼容插件条目 */
function makePlugin(overrides: Partial<IncompatiblePluginNotice> = {}): IncompatiblePluginNotice {
  return {
    plugin_id: 'demo.plugin',
    name: '演示插件',
    installed_version: '1.0.0',
    host_min_version: '0.10.0',
    host_max_version: '0.11.9',
    update_status: 'unavailable',
    update_version: null,
    ...overrides,
  }
}

beforeEach(() => {
  settingState.alwaysShow = false
  vi.mocked(ackUpdateNotice).mockResolvedValue({
    success: true,
    message: 'ok',
    version: '0.12.0',
  })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('UpdateNoticeDialog', () => {
  it('无待展示公告时不渲染任何对话框', async () => {
    vi.mocked(getUpdateNotice).mockResolvedValue(makeNotice({ pending: false }))
    render(<UpdateNoticeDialog />)

    await waitFor(() => expect(getUpdateNotice).toHaveBeenCalledWith(false))
    expect(document.querySelector('[role="dialog"]')).toBeNull()
    expect(screen.queryByText('更新内容')).toBeNull()
  })

  it('拉取公告失败时静默记录错误且不渲染', async () => {
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    vi.mocked(getUpdateNotice).mockRejectedValue(new Error('网络错误'))
    render(<UpdateNoticeDialog />)

    await waitFor(() =>
      expect(errorSpy).toHaveBeenCalledWith('[UpdateNotice] 获取更新公告失败:', expect.any(Error))
    )
    expect(document.querySelector('[role="dialog"]')).toBeNull()
    errorSpy.mockRestore()
  })

  it('存在待展示公告时弹出更新内容对话框并渲染 Markdown', async () => {
    vi.mocked(getUpdateNotice).mockResolvedValue(makeNotice())
    render(<UpdateNoticeDialog />)

    expect(await screen.findByText('更新内容')).toBeInTheDocument()
    expect(await screen.findByTestId('markdown-content')).toHaveTextContent('更新亮点内容')
    expect(screen.getByText('查看本次 MaiBot 更新包含的功能与修复。')).toBeInTheDocument()
  })

  it('无兼容性问题时点击知道了直接确认公告并关闭', async () => {
    vi.mocked(getUpdateNotice).mockResolvedValue(makeNotice())
    render(<UpdateNoticeDialog />)

    fireEvent.click(await screen.findByRole('button', { name: /知道了/ }))

    await waitFor(() => expect(ackUpdateNotice).toHaveBeenCalledTimes(1))
    expect(screen.queryByText('更新内容')).toBeNull()
    expect(screen.queryByText('插件兼容性提醒')).toBeNull()
  })

  it('存在不兼容插件时进入兼容性提醒并展示各状态徽章', async () => {
    vi.mocked(getUpdateNotice).mockResolvedValue(
      makeNotice({
        incompatible_plugins: [
          makePlugin({
            plugin_id: 'p.available',
            name: '可升级插件',
            update_status: 'available',
            update_version: '2.0.0',
          }),
          makePlugin({ plugin_id: 'p.failed', name: '检查失败插件', update_status: 'check_failed' }),
          makePlugin({ plugin_id: 'p.missing', name: '下架插件', update_status: 'not_found' }),
          makePlugin({ plugin_id: 'p.stuck', name: '无更新插件', update_status: 'unavailable' }),
        ],
      })
    )
    render(<UpdateNoticeDialog />)

    fireEvent.click(await screen.findByRole('button', { name: /知道了/ }))

    expect(await screen.findByText('插件兼容性提醒')).toBeInTheDocument()
    // 描述中提示当前版本
    expect(
      screen.getByText(/以下插件在 MaiBot 更新到 v0\.12\.0 后不再兼容/)
    ).toBeInTheDocument()
    // 各插件与状态标签
    expect(screen.getByText('可升级插件')).toBeInTheDocument()
    expect(screen.getByText('可更新至 v2.0.0')).toBeInTheDocument()
    expect(screen.getByText('检查失败插件')).toBeInTheDocument()
    expect(screen.getByText('兼容更新检查失败')).toBeInTheDocument()
    expect(screen.getByText('下架插件')).toBeInTheDocument()
    expect(screen.getByText('插件市场中未找到')).toBeInTheDocument()
    expect(screen.getByText('无更新插件')).toBeInTheDocument()
    expect(screen.getByText('暂无兼容更新')).toBeInTheDocument()
    // 版本支持范围说明
    expect(
      screen.getAllByText(/支持 MaiBot v0\.10\.0 - v0\.11\.9/).length
    ).toBe(4)
    // 进入兼容性阶段时尚未确认公告
    expect(ackUpdateNotice).not.toHaveBeenCalled()

    // 稍后处理：确认公告并关闭
    fireEvent.click(screen.getByRole('button', { name: '稍后处理' }))
    await waitFor(() => expect(ackUpdateNotice).toHaveBeenCalledTimes(1))
    expect(screen.queryByText('插件兼容性提醒')).toBeNull()
  })

  it('前往插件管理会确认公告并跳转插件配置页', async () => {
    vi.mocked(getUpdateNotice).mockResolvedValue(
      makeNotice({ incompatible_plugins: [makePlugin()] })
    )
    render(<UpdateNoticeDialog />)

    fireEvent.click(await screen.findByRole('button', { name: /知道了/ }))
    fireEvent.click(await screen.findByRole('button', { name: /前往插件管理/ }))

    await waitFor(() => expect(navigateMock).toHaveBeenCalledWith({ to: '/plugin-config' }))
    expect(ackUpdateNotice).toHaveBeenCalledTimes(1)
  })

  it('开启始终显示时无不兼容插件也进入兼容性阶段', async () => {
    settingState.alwaysShow = true
    vi.mocked(getUpdateNotice).mockResolvedValue(makeNotice())
    render(<UpdateNoticeDialog />)

    await waitFor(() => expect(getUpdateNotice).toHaveBeenCalledWith(true))
    fireEvent.click(await screen.findByRole('button', { name: /知道了/ }))

    expect(await screen.findByText('插件兼容性提醒')).toBeInTheDocument()
    expect(
      screen.getByText('当前版本未检测到因主程序更新而失去兼容性的已安装插件。')
    ).toBeInTheDocument()
    expect(
      screen.getByText(/已完成 MaiBot v0\.12\.0 的插件兼容性检查。/)
    ).toBeInTheDocument()

    // 空状态下的知道了：确认公告并关闭
    fireEvent.click(screen.getByRole('button', { name: /知道了/ }))
    await waitFor(() => expect(ackUpdateNotice).toHaveBeenCalledTimes(1))
    expect(screen.queryByText('插件兼容性提醒')).toBeNull()
  })

  it('确认公告接口失败时仍关闭对话框并记录错误', async () => {
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    vi.mocked(getUpdateNotice).mockResolvedValue(makeNotice())
    vi.mocked(ackUpdateNotice).mockRejectedValue(new Error('确认失败'))
    render(<UpdateNoticeDialog />)

    fireEvent.click(await screen.findByRole('button', { name: /知道了/ }))

    await waitFor(() =>
      expect(errorSpy).toHaveBeenCalledWith('[UpdateNotice] 确认更新公告失败:', expect.any(Error))
    )
    expect(screen.queryByText('更新内容')).toBeNull()
    errorSpy.mockRestore()
  })
})
