import type { Node, Edge } from 'reactflow'

export interface GraphNode {
  id: string
  type: 'entity' | 'paragraph'
  content: string
}

export interface GraphEdge {
  source: string
  target: string
  weight: number
}

export interface GraphData {
  nodes: GraphNode[]
  edges: GraphEdge[]
}

export interface GraphStats {
  total_nodes: number
  total_edges: number
  entity_nodes: number
  paragraph_nodes: number
}

export interface FlowNodeData {
  label: string
  content: string
}

export type FlowNode = Node<FlowNodeData>
export type FlowEdge = Edge

export interface SelectedEdgeData {
  source: GraphNode
  target: GraphNode
  edge: GraphEdge
}
