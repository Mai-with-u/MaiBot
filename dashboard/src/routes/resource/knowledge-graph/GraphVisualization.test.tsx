import { cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  EdgeDetailDialog,
  NodeDetailDialog,
  ParagraphDetailDialog,
  RelationDetailDialog,
} from './GraphDialogs'
import { GraphVisualization } from './GraphVisualization'
import type { GraphEdge, GraphNode, SelectedEdgeData } from './types'
import type {
  MemoryGraphEdgeDetailPayload,
  MemoryGraphNodeDetailPayload,
  MemoryGraphParagraphDetailPayload,
  MemoryGraphRelationDetailPayload,
} from '@/lib/memory-api'

// ReactFlow 在 jsdom 无法布局；仍执行 nodeTypes，并把布局算好的边样式暴露出来
vi.mock('reactflow', () => ({
  __esModule: true,
  default: ({
    nodes = [],
    edges = [],
    nodeTypes = {},
    onNodeClick,
    onEdgeClick,
    children,
  }: {
    nodes?: Array<{
      id: string
      type?: string
      data?: { label?: string; content?: string; type?: string; layout?: string }
    }>
    edges?: Array<{
      id?: string
      source: string
      target: string
      label?: string
      animated?: boolean
      style?: { stroke?: string; strokeWidth?: number; opacity?: number }
    }>
    nodeTypes?: Record<
      string,
      (props: { data: { label?: string; content?: string; type?: string; layout?: string } }) => React.ReactNode
    >
    onNodeClick?: (event: React.MouseEvent, node: { id: string }) => void
    onEdgeClick?: (event: React.MouseEvent, edge: { source: string; target: string }) => void
    children?: React.ReactNode
  }) => (
    <div data-testid="react-flow">
      <div data-testid="react-flow-counts">{`nodes:${nodes.length},edges:${edges.length}`}</div>
      {children}
      {nodes.map((node) => {
        const Comp = node.type ? nodeTypes[node.type] : undefined
        return (
          <button
            key={node.id}
            type="button"
            data-testid={`flow-node-${node.id}`}
            data-node-type={node.type ?? ''}
            data-layout={node.data?.layout ?? ''}
            onClick={(event) => onNodeClick?.(event, { id: node.id })}
          >
            {Comp ? <Comp data={node.data ?? {}} /> : (node.data?.label ?? node.id)}
          </button>
        )
      })}
      {edges.map((edge, index) => (
        <button
          key={edge.id ?? `${edge.source}-${edge.target}-${index}`}
          type="button"
          data-testid={`flow-edge-${edge.id ?? index}`}
          data-stroke={edge.style?.stroke ?? ''}
          data-stroke-width={String(edge.style?.strokeWidth ?? '')}
          data-opacity={String(edge.style?.opacity ?? '')}
          data-label={edge.label ?? ''}
          data-animated={edge.animated ? '1' : '0'}
          onClick={(event) => onEdgeClick?.(event, { source: edge.source, target: edge.target })}
        >
          {`edge:${edge.source}->${edge.target}:${edge.label ?? ''}`}
        </button>
      ))}
    </div>
  ),
  Background: () => <div data-testid="react-flow-background" />,
  BackgroundVariant: { Dots: 'dots', Lines: 'lines', Cross: 'cross' },
  Controls: () => <div data-testid="react-flow-controls" />,
  Handle: ({ type }: { type?: string }) => <span data-testid={`handle-${type ?? 'unknown'}`} />,
  Position: { Left: 'left', Right: 'right', Top: 'top', Bottom: 'bottom' },
  MarkerType: { ArrowClosed: 'arrowclosed' },
  // 每次渲染直接回传当前布局结果，避免在 mock 里再挂一份 useState
  useNodesState: (initial: unknown[]) => [initial, () => undefined, () => undefined],
}))

afterEach(() => {
  cleanup()
})

function renderGraph(
  graphData: { nodes: GraphNode[]; edges: GraphEdge[] },
  options: {
    loading?: boolean
    onNodeClick?: (event: React.MouseEvent, node: { id: string }) => void
    onEdgeClick?: (event: React.MouseEvent, edge: { source: string; target: string }) => void
  } = {},
) {
  return render(
    <GraphVisualization
      graphData={graphData}
      loading={options.loading}
      onNodeClick={options.onNodeClick ?? vi.fn()}
      onEdgeClick={options.onEdgeClick ?? vi.fn()}
    />,
  )
}

function emptyEvidenceGraph() {
  return { nodes: [], edges: [], focus_entities: [] }
}

function makeRelation(
  overrides: Partial<MemoryGraphRelationDetailPayload> = {},
): MemoryGraphRelationDetailPayload {
  return {
    hash: 'rel-1',
    subject: 'alpha',
    predicate: '关联',
    object: 'beta',
    text: 'alpha 关联 beta',
    confidence: 0.9,
    paragraph_count: 2,
    paragraph_hashes: ['p-1'],
    source_paragraph: 'p-1',
    ...overrides,
  }
}

function makeParagraph(
  overrides: Partial<MemoryGraphParagraphDetailPayload> = {},
): MemoryGraphParagraphDetailPayload {
  return {
    hash: 'p-1',
    content: '完整段落正文',
    preview: '段落预览',
    source: 'chat-log',
    updated_at: 1_710_000_000,
    entity_count: 2,
    relation_count: 1,
    entities: ['Alpha', 'Beta'],
    relations: ['alpha 关联 beta'],
    ...overrides,
  }
}

function makeNodeDetail(
  overrides: Partial<MemoryGraphNodeDetailPayload> = {},
): MemoryGraphNodeDetailPayload {
  return {
    success: true,
    node: { id: 'alpha', type: 'entity', content: 'Alpha', hash: 'entity-1', appearance_count: 3 },
    relations: [makeRelation()],
    paragraphs: [makeParagraph()],
    evidence_graph: emptyEvidenceGraph(),
    ...overrides,
  }
}

function makeSelectedEdge(): SelectedEdgeData {
  return {
    source: { id: 'alpha', type: 'entity', content: 'Alpha' },
    target: { id: 'beta', type: 'entity', content: 'Beta' },
    edge: {
      source: 'alpha',
      target: 'beta',
      weight: 1.5,
      kind: 'relation',
      label: '关联',
      relationCount: 2,
      evidenceCount: 4,
    },
  }
}

function makeEdgeDetail(
  overrides: Partial<MemoryGraphEdgeDetailPayload> = {},
): MemoryGraphEdgeDetailPayload {
  return {
    success: true,
    edge: {
      source: 'alpha',
      target: 'beta',
      weight: 2.5,
      predicates: ['关联', '认识'],
      relation_count: 3,
      evidence_count: 5,
      relation_hashes: ['rel-1'],
      label: '关联',
    },
    relations: [makeRelation()],
    paragraphs: [makeParagraph()],
    evidence_graph: emptyEvidenceGraph(),
    ...overrides,
  }
}

describe('GraphVisualization', () => {
  it('loading 时不渲染画布', () => {
    renderGraph({ nodes: [{ id: 'a', type: 'entity', content: 'A' }], edges: [] }, { loading: true })

    expect(screen.queryByTestId('react-flow')).not.toBeInTheDocument()
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
  })

  it('空图谱仍渲染 0 节点 0 边的无障碍文案', () => {
    renderGraph({ nodes: [], edges: [] })

    expect(screen.getByRole('img', { name: '知识图谱可视化，共 0 个节点，0 条关系' })).toBeInTheDocument()
    expect(screen.getByText('知识图谱包含 0 个节点和 0 条关系。')).toBeInTheDocument()
    expect(screen.getByTestId('react-flow-counts')).toHaveTextContent('nodes:0,edges:0')
    expect(screen.getByTestId('react-flow-background')).toBeInTheDocument()
    expect(screen.getByTestId('react-flow-controls')).toBeInTheDocument()
  })

  it('实体图会截断长标签、挂载关系节点，并按权重/标签绘制边', () => {
    renderGraph({
      nodes: [
        { id: 'alpha', type: 'entity', content: 'AlphaEntityNm' },
        { id: 'beta', type: 'entity', content: 'Beta' },
        { id: 'rel-1', type: 'relation', content: 'knows-about-x' },
      ],
      edges: [
        { source: 'alpha', target: 'beta', weight: 2, kind: 'relation', label: '关联' },
        { source: 'alpha', target: 'rel-1', weight: 12 },
        { source: 'beta', target: 'rel-1', weight: 3 },
      ],
    })

    expect(screen.getByRole('img', { name: '知识图谱可视化，共 3 个节点，3 条关系' })).toBeInTheDocument()
    expect(screen.getByTestId('flow-node-alpha')).toHaveAttribute('data-layout', '')
    expect(screen.getByTestId('flow-node-alpha')).toHaveTextContent('AlphaEntityN...')
    expect(screen.getByTestId('flow-node-rel-1')).toHaveAttribute('data-node-type', 'relation')
    expect(screen.getByTestId('flow-node-rel-1')).toHaveTextContent('knows-about-...')
    expect(within(screen.getByTestId('flow-node-alpha')).getByTestId('handle-target')).toBeInTheDocument()

    const labeled = screen.getByTestId('flow-edge-edge-0')
    expect(labeled).toHaveAttribute('data-label', '关联')
    expect(labeled).toHaveAttribute('data-stroke', '#475569')
    expect(labeled).toHaveAttribute('data-animated', '0')

    const heavy = screen.getByTestId('flow-edge-edge-1')
    expect(heavy).toHaveAttribute('data-label', '12')
    expect(heavy).toHaveAttribute('data-animated', '1')

    const light = screen.getByTestId('flow-edge-edge-2')
    expect(light).toHaveAttribute('data-label', '')
    expect(light).toHaveAttribute('data-animated', '0')
  })

  it('点击节点和边会转发给回调', async () => {
    const user = userEvent.setup()
    const onNodeClick = vi.fn()
    const onEdgeClick = vi.fn()
    renderGraph(
      {
        nodes: [
          { id: 'alpha', type: 'entity', content: 'Alpha' },
          { id: 'beta', type: 'entity', content: 'Beta' },
        ],
        edges: [{ source: 'alpha', target: 'beta', weight: 1, label: '关联' }],
      },
      { onNodeClick, onEdgeClick },
    )

    await user.click(screen.getByTestId('flow-node-alpha'))
    expect(onNodeClick).toHaveBeenCalledTimes(1)
    expect(onNodeClick.mock.calls[0][1]).toEqual({ id: 'alpha' })

    await user.click(screen.getByTestId('flow-edge-edge-0'))
    expect(onEdgeClick).toHaveBeenCalledTimes(1)
    expect(onEdgeClick.mock.calls[0][1]).toEqual({ source: 'alpha', target: 'beta' })
  })

  it('证据图按 kind 着色，并给各层节点使用 evidence 布局', () => {
    renderGraph({
      nodes: [
        { id: 'rel-a', type: 'relation', content: `${'R'.repeat(40)}` },
        { id: 'rel-b', type: 'relation', content: 'RelB' },
        { id: 'para-1', type: 'paragraph', content: `${'P'.repeat(40)}` },
        { id: 'ent-1', type: 'entity', content: `${'E'.repeat(40)}` },
        { id: 'ent-2', type: 'entity', content: 'Ent2' },
        { id: 'ent-isolated', type: 'entity', content: 'Lonely' },
        { id: 'misc', type: 'unknown' as GraphNode['type'], content: 'MiscOverflowName' },
      ],
      edges: [
        { source: 'para-1', target: 'ent-1', weight: 1, kind: 'mentions', label: '提及' },
        { source: 'para-1', target: 'rel-a', weight: 1, kind: 'supports', label: '支撑' },
        { source: 'rel-a', target: 'ent-1', weight: 1, kind: 'subject', label: '主语' },
        { source: 'rel-a', target: 'ent-2', weight: 1, kind: 'object', label: '宾语' },
        { source: 'ent-1', target: 'ent-2', weight: 3, kind: 'relation', label: '同层' },
        { source: 'ent-isolated', target: 'ent-2', weight: 1 },
      ],
    })

    expect(screen.getByTestId('flow-node-rel-a')).toHaveAttribute('data-layout', 'evidence')
    expect(screen.getByTestId('flow-node-para-1')).toHaveAttribute('data-node-type', 'paragraph')
    expect(screen.getByTestId('flow-node-ent-1')).toHaveAttribute('data-node-type', 'entity')
    expect(screen.getByTestId('flow-node-misc')).toHaveTextContent('MiscOverflow...')

    expect(screen.getByTestId('flow-edge-edge-0')).toHaveAttribute('data-stroke', '#10b981')
    expect(screen.getByTestId('flow-edge-edge-0')).toHaveAttribute('data-label', '提及')
    expect(screen.getByTestId('flow-edge-edge-1')).toHaveAttribute('data-stroke', '#f97316')
    expect(screen.getByTestId('flow-edge-edge-1')).toHaveAttribute('data-label', '支撑')
    expect(screen.getByTestId('flow-edge-edge-2')).toHaveAttribute('data-stroke', '#60a5fa')
    expect(screen.getByTestId('flow-edge-edge-3')).toHaveAttribute('data-stroke', '#a78bfa')
    expect(screen.getByTestId('flow-edge-edge-4')).toHaveAttribute('data-stroke', '#64748b')
    expect(screen.getByTestId('flow-edge-edge-4')).toHaveAttribute('data-opacity', '0.68')
    expect(screen.getByTestId('flow-edge-edge-5')).toHaveAttribute('data-stroke', '#64748b')
    expect(screen.getByTestId('flow-edge-edge-5')).toHaveAttribute('data-label', '')
  })

  it('证据图节点超过 60 时只给 supports 边保留标签', () => {
    const nodes: GraphNode[] = Array.from({ length: 61 }, (_, index) => ({
      id: `n-${index}`,
      type: 'entity',
      content: `E${index}`,
    }))
    renderGraph({
      nodes,
      edges: [
        { source: 'n-0', target: 'n-1', weight: 1, kind: 'supports', label: '支撑' },
        { source: 'n-2', target: 'n-3', weight: 1, kind: 'mentions', label: '提及' },
      ],
    })

    expect(screen.getByTestId('react-flow-counts')).toHaveTextContent('nodes:61,edges:2')
    expect(screen.getByTestId('flow-edge-edge-0')).toHaveAttribute('data-label', '支撑')
    expect(screen.getByTestId('flow-edge-edge-1')).toHaveAttribute('data-label', '')
  })

  it('同一关系下超过 4 个实体仍能完成分层布局', () => {
    renderGraph({
      nodes: [
        { id: 'rel-1', type: 'relation', content: 'R' },
        ...Array.from({ length: 5 }, (_, index) => ({
          id: `e-${index}`,
          type: 'entity' as const,
          content: `E${index}`,
        })),
      ],
      edges: Array.from({ length: 5 }, (_, index) => ({
        source: 'rel-1',
        target: `e-${index}`,
        weight: 1,
        kind: 'subject' as const,
        label: '主语',
      })),
    })

    expect(screen.getByTestId('react-flow-counts')).toHaveTextContent('nodes:6,edges:5')
    expect(screen.getByTestId('flow-node-e-4')).toHaveTextContent('E4')
  })
})

describe('NodeDetailDialog', () => {
  it('未选中实体时展示空态', () => {
    render(
      <NodeDetailDialog
        open
        onOpenChange={vi.fn()}
        selectedNodeData={null}
        nodeDetail={null}
      />,
    )

    expect(screen.getByRole('dialog', { name: '实体详情' })).toHaveTextContent('尚未选中实体。')
  })

  it('仅有选中节点时展示空列表，证据按钮禁用', () => {
    render(
      <NodeDetailDialog
        open
        onOpenChange={vi.fn()}
        selectedNodeData={{ id: 'alpha', type: 'entity', content: 'Alpha' }}
        nodeDetail={null}
      />,
    )

    const dialog = screen.getByRole('dialog', { name: '实体详情' })
    expect(dialog).toHaveTextContent('实体')
    expect(dialog).toHaveTextContent('Alpha')
    expect(dialog).toHaveTextContent('暂无可展示的关系语义。')
    expect(dialog).toHaveTextContent('暂无可展示的来源段落。')
    expect(screen.getByRole('button', { name: '切到证据视图' })).toBeDisabled()
    expect(screen.queryByRole('button', { name: '删除实体' })).not.toBeInTheDocument()
  })

  it('加载中显示状态指示，非实体类型直接展示 type', () => {
    render(
      <NodeDetailDialog
        open
        onOpenChange={vi.fn()}
        selectedNodeData={{ id: 'rel-1', type: 'relation', content: '关联节点' }}
        nodeDetail={null}
        loading
      />,
    )

    expect(screen.getByRole('status', { name: '加载中' })).toBeInTheDocument()
    expect(screen.getByRole('dialog', { name: '实体详情' })).toHaveTextContent('relation')
    expect(screen.queryByText('相关关系')).not.toBeInTheDocument()
  })

  it('详情列表展示回退文案，删除确认会带上是否包含段落', async () => {
    const user = userEvent.setup()
    const onOpenChange = vi.fn()
    const onOpenEvidence = vi.fn()
    const onDeleteEntity = vi.fn()
    const onDeleteRelation = vi.fn()
    const onDeleteParagraph = vi.fn()
    const extraEntities = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', 'I']

    render(
      <NodeDetailDialog
        open
        onOpenChange={onOpenChange}
        selectedNodeData={{ id: 'alpha', type: 'entity', content: '旧名称' }}
        nodeDetail={makeNodeDetail({
          relations: [makeRelation({ predicate: '', text: '无名关系' })],
          paragraphs: [
            makeParagraph({
              source: '',
              preview: '',
              content: '仅正文',
              updated_at: null,
              entities: extraEntities,
            }),
          ],
        })}
        onOpenEvidence={onOpenEvidence}
        onDeleteEntity={onDeleteEntity}
        onDeleteRelation={onDeleteRelation}
        onDeleteParagraph={onDeleteParagraph}
      />,
    )

    const dialog = screen.getByRole('dialog', { name: '实体详情' })
    expect(dialog).toHaveTextContent('出现次数 3')
    expect(dialog).toHaveTextContent('未命名谓词')
    expect(dialog).toHaveTextContent('未命名来源')
    expect(dialog).toHaveTextContent('仅正文')
    expect(dialog).toHaveTextContent('更新时间 未知')
    expect(dialog).toHaveTextContent('0.900')
    extraEntities.slice(0, 8).forEach((entity) => {
      expect(within(dialog).getByText(entity)).toBeInTheDocument()
    })
    expect(within(dialog).queryByText('I')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '切到证据视图' }))
    expect(onOpenEvidence).toHaveBeenCalledTimes(1)

    await user.click(screen.getByRole('button', { name: '删除关系' }))
    expect(onDeleteRelation).toHaveBeenCalledWith(expect.objectContaining({ hash: 'rel-1' }))

    await user.click(screen.getByRole('button', { name: '删除段落' }))
    expect(onDeleteParagraph).toHaveBeenCalledWith(expect.objectContaining({ hash: 'p-1' }))

    await user.click(screen.getByRole('button', { name: '删除实体' }))
    expect(onDeleteEntity).toHaveBeenCalledWith({ includeParagraphs: false })

    await user.click(screen.getByLabelText('删除该实体相关证据段落'))
    await user.click(screen.getByRole('button', { name: '删除实体' }))
    expect(onDeleteEntity).toHaveBeenLastCalledWith({ includeParagraphs: true })

    await user.click(within(dialog).getByRole('button', { name: '关闭' }))
    expect(onOpenChange).toHaveBeenCalledWith(false)
  })

  it('关闭后再打开会重置包含段落勾选', async () => {
    const user = userEvent.setup()
    const onDeleteEntity = vi.fn()
    const props = {
      selectedNodeData: { id: 'alpha', type: 'entity' as const, content: 'Alpha' },
      nodeDetail: makeNodeDetail({ relations: [], paragraphs: [] }),
      onDeleteEntity,
    }
    const view = render(<NodeDetailDialog open onOpenChange={vi.fn()} {...props} />)

    await user.click(screen.getByLabelText('删除该实体相关证据段落'))
    view.rerender(<NodeDetailDialog open={false} onOpenChange={vi.fn()} {...props} />)
    view.rerender(<NodeDetailDialog open onOpenChange={vi.fn()} {...props} />)

    expect(screen.getByLabelText('删除该实体相关证据段落')).toHaveAttribute('data-state', 'unchecked')
    await user.click(screen.getByRole('button', { name: '删除实体' }))
    expect(onDeleteEntity).toHaveBeenCalledWith({ includeParagraphs: false })
  })
})

describe('EdgeDetailDialog', () => {
  it('未选中关系时展示空态', () => {
    render(
      <EdgeDetailDialog
        open
        onOpenChange={vi.fn()}
        selectedEdgeData={null}
        edgeDetail={null}
      />,
    )

    expect(screen.getByRole('dialog', { name: '关系详情' })).toHaveTextContent('尚未选中关系。')
  })

  it('仅有选中边时回退标签与计数，取消关闭对话框', async () => {
    const user = userEvent.setup()
    const onOpenChange = vi.fn()
    render(
      <EdgeDetailDialog
        open
        onOpenChange={onOpenChange}
        selectedEdgeData={makeSelectedEdge()}
        edgeDetail={null}
      />,
    )

    const dialog = screen.getByRole('dialog', { name: '关系详情' })
    expect(dialog).toHaveTextContent('Alpha → Beta')
    expect(dialog).toHaveTextContent('关系 2')
    expect(dialog).toHaveTextContent('证据 4')
    expect(dialog).toHaveTextContent('聚合权重 1.5000')
    expect(dialog).toHaveTextContent('暂无可展示的关系语义。')
    expect(screen.getByRole('button', { name: '切到证据视图' })).toBeDisabled()

    await user.click(within(dialog).getByRole('button', { name: '关闭' }))
    expect(onOpenChange).toHaveBeenCalledWith(false)
  })

  it('详情加载中隐藏列表；确认删除关系组时带上段落选项', async () => {
    const user = userEvent.setup()
    const onDeleteEdgeGroup = vi.fn()
    const view = render(
      <EdgeDetailDialog
        open
        onOpenChange={vi.fn()}
        selectedEdgeData={makeSelectedEdge()}
        edgeDetail={null}
        loading
        onOpenEvidence={vi.fn()}
        onDeleteEdgeGroup={onDeleteEdgeGroup}
      />,
    )

    expect(screen.getByRole('status', { name: '加载中' })).toBeInTheDocument()
    expect(screen.queryByText('关系语义')).not.toBeInTheDocument()

    view.rerender(
      <EdgeDetailDialog
        open
        onOpenChange={vi.fn()}
        selectedEdgeData={null}
        edgeDetail={makeEdgeDetail()}
        onDeleteEdgeGroup={onDeleteEdgeGroup}
        onDeleteRelation={vi.fn()}
        onDeleteParagraph={vi.fn()}
      />,
    )

    const dialog = screen.getByRole('dialog', { name: '关系详情' })
    expect(dialog).toHaveTextContent('关联')
    expect(dialog).toHaveTextContent('认识')
    expect(dialog).toHaveTextContent('alpha → beta')
    expect(dialog).toHaveTextContent('关系 3')
    expect(dialog).toHaveTextContent('证据 5')

    await user.click(screen.getByRole('button', { name: '删除此关系组' }))
    expect(onDeleteEdgeGroup).toHaveBeenCalledWith({ includeParagraphs: false })

    await user.click(screen.getByLabelText('同时删除支撑段落'))
    await user.click(screen.getByRole('button', { name: '删除此关系组' }))
    expect(onDeleteEdgeGroup).toHaveBeenLastCalledWith({ includeParagraphs: true })
  })
})

describe('RelationDetailDialog', () => {
  it('relation 为空时不渲染', () => {
    render(
      <RelationDetailDialog
        open
        onOpenChange={vi.fn()}
        relation={null}
      />,
    )

    expect(screen.queryByRole('dialog', { name: '关系明细' })).not.toBeInTheDocument()
  })

  it('谓词可回退 metadata，确认删除或取消关闭', async () => {
    const user = userEvent.setup()
    const onOpenChange = vi.fn()
    const onDeleteRelation = vi.fn()
    render(
      <RelationDetailDialog
        open
        onOpenChange={onOpenChange}
        relation={makeRelation({ predicate: '' })}
        metadata={{ predicate: '认识' }}
        onDeleteRelation={onDeleteRelation}
      />,
    )

    const dialog = screen.getByRole('dialog', { name: '关系明细' })
    expect(dialog).toHaveTextContent('认识')
    expect(dialog).toHaveTextContent('证据段落 2')
    expect(dialog).toHaveTextContent('rel-1')

    await user.click(screen.getByRole('button', { name: '删除这条关系' }))
    expect(onDeleteRelation).toHaveBeenCalledWith(expect.objectContaining({ hash: 'rel-1' }), false)

    await user.click(screen.getByLabelText('同时删除支撑该关系的段落'))
    await user.click(screen.getByRole('button', { name: '删除这条关系' }))
    expect(onDeleteRelation).toHaveBeenLastCalledWith(expect.objectContaining({ hash: 'rel-1' }), true)

    await user.click(within(dialog).getByRole('button', { name: '关闭' }))
    expect(onOpenChange).toHaveBeenCalledWith(false)
  })

  it('谓词与 metadata 都缺失时显示未命名谓词', () => {
    render(
      <RelationDetailDialog
        open
        onOpenChange={vi.fn()}
        relation={makeRelation({ predicate: '' })}
      />,
    )

    expect(screen.getByRole('dialog', { name: '关系明细' })).toHaveTextContent('未命名谓词')
    expect(screen.queryByRole('button', { name: '删除这条关系' })).not.toBeInTheDocument()
  })
})

describe('ParagraphDetailDialog', () => {
  it('paragraph 为空时不渲染', () => {
    render(
      <ParagraphDetailDialog
        open
        onOpenChange={vi.fn()}
        paragraph={null}
      />,
    )

    expect(screen.queryByRole('dialog', { name: '段落明细' })).not.toBeInTheDocument()
  })

  it('来源和时间可回退 metadata，确认删除或取消关闭', async () => {
    const user = userEvent.setup()
    const onOpenChange = vi.fn()
    const onDeleteParagraph = vi.fn()
    render(
      <ParagraphDetailDialog
        open
        onOpenChange={onOpenChange}
        paragraph={makeParagraph({
          source: '',
          updated_at: null,
          content: '段落正文',
          entities: ['Alpha'],
        })}
        metadata={{ source: 'memo', updated_at: 1_710_000_000 }}
        onDeleteParagraph={onDeleteParagraph}
      />,
    )

    const dialog = screen.getByRole('dialog', { name: '段落明细' })
    expect(dialog).toHaveTextContent('memo')
    expect(dialog).toHaveTextContent('段落正文')
    expect(dialog).toHaveTextContent('Alpha')
    expect(dialog).toHaveTextContent(`更新时间 ${new Date(1_710_000_000 * 1000).toLocaleString()}`)

    await user.click(screen.getByRole('button', { name: '删除这段证据' }))
    expect(onDeleteParagraph).toHaveBeenCalledWith(expect.objectContaining({ hash: 'p-1' }))

    await user.click(within(dialog).getByRole('button', { name: '关闭' }))
    expect(onOpenChange).toHaveBeenCalledWith(false)
  })

  it('无效时间与空实体走回退文案，且无删除按钮', () => {
    render(
      <ParagraphDetailDialog
        open
        onOpenChange={vi.fn()}
        paragraph={makeParagraph({
          source: '',
          updated_at: Number.POSITIVE_INFINITY,
          entities: [],
        })}
      />,
    )

    const dialog = screen.getByRole('dialog', { name: '段落明细' })
    expect(dialog).toHaveTextContent('未命名来源')
    expect(dialog).toHaveTextContent('更新时间 未知')
    expect(screen.queryByText('Alpha')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '删除这段证据' })).not.toBeInTheDocument()
  })
})
