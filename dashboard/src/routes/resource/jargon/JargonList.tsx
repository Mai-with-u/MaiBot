import { Check, Edit, Globe, HelpCircle, Trash2, X } from 'lucide-react'
import type { ReactNode } from 'react'

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

import type { Jargon } from '@/types/jargon'

interface JargonListProps {
  jargons: Jargon[]
  loading: boolean
  total: number
  page: number
  pageSize: number
  selectedIds: Set<number>
  hideChatColumn?: boolean
  className?: string
  toolbar?: ReactNode
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
          是黑话
        </Badge>
      ) : (
        <Badge variant="secondary">
          <X className="mr-1 h-3 w-3" />
          无黑话
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

function renderManualMarker(createdBy: Jargon['created_by'], showLabel = false) {
  if (createdBy !== 'MANUAL') {
    return null
  }

  return showLabel ? (
    <Badge variant="outline">
      <Check className="mr-1 h-3 w-3" />
      手动
    </Badge>
  ) : (
    <Check className="h-4 w-4 text-green-600" aria-label="手动创建" />
  )
}

function formatJargonChatDisplay(jargon: Jargon) {
  const chatNames = jargon.chat_names?.length ? jargon.chat_names : []
  if (chatNames.length > 0) {
    return chatNames.join('、')
  }
  return jargon.chat_name || jargon.session_id
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
  hideChatColumn = false,
  className,
  toolbar,
  onEdit,
  onDelete,
  onToggleSelect,
  onToggleSelectAll,
  onPageChange,
  onPageSizeChange,
}: JargonListProps) {
  const tableColSpan = hideChatColumn ? 6 : 7
  const selectedCount = jargons.filter((jargon) => selectedIds.has(jargon.id)).length

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
            <col className={hideChatColumn ? 'w-[26%]' : 'w-[22%]'} />
            <col />
            {!hideChatColumn && <col className="w-[18%]" />}
            <col className="w-16" />
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
              {!hideChatColumn && <TableHead>聊天</TableHead>}
              <TableHead className="text-center">手动</TableHead>
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
                  暂无数据
                </TableCell>
              </TableRow>
            ) : (
              jargons.map((jargon) => (
                <TableRow key={jargon.id}>
                  <TableCell>
                    <Checkbox
                      size="sm"
                      aria-label={`选择黑话：${jargon.content}`}
                      checked={selectedIds.has(jargon.id)}
                      onCheckedChange={() => onToggleSelect(jargon.id)}
                    />
                  </TableCell>
                  <TableCell>
                    <div className="flex items-center gap-1.5">
                      {jargon.is_global && (
                        <span title="全局黑话">
                          <Globe className="h-4 w-4 flex-shrink-0 text-blue-500" />
                        </span>
                      )}
                      <span className="truncate" title={jargon.content}>
                        {jargon.content}
                      </span>
                    </div>
                  </TableCell>
                  <TableCell title={jargon.meaning || ''}>
                    {jargon.meaning ? (
                      <span className="bg-muted/30 inline-block max-w-full truncate border-l-2 border-orange-500/80 px-2 py-0.5 align-middle">
                        {jargon.meaning}
                      </span>
                    ) : (
                      <span className="text-muted-foreground">-</span>
                    )}
                  </TableCell>
                  {!hideChatColumn && (
                    <TableCell
                      className="text-muted-foreground truncate"
                      title={formatJargonChatDisplay(jargon)}
                    >
                      <span className="block truncate">{formatJargonChatDisplay(jargon)}</span>
                    </TableCell>
                  )}
                  <TableCell className="text-center">
                    <div className="flex justify-center">
                      {renderManualMarker(jargon.created_by)}
                    </div>
                  </TableCell>
                  <TableCell className="text-center">{jargon.count}</TableCell>
                  <TableCell className="text-right">
                    <div className="flex justify-end gap-1">
                      <Button
                        variant="ghost"
                        size="icon-xs"
                        className="text-muted-foreground/50 hover:text-foreground focus-visible:text-foreground"
                        onClick={() => onEdit(jargon)}
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
                        title="删除"
                        aria-label="删除黑话"
                      >
                        <Trash2 className="h-4 w-4" />
                      </Button>
                    </div>
                  </TableCell>
                </TableRow>
              ))
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
          <div className="text-muted-foreground py-8 text-center">暂无数据</div>
        ) : (
          jargons.map((jargon) => (
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
                    {jargon.is_global && <Globe className="h-4 w-4 flex-shrink-0 text-blue-500" />}
                    <h3 className="text-sm font-semibold break-all">{jargon.content}</h3>
                  </div>
                  {jargon.meaning && (
                    <p className="text-muted-foreground line-clamp-3 text-xs break-all">
                      {jargon.meaning}
                    </p>
                  )}
                  <div className="flex flex-wrap items-center gap-2 text-xs">
                    {renderJargonStatus(jargon)}
                    {renderManualMarker(jargon.created_by, true)}
                    <span className="text-muted-foreground">次数: {jargon.count}</span>
                  </div>
                  {!hideChatColumn && (
                    <div className="text-muted-foreground truncate text-xs">
                      聊天: {formatJargonChatDisplay(jargon)}
                    </div>
                  )}
                </div>
              </div>
              <div className="flex flex-wrap gap-1 border-t pt-2">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => onEdit(jargon)}
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
