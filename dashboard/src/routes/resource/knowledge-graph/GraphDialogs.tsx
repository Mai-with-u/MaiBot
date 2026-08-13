import { useState } from 'react'

import { Trash2 } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { ThinkingIllustration } from '@/components/ui/thinking-illustration'
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import type {
  MemoryEvidenceParagraphNodeMetadata,
  MemoryEvidenceRelationNodeMetadata,
  MemoryGraphEdgeDetailPayload,
  MemoryGraphNodeDetailPayload,
  MemoryGraphParagraphDetailPayload,
  MemoryGraphRelationDetailPayload,
} from '@/lib/memory-api'

import type { GraphNode, SelectedEdgeData } from './types'

function formatTimestamp(value?: number | null): string {
  if (!value) {
    return '未知'
  }
  const date = new Date(Number(value) * 1000)
  if (Number.isNaN(date.getTime())) {
    return '未知'
  }
  return date.toLocaleString()
}

function RelationList({
  items,
  onDeleteRelation,
}: {
  items: MemoryGraphRelationDetailPayload[]
  onDeleteRelation?: (relation: MemoryGraphRelationDetailPayload) => void
}) {
  if (items.length <= 0) {
    return <p className="text-sm text-muted-foreground">暂无可展示的关系语义。</p>
  }
  return (
    <div className="space-y-2">
      {items.map((relation) => (
        <div key={relation.hash} className="rounded-lg border bg-muted/40 p-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex flex-wrap items-center gap-2">
              <Badge variant="outline">{relation.predicate || '未命名谓词'}</Badge>
              <span className="text-xs text-muted-foreground">证据段落 {relation.paragraph_count}</span>
              <span className="text-xs text-muted-foreground">置信度 {relation.confidence.toFixed(3)}</span>
            </div>
            {onDeleteRelation ? (
              <Button size="sm" variant="outline" onClick={() => onDeleteRelation(relation)}>
                <Trash2 className="mr-2 h-4 w-4" />
                删除关系
              </Button>
            ) : null}
          </div>
          <p className="mt-2 text-sm font-medium">{relation.text}</p>
          <code className="mt-2 block break-all text-xs text-muted-foreground">{relation.hash}</code>
        </div>
      ))}
    </div>
  )
}

function ParagraphList({
  items,
  onDeleteParagraph,
}: {
  items: MemoryGraphParagraphDetailPayload[]
  onDeleteParagraph?: (paragraph: MemoryGraphParagraphDetailPayload) => void
}) {
  if (items.length <= 0) {
    return <p className="text-sm text-muted-foreground">暂无可展示的来源段落。</p>
  }
  return (
    <div className="space-y-3">
      {items.map((paragraph) => (
        <div key={paragraph.hash} className="rounded-lg border bg-muted/40 p-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex flex-wrap items-center gap-2">
              <Badge variant="secondary">{paragraph.source || '未命名来源'}</Badge>
              <span className="text-xs text-muted-foreground">实体 {paragraph.entity_count}</span>
              <span className="text-xs text-muted-foreground">关系 {paragraph.relation_count}</span>
              <span className="text-xs text-muted-foreground">更新时间 {formatTimestamp(paragraph.updated_at)}</span>
            </div>
            {onDeleteParagraph ? (
              <Button size="sm" variant="outline" onClick={() => onDeleteParagraph(paragraph)}>
                <Trash2 className="mr-2 h-4 w-4" />
                删除段落
              </Button>
            ) : null}
          </div>
          <p className="mt-2 whitespace-pre-wrap text-sm break-words">{paragraph.preview || paragraph.content}</p>
          {paragraph.entities.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1">
              {paragraph.entities.slice(0, 8).map((entity) => (
                <Badge key={`${paragraph.hash}-${entity}`} variant="outline" className="text-xs">
                  {entity}
                </Badge>
              ))}
            </div>
          )}
        </div>
      ))}
    </div>
  )
}

interface NodeDetailDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  selectedNodeData: GraphNode | null
  nodeDetail: MemoryGraphNodeDetailPayload | null
  loading?: boolean
  onOpenEvidence?: () => void
  onDeleteEntity?: (options: { includeParagraphs: boolean }) => void
  onDeleteRelation?: (relation: MemoryGraphRelationDetailPayload) => void
  onDeleteParagraph?: (paragraph: MemoryGraphParagraphDetailPayload) => void
}

export function NodeDetailDialog({
  open,
  onOpenChange,
  selectedNodeData,
  nodeDetail,
  loading = false,
  onOpenEvidence,
  onDeleteEntity,
  onDeleteRelation,
  onDeleteParagraph,
}: NodeDetailDialogProps) {
  const node = nodeDetail?.node ?? selectedNodeData
  const [includeParagraphs, setIncludeParagraphs] = useState(false)
  if (!open && includeParagraphs) {
    setIncludeParagraphs(false)
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-4xl max-h-[85vh] grid grid-rows-[auto_1fr_auto] overflow-hidden">
        <DialogHeader>
          <DialogTitle>实体详情</DialogTitle>
        </DialogHeader>
        <DialogBody className="h-full overflow-y-auto">
          {node ? (
            <div className="space-y-6 pb-2">
              <div className="flex flex-wrap items-start justify-between gap-3 rounded-xl border bg-muted/30 p-4">
                <div>
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge>{node.type === 'entity' ? '实体' : node.type}</Badge>
                    {'appearance_count' in (nodeDetail?.node ?? {}) && (
                      <Badge variant="outline">出现次数 {nodeDetail?.node.appearance_count ?? 0}</Badge>
                    )}
                  </div>
                  <h3 className="mt-2 text-lg font-semibold">{node.content}</h3>
                  <code className="mt-2 block break-all text-xs text-muted-foreground">{node.id}</code>
                </div>
                <div className="flex flex-col items-end gap-3">
                  <Button variant="outline" onClick={onOpenEvidence} disabled={!onOpenEvidence}>
                    切到证据视图
                  </Button>
                  {onDeleteEntity ? (
                    <div className="flex flex-col items-end gap-2 rounded-lg border bg-background p-3">
                      <div className="flex items-center gap-2 text-xs text-muted-foreground">
                        <Checkbox
                          checked={includeParagraphs}
                          onCheckedChange={(checked) => setIncludeParagraphs(Boolean(checked))}
                          aria-label="删除该实体相关证据段落"
                        />
                        <span>删除该实体相关证据段落</span>
                      </div>
                      <Button variant="outline" onClick={() => onDeleteEntity({ includeParagraphs })}>
                        <Trash2 className="mr-2 h-4 w-4" />
                        删除实体
                      </Button>
                    </div>
                  ) : null}
                </div>
              </div>

              {loading ? (
                <ThinkingIllustration size="sm" />
              ) : (
                <>
                  <section className="space-y-3">
                    <div className="flex items-center justify-between">
                      <h4 className="text-sm font-semibold">相关关系</h4>
                      <span className="text-xs text-muted-foreground">{nodeDetail?.relations.length ?? 0} 条</span>
                    </div>
                    <RelationList items={nodeDetail?.relations ?? []} onDeleteRelation={onDeleteRelation} />
                  </section>

                  <section className="space-y-3">
                    <div className="flex items-center justify-between">
                      <h4 className="text-sm font-semibold">支持段落</h4>
                      <span className="text-xs text-muted-foreground">{nodeDetail?.paragraphs.length ?? 0} 个</span>
                    </div>
                    <ParagraphList items={nodeDetail?.paragraphs ?? []} onDeleteParagraph={onDeleteParagraph} />
                  </section>
                </>
              )}
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">尚未选中实体。</p>
          )}
        </DialogBody>
      </DialogContent>
    </Dialog>
  )
}

interface EdgeDetailDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  selectedEdgeData: SelectedEdgeData | null
  edgeDetail: MemoryGraphEdgeDetailPayload | null
  loading?: boolean
  onOpenEvidence?: () => void
  onDeleteEdgeGroup?: (options: { includeParagraphs: boolean }) => void
  onDeleteRelation?: (relation: MemoryGraphRelationDetailPayload) => void
  onDeleteParagraph?: (paragraph: MemoryGraphParagraphDetailPayload) => void
}

export function EdgeDetailDialog({
  open,
  onOpenChange,
  selectedEdgeData,
  edgeDetail,
  loading = false,
  onOpenEvidence,
  onDeleteEdgeGroup,
  onDeleteRelation,
  onDeleteParagraph,
}: EdgeDetailDialogProps) {
  const sourceLabel = selectedEdgeData?.source.content ?? edgeDetail?.edge.source ?? ''
  const targetLabel = selectedEdgeData?.target.content ?? edgeDetail?.edge.target ?? ''
  const [includeParagraphs, setIncludeParagraphs] = useState(false)
  if (!open && includeParagraphs) {
    setIncludeParagraphs(false)
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-4xl max-h-[85vh] overflow-hidden grid grid-rows-[auto_1fr_auto]">
        <DialogHeader>
          <DialogTitle>关系详情</DialogTitle>
        </DialogHeader>
        <DialogBody className="overflow-y-auto">
          {selectedEdgeData || edgeDetail ? (
            <div className="space-y-6 pb-2">
              <div className="rounded-xl border bg-muted/30 p-4">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      {(edgeDetail?.edge.predicates ?? []).map((predicate) => (
                        <Badge key={predicate} variant="outline">{predicate}</Badge>
                      ))}
                      <Badge variant="secondary">关系 {edgeDetail?.edge.relation_count ?? selectedEdgeData?.edge.relationCount ?? 0}</Badge>
                      <Badge variant="secondary">证据 {edgeDetail?.edge.evidence_count ?? selectedEdgeData?.edge.evidenceCount ?? 0}</Badge>
                    </div>
                    <p className="mt-3 text-base font-semibold break-words">
                      {sourceLabel} → {targetLabel}
                    </p>
                    <p className="mt-2 text-sm text-muted-foreground">
                      聚合权重 {(edgeDetail?.edge.weight ?? selectedEdgeData?.edge.weight ?? 0).toFixed(4)}
                    </p>
                  </div>
                  <div className="flex flex-col items-end gap-3">
                    <Button variant="outline" onClick={onOpenEvidence} disabled={!onOpenEvidence}>
                      切到证据视图
                    </Button>
                    {onDeleteEdgeGroup ? (
                      <div className="flex flex-col items-end gap-2 rounded-lg border bg-background p-3">
                        <div className="flex items-center gap-2 text-xs text-muted-foreground">
                          <Checkbox
                            checked={includeParagraphs}
                            onCheckedChange={(checked) => setIncludeParagraphs(Boolean(checked))}
                            aria-label="同时删除支撑段落"
                          />
                          <span>同时删除支撑段落</span>
                        </div>
                        <Button variant="outline" onClick={() => onDeleteEdgeGroup({ includeParagraphs })}>
                          <Trash2 className="mr-2 h-4 w-4" />
                          删除此关系组
                        </Button>
                      </div>
                    ) : null}
                  </div>
                </div>
              </div>

              {loading ? (
                <ThinkingIllustration size="sm" />
              ) : (
                <>
                  <section className="space-y-3">
                    <div className="flex items-center justify-between">
                      <h4 className="text-sm font-semibold">关系语义</h4>
                      <span className="text-xs text-muted-foreground">{edgeDetail?.relations.length ?? 0} 条</span>
                    </div>
                    <RelationList items={edgeDetail?.relations ?? []} onDeleteRelation={onDeleteRelation} />
                  </section>

                  <section className="space-y-3">
                    <div className="flex items-center justify-between">
                      <h4 className="text-sm font-semibold">支持段落</h4>
                      <span className="text-xs text-muted-foreground">{edgeDetail?.paragraphs.length ?? 0} 个</span>
                    </div>
                    <ParagraphList items={edgeDetail?.paragraphs ?? []} onDeleteParagraph={onDeleteParagraph} />
                  </section>
                </>
              )}
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">尚未选中关系。</p>
          )}
        </DialogBody>
      </DialogContent>
    </Dialog>
  )
}

interface RelationDetailDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  relation: MemoryGraphRelationDetailPayload | null
  metadata?: MemoryEvidenceRelationNodeMetadata | null
  onDeleteRelation?: (relation: MemoryGraphRelationDetailPayload, includeParagraphs: boolean) => void
}

export function RelationDetailDialog({
  open,
  onOpenChange,
  relation,
  metadata,
  onDeleteRelation,
}: RelationDetailDialogProps) {
  const [includeParagraphs, setIncludeParagraphs] = useState(false)
  if (!open && includeParagraphs) {
    setIncludeParagraphs(false)
  }

  if (!relation) {
    return null
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl max-h-[80vh] grid grid-rows-[auto_1fr_auto] overflow-hidden">
        <DialogHeader>
          <DialogTitle>关系明细</DialogTitle>
        </DialogHeader>
        <DialogBody className="space-y-4 overflow-y-auto">
          <div className="rounded-xl border bg-muted/30 p-4">
            <div className="flex flex-wrap items-center gap-2">
              <Badge variant="outline">{relation.predicate || metadata?.predicate || '未命名谓词'}</Badge>
              <Badge variant="secondary">证据段落 {relation.paragraph_count}</Badge>
              <Badge variant="secondary">置信度 {relation.confidence.toFixed(3)}</Badge>
            </div>
            <p className="mt-3 text-base font-semibold break-words">{relation.text}</p>
            <code className="mt-3 block break-all text-xs text-muted-foreground">{relation.hash}</code>
          </div>

          {onDeleteRelation ? (
            <div className="rounded-lg border bg-background p-3">
              <div className="flex items-center gap-2 text-xs text-muted-foreground">
                <Checkbox
                  checked={includeParagraphs}
                  onCheckedChange={(checked) => setIncludeParagraphs(Boolean(checked))}
                  aria-label="同时删除支撑该关系的段落"
                />
                <span>同时删除支撑该关系的段落</span>
              </div>
              <Button className="mt-3" variant="outline" onClick={() => onDeleteRelation(relation, includeParagraphs)}>
                <Trash2 className="mr-2 h-4 w-4" />
                删除这条关系
              </Button>
            </div>
          ) : null}
        </DialogBody>
      </DialogContent>
    </Dialog>
  )
}

interface ParagraphDetailDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  paragraph: MemoryGraphParagraphDetailPayload | null
  metadata?: MemoryEvidenceParagraphNodeMetadata | null
  onDeleteParagraph?: (paragraph: MemoryGraphParagraphDetailPayload) => void
}

export function ParagraphDetailDialog({
  open,
  onOpenChange,
  paragraph,
  metadata,
  onDeleteParagraph,
}: ParagraphDetailDialogProps) {
  if (!paragraph) {
    return null
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl max-h-[80vh] grid grid-rows-[auto_1fr_auto] overflow-hidden">
        <DialogHeader>
          <DialogTitle>段落明细</DialogTitle>
        </DialogHeader>
        <DialogBody className="space-y-4 overflow-y-auto">
          <div className="rounded-xl border bg-muted/30 p-4">
            <div className="flex flex-wrap items-center gap-2">
              <Badge variant="secondary">{paragraph.source || metadata?.source || '未命名来源'}</Badge>
              <Badge variant="outline">实体 {paragraph.entity_count}</Badge>
              <Badge variant="outline">关系 {paragraph.relation_count}</Badge>
              <Badge variant="outline">更新时间 {formatTimestamp(paragraph.updated_at ?? metadata?.updated_at)}</Badge>
            </div>
            <p className="mt-3 whitespace-pre-wrap text-sm break-words">{paragraph.content}</p>
            <code className="mt-3 block break-all text-xs text-muted-foreground">{paragraph.hash}</code>
          </div>

          {paragraph.entities.length > 0 ? (
            <div className="flex flex-wrap gap-2">
              {paragraph.entities.map((entity) => (
                <Badge key={`${paragraph.hash}-${entity}`} variant="outline">{entity}</Badge>
              ))}
            </div>
          ) : null}

          {onDeleteParagraph ? (
            <Button variant="outline" onClick={() => onDeleteParagraph(paragraph)}>
              <Trash2 className="mr-2 h-4 w-4" />
              删除这段证据
            </Button>
          ) : null}
        </DialogBody>
      </DialogContent>
    </Dialog>
  )
}
