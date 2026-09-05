import type { Node, Edge } from 'reactflow'

export type GraphViewMode = 'entity' | 'evidence'

export interface GraphNode {
  id: string
  type: 'entity' | 'relation' | 'paragraph'
  content: string
  metadata?: Record<string, unknown>
}

export interface GraphEdge {
  source: string
  target: string
  weight: number
  kind?: 'relation' | 'mentions' | 'supports' | 'subject' | 'object'
  label?: string
  relationHashes?: string[]
  predicates?: string[]
  relationCount?: number
  evidenceCount?: number
}

export interface GraphData {
  nodes: GraphNode[]
  edges: GraphEdge[]
  focusEntities?: string[]
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
  type: GraphNode['type']
  layout?: 'radial' | 'evidence'
}

export type FlowNode = Node<FlowNodeData>
export type FlowEdge = Edge

export interface SelectedEdgeData {
  source: GraphNode
  target: GraphNode
  edge: GraphEdge
}
