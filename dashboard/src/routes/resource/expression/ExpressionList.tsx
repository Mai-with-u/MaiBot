import { ChevronLeft, ChevronRight, ChevronsLeft, ChevronsRight, Edit, Eye, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { useToast } from '@/hooks/use-toast'

import type { Expression } from '@/types/expression'

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
  onEdit,
  onViewDetail,
  onDelete,
  onToggleSelect,
  onToggleSelectAll,
  onPageChange,
  onJumpToPage,
}: {
  expressions: Expression[]
  loading: boolean
  total: number
  page: number
  pageSize: number
  selectedIds: Set<number>
  chatNameMap: Map<string, string>
  onEdit: (expression: Expression) => void
  onViewDetail: (expression: Expression) => void
  onDelete: (expression: Expression) => void
  onToggleSelect: (id: number) => void
  onToggleSelectAll: () => void
  onPageChange: (newPage: number) => void
  onJumpToPage: (targetPage: string) => void
}) {
  const { toast } = useToast()

  const getChatName = (chatId: string): string => {
    return chatNameMap.get(chatId) || chatId
  }

  const totalPages = Math.ceil(total / pageSize)

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
    <div className="rounded-lg border bg-card">
      {/* 桌面端表格视图 */}
      <div className="hidden md:block">
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
              <TableHead>聊天</TableHead>
              <TableHead className="text-right">操作</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {loading ? (
              <TableRow>
                <TableCell colSpan={5} className="text-center py-8 text-muted-foreground">
                  加载中...
                </TableCell>
              </TableRow>
            ) : expressions.length === 0 ? (
              <TableRow>
                <TableCell colSpan={5} className="text-center py-8 text-muted-foreground">
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
                  <TableCell className="font-medium max-w-xs truncate">
                    {expression.situation}
                  </TableCell>
                  <TableCell className="max-w-xs truncate">{expression.style}</TableCell>
                  <TableCell 
                    className="max-w-[200px] truncate" 
                    title={getChatName(expression.chat_id)}
                    style={{ wordBreak: 'keep-all' }}
                  >
                    <span className="whitespace-nowrap overflow-hidden text-ellipsis block">
                      {getChatName(expression.chat_id)}
                    </span>
                  </TableCell>
                  <TableCell className="text-right">
                    <div className="flex justify-end gap-2">
                      <Button
                        variant="default"
                        size="sm"
                        onClick={() => onEdit(expression)}
                      >
                        <Edit className="h-4 w-4 mr-1" />
                        编辑
                      </Button>
                      <Button
                        variant="outline"
                        size="icon"
                        className="h-8 w-8"
                        onClick={() => onViewDetail(expression)}
                        title="查看详情"
                      >
                        <Eye className="h-4 w-4" />
                      </Button>
                      <Button
                        size="sm"
                        onClick={() => onDelete(expression)}
                        className="bg-red-600 hover:bg-red-700 text-white"
                      >
                        <Trash2 className="h-4 w-4 mr-1" />
                        删除
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
      <div className="md:hidden space-y-3 p-4">
        {loading ? (
          <div className="text-center py-8 text-muted-foreground">
            加载中...
          </div>
        ) : expressions.length === 0 ? (
          <div className="text-center py-8 text-muted-foreground">
            暂无数据
          </div>
        ) : (
          expressions.map((expression) => (
            <div key={expression.id} className="rounded-lg border bg-card p-4 space-y-3 overflow-hidden">
              {/* 复选框和情境 */}
              <div className="flex items-start gap-3">
                <Checkbox
                  checked={selectedIds.has(expression.id)}
                  onCheckedChange={() => onToggleSelect(expression.id)}
                  className="mt-1"
                />
                <div className="min-w-0 flex-1 overflow-hidden space-y-2">
                  <div>
                    <div className="text-xs text-muted-foreground mb-1">情境</div>
                    <h3 className="font-semibold text-sm line-clamp-2 w-full break-all" title={expression.situation}>
                      {expression.situation}
                    </h3>
                  </div>
                  <div>
                    <div className="text-xs text-muted-foreground mb-1">风格</div>
                    <p className="text-sm line-clamp-2 w-full break-all" title={expression.style}>
                      {expression.style}
                    </p>
                  </div>
                </div>
              </div>

              {/* 聊天名称 */}
              <div className="text-sm">
                <div className="text-xs text-muted-foreground mb-1">聊天</div>
                <p 
                  className="text-sm truncate" 
                  title={getChatName(expression.chat_id)}
                  style={{ wordBreak: 'keep-all' }}
                >
                  {getChatName(expression.chat_id)}
                </p>
              </div>

              {/* 操作按钮 */}
              <div className="flex flex-wrap gap-1 pt-2 border-t overflow-hidden">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => onEdit(expression)}
                  className="text-xs px-2 py-1 h-auto flex-shrink-0"
                >
                  <Edit className="h-3 w-3 mr-1" />
                  编辑
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => onViewDetail(expression)}
                  className="text-xs px-2 py-1 h-auto flex-shrink-0"
                >
                  <Eye className="h-3 w-3" />
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => onDelete(expression)}
                  className="text-xs px-2 py-1 h-auto flex-shrink-0 text-destructive hover:text-destructive"
                >
                  <Trash2 className="h-3 w-3 mr-1" />
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
}: {
  total: number
  page: number
  pageSize: number
  onPageChange: (newPage: number) => void
  onJumpToPage: (targetPage: string) => void
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
    <div className="flex flex-col sm:flex-row items-center justify-between gap-4 px-4 py-3 border-t">
      <div className="text-sm text-muted-foreground">
        共 {total} 条记录，第 {page} / {totalPages} 页
      </div>
      <div className="flex items-center gap-2">
        {/* 首页 */}
        <Button
          variant="outline"
          size="sm"
          onClick={() => onPageChange(1)}
          disabled={page === 1}
          className="hidden sm:flex"
        >
          <ChevronsLeft className="h-4 w-4" />
        </Button>
        
        {/* 上一页 */}
        <Button
          variant="outline"
          size="sm"
          onClick={() => onPageChange(page - 1)}
          disabled={page === 1}
        >
          <ChevronLeft className="h-4 w-4 sm:mr-1" />
          <span className="hidden sm:inline">上一页</span>
        </Button>

        {/* 页码跳转 */}
        <div className="flex items-center gap-2">
          <Input
            type="number"
            value={jumpToPage}
            onChange={(e) => setJumpToPage(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleJump()}
            placeholder={page.toString()}
            className="w-16 h-8 text-center"
            min={1}
            max={totalPages}
          />
          <Button
            variant="outline"
            size="sm"
            onClick={handleJump}
            disabled={!jumpToPage}
            className="h-8"
          >
            跳转
          </Button>
        </div>
        
        {/* 下一页 */}
        <Button
          variant="outline"
          size="sm"
          onClick={() => onPageChange(page + 1)}
          disabled={page >= totalPages}
        >
          <span className="hidden sm:inline">下一页</span>
          <ChevronRight className="h-4 w-4 sm:ml-1" />
        </Button>

        {/* 末页 */}
        <Button
          variant="outline"
          size="sm"
          onClick={() => onPageChange(totalPages)}
          disabled={page >= totalPages}
          className="hidden sm:flex"
        >
          <ChevronsRight className="h-4 w-4" />
        </Button>
      </div>
    </div>
  )
}

