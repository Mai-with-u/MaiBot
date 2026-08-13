import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { KnowledgeGraphPage } from '.'
import * as memoryApi from '@/lib/memory-api'

import type { GraphEdge, GraphNode } from './types'

const navigateMock = vi.hoisted(() => vi.fn())
const toastMock = vi.hoisted(() => vi.fn())

vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => navigateMock,
}))

vi.mock('@/hooks/use-toast', () => ({
  useToast: () => ({ toast: toastMock }),
}))

// reactflow 在 jsdom 中过重，只保留点击节点/边以驱动页面编排
vi.mock('./GraphVisualization', () => ({
  GraphVisualization: ({
    graphData,
    onNodeClick,
    onEdgeClick,
    loading,
  }: {
    graphData: { nodes: GraphNode[]; edges: GraphEdge[] }
    onNodeClick: (event: React.MouseEvent, node: { id: string }) => void
    onEdgeClick: (event: React.MouseEvent, edge: { source: string; target: string }) => void
    loading?: boolean
  }) => {
    if (loading) {
      return <div data-testid="graph-loading">图谱加载中</div>
    }
    return (
      <div data-testid="graph-visualization">
        <div>{`nodes:${graphData.nodes.length},edges:${graphData.edges.length}`}</div>
        {graphData.nodes.map((node) => (
          <button
            key={node.id}
            type="button"
            onClick={(event) => onNodeClick(event as never, { id: node.id })}
          >
            {`click-node-${node.id}`}
          </button>
        ))}
        {graphData.edges.map((edge) => (
          <button
            key={`${edge.source}->${edge.target}`}
            type="button"
            onClick={(event) =>
              onEdgeClick(event as never, { source: edge.source, target: edge.target })}
          >
            {`click-edge-${edge.source}-${edge.target}`}
          </button>
        ))}
        <button type="button" onClick={(event) => onNodeClick(event as never, { id: '' })}>
          click-empty-node
        </button>
        <button
          type="button"
          onClick={(event) => onEdgeClick(event as never, { source: '', target: '' })}
        >
          click-empty-edge
        </button>
        <button type="button" onClick={(event) => onNodeClick(event as never, { id: 'ghost' })}>
          click-missing-node
        </button>
      </div>
    )
  },
}))

vi.mock('@/components/memory/MemoryDeleteDialog', () => ({
  MemoryDeleteDialog: ({
    open,
    title,
    error,
    preview,
    result,
    onExecute,
    onRestore,
    onOpenChange,
  }: {
    open: boolean
    title?: string
    error?: string | null
    preview?: { mode?: string; item_count?: number } | null
    result?: { operation_id?: string } | null
    onExecute?: () => void
    onRestore?: () => void
    onOpenChange?: (open: boolean) => void
  }) => (
    open ? (
      <div data-testid="memory-delete-dialog">
        <div>{title}</div>
        <div>{`preview:${preview?.mode ?? 'none'}:${preview?.item_count ?? 0}`}</div>
        <div>{`result:${result?.operation_id ?? 'none'}`}</div>
        {error ? <div>{`delete-error:${error}`}</div> : null}
        <button type="button" onClick={onExecute}>确认删除</button>
        <button type="button" onClick={onRestore}>恢复本次删除</button>
        <button type="button" onClick={() => onOpenChange?.(false)}>关闭删除对话框</button>
      </div>
    ) : null
  ),
}))

vi.mock('@/lib/memory-api', () => ({
  getMemoryGraph: vi.fn(),
  getMemoryGraphSearch: vi.fn(),
  getMemoryGraphNodeDetail: vi.fn(),
  getMemoryGraphEdgeDetail: vi.fn(),
  getMemoryGraphParagraphDetail: vi.fn(),
  previewMemoryDelete: vi.fn(),
  executeMemoryDelete: vi.fn(),
  restoreMemoryDelete: vi.fn(),
}))

afterEach(() => {
  cleanup()
})

function makeGraphPayload(overrides: Record<string, unknown> = {}) {
  return {
    success: true,
    nodes: [
      { id: 'alpha', name: 'Alpha' },
      { id: 'beta', name: 'Beta' },
    ],
    edges: [
      {
        source: 'alpha',
        target: 'beta',
        weight: 1,
        predicates: ['关联'],
        relation_count: 1,
        evidence_count: 2,
        relation_hashes: ['rel-1'],
        label: '关联',
      },
    ],
    total_nodes: 2,
    total_edges: 1,
    ...overrides,
  }
}

function makeRelation(hash = 'rel-1') {
  return {
    hash,
    subject: 'alpha',
    predicate: '关联',
    object: 'beta',
    text: 'alpha 关联 beta',
    confidence: 0.9,
    paragraph_count: 1,
    paragraph_hashes: ['p-1'],
    source_paragraph: 'p-1',
  }
}

function makeParagraph(hash = 'p-1') {
  return {
    hash,
    content: 'Alpha 提到了 Beta',
    preview: 'Alpha 提到了 Beta',
    source: 'demo',
    entity_count: 2,
    relation_count: 1,
    entities: ['Alpha', 'Beta'],
    relations: ['alpha 关联 beta'],
    updated_at: 1_710_000_000,
  }
}

function makeEvidenceGraph() {
  return {
    nodes: [
      {
        id: 'entity:alpha',
        type: 'entity',
        content: 'Alpha',
        metadata: { entity_name: 'alpha' },
      },
      {
        id: 'relation:rel-1',
        type: 'relation',
        content: 'alpha 关联 beta',
        metadata: { hash: 'rel-1' },
      },
      {
        id: 'relation:rel-meta',
        type: 'relation',
        content: 'from metadata',
        metadata: {
          hash: 'rel-meta',
          subject: 'X',
          predicate: '认识',
          object: 'Y',
          confidence: 0.42,
          paragraph_count: 2,
          paragraph_hashes: ['p-x'],
        },
      },
      {
        id: 'relation:rel-empty',
        type: 'relation',
        content: 'no hash',
        metadata: { subject: 'X', predicate: '认识', object: 'Y' },
      },
      {
        id: 'paragraph:p-1',
        type: 'paragraph',
        content: 'Alpha 提到了 Beta',
        metadata: { hash: 'p-1' },
      },
      {
        id: 'paragraph:p-meta',
        type: 'paragraph',
        content: 'from metadata',
        metadata: {
          hash: 'p-meta',
          preview: '元数据段落预览',
          source: 'chat-log',
          updated_at: 1_710_000_100,
          entity_count: 1,
          relation_count: 1,
        },
      },
      {
        id: 'paragraph:p-empty',
        type: 'paragraph',
        content: 'no hash',
        metadata: { preview: '无 hash 段落' },
      },
    ],
    edges: [
      { source: 'paragraph:p-1', target: 'entity:alpha', kind: 'mentions', label: '提及', weight: 1 },
      { source: 'paragraph:p-1', target: 'relation:rel-1', kind: 'supports', label: '支撑', weight: 1 },
    ],
    focus_entities: ['alpha'],
  }
}

function makeNodeDetail(overrides: Record<string, unknown> = {}) {
  return {
    success: true,
    node: { id: 'alpha', type: 'entity', content: 'Alpha', hash: 'entity-1', appearance_count: 3 },
    relations: [makeRelation()],
    paragraphs: [makeParagraph()],
    evidence_graph: makeEvidenceGraph(),
    ...overrides,
  }
}

function makeEdgeDetail(overrides: Record<string, unknown> = {}) {
  return {
    success: true,
    edge: {
      source: 'alpha',
      target: 'beta',
      weight: 1,
      predicates: ['关联'],
      relation_count: 1,
      evidence_count: 1,
      relation_hashes: ['rel-1'],
      label: '关联',
    },
    relations: [makeRelation()],
    paragraphs: [makeParagraph()],
    evidence_graph: makeEvidenceGraph(),
    ...overrides,
  }
}

function makeParagraphDetail(overrides: Record<string, unknown> = {}) {
  return {
    success: true,
    paragraph: makeParagraph(),
    evidence_graph: makeEvidenceGraph(),
    ...overrides,
  }
}

function makeDeletePreview() {
  return {
    success: true,
    mode: 'mixed',
    selector: { entity_hashes: ['entity-1'] },
    counts: { entities: 1, relations: 1, paragraphs: 1 },
    sources: ['demo'],
    items: [{ item_type: 'entity', item_hash: 'entity-1', label: 'Alpha' }],
    item_count: 1,
    dry_run: true,
  }
}

function makeDeleteResult(overrides: Record<string, unknown> = {}) {
  return {
    success: true,
    mode: 'mixed',
    operation_id: 'del-1',
    counts: { entities: 1, relations: 1, paragraphs: 1 },
    sources: ['demo'],
    deleted_count: 3,
    deleted_entity_count: 1,
    deleted_relation_count: 1,
    deleted_paragraph_count: 1,
    deleted_source_count: 0,
    ...overrides,
  }
}

function mockDefaultApis() {
  vi.mocked(memoryApi.getMemoryGraph).mockResolvedValue(makeGraphPayload() as never)
  vi.mocked(memoryApi.getMemoryGraphSearch).mockResolvedValue({
    success: true,
    query: 'alpha',
    limit: 50,
    count: 0,
    items: [],
  })
  vi.mocked(memoryApi.getMemoryGraphNodeDetail).mockResolvedValue(makeNodeDetail() as never)
  vi.mocked(memoryApi.getMemoryGraphEdgeDetail).mockResolvedValue(makeEdgeDetail() as never)
  vi.mocked(memoryApi.getMemoryGraphParagraphDetail).mockResolvedValue(makeParagraphDetail() as never)
  vi.mocked(memoryApi.previewMemoryDelete).mockResolvedValue(makeDeletePreview() as never)
  vi.mocked(memoryApi.executeMemoryDelete).mockResolvedValue(makeDeleteResult() as never)
  vi.mocked(memoryApi.restoreMemoryDelete).mockResolvedValue({ success: true } as never)
}

async function renderLoadedPage(props: Parameters<typeof KnowledgeGraphPage>[0] = {}) {
  render(<KnowledgeGraphPage {...props} />)
  await waitFor(() => {
    expect(memoryApi.getMemoryGraph).toHaveBeenCalled()
  })
}

async function openNodeDialog(user: ReturnType<typeof userEvent.setup>) {
  await screen.findByTestId('graph-visualization')
  await user.click(screen.getByRole('button', { name: 'click-node-alpha' }))
  expect(await screen.findByRole('dialog', { name: '实体详情' })).toBeInTheDocument()
  await screen.findByText('alpha 关联 beta')
}

async function openEdgeDialog(user: ReturnType<typeof userEvent.setup>) {
  await screen.findByTestId('graph-visualization')
  await user.click(screen.getByRole('button', { name: 'click-edge-alpha-beta' }))
  expect(await screen.findByRole('dialog', { name: '关系详情' })).toBeInTheDocument()
  await screen.findByText('alpha 关联 beta')
}

async function switchToEvidence(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole('button', { name: '切到证据视图' }))
  await waitFor(() => {
    expect(screen.getByRole('tab', { name: '证据视图' })).toHaveAttribute('data-state', 'active')
  })
  await screen.findByTestId('graph-visualization')
}

// 删除桩渲染在页面树里，会被上层 Radix Dialog 标成 aria-hidden，只能用 hidden 查询再 fireEvent
function clickDeleteDialogButton(name: string) {
  const dialog = screen.getByTestId('memory-delete-dialog')
  fireEvent.click(within(dialog).getByRole('button', { name, hidden: true }))
}

beforeEach(() => {
  navigateMock.mockReset()
  toastMock.mockReset()
  mockDefaultApis()
})

describe('KnowledgeGraphPage 加载与空态', () => {
  it('首次静默加载成功后展示图谱统计，不弹出更新 toast', async () => {
    await renderLoadedPage()

    expect(await screen.findByText('长期记忆图谱')).toBeInTheDocument()
    expect(memoryApi.getMemoryGraph).toHaveBeenCalledWith(120)
    expect(screen.getByText(/总节点 2/)).toBeInTheDocument()
    expect(screen.getByText(/总关系 1/)).toBeInTheDocument()
    expect(screen.getByTestId('graph-visualization')).toHaveTextContent('nodes:2,edges:1')
    expect(toastMock).not.toHaveBeenCalledWith(
      expect.objectContaining({ title: '图谱已更新' }),
    )
  })

  it('节点或边字段缺失时回退为空数组并展示空图谱', async () => {
    vi.mocked(memoryApi.getMemoryGraph).mockResolvedValue({
      success: true,
      total_nodes: 0,
      total_edges: 0,
    } as never)

    await renderLoadedPage()

    expect(await screen.findByText('还没有可展示的长期记忆图谱')).toBeInTheDocument()
    expect(screen.getByText(/总节点 0/)).toBeInTheDocument()
  })

  it('加载失败时提示错误并保留空态', async () => {
    vi.mocked(memoryApi.getMemoryGraph).mockRejectedValue(new Error('graph down'))

    render(<KnowledgeGraphPage />)

    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith({
        title: '加载失败',
        description: 'graph down',
        variant: 'destructive',
      })
    })
    expect(await screen.findByText('还没有可展示的长期记忆图谱')).toBeInTheDocument()
  })

  it('加载失败且错误不是 Error 时使用未知错误文案', async () => {
    vi.mocked(memoryApi.getMemoryGraph).mockRejectedValue('boom')

    render(<KnowledgeGraphPage />)

    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith({
        title: '加载失败',
        description: '未知错误',
        variant: 'destructive',
      })
    })
  })

  it('刷新成功会弹出更新 toast；刷新失败保留当前图谱', async () => {
    const user = userEvent.setup()
    await renderLoadedPage()
    await screen.findByTestId('graph-visualization')

    vi.mocked(memoryApi.getMemoryGraph).mockResolvedValueOnce(makeGraphPayload({
      nodes: [{ id: 'gamma' }],
      edges: [],
      total_nodes: 3,
      total_edges: 1,
    }) as never)

    await user.click(screen.getByRole('button', { name: '刷新图谱' }))

    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith({
        title: '图谱已更新',
        description: '当前加载 1 个节点、0 条关系',
      })
    })
    expect(screen.getByTestId('graph-visualization')).toHaveTextContent('nodes:1,edges:0')

    vi.mocked(memoryApi.getMemoryGraph).mockRejectedValueOnce(new Error('refresh failed'))
    await user.click(screen.getByRole('button', { name: '刷新图谱' }))

    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith({
        title: '加载失败',
        description: 'refresh failed',
        variant: 'destructive',
      })
    })
    expect(screen.getByTestId('graph-visualization')).toHaveTextContent('nodes:1,edges:0')
  })

  it('空图谱可前往控制台；自定义 onOpenConsole 时不再导航', async () => {
    const user = userEvent.setup()
    vi.mocked(memoryApi.getMemoryGraph).mockResolvedValue(makeGraphPayload({
      nodes: [],
      edges: [],
      total_nodes: 0,
      total_edges: 0,
    }) as never)

    const onOpenConsole = vi.fn()
    await renderLoadedPage({ onOpenConsole })

    await user.click(screen.getByRole('button', { name: '前往长期记忆控制台' }))
    expect(onOpenConsole).toHaveBeenCalledTimes(1)
    expect(navigateMock).not.toHaveBeenCalled()
  })

  it('未传入 onOpenConsole 时打开控制台会导航到知识库页', async () => {
    const user = userEvent.setup()
    await renderLoadedPage()

    await user.click(screen.getByRole('button', { name: '打开控制台' }))
    expect(navigateMock).toHaveBeenCalledWith({ to: '/resource/knowledge-base' })
  })

  it('embedded 模式不渲染页面标题', async () => {
    await renderLoadedPage({ embedded: true })

    expect(screen.queryByRole('heading', { name: '长期记忆图谱' })).not.toBeInTheDocument()
    expect(await screen.findByTestId('graph-visualization')).toBeInTheDocument()
  })
})

describe('KnowledgeGraphPage 证据空态', () => {
  it('未选择节点时切换证据视图展示空态，并可返回实体图', async () => {
    const user = userEvent.setup()
    await renderLoadedPage()
    await screen.findByTestId('graph-visualization')

    await user.click(screen.getByRole('tab', { name: '证据视图' }))

    expect(await screen.findByText('证据视图还没有可展示的选择')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '刷新证据视图' })).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '返回实体关系图' }))
    expect(screen.getByRole('tab', { name: '实体关系图' })).toHaveAttribute('data-state', 'active')
    expect(screen.getByTestId('graph-visualization')).toHaveTextContent('nodes:2,edges:1')
  })

  it('已有节点详情但证据图为空时显示刷新证据视图', async () => {
    const user = userEvent.setup()
    vi.mocked(memoryApi.getMemoryGraphNodeDetail).mockResolvedValue(makeNodeDetail({
      evidence_graph: { nodes: [], edges: [], focus_entities: [] },
    }) as never)

    await renderLoadedPage()
    await openNodeDialog(user)
    await user.click(screen.getByRole('button', { name: '切到证据视图' }))

    expect(await screen.findByText('证据视图还没有可展示的选择')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '刷新证据视图' })).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '刷新证据视图' }))
    expect(screen.getByRole('tab', { name: '证据视图' })).toHaveAttribute('data-state', 'active')
  })
})

describe('KnowledgeGraphPage 搜索', () => {
  it('空检索词会重置筛选并恢复完整图谱', async () => {
    const user = userEvent.setup()
    vi.mocked(memoryApi.getMemoryGraphSearch).mockResolvedValue({
      success: true,
      query: 'alpha',
      limit: 50,
      count: 1,
      items: [
        {
          type: 'entity',
          title: 'Alpha',
          matched_field: 'name',
          matched_value: 'Alpha',
          entity_name: 'alpha',
          entity_hash: 'entity-1',
          appearance_count: 3,
        },
      ],
    })

    await renderLoadedPage()
    const input = await screen.findByPlaceholderText('搜索实体、关系、hash（后端全库）')
    await user.type(input, 'alpha')
    await user.click(screen.getByRole('button', { name: '搜索' }))
    expect(await screen.findByText('搜索词：alpha')).toBeInTheDocument()

    await user.clear(input)
    await user.click(screen.getByRole('button', { name: '搜索' }))

    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith({
        title: '已重置筛选',
        description: '当前显示 2 个节点、1 条关系',
      })
    })
    expect(screen.queryByText('搜索词：alpha')).not.toBeInTheDocument()
    expect(screen.getByTestId('graph-visualization')).toHaveTextContent('nodes:2,edges:1')
  })

  it('回车触发全库检索；未命中时展示空结果', async () => {
    const user = userEvent.setup()
    await renderLoadedPage()

    const input = await screen.findByPlaceholderText('搜索实体、关系、hash（后端全库）')
    await user.type(input, 'missing{Enter}')

    await waitFor(() => {
      expect(memoryApi.getMemoryGraphSearch).toHaveBeenCalledWith('missing', 50)
    })
    expect(await screen.findByText('未命中实体或关系。')).toBeInTheDocument()
    expect(toastMock).toHaveBeenCalledWith({
      title: '全库检索完成',
      description: '命中 0 条结果',
    })
  })

  it('后端 success=false 或抛错时回退本地筛选，并按谓词命中边', async () => {
    const user = userEvent.setup()
    vi.mocked(memoryApi.getMemoryGraphSearch)
      .mockResolvedValueOnce({
        success: false,
        query: '关联',
        limit: 50,
        count: 0,
        items: [],
        error: 'search disabled',
      })
      .mockRejectedValueOnce(new Error('search unavailable'))

    await renderLoadedPage()
    const input = await screen.findByPlaceholderText('搜索实体、关系、hash（后端全库）')
    await user.type(input, '关联')
    await user.click(screen.getByRole('button', { name: '搜索' }))

    expect(await screen.findByText('仅当前已加载范围')).toBeInTheDocument()
    expect(screen.getByTestId('graph-visualization')).toHaveTextContent('nodes:2,edges:1')
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({ title: '后端检索失败，已回退本地筛选' }),
    )

    await user.clear(input)
    await user.type(input, 'missing')
    await user.click(screen.getByRole('button', { name: '搜索' }))
    expect(await screen.findByText('还没有可展示的长期记忆图谱')).toBeInTheDocument()
  })

  it('回退后刷新会继续按已应用检索词过滤新图谱', async () => {
    const user = userEvent.setup()
    vi.mocked(memoryApi.getMemoryGraphSearch).mockRejectedValue(new Error('down'))

    await renderLoadedPage()
    const input = await screen.findByPlaceholderText('搜索实体、关系、hash（后端全库）')
    await user.type(input, 'alpha')
    await user.click(screen.getByRole('button', { name: '搜索' }))
    await screen.findByText('仅当前已加载范围')

    vi.mocked(memoryApi.getMemoryGraph).mockResolvedValueOnce(makeGraphPayload({
      nodes: [
        { id: 'alpha', name: 'Alpha' },
        { id: 'omega', name: 'Omega' },
      ],
      edges: [],
    }) as never)
    await user.click(screen.getByRole('button', { name: '刷新图谱' }))

    await waitFor(() => {
      expect(screen.getByTestId('graph-visualization')).toHaveTextContent('nodes:1,edges:0')
    })
  })

  it('点击实体检索结果进入证据视图；空名称结果不可定位', async () => {
    const user = userEvent.setup()
    vi.mocked(memoryApi.getMemoryGraphSearch).mockResolvedValue({
      success: true,
      query: 'alpha',
      limit: 50,
      count: 2,
      items: [
        {
          type: 'entity',
          title: '',
          matched_field: 'name',
          matched_value: '',
          entity_name: '   ',
        },
        {
          type: 'entity',
          title: 'Alpha',
          matched_field: 'name',
          matched_value: 'Alpha',
          entity_name: 'alpha',
          appearance_count: 3,
        },
      ],
    })

    await renderLoadedPage()
    const input = await screen.findByPlaceholderText('搜索实体、关系、hash（后端全库）')
    await user.type(input, 'alpha')
    await user.click(screen.getByRole('button', { name: '搜索' }))

    await user.click(screen.getByRole('button', { name: /无标题结果/ }))
    expect(memoryApi.getMemoryGraphNodeDetail).not.toHaveBeenCalled()

    await user.click(screen.getByRole('button', { name: /Alpha/ }))
    await waitFor(() => {
      expect(memoryApi.getMemoryGraphNodeDetail).toHaveBeenCalledWith('alpha')
    })
    expect(screen.getByRole('tab', { name: '证据视图' })).toHaveAttribute('data-state', 'active')
  })

  it('关系检索缺少 subject/object 时提示无法定位', async () => {
    const user = userEvent.setup()
    vi.mocked(memoryApi.getMemoryGraphSearch).mockResolvedValue({
      success: true,
      query: '关联',
      limit: 50,
      count: 1,
      items: [
        {
          type: 'relation',
          title: '残缺关系',
          matched_field: 'predicate',
          matched_value: '关联',
          subject: '',
          object: '',
        },
      ],
    })

    await renderLoadedPage()
    const input = await screen.findByPlaceholderText('搜索实体、关系、hash（后端全库）')
    await user.type(input, '关联')
    await user.click(screen.getByRole('button', { name: '搜索' }))
    await user.click(screen.getByRole('button', { name: /残缺关系/ }))

    expect(toastMock).toHaveBeenCalledWith({
      title: '结果缺少定位信息',
      description: '该关系记录没有可用的 subject/object，无法定位。',
      variant: 'destructive',
    })
    expect(memoryApi.getMemoryGraphEdgeDetail).not.toHaveBeenCalled()
  })

  it('点击完整关系检索结果会定位到证据视图', async () => {
    const user = userEvent.setup()
    vi.mocked(memoryApi.getMemoryGraphSearch).mockResolvedValue({
      success: true,
      query: '关联',
      limit: 50,
      count: 1,
      items: [
        {
          type: 'relation',
          title: 'alpha 关联 beta',
          matched_field: 'predicate',
          matched_value: '关联',
          subject: 'alpha',
          object: 'beta',
          confidence: 0.9,
        },
      ],
    })

    await renderLoadedPage()
    const input = await screen.findByPlaceholderText('搜索实体、关系、hash（后端全库）')
    await user.type(input, '关联')
    await user.click(screen.getByRole('button', { name: '搜索' }))
    await user.click(screen.getByRole('button', { name: /alpha 关联 beta/ }))

    await waitFor(() => {
      expect(memoryApi.getMemoryGraphEdgeDetail).toHaveBeenCalledWith('alpha', 'beta')
    })
    expect(screen.getByRole('tab', { name: '证据视图' })).toHaveAttribute('data-state', 'active')
  })
})

describe('KnowledgeGraphPage 详情对话框', () => {
  it('点击节点打开实体详情，关闭后对话框消失', async () => {
    const user = userEvent.setup()
    await renderLoadedPage()
    await openNodeDialog(user)

    const dialog = screen.getByRole('dialog', { name: '实体详情' })
    expect(dialog).toHaveTextContent('出现次数 3')
    expect(within(dialog).getByText('相关关系')).toBeInTheDocument()

    await user.click(within(dialog).getByRole('button', { name: '关闭' }))
    await waitFor(() => {
      expect(screen.queryByRole('dialog', { name: '实体详情' })).not.toBeInTheDocument()
    })
  })

  it('点击边打开关系详情，关闭后对话框消失', async () => {
    const user = userEvent.setup()
    await renderLoadedPage()
    await openEdgeDialog(user)

    const dialog = screen.getByRole('dialog', { name: '关系详情' })
    expect(dialog).toHaveTextContent('Alpha → Beta')

    await user.click(within(dialog).getByRole('button', { name: '关闭' }))
    await waitFor(() => {
      expect(screen.queryByRole('dialog', { name: '关系详情' })).not.toBeInTheDocument()
    })
  })

  it('空白节点或边 id 不会发起详情请求', async () => {
    const user = userEvent.setup()
    await renderLoadedPage()
    await screen.findByTestId('graph-visualization')

    await user.click(screen.getByRole('button', { name: 'click-empty-node' }))
    await user.click(screen.getByRole('button', { name: 'click-empty-edge' }))

    expect(memoryApi.getMemoryGraphNodeDetail).not.toHaveBeenCalled()
    expect(memoryApi.getMemoryGraphEdgeDetail).not.toHaveBeenCalled()
  })

  it('节点详情失败会关闭对话框并提示', async () => {
    const user = userEvent.setup()
    vi.mocked(memoryApi.getMemoryGraphNodeDetail).mockRejectedValue(new Error('node missing'))

    await renderLoadedPage()
    await screen.findByTestId('graph-visualization')
    await user.click(screen.getByRole('button', { name: 'click-node-alpha' }))

    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith({
        title: '加载节点详情失败',
        description: 'node missing',
        variant: 'destructive',
      })
    })
    expect(screen.queryByRole('dialog', { name: '实体详情' })).not.toBeInTheDocument()
  })

  it('关系详情失败会提示并回到实体图', async () => {
    const user = userEvent.setup()
    vi.mocked(memoryApi.getMemoryGraphEdgeDetail).mockRejectedValue('edge boom')

    await renderLoadedPage()
    await screen.findByTestId('graph-visualization')
    await user.click(screen.getByRole('button', { name: 'click-edge-alpha-beta' }))

    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith({
        title: '加载关系详情失败',
        description: '未知错误',
        variant: 'destructive',
      })
    })
    expect(screen.getByRole('tab', { name: '实体关系图' })).toHaveAttribute('data-state', 'active')
  })

  it('实体缺少 hash 时不能删除', async () => {
    const user = userEvent.setup()
    vi.mocked(memoryApi.getMemoryGraphNodeDetail).mockResolvedValue(makeNodeDetail({
      node: { id: 'alpha', type: 'entity', content: 'Alpha' },
    }) as never)

    await renderLoadedPage()
    await openNodeDialog(user)
    await user.click(screen.getByRole('button', { name: '删除实体' }))

    expect(toastMock).toHaveBeenCalledWith({
      title: '缺少实体标识',
      description: '当前实体没有可用的 hash，无法执行删除。',
      variant: 'destructive',
    })
    expect(memoryApi.previewMemoryDelete).not.toHaveBeenCalled()
  })

  it('关系组缺少 hash 时不能删除', async () => {
    const user = userEvent.setup()
    vi.mocked(memoryApi.getMemoryGraphEdgeDetail).mockResolvedValue(makeEdgeDetail({
      edge: {
        source: 'alpha',
        target: 'beta',
        weight: 1,
        predicates: ['关联'],
        relation_count: 1,
        evidence_count: 1,
        relation_hashes: [],
        label: '关联',
      },
    }) as never)

    await renderLoadedPage()
    await openEdgeDialog(user)
    await user.click(screen.getByRole('button', { name: '删除此关系组' }))

    expect(toastMock).toHaveBeenCalledWith({
      title: '缺少关系标识',
      description: '当前关系组没有可用的 relation hash。',
      variant: 'destructive',
    })
    expect(memoryApi.previewMemoryDelete).not.toHaveBeenCalled()
  })

  it('删除实体可附带段落，并关闭删除对话框', async () => {
    const user = userEvent.setup()
    await renderLoadedPage()
    await openNodeDialog(user)

    await user.click(screen.getByLabelText('删除该实体相关证据段落'))
    await user.click(screen.getByRole('button', { name: '删除实体' }))

    expect(await screen.findByTestId('memory-delete-dialog')).toHaveTextContent('删除实体')
    expect(memoryApi.previewMemoryDelete).toHaveBeenCalledWith(
      expect.objectContaining({
        mode: 'mixed',
        selector: {
          entity_hashes: ['entity-1'],
          paragraph_hashes: ['p-1'],
        },
      }),
    )

    clickDeleteDialogButton('关闭删除对话框')
    await waitFor(() => {
      expect(screen.queryByTestId('memory-delete-dialog')).not.toBeInTheDocument()
    })
  })

  it('删除关系组可附带段落；预览失败会展示错误', async () => {
    const user = userEvent.setup()
    vi.mocked(memoryApi.previewMemoryDelete).mockRejectedValue(new Error('preview down'))

    await renderLoadedPage()
    await openEdgeDialog(user)
    await user.click(screen.getByLabelText('同时删除支撑段落'))
    await user.click(screen.getByRole('button', { name: '删除此关系组' }))

    expect(await screen.findByTestId('memory-delete-dialog')).toHaveTextContent('delete-error:preview down')
    expect(memoryApi.previewMemoryDelete).toHaveBeenCalledWith(
      expect.objectContaining({
        selector: {
          relation_hashes: ['rel-1'],
          paragraph_hashes: ['p-1'],
        },
      }),
    )
  })
})

describe('KnowledgeGraphPage 证据节点点击', () => {
  it('点击证据实体会拉取详情并重新打开实体对话框', async () => {
    const user = userEvent.setup()
    await renderLoadedPage()
    await openNodeDialog(user)
    await switchToEvidence(user)

    await user.click(screen.getByRole('button', { name: 'click-node-entity:alpha' }))
    expect(await screen.findByRole('dialog', { name: '实体详情' })).toBeInTheDocument()
    expect(memoryApi.getMemoryGraphNodeDetail).toHaveBeenCalledWith('alpha')
  })

  it('证据实体详情失败会 toast；不存在的节点被忽略', async () => {
    const user = userEvent.setup()
    await renderLoadedPage()
    await openNodeDialog(user)
    await switchToEvidence(user)

    const detailCalls = vi.mocked(memoryApi.getMemoryGraphNodeDetail).mock.calls.length
    await user.click(screen.getByRole('button', { name: 'click-missing-node' }))
    expect(vi.mocked(memoryApi.getMemoryGraphNodeDetail).mock.calls.length).toBe(detailCalls)

    vi.mocked(memoryApi.getMemoryGraphNodeDetail).mockRejectedValueOnce(new Error('entity gone'))
    await user.click(screen.getByRole('button', { name: 'click-node-entity:alpha' }))
    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith({
        title: '加载实体详情失败',
        description: 'entity gone',
        variant: 'destructive',
      })
    })
  })

  it('关系节点优先使用已加载详情，否则从 metadata 构建；空 hash 不打开明细', async () => {
    const user = userEvent.setup()
    await renderLoadedPage()
    await openNodeDialog(user)
    await switchToEvidence(user)

    await user.click(screen.getByRole('button', { name: 'click-node-relation:rel-1' }))
    const knownDialog = await screen.findByRole('dialog', { name: '关系明细' })
    expect(knownDialog).toHaveTextContent('alpha 关联 beta')
    await user.click(within(knownDialog).getByRole('button', { name: '关闭' }))

    await user.click(screen.getByRole('button', { name: 'click-node-relation:rel-meta' }))
    const metaDialog = await screen.findByRole('dialog', { name: '关系明细' })
    expect(metaDialog).toHaveTextContent('X 认识 Y')
    expect(metaDialog).toHaveTextContent('rel-meta')
    await user.click(within(metaDialog).getByRole('button', { name: '关闭' }))

    await user.click(screen.getByRole('button', { name: 'click-node-relation:rel-empty' }))
    expect(screen.queryByRole('dialog', { name: '关系明细' })).not.toBeInTheDocument()
  })

  it('段落节点优先使用已加载详情，否则从 metadata 构建；空 hash 不打开明细', async () => {
    const user = userEvent.setup()
    await renderLoadedPage()
    await openNodeDialog(user)
    await switchToEvidence(user)

    await user.click(screen.getByRole('button', { name: 'click-node-paragraph:p-1' }))
    const knownDialog = await screen.findByRole('dialog', { name: '段落明细' })
    expect(knownDialog).toHaveTextContent('Alpha 提到了 Beta')
    await user.click(within(knownDialog).getByRole('button', { name: '关闭' }))

    await user.click(screen.getByRole('button', { name: 'click-node-paragraph:p-meta' }))
    const metaDialog = await screen.findByRole('dialog', { name: '段落明细' })
    expect(metaDialog).toHaveTextContent('元数据段落预览')
    expect(metaDialog).toHaveTextContent('p-meta')
    await user.click(within(metaDialog).getByRole('button', { name: '关闭' }))

    await user.click(screen.getByRole('button', { name: 'click-node-paragraph:p-empty' }))
    expect(screen.queryByRole('dialog', { name: '段落明细' })).not.toBeInTheDocument()
  })
})

describe('KnowledgeGraphPage 删除执行与恢复', () => {
  it('删除成功后可恢复，并重新定位原实体', async () => {
    const user = userEvent.setup()
    await renderLoadedPage()
    await openNodeDialog(user)
    await user.click(screen.getByRole('button', { name: '删除实体' }))
    await screen.findByTestId('memory-delete-dialog')

    clickDeleteDialogButton('确认删除')
    await waitFor(() => {
      expect(memoryApi.executeMemoryDelete).toHaveBeenCalled()
    })
    expect(toastMock).toHaveBeenCalledWith({
      title: '删除成功',
      description: '操作 del-1 已完成',
      variant: 'default',
    })

    clickDeleteDialogButton('恢复本次删除')
    await waitFor(() => {
      expect(memoryApi.restoreMemoryDelete).toHaveBeenCalledWith({
        operation_id: 'del-1',
        requested_by: 'knowledge_graph',
      })
    })
    expect(toastMock).toHaveBeenCalledWith({
      title: '恢复成功',
      description: '删除操作 del-1 已恢复',
    })
    await waitFor(() => {
      expect(screen.queryByTestId('memory-delete-dialog')).not.toBeInTheDocument()
    })
  })

  it('没有 operation_id 时恢复是空操作', async () => {
    const user = userEvent.setup()
    vi.mocked(memoryApi.executeMemoryDelete).mockResolvedValue(makeDeleteResult({
      operation_id: '',
    }) as never)

    await renderLoadedPage()
    await openNodeDialog(user)
    await user.click(screen.getByRole('button', { name: '删除实体' }))
    await screen.findByTestId('memory-delete-dialog')
    clickDeleteDialogButton('确认删除')
    await waitFor(() => {
      expect(screen.getByTestId('memory-delete-dialog')).toHaveTextContent('result:none')
    })

    clickDeleteDialogButton('恢复本次删除')
    expect(memoryApi.restoreMemoryDelete).not.toHaveBeenCalled()
  })

  it('恢复失败会 toast 且保持删除对话框', async () => {
    const user = userEvent.setup()
    vi.mocked(memoryApi.restoreMemoryDelete).mockRejectedValue(new Error('restore down'))

    await renderLoadedPage()
    await openNodeDialog(user)
    await user.click(screen.getByRole('button', { name: '删除实体' }))
    await screen.findByTestId('memory-delete-dialog')
    clickDeleteDialogButton('确认删除')
    await waitFor(() => {
      expect(screen.getByTestId('memory-delete-dialog')).toHaveTextContent('result:del-1')
    })
    clickDeleteDialogButton('恢复本次删除')

    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith({
        title: '恢复失败',
        description: 'restore down',
        variant: 'destructive',
      })
    })
    expect(screen.getByTestId('memory-delete-dialog')).toBeInTheDocument()
  })

  it('删除接口抛错或返回失败都会提示', async () => {
    const user = userEvent.setup()
    vi.mocked(memoryApi.executeMemoryDelete)
      .mockRejectedValueOnce('delete boom')
      .mockResolvedValueOnce(makeDeleteResult({
        success: false,
        error: 'still referenced',
        operation_id: 'del-fail',
      }) as never)

    await renderLoadedPage()
    await openNodeDialog(user)
    await user.click(screen.getByRole('button', { name: '删除实体' }))
    await screen.findByTestId('memory-delete-dialog')
    clickDeleteDialogButton('确认删除')

    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith({
        title: '删除失败',
        description: '未知错误',
        variant: 'destructive',
      })
    })
    // 抛出非 Error 时，对话框错误文案与 toast description 不同
    expect(screen.getByTestId('memory-delete-dialog')).toHaveTextContent('delete-error:删除失败')

    clickDeleteDialogButton('确认删除')
    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith({
        title: '删除失败',
        description: 'still referenced',
        variant: 'destructive',
      })
    })
  })

  it('删除证据关系成功后按 view 目标恢复，只切换视图模式', async () => {
    const user = userEvent.setup()
    await renderLoadedPage({ initialParagraphHash: 'p-1' })
    const paragraphDialog = await screen.findByRole('dialog', { name: '段落明细' })
    await user.click(within(paragraphDialog).getByRole('button', { name: '关闭' }))
    await waitFor(() => {
      expect(screen.queryByRole('dialog', { name: '段落明细' })).not.toBeInTheDocument()
    })
    await screen.findByTestId('graph-visualization')

    await user.click(screen.getByRole('button', { name: 'click-node-relation:rel-meta' }))
    const relationDialog = await screen.findByRole('dialog', { name: '关系明细' })
    await user.click(within(relationDialog).getByLabelText('同时删除支撑该关系的段落'))
    await user.click(within(relationDialog).getByRole('button', { name: '删除这条关系' }))

    expect(memoryApi.previewMemoryDelete).toHaveBeenCalledWith(
      expect.objectContaining({
        selector: {
          relation_hashes: ['rel-meta'],
          paragraph_hashes: ['p-x'],
        },
      }),
    )

    await screen.findByTestId('memory-delete-dialog')
    clickDeleteDialogButton('确认删除')
    await waitFor(() => {
      expect(memoryApi.executeMemoryDelete).toHaveBeenCalled()
    })
    expect(screen.getByRole('tab', { name: '证据视图', hidden: true })).toHaveAttribute('data-state', 'active')
  })
})

describe('KnowledgeGraphPage 段落定位', () => {
  it('空白 initialParagraphHash 不会请求段落详情', async () => {
    await renderLoadedPage({ initialParagraphHash: '   ' })
    await screen.findByTestId('graph-visualization')
    expect(memoryApi.getMemoryGraphParagraphDetail).not.toHaveBeenCalled()
  })

  it('初始段落定位失败会提示并回到实体图', async () => {
    vi.mocked(memoryApi.getMemoryGraphParagraphDetail).mockRejectedValue(new Error('not found'))

    render(<KnowledgeGraphPage initialParagraphHash="p-missing" />)

    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith({
        title: '定位段落失败',
        description: 'not found',
        variant: 'destructive',
      })
    })
    expect(screen.getByRole('tab', { name: '实体关系图' })).toHaveAttribute('data-state', 'active')
  })

  it('删除直接打开的段落后，若段落已不存在则回到实体图', async () => {
    const user = userEvent.setup()
    vi.mocked(memoryApi.getMemoryGraphParagraphDetail)
      .mockResolvedValueOnce(makeParagraphDetail() as never)
      .mockRejectedValueOnce(new Error('paragraph missing'))

    await renderLoadedPage({ initialParagraphHash: 'p-1' })
    const dialog = await screen.findByRole('dialog', { name: '段落明细' })
    await user.click(within(dialog).getByRole('button', { name: '删除这段证据' }))
    await screen.findByTestId('memory-delete-dialog')
    clickDeleteDialogButton('确认删除')

    await waitFor(() => {
      expect(screen.getByRole('tab', { name: '实体关系图' })).toHaveAttribute('data-state', 'active')
    })
    expect(toastMock).toHaveBeenCalledWith({
      title: '已刷新图谱',
      description: '原段落已被删除，当前返回实体关系图。',
    })
  })
})
