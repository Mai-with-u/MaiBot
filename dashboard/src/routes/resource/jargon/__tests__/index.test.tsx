import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { JargonManagementPage } from '../index'
import * as jargonApi from '@/lib/jargon-api'

import type { ReactNode } from 'react'
import type { Jargon } from '@/types/jargon'
import type { JargonExportScope } from '../JargonDialogs'

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

// toast 断言用稳定引用的 spy（vi.mock 工厂被提升，必须用 vi.hoisted）
const toastMock = vi.hoisted(() => vi.fn())
vi.mock('@/hooks/use-toast', () => ({ useToast: () => ({ toast: toastMock }) }))

vi.mock('@/lib/jargon-api', () => ({
  batchDeleteJargons: vi.fn(),
  batchSetJargonStatus: vi.fn(),
  deleteJargon: vi.fn(),
  exportJargons: vi.fn(),
  getJargonChatList: vi.fn(),
  getJargonDetail: vi.fn(),
  getJargonList: vi.fn(),
  getJargonStats: vi.fn(),
}))

interface JargonListStubProps {
  jargons: Jargon[]
  total: number
  loading?: boolean
  page?: number
  pageSize?: number
  hideChatColumn?: boolean
  onEdit: (jargon: Jargon) => void
  onDelete: (jargon: Jargon) => void
  onToggleSelect: (id: number) => void
  onToggleSelectAll?: () => void
  onJumpToPage: (page: string) => void
  onPageSizeChange?: (pageSize: number) => void
}

// 子组件桩：暴露主文件传入的回调，用于驱动详情/删除/多选/跳页编排
vi.mock('../JargonList', () => ({
  JargonList: ({
    jargons,
    total,
    loading,
    page,
    pageSize,
    hideChatColumn,
    onEdit,
    onDelete,
    onToggleSelect,
    onToggleSelectAll,
    onJumpToPage,
    onPageSizeChange,
  }: JargonListStubProps) => (
    <div data-testid="jargon-list">
      <span data-testid="list-count">{`${jargons.length}/${total}`}</span>
      <span data-testid="list-loading">{String(Boolean(loading))}</span>
      <span data-testid="list-page">{`${page ?? ''}/${pageSize ?? ''}`}</span>
      <span data-testid="hide-chat">{String(hideChatColumn)}</span>
      <button type="button" onClick={() => onJumpToPage('99')}>
        jump-99
      </button>
      <button type="button" onClick={() => onJumpToPage('2')}>
        jump-2
      </button>
      <button type="button" onClick={() => onJumpToPage('abc')}>
        jump-abc
      </button>
      <button type="button" onClick={() => onPageSizeChange?.(1)}>
        page-size-1
      </button>
      <button type="button" onClick={() => onToggleSelectAll?.()}>
        toggle-all
      </button>
      {jargons.map((jargon) => (
        <div key={jargon.id}>
          <button type="button" onClick={() => onEdit(jargon)}>{`edit-${jargon.id}`}</button>
          <button type="button" onClick={() => onDelete(jargon)}>{`del-${jargon.id}`}</button>
          <button
            type="button"
            onClick={() => onToggleSelect(jargon.id)}
          >{`select-${jargon.id}`}</button>
        </div>
      ))}
    </div>
  ),
}))

// 对话框桩：只保留编排所需的最小交互面
vi.mock('../JargonDialogs', () => ({
  JargonDetailDialog: ({
    open,
    jargon,
    chatList,
    onChanged,
  }: {
    open: boolean
    jargon: Jargon | null
    chatList?: { session_id: string }[]
    onChanged: (jargon: Jargon) => void
  }) =>
    open && jargon ? (
      <div data-testid="detail-dialog">
        {jargon.content}
        <span data-testid="detail-chat-count">{chatList?.length ?? 0}</span>
        <button
          type="button"
          onClick={() => onChanged({ ...jargon, content: `${jargon.content}-changed` })}
        >
          detail-changed
        </button>
      </div>
    ) : null,
  JargonCreateDialog: ({
    open,
    onSuccess,
  }: {
    open: boolean
    onSuccess: () => void
  }) =>
    open ? (
      <div data-testid="create-dialog">
        <button type="button" onClick={() => onSuccess()}>
          create-success
        </button>
      </div>
    ) : null,
  JargonImportDialog: ({
    open,
    onSuccess,
  }: {
    open: boolean
    onSuccess: () => void
  }) =>
    open ? (
      <div data-testid="import-dialog">
        <button type="button" onClick={() => onSuccess()}>
          import-success
        </button>
      </div>
    ) : null,
  JargonExportDialog: ({
    open,
    scope,
    includeChatInfo,
    exporting,
    selectedCount,
    onExport,
    onScopeChange,
    onIncludeChatInfoChange,
  }: {
    open: boolean
    scope: JargonExportScope
    includeChatInfo: boolean
    exporting?: boolean
    selectedCount?: number
    onExport: (scope: JargonExportScope, includeChatInfo: boolean) => Promise<void>
    onScopeChange?: (scope: JargonExportScope) => void
    onIncludeChatInfoChange?: (include: boolean) => void
  }) =>
    open ? (
      <div data-testid="export-dialog">
        <span data-testid="export-scope">{scope}</span>
        <span data-testid="export-selected-count">{selectedCount ?? 0}</span>
        {exporting ? <span data-testid="exporting">导出中</span> : null}
        <button
          type="button"
          onClick={() => {
            void onExport(scope, includeChatInfo)
          }}
        >
          confirm-export
        </button>
        <button
          type="button"
          onClick={() => {
            void onExport('selected', includeChatInfo)
          }}
        >
          export-selected-scope
        </button>
        <button type="button" onClick={() => onScopeChange?.('selected')}>
          set-scope-selected
        </button>
        <button type="button" onClick={() => onIncludeChatInfoChange?.(true)}>
          set-include-chat
        </button>
      </div>
    ) : null,
  DeleteConfirmDialog: ({
    open,
    onConfirm,
    onOpenChange,
  }: {
    open: boolean
    onConfirm: () => void
    onOpenChange?: (open: boolean) => void
  }) => (
    <>
      {open ? (
        <>
          <button type="button" onClick={onConfirm}>
            confirm-delete
          </button>
          <button type="button" onClick={() => onOpenChange?.(false)}>
            cancel-delete
          </button>
        </>
      ) : null}
      <button type="button" onClick={onConfirm}>
        force-delete-confirm
      </button>
    </>
  ),
  BatchDeleteConfirmDialog: ({
    open,
    onConfirm,
    onOpenChange,
  }: {
    open: boolean
    onConfirm: () => void
    onOpenChange?: (open: boolean) => void
  }) =>
    open ? (
      <>
        <button type="button" onClick={onConfirm}>
          confirm-batch-delete
        </button>
        <button type="button" onClick={() => onOpenChange?.(false)}>
          cancel-batch-delete
        </button>
      </>
    ) : null,
}))

// 侧边聊天范围面板桩：条目渲染为按钮直接驱动 onItemSelect
vi.mock('@/components/chat-scope-filter-panel', () => ({
  ChatScopeFilterPanel: ({
    items,
    onItemSelect,
    emptyContent,
    collapsed,
    onCollapsedChange,
    collapseLabel,
    expandLabel,
  }: {
    items: { id: string; label: string }[]
    onItemSelect: (id: string) => void
    emptyContent?: ReactNode
    collapsed?: boolean
    onCollapsedChange?: (collapsed: boolean) => void
    collapseLabel?: string
    expandLabel?: string
  }) => (
    <div data-testid="scope-panel">
      <span data-testid="scope-collapsed">{String(Boolean(collapsed))}</span>
      {onCollapsedChange ? (
        <button type="button" onClick={() => onCollapsedChange(!collapsed)}>
          {collapsed ? expandLabel : collapseLabel}
        </button>
      ) : null}
      {items.map((item) => (
        <button type="button" key={item.id} onClick={() => onItemSelect(item.id)}>
          {`scope-${item.id}`}
        </button>
      ))}
      {items.length <= 1 ? emptyContent : null}
    </div>
  ),
}))

/** 构造一条完整的黑话数据 */
function makeJargon(id: number, content: string, overrides: Partial<Jargon> = {}): Jargon {
  return {
    id,
    content,
    meaning: `${content}的含义`,
    session_id: 'chat-1',
    session_ids: ['chat-1'],
    chat_name: '测试群',
    chat_names: ['测试群'],
    is_global: false,
    count: 5,
    is_jargon: true,
    is_legacy_empty_meaning: false,
    is_complete: false,
    created_by: 'AI',
    created_timestamp: '2026-01-01T00:00:00Z',
    updated_timestamp: '2026-01-02T00:00:00Z',
    ...overrides,
  }
}

function makeWrapper() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
}

beforeEach(() => {
  vi.mocked(jargonApi.getJargonList).mockResolvedValue({
    success: true,
    total: 2,
    page: 1,
    page_size: 20,
    data: [makeJargon(1, '词A'), makeJargon(2, '词B')],
  })
  vi.mocked(jargonApi.getJargonStats).mockResolvedValue({
    success: true,
    data: {
      total: 2,
      confirmed_jargon: 1,
      confirmed_not_jargon: 1,
      manual_jargon: 1,
      global_count: 1,
      complete_count: 1,
      chat_count: 1,
      top_chats: {},
    },
  })
  vi.mocked(jargonApi.getJargonChatList).mockResolvedValue({
    success: true,
    data: [
      {
        session_id: 'chat-1',
        chat_name: '测试群',
        platform: 'qq',
        account_id: null,
        is_group: true,
      },
    ],
  })
  vi.mocked(jargonApi.getJargonDetail).mockResolvedValue({
    success: true,
    data: makeJargon(1, '词A-详情'),
  })
  vi.mocked(jargonApi.deleteJargon).mockResolvedValue({
    success: true,
    message: 'ok',
    deleted_count: 1,
  })
  vi.mocked(jargonApi.batchDeleteJargons).mockResolvedValue({
    success: true,
    message: 'ok',
    deleted_count: 2,
  })
  vi.mocked(jargonApi.batchSetJargonStatus).mockResolvedValue({ success: true, message: 'ok' })
  vi.mocked(jargonApi.exportJargons).mockResolvedValue({
    success: true,
    version: 1,
    type: 'maibot.jargon.export',
    exported_at: '2026-07-27T00:00:00Z',
    include_chat_info: false,
    count: 2,
    jargons: [],
  })
})

async function renderPage() {
  render(<JargonManagementPage />, { wrapper: makeWrapper() })
  await screen.findByTestId('list-count')
  await waitFor(() => expect(screen.getByTestId('list-count')).toHaveTextContent('2/2'))
}

describe('JargonManagementPage 特征化', () => {
  it('初始加载拉取列表/统计/聊天列表，统计标签显示数量', async () => {
    await renderPage()
    expect(jargonApi.getJargonList).toHaveBeenCalledWith(
      expect.objectContaining({
        page: 1,
        page_size: 20,
        session_id: undefined,
        jargon_status: undefined,
        is_global: undefined,
      })
    )
    expect(jargonApi.getJargonStats).toHaveBeenCalled()
    // 侧边栏与表单聊天列表各取一份
    expect(jargonApi.getJargonChatList).toHaveBeenCalledTimes(2)
    expect(jargonApi.getJargonChatList).toHaveBeenCalledWith({ include_empty: true })
    const summaryMenu = await screen.findByRole('button', { name: '黑话分类：总数量 2' })
    expect(summaryMenu).toBeInTheDocument()
    await userEvent.setup().click(summaryMenu)
    expect(await screen.findByRole('menuitemradio', { name: '已确认黑话 1' })).toBeInTheDocument()
  })

  it('切换到已确认黑话分类：以 jargon_status 过滤重新拉取列表', async () => {
    const user = userEvent.setup()
    await renderPage()
    await user.click(screen.getByRole('button', { name: /黑话分类/ }))
    await user.click(await screen.findByRole('menuitemradio', { name: /已确认黑话/ }))
    await waitFor(() =>
      expect(jargonApi.getJargonList).toHaveBeenCalledWith(
        expect.objectContaining({ jargon_status: 'confirmed_jargon' })
      )
    )
  })

  it('选择聊天后带 session_id 查询并隐藏聊天列；切到全局标签重置聊天筛选', async () => {
    const user = userEvent.setup()
    await renderPage()
    expect(screen.getByTestId('hide-chat')).toHaveTextContent('false')

    await user.click(screen.getByText('scope-chat-1'))
    await waitFor(() =>
      expect(jargonApi.getJargonList).toHaveBeenLastCalledWith(
        expect.objectContaining({ session_id: 'chat-1' })
      )
    )
    expect(screen.getByTestId('hide-chat')).toHaveTextContent('true')

    // 切到全局黑话：chatId 被重置为 all，session_id 不再传，改传 is_global
    await user.click(screen.getByRole('button', { name: /黑话分类/ }))
    await user.click(await screen.findByRole('menuitemradio', { name: /全局黑话/ }))
    await waitFor(() =>
      expect(jargonApi.getJargonList).toHaveBeenLastCalledWith(
        expect.objectContaining({ session_id: undefined, is_global: true })
      )
    )
    expect(screen.getByTestId('hide-chat')).toHaveTextContent('true')
  })

  it('搜索输入经 300ms 防抖后带 search 参数查询', async () => {
    const user = userEvent.setup()
    await renderPage()
    await user.type(screen.getByPlaceholderText('搜索黑话内容...'), '缩写')
    await waitFor(() =>
      expect(jargonApi.getJargonList).toHaveBeenCalledWith(
        expect.objectContaining({ search: '缩写' })
      )
    )
  })

  it('单条删除：确认后调用 deleteJargon 并提示删除成功', async () => {
    const user = userEvent.setup()
    await renderPage()
    await user.click(screen.getByText('del-1'))
    await user.click(await screen.findByText('confirm-delete'))
    await waitFor(() => expect(jargonApi.deleteJargon).toHaveBeenCalledWith(1))
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({ title: '删除成功', description: '已删除黑话: 词A' })
      )
    )
  })

  it('批量删除：选中两项后确认调用 batchDeleteJargons', async () => {
    const user = userEvent.setup()
    await renderPage()
    await user.click(screen.getByText('select-1'))
    await user.click(screen.getByText('select-2'))
    expect(await screen.findByText('已选择 2 个')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /批量删除/ }))
    await user.click(await screen.findByText('confirm-batch-delete'))
    await waitFor(() => expect(jargonApi.batchDeleteJargons).toHaveBeenCalledWith([1, 2]))
  })

  it('批量标记为黑话/无黑话：以选中 id 与布尔值调用接口', async () => {
    const user = userEvent.setup()
    await renderPage()
    await user.click(screen.getByText('select-1'))
    await user.click(await screen.findByRole('button', { name: /标记为黑话/ }))
    await waitFor(() => expect(jargonApi.batchSetJargonStatus).toHaveBeenCalledWith([1], true))
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({ title: '操作成功', description: '已将 1 个词条设为黑话' })
      )
    )

    // 操作成功后选中被清空，重新选中再标记为无黑话
    await user.click(screen.getByText('select-2'))
    await user.click(await screen.findByRole('button', { name: /标记为无黑话/ }))
    await waitFor(() => expect(jargonApi.batchSetJargonStatus).toHaveBeenCalledWith([2], false))
  })

  it('导出（无选中）：默认全量导出并提示导出数量', async () => {
    const user = userEvent.setup()
    await renderPage()
    await user.click(screen.getByRole('button', { name: '导出黑话' }))
    await user.click(await screen.findByText('confirm-export'))
    await waitFor(() =>
      expect(jargonApi.exportJargons).toHaveBeenCalledWith({
        ids: undefined,
        include_chat_info: false,
      })
    )
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({ title: '导出成功', description: '已导出 2 个黑话' })
      )
    )
    // 导出成功后对话框关闭
    await waitFor(() => expect(screen.queryByText('confirm-export')).not.toBeInTheDocument())
  })

  it('导出失败：展示接口错误信息', async () => {
    const user = userEvent.setup()
    vi.mocked(jargonApi.exportJargons).mockRejectedValue(new Error('网络错误'))
    await renderPage()
    await user.click(screen.getByRole('button', { name: '导出黑话' }))
    await user.click(await screen.findByText('confirm-export'))
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({ title: '导出失败', description: '网络错误' })
      )
    )
  })

  it('查看详情：先展示列表数据，再用详情接口返回值替换', async () => {
    const user = userEvent.setup()
    await renderPage()
    await user.click(screen.getByText('edit-1'))
    await waitFor(() => expect(jargonApi.getJargonDetail).toHaveBeenCalledWith(1))
    expect(await screen.findByText('词A-详情')).toBeInTheDocument()
  })

  it('详情加载失败：提示加载详情失败', async () => {
    const user = userEvent.setup()
    vi.mocked(jargonApi.getJargonDetail).mockRejectedValue(new Error('记录不存在'))
    await renderPage()
    await user.click(screen.getByText('edit-1'))
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({ title: '加载详情失败', description: '记录不存在' })
      )
    )
    // 详情弹窗仍保留列表里的原始数据
    expect(screen.getByTestId('detail-dialog')).toHaveTextContent('词A')
  })

  it('无效页码跳转：超出范围时提示无效的页码', async () => {
    const user = userEvent.setup()
    await renderPage()
    await user.click(screen.getByText('jump-99'))
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({ title: '无效的页码', description: '请输入1-1之间的页码' })
    )
  })

  it('列表请求失败：显示错误信息与重试按钮，点击重试重新拉取', async () => {
    const user = userEvent.setup()
    vi.mocked(jargonApi.getJargonList).mockRejectedValue(new Error('获取黑话列表失败'))
    render(<JargonManagementPage />, { wrapper: makeWrapper() })
    expect(await screen.findByText('获取黑话列表失败')).toBeInTheDocument()
    vi.mocked(jargonApi.getJargonList).mockResolvedValue({
      success: true,
      total: 1,
      page: 1,
      page_size: 20,
      data: [makeJargon(1, '词A')],
    })
    await user.click(screen.getByRole('button', { name: '重试' }))
    expect(await screen.findByTestId('list-count')).toHaveTextContent('1/1')
  })

  it('无黑话/手动黑话/推断完成分类分别带 jargon_status 或 is_complete 查询', async () => {
    const user = userEvent.setup()
    await renderPage()
    await user.click(screen.getByRole('button', { name: /黑话分类/ }))
    await user.click(await screen.findByRole('menuitemradio', { name: /无黑话/ }))
    await waitFor(() =>
      expect(jargonApi.getJargonList).toHaveBeenCalledWith(
        expect.objectContaining({ jargon_status: 'confirmed_not_jargon' })
      )
    )

    await user.click(screen.getByRole('button', { name: /黑话分类/ }))
    await user.click(await screen.findByRole('menuitemradio', { name: /手动黑话/ }))
    await waitFor(() =>
      expect(jargonApi.getJargonList).toHaveBeenCalledWith(
        expect.objectContaining({ jargon_status: 'manual_jargon' })
      )
    )

    await user.click(screen.getByRole('button', { name: /黑话分类/ }))
    await user.click(await screen.findByRole('menuitemradio', { name: /推断完成/ }))
    await waitFor(() =>
      expect(jargonApi.getJargonList).toHaveBeenCalledWith(
        expect.objectContaining({ is_complete: true, jargon_status: undefined })
      )
    )
  })

  it('全局分类下再选聊天会重置为总数量并带 session_id', async () => {
    const user = userEvent.setup()
    await renderPage()
    await user.click(screen.getByRole('button', { name: /黑话分类/ }))
    await user.click(await screen.findByRole('menuitemradio', { name: /全局黑话/ }))
    await waitFor(() =>
      expect(jargonApi.getJargonList).toHaveBeenLastCalledWith(
        expect.objectContaining({ is_global: true, session_id: undefined })
      )
    )

    await user.click(screen.getByText('scope-chat-1'))
    await waitFor(() =>
      expect(jargonApi.getJargonList).toHaveBeenLastCalledWith(
        expect.objectContaining({ session_id: 'chat-1', is_global: undefined })
      )
    )
    expect(screen.getByRole('button', { name: /黑话分类：总数量/ })).toBeInTheDocument()
  })

  it('选中后导出默认 selected，包含聊天信息时下载 with-chat 文件', async () => {
    const user = userEvent.setup()
    let downloaded = ''
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (
      this: HTMLAnchorElement
    ) {
      downloaded = this.download
    })
    await renderPage()
    await user.click(screen.getByText('select-1'))
    await user.click(screen.getByText('select-2'))
    await user.click(screen.getByRole('button', { name: '导出黑话' }))
    expect(screen.getByTestId('export-scope')).toHaveTextContent('selected')
    expect(screen.getByTestId('export-selected-count')).toHaveTextContent('2')

    await user.click(screen.getByText('set-include-chat'))
    await user.click(screen.getByText('confirm-export'))
    await waitFor(() =>
      expect(jargonApi.exportJargons).toHaveBeenCalledWith({
        ids: [1, 2],
        include_chat_info: true,
      })
    )
    await waitFor(() => expect(downloaded).toBe('jargons-selected-with-chat.json'))
  })

  it('导出范围为 selected 但未选中时提示没有选中项目', async () => {
    const user = userEvent.setup()
    await renderPage()
    await user.click(screen.getByRole('button', { name: '导出黑话' }))
    await user.click(screen.getByText('export-selected-scope'))
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({
          title: '没有选中项目',
          description: '请先选择要导出的黑话',
          variant: 'destructive',
        })
      )
    )
    expect(jargonApi.exportJargons).not.toHaveBeenCalled()
  })

  it('导出失败非 Error 展示无法导出黑话', async () => {
    const user = userEvent.setup()
    vi.mocked(jargonApi.exportJargons).mockRejectedValue('boom')
    await renderPage()
    await user.click(screen.getByRole('button', { name: '导出黑话' }))
    await user.click(await screen.findByText('confirm-export'))
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({ title: '导出失败', description: '无法导出黑话' })
      )
    )
  })

  it('详情加载失败非 Error 展示无法加载黑话详情', async () => {
    const user = userEvent.setup()
    vi.mocked(jargonApi.getJargonDetail).mockRejectedValue('missing')
    await renderPage()
    await user.click(screen.getByText('edit-1'))
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({ title: '加载详情失败', description: '无法加载黑话详情' })
      )
    )
  })

  it('详情竞态：旧请求返回不覆盖新选中项', async () => {
    const user = userEvent.setup()
    let resolveSlow: (value: { success: true; data: Jargon }) => void = () => {}
    vi.mocked(jargonApi.getJargonDetail).mockImplementation((id: number) => {
      if (id === 1) {
        return new Promise((resolve) => {
          resolveSlow = resolve
        })
      }
      return Promise.resolve({ success: true, data: makeJargon(2, '词B-详情') })
    })
    await renderPage()
    await user.click(screen.getByText('edit-1'))
    await user.click(screen.getByText('edit-2'))
    expect(await screen.findByText('词B-详情')).toBeInTheDocument()
    resolveSlow({ success: true, data: makeJargon(1, '词A-慢') })
    await waitFor(() => {
      expect(screen.getByTestId('detail-dialog')).toHaveTextContent('词B-详情')
    })
    expect(screen.queryByText('词A-慢')).not.toBeInTheDocument()
  })

  it('改每页条数后有效页码跳转；非法页码仍提示', async () => {
    const user = userEvent.setup()
    await renderPage()
    await user.click(screen.getByText('page-size-1'))
    await waitFor(() =>
      expect(jargonApi.getJargonList).toHaveBeenCalledWith(expect.objectContaining({ page_size: 1 }))
    )
    await user.click(screen.getByText('jump-2'))
    await waitFor(() =>
      expect(jargonApi.getJargonList).toHaveBeenCalledWith(expect.objectContaining({ page: 2 }))
    )
    await user.click(screen.getByText('jump-abc'))
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({ title: '无效的页码', description: '请输入1-2之间的页码' })
    )
  })

  it('全选、取消选择，以及无确认目标时删除直接返回', async () => {
    const user = userEvent.setup()
    await renderPage()
    await user.click(screen.getByText('toggle-all'))
    expect(await screen.findByText('已选择 2 个')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '取消选择' }))
    expect(screen.queryByText('已选择 2 个')).not.toBeInTheDocument()

    await user.click(screen.getByText('force-delete-confirm'))
    expect(jargonApi.deleteJargon).not.toHaveBeenCalled()
  })

  it('打开新增/导入并在成功回调后刷新；导入会清空选中', async () => {
    const user = userEvent.setup()
    await renderPage()
    const listCalls = vi.mocked(jargonApi.getJargonList).mock.calls.length

    await user.click(screen.getByRole('button', { name: '新增黑话' }))
    expect(screen.getByTestId('create-dialog')).toBeInTheDocument()
    await user.click(screen.getByText('create-success'))
    await waitFor(() => expect(screen.queryByTestId('create-dialog')).not.toBeInTheDocument())
    await waitFor(() =>
      expect(vi.mocked(jargonApi.getJargonList).mock.calls.length).toBeGreaterThan(listCalls)
    )

    await user.click(screen.getByText('select-1'))
    expect(await screen.findByText('已选择 1 个')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '导入黑话' }))
    expect(screen.getByTestId('import-dialog')).toBeInTheDocument()
    await user.click(screen.getByText('import-success'))
    await waitFor(() => expect(screen.queryByText('已选择 1 个')).not.toBeInTheDocument())
  })

  it('详情 onChanged 更新当前词条；删除对话框可取消', async () => {
    const user = userEvent.setup()
    await renderPage()
    await user.click(screen.getByText('edit-1'))
    expect(await screen.findByText('词A-详情')).toBeInTheDocument()
    await user.click(screen.getByText('detail-changed'))
    expect(screen.getByTestId('detail-dialog')).toHaveTextContent('词A-详情-changed')

    await user.click(screen.getByText('del-1'))
    expect(await screen.findByText('confirm-delete')).toBeInTheDocument()
    await user.click(screen.getByText('cancel-delete'))
    expect(screen.queryByText('confirm-delete')).not.toBeInTheDocument()
    expect(jargonApi.deleteJargon).not.toHaveBeenCalled()
  })

  it('折叠范围列表；批量删除成功提示并可关闭确认框', async () => {
    const user = userEvent.setup()
    await renderPage()
    expect(screen.getByTestId('scope-collapsed')).toHaveTextContent('false')
    await user.click(screen.getByRole('button', { name: '折叠范围列表' }))
    expect(screen.getByTestId('scope-collapsed')).toHaveTextContent('true')
    expect(screen.getByRole('button', { name: '展开范围列表' })).toBeInTheDocument()

    await user.click(screen.getByText('select-1'))
    await user.click(screen.getByRole('button', { name: /批量删除/ }))
    expect(await screen.findByText('confirm-batch-delete')).toBeInTheDocument()
    await user.click(screen.getByText('cancel-batch-delete'))
    expect(screen.queryByText('confirm-batch-delete')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /批量删除/ }))
    await user.click(await screen.findByText('confirm-batch-delete'))
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({
          title: '批量删除成功',
          description: '已删除 1 个黑话',
        })
      )
    )
  })

  it('统计无 data 时数量回落为 0；初次加载把 loading 传给列表', async () => {
    vi.mocked(jargonApi.getJargonStats).mockResolvedValue({
      success: true,
      data: undefined as unknown as never,
    })
    vi.mocked(jargonApi.getJargonList).mockImplementation(() => new Promise(() => {}))
    render(<JargonManagementPage />, { wrapper: makeWrapper() })
    expect(await screen.findByTestId('list-loading')).toHaveTextContent('true')
    expect(await screen.findByRole('button', { name: '黑话分类：总数量 0' })).toBeInTheDocument()
  })
})
