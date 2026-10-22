import { Edit, ListFilter, Star, Trash2 } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { DataPagination } from '@/components/ui/data-pagination'
import { DataPanel, PageFrame } from '@/components/ui/surface'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
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

import type { Expression } from '@/types/expression'

type ReviewFilter = 'all' | 'user_checked' | 'unchecked'

/**
 * 表达方式列表组件（桌面端Table + 移动端Card视图 + 分页）
 */
export function ExpressionList({
  expressions,
  loading,
  total,
  page,
  pageSize,
  selectedIds,
  chatNameMap,
  hideChatColumn = false,
  reviewFilter,
  className,
  toolbar,
  onEdit,
  onDelete,
  onReviewFilterChange,
  onToggleReviewStatus,
  onToggleSelect,
  onToggleSelectAll,
  onPageChange,
  onPageSizeChange,
}: {
  expressions: Expression[]
  loading: boolean
  total: number
  page: number
  pageSize: number
  selectedIds: Set<number>
  chatNameMap: Map<string, string>
  hideChatColumn?: boolean
  reviewFilter: ReviewFilter
  className?: string
  toolbar?: ReactNode
  onEdit: (expression: Expression) => void
  onDelete: (expression: Expression) => void
  onReviewFilterChange: (filter: ReviewFilter) => void
  onToggleReviewStatus: (expression: Expression) => Promise<void>
  onToggleSelect: (id: number) => void
  onToggleSelectAll: () => void
  onPageChange: (newPage: number) => void
  onPageSizeChange?: (newPageSize: number) => void
}) {
  const [updatingReviewIds, setUpdatingReviewIds] = useState<Set<number>>(new Set())

  const getChatName = (expression: Expression): string => {
    return expression.chat_name || chatNameMap.get(expression.chat_id) || expression.chat_id
  }

  const tableColSpan = hideChatColumn ? 5 : 6
  const selectedCount = expressions.filter((expression) => selectedIds.has(expression.id)).length

  const isUserApproved = (expression: Expression) => {
    return expression.checked && expression.modified_by?.toLowerCase() === 'user'
  }

  const handleToggleReviewStatus = async (expression: Expression) => {
    setUpdatingReviewIds((current) => new Set(current).add(expression.id))
    try {
      await onToggleReviewStatus(expression)
    } finally {
      setUpdatingReviewIds((current) => {
        const next = new Set(current)
        next.delete(expression.id)
        return next
      })
    }
  }

  return (
    <PageFrame
      desktopOnly
      data-expression-browser-panel="true"
      className={cn('flex min-h-0 min-w-0 flex-col', className)}
    >
      {toolbar}
      {/* 桌面端表格视图 */}
      <DataPanel
        padding="none"
        inset
        data-expression-table-viewport="true"
        className="hidden min-h-0 flex-1 overflow-auto md:block"
      >
        <Table density="compact" className="table-fixed" aria-label="表达方式列表">
          <colgroup>
            <col className="w-11" />
            <col className={hideChatColumn ? 'w-[36%]' : 'w-[28%]'} />
            <col />
            {!hideChatColumn && <col className="w-[18%]" />}
            <col className="w-24" />
            <col className="w-24" />
          </colgroup>
          <TableHeader>
            <TableRow>
              <TableHead>
                <Checkbox
                  size="sm"
                  aria-label="全选本页表达方式"
                  checked={
                    selectedCount > 0 && selectedCount === expressions.length
                      ? true
                      : selectedCount > 0
                        ? 'indeterminate'
                        : false
                  }
                  onCheckedChange={onToggleSelectAll}
                  disabled={loading || expressions.length === 0}
                />
              </TableHead>
              <TableHead>情境</TableHead>
              <TableHead>风格</TableHead>
              {!hideChatColumn && <TableHead>聊天</TableHead>}
              <TableHead>
                <div className="flex items-center gap-1">
                  <span>精选</span>
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon-xs"
                        className="text-muted-foreground"
                        title="筛选精选状态"
                        aria-label="筛选精选状态"
                      >
                        <ListFilter className="h-3.5 w-3.5" />
                      </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="start">
                      <DropdownMenuRadioGroup
                        value={reviewFilter}
                        onValueChange={(value) => onReviewFilterChange(value as ReviewFilter)}
                      >
                        <DropdownMenuRadioItem value="all">全部</DropdownMenuRadioItem>
                        <DropdownMenuRadioItem value="user_checked">已精选</DropdownMenuRadioItem>
                        <DropdownMenuRadioItem value="unchecked">未精选</DropdownMenuRadioItem>
                      </DropdownMenuRadioGroup>
                    </DropdownMenuContent>
                  </DropdownMenu>
                </div>
              </TableHead>
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
            ) : expressions.length === 0 ? (
              <TableRow>
                <TableCell
                  colSpan={tableColSpan}
                  className="text-muted-foreground py-8 text-center"
                >
                  暂无数据
                </TableCell>
              </TableRow>
            ) : (
              expressions.map((expression) => (
                <TableRow key={expression.id}>
                  <TableCell>
                    <Checkbox
                      size="sm"
                      aria-label={`选择表达方式：${expression.situation}`}
                      checked={selectedIds.has(expression.id)}
                      onCheckedChange={() => onToggleSelect(expression.id)}
                    />
                  </TableCell>
                  <TableCell className="truncate" title={expression.situation}>
                    {expression.situation}
                  </TableCell>
                  <TableCell title={expression.style}>
                    <span
                      data-expression-style="true"
                      className="bg-muted/30 inline-block max-w-full truncate border-l-2 border-orange-500/80 px-2 py-0.5 align-middle"
                    >
                      {expression.style}
                    </span>
                  </TableCell>
                  {!hideChatColumn && (
                    <TableCell
                      className="text-muted-foreground truncate"
                      title={getChatName(expression)}
                      style={{ wordBreak: 'keep-all' }}
                    >
                      <span className="block overflow-hidden text-ellipsis whitespace-nowrap">
                        {getChatName(expression)}
                      </span>
                    </TableCell>
                  )}
                  <TableCell>
                    <Button
                      variant={isUserApproved(expression) ? 'outline' : 'ghost'}
                      aria-pressed={isUserApproved(expression)}
                      size="icon-xs"
                      onClick={() => handleToggleReviewStatus(expression)}
                      disabled={updatingReviewIds.has(expression.id)}
                      title={isUserApproved(expression) ? '取消精选' : '精选'}
                      aria-label={isUserApproved(expression) ? '取消精选' : '精选'}
                      className={isUserApproved(expression) ? '' : 'text-muted-foreground'}
                    >
                      <Star className={isUserApproved(expression) ? 'text-orange-500' : ''} />
                    </Button>
                  </TableCell>
                  <TableCell className="text-right">
                    <div className="flex justify-end gap-1">
                      <Button
                        variant="ghost"
                        size="icon-xs"
                        className="text-muted-foreground/50 hover:text-foreground focus-visible:text-foreground"
                        onClick={() => onEdit(expression)}
                        title="编辑"
                        aria-label="编辑"
                      >
                        <Edit className="h-4 w-4" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon-xs"
                        className="text-muted-foreground/50 hover:text-destructive focus-visible:text-destructive"
                        onClick={() => onDelete(expression)}
                        title="删除"
                        aria-label="删除"
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
        ) : expressions.length === 0 ? (
          <div className="text-muted-foreground py-8 text-center">暂无数据</div>
        ) : (
          expressions.map((expression) => (
            <div
              key={expression.id}
              className="min-w-0 space-y-2 overflow-hidden rounded-lg border bg-transparent p-3"
            >
              {/* 复选框和情境 */}
              <div className="flex items-start gap-2">
                <Checkbox
                  size="sm"
                  aria-label={`选择表达方式：${expression.situation}`}
                  checked={selectedIds.has(expression.id)}
                  onCheckedChange={() => onToggleSelect(expression.id)}
                  className="mt-1"
                />
                <div className="min-w-0 flex-1 space-y-2 overflow-hidden">
                  <div>
                    <div className="text-muted-foreground mb-1 text-xs">情境</div>
                    <h3
                      className="line-clamp-3 w-full text-sm leading-relaxed font-semibold break-all"
                      title={expression.situation}
                    >
                      {expression.situation}
                    </h3>
                  </div>
                  <div>
                    <div className="text-muted-foreground mb-1 text-xs">风格</div>
                    <p
                      className="line-clamp-3 w-full text-sm leading-relaxed break-all"
                      title={expression.style}
                    >
                      {expression.style}
                    </p>
                  </div>
                </div>
              </div>

              {/* 聊天名称 */}
              {!hideChatColumn && (
                <div className="text-sm">
                  <div className="text-muted-foreground mb-1 text-xs">聊天</div>
                  <p
                    className="truncate text-sm leading-relaxed"
                    title={getChatName(expression)}
                    style={{ wordBreak: 'keep-all' }}
                  >
                    {getChatName(expression)}
                  </p>
                </div>
              )}

              {/* 操作按钮 */}
              <div className="grid grid-cols-3 gap-2 border-t pt-2">
                <Button
                  variant="outline"
                  aria-pressed={isUserApproved(expression)}
                  size="icon"
                  onClick={() => handleToggleReviewStatus(expression)}
                  disabled={updatingReviewIds.has(expression.id)}
                  className="h-9 w-full justify-center"
                  title={isUserApproved(expression) ? '取消精选' : '精选'}
                  aria-label={isUserApproved(expression) ? '取消精选' : '精选'}
                >
                  <Star
                    className={cn('h-3 w-3', isUserApproved(expression) && 'text-orange-500')}
                  />
                </Button>
                <Button
                  variant="outline"
                  size="icon"
                  onClick={() => onEdit(expression)}
                  className="h-9 w-full justify-center"
                  title="编辑"
                  aria-label="编辑"
                >
                  <Edit className="h-3 w-3" />
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => onDelete(expression)}
                  className="text-destructive hover:text-destructive h-9 justify-center px-2 text-xs"
                >
                  <Trash2 className="mr-1 h-3 w-3" />
                  删除
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
