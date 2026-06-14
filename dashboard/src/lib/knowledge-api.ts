/**
 * 知识库 API
 *
 * 请求经由主后端实例 backendApi：自动携带 Cookie 认证、处理 Electron 后端地址，
 * 失败统一抛出 ApiError。
 */

import { backendApi } from '@/lib/http'

const API_BASE = '/api/webui/knowledge'

export interface KnowledgeNode {
  id: string
  type: 'entity' | 'paragraph'
  content: string
  create_time?: number
}

export interface KnowledgeEdge {
  source: string
  target: string
  weight: number
  create_time?: number
  update_time?: number
}

export interface KnowledgeGraph {
  nodes: KnowledgeNode[]
  edges: KnowledgeEdge[]
}

export interface KnowledgeStats {
  total_nodes: number
  total_edges: number
  entity_nodes: number
  paragraph_nodes: number
}

/**
 * 获取知识图谱数据
 */
export async function getKnowledgeGraph(limit: number = 100, nodeType: 'all' | 'entity' | 'paragraph' = 'all'): Promise<KnowledgeGraph> {
  return backendApi.get<KnowledgeGraph>(`${API_BASE}/graph`, {
    query: { limit, node_type: nodeType },
    errorMessage: '获取知识图谱失败',
  })
}

/**
 * 获取知识图谱统计信息
 */
export async function getKnowledgeStats(): Promise<KnowledgeStats> {
  return backendApi.get<KnowledgeStats>(`${API_BASE}/stats`, {
    errorMessage: '获取知识图谱统计信息失败',
  })
}

/**
 * 搜索知识节点
 */
export async function searchKnowledgeNode(query: string): Promise<KnowledgeNode[]> {
  return backendApi.get<KnowledgeNode[]>(`${API_BASE}/search`, {
    query: { query },
    errorMessage: '搜索知识节点失败',
  })
}
