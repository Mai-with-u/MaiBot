import type { ComponentProps } from 'react'

import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { EmojiList } from '../EmojiList'
import type { Emoji } from '@/types/emoji'

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

// 缩略图组件内部会发起 fetch 轮询，这里桩成纯 img 以聚焦列表本体行为
vi.mock('@/components/emoji-thumbnail', () => ({
  EmojiThumbnail: ({ src, alt }: { src: string; alt?: string }) => (
    <img data-testid="emoji-thumb" src={src} alt={alt} />
  ),
}))

function makeEmoji(overrides: Partial<Emoji> = {}): Emoji {
  return {
    id: 1,
    full_path: '/data/emoji/a.png',
    format: 'png',
    emoji_hash: 'hash-1',
    description: '开心的猫猫',
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

type EmojiListProps = ComponentProps<typeof EmojiList>

function renderList(overrides: Partial<EmojiListProps> = {}) {
  const props: EmojiListProps = {
    emojiList: [makeEmoji()],
    loading: false,
    total: 1,
    page: 1,
    pageSize: 20,
    selectedIds: new Set<number>(),
    cardSize: 'medium',
    jumpToPage: '',
    onPageChange: vi.fn(),
    onJumpToPage: vi.fn(),
    onJumpToPageChange: vi.fn(),
    onToggleSelect: vi.fn(),
    onEdit: vi.fn(),
    onViewDetail: vi.fn(),
    onRegister: vi.fn(),
    onBan: vi.fn(),
    onDelete: vi.fn(),
    ...overrides,
  }
  render(<EmojiList {...props} />)
  return props
}

// 分页控件区域：按信息文案定位兄弟容器，按钮顺序为 [首页, 上一页, 跳转, 下一页, 末页]
function getPaginationButtons(infoText: string) {
  const info = screen.getByText(
    (_, element) => element?.tagName === 'DIV' && element.textContent === infoText
  )
  const controls = info.nextElementSibling as HTMLElement
  return within(controls).getAllByRole('button')
}

describe('EmojiList 空状态', () => {
  it('列表为空时显示“暂无数据”，且不渲染分页', () => {
    renderList({ emojiList: [], total: 0 })
    expect(screen.getByText('暂无数据')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '跳转' })).not.toBeInTheDocument()
  })
})

describe('EmojiList 卡片渲染', () => {
  it('渲染状态徽标、格式、使用次数与描述', () => {
    renderList({
      emojiList: [
        makeEmoji({ id: 1, status: 'adopted', description: '开心的猫猫', usage_count: 3 }),
        makeEmoji({ id: 2, status: 'discarded', description: '  ', format: 'gif' }),
      ],
      total: 2,
    })
    expect(screen.getByText('据为己用')).toBeInTheDocument()
    expect(screen.getByText('丢弃')).toBeInTheDocument()
    expect(screen.getByText('PNG')).toBeInTheDocument()
    expect(screen.getByText('GIF')).toBeInTheDocument()
    expect(screen.getAllByText('3次')).toHaveLength(2)
    expect(screen.getByText('开心的猫猫')).toBeInTheDocument()
    // 描述为空白时兜底显示“暂无描述”
    expect(screen.getByText('暂无描述')).toBeInTheDocument()
  })

  it('点击卡片与按下 Enter 均触发选中切换', () => {
    const props = renderList()
    const card = screen.getByText('开心的猫猫').closest('[role="button"]') as HTMLElement
    fireEvent.click(card)
    expect(props.onToggleSelect).toHaveBeenCalledWith(1)
    fireEvent.keyDown(card, { key: 'Enter' })
    expect(props.onToggleSelect).toHaveBeenCalledTimes(2)
  })

  it('编辑/详情/删除按钮触发对应回调且不冒泡到选中', () => {
    const props = renderList()
    const emoji = props.emojiList[0]
    fireEvent.click(screen.getByTitle('编辑'))
    expect(props.onEdit).toHaveBeenCalledWith(emoji)
    fireEvent.click(screen.getByTitle('详情'))
    expect(props.onViewDetail).toHaveBeenCalledWith(emoji)
    fireEvent.click(screen.getByTitle('删除'))
    expect(props.onDelete).toHaveBeenCalledWith(emoji)
    // stopPropagation 保证操作按钮不会误触发卡片选中
    expect(props.onToggleSelect).not.toHaveBeenCalled()
  })

  it('adopted 状态隐藏注册按钮，仅显示封禁', () => {
    const props = renderList({ emojiList: [makeEmoji({ status: 'adopted' })] })
    expect(screen.queryByTitle('注册')).not.toBeInTheDocument()
    fireEvent.click(screen.getByTitle('封禁'))
    expect(props.onBan).toHaveBeenCalledWith(props.emojiList[0])
  })

  it('discarded 状态隐藏封禁按钮，仅显示注册', () => {
    const props = renderList({ emojiList: [makeEmoji({ status: 'discarded' })] })
    expect(screen.queryByTitle('封禁')).not.toBeInTheDocument()
    fireEvent.click(screen.getByTitle('注册'))
    expect(props.onRegister).toHaveBeenCalledWith(props.emojiList[0])
  })
})

describe('EmojiList 分页', () => {
  it('中间页显示条数信息，并可向前后与首末页翻页', () => {
    const props = renderList({ total: 50, page: 2, pageSize: 20 })
    const [first, prev, , next, last] = getPaginationButtons('显示 21 到 40 条，共 50 条')
    fireEvent.click(first)
    expect(props.onPageChange).toHaveBeenLastCalledWith(1)
    fireEvent.click(prev)
    expect(props.onPageChange).toHaveBeenLastCalledWith(1)
    fireEvent.click(next)
    expect(props.onPageChange).toHaveBeenLastCalledWith(3)
    fireEvent.click(last)
    expect(props.onPageChange).toHaveBeenLastCalledWith(3)
  })

  it('第一页禁用首页/上一页，末页禁用下一页/末页', () => {
    renderList({ total: 21, page: 1, pageSize: 20 })
    const [first, prev, , next, last] = getPaginationButtons('显示 1 到 20 条，共 21 条')
    expect(first).toBeDisabled()
    expect(prev).toBeDisabled()
    expect(next).toBeEnabled()
    expect(last).toBeEnabled()
    cleanup()
    renderList({ total: 21, page: 2, pageSize: 20 })
    const [first2, prev2, , next2, last2] = getPaginationButtons('显示 21 到 21 条，共 21 条')
    expect(first2).toBeEnabled()
    expect(prev2).toBeEnabled()
    expect(next2).toBeDisabled()
    expect(last2).toBeDisabled()
  })

  it('跳转输入回传值、空值时按钮禁用、回车触发跳转', () => {
    const props = renderList({ total: 50, page: 1, pageSize: 20 })
    const input = screen.getByPlaceholderText('1')
    fireEvent.change(input, { target: { value: '2' } })
    expect(props.onJumpToPageChange).toHaveBeenCalledWith('2')
    // jumpToPage 为空字符串时按钮禁用
    expect(screen.getByRole('button', { name: '跳转' })).toBeDisabled()
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(props.onJumpToPage).toHaveBeenCalledTimes(1)
  })

  it('填入页码后跳转按钮可点击并触发回调', () => {
    const props = renderList({ total: 50, page: 1, pageSize: 20, jumpToPage: '3' })
    const jump = screen.getByRole('button', { name: '跳转' })
    expect(jump).toBeEnabled()
    fireEvent.click(jump)
    expect(props.onJumpToPage).toHaveBeenCalledTimes(1)
  })
})
