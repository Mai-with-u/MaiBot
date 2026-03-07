/**
 * 知识库 API
 */

import { getApiBaseUrl } from './api-base'
import { isElectron } from './runtime'

async function getKnowledgeApiBase(): Promise<string> {
  if (isElectron()) {
    const base = await getApiBaseUrl()
    return base ? `${base}/api/webui` : '/api/webui'
  }
  return import.meta.env.VITE_API_BASE_URL || '/api/webui'
}

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
  const url = `${await getKnowledgeApiBase()}/knowledge/graph?limit=${limit}&node_type=${nodeType}`
  
  const response = await fetch(url)
  
  if (!response.ok) {
    throw new Error(`获取知识图谱失败: ${response.status}`)
  }
  
  return response.json()
}

/**
 * 获取知识图谱统计信息
 */
export async function getKnowledgeStats(): Promise<KnowledgeStats> {
  const response = await fetch(`${await getKnowledgeApiBase()}/knowledge/stats`)
  if (!response.ok) {
    throw new Error('获取知识图谱统计信息失败')
  }
  return response.json()
}

/**
 * 搜索知识节点
 */
export async function searchKnowledgeNode(query: string): Promise<KnowledgeNode[]> {
  const response = await fetch(`${await getKnowledgeApiBase()}/knowledge/search?query=${encodeURIComponent(query)}`)
  if (!response.ok) {
    throw new Error('搜索知识节点失败')
  }
  return response.json()
}
