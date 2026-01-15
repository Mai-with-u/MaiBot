/**
 * 模型列表 - 桌面端表格视图
 */
import React from 'react'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Checkbox } from '@/components/ui/checkbox'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { Pencil, Trash2 } from 'lucide-react'
import type { ModelInfo } from '../types'

interface ModelTableProps {
  /** 当前页显示的模型 (分页后的) */
  paginatedModels: ModelInfo[]
  /** 所有模型列表 (未分页) */
  allModels: ModelInfo[]
  /** 过滤后的模型列表 */
  filteredModels: ModelInfo[]
  /** 已选中的模型索引集合 */
  selectedModels: Set<number>
  /** 编辑模型回调 */
  onEdit: (model: ModelInfo, index: number) => void
  /** 删除模型回调 */
  onDelete: (index: number) => void
  /** 切换选中状态回调 */
  onToggleSelection: (index: number) => void
  /** 切换全选回调 */
  onToggleSelectAll: () => void
  /** 检查模型是否被使用 */
  isModelUsed: (modelName: string) => boolean
  /** 搜索关键词 */
  searchQuery: string
}

export const ModelTable = React.memo(function ModelTable({
  paginatedModels,
  allModels,
  filteredModels,
  selectedModels,
  onEdit,
  onDelete,
  onToggleSelection,
  onToggleSelectAll,
  isModelUsed,
  searchQuery,
}: ModelTableProps) {
  return (
    <div className="hidden md:block rounded-lg border bg-card overflow-hidden">
      <div className="overflow-x-auto">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-12">
                <Checkbox
                  checked={selectedModels.size === filteredModels.length && filteredModels.length > 0}
                  onCheckedChange={onToggleSelectAll}
                />
              </TableHead>
              <TableHead className="w-24">使用状态</TableHead>
              <TableHead>模型名称</TableHead>
              <TableHead>模型标识符</TableHead>
              <TableHead>提供商</TableHead>
              <TableHead className="text-center">温度</TableHead>
              <TableHead className="text-right">输入价格</TableHead>
              <TableHead className="text-right">输出价格</TableHead>
              <TableHead className="text-right">操作</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {paginatedModels.length === 0 ? (
              <TableRow>
                <TableCell colSpan={9} className="text-center text-muted-foreground py-8">
                  {searchQuery ? '未找到匹配的模型' : '暂无模型配置'}
                </TableCell>
              </TableRow>
            ) : (
              paginatedModels.map((model, displayIndex) => {
                const actualIndex = allModels.findIndex(m => m === model)
                const used = isModelUsed(model.name)
                return (
                  <TableRow key={displayIndex}>
                    <TableCell>
                      <Checkbox
                        checked={selectedModels.has(actualIndex)}
                        onCheckedChange={() => onToggleSelection(actualIndex)}
                      />
                    </TableCell>
                    <TableCell>
                      <Badge 
                        variant={used ? "default" : "secondary"}
                        className={used ? "bg-green-600 hover:bg-green-700" : ""}
                      >
                        {used ? '已使用' : '未使用'}
                      </Badge>
                    </TableCell>
                    <TableCell className="font-medium">{model.name}</TableCell>
                    <TableCell className="max-w-xs truncate" title={model.model_identifier}>
                      {model.model_identifier}
                    </TableCell>
                    <TableCell>{model.api_provider}</TableCell>
                    <TableCell className="text-center">
                      {model.temperature != null ? model.temperature : <span className="text-muted-foreground">-</span>}
                    </TableCell>
                    <TableCell className="text-right">¥{model.price_in}/M</TableCell>
                    <TableCell className="text-right">¥{model.price_out}/M</TableCell>
                    <TableCell className="text-right">
                      <div className="flex justify-end gap-2">
                        <Button
                          variant="default"
                          size="sm"
                          onClick={() => onEdit(model, actualIndex)}
                        >
                          <Pencil className="h-4 w-4 mr-1" strokeWidth={2} fill="none" />
                          编辑
                        </Button>
                        <Button
                          size="sm"
                          onClick={() => onDelete(actualIndex)}
                          className="bg-red-600 hover:bg-red-700 text-white"
                        >
                          <Trash2 className="h-4 w-4 mr-1" strokeWidth={2} fill="none" />
                          删除
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                )
              })
            )}
          </TableBody>
        </Table>
      </div>
    </div>
  )
})