import { Eye, Hash, RefreshCw, Search } from 'lucide-react'
import { useMemo, useState } from 'react'

import { useQuery } from '@tanstack/react-query'

import { AccentPanel } from '@/components/ui/accent-panel'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Skeleton } from '@/components/ui/skeleton'
import { getExpressionClusterMembers, getExpressionClusters } from '@/lib/expression-api'
import { cn } from '@/lib/utils'

import type { ExpressionClusterMember, ExpressionClusterSummary } from '@/types/expression'
import { ExpressionVectorSpaces } from './ExpressionVectorSpaces'

interface ExpressionClusterBrowserProps {
  onOpenExpression: (expressionId: number) => void
}

function getClusterKey(cluster: ExpressionClusterSummary): string {
  return `${cluster.embedding_profile_marker}:${cluster.cluster_id}`
}

function getProfileLabel(marker: string): string {
  return marker ? marker.slice(0, 12) : 'unknown'
}

function matchesClusterSearch(cluster: ExpressionClusterSummary, search: string): boolean {
  const normalizedSearch = search.trim().toLowerCase()
  if (!normalizedSearch) {
    return true
  }
  const haystack = [
    String(cluster.cluster_id),
    getProfileLabel(cluster.embedding_profile_marker),
    ...cluster.members.flatMap((member) => [member.situation, member.style]),
  ]
    .join('\n')
    .toLowerCase()
  return haystack.includes(normalizedSearch)
}

function ClusterSkeleton() {
  return (
    <div className="space-y-2">
      {Array.from({ length: 8 }).map((_, index) => (
        <Skeleton key={index} className="h-16 w-full" />
      ))}
    </div>
  )
}

function ClusterMemberRow({
  member,
  onOpenExpression,
}: {
  member: ExpressionClusterMember
  onOpenExpression: (expressionId: number) => void
}) {
  return (
    <div className="grid grid-cols-[minmax(0,1fr)_2.75rem] gap-2 border-b px-3 py-2 last:border-b-0 sm:grid-cols-[4.5rem_minmax(0,1.1fr)_minmax(0,1fr)_5rem_2.75rem] sm:items-center">
      <div className="hidden font-mono text-xs text-muted-foreground sm:block">#{member.id}</div>
      <div className="min-w-0">
        <div className="line-clamp-2 text-sm font-medium" title={member.situation}>
          {member.situation}
        </div>
        <div className="mt-1 text-xs text-muted-foreground sm:hidden">
          #{member.id} · {member.count} 次
        </div>
      </div>
      <div className="min-w-0 sm:block">
        <div className="line-clamp-2 text-sm text-muted-foreground" title={member.style}>
          {member.style}
        </div>
        {member.chat_name && (
          <div className="mt-1 truncate text-xs text-muted-foreground" title={member.chat_name}>
            {member.chat_name}
          </div>
        )}
      </div>
      <div className="hidden text-right text-sm tabular-nums text-muted-foreground sm:block">
        {member.count}
      </div>
      <Button
        type="button"
        variant="ghost"
        size="icon"
        className="h-8 w-8 self-start sm:self-center"
        title="查看表达"
        aria-label="查看表达"
        onClick={() => onOpenExpression(member.id)}
      >
        <Eye className="h-4 w-4" />
      </Button>
    </div>
  )
}

export function ExpressionClusterBrowser({ onOpenExpression }: ExpressionClusterBrowserProps) {
  const [search, setSearch] = useState('')
  const [selectedClusterKey, setSelectedClusterKey] = useState('')

  const clusterQuery = useQuery({
    queryKey: ['expression', 'clusters'],
    queryFn: getExpressionClusters,
  })

  const clusters = clusterQuery.data?.clusters
  const filteredClusters = useMemo(
    () => (clusters ?? []).filter((cluster) => matchesClusterSearch(cluster, search)),
    [clusters, search]
  )
  const activeCluster =
    filteredClusters.find((cluster) => getClusterKey(cluster) === selectedClusterKey) ??
    filteredClusters[0] ??
    null

  const memberQuery = useQuery({
    queryKey: [
      'expression',
      'clusters',
      'members',
      activeCluster?.embedding_profile_marker,
      activeCluster?.cluster_id,
    ],
    queryFn: () =>
      getExpressionClusterMembers({
        cluster_id: activeCluster?.cluster_id ?? 0,
        profile_marker: activeCluster?.embedding_profile_marker,
      }),
    enabled: !!activeCluster,
  })

  const members = memberQuery.data?.data ?? []
  const indexExists = clusterQuery.data?.index_exists ?? false

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4 pr-3 pb-2 sm:pr-4">
      <AccentPanel showRetroStripeDivider={false}>
        <div className="grid gap-3 px-4 py-3 sm:grid-cols-2 lg:grid-cols-4">
          <div>
            <div className="text-xs text-muted-foreground">表达数量</div>
            <div className="mt-1 text-lg font-semibold tabular-nums">
              {clusterQuery.data?.sample_count ?? 0}
            </div>
          </div>
          <div>
            <div className="text-xs text-muted-foreground">聚类数量</div>
            <div className="mt-1 text-lg font-semibold tabular-nums">{clusters?.length ?? 0}</div>
          </div>
          <div className="min-w-0">
            <div className="text-xs text-muted-foreground">Embedding</div>
            <div className="mt-1 truncate text-sm font-medium" title={clusterQuery.data?.embedding_model ?? ''}>
              {clusterQuery.data?.embedding_model ?? '-'}
            </div>
          </div>
          <div className="flex items-end justify-start gap-2 lg:justify-end">
            <ExpressionVectorSpaces />
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="h-8 gap-2"
              onClick={() => {
                clusterQuery.refetch()
                if (activeCluster) {
                  memberQuery.refetch()
                }
              }}
            >
              <RefreshCw className="h-4 w-4" />
              刷新
            </Button>
          </div>
        </div>
      </AccentPanel>

      <div className="grid min-h-0 flex-1 grid-cols-1 gap-4 lg:grid-cols-[18rem_minmax(0,1fr)]">
        <div className="flex min-h-[18rem] flex-col border bg-background lg:min-h-0">
          <div className="border-b p-3">
            <div className="relative">
              <Search className="absolute top-2 left-2.5 h-4 w-4 text-muted-foreground" />
              <Input
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                placeholder="搜索簇"
                className="h-8 pl-9"
              />
            </div>
          </div>
          <ScrollArea className="min-h-0 flex-1">
            <div className="space-y-1 p-2">
              {clusterQuery.isPending ? (
                <ClusterSkeleton />
              ) : !indexExists ? (
                <div className="px-3 py-8 text-center text-sm text-muted-foreground">暂无向量索引</div>
              ) : filteredClusters.length === 0 ? (
                <div className="px-3 py-8 text-center text-sm text-muted-foreground">没有匹配的聚类</div>
              ) : (
                filteredClusters.map((cluster) => {
                  const selected = activeCluster && getClusterKey(activeCluster) === getClusterKey(cluster)
                  const preview = cluster.members
                    .slice(0, 2)
                    .map((member) => member.situation)
                    .join(' / ')
                  return (
                    <button
                      key={getClusterKey(cluster)}
                      type="button"
                      className={cn(
                        'w-full border px-3 py-2 text-left transition-colors hover:bg-muted/60',
                        selected ? 'border-primary bg-primary/5' : 'border-transparent'
                      )}
                      onClick={() => setSelectedClusterKey(getClusterKey(cluster))}
                    >
                      <div className="flex items-center justify-between gap-2">
                        <div className="flex min-w-0 items-center gap-2">
                          <Hash className="h-4 w-4 shrink-0 text-muted-foreground" />
                          <span className="truncate text-sm font-semibold">{cluster.cluster_id}</span>
                        </div>
                        <Badge variant="outline" className="shrink-0 tabular-nums">
                          {cluster.size}
                        </Badge>
                      </div>
                      <div className="mt-1 truncate text-xs text-muted-foreground" title={preview}>
                        {preview || '无预览成员'}
                      </div>
                    </button>
                  )
                })
              )}
            </div>
          </ScrollArea>
        </div>

        <div className="flex min-h-[24rem] flex-col border bg-background lg:min-h-0">
          <div className="flex flex-col gap-2 border-b px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <Hash className="h-4 w-4 text-muted-foreground" />
                <span className="font-semibold">
                  {activeCluster ? `聚类 ${activeCluster.cluster_id}` : '未选择聚类'}
                </span>
                {activeCluster && (
                  <Badge variant="secondary" className="tabular-nums">
                    {activeCluster.size}
                  </Badge>
                )}
              </div>
              {activeCluster && (
                <div className="mt-1 truncate font-mono text-xs text-muted-foreground">
                  {getProfileLabel(activeCluster.embedding_profile_marker)}
                </div>
              )}
            </div>
            <div className="text-sm text-muted-foreground tabular-nums">{members.length} 个成员</div>
          </div>
          <ScrollArea className="min-h-0 flex-1">
            {memberQuery.isPending ? (
              <div className="space-y-2 p-3">
                {Array.from({ length: 8 }).map((_, index) => (
                  <Skeleton key={index} className="h-14 w-full" />
                ))}
              </div>
            ) : !activeCluster ? (
              <div className="px-4 py-12 text-center text-sm text-muted-foreground">暂无聚类</div>
            ) : members.length === 0 ? (
              <div className="px-4 py-12 text-center text-sm text-muted-foreground">暂无成员</div>
            ) : (
              <div>
                <div className="hidden grid-cols-[4.5rem_minmax(0,1.1fr)_minmax(0,1fr)_5rem_2.75rem] gap-2 border-b bg-muted/40 px-3 py-2 text-xs font-medium text-muted-foreground sm:grid">
                  <div>ID</div>
                  <div>Situation</div>
                  <div>Style</div>
                  <div className="text-right">次数</div>
                  <div />
                </div>
                {members.map((member) => (
                  <ClusterMemberRow
                    key={member.id}
                    member={member}
                    onOpenExpression={onOpenExpression}
                  />
                ))}
              </div>
            )}
          </ScrollArea>
        </div>
      </div>
    </div>
  )
}
