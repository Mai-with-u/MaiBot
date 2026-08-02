import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { PluginStats } from '../plugin-stats'

import {
  dislikePlugin,
  getPluginStats,
  getPluginUserState,
  likePlugin,
  ratePlugin,
  type PluginStatsData,
  type PluginUserState,
} from '@/lib/plugin-stats'

// toast 需要稳定引用，方便断言调用参数（mockReset 会在每个用例前清空调用记录）
const toastMock = vi.hoisted(() => vi.fn())

vi.mock('@/hooks/use-toast', () => ({
  useToast: () => ({ toast: toastMock }),
}))

vi.mock('@/lib/plugin-stats', () => ({
  getPluginStats: vi.fn(),
  getPluginUserState: vi.fn(),
  likePlugin: vi.fn(),
  dislikePlugin: vi.fn(),
  ratePlugin: vi.fn(),
}))

function makeStats(overrides: Partial<PluginStatsData> = {}): PluginStatsData {
  return {
    plugin_id: 'demo-plugin',
    likes: 3,
    dislikes: 1,
    downloads: 1234,
    rating: 4.2,
    rating_count: 9,
    ...overrides,
  }
}

function makeUserState(overrides: Partial<PluginUserState> = {}): PluginUserState {
  return {
    liked: false,
    disliked: false,
    rating: null,
    comment: '',
    ...overrides,
  }
}

/** 评分对话框里的五个星星按钮没有可访问名称，只能靠类名筛选 */
function getStarButtons(dialog: HTMLElement): HTMLElement[] {
  return within(dialog)
    .getAllByRole('button')
    .filter((button) => button.className === 'focus:outline-none')
}

describe('PluginStats', () => {
  beforeEach(() => {
    vi.mocked(getPluginStats).mockResolvedValue(makeStats())
    vi.mocked(getPluginUserState).mockResolvedValue(makeUserState())
  })

  it('加载期间显示两个占位横杠', () => {
    vi.mocked(getPluginStats).mockReturnValue(new Promise(() => {}))
    vi.mocked(getPluginUserState).mockReturnValue(new Promise(() => {}))

    render(<PluginStats pluginId="demo-plugin" />)

    expect(screen.getAllByText('-')).toHaveLength(2)
  })

  it('统计数据为 null 时不渲染任何内容', async () => {
    vi.mocked(getPluginStats).mockResolvedValue(null)

    const { container } = render(<PluginStats pluginId="demo-plugin" />)

    await waitFor(() => {
      expect(container).toBeEmptyDOMElement()
    })
  })

  it('compact 模式只展示下载量、评分与点赞数', async () => {
    render(<PluginStats pluginId="demo-plugin" compact />)

    expect(await screen.findByText((1234).toLocaleString())).toBeInTheDocument()
    expect(screen.getByText('4.2')).toBeInTheDocument()
    expect(screen.getByText('3')).toBeInTheDocument()
    // compact 模式下不渲染操作按钮
    expect(screen.queryByRole('button', { name: '点赞' })).not.toBeInTheDocument()
  })

  it('完整模式渲染统计卡片，并按用户状态标记已点赞', async () => {
    vi.mocked(getPluginUserState).mockResolvedValue(
      makeUserState({ liked: true, rating: 4, comment: '好用' })
    )

    render(<PluginStats pluginId="demo-plugin" />)

    expect(await screen.findByText('下载量')).toBeInTheDocument()
    expect(screen.getByText('9 条评分')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '已点赞' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '点踩' })).toBeInTheDocument()
    // 已有评分时按钮文案变为「修改评价」
    expect(screen.getByRole('button', { name: '修改评价' })).toBeInTheDocument()
  })

  it('渲染最近评价列表，无评分的条目显示「仅评论」', async () => {
    vi.mocked(getPluginStats).mockResolvedValue(
      makeStats({
        recent_ratings: [
          { user_id: 'u1', rating: null, comment: '非常好用', created_at: '2026-01-02T00:00:00Z' },
          { user_id: 'u2', rating: 5, created_at: '2026-01-03T00:00:00Z' },
        ],
      })
    )

    render(<PluginStats pluginId="demo-plugin" />)

    expect(await screen.findByText('最近评价')).toBeInTheDocument()
    expect(screen.getByText('仅评论')).toBeInTheDocument()
    expect(screen.getByText('非常好用')).toBeInTheDocument()
  })

  it('点赞成功后更新计数并弹出成功提示', async () => {
    vi.mocked(likePlugin).mockResolvedValue({
      success: true,
      liked: true,
      disliked: false,
      likes: 4,
      dislikes: 1,
    })

    render(<PluginStats pluginId="demo-plugin" />)

    fireEvent.click(await screen.findByRole('button', { name: '点赞' }))

    await waitFor(() => {
      expect(screen.getByRole('button', { name: '已点赞' })).toBeInTheDocument()
    })
    expect(likePlugin).toHaveBeenCalledWith('demo-plugin')
    expect(screen.getByText('4')).toBeInTheDocument()
    expect(toastMock).toHaveBeenCalledWith({ title: '已点赞', description: '感谢你的支持' })
  })

  it('点赞失败时弹出 destructive 提示', async () => {
    vi.mocked(likePlugin).mockResolvedValue({ success: false, error: '后端错误' })

    render(<PluginStats pluginId="demo-plugin" />)

    fireEvent.click(await screen.findByRole('button', { name: '点赞' }))

    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith({
        title: '点赞失败',
        description: '后端错误',
        variant: 'destructive',
      })
    })
  })

  it('点踩成功后按钮状态与计数同步更新', async () => {
    vi.mocked(dislikePlugin).mockResolvedValue({
      success: true,
      liked: false,
      disliked: true,
      likes: 3,
      dislikes: 2,
    })

    render(<PluginStats pluginId="demo-plugin" />)

    fireEvent.click(await screen.findByRole('button', { name: '点踩' }))

    await waitFor(() => {
      expect(screen.getByRole('button', { name: '已点踩' })).toBeInTheDocument()
    })
    expect(dislikePlugin).toHaveBeenCalledWith('demo-plugin')
    expect(screen.getByText('2')).toBeInTheDocument()
    expect(toastMock).toHaveBeenCalledWith({ title: '已点踩', description: '已更新你的反馈状态' })
  })

  it('对话框中默认状态下提交按钮禁用', async () => {
    render(<PluginStats pluginId="demo-plugin" />)

    fireEvent.click(await screen.findByRole('button', { name: '评价' }))

    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText('点击星星进行评分')).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: '提交评价' })).toBeDisabled()
  })

  it('选择星级提交后更新总评分并关闭对话框', async () => {
    vi.mocked(ratePlugin).mockResolvedValue({
      success: true,
      user_rating: 4,
      rating: 4.5,
      rating_count: 10,
    })

    render(<PluginStats pluginId="demo-plugin" />)

    fireEvent.click(await screen.findByRole('button', { name: '评价' }))
    const dialog = await screen.findByRole('dialog')

    const stars = getStarButtons(dialog)
    expect(stars).toHaveLength(5)
    fireEvent.click(stars[3])
    expect(within(dialog).getByText('不错')).toBeInTheDocument()

    fireEvent.click(within(dialog).getByRole('button', { name: '提交评价' }))

    await waitFor(() => {
      expect(ratePlugin).toHaveBeenCalledWith('demo-plugin', 4, undefined)
    })
    await waitFor(() => {
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    })
    expect(screen.getByText('4.5')).toBeInTheDocument()
    expect(screen.getByText('10 条评分')).toBeInTheDocument()
    expect(toastMock).toHaveBeenCalledWith({ title: '评价已更新', description: '你的评分或评论已保存' })
  })

  it('只填写评论时以 undefined 评分提交', async () => {
    vi.mocked(ratePlugin).mockResolvedValue({
      success: true,
      user_rating: null,
      user_comment: '很好用',
    })

    render(<PluginStats pluginId="demo-plugin" />)

    fireEvent.click(await screen.findByRole('button', { name: '评价' }))
    const dialog = await screen.findByRole('dialog')

    fireEvent.change(within(dialog).getByLabelText('评论'), { target: { value: '很好用' } })
    expect(within(dialog).getByText('3 / 500')).toBeInTheDocument()

    fireEvent.click(within(dialog).getByRole('button', { name: '提交评价' }))

    await waitFor(() => {
      expect(ratePlugin).toHaveBeenCalledWith('demo-plugin', undefined, '很好用')
    })
    // 评论保存后按钮文案变为「修改评价」
    await waitFor(() => {
      expect(screen.getByRole('button', { name: '修改评价' })).toBeInTheDocument()
    })
  })

  it('评分提交失败时弹出错误提示且对话框保持打开', async () => {
    vi.mocked(ratePlugin).mockResolvedValue({ success: false, error: '每天最多评分 3 次' })

    render(<PluginStats pluginId="demo-plugin" />)

    fireEvent.click(await screen.findByRole('button', { name: '评价' }))
    const dialog = await screen.findByRole('dialog')

    fireEvent.click(getStarButtons(dialog)[0])
    fireEvent.click(within(dialog).getByRole('button', { name: '提交评价' }))

    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith({
        title: '评价失败',
        description: '每天最多评分 3 次',
        variant: 'destructive',
      })
    })
    expect(screen.getByRole('dialog')).toBeInTheDocument()
  })
})
