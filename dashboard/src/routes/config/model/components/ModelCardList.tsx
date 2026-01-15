/**
 * 模型列表 - 移动端卡片视图
 */
import React from 'react'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Pencil, Trash2 } from 'lucide-react'
import type { ModelInfo } from '../types'

interface ModelCardListProps {
  /** 当前页显示的模型 (分页后的) */
  paginatedModels: ModelInfo[]
  /** 所有模型列表 (未分页) */
  allModels: ModelInfo[]
  /** 编辑模型回调 */
  onEdit: (model: ModelInfo, index: number) => void
  /** 删除模型回调 */
  onDelete: (index: number) => void
  /** 检查模型是否被使用 */
  isModelUsed: (modelName: string) => boolean
  /** 搜索关键词 */
  searchQuery: string
}

export const ModelCardList = React.memo(function ModelCardList({
  paginatedModels,
  allModels,
  onEdit,
  onDelete,
  isModelUsed,
  searchQuery,
}: ModelCardListProps) {
  if (paginatedModels.length === 0) {
    return (
      <div className="md:hidden text-center text-muted-foreground py-8 rounded-lg border bg-card">
        {searchQuery ? '未找到匹配的模型' : '暂无模型配置'}
      </div>
    )
  }

  return (
    <div className="md:hidden space-y-3">
      {paginatedModels.map((model, displayIndex) => {
        const actualIndex = allModels.findIndex(m => m === model)
        const used = isModelUsed(model.name)
        return (
          <div key={displayIndex} className="rounded-lg border bg-card p-4 space-y-3">
            <div className="flex items-start justify-between gap-2">
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 mb-1">
                  <h3 className="font-semibold text-base">{model.name}</h3>
                  <Badge 
                    variant={used ? "default" : "secondary"}
                    className={used ? "bg-green-600 hover:bg-green-700" : ""}
                  >
                    {used ? '已使用' : '未使用'}
                  </Badge>
                </div>
                <p className="text-xs text-muted-foreground break-all" title={model.model_identifier}>
                  {model.model_identifier}
                </p>
              </div>
              <div className="flex gap-1 flex-shrink-0">
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
            </div>
            <div className="grid grid-cols-2 gap-2 text-sm">
              <div>
                <span className="text-muted-foreground text-xs">提供商</span>
                <p className="font-medium">{model.api_provider}</p>
              </div>
              <div>
                <span className="text-muted-foreground text-xs">模型温度</span>
                <p className="font-medium">{model.temperature != null ? model.temperature : <span className="text-muted-foreground">默认</span>}</p>
              </div>
              <div>
                <span className="text-muted-foreground text-xs">输入价格</span>
                <p className="font-medium">¥{model.price_in}/M</p>
              </div>
              <div>
                <span className="text-muted-foreground text-xs">输出价格</span>
                <p className="font-medium">¥{model.price_out}/M</p>
              </div>
            </div>
          </div>
        )
      })}
    </div>
  )
})
