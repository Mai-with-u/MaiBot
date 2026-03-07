
import { Badge } from '@/components/ui/badge'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { ScrollArea } from '@/components/ui/scroll-area'

import type { GraphNode, SelectedEdgeData } from './types'

interface NodeDetailDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  selectedNodeData: GraphNode | null
}

export function NodeDetailDialog({ open, onOpenChange, selectedNodeData }: NodeDetailDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl max-h-[80vh] grid grid-rows-[auto_1fr_auto] overflow-hidden">
        <DialogHeader>
          <DialogTitle>节点详情</DialogTitle>
        </DialogHeader>
        {selectedNodeData && (
          <ScrollArea className="h-full pr-4">
            <div className="space-y-4 pb-2">
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <p className="text-sm font-medium text-muted-foreground">类型</p>
                  <div className="mt-1">
                    <Badge variant={selectedNodeData.type === 'entity' ? 'default' : 'secondary'}>
                      {selectedNodeData.type === 'entity' ? '🏷️ 实体' : '📄 段落'}
                    </Badge>
                  </div>
                </div>
              </div>

              <div>
                <p className="text-sm font-medium text-muted-foreground">ID</p>
                <code className="mt-1 block p-2 bg-muted rounded text-xs break-all">
                  {selectedNodeData.id}
                </code>
              </div>

              <div>
                <p className="text-sm font-medium text-muted-foreground">内容</p>
                <div className="mt-1 p-3 bg-muted rounded border">
                  <p className="text-sm whitespace-pre-wrap break-words">{selectedNodeData.content}</p>
                </div>
                {selectedNodeData.type === 'paragraph' && selectedNodeData.content && selectedNodeData.content.length < 20 && (
                  <div className="mt-2 p-3 bg-yellow-50 dark:bg-yellow-950 border border-yellow-200 dark:border-yellow-800 rounded">
                    <p className="text-xs text-yellow-800 dark:text-yellow-200">
                      💡 <strong>提示：</strong>段落内容显示不完整？
                      <br />
                      您可以在 <strong>配置 → WebUI 服务配置</strong> 中启用 "在知识图谱中加载段落完整内容" 选项，以显示段落的完整文本。
                      <br />
                      注意：此功能会额外再次加载 embedding store，占用约数百MB内存。不建议在生产环境中长期开启。
                    </p>
                  </div>
                )}
              </div>
            </div>
          </ScrollArea>
        )}
      </DialogContent>
    </Dialog>
  )
}

interface EdgeDetailDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  selectedEdgeData: SelectedEdgeData | null
}

export function EdgeDetailDialog({ open, onOpenChange, selectedEdgeData }: EdgeDetailDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl max-h-[80vh] overflow-hidden flex flex-col">
        <DialogHeader>
          <DialogTitle>边详情</DialogTitle>
        </DialogHeader>
        {selectedEdgeData && (
          <ScrollArea className="flex-1 pr-4">
            <div className="space-y-4">
              <div className="flex items-center gap-4">
                <div className="flex-1 min-w-0 p-3 bg-blue-50 dark:bg-blue-950 rounded border-2 border-blue-200 dark:border-blue-800">
                  <div className="text-xs text-muted-foreground mb-1">源节点</div>
                  <div className="font-medium text-sm mb-2 truncate">{selectedEdgeData.source.content}</div>
                  <code className="text-xs text-muted-foreground truncate block">
                    {selectedEdgeData.source.id.slice(0, 40)}...
                  </code>
                </div>

                <div className="text-2xl text-muted-foreground flex-shrink-0">→</div>

                <div className="flex-1 min-w-0 p-3 bg-green-50 dark:bg-green-950 rounded border-2 border-green-200 dark:border-green-800">
                  <div className="text-xs text-muted-foreground mb-1">目标节点</div>
                  <div className="font-medium text-sm mb-2 truncate">{selectedEdgeData.target.content}</div>
                  <code className="text-xs text-muted-foreground truncate block">
                    {selectedEdgeData.target.id.slice(0, 40)}...
                  </code>
                </div>
              </div>

              <div>
                <p className="text-sm font-medium text-muted-foreground">权重</p>
                <div className="mt-1">
                  <Badge variant="outline" className="text-base font-mono">
                    {selectedEdgeData.edge.weight.toFixed(4)}
                  </Badge>
                </div>
              </div>
            </div>
          </ScrollArea>
        )}
      </DialogContent>
    </Dialog>
  )
}
