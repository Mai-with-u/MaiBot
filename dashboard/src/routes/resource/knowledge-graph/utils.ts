import type {
  MemoryEvidenceGraphPayload,
  MemoryEvidenceParagraphNodeMetadata,
  MemoryEvidenceRelationNodeMetadata,
  MemoryGraphEdgeDetailPayload,
  MemoryGraphNodeDetailPayload,
  MemoryGraphParagraphDetailPayload,
  MemoryGraphPayload,
  MemoryGraphRelationDetailPayload,
} from '@/lib/memory-api'

import type { GraphData, GraphNode } from './types'

export function toEntityGraphData(payload: MemoryGraphPayload): GraphData {
  const nodes: GraphNode[] = (payload.nodes ?? []).map((node) => ({
    id: node.id,
    type: 'entity',
    content: String(node.name ?? node.id),
    metadata: node.attributes ?? {},
  }))
  const edges = (payload.edges ?? []).map((edge) => ({
    source: edge.source,
    target: edge.target,
    weight: Number(edge.weight ?? 1),
    kind: 'relation' as const,
    label: String(edge.label ?? ''),
    relationHashes: edge.relation_hashes ?? [],
    predicates: edge.predicates ?? [],
    relationCount: Number(edge.relation_count ?? edge.relation_hashes?.length ?? 0),
    evidenceCount: Number(edge.evidence_count ?? 0),
  }))
  return { nodes, edges }
}

export function toEvidenceGraphData(payload: MemoryEvidenceGraphPayload | null | undefined): GraphData {
  return {
    nodes: (payload?.nodes ?? []).map((node) => ({
      id: node.id,
      type: node.type,
      content: node.content,
      metadata: node.metadata ?? {},
    })),
    edges: (payload?.edges ?? []).map((edge) => ({
      source: edge.source,
      target: edge.target,
      weight: Number(edge.weight ?? 1),
      kind: edge.kind,
      label: edge.label,
    })),
    focusEntities: payload?.focus_entities ?? [],
  }
}

export function filterGraphData(graph: GraphData, query: string): GraphData {
  const keyword = query.trim().toLowerCase()
  if (!keyword) {
    return graph
  }

  const matchedNodeIds = new Set(
    graph.nodes
      .filter((node) => node.content.toLowerCase().includes(keyword) || node.id.toLowerCase().includes(keyword))
      .map((node) => node.id),
  )

  const edges = graph.edges.filter((edge) => {
    const label = String(edge.label ?? '').toLowerCase()
    const predicateMatched = (edge.predicates ?? []).some((predicate) => predicate.toLowerCase().includes(keyword))
    const matched =
      matchedNodeIds.has(edge.source) ||
      matchedNodeIds.has(edge.target) ||
      label.includes(keyword) ||
      predicateMatched
    if (matched) {
      matchedNodeIds.add(edge.source)
      matchedNodeIds.add(edge.target)
    }
    return matched
  })

  return {
    nodes: graph.nodes.filter((node) => matchedNodeIds.has(node.id)),
    edges,
    focusEntities: graph.focusEntities,
  }
}

export function mergeUniqueRelations(
  nodeDetail: MemoryGraphNodeDetailPayload | null,
  edgeDetail: MemoryGraphEdgeDetailPayload | null,
): MemoryGraphRelationDetailPayload[] {
  const seen = new Set<string>()
  const items: MemoryGraphRelationDetailPayload[] = []
  for (const relation of [...(nodeDetail?.relations ?? []), ...(edgeDetail?.relations ?? [])]) {
    if (seen.has(relation.hash)) {
      continue
    }
    seen.add(relation.hash)
    items.push(relation)
  }
  return items
}

export function mergeUniqueParagraphs(
  nodeDetail: MemoryGraphNodeDetailPayload | null,
  edgeDetail: MemoryGraphEdgeDetailPayload | null,
): MemoryGraphParagraphDetailPayload[] {
  const seen = new Set<string>()
  const items: MemoryGraphParagraphDetailPayload[] = []
  for (const paragraph of [...(nodeDetail?.paragraphs ?? []), ...(edgeDetail?.paragraphs ?? [])]) {
    if (seen.has(paragraph.hash)) {
      continue
    }
    seen.add(paragraph.hash)
    items.push(paragraph)
  }
  return items
}

export function buildRelationFromMetadata(
  metadata: MemoryEvidenceRelationNodeMetadata | null | undefined,
): MemoryGraphRelationDetailPayload | null {
  const hash = String(metadata?.hash ?? '').trim()
  if (!hash) {
    return null
  }
  const subject = String(metadata?.subject ?? '').trim()
  const predicate = String(metadata?.predicate ?? '').trim()
  const object = String(metadata?.object ?? '').trim()
  const text = String(metadata?.text ?? `${subject} ${predicate} ${object}`).trim()
  return {
    hash,
    subject,
    predicate,
    object,
    text,
    confidence: Number(metadata?.confidence ?? 0),
    paragraph_count: Number(metadata?.paragraph_count ?? 0),
    paragraph_hashes: Array.isArray(metadata?.paragraph_hashes) ? metadata.paragraph_hashes.map(String) : [],
    source_paragraph: '',
  }
}

export function buildParagraphFromMetadata(
  metadata: MemoryEvidenceParagraphNodeMetadata | null | undefined,
): MemoryGraphParagraphDetailPayload | null {
  const hash = String(metadata?.hash ?? '').trim()
  if (!hash) {
    return null
  }
  const preview = String(metadata?.preview ?? '').trim()
  return {
    hash,
    content: preview,
    preview,
    source: String(metadata?.source ?? '').trim(),
    updated_at: typeof metadata?.updated_at === 'number' ? metadata.updated_at : null,
    entity_count: Number(metadata?.entity_count ?? 0),
    relation_count: Number(metadata?.relation_count ?? 0),
    entities: [],
    relations: [],
  }
}
