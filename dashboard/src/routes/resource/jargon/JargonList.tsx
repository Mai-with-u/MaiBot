import { Check, ChevronRight, Edit, Globe, HelpCircle, Lock, Trash2, X } from 'lucide-react'
import { useQueries } from '@tanstack/react-query'
import { useState, type ReactNode } from 'react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { DataPagination } from '@/components/ui/data-pagination'
import { DataPanel, PageFrame } from '@/components/ui/surface'
import { ThinkingIllustration } from '@/components/ui/thinking-illustration'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { cn } from '@/lib/utils'
import { getJargonVersions } from '@/lib/jargon-api'

import type { Jargon } from '@/types/jargon'

interface JargonListProps {
  jargons: Jargon[]
  loading: boolean
  total: number
  page: number
  pageSize: number
  selectedIds: Set<number>
  emptyAction?: ReactNode
  hideChatColumn?: boolean
  className?: string
  toolbar?: ReactNode
  onOpenTerm?: (jargon: Jargon) => void
  onEdit: (jargon: Jargon) => void
  onDelete: (jargon: Jargon) => void
  onToggleSelect: (id: number) => void
  onToggleSelectAll: () => void
  onPageChange: (page: number) => void
  onPageSizeChange?: (pageSize: number) => void
}

/**
 * 渲染黑话状态徽章
 */
function renderJargonStatus(jargon: Jargon) {
  return (
    <>
      {jargon.is_jargon ? (
        <Badge variant="default" className="bg-green-600 hover:bg-green-700">
          <Check className="mr-1 h-3 w-3" />
          黑话
        </Badge>
      ) : jargon.is_jargon === null ? (
        <Badge variant="outline">积累中</Badge>
      ) : (
        <Badge variant="secondary">
          <X className="mr-1 h-3 w-3" />
          不是黑话
        </Badge>
      )}
      {jargon.is_legacy_empty_meaning && (
        <Badge variant="outline">
          <HelpCircle className="mr-1 h-3 w-3" />
          旧数据
        </Badge>
      )}
    </>
  )
}

function renderUpdateMarker(allowUpdate: boolean, showLabel = false) {
  if (allowUpdate) {
    return null
  }

  return showLabel ? (
    <Badge variant="outline">
      <Check className="mr-1 h-3 w-3" />
      已暂停更新
    </Badge>
  ) : (
    <span title="已暂停更新">
      <Lock className="h-4 w-4" aria-label="已暂停更新" />
    </span>
  )
}

function formatJargonChatDisplay(jargon: Jargon) {
  if (jargon.scope_name) return jargon.scope_name
  if (jargon.scope_names?.length) return jargon.scope_names.join('、')
  const chatNames = jargon.chat_names?.length ? jargon.chat_names : []
  if (chatNames.length > 0) {
    return chatNames.join('、')
  }
  return jargon.chat_name || jargon.session_id
}

function scopeVersions(jargon: Jargon): Jargon[] {
  return jargon.scope_versions?.length ? jargon.scope_versions : [jargon]
}

/**
 * 黑话列表组件（桌面端表格 + 移动端卡片 + 分页）
 */
export function JargonList({
  jargons,
  loading,
  total,
  page,
  pageSize,
  selectedIds,
  emptyAction,
  hideChatColumn = false,
  className,
  toolbar,
  onEdit,
  onOpenTerm = onEdit,
  onDelete,
  onToggleSelect,
  onToggleSelectAll,
  onPageChange,
  onPageSizeChange,
}: JargonListProps) {
  const tableColSpan = hideChatColumn ? 6 : 7
  const selectedCount = jargons.filter((jargon) => selectedIds.has(jargon.id)).length

  const [expandedTerms, setExpandedTerms] = useState(() => new Set<string>())
  const expanding = jargons.filter(row => expandedTerms.has(row.content) && !row.scope_versions?.length && (row.scope_ids?.length ?? 0) > 1)
  const queries = useQueries({ queries: expanding.map(row => ({
    queryKey: ['jargon', 'versions', row.content, false],
    queryFn: () => getJargonVersions(row.content, false),
  })) })
  const expandedQueries = new Map(expanding.map((row, i) => [row.content, queries[i]]))
  const entries = jargons.map((jargon) => {
    const term = jargon.content
    const query = expandedQueries.get(term)
    const available = new Map(query?.data?.map(row => [row.id, row]))
    const loaded = jargon.scope_ids?.flatMap(id => available.get(id) ?? [])
    const versions = loaded?.length ? loaded : scopeVersions(jargon)
    const versionCount = jargon.scope_ids?.length || versions.length
    const expanded = expandedTerms.has(term)
    const label = `${expanded ? '收起' : '展开'}「${jargon.content}」的 ${versionCount} 个作用域`
    const toggleExpanded = () => setExpandedTerms((previous) => {
      const next = new Set(previous)
      if (next.has(term)) next.delete(term)
      else next.add(term)
      return next
    })
    const toggle = versionCount > 1 && (
      <Button
        type="button"
        variant="ghost"
        size="xs"
        className="shrink-0 gap-0.5 px-1 tabular-nums md:hover:bg-transparent"
        aria-label={label}
        title={label}
        aria-expanded={expanded}
        onClick={toggleExpanded}
      >
        <ChevronRight className={cn('size-3', expanded && 'rotate-90')} aria-hidden="true" />
        {query?.isPending ? '加载中…' : query?.isError ? '加载失败' : versionCount}
      </Button>
    )
    return {
      jargon,
      visibleVersions: expanded ? versions : versions.slice(0, 1),
      versionCount,
      toggle,
      toggleExpanded,
    }
  })

  return (
    <PageFrame
      desktopOnly
      data-jargon-browser-panel="true"
      className={cn('flex min-h-0 min-w-0 flex-col', className)}
    >
      {toolbar}
      {/* 桌面端表格视图 */}
      <DataPanel
        padding="none"
        inset
        data-jargon-table-viewport="true"
        className="hidden min-h-0 flex-1 overflow-auto md:block"
      >
        <Table density="compact" className="table-fixed" aria-label="黑话列表">
          <colgroup>
            <col className="w-11" />
            <col className="w-[14%]" />
            <col />
            {!hideChatColumn && <col className="w-[18%]" />}
            <col className="w-28" />
            <col className="w-22" />
            <col className="w-24" />
          </colgroup>
          <TableHeader>
            <TableRow>
              <TableHead>
                <Checkbox
                  size="sm"
                  aria-label="全选本页黑话"
                  checked={
                    selectedCount > 0 && selectedCount === jargons.length
                      ? true
                      : selectedCount > 0
                        ? 'indeterminate'
                        : false
                  }
                  onCheckedChange={onToggleSelectAll}
                  disabled={loading || jargons.length === 0}
                />
              </TableHead>
              <TableHead>内容</TableHead>
              <TableHead>含义</TableHead>
              {!hideChatColumn && <TableHead>作用域</TableHead>}
              <TableHead className="text-center">状态</TableHead>
              <TableHead className="text-center">遇见次数</TableHead>
              <TableHead>
                <span className="sr-only">操作</span>
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {loading ? (
              <TableRow>
                <TableCell
                  colSpan={tableColSpan}
                  className="text-muted-foreground py-8 text-center"
                >
                  <ThinkingIllustration size="sm" className="mx-auto" />
                </TableCell>
              </TableRow>
            ) : jargons.length === 0 ? (
              <TableRow>
                <TableCell
                  colSpan={tableColSpan}
                  className="text-muted-foreground py-8 text-center"
                >
                  暂无数据 {emptyAction}
                </TableCell>
              </TableRow>
            ) : (
              entries.map(({ jargon, visibleVersions, versionCount, toggle, toggleExpanded }) =>
                visibleVersions.map((version, index) => {
                  const first = index === 0
                  return (
                    <TableRow
                      key={version.id}
                      data-jargon-row
                      className="cursor-pointer hover:bg-transparent"
                      onClick={(event) => {
                        const target = event.target
                        if (!(target instanceof Element) || target.closest('button, input, [role="checkbox"]')) return
                        if (target.closest('[data-term-cell]')) {
                          toggleExpanded()
                          return
                        }
                        if (expandedTerms.has(jargon.content)) onEdit(version)
                        else onOpenTerm(jargon)
                      }}
                    >
                      {first && (
                        <TableCell data-term-cell={versionCount > 1 || undefined} rowSpan={visibleVersions.length} className={visibleVersions.length > 1 ? 'align-top' : undefined}>
                          <Checkbox
                            size="sm"
                            aria-label={`选择黑话：${jargon.content}`}
                            checked={selectedIds.has(jargon.id)}
                            onCheckedChange={() => onToggleSelect(jargon.id)}
                          />
                        </TableCell>
                      )}
                      {first && (
                        <TableCell data-term-cell={versionCount > 1 || undefined} rowSpan={visibleVersions.length} className={visibleVersions.length > 1 ? 'align-top' : undefined}>
                          <div className="flex items-center gap-1.5">
                            {versionCount === 1 && renderUpdateMarker(jargon.allow_update)}
                            {versionCount === 1 && jargon.is_global && (
                              <span title="通用词条">
                                <Globe className="h-4 w-4 flex-shrink-0 text-blue-500" />
                              </span>
                            )}
                            <span className="truncate" title={jargon.content}>
                              {jargon.content}
                            </span>
                            {toggle}
                          </div>
                        </TableCell>
                      )}
                      <TableCell title={version.meaning || undefined}>
                        {version.meaning ? (
                          <span className="line-clamp-2 break-words whitespace-normal">
                            {version.meaning}
                          </span>
                        ) : (
                          <span className="text-muted-foreground">暂无释义</span>
                        )}
                      </TableCell>
                      {!hideChatColumn && (
                        <TableCell
                          className="text-muted-foreground truncate"
                          title={formatJargonChatDisplay(version)}
                        >
                          <button
                            type="button"
                            className="block max-w-full truncate text-left hover:underline"
                            onClick={() => onEdit(version)}
                            aria-label={`查看${formatJargonChatDisplay(version)}释义`}
                          >
                            {formatJargonChatDisplay(version)}
                          </button>
                        </TableCell>
                      )}
                      <TableCell className="text-center">
                        <div className="flex flex-wrap items-center justify-center gap-1">
                          {renderJargonStatus(version)}
                          {versionCount > 1 && renderUpdateMarker(version.allow_update)}
                        </div>
                      </TableCell>
                      <TableCell className="text-center">{version.count}</TableCell>
                      {first && (
                        <TableCell data-shared-actions={visibleVersions.length > 1 || undefined} rowSpan={visibleVersions.length} className={cn('text-right', visibleVersions.length > 1 && 'align-top')}>
                          <div className="flex justify-end gap-1">
                            <Button
                              variant="ghost"
                              size="icon-xs"
                              className="text-muted-foreground/50 hover:text-foreground focus-visible:text-foreground"
                              onClick={() => onOpenTerm(jargon)}
                              title="查看/编辑"
                              aria-label="查看或编辑黑话"
                            >
                              <Edit className="h-4 w-4" />
                            </Button>
                            <Button
                              variant="ghost"
                              size="icon-xs"
                              className="text-muted-foreground/50 hover:text-destructive focus-visible:text-destructive"
                              onClick={() => onDelete(jargon)}
                              data-jargon-delete
                              title="删除"
                              aria-label="删除黑话"
                            >
                              <Trash2 className="h-4 w-4" />
                            </Button>
                          </div>
                        </TableCell>
                      )}
                    </TableRow>
                  )
                })
              )
            )}
          </TableBody>
        </Table>
      </DataPanel>

      {/* 移动端卡片视图 */}
      <div className="min-w-0 space-y-2 md:hidden">
        {loading ? (
          <div className="text-muted-foreground py-8 text-center">
            <ThinkingIllustration size="sm" className="mx-auto" />
          </div>
        ) : jargons.length === 0 ? (
          <div className="text-muted-foreground py-8 text-center">暂无数据 {emptyAction}</div>
        ) : (
          entries.map(({ jargon, visibleVersions, toggle }) => (
            <div
              key={jargon.id}
              className="min-w-0 space-y-2 overflow-hidden rounded-lg border bg-transparent p-3"
            >
              <div className="flex items-start gap-2.5">
                <Checkbox
                  size="sm"
                  aria-label={`选择黑话：${jargon.content}`}
                  checked={selectedIds.has(jargon.id)}
                  onCheckedChange={() => onToggleSelect(jargon.id)}
                  className="mt-1"
                />
                <div className="min-w-0 flex-1 space-y-1.5">
                  <div className="flex items-center gap-2">
                    {!jargon.scope_versions?.length && jargon.is_global && (
                      <Globe className="h-4 w-4 flex-shrink-0 text-blue-500" />
                    )}
                    <h3 className="text-sm font-semibold break-all">{jargon.content}</h3>
                    {toggle}
                  </div>
                  {visibleVersions.map((version) => (
                    <div
                      key={version.id}
                      className="bg-muted/30 space-y-1 rounded-md px-2.5 py-2"
                    >
                      <p className="line-clamp-3 text-sm break-words">
                        {version.meaning || '暂无释义'}
                      </p>
                      <div className="flex flex-wrap items-center gap-1.5 text-xs">
                        {!hideChatColumn && (
                          <button
                            type="button"
                            onClick={() => onEdit(version)}
                            className="text-left break-all underline"
                          >
                            {formatJargonChatDisplay(version)}
                          </button>
                        )}
                        {renderJargonStatus(version)}
                        {renderUpdateMarker(version.allow_update, true)}
                        <span className="text-muted-foreground">遇见 {version.count} 次</span>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
              <div className="flex flex-wrap gap-1 border-t pt-2">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => onOpenTerm(jargon)}
                  className="h-auto px-2 py-1 text-xs"
                >
                  <Edit className="mr-1 h-3 w-3" />
                  查看/编辑
                </Button>
                <Button
                  variant="outline"
                  size="icon"
                  onClick={() => onDelete(jargon)}
                  className="text-destructive hover:text-destructive h-8 w-8"
                  title="删除"
                  aria-label="删除黑话"
                >
                  <Trash2 className="h-3 w-3" />
                </Button>
              </div>
            </div>
          ))
        )}
      </div>

      {/* 分页 */}
      <DataPagination
        total={total}
        page={page}
        pageSize={pageSize}
        onPageChange={onPageChange}
        onPageSizeChange={onPageSizeChange}
        disabled={loading}
        className="shrink-0"
      />
    </PageFrame>
  )
}
