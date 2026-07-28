/**
 * ExpressionClusterBrowser 行为测试：
 * 覆盖统计区渲染 / 无索引空态 / 首个聚类自动激活并拉取成员 /
 * 搜索过滤与无匹配空态 / 切换聚类 / 成员查看回调 / 刷新重拉。
 */
import type { ReactNode } from 'react'

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { getExpressionClusterMembers, getExpressionClusters } from '@/lib/expression-api'

import { ExpressionClusterBrowser } from '../ExpressionClusterBrowser'

import type {
  ExpressionClusterListResponse,
  ExpressionClusterMember,
  ExpressionClusterSummary,
} from '@/types/expression'

vi.mock('@/lib/expression-api', () => ({
  getExpressionClusterMembers: vi.fn(),
  getExpressionClusters: vi.fn(),
}))

afterEach(() => {
  cleanup()
})

function makeMember(id: number, situation: string, style: string): ExpressionClusterMember {
  return {
    id,
    situation,
    style,
    count: id * 10,
    chat_id: 'chat-1',
    chat_name: '测试群',
    checked: false,
    modified_by: null,
  }
}

const memberA = makeMember(1, '被夸奖时', '害羞回应')
const memberB = makeMember(2, '被质疑时', '认真解释')
const memberC = makeMember(3, '独特情境', '独特风格')

function makeCluster(
  clusterId: number,
  members: ExpressionClusterMember[]
): ExpressionClusterSummary {
  return {
    embedding_profile_marker: 'profilemarker123456',
    cluster_id: clusterId,
    size: members.length,
    members,
  }
}

function makeClustersResponse(
  clusters: ExpressionClusterSummary[],
  indexExists = true
): ExpressionClusterListResponse {
  return {
    success: true,
    index_exists: indexExists,
    index_path: 'data/expression_selection/index.json',
    generated_at: null,
    updated_at: null,
    embedding_model: 'bge-m3',
    embedding_dimension: 1024,
    sample_count: 12,
    clusters,
  }
}

function renderBrowser(onOpenExpression = vi.fn()) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
  render(<ExpressionClusterBrowser onOpenExpression={onOpenExpression} />, { wrapper })
  return { onOpenExpression }
}

beforeEach(() => {
  vi.mocked(getExpressionClusters).mockResolvedValue(
    makeClustersResponse([makeCluster(1, [memberA, memberB]), makeCluster(2, [memberC])])
  )
  vi.mocked(getExpressionClusterMembers).mockResolvedValue({
    success: true,
    cluster: null,
    data: [memberA, memberB],
  })
})

describe('ExpressionClusterBrowser', () => {
  it('渲染统计区并自动激活首个聚类拉取成员', async () => {
    renderBrowser()

    // 统计区：表达数量 / 聚类数量 / Embedding 模型
    expect(await screen.findByText('12')).toBeInTheDocument()
    expect(screen.getByText('bge-m3')).toBeInTheDocument()

    // 首个聚类自动激活
    expect(await screen.findByText('聚类 1')).toBeInTheDocument()
    await waitFor(() => {
      expect(getExpressionClusterMembers).toHaveBeenCalledWith({
        cluster_id: 1,
        profile_marker: 'profilemarker123456',
      })
    })
    // 成员行渲染与成员数
    expect(await screen.findAllByText('被夸奖时')).not.toHaveLength(0)
    expect(screen.getByText('2 个成员')).toBeInTheDocument()
    // profile marker 截断为前 12 位
    expect(screen.getByText('profilemarke')).toBeInTheDocument()
  })

  it('无向量索引时展示空态且不选中聚类', async () => {
    vi.mocked(getExpressionClusters).mockResolvedValue(makeClustersResponse([], false))
    renderBrowser()

    expect(await screen.findByText('暂无向量索引')).toBeInTheDocument()
    expect(screen.getByText('未选择聚类')).toBeInTheDocument()
    expect(screen.getByText('0 个成员')).toBeInTheDocument()
    expect(getExpressionClusterMembers).not.toHaveBeenCalled()
  })

  it('搜索命中第二个聚类时过滤列表并自动切换激活聚类', async () => {
    const user = userEvent.setup()
    renderBrowser()
    await screen.findByText('聚类 1')

    await user.type(screen.getByPlaceholderText('搜索簇'), '独特')

    // 聚类 1 被过滤掉，激活项落到聚类 2
    expect(await screen.findByText('聚类 2')).toBeInTheDocument()
    await waitFor(() => {
      expect(getExpressionClusterMembers).toHaveBeenCalledWith({
        cluster_id: 2,
        profile_marker: 'profilemarker123456',
      })
    })
  })

  it('搜索无匹配时展示没有匹配的聚类', async () => {
    const user = userEvent.setup()
    renderBrowser()
    await screen.findByText('聚类 1')

    await user.type(screen.getByPlaceholderText('搜索簇'), '不存在的关键词')

    expect(await screen.findByText('没有匹配的聚类')).toBeInTheDocument()
    expect(screen.getByText('未选择聚类')).toBeInTheDocument()
  })

  it('点击聚类列表项切换激活聚类并重拉成员', async () => {
    const user = userEvent.setup()
    renderBrowser()
    await screen.findByText('聚类 1')

    // 左侧聚类按钮上渲染 cluster_id 文本 "2"
    await user.click(screen.getByRole('button', { name: /独特情境/ }))

    expect(await screen.findByText('聚类 2')).toBeInTheDocument()
    await waitFor(() => {
      expect(getExpressionClusterMembers).toHaveBeenCalledWith({
        cluster_id: 2,
        profile_marker: 'profilemarker123456',
      })
    })
  })

  it('点击成员的查看按钮回调 onOpenExpression', async () => {
    const user = userEvent.setup()
    const { onOpenExpression } = renderBrowser()
    await screen.findAllByText('被夸奖时')

    const viewButtons = await screen.findAllByRole('button', { name: '查看表达' })
    await user.click(viewButtons[0])

    expect(onOpenExpression).toHaveBeenCalledWith(1)
  })

  it('点击刷新重新拉取聚类列表', async () => {
    const user = userEvent.setup()
    renderBrowser()
    await screen.findByText('聚类 1')
    expect(getExpressionClusters).toHaveBeenCalledTimes(1)

    await user.click(screen.getByRole('button', { name: '刷新' }))

    await waitFor(() => {
      expect(getExpressionClusters).toHaveBeenCalledTimes(2)
    })
  })
})
