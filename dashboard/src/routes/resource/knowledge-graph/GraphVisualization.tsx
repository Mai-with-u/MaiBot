import { memo, useCallback } from 'react'
import ReactFlow, {
  Background,
  BackgroundVariant,
  Controls,
  Handle,
  MiniMap,
  Panel,
  Position,
  useEdgesState,
  useNodesState,
  type Edge,
  type Node,
  type NodeTypes,
} from 'reactflow'

import 'reactflow/dist/style.css'
import dagre from 'dagre'

import type { FlowEdge, FlowNode, GraphEdge, GraphNode } from './types'

const EntityNode = memo(({ data }: { data: { label: string; content: string } }) => {
  return (
    <div className="px-4 py-2 shadow-md rounded-lg bg-gradient-to-br from-blue-500 to-blue-600 border-2 border-blue-700 min-w-[120px]">
      <Handle type="target" position={Position.Top} />
      <div className="font-semibold text-white text-sm truncate max-w-[200px]" title={data.content}>
        {data.label}
      </div>
      <Handle type="source" position={Position.Bottom} />
    </div>
  )
})

EntityNode.displayName = 'EntityNode'

const ParagraphNode = memo(({ data }: { data: { label: string; content: string } }) => {
  return (
    <div className="px-3 py-2 shadow-md rounded-md bg-gradient-to-br from-green-500 to-green-600 border-2 border-green-700 min-w-[100px]">
      <Handle type="target" position={Position.Top} />
      <div className="font-medium text-white text-xs truncate max-w-[150px]" title={data.content}>
        {data.label}
      </div>
      <Handle type="source" position={Position.Bottom} />
    </div>
  )
})

ParagraphNode.displayName = 'ParagraphNode'

const nodeTypes: NodeTypes = {
  entity: EntityNode,
  paragraph: ParagraphNode,
}

function calculateLayout(nodes: GraphNode[], edges: GraphEdge[]): { nodes: FlowNode[]; edges: FlowEdge[] } {
  const dagreGraph = new dagre.graphlib.Graph()
  dagreGraph.setDefaultEdgeLabel(() => ({}))
  dagreGraph.setGraph({ rankdir: 'TB', ranksep: 100, nodesep: 80 })

  const flowNodes: FlowNode[] = []
  const flowEdges: FlowEdge[] = []

  nodes.forEach((node) => {
    dagreGraph.setNode(node.id, { width: 150, height: 50 })
  })

  edges.forEach((edge) => {
    dagreGraph.setEdge(edge.source, edge.target)
  })

  dagre.layout(dagreGraph)

  nodes.forEach((node) => {
    const nodeWithPosition = dagreGraph.node(node.id)
    flowNodes.push({
      id: node.id,
      type: node.type,
      position: {
        x: nodeWithPosition.x - 75,
        y: nodeWithPosition.y - 25,
      },
      data: {
        label: node.content.slice(0, 20) + (node.content.length > 20 ? '...' : ''),
        content: node.content,
      },
    })
  })

  edges.forEach((edge, index) => {
    const flowEdge: FlowEdge = {
      id: `edge-${index}`,
      source: edge.source,
      target: edge.target,
      animated: nodes.length <= 200 && edge.weight > 5,
      style: {
        strokeWidth: Math.min(edge.weight / 2, 5),
        opacity: 0.6,
      },
    }
    if (edge.weight > 10 && nodes.length < 100) {
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
  const { nodes: flowNodes, edges: flowEdges } = calculateLayout(graphData.nodes, graphData.edges)
  const [nodes, , onNodesChange] = useNodesState(flowNodes)
  const [edges, , onEdgesChange] = useEdgesState(flowEdges)
  const nodeCount = nodes.length

  const miniMapNodeColor = useCallback((node: Node) => {
    if (node.type === 'entity') return '#6366f1'
    if (node.type === 'paragraph') return '#10b981'
    return '#6b7280'
  }, [])

  if (loading) {
    return null
  }

  return (
    <div
      style={{ touchAction: 'none' }}
      role="img"
      aria-label={`知识图谱可视化，共 ${nodeCount} 个节点，${edges.length} 条关系`}
      className="w-full h-full"
    >
      <span className="sr-only">
        {`知识图谱包含 ${nodeCount} 个节点和 ${edges.length} 条关系。`}
      </span>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onNodeClick={onNodeClick}
        onEdgeClick={onEdgeClick}
        nodeTypes={nodeTypes}
        fitView
        minZoom={0.05}
        maxZoom={1.5}
        defaultViewport={{ x: 0, y: 0, zoom: 0.5 }}
        elevateNodesOnSelect={nodeCount <= 500}
        nodesDraggable={nodeCount <= 1000}
        attributionPosition="bottom-left"
        panOnScroll
        panOnScrollMode={undefined}
        panOnDrag
        zoomOnPinch
      >
        <Background variant={BackgroundVariant.Dots} gap={12} size={1} />
        <Controls />
        {nodeCount <= 500 && (
          <MiniMap
            nodeColor={miniMapNodeColor}
            nodeBorderRadius={8}
            pannable
            zoomable
          />
        )}

        <Panel position="top-right" className="bg-background/95 backdrop-blur-sm rounded-lg border p-3 shadow-lg">
          <div className="text-sm font-semibold mb-2">图例</div>
          <div className="space-y-2 text-xs">
            <div className="flex items-center gap-2">
              <div className="w-4 h-4 rounded bg-gradient-to-br from-blue-500 to-blue-600 border-2 border-blue-700" aria-hidden="true" />
              <span>实体节点</span>
            </div>
            <div className="flex items-center gap-2">
              <div className="w-4 h-4 rounded bg-gradient-to-br from-green-500 to-green-600 border-2 border-green-700" aria-hidden="true" />
              <span>段落节点</span>
            </div>
            {nodeCount > 200 && (
              <div className="mt-2 pt-2 border-t text-yellow-600 dark:text-yellow-500">
                <div className="font-semibold">性能模式</div>
                <div>已禁用动画</div>
                {nodeCount > 500 && <div>已禁用缩略图</div>}
              </div>
            )}
          </div>
        </Panel>
      </ReactFlow>
    </div>
  )
}
