import { act, cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { LogViewerPage, ReasoningLogViewerPage, StatisticsLogViewerPage } from '../logs'
import type { LogEntry } from '@/lib/log-websocket'

// 全局日志 WebSocket 管理器桩：页面只依赖这四个方法
const logWsMocks = vi.hoisted(() => ({
  clearLogs: vi.fn(),
  getAllLogs: vi.fn(),
  onConnectionChange: vi.fn(),
  onLog: vi.fn(),
}))

// 虚拟滚动桩：直接渲染全部行，并暴露 scrollToIndex 供自动滚动断言
const virtualizerMocks = vi.hoisted(() => ({
  measureElement: vi.fn(),
  scrollToIndex: vi.fn(),
}))

vi.mock('@/lib/log-websocket', () => ({ logWebSocket: logWsMocks }))

vi.mock('@tanstack/react-virtual', () => ({
  useVirtualizer: ({ count, estimateSize }: { count: number; estimateSize: () => number }) => ({
    getTotalSize: () => count * estimateSize(),
    getVirtualItems: () =>
      Array.from({ length: count }, (_, index) => ({
        index,
        key: index,
        start: index * estimateSize(),
      })),
    measureElement: virtualizerMocks.measureElement,
    scrollToIndex: virtualizerMocks.scrollToIndex,
  }),
}))

// 推理过程页较重（WS/图表），此处仅记录主文件传入的编排 props
vi.mock('../reasoning-process', () => ({
  ReasoningProcessPage: ({
    embedded,
    toolbarContainerId,
    toolbarVisible,
    topbarActionsContainerId,
  }: {
    embedded?: boolean
    toolbarContainerId?: string
    toolbarVisible?: boolean
    topbarActionsContainerId?: string
    onToolbarContentVisibleChange?: (visible: boolean) => void
  }) => (
    <div
      data-testid="reasoning-process-stub"
      data-embedded={String(embedded)}
      data-toolbar-container={toolbarContainerId}
      data-toolbar-visible={String(toolbarVisible)}
      data-topbar-actions={topbarActionsContainerId}
    />
  ),
}))

vi.mock('../statistics', () => ({
  StatisticsPage: () => <div data-testid="statistics-page-stub">详细统计内容</div>,
}))

const HINT_DISMISSED_KEY = 'log-viewer-switch-hint-dismissed'
const ACTIVE_TAB_KEY = 'log-viewer-active-tab'

function makeLog(id: string, overrides: Partial<LogEntry> = {}): LogEntry {
  return {
    id,
    level: 'INFO',
    message: `消息-${id}`,
    module: 'core.chat',
    timestamp: '2026-07-24 08:00:00',
    ...overrides,
  }
}

let logCallback: ((logs: LogEntry[]) => void) | null = null
let connectionCallback: ((connected: boolean) => void) | null = null

beforeEach(() => {
  window.localStorage.clear()
  // 默认关闭切换提示，避免干扰终端面板相关断言；提示用例内会单独清除
  window.localStorage.setItem(HINT_DISMISSED_KEY, 'true')
  logCallback = null
  connectionCallback = null
  logWsMocks.getAllLogs.mockReturnValue([])
  logWsMocks.onLog.mockImplementation((callback: (logs: LogEntry[]) => void) => {
    logCallback = callback
    return () => {}
  })
  logWsMocks.onConnectionChange.mockImplementation((callback: (connected: boolean) => void) => {
    connectionCallback = callback
    return () => {}
  })
})

afterEach(() => {
  cleanup()
  window.localStorage.clear()
})

describe('LogViewerPage 终端面板', () => {
  it('无日志时显示空态，清空与导出按钮均禁用', () => {
    render(<LogViewerPage />)

    expect(screen.getByText('暂无日志数据')).toBeInTheDocument()
    expect(screen.getByText('0 / 0')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '清空' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '导出' })).toBeDisabled()
  })

  it('渲染日志行：截断年份与级别，并应用模块自定义颜色', () => {
    logWsMocks.getAllLogs.mockReturnValue([
      makeLog('i1', { message: '普通消息', timestamp: '2026-07-24 08:00:01' }),
      makeLog('w1', {
        level: 'WARNING',
        message: '警告消息',
        module: 'plugin.loader',
        timestamp: '2026-07-25T09:30:00',
      }),
      makeLog('c1', {
        level: 'CRITICAL',
        message: '致命错误',
        module: 'colored.module',
        moduleBold: true,
        moduleColor: '#ff6600',
      }),
    ])

    render(<LogViewerPage />)

    // 时间戳去掉年份；ISO 的 T 分隔符替换为空格
    expect(screen.getAllByText('07-24 08:00:01').length).toBeGreaterThan(0)
    expect(screen.getAllByText('07-25 09:30:00').length).toBeGreaterThan(0)
    // 级别文本截断为 4 个字符
    expect(screen.getAllByText('[INFO]').length).toBeGreaterThan(0)
    expect(screen.getAllByText('[WARN]').length).toBeGreaterThan(0)
    expect(screen.getAllByText('[CRIT]').length).toBeGreaterThan(0)
    // 后端下发的模块颜色与加粗生效
    for (const moduleEl of screen.getAllByText('colored.module')) {
      expect(moduleEl).toHaveStyle('color: #ff6600; font-weight: 700')
    }
    expect(screen.getByText('3 / 3')).toBeInTheDocument()
  })

  it('存档级别非法时回退为 INFO，并隐藏 DEBUG 日志', () => {
    window.localStorage.setItem('maibot-log-level-filter', 'BOGUS')
    logWsMocks.getAllLogs.mockReturnValue([
      makeLog('d1', { level: 'DEBUG', message: '调试消息' }),
      makeLog('i1', { message: '普通消息' }),
    ])

    render(<LogViewerPage />)

    expect(screen.queryAllByText('调试消息')).toHaveLength(0)
    expect(screen.getAllByText('普通消息').length).toBeGreaterThan(0)
    expect(screen.getByText('1 / 2')).toBeInTheDocument()
  })

  it('存档的模块过滤生效；模块不存在时回退为全部模块', () => {
    const entries = [
      makeLog('a1', { message: 'Alpha 联结' }),
      makeLog('b1', { message: '心跳包', module: 'net.ws' }),
    ]
    logWsMocks.getAllLogs.mockReturnValue(entries)

    window.localStorage.setItem('maibot-log-module-filter', 'net.ws')
    render(<LogViewerPage />)
    expect(screen.getByText('1 / 2')).toBeInTheDocument()
    expect(screen.getAllByText('心跳包').length).toBeGreaterThan(0)
    expect(screen.queryAllByText('Alpha 联结')).toHaveLength(0)
    cleanup()

    // 存档模块在当前日志中不存在 → 视为“全部模块”
    window.localStorage.setItem('maibot-log-module-filter', 'ghost.module')
    render(<LogViewerPage />)
    expect(screen.getByText('2 / 2')).toBeInTheDocument()
  })

  it('模块按中文名显示为可换行标签，点击可切换显示状态', async () => {
    const user = userEvent.setup()
    logWsMocks.getAllLogs.mockReturnValue([
      makeLog('a1', { message: '聊天消息', moduleDisplayName: '聊天管理' }),
      makeLog('b1', { message: '心跳包', module: 'net.ws', moduleDisplayName: '网络连接' }),
    ])

    render(<LogViewerPage />)

    const moduleFilters = screen.getByLabelText('模块显示筛选')
    expect(moduleFilters).toHaveClass('flex-wrap')
    expect(moduleFilters).toHaveClass('overflow-y-auto', 'border', 'h-10', 'max-h-10')
    const chatModuleButton = within(moduleFilters).getByRole('button', { name: /隐藏 聊天管理/ })
    expect(chatModuleButton).toHaveAttribute('aria-pressed', 'true')

    await user.click(chatModuleButton)
    expect(screen.queryAllByText('聊天消息')).toHaveLength(0)
    expect(screen.getAllByText('心跳包').length).toBeGreaterThan(0)
    expect(chatModuleButton).toHaveAttribute('aria-pressed', 'false')
    expect(window.localStorage.getItem('maibot-log-module-filter')).toBe('["core.chat"]')

    await user.click(chatModuleButton)
    expect(screen.getAllByText('聊天消息').length).toBeGreaterThan(0)
    expect(window.localStorage.getItem('maibot-log-module-filter')).toBe('all')

    await user.click(screen.getByRole('button', { name: '筛选' }))
    expect(moduleFilters).toHaveClass('max-h-[104px]', 'lg:min-h-full')
    expect(moduleFilters).not.toHaveClass('h-10', 'max-h-10')
  })

  it('搜索按消息与模块过滤，Escape 与清空按钮均可清除关键字', async () => {
    const user = userEvent.setup()
    logWsMocks.getAllLogs.mockReturnValue([
      makeLog('a1', { message: 'Alpha 联结' }),
      makeLog('b1', { message: '心跳包', module: 'net.ws' }),
    ])

    render(<LogViewerPage />)
    const searchInput = screen.getByPlaceholderText('搜索日志...')

    // 按消息内容过滤（大小写不敏感）
    await user.type(searchInput, 'alpha')
    expect(screen.getByText('1 / 2')).toBeInTheDocument()
    expect(screen.queryAllByText('心跳包')).toHaveLength(0)

    // Escape 清空关键字后恢复全部
    await user.keyboard('{Escape}')
    expect(screen.getByText('2 / 2')).toBeInTheDocument()

    // 按模块名过滤
    await user.type(searchInput, 'net')
    expect(screen.getByText('1 / 2')).toBeInTheDocument()
    expect(screen.queryAllByText('Alpha 联结')).toHaveLength(0)

    // 清空搜索按钮
    await user.click(screen.getByRole('button', { name: '清空搜索' }))
    expect(screen.getByText('2 / 2')).toBeInTheDocument()
  })

  it('连接状态跟随 onConnectionChange 回调切换', () => {
    render(<LogViewerPage />)
    expect(screen.getByText('未连接')).toBeInTheDocument()

    act(() => {
      connectionCallback?.(true)
    })
    expect(screen.getByText('已连接')).toBeInTheDocument()

    act(() => {
      connectionCallback?.(false)
    })
    expect(screen.getByText('未连接')).toBeInTheDocument()
  })

  it('新日志到达时自动滚动到底部，暂停后不再滚动', async () => {
    const user = userEvent.setup()
    const first = makeLog('a1')
    logWsMocks.getAllLogs.mockReturnValue([first])

    render(<LogViewerPage />)
    expect(logCallback).not.toBeNull()
    virtualizerMocks.scrollToIndex.mockClear()

    // 日志数量增加 → 滚动到最后一行
    act(() => {
      logCallback?.([first, makeLog('a2')])
    })
    expect(virtualizerMocks.scrollToIndex).toHaveBeenCalledTimes(1)
    expect(virtualizerMocks.scrollToIndex).toHaveBeenCalledWith(1, {
      align: 'end',
      behavior: 'auto',
    })

    // 点击“滚动”切换为“暂停”，并持久化 logAutoScroll
    await user.click(screen.getByRole('button', { name: '滚动' }))
    expect(screen.getByRole('button', { name: '暂停' })).toBeInTheDocument()
    expect(window.localStorage.getItem('maibot-log-auto-scroll')).toBe('false')

    act(() => {
      logCallback?.([first, makeLog('a2'), makeLog('a3')])
    })
    expect(virtualizerMocks.scrollToIndex).toHaveBeenCalledTimes(1)
  })

  it('清空按钮调用全局缓存的 clearLogs', async () => {
    const user = userEvent.setup()
    logWsMocks.getAllLogs.mockReturnValue([makeLog('a1')])

    render(<LogViewerPage />)
    await user.click(screen.getByRole('button', { name: '清空' }))

    expect(logWsMocks.clearLogs).toHaveBeenCalledTimes(1)
  })

  it('导出仅包含过滤后的日志并触发一次下载', async () => {
    const user = userEvent.setup()
    logWsMocks.getAllLogs.mockReturnValue([
      makeLog('d1', { level: 'DEBUG', message: '调试消息', module: 'core.debug' }),
      makeLog('i1', { message: '普通消息', timestamp: '2026-07-24 08:00:01' }),
    ])
    const createObjectUrlSpy = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:vitest')
    const revokeObjectUrlSpy = vi.spyOn(URL, 'revokeObjectURL')
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})

    render(<LogViewerPage />)
    await user.click(screen.getByRole('button', { name: '导出' }))

    expect(createObjectUrlSpy).toHaveBeenCalledTimes(1)
    // 默认 INFO 过滤生效：导出内容不含 DEBUG 行，级别按 8 位补齐
    const blob = createObjectUrlSpy.mock.calls[0][0] as Blob
    await expect(blob.text()).resolves.toBe('2026-07-24 08:00:01 [INFO    ] [core.chat] 普通消息')
    // 下载文件名与 URL 释放
    expect(clickSpy).toHaveBeenCalledTimes(1)
    const anchor = clickSpy.mock.contexts[0] as HTMLAnchorElement
    expect(anchor.download).toMatch(/^logs-\d{4}-\d{2}-\d{2}-\d{6}\.txt$/)
    expect(anchor.href).toContain('blob:vitest')
    expect(revokeObjectUrlSpy).toHaveBeenCalledWith('blob:vitest')
  })

  it('展开筛选并重置：恢复级别/模块过滤并写入持久化设置', async () => {
    const user = userEvent.setup()
    window.localStorage.setItem('maibot-log-level-filter', 'DEBUG')
    logWsMocks.getAllLogs.mockReturnValue([
      makeLog('d1', { level: 'DEBUG', message: '调试消息' }),
      makeLog('i1', { message: '普通消息' }),
    ])

    render(<LogViewerPage />)
    // 存档为 DEBUG 时两条都可见
    expect(screen.getByText('2 / 2')).toBeInTheDocument()

    // 展开筛选面板并持久化展开状态
    await user.click(screen.getByRole('button', { name: '筛选' }))
    expect(window.localStorage.getItem('maibot-log-filters-open')).toBe('true')

    // 重置后回到 INFO 级别，DEBUG 日志被隐藏
    await user.click(screen.getByRole('button', { name: '重置' }))
    expect(screen.getByText('1 / 2')).toBeInTheDocument()
    expect(screen.queryAllByText('调试消息')).toHaveLength(0)
    expect(window.localStorage.getItem('maibot-log-level-filter')).toBe('INFO')
    expect(window.localStorage.getItem('maibot-log-module-filter')).toBe('all')
  })

  it('显示设置：行距/列宽超界时钳制到边界值，字号按钮写入设置', async () => {
    const user = userEvent.setup()
    window.localStorage.setItem('maibot-log-line-spacing', '999')
    window.localStorage.setItem('maibot-log-column-width-extra', '999')

    render(<LogViewerPage />)
    await user.click(screen.getByRole('button', { name: '筛选' }))

    // 行距上限 12px，列宽附加上限 96
    expect(screen.getByText('12px')).toBeInTheDocument()
    expect(screen.getByText('+96')).toBeInTheDocument()

    // 点击“大”号字体后写入 logFontSize
    await user.click(screen.getByRole('button', { name: '大' }))
    expect(window.localStorage.getItem('maibot-log-font-size')).toBe('base')
  })
})

describe('LogViewerPage 页签与提示', () => {
  it('首次访问显示切换提示，关闭后写入 localStorage 并不再显示', async () => {
    const user = userEvent.setup()
    window.localStorage.removeItem(HINT_DISMISSED_KEY)

    render(<LogViewerPage />)
    expect(screen.getByText('小提示')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '关闭提示' }))
    expect(screen.queryByText('小提示')).not.toBeInTheDocument()
    expect(window.localStorage.getItem(HINT_DISMISSED_KEY)).toBe('true')

    // 重新挂载后提示保持关闭
    cleanup()
    render(<LogViewerPage />)
    expect(screen.queryByText('小提示')).not.toBeInTheDocument()
  })

  it('切换到推理过程页签挂载推理页并卸载终端面板，可再切回', async () => {
    const user = userEvent.setup()

    render(<LogViewerPage />)
    expect(screen.getByText('暂无日志数据')).toBeInTheDocument()
    expect(screen.queryByTestId('reasoning-process-stub')).not.toBeInTheDocument()

    await user.click(screen.getByRole('tab', { name: '推理过程' }))
    const reasoningStub = screen.getByTestId('reasoning-process-stub')
    // 主文件把工具栏容器等编排参数透传给推理页
    expect(reasoningStub).toHaveAttribute('data-embedded', 'true')
    expect(reasoningStub).toHaveAttribute('data-toolbar-visible', 'true')
    expect(reasoningStub).toHaveAttribute('data-toolbar-container', 'log-terminal-toolbar')
    expect(reasoningStub).toHaveAttribute('data-topbar-actions', 'reasoning-topbar-actions')
    expect(screen.queryByText('暂无日志数据')).not.toBeInTheDocument()

    await user.click(screen.getByRole('tab', { name: '终端' }))
    expect(screen.getByText('暂无日志数据')).toBeInTheDocument()
    expect(screen.queryByTestId('reasoning-process-stub')).not.toBeInTheDocument()
  })

  it('ReasoningLogViewerPage 默认打开推理过程页签', () => {
    render(<ReasoningLogViewerPage />)

    const reasoningStub = screen.getByTestId('reasoning-process-stub')
    expect(reasoningStub).toHaveAttribute('data-toolbar-visible', 'true')
    expect(screen.queryByText('暂无日志数据')).not.toBeInTheDocument()
  })

  it('详细统计与终端和推理过程并列，并记住离开前选中的页签', async () => {
    const user = userEvent.setup()
    render(<LogViewerPage />)

    await user.click(screen.getByRole('tab', { name: '详细统计' }))
    expect(await screen.findByTestId('statistics-page-stub')).toBeInTheDocument()
    expect(window.localStorage.getItem(ACTIVE_TAB_KEY)).toBe('statistics')

    cleanup()
    render(<LogViewerPage />)
    expect(await screen.findByTestId('statistics-page-stub')).toBeInTheDocument()
  })

  it('旧详细统计地址默认进入麦麦日志的详细统计页签', async () => {
    render(<StatisticsLogViewerPage />)

    expect(await screen.findByTestId('statistics-page-stub')).toBeInTheDocument()
  })

  it('顶栏容器存在时通过 Portal 渲染页签切换器与测量节点', async () => {
    const topbarRoot = document.createElement('div')
    topbarRoot.id = 'log-viewer-topbar-tabs'
    document.body.appendChild(topbarRoot)

    try {
      render(<LogViewerPage />)

      // 顶栏根节点在 requestAnimationFrame 后才被发现
      await waitFor(() => {
        expect(topbarRoot.querySelector('[data-log-viewer-switcher="true"]')).not.toBeNull()
      })
      expect(within(topbarRoot).getByRole('tab', { name: '终端' })).toBeInTheDocument()
      expect(within(topbarRoot).getByRole('tab', { name: '推理过程' })).toBeInTheDocument()
      expect(within(topbarRoot).getByRole('tab', { name: '详细统计' })).toBeInTheDocument()
      // 用于紧凑模式测量的隐藏节点也随 Portal 渲染
      expect(topbarRoot.querySelector('[data-log-viewer-switcher-measure="true"]')).not.toBeNull()
    } finally {
      topbarRoot.remove()
    }
  })
})
