import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from '@tanstack/react-router'
import type { Edge, Node } from 'reactflow'

import { Database, FileText, Info, Network, RefreshCw, Search } from 'lucide-react'

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { useToast } from '@/hooks/use-toast'
import {
  getKnowledgeGraph,
  getKnowledgeStats,
  searchKnowledgeNode,
  type KnowledgeStats,
} from '@/lib/knowledge-api'
import { cn } from '@/lib/utils'

import { EdgeDetailDialog, NodeDetailDialog } from './GraphDialogs'
import { GraphVisualization } from './GraphVisualization'
import type { GraphData, GraphNode, SelectedEdgeData } from './types'

export function KnowledgeGraphPage() {
  const navigate = useNavigate()
  const [loading, setLoading] = useState(false)
  const [stats, setStats] = useState<KnowledgeStats | null>(null)
  const [searchQuery, setSearchQuery] = useState('')
  const [nodeType, setNodeType] = useState<'all' | 'entity' | 'paragraph'>('all')
  const [nodeLimit, setNodeLimit] = useState(50)
  const [customLimit, setCustomLimit] = useState('50')
  const [showCustomInput, setShowCustomInput] = useState(false)
  const [showInitialConfirm, setShowInitialConfirm] = useState(true)
  const [userConfirmedLoad, setUserConfirmedLoad] = useState(false)
  const [showHighNodeWarning, setShowHighNodeWarning] = useState(false)
  const [graphData, setGraphData] = useState<GraphData>({ nodes: [], edges: [] })
  const [selectedNodeData, setSelectedNodeData] = useState<GraphNode | null>(null)
  const [selectedEdgeData, setSelectedEdgeData] = useState<SelectedEdgeData | null>(null)
  const { toast } = useToast()

  const loadGraph = useCallback(async (skipWarning = false) => {
    try {
      if (!skipWarning && nodeLimit > 200) {
        setShowHighNodeWarning(true)
        return
      }

      setLoading(true)
      const [graphResult, statsData] = await Promise.all([
        getKnowledgeGraph(nodeLimit, nodeType),
        getKnowledgeStats(),
      ])

      setStats(statsData)

      if (graphResult.nodes.length === 0) {
        toast({
          title: '提示',
          description: '知识库为空，请先导入知识数据',
        })
        setGraphData({ nodes: [], edges: [] })
        return
      }

      setGraphData({ nodes: graphResult.nodes, edges: graphResult.edges })

      if (statsData && statsData.total_nodes > nodeLimit) {
        toast({
          title: '提示',
          description: `知识图谱包含 ${statsData.total_nodes} 个节点，当前显示 ${graphResult.nodes.length} 个`,
        })
      }
      
      toast({
        title: '加载成功',
        description: `已加载 ${graphResult.nodes.length} 个节点，${graphResult.edges.length} 条边`,
      })
    } catch (error) {
      console.error('加载知识图谱失败:', error)
      toast({
        title: '加载失败',
        description: error instanceof Error ? error.message : '未知错误',
        variant: 'destructive',
      })
    } finally {
      setLoading(false)
    }
  }, [nodeLimit, nodeType, toast])

  const handleSearch = useCallback(async () => {
    if (!searchQuery.trim()) {
      toast({
        title: '提示',
        description: '请输入搜索关键词',
      })
      return
    }

    try {
      const results = await searchKnowledgeNode(searchQuery)
      if (results.length === 0) {
        toast({
          title: '未找到',
          description: '没有找到匹配的节点',
        })
        return
      }

      toast({
        title: '搜索完成',
        description: `找到 ${results.length} 个匹配节点`,
      })
    } catch (error) {
      console.error('搜索失败:', error)
      toast({
        title: '搜索失败',
        description: error instanceof Error ? error.message : '未知错误',
        variant: 'destructive',
      })
    }
  }, [searchQuery, toast])

  const handleResetHighlight = useCallback(() => {
    toast({
      title: '提示',
      description: '已重置高亮',
    })
  }, [toast])

  const handleInitialConfirm = useCallback(() => {
    setShowInitialConfirm(false)
    setUserConfirmedLoad(true)
    loadGraph()
  }, [loadGraph])

  const handleHighNodeConfirm = useCallback(() => {
    setShowHighNodeWarning(false)
    setTimeout(() => {
      loadGraph(true)
    }, 0)
  }, [loadGraph])

  const onNodeClick = useCallback((_: React.MouseEvent, node: Node) => {
    setSelectedNodeData({
      id: node.id,
      type: node.type as 'entity' | 'paragraph',
      content: node.data.content,
    })
  }, [])

  useEffect(() => {
    if (showInitialConfirm) return
    if (!userConfirmedLoad) return
    
    loadGraph()
  }, [nodeLimit, nodeType, showInitialConfirm, userConfirmedLoad])

  const onEdgeClick = useCallback((_: React.MouseEvent, edge: Edge) => {
    const sourceNode = graphData.nodes.find(n => n.id === edge.source)
    const targetNode = graphData.nodes.find(n => n.id === edge.target)
    const edgeData = graphData.edges.find(e => e.source === edge.source && e.target === edge.target)
    
    if (sourceNode && targetNode && edgeData) {
      setSelectedEdgeData({
        source: {
          id: sourceNode.id,
          type: sourceNode.type as 'entity' | 'paragraph',
          content: sourceNode.content,
        },
        target: {
          id: targetNode.id,
          type: targetNode.type as 'entity' | 'paragraph',
          content: targetNode.content,
        },
        edge: {
          source: edge.source,
          target: edge.target,
          weight: parseFloat(edge.label as string || '0'),
        },
      })
    }
  }, [graphData])

  return (
    <div className="h-full flex flex-col">
      <div className="flex-shrink-0 p-4 border-b bg-background">
        <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4">
          <div>
            <h1 className="text-2xl sm:text-3xl font-bold">麦麦知识库图谱</h1>
            <p className="text-muted-foreground mt-1">可视化知识实体与关系网络</p>
          </div>

          {stats && (
            <div className="flex gap-2 flex-wrap">
              <Badge variant="outline" className="gap-1">
                <Database className="h-3 w-3" />
                节点: {stats.total_nodes}
              </Badge>
              <Badge variant="outline" className="gap-1">
                <Network className="h-3 w-3" />
                边: {stats.total_edges}
              </Badge>
              <Badge variant="outline" className="gap-1">
                <Info className="h-3 w-3" />
                实体: {stats.entity_nodes}
              </Badge>
              <Badge variant="outline" className="gap-1">
                <FileText className="h-3 w-3" />
                段落: {stats.paragraph_nodes}
              </Badge>
            </div>
          )}
        </div>

        <div className="flex flex-col sm:flex-row gap-2 mt-4">
          <div className="flex-1 flex gap-2">
            <Input
              placeholder="搜索节点内容..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
              className="flex-1"
            />
            <Button onClick={handleSearch} size="sm">
              <Search className="h-4 w-4" />
            </Button>
            <Button onClick={handleResetHighlight} variant="outline" size="sm">
              重置
            </Button>
          </div>

          <div className="flex gap-2">
            <Select value={nodeType} onValueChange={(v) => setNodeType(v as 'all' | 'entity' | 'paragraph')}>
              <SelectTrigger className="w-[120px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">全部节点</SelectItem>
                <SelectItem value="entity">仅实体</SelectItem>
                <SelectItem value="paragraph">仅段落</SelectItem>
              </SelectContent>
            </Select>

            <Select 
              value={
                nodeLimit === 10000 ? 'all' :
                showCustomInput ? 'custom' :
                nodeLimit.toString()
              } 
              onValueChange={(v) => {
                if (v === 'custom') {
                  setShowCustomInput(true)
                  setCustomLimit(nodeLimit.toString())
                } else if (v === 'all') {
                  setShowCustomInput(false)
                  setNodeLimit(10000)
                } else {
                  setShowCustomInput(false)
                  setNodeLimit(Number(v))
                }
              }}
            >
              <SelectTrigger className="w-[120px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="50">50 节点</SelectItem>
                <SelectItem value="100">100 节点</SelectItem>
                <SelectItem value="200">200 节点</SelectItem>
                <SelectItem value="500">500 节点</SelectItem>
                <SelectItem value="1000">1000 节点</SelectItem>
                <SelectItem value="all">全部 (最多10000)</SelectItem>
                <SelectItem value="custom">自定义...</SelectItem>
              </SelectContent>
            </Select>

            {showCustomInput && (
              <Input
                type="number"
                min="50"
                value={customLimit}
                onChange={(e) => setCustomLimit(e.target.value)}
                onBlur={() => {
                  const num = parseInt(customLimit)
                  if (!isNaN(num) && num >= 50) {
                    setNodeLimit(num)
                  } else {
                    setCustomLimit('50')
                    setNodeLimit(50)
                  }
                }}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    const num = parseInt(customLimit)
                    if (!isNaN(num) && num >= 50) {
                      setNodeLimit(num)
                    } else {
                      setCustomLimit('50')
                      setNodeLimit(50)
                    }
                  }
                }}
                placeholder="最少50个"
                className="w-[120px]"
              />
            )}

            <Button onClick={() => loadGraph()} variant="outline" size="sm" disabled={loading}>
              <RefreshCw className={cn('h-4 w-4', loading && 'animate-spin')} />
            </Button>
          </div>
        </div>
      </div>

      <div className="flex-1 relative">
        {loading ? (
          <div className="absolute inset-0 flex items-center justify-center">
            <div className="text-center">
              <RefreshCw className="h-8 w-8 animate-spin mx-auto mb-2 text-muted-foreground" />
              <p className="text-muted-foreground">加载知识图谱中...</p>
            </div>
          </div>
        ) : graphData.nodes.length === 0 ? (
          <div className="absolute inset-0 flex items-center justify-center">
            <div className="text-center">
              <Database className="h-12 w-12 mx-auto mb-4 text-muted-foreground" />
              <h3 className="text-lg font-semibold mb-2">知识库为空</h3>
              <p className="text-muted-foreground">请先导入知识数据</p>
            </div>
          </div>
        ) : (
          <GraphVisualization
            graphData={graphData}
            onNodeClick={onNodeClick}
            onEdgeClick={onEdgeClick}
            loading={loading}
          />
        )}
      </div>

      <NodeDetailDialog
        open={!!selectedNodeData}
        onOpenChange={(open) => !open && setSelectedNodeData(null)}
        selectedNodeData={selectedNodeData}
      />

      <EdgeDetailDialog
        open={!!selectedEdgeData}
        onOpenChange={(open) => !open && setSelectedEdgeData(null)}
        selectedEdgeData={selectedEdgeData}
      />

      <AlertDialog open={showInitialConfirm} onOpenChange={setShowInitialConfirm}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>加载知识图谱</AlertDialogTitle>
            <AlertDialogDescription>
              知识图谱的动态展示会消耗较多系统资源。
              <br />
              确定要加载知识图谱吗?
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel onClick={() => navigate({ to: '/' })}>
              取消 (返回首页)
            </AlertDialogCancel>
            <AlertDialogAction onClick={handleInitialConfirm}>
              确认加载
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog open={showHighNodeWarning} onOpenChange={setShowHighNodeWarning}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>⚠️ 节点数量较多</AlertDialogTitle>
            <AlertDialogDescription asChild>
              <div>
                <p>
                  您正在尝试加载 <strong className="text-orange-600">{nodeLimit >= 10000 ? '全部 (最多10000个)' : nodeLimit}</strong> 个节点。
                </p>
                <p className="mt-4">节点数量过多可能导致:</p>
                <ul className="list-disc list-inside mt-2 space-y-1">
                  <li>页面加载时间较长</li>
                  <li>浏览器卡顿或崩溃</li>
                  <li>系统资源占用过高</li>
                </ul>
                <p className="mt-4">建议先选择较少的节点数量 (50-200 个)。</p>
              </div>
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel onClick={() => {
              setShowHighNodeWarning(false)
              if (nodeLimit > 200) {
                setNodeLimit(50)
                setShowCustomInput(false)
              }
            }}>
              取消
            </AlertDialogCancel>
            <AlertDialogAction onClick={handleHighNodeConfirm} className="bg-orange-600 hover:bg-orange-700">
              我了解风险，继续加载
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
