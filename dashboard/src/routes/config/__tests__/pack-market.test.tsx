/**
 * PackMarketPage 特征化测试
 *
 * Pack 市场页：列表加载 / 搜索防抖 / 排序切换 / 分页 / 点赞 / 查看详情 / 空态与错误态。
 * pack-api 全量打桩；列表状态由 useDataList（react-query）真实驱动。
 */
import type { ReactNode } from 'react'
import type { ListPacksResponse, PackListItem } from '@/lib/pack-api'

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import PackMarketPage from '../pack-market'
import * as packApi from '@/lib/pack-api'

const toastMock = vi.fn()
const navigateMock = vi.fn()

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

// 页面直接 import { toast }，同时兜住 useToast 形态
vi.mock('@/hooks/use-toast', () => ({
  toast: (...args: unknown[]) => toastMock(...args),
  useToast: () => ({ toast: toastMock }),
}))
vi.mock('@tanstack/react-router', () => ({ useNavigate: () => navigateMock }))
vi.mock('@/lib/pack-api', () => ({
  listPacks: vi.fn(),
  togglePackLike: vi.fn(),
  checkPackLike: vi.fn(),
  getPackUserId: vi.fn(),
}))

function makePack(id: string, overrides: Partial<PackListItem> = {}): PackListItem {
  return {
    id,
    name: `模板-${id}`,
    description: `${id} 的描述`,
    author: '作者甲',
    version: '1.0.0',
    created_at: '2026-01-02T12:00:00Z',
    updated_at: '2026-01-02T12:00:00Z',
    status: 'approved',
    downloads: 42,
    likes: 5,
    tags: ['openai', 'chat'],
    provider_count: 1,
    model_count: 4,
    task_count: 3,
    ...overrides,
  }
}

function makeListResponse(packs: PackListItem[], total: number): ListPacksResponse {
  return {
    packs,
    total,
    page: 1,
    page_size: 12,
    total_pages: Math.max(1, Math.ceil(total / 12)),
  }
}

/** 默认两个 Pack：Alpha（likes=5）与 Beta（likes=8、5 个标签用于折叠断言） */
function defaultPacks(): PackListItem[] {
  return [
    makePack('pack-1', { name: 'Alpha 模板' }),
    makePack('pack-2', {
      name: 'Beta 模板',
      author: '作者乙',
      likes: 8,
      downloads: 17,
      created_at: '2026-03-04T12:00:00Z',
      tags: ['tag-a', 'tag-b', 'tag-c', 'tag-d', 'tag-e'],
    }),
  ]
}

function makeWrapper() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
}

beforeEach(() => {
  vi.mocked(packApi.getPackUserId).mockReturnValue('user-test')
  vi.mocked(packApi.listPacks).mockResolvedValue(makeListResponse(defaultPacks(), 2))
  vi.mocked(packApi.checkPackLike).mockResolvedValue(false)
  vi.mocked(packApi.togglePackLike).mockResolvedValue({ likes: 6, liked: true })
})

async function renderPage() {
  render(<PackMarketPage />, { wrapper: makeWrapper() })
  await screen.findByText('Alpha 模板')
}

describe('PackMarketPage 特征化', () => {
  it('初始加载按默认参数请求列表并渲染卡片与点赞状态', async () => {
    await renderPage()

    // 默认请求形状：approved / 第 1 页 / 每页 12 / 按下载量降序
    expect(packApi.listPacks).toHaveBeenCalledWith({
      status: 'approved',
      page: 1,
      page_size: 12,
      search: undefined,
      sort_by: 'downloads',
      sort_order: 'desc',
    })

    expect(screen.getByText('Beta 模板')).toBeInTheDocument()
    expect(screen.getByText('作者甲')).toBeInTheDocument()
    expect(screen.getAllByText('v1.0.0')).toHaveLength(2)
    expect(screen.getByText('2026年1月2日')).toBeInTheDocument()
    expect(screen.getByText(/共找到/)).toBeInTheDocument()

    // 点赞状态随当前页 Pack 旁路加载
    await waitFor(() => expect(packApi.checkPackLike).toHaveBeenCalledTimes(2))
    expect(packApi.checkPackLike).toHaveBeenCalledWith('pack-1', 'user-test')
    expect(packApi.checkPackLike).toHaveBeenCalledWith('pack-2', 'user-test')
  })

  it('标签超过 3 个时折叠为 +N', async () => {
    await renderPage()
    expect(screen.getByText('tag-a')).toBeInTheDocument()
    expect(screen.getByText('tag-c')).toBeInTheDocument()
    expect(screen.queryByText('tag-d')).not.toBeInTheDocument()
    expect(screen.getByText('+2')).toBeInTheDocument()
  })

  it('空列表显示暂无模板提示', async () => {
    vi.mocked(packApi.listPacks).mockResolvedValue(makeListResponse([], 0))
    render(<PackMarketPage />, { wrapper: makeWrapper() })
    expect(await screen.findByText('暂无模板')).toBeInTheDocument()
    expect(screen.getByText('当前还没有可用的配置模板')).toBeInTheDocument()
  })

  it('加载失败显示错误信息，点击重试重新请求', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    vi.mocked(packApi.listPacks).mockRejectedValue(new Error('后端不可用'))
    const user = userEvent.setup()
    render(<PackMarketPage />, { wrapper: makeWrapper() })

    expect(await screen.findByText('后端不可用')).toBeInTheDocument()

    const callsBefore = vi.mocked(packApi.listPacks).mock.calls.length
    await user.click(screen.getByRole('button', { name: '重试' }))
    await waitFor(() =>
      expect(vi.mocked(packApi.listPacks).mock.calls.length).toBeGreaterThan(callsBefore)
    )
  })

  it('点击查看详情跳转到对应 Pack 详情路由', async () => {
    const user = userEvent.setup()
    await renderPage()
    await user.click(screen.getAllByRole('button', { name: '查看详情' })[0])
    expect(navigateMock).toHaveBeenCalledWith({
      to: '/config/pack-market/$packId',
      params: { packId: 'pack-1' },
    })
  })

  it('点赞成功：调用 togglePackLike 并刷新列表、心形高亮', async () => {
    const user = userEvent.setup()
    await renderPage()
    await waitFor(() => expect(packApi.checkPackLike).toHaveBeenCalledTimes(2))

    // Alpha 模板的点赞按钮可及名称即 likes 数
    await user.click(screen.getByRole('button', { name: '5' }))
    await waitFor(() => expect(packApi.togglePackLike).toHaveBeenCalledWith('pack-1', 'user-test'))

    // liked=true 后按钮高亮；invalidate 触发列表重取
    await waitFor(() => expect(screen.getByRole('button', { name: '5' })).toHaveClass('text-red-500'))
    await waitFor(() =>
      expect(vi.mocked(packApi.listPacks).mock.calls.length).toBeGreaterThanOrEqual(2)
    )
  })

  it('点赞失败弹出破坏性 toast', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    vi.mocked(packApi.togglePackLike).mockRejectedValue(new Error('网络错误'))
    const user = userEvent.setup()
    await renderPage()

    await user.click(screen.getByRole('button', { name: '5' }))
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith({ title: '点赞失败', variant: 'destructive' })
    )
  })

  it('搜索输入防抖后携带关键字重新请求并重置页码', async () => {
    const user = userEvent.setup()
    await renderPage()

    await user.type(screen.getByPlaceholderText('搜索模板名称、描述...'), 'GPT')
    await waitFor(() =>
      expect(packApi.listPacks).toHaveBeenCalledWith(
        expect.objectContaining({ search: 'GPT', page: 1 })
      )
    )
  })

  it('切换排序方式后按新字段重新请求', async () => {
    const user = userEvent.setup()
    await renderPage()

    await user.click(screen.getByRole('button', { name: /下载最多/ }))
    await user.click(await screen.findByRole('menuitem', { name: '最新发布' }))
    await waitFor(() =>
      expect(packApi.listPacks).toHaveBeenCalledWith(
        expect.objectContaining({ sort_by: 'created_at', page: 1 })
      )
    )
  })

  it('多页数据点击下一页请求第 2 页', async () => {
    vi.mocked(packApi.listPacks).mockResolvedValue(makeListResponse(defaultPacks(), 30))
    const user = userEvent.setup()
    render(<PackMarketPage />, { wrapper: makeWrapper() })
    await screen.findByText('Alpha 模板')

    await user.click(screen.getByText('下一页'))
    await waitFor(() =>
      expect(packApi.listPacks).toHaveBeenCalledWith(expect.objectContaining({ page: 2 }))
    )
  })
})
