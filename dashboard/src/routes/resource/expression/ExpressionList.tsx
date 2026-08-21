import {
  Check,
  ChevronLeft,
  ChevronRight,
  ChevronsLeft,
  ChevronsRight,
  Edit,
  ListFilter,
  Star,
  StarOff,
  Trash2,
} from 'lucide-react'
import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { ThinkingIllustration } from '@/components/ui/thinking-illustration'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { useToast } from '@/hooks/use-toast'
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
  onEdit,
  onDelete,
  onReviewFilterChange,
  onToggleReviewStatus,
  onToggleSelect,
  onToggleSelectAll,
  onPageChange,
  onJumpToPage,
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
  onEdit: (expression: Expression) => void
  onDelete: (expression: Expression) => void
  onReviewFilterChange: (filter: ReviewFilter) => void
  onToggleReviewStatus: (expression: Expression) => Promise<void>
  onToggleSelect: (id: number) => void
  onToggleSelectAll: () => void
  onPageChange: (newPage: number) => void
  onJumpToPage: (targetPage: string) => void
  onPageSizeChange?: (newPageSize: number) => void
}) {
  const { toast } = useToast()
  const [updatingReviewIds, setUpdatingReviewIds] = useState<Set<number>>(new Set())

  const getChatName = (expression: Expression): string => {
    return expression.chat_name || chatNameMap.get(expression.chat_id) || expression.chat_id
  }

  const getReviewBadge = (expression: Expression) => {
    const modifier = expression.modified_by?.toLowerCase()

    if (expression.checked && modifier === 'user') {
      return <Check className="h-4 w-4 stroke-[3]" aria-label="已精选" />
    }
    return null
  }

  const totalPages = Math.ceil(total / pageSize)
  const tableColSpan = hideChatColumn ? 5 : 6

  const isUserApproved = (expression: Expression) => {
    return expression.checked && expression.modified_by === 'user'
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

  const handleJumpToPage = (jumpToPage: string) => {
    const targetPage = parseInt(jumpToPage)
    if (targetPage >= 1 && targetPage <= totalPages) {
      onJumpToPage(jumpToPage)
    } else {
      toast({
        title: '无效的页码',
        description: `请输入1-${totalPages}之间的页码`,
        variant: 'destructive',
      })
    }
  }

  return (
    <div className={cn('flex min-h-0 flex-col border-2 bg-transparent', className)}>
      {/* 桌面端表格视图 */}
      <div
        data-expression-table-viewport="true"
        className="hidden min-h-0 flex-1 overflow-auto md:block"
      >
        <Table aria-label="表达方式列表">
          <TableHeader>
            <TableRow>
              <TableHead className="w-12">
                <Checkbox
                  checked={selectedIds.size === expressions.length && expressions.length > 0}
                  onCheckedChange={onToggleSelectAll}
                />
              </TableHead>
              <TableHead>情境</TableHead>
              <TableHead>风格</TableHead>
              {!hideChatColumn && <TableHead>聊天</TableHead>}
              <TableHead>
                <div className="flex items-center gap-1.5">
                  <span>精选</span>
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <button
                        type="button"
                        className="text-muted-foreground hover:text-foreground focus-visible:ring-ring inline-flex h-7 w-7 items-center justify-center transition-colors focus-visible:ring-1 focus-visible:outline-none"
                        title="筛选精选状态"
                        aria-label="筛选精选状态"
                      >
                        <ListFilter className="h-3.5 w-3.5" />
                      </button>
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
              <TableHead className="text-right">操作</TableHead>
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
                      checked={selectedIds.has(expression.id)}
                      onCheckedChange={() => onToggleSelect(expression.id)}
                    />
                  </TableCell>
                  <TableCell className="max-w-xs truncate font-medium">
                    {expression.situation}
                  </TableCell>
                  <TableCell className="max-w-xs truncate">{expression.style}</TableCell>
                  {!hideChatColumn && (
                    <TableCell
                      className="max-w-[200px] truncate"
                      title={getChatName(expression)}
                      style={{ wordBreak: 'keep-all' }}
                    >
                      <span className="block overflow-hidden text-ellipsis whitespace-nowrap">
                        {getChatName(expression)}
                      </span>
                    </TableCell>
                  )}
                  <TableCell>
                    <div className="flex flex-wrap items-center gap-1.5">
                      {getReviewBadge(expression)}
                    </div>
                  </TableCell>
                  <TableCell className="text-right">
                    <div className="flex justify-end gap-2">
                      <Button
                        variant={isUserApproved(expression) ? 'outline' : 'default'}
                        size="icon"
                        className="h-8 w-8"
                        onClick={() => handleToggleReviewStatus(expression)}
                        disabled={updatingReviewIds.has(expression.id)}
                        title={isUserApproved(expression) ? '取消精选' : '精选'}
                        aria-label={isUserApproved(expression) ? '取消精选' : '精选'}
                      >
                        {isUserApproved(expression) ? (
                          <StarOff className="h-4 w-4" />
                        ) : (
                          <Star className="h-4 w-4" />
                        )}
                      </Button>
                      <Button
                        variant="default"
                        size="icon"
                        className="h-8 w-8"
                        onClick={() => onEdit(expression)}
                        title="编辑"
                        aria-label="编辑"
                      >
                        <Edit className="h-4 w-4" />
                      </Button>
                      <Button
                        size="icon"
                        onClick={() => onDelete(expression)}
                        className="h-8 w-8 bg-red-600 text-white hover:bg-red-700"
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
      </div>

      {/* 移动端卡片视图 */}
      <div className="space-y-4 p-4 md:hidden">
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
              className="space-y-4 overflow-hidden rounded-lg border bg-transparent p-4"
            >
              {/* 复选框和情境 */}
              <div className="flex items-start gap-3">
                <Checkbox
                  checked={selectedIds.has(expression.id)}
                  onCheckedChange={() => onToggleSelect(expression.id)}
                  className="mt-1"
                />
                <div className="min-w-0 flex-1 space-y-3 overflow-hidden">
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

              <div className="text-sm">
                <div className="text-muted-foreground mb-1 text-xs">精选</div>
                <div className="flex flex-wrap items-center gap-2">
                  {getReviewBadge(expression)}
                </div>
              </div>

              {/* 操作按钮 */}
              <div className="grid grid-cols-4 gap-2 overflow-hidden border-t pt-3">
                <Button
                  variant={isUserApproved(expression) ? 'outline' : 'default'}
                  size="icon"
                  onClick={() => handleToggleReviewStatus(expression)}
                  disabled={updatingReviewIds.has(expression.id)}
                  className="h-9 w-full justify-center"
                  title={isUserApproved(expression) ? '取消精选' : '精选'}
                  aria-label={isUserApproved(expression) ? '取消精选' : '精选'}
                >
                  {isUserApproved(expression) ? (
                    <StarOff className="h-3 w-3" />
                  ) : (
                    <Star className="h-3 w-3" />
                  )}
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
      {total > 0 && (
        <Pagination
          total={total}
          page={page}
          pageSize={pageSize}
          onPageChange={onPageChange}
          onJumpToPage={handleJumpToPage}
          onPageSizeChange={onPageSizeChange}
        />
      )}
    </div>
  )
}

/**
 * 分页组件
 */
function Pagination({
  total,
  page,
  pageSize,
  onPageChange,
  onJumpToPage,
  onPageSizeChange,
}: {
  total: number
  page: number
  pageSize: number
  onPageChange: (newPage: number) => void
  onJumpToPage: (targetPage: string) => void
  onPageSizeChange?: (newPageSize: number) => void
}) {
  const [jumpToPage, setJumpToPage] = useState('')
  const totalPages = Math.ceil(total / pageSize)

  const handleJump = () => {
    if (jumpToPage) {
      onJumpToPage(jumpToPage)
      setJumpToPage('')
    }
  }

  return (
    <div className="flex flex-col items-center justify-between gap-2 border-t px-3 py-2 sm:flex-row sm:py-1.5">
      <div className="text-muted-foreground flex items-center gap-2 text-xs">
        <span>
          {total} 条 · {page}/{totalPages}
        </span>
        {onPageSizeChange && (
          <div className="flex items-center gap-1.5">
            <Label htmlFor="page-size" className="whitespace-nowrap">
              每页
            </Label>
            <Select
              value={pageSize.toString()}
              onValueChange={(value) => onPageSizeChange(parseInt(value))}
            >
              <SelectTrigger id="page-size" className="h-7 w-16 text-xs">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="10">10</SelectItem>
                <SelectItem value="20">20</SelectItem>
                <SelectItem value="50">50</SelectItem>
                <SelectItem value="100">100</SelectItem>
              </SelectContent>
            </Select>
          </div>
        )}
      </div>
      <div className="flex w-full flex-wrap items-center justify-center gap-1.5 sm:w-auto sm:flex-nowrap">
        {/* 首页 */}
        <Button
          variant="outline"
          size="sm"
          onClick={() => onPageChange(1)}
          disabled={page === 1}
          className="hidden h-7 px-2 sm:flex"
          title="首页"
          aria-label="首页"
        >
          <ChevronsLeft className="h-3.5 w-3.5" />
        </Button>

        {/* 上一页 */}
        <Button
          variant="outline"
          size="sm"
          onClick={() => onPageChange(page - 1)}
          disabled={page === 1}
          className="h-7 px-2"
          title="上一页"
          aria-label="上一页"
        >
          <ChevronLeft className="h-3.5 w-3.5" />
        </Button>

        {/* 页码跳转 */}
        <div className="flex items-center gap-1.5">
          <Input
            type="number"
            value={jumpToPage}
            onChange={(e) => setJumpToPage(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleJump()}
            placeholder={page.toString()}
            className="h-7 w-12 text-center text-xs"
            min={1}
            max={totalPages}
          />
          <Button
            variant="outline"
            size="sm"
            onClick={handleJump}
            disabled={!jumpToPage}
            className="h-7 px-2 text-xs"
          >
            跳
          </Button>
        </div>

        {/* 下一页 */}
        <Button
          variant="outline"
          size="sm"
          onClick={() => onPageChange(page + 1)}
          disabled={page >= totalPages}
          className="h-7 px-2"
          title="下一页"
          aria-label="下一页"
        >
          <ChevronRight className="h-3.5 w-3.5" />
        </Button>

        {/* 末页 */}
        <Button
          variant="outline"
          size="sm"
          onClick={() => onPageChange(totalPages)}
          disabled={page >= totalPages}
          className="hidden h-7 px-2 sm:flex"
          title="末页"
          aria-label="末页"
        >
          <ChevronsRight className="h-3.5 w-3.5" />
        </Button>
      </div>
    </div>
  )
}
