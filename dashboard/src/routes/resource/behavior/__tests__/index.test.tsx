import type { ReactNode } from 'react'

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { BehaviorLearningPage } from '../index'
import * as behaviorApi from '@/lib/behavior-api'
import type {
  BehaviorClusterItem,
  BehaviorGraphData,
  BehaviorPathDetail,
  BehaviorPathItem,
  BehaviorRetrievalDebugPayload,
  BehaviorSceneGraphNode,
  BehaviorTagNetworkNode,
} from '@/lib/behavior-api'

const toastMock = vi.hoisted(() => vi.fn())
vi.mock('@/hooks/use-toast', () => ({ useToast: () => ({ toast: toastMock }) }))

// ReactFlow 在 jsdom 无法布局；仍把 nodeTypes 跑一遍，锁住节点 kind 样式与边计数
vi.mock('reactflow', () => ({
  __esModule: true,
  default: ({
    nodes = [],
    edges = [],
    nodeTypes = {},
  }: {
    nodes?: Array<{ id: string; type?: string; data?: { kind?: string; label?: string; detail?: string } }>
    edges?: unknown[]
    nodeTypes?: Record<string, (props: { data: { kind?: string; label?: string; detail?: string } }) => ReactNode>
  }) => (
    <div data-testid="react-flow">
      {`nodes:${nodes.length},edges:${edges.length}`}
      {nodes.map((node) => {
        const Comp = node.type ? nodeTypes[node.type] : undefined
        if (!Comp) return null
        return (
          <div key={node.id} data-testid={`flow-node-${node.data?.kind ?? 'unknown'}`}>
            <Comp data={node.data ?? { kind: '', label: '', detail: '' }} />
          </div>
        )
      })}
    </div>
  ),
  Background: () => null,
  BackgroundVariant: { Dots: 'dots', Lines: 'lines', Cross: 'cross' },
  Controls: () => null,
  Handle: ({ type }: { type?: string }) => <span data-testid={`handle-${type ?? 'unknown'}`} />,
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

// Radix Select 在 jsdom 里会读 pointer capture；setup 未补，这里用普通函数避免 restoreMocks 清空
if (!Element.prototype.hasPointerCapture) {
  Element.prototype.hasPointerCapture = () => false
}
if (!Element.prototype.setPointerCapture) {
  Element.prototype.setPointerCapture = () => {}
}
if (!Element.prototype.releasePointerCapture) {
  Element.prototype.releasePointerCapture = () => {}
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

const chats = [
  { session_id: 'sess1', display_name: '会话1', cluster_count: 2, path_count: 5 },
  { session_id: '', display_name: '全局池', cluster_count: 1, path_count: 1 },
]
const paths = [
  {
    id: 1,
    session_id: 'sess1',
    chat_name: '会话1',
    action: '发送消息',
    outcome: '收到确认',
    score: 0.85,
    scene_cluster_id: 'sc1',
    scene_cluster_name: '信息查询',
    scene_cluster_tags: [],
    enabled: true,
    activation_count: 5,
    success_count: 3,
    failure_count: 0,
    count: 8,
    learning_type: 'self_feedback',
    update_time: '2025-01-01T00:00:00Z',
  },
]

const emptyGraph: BehaviorGraphData = {
  scene_cluster_network: { nodes: [], edges: [] },
  tag_network: { nodes: [], edges: [] },
}

function makePath(overrides: Partial<BehaviorPathItem> = {}): BehaviorPathItem {
  return {
    id: 1,
    session_id: 'sess1',
    chat_name: '会话1',
    scene_cluster_id: 10,
    scene_cluster_name: '信息查询',
    scene_cluster_tags: [],
    scene_cluster_source_count: 4,
    actor_type: 'maibot_self',
    learning_type: 'self_reflection',
    action: '发送消息',
    outcome: '收到确认',
    count: 8,
    activation_count: 5,
    success_count: 3,
    failure_count: 1,
    score: 0.85,
    enabled: true,
    last_active_time: '2025-01-02T00:00:00Z',
    last_feedback_time: '2025-01-03T00:00:00Z',
    update_time: '2025-01-01T00:00:00Z',
    ...overrides,
  }
}

function makeCluster(overrides: Partial<BehaviorClusterItem> = {}): BehaviorClusterItem {
  return {
    id: 1,
    name: '场景A',
    tags: [],
    source_count: 3,
    update_time: '2025-01-01T00:00:00Z',
    session_id: 'sess1',
    chat_name: '会话1',
    path_count: 2,
    enabled_path_count: 1,
    activation_count: 4,
    success_count: 2,
    failure_count: 1,
    observed_path_count: 1,
    self_reflection_path_count: 1,
    last_active_time: null,
    ...overrides,
  }
}

function makeSceneNode(
  id: number,
  overrides: Partial<BehaviorSceneGraphNode> = {}
): BehaviorSceneGraphNode {
  return {
    id,
    label: `场景簇${id}`,
    short_label: `簇${id}`,
    session_id: 'sess1',
    source_count: 2,
    score: 1,
    path_count: 3,
    activation_count: 2,
    success_count: 1,
    failure_count: 0,
    update_time: '2025-01-01T00:00:00Z',
    tags: [],
    ...overrides,
  }
}

function makeTagNode(
  id: string,
  kind: string,
  overrides: Partial<BehaviorTagNetworkNode> = {}
): BehaviorTagNetworkNode {
  return {
    id,
    kind,
    cluster_key: id,
    label: `${kind}标签`,
    aliases: [`${kind}-alias`],
    weight: 1.25,
    scene_count: 2,
    source_count: 3,
    ...overrides,
  }
}

function makeDetail(overrides: Partial<BehaviorPathDetail> = {}): BehaviorPathDetail {
  return {
    path: makePath(),
    scene_cluster: {
      id: 10,
      name: '信息查询',
      tags: [],
      source_count: 4,
      update_time: null,
    },
    evidence: [],
    feedback: [],
    nodes: [],
    edges: [],
    ...overrides,
  }
}

function makeDebugPayload(
  overrides: Partial<BehaviorRetrievalDebugPayload> = {}
): BehaviorRetrievalDebugPayload {
  return {
    retrieval_mode: 'tag_cluster_spread_1',
    descriptors: [],
    matched_clusters: [],
    candidate_scores: [],
    candidates: [],
    retrieval_debug: {},
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
  vi.mocked(behaviorApi.listBehaviorChats).mockResolvedValue({ success: true, data: chats } as never)
  vi.mocked(behaviorApi.listBehaviorPaths).mockResolvedValue({
    success: true,
    total: 1,
    page: 1,
    page_size: 20,
    data: paths,
  } as never)
  vi.mocked(behaviorApi.listBehaviorClusters).mockResolvedValue({
    success: true,
    total: 0,
    page: 1,
    page_size: 20,
    data: [],
  } as never)
  vi.mocked(behaviorApi.getBehaviorGraphData).mockResolvedValue({
    success: true,
    data: emptyGraph,
  } as never)
  vi.mocked(behaviorApi.getBehaviorPathDetail).mockResolvedValue({
    success: true,
    data: {
      path: paths[0],
      scene_cluster: { id: 1, name: '信息查询', tags: [], source_count: 0, update_time: null },
      evidence: [],
      feedback: [],
      nodes: [],
      edges: [],
    },
  } as never)
  vi.mocked(behaviorApi.debugBehaviorRetrieval).mockResolvedValue({
    success: true,
    data: makeDebugPayload(),
  } as never)
})

function renderPage() {
  render(<BehaviorLearningPage />, { wrapper: makeWrapper() })
}

async function renderReady() {
  renderPage()
  await waitFor(() => expect(behaviorApi.listBehaviorPaths).toHaveBeenCalled())
  await screen.findByRole('heading', { name: '行为学习' })
}

function getComboboxByText(text: string | RegExp) {
  const match = (value: string) =>
    typeof text === 'string' ? value.includes(text) : text.test(value)
  const found = screen.getAllByRole('combobox').find((element) => match(element.textContent ?? ''))
  if (!found) {
    throw new Error(`未找到包含「${String(text)}」的下拉框`)
  }
  return found
}

async function chooseOption(
  user: ReturnType<typeof userEvent.setup>,
  current: string | RegExp,
  next: string | RegExp
) {
  await user.click(getComboboxByText(current))
  await user.click(await screen.findByRole('option', { name: next }))
}

function stubFrames() {
  const queue: FrameRequestCallback[] = []
  vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => {
    queue.push(cb)
    return queue.length
  })
  vi.stubGlobal('cancelAnimationFrame', () => undefined)
  return {
    flush(count = 1) {
      for (let i = 0; i < count; i += 1) {
        const cb = queue.shift()
        if (!cb) return
        cb(i * 16)
      }
    },
  }
}

function installCanvasMetrics() {
  Object.defineProperty(HTMLCanvasElement.prototype, 'clientWidth', {
    configurable: true,
    get: () => 800,
  })
  Object.defineProperty(HTMLCanvasElement.prototype, 'clientHeight', {
    configurable: true,
    get: () => 680,
  })
  const originalRect = HTMLCanvasElement.prototype.getBoundingClientRect
  HTMLCanvasElement.prototype.getBoundingClientRect = function getBoundingClientRect() {
    return {
      x: 0,
      y: 0,
      left: 0,
      top: 0,
      right: 800,
      bottom: 680,
      width: 800,
      height: 680,
      toJSON: () => ({}),
    }
  }
  HTMLCanvasElement.prototype.setPointerCapture = function setPointerCapture() {}
  HTMLCanvasElement.prototype.releasePointerCapture = function releasePointerCapture() {}
  return () => {
    delete (HTMLCanvasElement.prototype as { clientWidth?: number }).clientWidth
    delete (HTMLCanvasElement.prototype as { clientHeight?: number }).clientHeight
    HTMLCanvasElement.prototype.getBoundingClientRect = originalRect
  }
}

function installArcRecorder() {
  let tx = 0
  let ty = 0
  let zoom = 1
  const arcs: Array<{ x: number; y: number }> = []
  const original = HTMLCanvasElement.prototype.getContext
  HTMLCanvasElement.prototype.getContext = function getContext(
    this: HTMLCanvasElement,
    id: string,
    options?: unknown
  ) {
    const ctx = original.call(this, id, options)
    if (!ctx) return ctx
    return {
      ...ctx,
      translate(x: number, y: number) {
        tx = x
        ty = y
      },
      scale(x: number) {
        zoom = x
      },
      arc(x: number, y: number) {
        arcs.push({ x, y })
      },
    }
  } as typeof original
  return {
    lastScreenPoint() {
      const arc = arcs.at(-1)
      if (!arc) return null
      return { clientX: arc.x * zoom + tx, clientY: arc.y * zoom + ty }
    },
    restore() {
      HTMLCanvasElement.prototype.getContext = original
    },
  }
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
      expect(behaviorApi.listBehaviorPaths).toHaveBeenCalledWith(
        expect.objectContaining({ search: '查询' })
      )
    )
  })
})

describe('经验路径', () => {
  it('空列表显示暂无行为经验路径', async () => {
    vi.mocked(behaviorApi.listBehaviorPaths).mockResolvedValue({
      success: true,
      total: 0,
      page: 1,
      page_size: 20,
      data: [],
    } as never)
    await renderReady()
    expect(await screen.findByText('暂无行为经验路径')).toBeInTheDocument()
    expect(screen.getByText(/全部聊天流 · 0 个场景簇 · 0 条经验路径/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /上一页/ })).toBeDisabled()
    expect(screen.getByRole('button', { name: /下一页/ })).toBeDisabled()
  })

  it('回车提交搜索并重置到第一页', async () => {
    const user = userEvent.setup()
    await renderReady()
    const input = screen.getByPlaceholderText('搜索场景簇 tag、行为、结果')
    await user.type(input, '插件{Enter}')
    await waitFor(() =>
      expect(behaviorApi.listBehaviorPaths).toHaveBeenCalledWith(
        expect.objectContaining({ search: '插件', page: 1 })
      )
    )
  })

  it('筛选、排序与分页带着新参数重拉路径', async () => {
    const user = userEvent.setup()
    vi.mocked(behaviorApi.listBehaviorPaths).mockResolvedValue({
      success: true,
      total: 21,
      page: 1,
      page_size: 20,
      data: paths,
    } as never)
    await renderReady()
    await chooseOption(user, '全部状态', '启用中')
    await chooseOption(user, '全部类型', '自身反馈')
    await chooseOption(user, '最近更新', '路径分数')
    await chooseOption(user, '降序', '升序')
    await waitFor(() =>
      expect(behaviorApi.listBehaviorPaths).toHaveBeenCalledWith(
        expect.objectContaining({
          enabled: 'true',
          learning_type: 'self_reflection',
          sort_by: 'score',
          sort_order: 'asc',
          page: 1,
        })
      )
    )
    expect(screen.getByText('1 / 2')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /下一页/ }))
    await waitFor(() =>
      expect(behaviorApi.listBehaviorPaths).toHaveBeenCalledWith(expect.objectContaining({ page: 2 }))
    )
  })

  it('切换聊天流与刷新会重拉聊天流、路径和场景簇', async () => {
    const user = userEvent.setup()
    await renderReady()
    await chooseOption(user, '全部聊天流', /会话1/)
    await waitFor(() =>
      expect(behaviorApi.listBehaviorPaths).toHaveBeenCalledWith(
        expect.objectContaining({ session_id: 'sess1', page: 1 })
      )
    )
    expect(screen.getByText(/会话1 · 1 个场景簇 · 1 条经验路径/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '刷新' }))
    await waitFor(() => expect(behaviorApi.listBehaviorChats).toHaveBeenCalledTimes(2))
    expect(behaviorApi.listBehaviorClusters).toHaveBeenCalled()
  })

  it('展开场景组后按路径类型展示，点选路径切到局部图谱', async () => {
    const user = userEvent.setup()
    const longAction = '排查启动失败'.repeat(20)
    const grouped = [
      makePath({
        id: 1,
        scene_cluster_tags: [
          { tag: 'domain:abc_0123456789abcdef', probability: 0.99 },
          { tag: 'plugin', display: '插件排障', probability: 0.8 },
        ],
        action: longAction,
      }),
      makePath({
        id: 2,
        actor_type: 'group_collective',
        learning_type: 'observed_behavior',
        action: '观察群聊',
        enabled: false,
        score: 0.2,
      }),
      makePath({
        id: 3,
        actor_type: 'other_user',
        learning_type: 'observed_behavior',
        action: '他人插话',
        scene_cluster_id: 11,
        scene_cluster_name: '',
        scene_cluster_tags: [],
      }),
    ]
    vi.mocked(behaviorApi.listBehaviorPaths).mockResolvedValue({
      success: true,
      total: 3,
      page: 1,
      page_size: 20,
      data: grouped,
    } as never)
    vi.mocked(behaviorApi.getBehaviorPathDetail).mockImplementation(async (id: number) => ({
      success: true,
      data: makeDetail({
        path: grouped.find((item) => item.id === id) ?? grouped[0],
        scene_cluster: {
          id: 10,
          name: '信息查询',
          tags: [{ tag: 'plugin', display: '插件排障', probability: 0.8 }],
          source_count: 4,
          update_time: null,
        },
      }),
    }))
    await renderReady()
    expect(await screen.findByText(/2 个场景簇 · 3 条经验路径/)).toBeInTheDocument()
    expect(
      screen.getByText((_, element) => element?.tagName === 'P' && (element.textContent ?? '').includes('插件排障'))
    ).toBeInTheDocument()
    expect(
      screen.getByText((_, element) => element?.tagName === 'P' && (element.textContent ?? '').includes('未命名场景簇'))
    ).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /插件排障/ }))
    expect(await screen.findByText('自身反馈')).toBeInTheDocument()
    expect(screen.getByText('群体观察')).toBeInTheDocument()
    expect(screen.getByText('启用')).toBeInTheDocument()
    expect(screen.getByText('停用')).toBeInTheDocument()
    expect(screen.getByText(new RegExp(`${longAction.slice(0, 110)}\\.\\.\\.`))).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /经验路径 #2/ }))
    expect(await screen.findByRole('tab', { name: '局部图谱' })).toHaveAttribute('data-state', 'active')
    expect(await screen.findByText('#2 会话1')).toBeInTheDocument()
    expect(screen.getByText('观察学习路径不记录正向/负向反馈。')).toBeInTheDocument()
  })
})

describe('场景簇浏览', () => {
  it('空列表显示暂无场景簇', async () => {
    const user = userEvent.setup()
    await renderReady()
    await user.click(screen.getByRole('tab', { name: '场景簇浏览' }))
    expect(await screen.findByText('暂无场景簇')).toBeInTheDocument()
    expect(behaviorApi.listBehaviorClusters).toHaveBeenCalledWith(
      expect.objectContaining({ session_id: 'all', page: 1, page_size: 20 })
    )
  })

  it('加载失败时对 Error 与非 Error 分别 toast', async () => {
    const user = userEvent.setup()
    vi.mocked(behaviorApi.listBehaviorClusters).mockRejectedValueOnce(new Error('网络断开'))
    await renderReady()
    await user.click(screen.getByRole('tab', { name: '场景簇浏览' }))
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({
          title: '加载场景簇失败',
          description: '网络断开',
          variant: 'destructive',
        })
      )
    )

    cleanup()
    toastMock.mockClear()
    vi.mocked(behaviorApi.listBehaviorClusters).mockRejectedValueOnce('boom')
    await renderReady()
    await user.click(screen.getByRole('tab', { name: '场景簇浏览' }))
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({
          title: '加载场景簇失败',
          description: '无法读取行为场景簇',
          variant: 'destructive',
        })
      )
    )
  })

  it('按路径数量分层、搜索与分页', async () => {
    const user = userEvent.setup()
    const clusters = [
      makeCluster({ id: 1, name: '空簇', path_count: 0, tags: [] }),
      makeCluster({
        id: 2,
        name: '单簇',
        path_count: 1,
        tags: [{ tag: 'need:xyz_0123456789abcdef', probability: 0.9 }],
      }),
      makeCluster({
        id: null,
        name: '',
        path_count: 3,
        chat_name: '',
        session_id: '',
        tags: [{ tag: 'help', display: '求助', probability: Number.NaN }],
      }),
      makeCluster({ id: 4, name: '中簇', path_count: 6 }),
      makeCluster({ id: 5, name: '大簇', path_count: 12, last_active_time: '2025-02-01T00:00:00Z' }),
    ]
    vi.mocked(behaviorApi.listBehaviorClusters).mockResolvedValue({
      success: true,
      total: 21,
      page: 1,
      page_size: 20,
      data: clusters,
    } as never)
    await renderReady()
    await user.click(screen.getByRole('tab', { name: '场景簇浏览' }))
    expect(await screen.findByText(/全部聊天流 · 21 个场景簇/)).toBeInTheDocument()
    expect(screen.getByText('全部场景簇')).toBeInTheDocument()
    expect(screen.getAllByText('暂无 tag 分布').length).toBeGreaterThan(0)
    expect(screen.getByRole('heading', { name: '求助' })).toBeInTheDocument()
    expect(screen.getByText('0%')).toBeInTheDocument()
    expect(screen.getByText('全局行为')).toBeInTheDocument()

    await chooseOption(user, '不分层', '按路径数量分层')
    expect(screen.getByText('未连接行为路径')).toBeInTheDocument()
    expect(screen.getByText('1 条路径')).toBeInTheDocument()
    expect(screen.getByText('2-4 条路径')).toBeInTheDocument()
    expect(screen.getByText('5-9 条路径')).toBeInTheDocument()
    expect(screen.getByText('10 条以上路径')).toBeInTheDocument()
    expect(screen.getAllByText(/个场景簇/).length).toBeGreaterThan(1)

    const search = screen.getByPlaceholderText('搜索场景簇 tag')
    await user.type(search, '求助{Enter}')
    await waitFor(() =>
      expect(behaviorApi.listBehaviorClusters).toHaveBeenCalledWith(
        expect.objectContaining({ search: '求助', page: 1 })
      )
    )
    await chooseOption(user, '最近更新', '学习样本')
    await waitFor(() =>
      expect(behaviorApi.listBehaviorClusters).toHaveBeenCalledWith(
        expect.objectContaining({ sort_by: 'source_count', page: 1 })
      )
    )
    expect(screen.getByText('1 / 2')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /下一页/ }))
    await waitFor(() =>
      expect(behaviorApi.listBehaviorClusters).toHaveBeenCalledWith(
        expect.objectContaining({ page: 2 })
      )
    )
  })
})

describe('网络图谱', () => {
  it('空图谱显示暂无图谱数据', async () => {
    const user = userEvent.setup()
    await renderReady()
    await user.click(screen.getByRole('tab', { name: '场景簇图谱' }))
    expect(await screen.findByText('暂无图谱数据')).toBeInTheDocument()
    await user.click(screen.getByRole('tab', { name: 'Tag簇网络' }))
    expect(await screen.findByText('暂无图谱数据')).toBeInTheDocument()
  })

  it('场景簇图谱渲染节点并支持调节、点选详情与画布手势', async () => {
    const user = userEvent.setup()
    const restoreMetrics = installCanvasMetrics()
    const recorder = installArcRecorder()
    const frames = stubFrames()
    vi.mocked(behaviorApi.getBehaviorGraphData).mockResolvedValue({
      success: true,
      data: {
        scene_cluster_network: {
          nodes: [
            makeSceneNode(1, {
              label: '插件启动失败排查现场',
              tags: [
                {
                  tag: 'plugin',
                  kind: 'domain',
                  cluster_key: 'd1',
                  display: '插件',
                  probability: 0.42,
                },
              ],
            }),
            makeSceneNode(2, { short_label: '', path_count: 1, source_count: 1, tags: [] }),
          ],
          edges: [
            {
              source: 1,
              target: 2,
              source_label: 'a',
              target_label: 'b',
              weight: 0.3,
              shared_tags: [],
            },
            {
              source: 99,
              target: 1,
              source_label: 'missing',
              target_label: 'a',
              weight: 1,
              shared_tags: [],
            },
          ],
        },
        tag_network: { nodes: [], edges: [] },
      },
    } as never)
    try {
      await renderReady()
      await user.click(screen.getByRole('tab', { name: '场景簇图谱' }))
      expect(await screen.findByRole('heading', { name: '场景簇图谱' })).toBeInTheDocument()
      expect(screen.getByText(/节点表示场景簇/)).toBeInTheDocument()
      expect(screen.getByText('2 节点')).toBeInTheDocument()
      expect(screen.getByText('1 连线')).toBeInTheDocument()

      await user.click(screen.getByRole('button', { name: '展开图谱调节' }))
      expect(screen.getByLabelText('搜索')).toHaveAttribute(
        'placeholder',
        '名称、ID、tag 或 session'
      )
      expect(screen.getByText(/最小重叠度 0.00/)).toBeInTheDocument()
      const weightSlider = document.querySelector('input[type="range"]') as HTMLInputElement
      fireEvent.change(weightSlider, { target: { value: '30' } })
      expect(await screen.findByText(/最小重叠度 0.30/)).toBeInTheDocument()
      await user.type(screen.getByLabelText('搜索'), '插件')
      await user.click(screen.getByRole('checkbox', { name: '显示标签' }))
      await user.click(screen.getByRole('checkbox', { name: '暂停' }))
      await user.click(screen.getByRole('button', { name: '重新布局' }))
      await user.click(screen.getByRole('button', { name: '重置' }))

      const canvas = document.querySelector('canvas')
      expect(canvas).toBeTruthy()
      frames.flush(1)
      fireEvent.wheel(canvas!, { deltaY: -80, clientX: 400, clientY: 300 })
      fireEvent.wheel(canvas!, { deltaY: 80, clientX: 400, clientY: 300 })
      fireEvent.pointerDown(canvas!, { clientX: 2, clientY: 2, pointerId: 1 })
      fireEvent.pointerMove(canvas!, { clientX: 24, clientY: 16, pointerId: 1 })
      fireEvent.pointerUp(canvas!, { clientX: 24, clientY: 16, pointerId: 1 })
      const point = recorder.lastScreenPoint()
      if (point) {
        fireEvent.pointerDown(canvas!, { ...point, pointerId: 2 })
        fireEvent.pointerMove(canvas!, { clientX: point.clientX + 4, clientY: point.clientY, pointerId: 2 })
        fireEvent.pointerUp(canvas!, { ...point, pointerId: 2 })
      }
      fireEvent.doubleClick(canvas!)
      if (point) {
        await waitFor(() => expect(screen.getByText('节点详情')).toBeInTheDocument())
        expect(screen.getByText(/场景簇 #/)).toBeInTheDocument()
        expect(screen.getByText('插件')).toBeInTheDocument()
        await user.click(screen.getByRole('button', { name: '关闭节点详情' }))
      }
    } finally {
      recorder.restore()
      restoreMetrics()
      vi.unstubAllGlobals()
    }
  })

  it('Tag簇网络只保留连线节点，并按 kind 着色过滤', async () => {
    const user = userEvent.setup()
    const restoreMetrics = installCanvasMetrics()
    const recorder = installArcRecorder()
    const frames = stubFrames()
    vi.mocked(behaviorApi.getBehaviorGraphData).mockResolvedValue({
      success: true,
      data: {
        scene_cluster_network: { nodes: [], edges: [] },
        tag_network: {
          nodes: [
            makeTagNode('need:1', 'need', { label: '安抚需求' }),
            makeTagNode('attitude:1', 'attitude', { label: '焦虑态度', aliases: [] }),
            makeTagNode('domain:1', 'domain', { label: '插件领域' }),
            makeTagNode('scene:1', 'scene', { label: '排障场景' }),
            makeTagNode('misc:1', 'misc', { label: '其它标签' }),
            makeTagNode('isolated:1', 'need', { label: '孤立需求' }),
          ],
          edges: [
            { source: 'need:1', target: 'attitude:1', weight: 4, count: 2 },
            { source: 'domain:1', target: 'scene:1', weight: 2, count: 1 },
            { source: 'need:1', target: 'misc:1', weight: 1, count: 1 },
          ],
        },
      },
    } as never)
    try {
      await renderReady()
      await user.click(screen.getByRole('tab', { name: 'Tag簇网络' }))
      expect(await screen.findByText('Tag簇分布网络')).toBeInTheDocument()
      // 孤立节点在构图阶段被丢掉，只剩 5 个有边的 tag
      expect(screen.getByText('5 节点')).toBeInTheDocument()
      expect(screen.getByText('3 连线')).toBeInTheDocument()

      await user.click(screen.getByRole('button', { name: '展开图谱调节' }))
      expect(screen.getByLabelText('搜索')).toHaveAttribute('placeholder', 'tag 或 cluster_key')
      expect(screen.getByText(/最小边权重 0/)).toBeInTheDocument()
      await user.click(screen.getByRole('checkbox', { name: '只显示参与场景分布的 tag 簇' }))
      await user.type(screen.getByLabelText('搜索'), '安抚')
      expect(await screen.findByText('1 节点')).toBeInTheDocument()

      const canvas = document.querySelector('canvas')
      expect(canvas).toBeTruthy()
      frames.flush(1)
      const point = recorder.lastScreenPoint()
      if (point) {
        fireEvent.pointerDown(canvas!, { ...point, pointerId: 1 })
        await waitFor(() => expect(screen.getByText('节点详情')).toBeInTheDocument())
        expect(screen.getByText(/场景簇/)).toBeInTheDocument()
      }
    } finally {
      recorder.restore()
      restoreMetrics()
      vi.unstubAllGlobals()
    }
  })
})

describe('检索调试', () => {
  it('未试跑时展示空态说明', async () => {
    const user = userEvent.setup()
    await renderReady()
    await user.click(screen.getByRole('tab', { name: '检索调试' }))
    expect(await screen.findByText(/输入场景画像后/)).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: '输入场景画像' })).toBeInTheDocument()
  })

  it('试跑检索按分隔符拆 tag，并渲染完整调试结果', async () => {
    const user = userEvent.setup()
    const path = makePath({
      scene_cluster_tags: [{ tag: 'plugin', display: '插件排障', probability: 0.7 }],
    })
    vi.mocked(behaviorApi.debugBehaviorRetrieval).mockResolvedValue({
      success: true,
      data: makeDebugPayload({
        retrieval_mode: 'tag_cluster_spread_1',
        input_mode: 'llm_scene_text',
        error: '部分描述符被跳过',
        scenario_profile: {
          summary: '群里在排查插件',
          confidence: 0.8,
          tag_clusters: [
            { kind: 'domain', tags: ['插件'] },
            { kind: 'need', tags: ['安抚'] },
            { kind: 'attitude', tags: ['焦虑'] },
            { kind: 'custom', tags: ['其它'] },
          ],
        },
        descriptors: [{ node_kind: 'need', name: '安抚', weight: 1 }],
        matched_clusters: [
          {
            cluster_id: 9,
            name: '命中簇',
            score: 0.77,
            tags: [{ tag: 'plugin', display: '插件排障', probability: 0.5 }],
            source_count: 3,
          },
        ],
        candidate_scores: [{ behavior_id: 1, score: 0.91 }],
        candidates: [
          { behavior_id: 1, score: 0.91, path },
          { behavior_id: 2, score: 0.1, path: null },
        ],
        retrieval_debug: {
          direct: { direct_tag_count: 2, cluster_count: 1, hop_counts: { '0': 2 } },
          spread: {
            direct_tag_count: 2,
            expanded_tag_count: 5,
            total_query_tag_count: 7,
            cluster_count: 3,
            hop_counts: { '1': 4 },
          },
          direct_top_score: 0.88,
          direct_locked: true,
          direct_lock_threshold: 0.7,
          locked_direct_spread_factor: 0.3,
        },
      }),
    } as never)
    await renderReady()
    await user.click(screen.getByRole('tab', { name: '检索调试' }))
    await user.type(screen.getByPlaceholderText(/例如：群里有人焦虑/), '群里有人在问启动失败')
    const tagInputs = screen.getAllByPlaceholderText('用逗号分隔')
    await user.type(tagInputs[0], '插件，配置、排查,启动')
    await user.type(tagInputs[1], '安抚')
    await user.type(tagInputs[2], '焦虑, 紧张')
    await user.click(screen.getByRole('button', { name: '试跑检索' }))

    await waitFor(() =>
      expect(behaviorApi.debugBehaviorRetrieval).toHaveBeenCalledWith({
        session_id: undefined,
        include_global: true,
        retrieval_mode: 'tag_cluster_spread_1',
        scene_text: '群里有人在问启动失败',
        tag_clusters: [
          { tag_name: '插件', tag_aliases: [] },
          { tag_name: '配置', tag_aliases: [] },
          { tag_name: '排查', tag_aliases: [] },
          { tag_name: '启动', tag_aliases: [] },
        ],
        need: { tag_name: '安抚', tag_aliases: [] },
        other_traits: [
          { tag_name: '焦虑', tag_aliases: [] },
          { tag_name: '紧张', tag_aliases: [] },
        ],
        max_count: 20,
      })
    )
    expect(await screen.findByText('部分描述符被跳过')).toBeInTheDocument()
    expect(screen.getByText('LLM 场景画像')).toBeInTheDocument()
    expect(screen.getByText('群里在排查插件')).toBeInTheDocument()
    expect(screen.getByText(/领域：插件/)).toBeInTheDocument()
    expect(screen.getByText(/需求：安抚/)).toBeInTheDocument()
    expect(screen.getByText(/他人特点\/态度：焦虑/)).toBeInTheDocument()
    expect(screen.getByText(/custom：其它/)).toBeInTheDocument()
    expect(screen.getByText('Tag 簇一跳扩散')).toBeInTheDocument()
    expect(screen.getByText('#9')).toBeInTheDocument()
    expect(screen.getByText('直接：2')).toBeInTheDocument()
    expect(screen.getByText('1 跳：4')).toBeInTheDocument()
    expect(screen.getByText('是')).toBeInTheDocument()
    expect(screen.getByText('#1')).toBeInTheDocument()
    expect(screen.getByText(/行为：发送消息/)).toBeInTheDocument()
    expect(screen.getByText('路径已不存在')).toBeInTheDocument()
  })

  it('指定会话与全局行为分别组装 session_id / include_global', async () => {
    const user = userEvent.setup()
    await renderReady()
    await chooseOption(user, '全部聊天流', /会话1/)
    await user.click(screen.getByRole('tab', { name: '检索调试' }))
    await user.click(screen.getByRole('button', { name: '试跑检索' }))
    await waitFor(() =>
      expect(behaviorApi.debugBehaviorRetrieval).toHaveBeenCalledWith(
        expect.objectContaining({
          session_id: 'sess1',
          include_global: false,
          need: { tag_name: '', tag_aliases: [] },
          tag_clusters: [],
          other_traits: [],
        })
      )
    )

    await user.click(screen.getByRole('tab', { name: '经验路径' }))
    await chooseOption(user, '会话1', /全局池/)
    await user.click(screen.getByRole('tab', { name: '检索调试' }))
    await user.click(screen.getByRole('button', { name: '试跑检索' }))
    await waitFor(() =>
      expect(behaviorApi.debugBehaviorRetrieval).toHaveBeenCalledWith(
        expect.objectContaining({
          session_id: undefined,
          include_global: false,
        })
      )
    )
  })

  it('调试结果覆盖空候选、未知模式、手动画像与缺失阶段', async () => {
    const user = userEvent.setup()
    vi.mocked(behaviorApi.debugBehaviorRetrieval).mockResolvedValue({
      success: true,
      data: makeDebugPayload({
        retrieval_mode: 'weird_mode',
        input_mode: 'manual',
        scenario_profile: { summary: '', confidence: 0.1, tag_clusters: [] },
        retrieval_debug: {
          spread: { direct_tag_count: 0, cluster_count: 0 },
          direct_top_score: 0.12,
          direct_locked: false,
        },
      }),
    } as never)
    await renderReady()
    await user.click(screen.getByRole('tab', { name: '检索调试' }))
    await user.click(screen.getByRole('button', { name: '试跑检索' }))
    expect(await screen.findByText('手动场景画像')).toBeInTheDocument()
    expect(screen.getByText('没有生成 tag 簇')).toBeInTheDocument()
    expect(screen.getByText('weird_mode')).toBeInTheDocument()
    expect(screen.getByText('暂无数据')).toBeInTheDocument()
    expect(screen.getByText('没有命中候选')).toBeInTheDocument()
    expect(screen.getByText('当前检索模式未产生这部分调试信息')).toBeInTheDocument()
    expect(screen.getByText('否')).toBeInTheDocument()
  })

  it('另外两种检索模式标签与直接领域重叠', async () => {
    const user = userEvent.setup()
    vi.mocked(behaviorApi.debugBehaviorRetrieval)
      .mockResolvedValueOnce({
        success: true,
        data: makeDebugPayload({ retrieval_mode: 'direct_domain_overlap' }),
      } as never)
      .mockResolvedValueOnce({
        success: true,
        data: makeDebugPayload({ retrieval_mode: 'tag_cluster_spread_2' }),
      } as never)
    await renderReady()
    await user.click(screen.getByRole('tab', { name: '检索调试' }))
    await user.click(screen.getByRole('button', { name: '试跑检索' }))
    expect(await screen.findByText('直接领域重叠')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '试跑检索' }))
    expect(await screen.findByText('Tag 簇两跳扩散')).toBeInTheDocument()
  })
})

describe('局部图谱', () => {
  it('未选中路径时提示先选择一条经验路径', async () => {
    const user = userEvent.setup()
    vi.mocked(behaviorApi.listBehaviorPaths).mockResolvedValue({
      success: true,
      total: 0,
      page: 1,
      page_size: 20,
      data: [],
    } as never)
    await renderReady()
    await user.click(screen.getByRole('tab', { name: '局部图谱' }))
    expect(await screen.findByText('先选择一条经验路径')).toBeInTheDocument()
  })

  it('详情加载中显示读取提示', async () => {
    const user = userEvent.setup()
    let resolveDetail: (value: { success: boolean; data: BehaviorPathDetail }) => void = () => undefined
    vi.mocked(behaviorApi.getBehaviorPathDetail).mockReturnValue(
      new Promise((resolve) => {
        resolveDetail = resolve
      })
    )
    await renderReady()
    await user.click(screen.getByRole('tab', { name: '局部图谱' }))
    expect(await screen.findByText('正在读取局部图谱')).toBeInTheDocument()
    resolveDetail({ success: true, data: makeDetail() })
    expect(await screen.findByText('#1 会话1')).toBeInTheDocument()
  })

  it('渲染分层节点、边标签、证据反馈与 kind 回退样式', async () => {
    const user = userEvent.setup()
    const longLabel = '这是一段需要被截断的超长场景描述文本用来覆盖 shortText'
    vi.mocked(behaviorApi.getBehaviorPathDetail).mockResolvedValue({
      success: true,
      data: makeDetail({
        path: makePath({ outcome: '', update_time: null }),
        scene_cluster: {
          id: 10,
          name: '',
          tags: [],
          source_count: 0,
          update_time: null,
        },
        evidence: [{ step: 1 }, { step: 2 }],
        feedback: [{ ok: true }],
        nodes: [
          { id: 1, kind: 'scene', label: longLabel, score: 1, source_count: 1 },
          { id: 2, kind: 'intent', label: '意图', score: 1, source_count: 1 },
          { id: 3, kind: 'phase', label: '阶段', score: 1, source_count: 1 },
          { id: 4, kind: 'domain', label: '领域', score: 1, source_count: 1 },
          { id: 5, kind: 'need', label: '需求', score: 1, source_count: 1 },
          { id: 6, kind: 'risk', label: '风险', score: 1, source_count: 1 },
          { id: 7, kind: 'misc', label: '杂项', score: 1, source_count: 1 },
          { id: 8, kind: 'action', label: '行动节点', score: 1, source_count: 1 },
          { id: 9, kind: 'outcome', label: '结果节点', score: 1, source_count: 1 },
        ],
        edges: [
          { id: 'e1', source: 'scene:1', target: 'action:8', kind: 'scene_action', weight: 2, count: 3 },
          {
            id: 'e2',
            source: 'action:8',
            target: 'outcome:9',
            kind: 'action_outcome',
            weight: 1.5,
            count: 2,
          },
          { id: 'e3', source: 'scene:1', target: 'intent:2', kind: 'co_occurs', weight: 0.5, count: 1 },
          { id: 'e4', source: 'domain:4', target: 'need:5', kind: 'related', weight: 1, count: 1 },
          { id: 'e5', source: 'missing', target: 'gone', kind: 'scene_action', weight: 1, count: 1 },
        ],
      }),
    } as never)
    await renderReady()
    await user.click(screen.getByRole('tab', { name: '局部图谱' }))
    // 非 action/outcome/path 的节点 id 一律 scene:id，因此 intent/domain 边会被过滤掉
    expect(await screen.findByTestId('react-flow')).toHaveTextContent('nodes:10,edges:2')
    expect(screen.getByTestId('flow-node-action')).toHaveTextContent('action')
    expect(screen.getByTestId('flow-node-outcome')).toHaveTextContent('outcome')
    expect(screen.getByTestId('flow-node-path')).toHaveTextContent('path')
    expect(screen.getByTestId('flow-node-misc')).toHaveTextContent('misc')
    expect(screen.getAllByText('scene_action').length).toBeGreaterThan(0)
    expect(screen.getAllByText('action_outcome').length).toBeGreaterThan(0)
    expect(screen.getByText(/权重 2.00 · 3 次/)).toBeInTheDocument()
    expect(screen.getAllByText('-').length).toBeGreaterThan(0)
    expect(screen.getByText(/"step": 2/)).toBeInTheDocument()
    expect(screen.getByText(/"ok": true/)).toBeInTheDocument()
    expect(screen.getByText('自身反馈')).toBeInTheDocument()
  })
})
