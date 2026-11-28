import { memo, useEffect, useMemo } from 'react'
import ReactFlow, {
  Background,
  BackgroundVariant,
  Controls,
  Handle,
  MarkerType,
  Position,
  type Edge,
  type Node,
  type NodeTypes,
  useNodesState,
} from 'reactflow'

import 'reactflow/dist/style.css'

import type { FlowEdge, FlowNode, FlowNodeData, GraphEdge, GraphNode } from './types'

const EntityNode = memo(({ data }: { data: FlowNodeData }) => {
  const evidenceClassName = data.layout === 'evidence'
    ? 'min-h-14 w-[13rem] rounded-2xl px-4 py-2.5'
    : 'min-h-12 min-w-12 max-w-[11rem] rounded-full px-4 py-2'
  const textClassName = data.layout === 'evidence'
    ? 'line-clamp-2 max-w-[11.5rem] whitespace-normal break-words text-xs font-semibold leading-snug text-white'
    : 'max-w-[9rem] truncate text-xs font-semibold leading-tight text-white'
  return (
    <div className={`flex cursor-grab items-center justify-center border border-blue-300/70 bg-blue-500/90 text-center shadow-[0_0_22px_rgba(37,99,235,0.26)] backdrop-blur active:cursor-grabbing ${evidenceClassName}`}>
      <Handle className="opacity-0" type="target" position={Position.Top} />
      <div className={textClassName} title={data.content}>
        {data.label}
      </div>
      <Handle className="opacity-0" type="source" position={Position.Bottom} />
    </div>
  )
})

EntityNode.displayName = 'EntityNode'

const ParagraphNode = memo(({ data }: { data: FlowNodeData }) => {
  const evidenceClassName = data.layout === 'evidence'
    ? 'min-h-14 w-[13rem] rounded-2xl px-4 py-2.5'
    : 'min-h-10 min-w-10 max-w-[10rem] rounded-full px-3 py-2'
  const textClassName = data.layout === 'evidence'
    ? 'line-clamp-2 max-w-[11.5rem] whitespace-normal break-words text-xs font-medium leading-snug text-white'
    : 'max-w-[8rem] truncate text-xs font-medium leading-tight text-white'
  return (
    <div className={`flex cursor-grab items-center justify-center border border-emerald-300/70 bg-emerald-500/90 text-center shadow-[0_0_18px_rgba(16,185,129,0.22)] backdrop-blur active:cursor-grabbing ${evidenceClassName}`}>
      <Handle className="opacity-0" type="target" position={Position.Top} />
      <div className={textClassName} title={data.content}>
        {data.label}
      </div>
      <Handle className="opacity-0" type="source" position={Position.Bottom} />
    </div>
  )
})

ParagraphNode.displayName = 'ParagraphNode'

const RelationNode = memo(({ data }: { data: FlowNodeData }) => {
  const evidenceClassName = data.layout === 'evidence'
    ? 'min-h-14 w-[13rem] rounded-2xl px-4 py-2.5'
    : 'min-h-11 min-w-11 max-w-[11rem] rounded-full px-3 py-2'
  const textClassName = data.layout === 'evidence'
    ? 'line-clamp-2 max-w-[11.5rem] whitespace-normal break-words text-xs font-medium leading-snug text-white'
    : 'max-w-[9rem] truncate text-xs font-medium leading-tight text-white'
  return (
    <div className={`flex cursor-grab items-center justify-center border border-orange-300/70 bg-orange-500/90 text-center shadow-[0_0_18px_rgba(249,115,22,0.24)] backdrop-blur active:cursor-grabbing ${evidenceClassName}`}>
      <Handle className="opacity-0" type="target" position={Position.Top} />
      <div className={textClassName} title={data.content}>
        {data.label}
      </div>
      <Handle className="opacity-0" type="source" position={Position.Bottom} />
    </div>
  )
})

RelationNode.displayName = 'RelationNode'

const nodeTypes: NodeTypes = {
  entity: EntityNode,
  relation: RelationNode,
  paragraph: ParagraphNode,
}

function hashString(input: string): number {
  let hash = 2166136261
  for (let index = 0; index < input.length; index += 1) {
    hash ^= input.charCodeAt(index)
    hash = Math.imul(hash, 16777619)
  }
  return hash >>> 0
}

function isEvidenceGraph(nodes: GraphNode[], edges: GraphEdge[]): boolean {
  return nodes.some((node) => node.type === 'paragraph') || edges.some((edge) => edge.kind && edge.kind !== 'relation')
}

function average(values: number[], fallback: number): number {
  if (values.length === 0) {
    return fallback
  }
  return values.reduce((sum, value) => sum + value, 0) / values.length
}

function fallbackAnchor(id: string, index: number, total: number, spacing: number): number {
  if (total <= 1) {
    return 0
  }
  const centeredIndex = index - (total - 1) / 2
  const jitter = ((hashString(id) % 1000) / 1000 - 0.5) * spacing * 0.24
  return centeredIndex * spacing + jitter
}

function createFlowNode(
  node: GraphNode,
  position: { x: number; y: number },
  labelLimit = 12,
): FlowNode {
  return {
    id: node.id,
    type: node.type,
    position,
    data: {
      label: node.content.slice(0, labelLimit) + (node.content.length > labelLimit ? '...' : ''),
      content: node.content,
      type: node.type,
      layout: 'evidence',
    },
    sourcePosition: Position.Bottom,
    targetPosition: Position.Top,
    zIndex: node.type === 'relation' ? 30 : 20,
  }
}

function calculateEvidenceLayout(nodes: GraphNode[], edges: GraphEdge[]): { nodes: FlowNode[]; edges: FlowEdge[] } {
  const flowNodes: FlowNode[] = []
  const flowEdges: FlowEdge[] = []
  const nodeById = new Map(nodes.map((node) => [node.id, node]))
  const degreeMap = new Map<string, number>()

  edges.forEach((edge) => {
    degreeMap.set(edge.source, (degreeMap.get(edge.source) ?? 0) + Math.max(edge.weight, 1))
    degreeMap.set(edge.target, (degreeMap.get(edge.target) ?? 0) + Math.max(edge.weight, 1))
  })

  const sortByImportance = (left: GraphNode, right: GraphNode) => {
    const degreeDelta = (degreeMap.get(right.id) ?? 0) - (degreeMap.get(left.id) ?? 0)
    return degreeDelta || left.content.localeCompare(right.content)
  }

  const relationNodes = nodes.filter((node) => node.type === 'relation').sort(sortByImportance)
  const paragraphNodes = nodes.filter((node) => node.type === 'paragraph').sort(sortByImportance)
  const entityNodes = nodes.filter((node) => node.type === 'entity').sort(sortByImportance)
  const relationSpacing = relationNodes.length <= 8 ? 380 : relationNodes.length <= 18 ? 340 : 300
  const paragraphY = -390
  const relationY = 0
  const entityY = 390
  const relationX = new Map<string, number>()

  relationNodes.forEach((node, index) => {
    const x = fallbackAnchor(node.id, index, relationNodes.length, relationSpacing)
    relationX.set(node.id, x)
    flowNodes.push(createFlowNode(node, { x, y: relationY }, 30))
  })

  const anchoredX = (nodeId: string, fallbackIndex: number, fallbackTotal: number) => {
    const linkedRelationXs = edges
      .filter((edge) => {
        if (edge.source === nodeId && relationX.has(edge.target)) {
          return true
        }
        return edge.target === nodeId && relationX.has(edge.source)
      })
      .map((edge) => relationX.get(edge.source) ?? relationX.get(edge.target))
      .filter((value): value is number => typeof value === 'number')
    return average(linkedRelationXs, fallbackAnchor(nodeId, fallbackIndex, fallbackTotal, relationSpacing))
  }

  const placeAnchoredLayer = (layerNodes: GraphNode[], baseY: number, spread: number, labelLimit: number) => {
    const buckets = new Map<number, GraphNode[]>()
    layerNodes.forEach((node, index) => {
      const anchor = Math.round(anchoredX(node.id, index, layerNodes.length) / 40) * 40
      buckets.set(anchor, [...(buckets.get(anchor) ?? []), node])
    })

    Array.from(buckets.entries())
      .sort(([left], [right]) => left - right)
      .forEach(([anchor, bucket]) => {
        bucket.sort(sortByImportance).forEach((node, index) => {
          const centeredIndex = index - (bucket.length - 1) / 2
          const rowOffset = bucket.length > 4 ? ((index % 3) - 1) * 78 : 0
          flowNodes.push(createFlowNode(node, {
            x: anchor + centeredIndex * spread,
            y: baseY + rowOffset,
          }, labelLimit))
        })
      })
  }

  placeAnchoredLayer(paragraphNodes, paragraphY, 236, 34)
  placeAnchoredLayer(entityNodes, entityY, 216, 28)

  nodes
    .filter((node) => !flowNodes.some((flowNode) => flowNode.id === node.id))
    .forEach((node, index) => {
      flowNodes.push(createFlowNode(node, {
        x: fallbackAnchor(node.id, index, nodes.length, relationSpacing),
        y: node.type === 'paragraph' ? paragraphY : node.type === 'entity' ? entityY : relationY,
      }))
    })

  edges.forEach((edge, index) => {
    const sourceNode = nodeById.get(edge.source)
    const targetNode = nodeById.get(edge.target)
    const strokeColor =
      edge.kind === 'mentions'
        ? '#10b981'
        : edge.kind === 'supports'
          ? '#f97316'
          : edge.kind === 'subject'
            ? '#60a5fa'
            : edge.kind === 'object'
              ? '#a78bfa'
              : '#64748b'
    const isCrossLayer = sourceNode?.type !== targetNode?.type
    const flowEdge: FlowEdge = {
      id: `edge-${index}`,
      source: edge.source,
      target: edge.target,
      type: 'smoothstep',
      animated: nodes.length <= 200,
      markerEnd: {
        type: MarkerType.ArrowClosed,
        width: 16,
        height: 16,
        color: strokeColor,
      },
      zIndex: 0,
      style: {
        strokeWidth: Math.min(Math.max(edge.weight, isCrossLayer ? 2.5 : 2), 5),
        opacity: isCrossLayer ? 0.88 : 0.68,
        stroke: strokeColor,
      },
      labelStyle: {
        fill: '#334155',
        fontSize: 12,
        fontWeight: 600,
      },
      labelBgPadding: [6, 2],
      labelBgBorderRadius: 6,
      labelBgStyle: { fill: 'rgba(255,255,255,0.9)', fillOpacity: 0.95 },
    }
    if (edge.label && (edge.kind === 'supports' || nodes.length <= 60)) {
      flowEdge.label = edge.label
    }
    flowEdges.push(flowEdge)
  })

  return { nodes: flowNodes, edges: flowEdges }
}

function calculateLayout(nodes: GraphNode[], edges: GraphEdge[]): { nodes: FlowNode[]; edges: FlowEdge[] } {
  if (isEvidenceGraph(nodes, edges)) {
    return calculateEvidenceLayout(nodes, edges)
  }

  const flowNodes: FlowNode[] = []
  const flowEdges: FlowEdge[] = []
  const degreeMap = new Map<string, number>()

  edges.forEach((edge) => {
    degreeMap.set(edge.source, (degreeMap.get(edge.source) ?? 0) + Math.max(edge.weight, 1))
    degreeMap.set(edge.target, (degreeMap.get(edge.target) ?? 0) + Math.max(edge.weight, 1))
  })

  const orderedNodes = [...nodes].sort((left, right) => {
    const degreeDelta = (degreeMap.get(right.id) ?? 0) - (degreeMap.get(left.id) ?? 0)
    return degreeDelta || left.id.localeCompare(right.id)
  })
  const nodeCount = Math.max(orderedNodes.length, 1)
  const goldenAngle = Math.PI * (3 - Math.sqrt(5))
  const baseRadius = nodeCount <= 80 ? 560 : nodeCount <= 160 ? 760 : 980
  const radiusScale = baseRadius / Math.sqrt(nodeCount)

  orderedNodes.forEach((node, index) => {
    const hashOffset = (hashString(node.id) % 1000) / 1000
    const radius = Math.sqrt(index + 0.75) * radiusScale * (0.86 + hashOffset * 0.22)
    const angle = index * goldenAngle + hashOffset * Math.PI * 2
    const typeOffset = node.type === 'relation' ? 0.8 : node.type === 'paragraph' ? -0.65 : 0
    const labelLength = node.content.length > 10 ? 18 : 14
    flowNodes.push({
      id: node.id,
      type: node.type,
      position: {
        x: Math.cos(angle) * radius - labelLength * 2,
        y: Math.sin(angle) * radius * 0.86 + typeOffset * 72,
      },
      data: {
        label: node.content.slice(0, 12) + (node.content.length > 12 ? '...' : ''),
        content: node.content,
        type: node.type,
      },
      sourcePosition: Position.Bottom,
      targetPosition: Position.Top,
    })
  })

  edges.forEach((edge, index) => {
    const isEvidenceEdge = edge.kind && edge.kind !== 'relation'
    const strokeColor =
      edge.kind === 'mentions'
        ? '#0f766e'
        : edge.kind === 'supports'
          ? '#b45309'
          : edge.kind === 'subject'
            ? '#4f46e5'
            : edge.kind === 'object'
              ? '#7c3aed'
              : '#475569'
    const flowEdge: FlowEdge = {
      id: `edge-${index}`,
      source: edge.source,
      target: edge.target,
      animated: nodes.length <= 200 && (isEvidenceEdge || edge.weight > 5),
      style: {
        strokeWidth: isEvidenceEdge
          ? Math.min(Math.max(edge.weight, 2), 5)
          : Math.min(Math.max(edge.weight / 2, 1.75), 5.5),
        opacity: isEvidenceEdge ? 0.95 : 0.78,
        stroke: strokeColor,
      },
      labelStyle: {
        fill: '#334155',
        fontSize: 12,
        fontWeight: 600,
      },
      labelBgPadding: [6, 2],
      labelBgBorderRadius: 6,
      labelBgStyle: { fill: 'rgba(255,255,255,0.88)', fillOpacity: 0.95 },
    }
    if (edge.label && (isEvidenceEdge || nodes.length <= 120)) {
      flowEdge.label = edge.label
    } else if (edge.weight > 10 && nodes.length < 100) {
      flowEdge.label = `${edge.weight.toFixed(0)}`
    }
    flowEdges.push(flowEdge)
  })

  return { nodes: flowNodes, edges: flowEdges }
}

interface GraphVisualizationProps {
  graphData: { nodes: GraphNode[]; edges: GraphEdge[] }
  onNodeClick: (event: React.MouseEvent, node: Node) => void
  onEdgeClick: (event: React.MouseEvent, edge: Edge) => void
  loading?: boolean
}

export function GraphVisualization({ graphData, onNodeClick, onEdgeClick, loading = false }: GraphVisualizationProps) {
  const { nodes: flowNodes, edges: flowEdges } = useMemo(
    () => calculateLayout(graphData.nodes, graphData.edges),
    [graphData.edges, graphData.nodes],
  )
  const [nodes, setNodes, onNodesChange] = useNodesState(flowNodes)
  const nodeCount = nodes.length

  useEffect(() => {
    setNodes(flowNodes)
  }, [flowNodes, setNodes])

  if (loading) {
    return null
  }

  return (
    <div
      style={{ touchAction: 'none' }}
      role="img"
      aria-label={`知识图谱可视化，共 ${nodeCount} 个节点，${flowEdges.length} 条关系`}
      className="w-full h-full"
    >
      <span className="sr-only">
        {`知识图谱包含 ${nodeCount} 个节点和 ${flowEdges.length} 条关系。`}
      </span>
      <ReactFlow
        nodes={nodes}
        edges={flowEdges}
        onNodesChange={onNodesChange}
        onNodeClick={onNodeClick}
        onEdgeClick={onEdgeClick}
        nodeTypes={nodeTypes}
        fitView
        fitViewOptions={{ padding: 0.32, duration: 260, maxZoom: 0.9 }}
        minZoom={0.05}
        maxZoom={2}
        defaultViewport={{ x: 0, y: 0, zoom: 0.34 }}
        elevateNodesOnSelect={nodeCount <= 500}
        nodesDraggable={nodeCount <= 1000}
        attributionPosition="bottom-left"
        panOnDrag
        zoomOnScroll
        zoomOnPinch
      >
        <Background variant={BackgroundVariant.Dots} gap={18} size={1} />
        <Controls />
      </ReactFlow>
    </div>
  )
}
