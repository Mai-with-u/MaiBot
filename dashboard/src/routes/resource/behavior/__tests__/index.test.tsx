import type { ReactNode } from 'react'

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import { BehaviorLearningPage } from '../index'
import * as behaviorApi from '@/lib/behavior-api'

vi.mock('@/hooks/use-toast', () => ({ useToast: () => ({ toast: vi.fn() }) }))

// ReactFlow（仅 graph tab 用）在 jsdom 无法渲染，stub 为占位
vi.mock('reactflow', () => ({
  __esModule: true,
  default: ({ nodes = [], edges = [] }: { nodes?: unknown[]; edges?: unknown[] }) => (
    <div data-testid="react-flow">{`nodes:${nodes.length},edges:${edges.length}`}</div>
  ),
  Background: () => null,
  Controls: () => null,
  Handle: () => null,
  Position: { Left: 'left', Right: 'right', Top: 'top', Bottom: 'bottom' },
  MarkerType: { ArrowClosed: 'arrowclosed' },
}))

vi.mock('@/lib/behavior-api', () => ({
  listBehaviorChats: vi.fn(),
  listBehaviorPaths: vi.fn(),
  listBehaviorClusters: vi.fn(),
  getBehaviorGraphData: vi.fn(),
  getBehaviorPathDetail: vi.fn(),
  debugBehaviorRetrieval: vi.fn(),
}))

beforeAll(() => {
  // 场景簇/Tag 网络用 Canvas 2D，jsdom 无该上下文，stub 之
  HTMLCanvasElement.prototype.getContext = vi.fn(() => ({
    setTransform: vi.fn(), clearRect: vi.fn(), save: vi.fn(), restore: vi.fn(),
    translate: vi.fn(), scale: vi.fn(), beginPath: vi.fn(), moveTo: vi.fn(),
    lineTo: vi.fn(), stroke: vi.fn(), fill: vi.fn(), arc: vi.fn(), fillRect: vi.fn(),
    fillText: vi.fn(), measureText: vi.fn(() => ({ width: 10 })), closePath: vi.fn(),
    createLinearGradient: vi.fn(() => ({ addColorStop: vi.fn() })),
  })) as never
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

const chats = [{ session_id: 'sess1', display_name: '会话1', cluster_count: 2, path_count: 5 }]
const paths = [
  {
    id: 1, session_id: 'sess1', chat_name: '会话1', action: '发送消息', outcome: '收到确认', score: 0.85,
    scene_cluster_id: 'sc1', scene_cluster_name: '信息查询', scene_cluster_tags: [],
    enabled: true, activation_count: 5, success_count: 3, failure_count: 0, count: 8,
    learning_type: 'self_feedback', update_time: '2025-01-01T00:00:00Z',
  },
]

function makeWrapper() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return ({ children }: { children: ReactNode }) => <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
}

beforeEach(() => {
  vi.mocked(behaviorApi.listBehaviorChats).mockResolvedValue({ success: true, data: chats } as never)
  vi.mocked(behaviorApi.listBehaviorPaths).mockResolvedValue({ success: true, total: 1, page: 1, page_size: 20, data: paths } as never)
  vi.mocked(behaviorApi.getBehaviorGraphData).mockResolvedValue({
    success: true,
    data: { scene_cluster_network: { nodes: [], edges: [] }, tag_network: { nodes: [], edges: [] } },
  } as never)
  vi.mocked(behaviorApi.getBehaviorPathDetail).mockResolvedValue({
    success: true,
    data: { path: paths[0], scene_cluster: null, evidence: [], feedback: [], nodes: [], edges: [] },
  } as never)
})

function renderPage() {
  render(<BehaviorLearningPage />, { wrapper: makeWrapper() })
}

describe('BehaviorLearningPage 特征化', () => {
  it('初始加载调用 listBehaviorChats + listBehaviorPaths 并渲染 tab', async () => {
    renderPage()
    await waitFor(() => expect(behaviorApi.listBehaviorChats).toHaveBeenCalled())
    await waitFor(() => expect(behaviorApi.listBehaviorPaths).toHaveBeenCalled())
    expect(await screen.findByRole('tab', { name: '经验路径' })).toBeInTheDocument()
  })

  it('切到场景簇图谱 tab 调用 getBehaviorGraphData', async () => {
    const user = userEvent.setup()
    renderPage()
    await screen.findByRole('tab', { name: '场景簇图谱' })
    await user.click(screen.getByRole('tab', { name: '场景簇图谱' }))
    await waitFor(() => expect(behaviorApi.getBehaviorGraphData).toHaveBeenCalled())
  })

  it('搜索后以 search 参数重新拉取路径', async () => {
    const user = userEvent.setup()
    renderPage()
    const input = await screen.findByPlaceholderText(/搜索场景簇/)
    await user.type(input, '查询')
    await user.click(screen.getByRole('button', { name: '搜索' }))
    await waitFor(() =>
      expect(behaviorApi.listBehaviorPaths).toHaveBeenCalledWith(expect.objectContaining({ search: '查询' })),
    )
  })
})
