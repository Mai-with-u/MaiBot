import { Database, Network, RefreshCw, Search, SlidersHorizontal } from 'lucide-react'

import { MemoryDeleteDialog } from '@/components/memory/MemoryDeleteDialog'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'

import {
  EdgeDetailDialog,
  NodeDetailDialog,
  ParagraphDetailDialog,
  RelationDetailDialog,
} from './GraphDialogs'
import { GraphVisualization } from './GraphVisualization'
import type { GraphViewMode } from './types'
import { useKnowledgeGraphPage } from './useKnowledgeGraphPage'

interface KnowledgeGraphPageProps {
  embedded?: boolean
  initialParagraphHash?: string
  onOpenConsole?: () => void
}

export function KnowledgeGraphPage({ embedded = false, initialParagraphHash = '', onOpenConsole }: KnowledgeGraphPageProps = {}) {
  const {
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
  } = useKnowledgeGraphPage({ initialParagraphHash, onOpenConsole })

  return (
    <div className="flex h-full flex-col">
      <div className={embedded ? 'flex-none border-b bg-card/60 px-4 py-3 backdrop-blur' : 'flex-none border-b bg-card/60 px-6 py-4 backdrop-blur'}>
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
          {!embedded && (
            <div>
              <h1 className="text-2xl font-bold">长期记忆图谱</h1>
              <p className="mt-1 text-sm text-muted-foreground">
                基于 A_Memorix 的实体关系图与证据视图
              </p>
            </div>
          )}

          <div className="flex flex-wrap gap-2">
            <Badge variant="outline" className="gap-1">
              <Database className="h-3.5 w-3.5" />
              总节点 {stats.totalNodes}
            </Badge>
            <Badge variant="outline" className="gap-1">
              <Network className="h-3.5 w-3.5" />
              总关系 {stats.totalEdges}
            </Badge>
            <Badge variant="secondary">
              {viewMode === 'entity'
                ? `当前显示 ${stats.visibleNodes} / ${stats.visibleEdges}`
                : `证据视图 ${stats.evidenceNodes} / ${stats.evidenceEdges}`}
            </Badge>
          </div>
        </div>

        <div className="mt-3 flex flex-col gap-2">
          <div className="flex flex-col gap-2 xl:flex-row xl:items-center">
            <Tabs value={viewMode} onValueChange={(value) => setViewMode(value as GraphViewMode)}>
              <TabsList className="h-10 flex-nowrap justify-start">
                <TabsTrigger value="entity" className="whitespace-nowrap">实体关系图</TabsTrigger>
                <TabsTrigger value="evidence" className="whitespace-nowrap">证据视图</TabsTrigger>
              </TabsList>
            </Tabs>

            <div className="flex flex-1 gap-2">
              <Input
                value={searchInput}
                onChange={(event) => setSearchInput(event.target.value)}
                onKeyDown={(event) => event.key === 'Enter' && void handleSearch()}
                placeholder="搜索实体、关系、hash（后端全库）"
              />
              <Button onClick={() => void handleSearch()} variant="secondary" disabled={searchLoading}>
                <Search className="mr-2 h-4 w-4" />
                {searchLoading ? '检索中' : '搜索'}
              </Button>
            </div>

            <div className="flex flex-wrap gap-2">
              <Select value={nodeLimit} onValueChange={setNodeLimit}>
                <SelectTrigger className="w-[140px]">
                  <SelectValue placeholder="节点上限" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="80">80 节点</SelectItem>
                  <SelectItem value="120">120 节点</SelectItem>
                  <SelectItem value="240">240 节点</SelectItem>
                  <SelectItem value="480">480 节点</SelectItem>
                </SelectContent>
              </Select>
              <Button onClick={() => void loadGraph()} disabled={loading}>
                <RefreshCw className={`mr-2 h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
                刷新图谱
              </Button>
              <Button variant="outline" onClick={openConsole} className={embedded ? 'hidden' : undefined}>
                <SlidersHorizontal className="mr-2 h-4 w-4" />
                打开控制台
              </Button>
            </div>
          </div>

          {appliedSearchQuery ? (
            <div className="rounded-lg border bg-background/80 p-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="text-sm font-medium">
                  搜索词：{appliedSearchQuery}
                </div>
                <Badge variant={searchFallbackMode ? 'destructive' : 'secondary'}>
                  {searchFallbackMode ? '仅当前已加载范围' : `全库命中 ${searchResults.length} 条`}
                </Badge>
              </div>
              {searchFallbackMode ? (
                <p className="mt-2 text-sm text-muted-foreground">
                  后端检索不可用，当前结果来自已加载图谱范围。请先刷新图谱或稍后重试。
                </p>
              ) : searchResults.length <= 0 ? (
                <p className="mt-2 text-sm text-muted-foreground">未命中实体或关系。</p>
              ) : (
                <div className="mt-3 max-h-56 space-y-2 overflow-auto pr-1">
                  {searchResults.map((item, index) => (
                    <button
                      key={`${item.type}-${item.entity_hash ?? item.relation_hash ?? `${item.title}-${index}`}`}
                      type="button"
                      className="w-full rounded-md border bg-card px-3 py-2 text-left transition hover:bg-accent/40"
                      onClick={() => handleSearchResultClick(item)}
                    >
                      <div className="flex items-center gap-2">
                        <Badge variant="outline">{item.type === 'entity' ? '实体' : '关系'}</Badge>
                        <span className="truncate text-sm font-medium">{item.title || '(无标题结果)'}</span>
                      </div>
                      <p className="mt-1 text-xs text-muted-foreground">
                        命中字段：{item.matched_field} = {item.matched_value}
                        {item.type === 'entity'
                          ? ` · 有效证据=${item.active_evidence_count ?? 0} · 累计出现=${item.appearance_count ?? 0}`
                          : ` · confidence=${Number(item.confidence ?? 0).toFixed(2)}`}
                      </p>
                    </button>
                  ))}
                </div>
              )}
            </div>
          ) : null}
        </div>
      </div>

      <div className="min-h-0 flex-1 bg-muted/20">
        {viewMode === 'entity' && graphData.nodes.length > 0 ? (
          <GraphVisualization
            graphData={graphData}
            loading={loading}
            onNodeClick={handleNodeClick}
            onEdgeClick={handleEdgeClick}
          />
        ) : viewMode === 'evidence' && activeGraph.nodes.length > 0 ? (
          <GraphVisualization
            graphData={activeGraph}
            loading={detailLoading}
            onNodeClick={handleEvidenceNodeClick}
            onEdgeClick={() => undefined}
          />
        ) : (
          <div className="flex h-full items-center justify-center">
            <div className="max-w-xl rounded-xl border bg-background p-8 text-center shadow-sm">
              {viewMode === 'entity' ? (
                <>
                  <h2 className="text-lg font-semibold">还没有可展示的长期记忆图谱</h2>
                  <p className="mt-2 text-sm text-muted-foreground">
                    先在长期记忆控制台里完成导入或记忆生成，再回来查看关系网络。
                  </p>
                  <Button className="mt-4" onClick={openConsole}>
                    前往长期记忆控制台
                  </Button>
                </>
              ) : (
                <>
                  <h2 className="text-lg font-semibold">证据视图还没有可展示的选择</h2>
                  <p className="mt-2 text-sm text-muted-foreground">
                    先在实体关系图里点击某个实体或边，再切换到证据视图查看 paragraph → relation/entity 的牵引。
                  </p>
                  <div className="mt-4 flex justify-center gap-2">
                    <Button variant="outline" onClick={() => setViewMode('entity')}>
                      返回实体关系图
                    </Button>
                    {canShowEvidence && (
                      <Button onClick={() => setViewMode('evidence')}>刷新证据视图</Button>
                    )}
                  </div>
                </>
              )}
            </div>
          </div>
        )}
      </div>

      <NodeDetailDialog
        open={Boolean(selectedNodeData)}
        onOpenChange={(open) => !open && setSelectedNodeData(null)}
        selectedNodeData={selectedNodeData}
        nodeDetail={nodeDetail}
        loading={detailLoading}
        onOpenEvidence={handleOpenNodeEvidence}
        onDeleteEntity={requestDeleteEntity}
        onDeleteRelation={(relation) => requestDeleteRelation(relation)}
        onDeleteParagraph={requestDeleteParagraph}
      />
      <EdgeDetailDialog
        open={Boolean(selectedEdgeData)}
        onOpenChange={(open) => !open && setSelectedEdgeData(null)}
        selectedEdgeData={selectedEdgeData}
        edgeDetail={edgeDetail}
        loading={detailLoading}
        onOpenEvidence={handleOpenEdgeEvidence}
        onDeleteEdgeGroup={requestDeleteEdgeGroup}
        onDeleteRelation={(relation) => requestDeleteRelation(relation)}
        onDeleteParagraph={requestDeleteParagraph}
      />
      <RelationDetailDialog
        open={Boolean(selectedRelationDetail)}
        onOpenChange={(open) => !open && setSelectedRelationDetail(null)}
        relation={selectedRelationDetail}
        metadata={selectedRelationMetadata}
        onDeleteRelation={(relation, includeParagraphs) => requestDeleteRelation(relation, includeParagraphs)}
      />
      <ParagraphDetailDialog
        open={Boolean(selectedParagraphDetail)}
        onOpenChange={(open) => !open && setSelectedParagraphDetail(null)}
        paragraph={selectedParagraphDetail}
        metadata={selectedParagraphMetadata}
        onDeleteParagraph={requestDeleteParagraph}
      />
      <MemoryDeleteDialog
        open={Boolean(deleteDraft)}
        onOpenChange={closeDeleteDialog}
        title={deleteDraft?.title ?? '删除预览'}
        description={deleteDraft?.description}
        preview={deletePreview}
        result={deleteResult}
        loadingPreview={deletePreviewLoading}
        executing={deleteExecuting}
        restoring={deleteRestoring}
        error={deletePreviewError}
        onExecute={executeCurrentDelete}
        onRestore={restoreCurrentDelete}
      />
    </div>
  )
}
