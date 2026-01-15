/**
 * 模型列表分页组件
 */
import React from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { ChevronLeft, ChevronRight, ChevronsLeft, ChevronsRight } from 'lucide-react'
import { PAGE_SIZE_OPTIONS } from '../constants'

interface PaginationProps {
  page: number
  pageSize: number
  totalItems: number
  jumpToPage: string
  onPageChange: (page: number) => void
  onPageSizeChange: (size: number) => void
  onJumpToPageChange: (value: string) => void
  onJumpToPage: () => void
  onSelectionClear?: () => void
}

export const Pagination = React.memo(function Pagination({
  page,
  pageSize,
  totalItems,
  jumpToPage,
  onPageChange,
  onPageSizeChange,
  onJumpToPageChange,
  onJumpToPage,
  onSelectionClear,
}: PaginationProps) {
  const totalPages = Math.ceil(totalItems / pageSize)

  const handlePageSizeChange = (value: string) => {
    onPageSizeChange(parseInt(value))
    onPageChange(1)
    onSelectionClear?.()
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      onJumpToPage()
    }
  }

  if (totalItems === 0) return null

  return (
    <div className="flex flex-col sm:flex-row items-center justify-between gap-4 mt-4">
      <div className="flex items-center gap-2">
        <Label htmlFor="page-size-model" className="text-sm whitespace-nowrap">每页显示</Label>
        <Select
          value={pageSize.toString()}
          onValueChange={handlePageSizeChange}
        >
          <SelectTrigger id="page-size-model" className="w-20">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {PAGE_SIZE_OPTIONS.map((size) => (
              <SelectItem key={size} value={size.toString()}>
                {size}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <span className="text-sm text-muted-foreground">
          显示 {(page - 1) * pageSize + 1} 到{' '}
          {Math.min(page * pageSize, totalItems)} 条，共 {totalItems} 条
        </span>
      </div>
      <div className="flex items-center gap-2">
        <Button
          variant="outline"
          size="sm"
          onClick={() => onPageChange(1)}
          disabled={page === 1}
          className="hidden sm:flex"
        >
          <ChevronsLeft className="h-4 w-4" />
        </Button>
        <Button
          variant="outline"
          size="sm"
          onClick={() => onPageChange(Math.max(1, page - 1))}
          disabled={page === 1}
        >
          <ChevronLeft className="h-4 w-4 sm:mr-1" />
          <span className="hidden sm:inline">上一页</span>
        </Button>
        <div className="flex items-center gap-2">
          <Input
            type="number"
            value={jumpToPage}
            onChange={(e) => onJumpToPageChange(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={page.toString()}
            className="w-16 h-8 text-center"
            min={1}
            max={totalPages}
          />
          <Button
            variant="outline"
            size="sm"
            onClick={onJumpToPage}
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
          disabled={page >= totalPages}
        >
          <span className="hidden sm:inline">下一页</span>
          <ChevronRight className="h-4 w-4 sm:ml-1" />
        </Button>
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
})
