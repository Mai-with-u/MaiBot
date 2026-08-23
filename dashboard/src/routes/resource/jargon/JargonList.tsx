import React from 'react'
import {
  Check,
  ChevronLeft,
  ChevronRight,
  ChevronsLeft,
  ChevronsRight,
  Edit,
  Globe,
  HelpCircle,
  Trash2,
  X,
} from 'lucide-react'

import { AccentPanel } from '@/components/ui/accent-panel'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
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
  onEdit: (jargon: Jargon) => void
  onDelete: (jargon: Jargon) => void
  onToggleSelect: (id: number) => void
  onToggleSelectAll: () => void
  onPageChange: (page: number) => void
  onJumpToPage: (page: string) => void
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
 * 黑话列表组件（桁面端表格 + 移动端卡片 + 分页）
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
  onEdit,
  onDelete,
  onToggleSelect,
  onToggleSelectAll,
  onPageChange,
  onJumpToPage,
  onPageSizeChange,
}: JargonListProps) {
  const [jumpToPage, setJumpToPage] = React.useState('')
  const tableColSpan = hideChatColumn ? 6 : 7

  const handleJumpToPage = () => {
    onJumpToPage(jumpToPage)
    setJumpToPage('')
  }

  return (
    <AccentPanel
      showRetroStripes={false}
      className={cn('bg-card flex min-h-0 flex-col border', className)}
      contentClassName="flex h-full !min-h-0 flex-1 flex-col"
    >
      {/* 桁面端表格视图 */}
      <div
        data-jargon-table-viewport="true"
        className="hidden min-h-0 flex-1 overflow-auto md:block"
      >
        <Table aria-label="黑话列表">
          <TableHeader>
            <TableRow>
              <TableHead className="h-9 w-10 px-2">
                <Checkbox
                  checked={selectedIds.size === jargons.length && jargons.length > 0}
                  onCheckedChange={onToggleSelectAll}
                />
              </TableHead>
              <TableHead className="h-9 px-2">内容</TableHead>
              <TableHead className="h-9 px-2">含义</TableHead>
              {!hideChatColumn && <TableHead className="h-9 px-2">聊天</TableHead>}
              <TableHead className="h-9 px-2 text-center">手动</TableHead>
              <TableHead className="h-9 px-2 text-center">遇见次数</TableHead>
              <TableHead className="h-9 px-2 text-right">操作</TableHead>
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
                <TableRow key={jargon.id} className="align-top">
                  <TableCell className="px-2 py-1.5">
                    <Checkbox
                      checked={selectedIds.has(jargon.id)}
                      onCheckedChange={() => onToggleSelect(jargon.id)}
                    />
                  </TableCell>
                  <TableCell className="max-w-[200px] px-2 py-1.5 font-medium">
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
                  <TableCell className="max-w-[300px] px-2 py-1.5" title={jargon.meaning || ''}>
                    {jargon.meaning ? (
                      <span className="line-clamp-2 text-xs leading-4 break-words whitespace-normal">
                        {jargon.meaning}
                      </span>
                    ) : (
                      <span className="text-muted-foreground">-</span>
                    )}
                  </TableCell>
                  {!hideChatColumn && (
                    <TableCell
                      className="max-w-[220px] px-2 py-1.5"
                      title={formatJargonChatDisplay(jargon)}
                    >
                      <span className="line-clamp-2 text-xs leading-4 break-words whitespace-normal">
                        {formatJargonChatDisplay(jargon)}
                      </span>
                    </TableCell>
                  )}
                  <TableCell className="px-2 py-1.5 text-center">
                    <div className="flex justify-center">
                      {renderManualMarker(jargon.created_by)}
                    </div>
                  </TableCell>
                  <TableCell className="px-2 py-1.5 text-center">{jargon.count}</TableCell>
                  <TableCell className="px-2 py-1.5 text-right">
                    <div className="flex justify-end gap-1.5">
                      <Button
                        variant="default"
                        size="icon"
                        className="h-7 w-7"
                        onClick={() => onEdit(jargon)}
                        title="查看/编辑"
                        aria-label="查看或编辑黑话"
                      >
                        <Edit className="h-4 w-4" />
                      </Button>
                      <Button
                        variant="outline"
                        size="icon"
                        onClick={() => onDelete(jargon)}
                        className="text-destructive hover:text-destructive h-7 w-7"
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
      </div>

      {/* 移动端卡片视图 */}
      <div className="space-y-2 p-3 md:hidden">
        {loading ? (
          <div className="text-muted-foreground py-8 text-center">
            <ThinkingIllustration size="sm" className="mx-auto" />
          </div>
        ) : jargons.length === 0 ? (
          <div className="text-muted-foreground py-8 text-center">暂无数据</div>
        ) : (
          jargons.map((jargon) => (
            <div key={jargon.id} className="bg-card space-y-2 rounded-lg border p-3">
              <div className="flex items-start gap-2.5">
                <Checkbox
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
      {total > 0 && (
        <div className="flex flex-col items-center justify-between gap-2 border-t px-3 py-2 sm:flex-row">
          <div className="text-muted-foreground flex items-center gap-2 text-xs">
            <span>
              共 {total} 条记录，第 {page} / {Math.ceil(total / pageSize)} 页
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
          <div className="flex items-center gap-1.5">
            <Button
              variant="outline"
              size="sm"
              onClick={() => onPageChange(1)}
              disabled={page === 1}
              className="hidden h-8 sm:flex"
            >
              <ChevronsLeft className="h-4 w-4" />
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => onPageChange(page - 1)}
              disabled={page === 1}
              className="h-8"
            >
              <ChevronLeft className="h-4 w-4 sm:mr-1" />
              <span className="hidden sm:inline">上一页</span>
            </Button>
            <div className="flex items-center gap-1.5">
              <Input
                type="number"
                value={jumpToPage}
                onChange={(e) => setJumpToPage(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && handleJumpToPage()}
                placeholder={page.toString()}
                className="h-8 w-16 text-center"
                min={1}
                max={Math.ceil(total / pageSize)}
              />
              <Button
                variant="outline"
                size="sm"
                onClick={handleJumpToPage}
                disabled={!jumpToPage}
                className="h-8"
              >
                跳转
              </Button>
            </div>
            <Button
              variant="outline"
              size="sm"
              onClick={() => onPageChange(page + 1)}
              disabled={page >= Math.ceil(total / pageSize)}
              className="h-8"
            >
              <span className="hidden sm:inline">下一页</span>
              <ChevronRight className="h-4 w-4 sm:ml-1" />
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => onPageChange(Math.ceil(total / pageSize))}
              disabled={page >= Math.ceil(total / pageSize)}
              className="hidden h-8 sm:flex"
            >
              <ChevronsRight className="h-4 w-4" />
            </Button>
          </div>
        </div>
      )}
    </AccentPanel>
  )
}
