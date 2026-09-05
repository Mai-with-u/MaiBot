/**
 * useKnowledgeGraphPage —— 长期记忆图谱页状态机（页面逻辑下沉）。
 *
 * 收编图谱加载、全库检索/本地 fallback、实体/证据视图切换、节点/边/段落详情、
 * 删除预览-执行-恢复，以及 initialParagraphHash 深链接定位。
 * 不套 useDataList（图谱不是 {items,total} 分页列表）。
 */
import { useCallback, useEffect, useMemo, useState, type Dispatch, type SetStateAction } from 'react'

import { useNavigate } from '@tanstack/react-router'

import type { Edge, Node } from 'reactflow'

import { useToast } from '@/hooks/use-toast'
import {
  executeMemoryDelete,
  getMemoryGraph,
  getMemoryGraphEdgeDetail,
  getMemoryGraphNodeDetail,
  getMemoryGraphParagraphDetail,
  getMemoryGraphSearch,
  previewMemoryDelete,
  restoreMemoryDelete,
  type MemoryDeleteExecutePayload,
  type MemoryDeleteRequestPayload,
  type MemoryEvidenceParagraphNodeMetadata,
  type MemoryEvidenceRelationNodeMetadata,
  type MemoryGraphEdgeDetailPayload,
  type MemoryGraphNodeDetailPayload,
  type MemoryGraphParagraphDetailPayload,
  type MemoryGraphPayload,
  type MemoryGraphRelationDetailPayload,
  type MemoryGraphSearchItem,
} from '@/lib/memory-api'

import type { GraphData, GraphNode, GraphViewMode, SelectedEdgeData } from './types'
import {
  buildParagraphFromMetadata,
  buildRelationFromMetadata,
  filterGraphData,
  mergeUniqueParagraphs,
  mergeUniqueRelations,
  toEntityGraphData,
  toEvidenceGraphData,
} from './utils'

type GraphRestoreTarget =
  | { type: 'entity'; nodeId: string; viewMode: GraphViewMode }
  | { type: 'edge'; source: string; target: string; viewMode: GraphViewMode }
  | { type: 'paragraph'; paragraphHash: string; viewMode: GraphViewMode }
  | { type: 'view'; viewMode: GraphViewMode }

type DeleteDraft = {
  title: string
  description: string
  request: MemoryDeleteRequestPayload
  restoreTarget: GraphRestoreTarget
}

export interface UseKnowledgeGraphPageOptions {
  initialParagraphHash?: string
  onOpenConsole?: () => void
}

export interface KnowledgeGraphPageStats {
  totalNodes: number
  totalEdges: number
  visibleNodes: number
  visibleEdges: number
  evidenceNodes: number
  evidenceEdges: number
}

export interface UseKnowledgeGraphPageResult {
  loading: boolean
  nodeLimit: string
  setNodeLimit: Dispatch<SetStateAction<string>>
  searchInput: string
  setSearchInput: Dispatch<SetStateAction<string>>
  appliedSearchQuery: string
  searchLoading: boolean
  searchResults: MemoryGraphSearchItem[]
  searchFallbackMode: boolean
  viewMode: GraphViewMode
  setViewMode: Dispatch<SetStateAction<GraphViewMode>>
  graphData: GraphData
  stats: KnowledgeGraphPageStats
  handleSearch: () => Promise<void>
  loadGraph: (options?: { silent?: boolean; keepSelection?: boolean }) => Promise<void>
  selectedNodeData: GraphNode | null
  setSelectedNodeData: Dispatch<SetStateAction<GraphNode | null>>
  selectedEdgeData: SelectedEdgeData | null
  setSelectedEdgeData: Dispatch<SetStateAction<SelectedEdgeData | null>>
  nodeDetail: MemoryGraphNodeDetailPayload | null
  edgeDetail: MemoryGraphEdgeDetailPayload | null
  selectedRelationDetail: MemoryGraphRelationDetailPayload | null
  setSelectedRelationDetail: Dispatch<SetStateAction<MemoryGraphRelationDetailPayload | null>>
  selectedRelationMetadata: MemoryEvidenceRelationNodeMetadata | null
  selectedParagraphDetail: MemoryGraphParagraphDetailPayload | null
  setSelectedParagraphDetail: Dispatch<SetStateAction<MemoryGraphParagraphDetailPayload | null>>
  selectedParagraphMetadata: MemoryEvidenceParagraphNodeMetadata | null
  detailLoading: boolean
  deleteDraft: DeleteDraft | null
  deletePreviewLoading: boolean
  deletePreviewError: string | null
  deleteResult: MemoryDeleteExecutePayload | null
  deleteExecuting: boolean
  deleteRestoring: boolean
  deletePreview: Awaited<ReturnType<typeof previewMemoryDelete>> | null
  handleNodeClick: (event: React.MouseEvent, node: Node) => void
  handleEdgeClick: (event: React.MouseEvent, edge: Edge) => void
  handleSearchResultClick: (item: MemoryGraphSearchItem) => void
  handleEvidenceNodeClick: (event: React.MouseEvent, node: Node) => Promise<void>
  handleOpenNodeEvidence: () => void
  handleOpenEdgeEvidence: () => void
  requestDeleteEntity: (options: { includeParagraphs: boolean }) => void
  requestDeleteEdgeGroup: (options: { includeParagraphs: boolean }) => void
  requestDeleteRelation: (relation: MemoryGraphRelationDetailPayload, includeParagraphs?: boolean) => void
  requestDeleteParagraph: (paragraph: MemoryGraphParagraphDetailPayload) => void
  closeDeleteDialog: (open: boolean) => void
  executeCurrentDelete: () => Promise<void>
  restoreCurrentDelete: () => Promise<void>
  activeGraph: GraphData
  canShowEvidence: boolean
  openConsole: () => void
}

export function useKnowledgeGraphPage({
  initialParagraphHash = '',
  onOpenConsole,
}: UseKnowledgeGraphPageOptions = {}): UseKnowledgeGraphPageResult {
  const navigate = useNavigate()
  const { toast } = useToast()
  const [loading, setLoading] = useState(false)
  const [nodeLimit, setNodeLimit] = useState('120')
  const [searchInput, setSearchInput] = useState('')
  const [appliedSearchQuery, setAppliedSearchQuery] = useState('')
  const [searchLoading, setSearchLoading] = useState(false)
  const [searchResults, setSearchResults] = useState<MemoryGraphSearchItem[]>([])
  const [searchFallbackMode, setSearchFallbackMode] = useState(false)
  const [viewMode, setViewMode] = useState<GraphViewMode>('entity')
  const [fullGraph, setFullGraph] = useState<GraphData>({ nodes: [], edges: [] })
  const [graphData, setGraphData] = useState<GraphData>({ nodes: [], edges: [] })
  const [evidenceGraph, setEvidenceGraph] = useState<GraphData>({ nodes: [], edges: [] })
  const [graphMeta, setGraphMeta] = useState<MemoryGraphPayload | null>(null)
  const [selectedNodeData, setSelectedNodeData] = useState<GraphNode | null>(null)
  const [selectedEdgeData, setSelectedEdgeData] = useState<SelectedEdgeData | null>(null)
  const [nodeDetail, setNodeDetail] = useState<MemoryGraphNodeDetailPayload | null>(null)
  const [edgeDetail, setEdgeDetail] = useState<MemoryGraphEdgeDetailPayload | null>(null)
  const [selectedRelationDetail, setSelectedRelationDetail] = useState<MemoryGraphRelationDetailPayload | null>(null)
  const [selectedRelationMetadata, setSelectedRelationMetadata] = useState<MemoryEvidenceRelationNodeMetadata | null>(null)
  const [selectedParagraphDetail, setSelectedParagraphDetail] = useState<MemoryGraphParagraphDetailPayload | null>(null)
  const [selectedParagraphMetadata, setSelectedParagraphMetadata] = useState<MemoryEvidenceParagraphNodeMetadata | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [deleteDraft, setDeleteDraft] = useState<DeleteDraft | null>(null)
  const [deletePreviewLoading, setDeletePreviewLoading] = useState(false)
  const [deletePreviewError, setDeletePreviewError] = useState<string | null>(null)
  const [deleteResult, setDeleteResult] = useState<MemoryDeleteExecutePayload | null>(null)
  const [deleteExecuting, setDeleteExecuting] = useState(false)
  const [deleteRestoring, setDeleteRestoring] = useState(false)
  const [deletePreview, setDeletePreview] = useState<Awaited<ReturnType<typeof previewMemoryDelete>> | null>(null)
  const [appliedInitialParagraphHash, setAppliedInitialParagraphHash] = useState('')

  const allRelationDetails = useMemo(
    () => mergeUniqueRelations(nodeDetail, edgeDetail),
    [edgeDetail, nodeDetail],
  )
  const allParagraphDetails = useMemo(
    () => mergeUniqueParagraphs(nodeDetail, edgeDetail),
    [edgeDetail, nodeDetail],
  )

  const resetDetailSelections = useCallback(() => {
    setSelectedNodeData(null)
    setSelectedEdgeData(null)
    setNodeDetail(null)
    setEdgeDetail(null)
    setSelectedRelationDetail(null)
    setSelectedRelationMetadata(null)
    setSelectedParagraphDetail(null)
    setSelectedParagraphMetadata(null)
  }, [])

  const loadGraph = useCallback(async (options?: { silent?: boolean; keepSelection?: boolean }) => {
    try {
      setLoading(true)
      const payload = await getMemoryGraph(Number(nodeLimit))
      const nextGraph = toEntityGraphData(payload)
      const visibleGraph = searchFallbackMode && appliedSearchQuery
        ? filterGraphData(nextGraph, appliedSearchQuery)
        : nextGraph
      setGraphMeta(payload)
      setFullGraph(nextGraph)
      setGraphData(visibleGraph)
      if (!options?.keepSelection) {
        setEvidenceGraph({ nodes: [], edges: [] })
        resetDetailSelections()
      }
      if (!options?.silent) {
        toast({
          title: '图谱已更新',
          description: `当前加载 ${nextGraph.nodes.length} 个节点、${nextGraph.edges.length} 条关系`,
        })
      }
    } catch (error) {
      toast({
        title: '加载失败',
        description: error instanceof Error ? error.message : '未知错误',
        variant: 'destructive',
      })
    } finally {
      setLoading(false)
    }
  }, [appliedSearchQuery, nodeLimit, resetDetailSelections, searchFallbackMode, toast])

  useEffect(() => {
    void loadGraph({ silent: true, keepSelection: Boolean(initialParagraphHash.trim()) })
  }, [initialParagraphHash, loadGraph])

  const handleSearch = useCallback(async () => {
    const nextQuery = searchInput.trim()
    if (!nextQuery) {
      setAppliedSearchQuery('')
      setSearchFallbackMode(false)
      setSearchResults([])
      setGraphData(fullGraph)
      toast({
        title: '已重置筛选',
        description: `当前显示 ${fullGraph.nodes.length} 个节点、${fullGraph.edges.length} 条关系`,
      })
      return
    }

    setSearchLoading(true)
    setAppliedSearchQuery(nextQuery)
    try {
      const payload = await getMemoryGraphSearch(nextQuery, 50)
      if (!payload.success) {
        throw new Error(payload.error || '图谱检索失败')
      }
      const items = Array.isArray(payload.items) ? payload.items : []
      setSearchResults(items)
      setSearchFallbackMode(false)
      setGraphData(fullGraph)
      toast({
        title: '全库检索完成',
        description: `命中 ${payload.count ?? items.length} 条结果`,
      })
    } catch {
      const filtered = filterGraphData(fullGraph, nextQuery)
      setSearchResults([])
      setSearchFallbackMode(true)
      setGraphData(filtered)
      toast({
        title: '后端检索失败，已回退本地筛选',
        description: `仅当前已加载范围（${filtered.nodes.length} 个节点、${filtered.edges.length} 条关系）`,
        variant: 'destructive',
      })
    } finally {
      setSearchLoading(false)
    }
  }, [fullGraph, searchInput, toast])

  const stats = useMemo(
    () => ({
      totalNodes: graphMeta?.total_nodes ?? fullGraph.nodes.length,
      totalEdges: graphMeta?.total_edges ?? fullGraph.edges.length,
      visibleNodes: graphData.nodes.length,
      visibleEdges: graphData.edges.length,
      evidenceNodes: evidenceGraph.nodes.length,
      evidenceEdges: evidenceGraph.edges.length,
    }),
    [
      evidenceGraph.edges.length,
      evidenceGraph.nodes.length,
      fullGraph.edges.length,
      fullGraph.nodes.length,
      graphData.edges.length,
      graphData.nodes.length,
      graphMeta,
    ],
  )

  const openDeleteDialog = useCallback(async (draft: DeleteDraft) => {
    setDeleteDraft(draft)
    setDeletePreview(null)
    setDeleteResult(null)
    setDeletePreviewError(null)
    setDeletePreviewLoading(true)
    try {
      const preview = await previewMemoryDelete(draft.request)
      setDeletePreview(preview)
    } catch (error) {
      setDeletePreviewError(error instanceof Error ? error.message : '删除预览失败')
    } finally {
      setDeletePreviewLoading(false)
    }
  }, [])

  const closeDeleteDialog = useCallback((open: boolean) => {
    if (!open) {
      setDeleteDraft(null)
      setDeletePreview(null)
      setDeleteResult(null)
      setDeletePreviewError(null)
    }
  }, [])

  const openNodeDetail = useCallback(async (
    nodeId: string,
    options?: { locateInEvidence?: boolean },
  ) => {
    const nodeToken = String(nodeId || '').trim()
    if (!nodeToken) {
      return
    }
    const selected = graphData.nodes.find((item) => item.id === nodeToken)
    if (options?.locateInEvidence) {
      setSelectedNodeData(null)
    } else {
      setSelectedNodeData(
        selected ?? {
          id: nodeToken,
          type: 'entity',
          content: nodeToken,
          metadata: {},
        },
      )
    }
    setSelectedEdgeData(null)
    setNodeDetail(null)
    setEdgeDetail(null)
    setSelectedRelationDetail(null)
    setSelectedRelationMetadata(null)
    setSelectedParagraphDetail(null)
    setSelectedParagraphMetadata(null)
    try {
      setDetailLoading(true)
      const detail = await getMemoryGraphNodeDetail(nodeToken)
      setNodeDetail(detail)
      setEvidenceGraph(toEvidenceGraphData(detail.evidence_graph))
      if (options?.locateInEvidence) {
        setViewMode('evidence')
      }
    } catch (error) {
      setSelectedNodeData(null)
      setNodeDetail(null)
      setEvidenceGraph({ nodes: [], edges: [] })
      setViewMode('entity')
      toast({
        title: '加载节点详情失败',
        description: error instanceof Error ? error.message : '未知错误',
        variant: 'destructive',
      })
    } finally {
      setDetailLoading(false)
    }
  }, [graphData.nodes, toast])

  const openEdgeDetail = useCallback(async (
    source: string,
    target: string,
    options?: { locateInEvidence?: boolean },
  ) => {
    const sourceToken = String(source || '').trim()
    const targetToken = String(target || '').trim()
    if (!sourceToken || !targetToken) {
      return
    }
    setSelectedNodeData(null)
    setNodeDetail(null)
    setEdgeDetail(null)
    setSelectedRelationDetail(null)
    setSelectedRelationMetadata(null)
    setSelectedParagraphDetail(null)
    setSelectedParagraphMetadata(null)
    if (options?.locateInEvidence) {
      setSelectedEdgeData(null)
    } else {
      const sourceNode = graphData.nodes.find((nodeItem) => nodeItem.id === sourceToken) ?? {
        id: sourceToken,
        type: 'entity' as const,
        content: sourceToken,
        metadata: {},
      }
      const targetNode = graphData.nodes.find((nodeItem) => nodeItem.id === targetToken) ?? {
        id: targetToken,
        type: 'entity' as const,
        content: targetToken,
        metadata: {},
      }
      const edgeData = graphData.edges.find((item) => item.source === sourceToken && item.target === targetToken) ?? {
        source: sourceToken,
        target: targetToken,
        weight: 1,
        kind: 'relation' as const,
        label: '',
        relationHashes: [],
        predicates: [],
        relationCount: 0,
        evidenceCount: 0,
      }
      setSelectedEdgeData({
        source: sourceNode,
        target: targetNode,
        edge: edgeData,
      })
    }
    try {
      setDetailLoading(true)
      const detail = await getMemoryGraphEdgeDetail(sourceToken, targetToken)
      setEdgeDetail(detail)
      setEvidenceGraph(toEvidenceGraphData(detail.evidence_graph))
      if (options?.locateInEvidence) {
        setViewMode('evidence')
      }
    } catch (error) {
      setSelectedEdgeData(null)
      setEdgeDetail(null)
      setEvidenceGraph({ nodes: [], edges: [] })
      setViewMode('entity')
      toast({
        title: '加载关系详情失败',
        description: error instanceof Error ? error.message : '未知错误',
        variant: 'destructive',
      })
    } finally {
      setDetailLoading(false)
    }
  }, [graphData.edges, graphData.nodes, toast])

  const openParagraphDetail = useCallback(async (
    paragraphHash: string,
    options?: { silent?: boolean },
  ): Promise<boolean> => {
    const cleanHash = String(paragraphHash || '').trim()
    if (!cleanHash) {
      return false
    }
    setSelectedNodeData(null)
    setSelectedEdgeData(null)
    setNodeDetail(null)
    setEdgeDetail(null)
    setSelectedRelationDetail(null)
    setSelectedRelationMetadata(null)
    try {
      setDetailLoading(true)
      const detail = await getMemoryGraphParagraphDetail(cleanHash)
      setEvidenceGraph(toEvidenceGraphData(detail.evidence_graph))
      setSelectedParagraphDetail(detail.paragraph)
      setSelectedParagraphMetadata({
        hash: detail.paragraph.hash,
        source: detail.paragraph.source,
        updated_at: detail.paragraph.updated_at,
        entity_count: detail.paragraph.entity_count,
        relation_count: detail.paragraph.relation_count,
        preview: detail.paragraph.preview,
      })
      setViewMode('evidence')
      return true
    } catch (error) {
      setEvidenceGraph({ nodes: [], edges: [] })
      setSelectedParagraphDetail(null)
      setSelectedParagraphMetadata(null)
      setViewMode('entity')
      if (!options?.silent) {
        toast({
          title: '定位段落失败',
          description: error instanceof Error ? error.message : '未能找到这段记忆',
          variant: 'destructive',
        })
      }
      return false
    } finally {
      setDetailLoading(false)
    }
  }, [toast])

  const restoreGraphTarget = useCallback(async (target: GraphRestoreTarget) => {
    if (target.type === 'entity') {
      await openNodeDetail(target.nodeId, { locateInEvidence: target.viewMode === 'evidence' })
      if (target.viewMode === 'entity') {
        setViewMode('entity')
      }
      return
    }
    if (target.type === 'edge') {
      await openEdgeDetail(target.source, target.target, { locateInEvidence: target.viewMode === 'evidence' })
      if (target.viewMode === 'entity') {
        setViewMode('entity')
      }
      return
    }
    if (target.type === 'paragraph') {
      const restored = await openParagraphDetail(target.paragraphHash, { silent: true })
      if (!restored) {
        toast({
          title: '已刷新图谱',
          description: '原段落已被删除，当前返回实体关系图。',
        })
      }
      return
    }
    setViewMode(target.viewMode)
  }, [openEdgeDetail, openNodeDetail, openParagraphDetail, toast])

  const getCurrentRestoreTarget = useCallback((fallback?: GraphRestoreTarget): GraphRestoreTarget => {
    if (nodeDetail?.node.id) {
      return { type: 'entity', nodeId: nodeDetail.node.id, viewMode }
    }
    if (edgeDetail?.edge.source && edgeDetail.edge.target) {
      return { type: 'edge', source: edgeDetail.edge.source, target: edgeDetail.edge.target, viewMode }
    }
    if (selectedNodeData?.id) {
      return { type: 'entity', nodeId: selectedNodeData.id, viewMode }
    }
    if (selectedEdgeData?.source.id && selectedEdgeData.target.id) {
      return { type: 'edge', source: selectedEdgeData.source.id, target: selectedEdgeData.target.id, viewMode }
    }
    if (selectedParagraphDetail?.hash) {
      return { type: 'paragraph', paragraphHash: selectedParagraphDetail.hash, viewMode }
    }
    return fallback ?? { type: 'view', viewMode }
  }, [edgeDetail?.edge.source, edgeDetail?.edge.target, nodeDetail?.node.id, selectedEdgeData, selectedNodeData, selectedParagraphDetail?.hash, viewMode])

  useEffect(() => {
    const cleanHash = initialParagraphHash.trim()
    if (!cleanHash || cleanHash === appliedInitialParagraphHash) {
      return
    }
    setAppliedInitialParagraphHash(cleanHash)
    void openParagraphDetail(cleanHash)
  }, [appliedInitialParagraphHash, initialParagraphHash, openParagraphDetail])

  const executeCurrentDelete = useCallback(async () => {
    if (!deleteDraft) {
      return
    }
    try {
      setDeleteExecuting(true)
      const result = await executeMemoryDelete(deleteDraft.request)
      setDeleteResult(result)
      toast({
        title: result.success ? '删除成功' : '删除失败',
        description: result.success
          ? `操作 ${result.operation_id} 已完成`
          : result.error || '未能执行删除',
        variant: result.success ? 'default' : 'destructive',
      })
      if (result.success) {
        await loadGraph({ silent: true, keepSelection: true })
        await restoreGraphTarget(deleteDraft.restoreTarget)
      }
    } catch (error) {
      setDeletePreviewError(error instanceof Error ? error.message : '删除失败')
      toast({
        title: '删除失败',
        description: error instanceof Error ? error.message : '未知错误',
        variant: 'destructive',
      })
    } finally {
      setDeleteExecuting(false)
    }
  }, [deleteDraft, loadGraph, restoreGraphTarget, toast])

  const restoreCurrentDelete = useCallback(async () => {
    if (!deleteResult?.operation_id) {
      return
    }
    try {
      setDeleteRestoring(true)
      await restoreMemoryDelete({
        operation_id: deleteResult.operation_id,
        requested_by: 'knowledge_graph',
      })
      toast({
        title: '恢复成功',
        description: `删除操作 ${deleteResult.operation_id} 已恢复`,
      })
      const restoreTarget = deleteDraft?.restoreTarget ?? getCurrentRestoreTarget()
      closeDeleteDialog(false)
      await loadGraph({ silent: true, keepSelection: true })
      await restoreGraphTarget(restoreTarget)
    } catch (error) {
      toast({
        title: '恢复失败',
        description: error instanceof Error ? error.message : '未知错误',
        variant: 'destructive',
      })
    } finally {
      setDeleteRestoring(false)
    }
  }, [closeDeleteDialog, deleteDraft?.restoreTarget, deleteResult?.operation_id, getCurrentRestoreTarget, loadGraph, restoreGraphTarget, toast])

  const handleNodeClick = useCallback((_: React.MouseEvent, node: Node) => {
    void openNodeDetail(node.id)
  }, [openNodeDetail])

  const handleEdgeClick = useCallback((_: React.MouseEvent, edge: Edge) => {
    void openEdgeDetail(edge.source, edge.target)
  }, [openEdgeDetail])

  const handleSearchResultClick = useCallback((item: MemoryGraphSearchItem) => {
    if (item.type === 'entity') {
      const entityName = String(item.entity_name ?? item.title ?? '').trim()
      if (!entityName) {
        return
      }
      void openNodeDetail(entityName, { locateInEvidence: true })
      return
    }
    const source = String(item.subject ?? '').trim()
    const target = String(item.object ?? '').trim()
    if (!source || !target) {
      toast({
        title: '结果缺少定位信息',
        description: '该关系记录没有可用的 subject/object，无法定位。',
        variant: 'destructive',
      })
      return
    }
    void openEdgeDetail(source, target, { locateInEvidence: true })
  }, [openEdgeDetail, openNodeDetail, toast])

  const handleEvidenceNodeClick = useCallback(async (_: React.MouseEvent, node: Node) => {
    const selected = evidenceGraph.nodes.find((item) => item.id === node.id)
    if (!selected) {
      return
    }

    if (selected.type === 'entity') {
      const entityName =
        String((selected.metadata as Record<string, unknown> | undefined)?.entity_name ?? '').trim() || selected.content
      try {
        setDetailLoading(true)
        const detail = await getMemoryGraphNodeDetail(entityName)
        setSelectedNodeData({
          id: detail.node.id,
          type: 'entity',
          content: detail.node.content,
          metadata: { hash: detail.node.hash },
        })
        setSelectedEdgeData(null)
        setNodeDetail(detail)
      } catch (error) {
        toast({
          title: '加载实体详情失败',
          description: error instanceof Error ? error.message : '未知错误',
          variant: 'destructive',
        })
      } finally {
        setDetailLoading(false)
      }
      return
    }

    if (selected.type === 'relation') {
      const metadata = (selected.metadata ?? {}) as MemoryEvidenceRelationNodeMetadata
      const hash = String(metadata.hash ?? '').trim()
      const relation =
        allRelationDetails.find((item) => item.hash === hash) ?? buildRelationFromMetadata(metadata)
      setSelectedRelationMetadata(metadata)
      setSelectedRelationDetail(relation)
      setSelectedParagraphDetail(null)
      return
    }

    if (selected.type === 'paragraph') {
      const metadata = (selected.metadata ?? {}) as MemoryEvidenceParagraphNodeMetadata
      const hash = String(metadata.hash ?? '').trim()
      const paragraph =
        allParagraphDetails.find((item) => item.hash === hash) ?? buildParagraphFromMetadata(metadata)
      setSelectedParagraphMetadata(metadata)
      setSelectedParagraphDetail(paragraph)
      setSelectedRelationDetail(null)
    }
  }, [allParagraphDetails, allRelationDetails, evidenceGraph.nodes, toast])

  const handleOpenNodeEvidence = useCallback(() => {
    setViewMode('evidence')
    setSelectedNodeData(null)
  }, [])

  const handleOpenEdgeEvidence = useCallback(() => {
    setViewMode('evidence')
    setSelectedEdgeData(null)
  }, [])

  const requestDeleteEntity = useCallback(({ includeParagraphs }: { includeParagraphs: boolean }) => {
    const entityHash = String(nodeDetail?.node.hash ?? '').trim()
    if (!entityHash) {
      toast({
        title: '缺少实体标识',
        description: '当前实体没有可用的 hash，无法执行删除。',
        variant: 'destructive',
      })
      return
    }
    void openDeleteDialog({
      title: '删除实体',
      description: '将删除该实体，并自动包含与该实体关联的关系。可按需额外删除支撑段落。',
      restoreTarget: getCurrentRestoreTarget({
        type: 'entity',
        nodeId: String(nodeDetail?.node.id ?? nodeDetail?.node.content ?? ''),
        viewMode,
      }),
      request: {
        mode: 'mixed',
        selector: {
          entity_hashes: [entityHash],
          paragraph_hashes: includeParagraphs ? (nodeDetail?.paragraphs ?? []).map((item) => item.hash) : [],
        },
        reason: 'knowledge_graph_delete_entity',
        requested_by: 'knowledge_graph',
      },
    })
  }, [getCurrentRestoreTarget, nodeDetail, openDeleteDialog, toast, viewMode])

  const requestDeleteEdgeGroup = useCallback(({ includeParagraphs }: { includeParagraphs: boolean }) => {
    const relationHashes = edgeDetail?.edge.relation_hashes ?? []
    if (relationHashes.length <= 0) {
      toast({
        title: '缺少关系标识',
        description: '当前关系组没有可用的 relation hash。',
        variant: 'destructive',
      })
      return
    }
    void openDeleteDialog({
      title: '删除关系组',
      description: '将删除这条聚合边对应的全部关系。可按需额外删除支撑段落。',
      restoreTarget: getCurrentRestoreTarget({
        type: 'edge',
        source: String(edgeDetail?.edge.source ?? ''),
        target: String(edgeDetail?.edge.target ?? ''),
        viewMode,
      }),
      request: {
        mode: 'mixed',
        selector: {
          relation_hashes: relationHashes,
          paragraph_hashes: includeParagraphs ? (edgeDetail?.paragraphs ?? []).map((item) => item.hash) : [],
        },
        reason: 'knowledge_graph_delete_edge_group',
        requested_by: 'knowledge_graph',
      },
    })
  }, [edgeDetail, getCurrentRestoreTarget, openDeleteDialog, toast, viewMode])

  const requestDeleteRelation = useCallback(
    (relation: MemoryGraphRelationDetailPayload, includeParagraphs = false) => {
      void openDeleteDialog({
        title: '删除关系',
        description: includeParagraphs ? '将删除这条关系及其支撑段落。' : '将只删除这条关系，保留段落证据。',
        restoreTarget: getCurrentRestoreTarget({ type: 'view', viewMode }),
        request: {
          mode: 'mixed',
          selector: {
            relation_hashes: [relation.hash],
            paragraph_hashes: includeParagraphs ? relation.paragraph_hashes : [],
          },
          reason: 'knowledge_graph_delete_relation',
          requested_by: 'knowledge_graph',
        },
      })
    },
    [getCurrentRestoreTarget, openDeleteDialog, viewMode],
  )

  const requestDeleteParagraph = useCallback((paragraph: MemoryGraphParagraphDetailPayload) => {
    void openDeleteDialog({
      title: '删除段落证据',
      description: '将删除这段证据，并自动删除失去全部证据的关系。',
      restoreTarget: getCurrentRestoreTarget({
        type: 'paragraph',
        paragraphHash: paragraph.hash,
        viewMode,
      }),
      request: {
        mode: 'mixed',
        selector: {
          paragraph_hashes: [paragraph.hash],
        },
        reason: 'knowledge_graph_delete_paragraph',
        requested_by: 'knowledge_graph',
      },
    })
  }, [getCurrentRestoreTarget, openDeleteDialog, viewMode])

  const activeGraph = viewMode === 'entity' ? graphData : evidenceGraph
  const canShowEvidence = Boolean(selectedNodeData || selectedEdgeData || nodeDetail || edgeDetail)
  const openConsole = useCallback(() => {
    if (onOpenConsole) {
      onOpenConsole()
      return
    }
    void navigate({ to: '/resource/knowledge-base' })
  }, [navigate, onOpenConsole])

  return {
    loading,
    nodeLimit,
    setNodeLimit,
    searchInput,
    setSearchInput,
    appliedSearchQuery,
    searchLoading,
    searchResults,
    searchFallbackMode,
    viewMode,
    setViewMode,
    graphData,
    stats,
    handleSearch,
    loadGraph,
    selectedNodeData,
    setSelectedNodeData,
    selectedEdgeData,
    setSelectedEdgeData,
    nodeDetail,
    edgeDetail,
    selectedRelationDetail,
    setSelectedRelationDetail,
    selectedRelationMetadata,
    selectedParagraphDetail,
    setSelectedParagraphDetail,
    selectedParagraphMetadata,
    detailLoading,
    deleteDraft,
    deletePreviewLoading,
    deletePreviewError,
    deleteResult,
    deleteExecuting,
    deleteRestoring,
    deletePreview,
    handleNodeClick,
    handleEdgeClick,
    handleSearchResultClick,
    handleEvidenceNodeClick,
    handleOpenNodeEvidence,
    handleOpenEdgeEvidence,
    requestDeleteEntity,
    requestDeleteEdgeGroup,
    requestDeleteRelation,
    requestDeleteParagraph,
    closeDeleteDialog,
    executeCurrentDelete,
    restoreCurrentDelete,
    activeGraph,
    canShowEvidence,
    openConsole,
  }
}
