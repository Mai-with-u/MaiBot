/**
 * 知识图谱 API
 */

import { fetchWithAuth } from './fetch-with-auth'

export interface KnowledgeNode {
  id: string
  label: string
  type: string
  data?: Record<string, unknown>
}

export interface KnowledgeEdge {
  id: string
  source: string
  target: string
  label?: string
}

export interface KnowledgeStats {
  nodes: number
  edges: number
  lastUpdated: string
}

export async function getKnowledgeGraph(): Promise<{ success: boolean; nodes: KnowledgeNode[]; edges: KnowledgeEdge[] }> {
  const response = await fetchWithAuth('/api/webui/knowledge/graph')
  return response.json()
}

export async function getKnowledgeStats(): Promise<{ success: boolean; data: KnowledgeStats }> {
  const response = await fetchWithAuth('/api/webui/knowledge/stats')
  return response.json()
}

export async function searchKnowledgeNode(query: string): Promise<{ success: boolean; data: KnowledgeNode[] }> {
  const response = await fetchWithAuth(`/api/webui/knowledge/search?q=${encodeURIComponent(query)}`)
  return response.json()
}
