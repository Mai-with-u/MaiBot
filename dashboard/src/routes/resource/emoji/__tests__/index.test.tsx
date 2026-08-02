import type { ReactNode } from 'react'

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { EmojiManagementPage } from '../index'
import * as emojiApi from '@/lib/emoji-api'
import type { Emoji } from '@/types/emoji'

const toastMock = vi.hoisted(() => vi.fn())
vi.mock('@/hooks/use-toast', () => ({ useToast: () => ({ toast: toastMock }) }))

vi.mock('@/lib/emoji-api', () => ({
  banEmoji: vi.fn(),
  batchDeleteEmojis: vi.fn(),
  deleteEmoji: vi.fn(),
  getEmojiList: vi.fn(),
  getEmojiStats: vi.fn(),
  registerEmoji: vi.fn(),
}))

interface EmojiListStubProps {
  emojiList: Emoji[]
  total: number
  onToggleSelect: (id: number) => void
  onEdit: (emoji: Emoji) => void
  onViewDetail: (emoji: Emoji) => void
  onRegister: (emoji: Emoji) => void
  onBan: (emoji: Emoji) => void
  onDelete: (emoji: Emoji) => void
  onJumpToPage: () => void
  onJumpToPageChange: (value: string) => void
}

// 列表子组件桩：暴露主文件传入的回调，用于驱动删除/注册/封禁/多选编排
vi.mock('../EmojiList', () => ({
  EmojiList: (props: EmojiListStubProps) => (
    <div data-testid="emoji-list">
      <span data-testid="list-count">{`${props.emojiList.length}/${props.total}`}</span>
      {props.emojiList.map((emoji) => (
        <div key={emoji.id}>
          <button type="button" onClick={() => props.onToggleSelect(emoji.id)}>
            {`select-${emoji.id}`}
          </button>
          <button type="button" onClick={() => props.onDelete(emoji)}>{`del-${emoji.id}`}</button>
          <button type="button" onClick={() => props.onRegister(emoji)}>
            {`reg-${emoji.id}`}
          </button>
          <button type="button" onClick={() => props.onBan(emoji)}>{`ban-${emoji.id}`}</button>
          <button type="button" onClick={() => props.onEdit(emoji)}>{`edit-${emoji.id}`}</button>
          <button type="button" onClick={() => props.onViewDetail(emoji)}>
            {`detail-${emoji.id}`}
          </button>
        </div>
      ))}
      <button type="button" onClick={() => props.onJumpToPageChange('99')}>
        set-jump-99
      </button>
      <button type="button" onClick={() => props.onJumpToPage()}>
        do-jump
      </button>
    </div>
  ),
}))

vi.mock('../EmojiDialogs', () => ({
  EmojiDetailDialog: ({ open, emoji }: { open: boolean; emoji: Emoji | null }) =>
    open ? <div data-testid="detail-dialog">{emoji?.id}</div> : null,
  EmojiEditDialog: ({ open, emoji }: { open: boolean; emoji: Emoji | null }) =>
    open ? <div data-testid="edit-dialog">{emoji?.id}</div> : null,
  EmojiUploadDialog: ({ open, onSuccess }: { open: boolean; onSuccess: () => void }) =>
    open ? (
      <div data-testid="upload-dialog">
        <button type="button" onClick={onSuccess}>
          upload-success
        </button>
      </div>
    ) : null,
}))

vi.mock('../EmojiCacheMaintenancePanel', () => ({
  EmojiCacheMaintenancePanel: ({ onCacheChanged }: { onCacheChanged: () => void }) => (
    <button type="button" onClick={onCacheChanged}>
      cache-changed
    </button>
  ),
}))

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

function makeEmoji(id: number, overrides: Partial<Emoji> = {}): Emoji {
  return {
    id,
    full_path: `/data/emoji/${id}.png`,
    format: 'png',
    emoji_hash: `hash-${id}`,
    description: `表情${id}`,
    query_count: 0,
    is_registered: true,
    is_banned: false,
    status: 'adopted',
    emotion: '开心',
    record_time: 1_710_000_000,
    register_time: 1_710_000_100,
    usage_count: 3,
    last_used_time: 1_710_000_200,
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
  vi.mocked(emojiApi.getEmojiList).mockResolvedValue({
    success: true,
    total: 2,
    page: 1,
    page_size: 20,
    data: [makeEmoji(1), makeEmoji(2)],
  })
  vi.mocked(emojiApi.getEmojiStats).mockResolvedValue({
    success: true,
    data: {
      total: 10,
      registered: 4,
      banned: 2,
      unregistered: 6,
      known: 3,
      unknown: 1,
      adopted: 4,
      discarded: 2,
      formats: { png: 7, gif: 3 },
      top_used: [],
    },
  })
  vi.mocked(emojiApi.deleteEmoji).mockResolvedValue({ success: true, message: '已删除' })
  vi.mocked(emojiApi.registerEmoji).mockResolvedValue({ success: true, message: '已注册' })
  vi.mocked(emojiApi.banEmoji).mockResolvedValue({ success: true, message: '已封禁' })
  vi.mocked(emojiApi.batchDeleteEmojis).mockResolvedValue({
    success: true,
    message: '已删除 2 个',
    deleted_count: 2,
    failed_count: 0,
    failed_ids: [],
  })
})

async function renderPage() {
  render(<EmojiManagementPage />, { wrapper: makeWrapper() })
  await screen.findByTestId('emoji-list')
}

describe('EmojiManagementPage 特征化', () => {
  it('初始加载：以默认筛选拉取列表与统计，状态 Tabs 显示计数', async () => {
    await renderPage()
    await waitFor(() =>
      expect(emojiApi.getEmojiList).toHaveBeenCalledWith({
        page: 1,
        page_size: 20,
        status: 'adopted',
        format: undefined,
        search: undefined,
        sort_by: 'usage_count',
        sort_order: 'desc',
      })
    )
    expect(emojiApi.getEmojiStats).toHaveBeenCalled()
    expect(await screen.findByTestId('list-count')).toHaveTextContent('2/2')
    // 统计 Tabs：四个状态与各自计数
    const adoptedTab = await screen.findByRole('tab', { name: /据为己用/ })
    expect(adoptedTab).toHaveTextContent('4')
    expect(screen.getByRole('tab', { name: /不认识/ })).toHaveTextContent('1')
    // 命中总数文案（textContent 会向上传播到祖先，限定叶子节点）
    expect(
      screen.getByText(
        (_, element) =>
          element?.children.length === 0 &&
          element.textContent === '共 2 个表情包,当前第 1 页'
      )
    ).toBeInTheDocument()
  })

  it('切换状态 Tab 后按新状态重新拉取列表', async () => {
    const user = userEvent.setup()
    await renderPage()
    await user.click(await screen.findByRole('tab', { name: /^认识/ }))
    await waitFor(() =>
      expect(emojiApi.getEmojiList).toHaveBeenCalledWith(
        expect.objectContaining({ status: 'known' })
      )
    )
  })

  it('搜索输入经 300ms 防抖后带 search 参数请求，并显示命中文案', async () => {
    const user = userEvent.setup()
    await renderPage()
    await user.type(screen.getByPlaceholderText('搜索 tag、描述或哈希...'), '猫猫')
    await waitFor(() =>
      expect(emojiApi.getEmojiList).toHaveBeenCalledWith(
        expect.objectContaining({ search: '猫猫' })
      )
    )
    expect(
      screen.getByText(
        (_, element) =>
          element?.children.length === 0 &&
          element.textContent === '搜索“猫猫”命中 2 个表情包,当前第 1 页'
      )
    ).toBeInTheDocument()
    // 清空按钮出现并可清空输入
    await user.click(screen.getByRole('button', { name: '清空搜索' }))
    expect(screen.getByPlaceholderText('搜索 tag、描述或哈希...')).toHaveValue('')
  })

  it('单个删除：确认对话框中点删除后调用 deleteEmoji 并提示成功', async () => {
    const user = userEvent.setup()
    await renderPage()
    await user.click(await screen.findByText('del-1'))
    expect(await screen.findByText('确定要删除这个表情包吗?此操作无法撤销。')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '删除' }))
    await waitFor(() => expect(emojiApi.deleteEmoji).toHaveBeenCalledWith(1))
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({ title: '成功', description: '表情包已删除' })
      )
    )
  })

  it('快速注册与快速封禁分别调用对应 API', async () => {
    const user = userEvent.setup()
    await renderPage()
    await user.click(await screen.findByText('reg-1'))
    await waitFor(() => expect(emojiApi.registerEmoji).toHaveBeenCalledWith(1))
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({ description: '表情包已注册' })
      )
    )
    await user.click(await screen.findByText('ban-2'))
    await waitFor(() => expect(emojiApi.banEmoji).toHaveBeenCalledWith(2))
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({ description: '表情包已封禁' })
      )
    )
  })

  it('批量删除：选中两项后确认，携带选中 ID 调用批量接口', async () => {
    const user = userEvent.setup()
    await renderPage()
    await user.click(await screen.findByText('select-1'))
    await user.click(await screen.findByText('select-2'))
    expect(await screen.findByText('已选择 2 个表情包')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /批量删除/ }))
    expect(await screen.findByText('确认批量删除')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '确认删除' }))
    await waitFor(() => expect(emojiApi.batchDeleteEmojis).toHaveBeenCalledWith([1, 2]))
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({ title: '批量删除完成', description: '已删除 2 个' })
      )
    )
  })

  it('取消选择按钮清空选中状态', async () => {
    const user = userEvent.setup()
    await renderPage()
    await user.click(await screen.findByText('select-1'))
    expect(await screen.findByText('已选择 1 个表情包')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '取消选择' }))
    await waitFor(() => expect(screen.queryByText('已选择 1 个表情包')).not.toBeInTheDocument())
  })

  it('跳转页码超出范围时弹出无效页码提示', async () => {
    const user = userEvent.setup()
    await renderPage()
    await user.click(await screen.findByText('set-jump-99'))
    await user.click(await screen.findByText('do-jump'))
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({
          title: '无效的页码',
          description: '请输入1-1之间的页码',
          variant: 'destructive',
        })
      )
    )
  })

  it('点击新增打开上传对话框，上传成功回调触发列表刷新', async () => {
    const user = userEvent.setup()
    await renderPage()
    expect(screen.queryByTestId('upload-dialog')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /新增/ }))
    expect(await screen.findByTestId('upload-dialog')).toBeInTheDocument()
    const callsBefore = vi.mocked(emojiApi.getEmojiList).mock.calls.length
    await user.click(screen.getByText('upload-success'))
    await waitFor(() =>
      expect(vi.mocked(emojiApi.getEmojiList).mock.calls.length).toBeGreaterThan(callsBefore)
    )
  })

  it('详情与编辑回调分别打开对应对话框并带上选中表情包', async () => {
    const user = userEvent.setup()
    await renderPage()
    await user.click(await screen.findByText('detail-1'))
    expect(await screen.findByTestId('detail-dialog')).toHaveTextContent('1')
    await user.click(await screen.findByText('edit-2'))
    expect(await screen.findByTestId('edit-dialog')).toHaveTextContent('2')
  })

  it('缓存面板通知变更后统计接口被重新拉取', async () => {
    const user = userEvent.setup()
    await renderPage()
    await waitFor(() => expect(emojiApi.getEmojiStats).toHaveBeenCalled())
    const statsCallsBefore = vi.mocked(emojiApi.getEmojiStats).mock.calls.length
    await user.click(screen.getByText('cache-changed'))
    await waitFor(() =>
      expect(vi.mocked(emojiApi.getEmojiStats).mock.calls.length).toBeGreaterThan(statsCallsBefore)
    )
  })

  it('列表请求失败时显示错误信息与重试按钮', async () => {
    vi.mocked(emojiApi.getEmojiList).mockRejectedValue(new Error('列表炸了'))
    render(<EmojiManagementPage />, { wrapper: makeWrapper() })
    expect(await screen.findByText('列表炸了')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '重试' })).toBeInTheDocument()
    expect(screen.queryByTestId('emoji-list')).not.toBeInTheDocument()
  })
})
