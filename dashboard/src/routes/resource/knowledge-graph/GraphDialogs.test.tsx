import { cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  EdgeDetailDialog,
  NodeDetailDialog,
  ParagraphDetailDialog,
  RelationDetailDialog,
} from './GraphDialogs'
import type { GraphNode, SelectedEdgeData } from './types'

import type {
  MemoryGraphEdgeDetailPayload,
  MemoryGraphNodeDetailPayload,
  MemoryGraphParagraphDetailPayload,
  MemoryGraphRelationDetailPayload,
} from '@/lib/memory-api'

afterEach(() => {
  cleanup()
})

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

function makeNode(overrides: Partial<GraphNode> = {}): GraphNode {
  return {
    id: 'alpha',
    type: 'entity',
    content: 'Alpha',
    ...overrides,
  }
}

function makeNodeDetail(
  overrides: Partial<MemoryGraphNodeDetailPayload> = {},
): MemoryGraphNodeDetailPayload {
  return {
    success: true,
    node: {
      id: 'alpha',
      type: 'entity',
      content: 'Alpha',
      hash: 'entity-1',
      appearance_count: 3,
      active_evidence_count: 2,
    },
    relations: [makeRelation()],
    paragraphs: [makeParagraph()],
    evidence_graph: emptyEvidenceGraph(),
    ...overrides,
  }
}

function makeSelectedEdge(overrides: Partial<SelectedEdgeData> = {}): SelectedEdgeData {
  return {
    source: makeNode(),
    target: makeNode({ id: 'beta', content: 'Beta' }),
    edge: {
      source: 'alpha',
      target: 'beta',
      weight: 1.5,
      kind: 'relation',
      label: '关联',
      relationCount: 2,
      evidenceCount: 4,
    },
    ...overrides,
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

describe('NodeDetailDialog', () => {
  it('关闭时不渲染对话框', () => {
    render(
      <NodeDetailDialog
        open={false}
        onOpenChange={vi.fn()}
        selectedNodeData={makeNode()}
        nodeDetail={makeNodeDetail()}
      />,
    )

    expect(screen.queryByRole('dialog', { name: '实体详情' })).not.toBeInTheDocument()
  })

  it('打开但未选中实体时展示空态', () => {
    render(
      <NodeDetailDialog open onOpenChange={vi.fn()} selectedNodeData={null} nodeDetail={null} />,
    )

    expect(screen.getByRole('dialog', { name: '实体详情' })).toHaveTextContent('尚未选中实体。')
  })

  it('Esc 关闭对话框', async () => {
    const user = userEvent.setup()
    const onOpenChange = vi.fn()
    render(
      <NodeDetailDialog
        open
        onOpenChange={onOpenChange}
        selectedNodeData={makeNode()}
        nodeDetail={null}
      />,
    )

    await user.keyboard('{Escape}')
    expect(onOpenChange).toHaveBeenCalledWith(false)
  })

  it('详情节点覆盖选中节点文案，缺处理器时禁用证据并隐藏删除', () => {
    render(
      <NodeDetailDialog
        open
        onOpenChange={vi.fn()}
        selectedNodeData={makeNode({ content: '旧名称' })}
        nodeDetail={makeNodeDetail({
          node: {
            id: 'alpha',
            type: 'entity',
            content: '详情名称',
            appearance_count: 0,
            active_evidence_count: 0,
          },
          relations: [],
          paragraphs: [],
        })}
      />,
    )

    const dialog = screen.getByRole('dialog', { name: '实体详情' })
    expect(dialog).toHaveTextContent('详情名称')
    expect(dialog).not.toHaveTextContent('旧名称')
    expect(dialog).toHaveTextContent('有效证据 0')
    expect(dialog).toHaveTextContent('累计出现 0')
    expect(dialog).toHaveTextContent('暂无可展示的关系语义。')
    expect(dialog).toHaveTextContent('暂无可展示的来源段落。')
    expect(screen.getByRole('button', { name: '切到证据视图' })).toBeDisabled()
    expect(screen.queryByRole('button', { name: '删除实体' })).not.toBeInTheDocument()
  })

  it('加载中展示状态指示，非实体类型直接显示 type', () => {
    render(
      <NodeDetailDialog
        open
        onOpenChange={vi.fn()}
        selectedNodeData={makeNode({ id: 'rel-1', type: 'relation', content: '关联节点' })}
        nodeDetail={null}
        loading
      />,
    )

    expect(screen.getByRole('status', { name: '加载中' })).toBeInTheDocument()
    expect(screen.getByRole('dialog', { name: '实体详情' })).toHaveTextContent('relation')
    expect(screen.queryByText('相关关系')).not.toBeInTheDocument()
  })

  it('提交删除时带上是否包含段落，关闭后再开重置勾选', async () => {
    const user = userEvent.setup()
    const onOpenChange = vi.fn()
    const onOpenEvidence = vi.fn()
    const onDeleteEntity = vi.fn()
    const onDeleteRelation = vi.fn()
    const onDeleteParagraph = vi.fn()
    const extraEntities = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', 'I']
    const props = {
      selectedNodeData: makeNode({ content: '旧名称' }),
      nodeDetail: makeNodeDetail({
        relations: [makeRelation({ predicate: '', text: '无名关系' })],
        paragraphs: [
          makeParagraph({
            source: '',
            preview: '',
            content: '仅正文',
            updated_at: 0,
            entities: extraEntities,
          }),
        ],
      }),
      onOpenEvidence,
      onDeleteEntity,
      onDeleteRelation,
      onDeleteParagraph,
    }

    const view = render(<NodeDetailDialog open onOpenChange={onOpenChange} {...props} />)
    const dialog = screen.getByRole('dialog', { name: '实体详情' })

    expect(dialog).toHaveTextContent('未命名谓词')
    expect(dialog).toHaveTextContent('未命名来源')
    expect(dialog).toHaveTextContent('仅正文')
    expect(dialog).toHaveTextContent('更新时间 未知')
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

    view.rerender(<NodeDetailDialog open={false} onOpenChange={onOpenChange} {...props} />)
    view.rerender(<NodeDetailDialog open onOpenChange={onOpenChange} {...props} />)
    expect(screen.getByLabelText('删除该实体相关证据段落')).toHaveAttribute('data-state', 'unchecked')
    await user.click(screen.getByRole('button', { name: '删除实体' }))
    expect(onDeleteEntity).toHaveBeenLastCalledWith({ includeParagraphs: false })
  })
})

describe('EdgeDetailDialog', () => {
  it('关闭时不渲染对话框', () => {
    render(
      <EdgeDetailDialog
        open={false}
        onOpenChange={vi.fn()}
        selectedEdgeData={makeSelectedEdge()}
        edgeDetail={makeEdgeDetail()}
      />,
    )

    expect(screen.queryByRole('dialog', { name: '关系详情' })).not.toBeInTheDocument()
  })

  it('打开但未选中关系时展示空态', () => {
    render(
      <EdgeDetailDialog open onOpenChange={vi.fn()} selectedEdgeData={null} edgeDetail={null} />,
    )

    expect(screen.getByRole('dialog', { name: '关系详情' })).toHaveTextContent('尚未选中关系。')
  })

  it('仅有选中边时回退标签与计数，关闭按钮触发 onOpenChange', async () => {
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

  it('只有 edgeDetail 时用详情端点与计数', () => {
    render(
      <EdgeDetailDialog
        open
        onOpenChange={vi.fn()}
        selectedEdgeData={null}
        edgeDetail={makeEdgeDetail()}
      />,
    )

    const dialog = screen.getByRole('dialog', { name: '关系详情' })
    expect(dialog).toHaveTextContent('alpha → beta')
    expect(dialog).toHaveTextContent('关系 3')
    expect(dialog).toHaveTextContent('证据 5')
    expect(dialog).toHaveTextContent('聚合权重 2.5000')
    expect(dialog).toHaveTextContent('关联')
    expect(dialog).toHaveTextContent('认识')
  })

  it('加载中隐藏列表；提交删除关系组时带上段落选项并在重开后重置', async () => {
    const user = userEvent.setup()
    const onOpenEvidence = vi.fn()
    const onDeleteEdgeGroup = vi.fn()
    const onDeleteRelation = vi.fn()
    const onDeleteParagraph = vi.fn()
    const props = {
      selectedEdgeData: makeSelectedEdge(),
      edgeDetail: makeEdgeDetail(),
      onOpenEvidence,
      onDeleteEdgeGroup,
      onDeleteRelation,
      onDeleteParagraph,
    }

    const view = render(
      <EdgeDetailDialog open onOpenChange={vi.fn()} selectedEdgeData={makeSelectedEdge()} edgeDetail={null} loading onOpenEvidence={onOpenEvidence} onDeleteEdgeGroup={onDeleteEdgeGroup} />,
    )
    expect(screen.getByRole('status', { name: '加载中' })).toBeInTheDocument()
    expect(screen.queryByText('关系语义')).not.toBeInTheDocument()

    view.rerender(<EdgeDetailDialog open onOpenChange={vi.fn()} {...props} />)

    await user.click(screen.getByRole('button', { name: '切到证据视图' }))
    expect(onOpenEvidence).toHaveBeenCalledTimes(1)
    await user.click(screen.getByRole('button', { name: '删除关系' }))
    expect(onDeleteRelation).toHaveBeenCalledWith(expect.objectContaining({ hash: 'rel-1' }))
    await user.click(screen.getByRole('button', { name: '删除段落' }))
    expect(onDeleteParagraph).toHaveBeenCalledWith(expect.objectContaining({ hash: 'p-1' }))

    await user.click(screen.getByRole('button', { name: '删除此关系组' }))
    expect(onDeleteEdgeGroup).toHaveBeenCalledWith({ includeParagraphs: false })
    await user.click(screen.getByLabelText('同时删除支撑段落'))
    await user.click(screen.getByRole('button', { name: '删除此关系组' }))
    expect(onDeleteEdgeGroup).toHaveBeenLastCalledWith({ includeParagraphs: true })

    view.rerender(<EdgeDetailDialog open={false} onOpenChange={vi.fn()} {...props} />)
    view.rerender(<EdgeDetailDialog open onOpenChange={vi.fn()} {...props} />)
    expect(screen.getByLabelText('同时删除支撑段落')).toHaveAttribute('data-state', 'unchecked')
  })
})

describe('RelationDetailDialog', () => {
  it('relation 为空时不渲染', () => {
    render(<RelationDetailDialog open onOpenChange={vi.fn()} relation={null} />)
    expect(screen.queryByRole('dialog', { name: '关系明细' })).not.toBeInTheDocument()
  })

  it('关闭时即使有 relation 也不展示对话框', () => {
    render(
      <RelationDetailDialog open={false} onOpenChange={vi.fn()} relation={makeRelation()} />,
    )
    expect(screen.queryByRole('dialog', { name: '关系明细' })).not.toBeInTheDocument()
  })

  it('谓词与 metadata 都缺失时显示未命名谓词，且无删除按钮', () => {
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

  it('谓词回退 metadata，提交删除带段落选项，关闭后再开重置勾选', async () => {
    const user = userEvent.setup()
    const onOpenChange = vi.fn()
    const onDeleteRelation = vi.fn()
    const props = {
      relation: makeRelation({ predicate: '' }),
      metadata: { predicate: '认识' },
      onDeleteRelation,
    }
    const view = render(<RelationDetailDialog open onOpenChange={onOpenChange} {...props} />)

    const dialog = screen.getByRole('dialog', { name: '关系明细' })
    expect(dialog).toHaveTextContent('认识')
    expect(dialog).toHaveTextContent('证据段落 2')
    expect(dialog).toHaveTextContent('0.900')
    expect(dialog).toHaveTextContent('rel-1')

    await user.click(screen.getByRole('button', { name: '删除这条关系' }))
    expect(onDeleteRelation).toHaveBeenCalledWith(expect.objectContaining({ hash: 'rel-1' }), false)

    await user.click(screen.getByLabelText('同时删除支撑该关系的段落'))
    await user.click(screen.getByRole('button', { name: '删除这条关系' }))
    expect(onDeleteRelation).toHaveBeenLastCalledWith(
      expect.objectContaining({ hash: 'rel-1' }),
      true,
    )

    await user.click(within(dialog).getByRole('button', { name: '关闭' }))
    expect(onOpenChange).toHaveBeenCalledWith(false)

    view.rerender(<RelationDetailDialog open={false} onOpenChange={onOpenChange} {...props} />)
    view.rerender(<RelationDetailDialog open onOpenChange={onOpenChange} {...props} />)
    expect(screen.getByLabelText('同时删除支撑该关系的段落')).toHaveAttribute(
      'data-state',
      'unchecked',
    )
  })
})

describe('ParagraphDetailDialog', () => {
  it('paragraph 为空时不渲染', () => {
    render(<ParagraphDetailDialog open onOpenChange={vi.fn()} paragraph={null} />)
    expect(screen.queryByRole('dialog', { name: '段落明细' })).not.toBeInTheDocument()
  })

  it('关闭时即使有 paragraph 也不展示对话框', () => {
    render(
      <ParagraphDetailDialog open={false} onOpenChange={vi.fn()} paragraph={makeParagraph()} />,
    )
    expect(screen.queryByRole('dialog', { name: '段落明细' })).not.toBeInTheDocument()
  })

  it('来源和时间可回退 metadata，确认删除或关闭', async () => {
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
    expect(dialog).toHaveTextContent('p-1')

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
